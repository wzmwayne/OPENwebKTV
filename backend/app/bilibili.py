"""
OwK Bilibili API Client
搜索 / 视频信息 / 字幕歌词 / 下载视频
"""

import json
import os
import logging
import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional
import httpx

log = logging.getLogger("owk.bilibili")

COOKIE_PATH = "bilibili_cookie.json"


# ── 下载进度追踪 ──────────────────────────────────────

_active_downloads: dict[str, dict] = {}  # bvid -> {percent, status, title}


def update_dl_progress(bvid: str, percent: int, status: str = "downloading", title: str = ""):
    _active_downloads[bvid] = {"percent": min(100, percent), "status": status, "title": title}


def get_dl_progress(bvid: str) -> dict | None:
    return _active_downloads.get(bvid)


def get_all_dl_progress() -> dict:
    return dict(_active_downloads)


def clear_dl_progress(bvid: str):
    _active_downloads.pop(bvid, None)


# ── 数据模型 ──────────────────────────────────────────

@dataclass
class SearchItem:
    bvid: str
    title: str
    author: str
    duration: int
    cover: str
    play_count: int


@dataclass
class VideoInfo:
    bvid: str
    title: str
    uploader: str
    uploader_uid: int
    cid: int
    duration: int
    cover: str
    description: str
    pages: list = field(default_factory=list)


@dataclass
class SubtitleTrack:
    lan: str
    lan_doc: str
    url: str


@dataclass
class SubtitleLine:
    start: float
    end: float
    text: str


@dataclass
class AudioStream:
    id: int
    url: str
    bandwidth: int
    codec: str
    mime: str


@dataclass
class PlayInfo:
    audio_streams: list[AudioStream] = field(default_factory=list)
    video_streams: list[dict] = field(default_factory=list)
    quality: int = 0
    accept_quality: list[int] = field(default_factory=list)
    duration: int = 0


# ── B站客户端 ─────────────────────────────────────────

class BilibiliClient:
    """Bilibili API 异步客户端"""

    API_BASE = "https://api.bilibili.com"

    def __init__(self, cookie_path: str = COOKIE_PATH):
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }
        self._client: Optional[httpx.AsyncClient] = None
        if cookie_path and os.path.exists(cookie_path):
            self._load_cookies(cookie_path)

    def _load_cookies(self, path: str):
        try:
            with open(path) as f:
                cookies = json.load(f)
            if cookies:
                self._headers["Cookie"] = "; ".join(
                    f"{k}={v}" for k, v in cookies.items()
                )
                log.info(f"已加载Cookie: {list(cookies.keys())}")
            else:
                log.info("Cookie文件为空")
        except Exception as e:
            log.warning(f"加载Cookie失败: {e}")

    def _ensure_client(self):
        if not self._client:
            self._client = httpx.AsyncClient(
                headers=self._headers, timeout=30, follow_redirects=True
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict = None) -> dict:
        c = self._ensure_client()
        url = f"{self.API_BASE}{path}"
        resp = await c.get(url, params=params)
        try:
            data = resp.json()
        except Exception:
            log.error(f"B站API非JSON响应 [{resp.status_code}]: {resp.text[:200]}")
            raise Exception(f"B站API响应异常: HTTP {resp.status_code}")
        if data.get("code") != 0:
            msg = data.get("message", "unknown")
            log.warning(f"B站API错误 [{path}]: code={data['code']}, msg={msg}")
            raise Exception(f"B站API错误: {msg}")
        return data.get("data", {})

    # ── 公开 API ─────────────────────────────────────

    async def search(self, keyword: str, page: int = 1) -> list[SearchItem]:
        """搜索B站视频"""
        params = {"keyword": keyword, "page": page}
        data = await self._get("/x/web-interface/search/all/v2", params)
        results = []
        for section in data.get("result", []):
            items = section.get("data", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if "bvid" not in item:
                    continue
                dur_str = str(item.get("duration", "0"))
                parts = dur_str.split(":")
                duration_secs = 0
                for p in parts:
                    duration_secs = duration_secs * 60 + int(p) if p else 0

                cover = item.get("pic", "")
                if cover.startswith("//"):
                    cover = "https:" + cover

                results.append(SearchItem(
                    bvid=item["bvid"],
                    title=re.sub(r"<[^>]+>", "", item.get("title", "")),
                    author=item.get("author", ""),
                    duration=duration_secs,
                    cover=cover,
                    play_count=item.get("play", 0),
                ))
        log.info(f"搜索 '{keyword}' 找到 {len(results)} 个视频")
        return results

    async def video_info(self, bvid: str) -> VideoInfo:
        """获取视频详细信息"""
        data = await self._get("/x/web-interface/view", {"bvid": bvid})
        pages = []
        for p in data.get("pages", []):
            pages.append({
                "cid": p.get("cid", 0),
                "page": p.get("page", 1),
                "part": p.get("part", ""),
            })
        info = VideoInfo(
            bvid=bvid,
            title=data.get("title", ""),
            uploader=data.get("owner", {}).get("name", ""),
            uploader_uid=data.get("owner", {}).get("mid", 0),
            cid=data.get("cid", 0),
            duration=data.get("duration", 0),
            cover=data.get("pic", ""),
            description=data.get("desc", ""),
            pages=pages,
        )
        log.info(f"视频信息: {info.title} | UP:{info.uploader} | {info.duration}秒")
        return info

    async def subtitles(self, bvid: str, cid: int = None) -> list[SubtitleTrack]:
        """获取视频字幕/CC列表"""
        if cid is None:
            info = await self.video_info(bvid)
            cid = info.cid
        data = await self._get("/x/player/v2", {"bvid": bvid, "cid": cid})
        subtitles = data.get("subtitle", {}).get("subtitles", [])
        tracks = []
        for s in subtitles:
            url = s.get("subtitle_url", "")
            if url and not url.startswith("http"):
                url = "https:" + url
            tracks.append(SubtitleTrack(
                lan=s.get("lan", ""),
                lan_doc=s.get("lan_doc", ""),
                url=url,
            ))
        log.info(f"字幕: 找到 {len(tracks)} 条字幕轨")
        return tracks

    async def subtitle_content(self, subtitle_url: str) -> list[SubtitleLine]:
        """下载并解析字幕JSON内容"""
        if not subtitle_url:
            return []
        if not subtitle_url.startswith("http"):
            subtitle_url = "https:" + subtitle_url
        c = self._ensure_client()
        resp = await c.get(subtitle_url)
        if resp.status_code != 200:
            log.warning(f"字幕下载失败: HTTP {resp.status_code}")
            return []
        data = resp.json()
        lines = []
        for b in data.get("body", []):
            lines.append(SubtitleLine(
                start=float(b.get("from", 0)),
                end=float(b.get("to", 0)),
                text=b.get("content", ""),
            ))
        log.info(f"字幕内容: {len(lines)} 条时间轴")
        return lines

    async def play_info(self, bvid: str, cid: int = None) -> PlayInfo:
        """获取视频播放地址(DASH流)"""
        if cid is None:
            info = await self.video_info(bvid)
            cid = info.cid
        params = {
            "bvid": bvid,
            "cid": cid,
            "fnver": 0,
            "fnval": 4048,
            "fourk": 1,
        }
        data = await self._get("/x/player/playurl", params)
        pi = PlayInfo(
            quality=data.get("quality", 0),
            accept_quality=data.get("accept_quality", []),
            duration=data.get("timelength", 0) // 1000,
        )
        dash = data.get("dash", {})
        for a in dash.get("audio", []):
            base_url = a.get("base_url", "") or (a.get("backup_url") or [""])[0]
            pi.audio_streams.append(AudioStream(
                id=a.get("id", 0),
                url=base_url,
                bandwidth=a.get("bandwidth", 0),
                codec=a.get("codecs", ""),
                mime=a.get("mimeType", ""),
            ))
        for v in dash.get("video", []):
            base_url = v.get("base_url", "") or (v.get("backup_url") or [""])[0]
            pi.video_streams.append({
                "id": v.get("id"),
                "url": base_url,
                "width": v.get("width", 0),
                "height": v.get("height", 0),
                "bandwidth": v.get("bandwidth", 0),
                "codecid": v.get("codecid"),
            })
        log.info(f"播放信息: {len(pi.audio_streams)} 音频轨, {len(pi.video_streams)} 视频轨")
        return pi

    async def get_lyrics(self, bvid: str) -> list[SubtitleLine]:
        """一键获取歌词: 找字幕→下载→返回时间轴"""
        tracks = await self.subtitles(bvid)
        for track in tracks:
            if track.url:
                lines = await self.subtitle_content(track.url)
                if lines:
                    log.info(f"歌词: 使用字幕 '{track.lan_doc}' ({len(lines)} 行)")
                    return lines
        log.info("歌词: 无可用字幕")
        return []

    async def download_video(
        self, bvid: str, output_dir: str = "downloads",
        merge: bool = True, keep_temp: bool = False
    ) -> str:
        """
        下载B站视频并合成为MP4。
        先用-c copy快速合成，失败则重编码为H.264+AAC。
        返回最终文件路径。
        """
        info = await self.video_info(bvid)
        pi = await self.play_info(bvid, info.cid)
        os.makedirs(output_dir, exist_ok=True)

        safe_title = re.sub(r'[\\/:*?"<>|]', "_", info.title)[:80]
        base = f"{safe_title}_{bvid}"

        # .m4s 本质是fragmented mp4，存为.mp4让ffmpeg正确识别
        audio_raw = os.path.join(output_dir, f"{base}_audio.m4s")
        video_raw = os.path.join(output_dir, f"{base}_video.m4s")
        audio_mp4 = os.path.join(output_dir, f"{base}_audio.mp4")
        video_mp4 = os.path.join(output_dir, f"{base}_video.mp4")
        output_path = os.path.join(output_dir, f"{base}.mp4")

        best_audio = max(pi.audio_streams, key=lambda x: x.bandwidth) if pi.audio_streams else None
        best_video = max(pi.video_streams, key=lambda x: x["height"]) if pi.video_streams else None

        if not best_audio and not best_video:
            raise Exception("无可下载的流")

        c = self._ensure_client()
        log.info(f"下载中: {info.title}")
        update_dl_progress(bvid, 0, "downloading", info.title)

        total_steps = (1 if best_audio else 0) + (1 if best_video else 0)
        done_steps = 0

        def _pct():
            nonlocal done_steps
            return (done_steps * 100) // max(1, total_steps)

        # ── 下载 ────────────────────────────────────
        if best_audio:
            log.info(f"  音频: {best_audio.bandwidth//1000}kbps")
            await self._download_file(c, best_audio.url, audio_raw, bvid, _pct())
            done_steps += 1
            update_dl_progress(bvid, _pct(), "downloading", info.title)
            log.info(f"  音频下载完成 ({os.path.getsize(audio_raw)//1024}KB)")

        if best_video:
            log.info(f"  视频: {best_video['width']}x{best_video['height']}")
            await self._download_file(c, best_video["url"], video_raw, bvid, _pct())
            done_steps += 1
            update_dl_progress(bvid, _pct(), "downloading", info.title)
            log.info(f"  视频下载完成 ({os.path.getsize(video_raw)//1024}KB)")

        # ── remux .m4s → .mp4 (修正moov box位置) ──
        for src, dst in [(audio_raw, audio_mp4), (video_raw, video_mp4)]:
            if os.path.exists(src) and os.path.getsize(src) > 0:
                try:
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-f", "mp4", "-i", src,
                        "-c", "copy",
                        "-movflags", "+faststart",
                        dst,
                    ], check=True, capture_output=True, text=True)
                    log.debug(f"  remux: {os.path.basename(dst)}")
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    log.warning(f"  remux失败: {e}, 直接用原始文件")
                    if os.path.exists(src):
                        os.rename(src, dst)

        # ── 合并 ────────────────────────────────────
        if merge and best_audio and best_video and os.path.exists(audio_mp4) and os.path.exists(video_mp4):
            log.info("  FFmpeg合并(音视频)...")
            # 先尝试快速复制(copy)，不支持则转码
            for attempt, cmd in enumerate([
                # 尝试1: 直接复制(仅重封装)
                ["ffmpeg", "-y",
                 "-i", video_mp4,
                 "-i", audio_mp4,
                 "-c", "copy",
                 "-movflags", "+faststart",
                 output_path],
                # 尝试2: 视频转H.264 + 音频复制
                ["ffmpeg", "-y",
                 "-i", video_mp4,
                 "-i", audio_mp4,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "copy",
                 "-movflags", "+faststart",
                 output_path],
                # 尝试3: 都转码
                ["ffmpeg", "-y",
                 "-i", video_mp4,
                 "-i", audio_mp4,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart",
                 output_path],
            ]):
                try:
                    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                    log.info(f"  合并完成 (尝试{attempt+1})")
                    update_dl_progress(bvid, 100, "ready", info.title)
                    if not keep_temp:
                        for p in [audio_raw, video_raw, audio_mp4, video_mp4]:
                            if os.path.exists(p):
                                os.remove(p)
                    return output_path
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    log.warning(f"  尝试{attempt+1}失败: {e}")
                    continue

            log.error("  所有合并尝试均失败")
            # 回退: 返回音频
            if os.path.exists(audio_mp4):
                update_dl_progress(bvid, 100, "ready", info.title)
                return audio_mp4

        # ── 仅音频 ────────────────────────────────
        if best_audio:
            src = audio_mp4 if os.path.exists(audio_mp4) else audio_raw
            if os.path.exists(src):
                out = src.replace("_audio.mp4", ".aac").replace("_audio.m4s", ".aac")
                try:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", src,
                        "-c", "copy",
                        "-bsf:a", "aac_adtstoasc",
                        out,
                    ], check=True, capture_output=True)
                    log.info(f"  音频完成: {out}")
                    if not keep_temp:
                        os.remove(src)
                    return out
                except (subprocess.CalledProcessError, FileNotFoundError):
                    pass
                return src

        raise Exception("下载失败")

    @staticmethod
    async def _download_file(client: httpx.AsyncClient, url: str, path: str,
                              bvid: str = "", base_pct: int = 0):
        """流式下载文件"""
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            last_report = 0
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and bvid:
                        pct = downloaded * 100 // total
                        overall = base_pct + (pct * (100 - base_pct) // 100)
                        if overall > last_report + 4:
                            last_report = overall
                            update_dl_progress(bvid, min(99, overall), "downloading")

    async def __aenter__(self):
        self._ensure_client()
        return self

    async def __aexit__(self, *args):
        await self.close()
