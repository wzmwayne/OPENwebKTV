import asyncio
import hashlib
import io
import json
import os
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from ..database import get_db
from ..models import Song, Playlist, PlaylistSong
from ..schemas import (
    SongOut, QueueOut, PlaylistOut,
    QueueAddRequest, ReorderRequest, PlaylistCreate,
)
from ..player_engine import player_engine
from ..bilibili import BilibiliClient, fetch_lrclib_lyrics
from ..config import settings

router = APIRouter(prefix="/api")
log = logging.getLogger("owk.api")


# ── 封面代理 ────────────────────────────────────────

COVER_CACHE: dict[str, tuple[str, bytes]] = {}   # url -> (content_type, data)
ALLOWED_COVER_HOSTS = ("hdslb.com", "bilibili.com", "biliimg.com", "hdslb.net")


@router.get("/cover")
async def proxy_cover(url: str):
    """代理B站封面图片: 绕过热链保护(带UA+Referer), 内存缓存, 仅允许B站图片域名(防SSRF)"""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_COVER_HOSTS):
        raise HTTPException(400, "非法的封面URL")
    if url in COVER_CACHE:
        ct, data = COVER_CACHE[url]
        return Response(content=data, media_type=ct)
    try:
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            timeout=15, follow_redirects=True,
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
        data = r.content
        ct = r.headers.get("content-type", "image/jpeg") or "image/jpeg"
        COVER_CACHE[url] = (ct, data)
        return Response(content=data, media_type=ct)
    except Exception as e:
        log.warning(f"封面获取失败 {url[:60]}: {e}")
        raise HTTPException(404, "封面获取失败")


# ── 歌词 ────────────────────────────────────────────

LYRICS_CACHE: dict[str, list] = {}


def clear_lyrics_cache(bvid: str):
    """删除歌曲时清理该 bvid 相关的所有歌词缓存键(与存储/内存两层同步删除)"""
    keys = [
        k for k in LYRICS_CACHE
        if k.startswith(f"{bvid}:")          # B站字幕轨(含默认-1键)
        or k.startswith(f"lrc-k:{bvid}:")    # LRCLIB 搜索词(键含关键词哈希)
        or k == f"lrc-t:{bvid}"              # LRCLIB 标题
    ]
    for k in keys:
        LYRICS_CACHE.pop(k, None)
    if keys:
        log.info(f"已清理歌词缓存: {bvid} ({len(keys)} 个键)")


def _lines_to_json(lines) -> str:
    return json.dumps(
        [{"start": l.start, "end": l.end, "text": l.text} for l in lines],
        ensure_ascii=False,
    )


def _lrc_kw_key(bvid: str, kw: str) -> str:
    """LRCLIB 搜索词歌词缓存键: 关键词参与哈希, 避免不同搜索词互相污染"""
    kw = (kw or "").strip()
    if not kw:
        return f"lrc-k:{bvid}:"
    return f"lrc-k:{bvid}:" + hashlib.md5(kw.encode("utf-8")).hexdigest()[:8]


@router.get("/lyrics/tracks/{bvid}")
async def lyric_tracks(bvid: str, keyword: str = "", title: str = "", duration: int = 0,
                       db: Session = Depends(get_db)):
    """列出歌词来源选项(供控制端同级选择, 全部始终显示):
    - B站字幕轨(index >= 0, 带 ai 标记)
    - 第三方LRCLIB(用户搜索词, index=-2; 无搜索词或LRCLIB无结果时 error)
    - 第三方LRCLIB(标题提取, index=-3; 无标题或LRCLIB无结果时 error)
    error 非空 = 该来源不可用(控制端暗红置灰禁止点击)。
    title/duration 由控制端传搜索结果(未下载时 Song 行不存在), 否则回退 Song 行。
    """
    try:
        async with BilibiliClient(cookie_path=settings.BILIBILI_COOKIE) as client:
            tracks = await client.subtitles(bvid)
    except Exception as e:
        log.warning(f"获取字幕轨失败 {bvid}: {e}")
        tracks = []
    result = [
        {"index": i, "lan": t.lan, "lan_doc": t.lan_doc, "ai": t.ai, "kind": "bili", "error": ""}
        for i, t in enumerate(tracks) if t.url
    ]
    song = db.query(Song).filter(Song.bvid == bvid).first()
    stitle = (title or "").strip() or (song.title if song else "")
    sdur = duration or (song.duration if song else 0)
    kw = (keyword or "").strip() or (song.search_keyword if song else "").strip()

    # 第三方(搜索词) -2: 始终显示; 无搜索词或LRCLIB无结果 → error
    if not kw:
        result.append({"index": -2, "lan": "lrclib", "lan_doc": "第三方歌词(搜索词)",
                       "ai": False, "kind": "lrclib", "error": "缺少搜索词"})
    else:
        key = _lrc_kw_key(bvid, kw)
        if key not in LYRICS_CACHE:
            lrclines = await fetch_lrclib_lyrics(stitle, sdur, kw, mode="keyword")
            LYRICS_CACHE[key] = [
                {"start": l.start, "end": l.end, "text": l.text} for l in lrclines
            ]
        result.append({"index": -2, "lan": "lrclib", "lan_doc": "第三方歌词(搜索词)",
                       "ai": False, "kind": "lrclib",
                       "error": "" if LYRICS_CACHE[key] else "未找到歌词"})

    # 第三方(标题) -3: 始终显示; 无标题或LRCLIB无结果 → error
    if not stitle:
        result.append({"index": -3, "lan": "lrclib", "lan_doc": "第三方歌词(标题)",
                       "ai": False, "kind": "lrclib", "error": "缺少歌曲标题"})
    else:
        key = f"lrc-t:{bvid}"
        if key not in LYRICS_CACHE:
            lrclines = await fetch_lrclib_lyrics(stitle, sdur, "", mode="title")
            LYRICS_CACHE[key] = [
                {"start": l.start, "end": l.end, "text": l.text} for l in lrclines
            ]
        result.append({"index": -3, "lan": "lrclib", "lan_doc": "第三方歌词(标题)",
                       "ai": False, "kind": "lrclib",
                       "error": "" if LYRICS_CACHE[key] else "未找到歌词"})
    return {"tracks": result}


@router.get("/lyrics/{bvid}")
async def get_song_lyrics(bvid: str, track: int = -1, keyword: str = "",
                          db: Session = Depends(get_db)):
    """获取歌词(带时间轴)。

    - track = -2: 第三方LRCLIB, 用户搜索词优先(keyword 参数或 Song.search_keyword)
    - track = -3: 第三方LRCLIB, 标题提取
    - track < 0(默认-1): 仅返回下载时存储的歌词(Song.lyrics);
      无存储(''/NULL)或显式无歌词('[]')一律返回空, **不回落** B站/LRCLIB(存储即所得, 2026-08 修改)
    - track >= 0: 取指定B站字幕轨内容(控制端预览切换用, 轨为空时 LRCLIB 兜底)
    """
    if track == -2:
        song = db.query(Song).filter(Song.bvid == bvid).first()
        kw = (keyword or "").strip() or (song.search_keyword if song else "").strip()
        key = _lrc_kw_key(bvid, kw)
        if key not in LYRICS_CACHE:
            lrclines = await fetch_lrclib_lyrics(
                song.title if song else "", song.duration if song else 0, kw, mode="keyword")
            LYRICS_CACHE[key] = [
                {"start": l.start, "end": l.end, "text": l.text} for l in lrclines
            ]
        return {"lyrics": LYRICS_CACHE[key]}
    if track == -3:
        key = f"lrc-t:{bvid}"
        if key not in LYRICS_CACHE:
            song = db.query(Song).filter(Song.bvid == bvid).first()
            lrclines = await fetch_lrclib_lyrics(
                song.title if song else "", song.duration if song else 0, "", mode="title")
            LYRICS_CACHE[key] = [
                {"start": l.start, "end": l.end, "text": l.text} for l in lrclines
            ]
        return {"lyrics": LYRICS_CACHE[key]}
    if track < 0:
        song = db.query(Song).filter(Song.bvid == bvid).first()
        if song and song.lyrics:
            try:
                return {"lyrics": json.loads(song.lyrics)}
            except Exception:
                return {"lyrics": []}   # 存储歌词损坏 → 视为无歌词, 不回落
        return {"lyrics": []}           # 未存储(''/NULL)或显式无歌词('[]') → 一律不回落
    key = f"{bvid}:{track}"
    if key in LYRICS_CACHE:
        return {"lyrics": LYRICS_CACHE[key]}
    lines = []
    try:
        async with BilibiliClient(cookie_path=settings.BILIBILI_COOKIE) as client:
            tracks = await client.subtitles(bvid)
            usable = [t for t in tracks if t.url]
            if usable:
                if track >= 0:
                    idx = track if 0 <= track < len(usable) else 0
                    pick = usable[idx]
                else:
                    # 默认优先非AI字幕(避免B站AI字幕内容错乱)
                    pick = next((t for t in usable if not t.ai), usable[0])
                lines = await client.subtitle_content(pick.url)
    except Exception as e:
        log.warning(f"获取歌词失败 {bvid}: {e}")
    data = [{"start": l.start, "end": l.end, "text": l.text} for l in lines]
    if not data:
        # LRCLIB 兜底: 仅 track>=0(显式预览B站轨)场景, 所选轨为空时兜底(2026-08 起 track<0 已不回落)
        song = db.query(Song).filter(Song.bvid == bvid).first()
        if song and song.title:
            try:
                lrclines = await fetch_lrclib_lyrics(
                    song.title, song.duration, song.search_keyword or "")
                data = [{"start": l.start, "end": l.end, "text": l.text} for l in lrclines]
                if data:
                    log.info(f"LRCLIB兜底歌词: {bvid} ({len(data)}行)")
            except Exception as e:
                log.warning(f"LRCLIB兜底失败 {bvid}: {e}")
    LYRICS_CACHE[key] = data
    return {"lyrics": data}


# ── 公共函数 ─────────────────────────────────────────


async def _ensure_song(bvid: str, db: Session) -> Song:
    song = db.query(Song).filter(Song.bvid == bvid).first()
    if not song:
        try:
            async with BilibiliClient(cookie_path=settings.BILIBILI_COOKIE) as client:
                info = await client.video_info(bvid)
            song = Song(
                bvid=bvid, title=info.title,
                uploader=info.uploader, duration=info.duration,
                cover=info.cover, download_status="pending",
            )
            db.add(song)
            db.commit()
            db.refresh(song)
        except IntegrityError:
            # 并发首次入库撞唯一约束: 回滚后取已有记录
            db.rollback()
            song = db.query(Song).filter(Song.bvid == bvid).first()
    return song


def _get_video_duration(path: str) -> float:
    """用 ffprobe 获取视频实际时长(秒)"""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0


def _has_video_stream(path: str) -> bool:
    """用 ffprobe 判断文件是否包含视频轨(纯音频时标记 audio_only, 前端显示封面兜底)"""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return "video" in r.stdout
    except Exception:
        return True  # ffprobe 不可用/异常时按有视频处理, 保持原行为


async def _run_download(song_id: int, bvid: str, track: int = 0, keyword: str = ""):
    from ..database import SessionLocal
    from ..bilibili import clear_dl_progress
    db = SessionLocal()
    try:
        s = db.query(Song).filter(Song.id == song_id).first()
        if not s or s.download_status in ("ready", "downloading"):
            return
        song_title = s.title
        song_duration = s.duration or 0
        if keyword:
            s.search_keyword = keyword
        s.download_status = "downloading"
        db.commit()
        await player_engine.broadcast_state()
    finally:
        db.close()

    lyrics_json = ""
    try:
        async with BilibiliClient(cookie_path=settings.BILIBILI_COOKIE) as client:
            path = await client.download_video(bvid, settings.MEDIA_DIR)
            size = os.path.getsize(path)
            # 同时下载歌词(所选字幕轨; 失败不阻塞视频下载)
            if track >= 0:
                try:
                    tracks = await client.subtitles(bvid)
                    if tracks:
                        idx = track if 0 <= track < len(tracks) else 0
                        lines = await client.subtitle_content(tracks[idx].url)
                        lyrics_json = _lines_to_json(lines)
                    if not lyrics_json and song_title:
                        # B站无歌词时: LRCLIB 兜底(用户搜索词 + 标题)
                        lrclines = await fetch_lrclib_lyrics(song_title, song_duration, keyword)
                        lyrics_json = _lines_to_json(lrclines)
                except Exception as e:
                    log.warning(f"歌词下载失败 {bvid}: {e}")
            elif track == -2 or track == -3:
                # 第三方LRCLIB: 搜索词(-2) / 标题提取(-3)
                try:
                    mode = "keyword" if track == -2 else "title"
                    kw = keyword if track == -2 else ""
                    lrclines = await fetch_lrclib_lyrics(song_title, song_duration, kw, mode=mode)
                    lyrics_json = _lines_to_json(lrclines)
                except Exception as e:
                    log.warning(f"LRCLIB歌词下载失败 {bvid}: {e}")
            # track == -1: 不带歌词 → 写入显式无歌词标记 '[]'
            # ('[]' 与 '' 语义不同: '' 会被 /api/lyrics 回落B站/第三方兜底显示歌词, '[]' 强制无歌词)
        db = SessionLocal()
        s = db.query(Song).filter(Song.id == song_id).first()
        if s:
            s.file_path = path
            s.file_size = size
            s.lyrics = "[]" if track == -1 else lyrics_json
            # 用 ffprobe 修正时长(B站 video_info 的 duration 字段偶发错误, 如 3 分钟视频返回 54 分钟)
            actual_dur = _get_video_duration(path)
            if actual_dur > 0:
                s.duration = int(actual_dur)
            # 用 ffprobe 判定是否含视频轨；纯音频文件标记为 audio_only
            s.download_status = "ready" if _has_video_stream(path) else "audio_only"
            db.commit()
            log.info(f"下载完成: {bvid} ({'视频' if s.download_status == 'ready' else '仅音频'}"
                     f"{' 含歌词' if lyrics_json else ' 无歌词'})"
                     f"{' 时长已修正' if actual_dur > 0 else ''}")
        db.close()
        await player_engine.broadcast_state()
    except Exception as e:
        log.error(f"下载失败 {bvid}: {e}")
        db = SessionLocal()
        s = db.query(Song).filter(Song.id == song_id).first()
        if s:
            s.download_status = "error"
            db.commit()
        db.close()
        await player_engine.broadcast_state()
    finally:
        await asyncio.sleep(5)
        clear_dl_progress(bvid)


# ── 搜索 ────────────────────────────────────────────


@router.get("/search")
async def search(keyword: str, page: int = 1):
    async with BilibiliClient(cookie_path=settings.BILIBILI_COOKIE) as client:
        items = await client.search(keyword, page)
        return {"items": [i.__dict__ for i in items], "page": page}


# ── 歌曲 ────────────────────────────────────────────


@router.get("/songs")
def list_songs(local: bool = False, q: str = "", db: Session = Depends(get_db)):
    query = db.query(Song)
    if local:
        query = query.filter(Song.download_status == "ready")
    if q:
        query = query.filter(Song.title.ilike(f"%{q}%"))
    songs = query.order_by(Song.created_at.desc()).limit(200).all()
    return {"songs": [SongOut.model_validate(s).model_dump() for s in songs]}


@router.get("/downloads")
def get_downloads():
    from ..bilibili import get_all_dl_progress
    return {"downloads": get_all_dl_progress()}


@router.get("/songs/{bvid}")
async def get_song(bvid: str, db: Session = Depends(get_db)):
    song = await _ensure_song(bvid, db)
    return SongOut.model_validate(song)


@router.post("/download")
async def download_song(body: QueueAddRequest, db: Session = Depends(get_db)):
    song = await _ensure_song(body.bvid, db)
    if song.download_status == "ready":
        return {"status": "already_downloaded", "song_id": song.id}
    asyncio.create_task(_run_download(song.id, body.bvid, body.track, body.keyword))
    return {"status": "downloading", "song_id": song.id}


# ── 队列 ────────────────────────────────────────────


@router.get("/queue")
def get_queue():
    state = player_engine.get_state()
    return {"queue": [q.model_dump() for q in state.queue], "current": state.current.model_dump() if state.current else None}


@router.post("/queue")
async def add_queue(body: QueueAddRequest, db: Session = Depends(get_db)):
    if player_engine.status == "blocked":
        raise HTTPException(403, "系统维护中，暂时无法点歌")
    song = await _ensure_song(body.bvid, db)
    item_id, result = await player_engine.add_to_queue(song.id, db)
    if result == "blocked":
        raise HTTPException(403, "系统维护中，暂时无法点歌")
    if result == "full":
        raise HTTPException(400, "队列已满(上限50首)")
    if result == "exists":
        raise HTTPException(409, "歌曲已在队列中")
    asyncio.create_task(_run_download(song.id, body.bvid, body.track, body.keyword))
    return {"item_id": item_id}


@router.delete("/queue/{item_id}")
async def remove_queue(item_id: int, db: Session = Depends(get_db)):
    await player_engine.remove_from_queue(item_id, db)
    return {"status": "removed"}


@router.put("/queue/reorder")
async def reorder_queue(body: ReorderRequest, db: Session = Depends(get_db)):
    await player_engine.reorder_queue(body.order, db)
    return {"status": "reordered"}


# ── 播放控制 ────────────────────────────────────────


@router.post("/control/play")
async def control_play():
    await player_engine.play()
    return {"status": "playing"}


@router.post("/control/pause")
async def control_pause():
    await player_engine.pause()
    return {"status": "paused"}


@router.post("/control/resume")
async def control_resume():
    await player_engine.resume()
    return {"status": "resumed"}


@router.post("/control/next")
async def control_next():
    await player_engine.next()
    return {"status": "next"}


@router.post("/control/prev")
async def control_prev():
    await player_engine.prev()
    return {"status": "prev"}


@router.get("/state")
def get_state():
    return player_engine.get_state().model_dump()


# ── 歌单 ────────────────────────────────────────────


@router.get("/playlists")
def list_playlists(db: Session = Depends(get_db)):
    playlists = db.query(Playlist).all()
    return {"playlists": [PlaylistOut.model_validate(p).model_dump() for p in playlists]}


@router.post("/playlists")
def create_playlist(body: PlaylistCreate, db: Session = Depends(get_db)):
    pl = Playlist(name=body.name)
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return PlaylistOut.model_validate(pl).model_dump()


@router.delete("/playlists/{pl_id}")
def delete_playlist(pl_id: int, db: Session = Depends(get_db)):
    db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl_id).delete()
    db.query(Playlist).filter(Playlist.id == pl_id).delete()
    db.commit()
    return {"status": "deleted"}


@router.get("/playlists/{pl_id}/songs")
def list_playlist_songs(pl_id: int, db: Session = Depends(get_db)):
    items = (
        db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl_id)
        .order_by(PlaylistSong.order).all()
    )
    songs = []
    for item in items:
        song = db.query(Song).filter(Song.id == item.song_id).first()
        if song:
            songs.append(SongOut.model_validate(song).model_dump())
    return {"songs": songs}


@router.post("/playlists/{pl_id}/songs")
async def add_playlist_song(pl_id: int, body: QueueAddRequest, db: Session = Depends(get_db)):
    song = await _ensure_song(body.bvid, db)
    count = db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl_id).count()
    ps = PlaylistSong(playlist_id=pl_id, song_id=song.id, order=count)
    db.add(ps)
    db.commit()
    return {"status": "added"}


@router.post("/playlists/{pl_id}/play")
async def play_playlist(pl_id: int, db: Session = Depends(get_db)):
    if player_engine.status == "blocked":
        raise HTTPException(403, "系统维护中，暂时无法播放")
    items = (
        db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl_id)
        .order_by(PlaylistSong.order).all()
    )
    if not items:
        raise HTTPException(400, "歌单为空")
    added = 0
    for ps in items:
        song = db.query(Song).filter(Song.id == ps.song_id).first()
        if not song:
            continue
        item_id, result = await player_engine.add_to_queue(song.id, db)
        if result == "full":
            break
        asyncio.create_task(_run_download(song.id, song.bvid, 0))
        added += 1
    return {"added": added}


@router.delete("/playlists/{pl_id}/songs/{song_id}")
def remove_playlist_song(pl_id: int, song_id: int, db: Session = Depends(get_db)):
    db.query(PlaylistSong).filter(
        PlaylistSong.playlist_id == pl_id, PlaylistSong.song_id == song_id
    ).delete()
    db.commit()
    return {"status": "removed"}


# ── 媒体流 ──────────────────────────────────────────


@router.get("/media/{song_id}")
async def stream_media(song_id: int):
    from fastapi.responses import FileResponse
    from ..database import SessionLocal
    db = SessionLocal()
    song = db.query(Song).filter(Song.id == song_id).first()
    db.close()
    # 仅就绪文件可流式播放; 下载中/失败一律 404, 避免读到半成品
    if not song or song.download_status not in ("ready", "audio_only") \
            or not song.file_path or not os.path.exists(song.file_path):
        raise HTTPException(404, "文件不存在")
    ext = os.path.splitext(song.file_path)[1].lower()
    media_type = {
        "mp4": "video/mp4",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "m4s": "video/mp4",
    }.get(ext, "video/mp4")
    return FileResponse(song.file_path, media_type=media_type)


# ── QR 码 ────────────────────────────────────────────


@router.get("/qr/controller")
async def qr_controller(request: Request):
    import qrcode
    url = str(request.base_url).rstrip("/") + "/controller.html"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")
