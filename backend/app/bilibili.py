"""
OwK Bilibili API Client
搜索 / 视频信息 / 字幕歌词 / 下载视频
"""

import json
import os
import time
import hashlib
import logging
import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional
import httpx

log = logging.getLogger("owk.bilibili")

COOKIE_PATH = "bilibili_cookie.json"

# B站 WBI 签名 mixin key 置换表
_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
              27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
              37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
              22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


def _wbi_mixin_key(orig: str) -> str:
    return ''.join(orig[i] for i in _MIXIN_TAB)[:32]


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
    ai: bool = False   # True = B站AI字幕(对歌声的语音转写常错乱, 优先避开)


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


# ── 选流策略 ─────────────────────────────────────────

AVC_CODEC_ID = 7  # B站 codecid: 7=H.264/AVC, 12=HEVC, 13=AV1


def pick_best_video(streams: list[dict]) -> dict | None:
    """选择最合适的视频流。

    优先 H.264 (codecid=7) —— 浏览器/电视 WebView 兼容性最好，
    这是"只有音乐没画面"问题的关键修复: 旧逻辑只按分辨率选流，
    可能选中 HEVC/AV1，Linux Chrome 等环境无 HEVC 解码 → 黑屏。
    若视频没有 H.264 流，退回分辨率最高者，由合并阶段强制转码。
    """
    if not streams:
        return None
    best = max(streams, key=lambda s: (s.get("height", 0), s.get("bandwidth", 0)))
    if best.get("codecid") == AVC_CODEC_ID:
        return best
    avc = [s for s in streams if s.get("codecid") == AVC_CODEC_ID]
    if avc:
        target = best.get("height", 0)
        return min(avc, key=lambda s: abs(s.get("height", 0) - target))
    return best


def video_needs_transcode(stream: dict) -> bool:
    """视频流不是 H.264 时，合并阶段必须转码，不能用 -c copy 保留原编码"""
    return bool(stream) and stream.get("codecid") not in (None, AVC_CODEC_ID)


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
        self._wbi_key: Optional[tuple] = None   # (mixin_key, fetched_at)
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

    async def _wbi_sign(self, params: dict) -> dict:
        """WBI签名: 返回带 wts + w_rid 的 params。

        B站 /x/player/wbi/v2 等接口必须签名, 未签名时 AI 字幕内容会不稳定/错乱。
        """
        if not self._wbi_key or time.time() - self._wbi_key[1] > 3600:
            nav = await self._get("/x/web-interface/nav")
            wbi = nav["wbi_img"]
            img = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
            sub = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
            self._wbi_key = (_wbi_mixin_key(img + sub), time.time())
        params["wts"] = int(time.time())
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["w_rid"] = hashlib.md5((query + self._wbi_key[0]).encode()).hexdigest()
        return params

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
        try:
            params = await self._wbi_sign({"bvid": bvid, "cid": cid})
            data = await self._get("/x/player/wbi/v2", params)
        except Exception:
            log.warning("WBI字幕接口失败, 回退未签名 /x/player/v2 (AI字幕可能不稳定)")
            data = await self._get("/x/player/v2", {"bvid": bvid, "cid": cid})
        subtitles = data.get("subtitle", {}).get("subtitles", [])
        tracks = []
        for s in subtitles:
            url = s.get("subtitle_url", "")
            if url and not url.startswith("http"):
                url = "https:" + url
            lan = str(s.get("lan", ""))
            tracks.append(SubtitleTrack(
                lan=lan,
                lan_doc=str(s.get("lan_doc", "") or ""),
                url=url,
                ai=lan.startswith("ai-") or s.get("ai_type") in (1, "1"),
            ))
        log.info(f"字幕: 找到 {len(tracks)} 条字幕轨"
                 f"({'AI:' + ','.join(t.lan for t in tracks if t.ai) if any(t.ai for t in tracks) else ' 无AI'})")
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
        """一键获取歌词: 找字幕→下载→返回时间轴。

        优先取非AI字幕轨(B站AI字幕对歌声的转写经常错乱/张冠李戴),
        仅当全部为AI轨时退回第一条。
        """
        tracks = await self.subtitles(bvid)
        usable = [t for t in tracks if t.url]
        pick = next((t for t in usable if not t.ai), None)
        if pick is None and usable:
            pick = usable[0]
        if pick:
            lines = await self.subtitle_content(pick.url)
            if lines:
                log.info(f"歌词: 使用字幕 '{pick.lan_doc or pick.lan}'"
                         f"{' (AI字幕)' if pick.ai else ''} ({len(lines)} 行)")
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
        best_video = pick_best_video(pi.video_streams)

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
            log.info(f"  视频: {best_video['width']}x{best_video['height']} codecid={best_video.get('codecid')}")
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
            transcode = video_needs_transcode(best_video)
            attempts = []
            if not transcode:
                # 尝试1: 直接复制(仅重封装) —— 仅当视频已是 H.264
                attempts.append(["ffmpeg", "-y",
                                 "-i", video_mp4,
                                 "-i", audio_mp4,
                                 "-c", "copy",
                                 "-movflags", "+faststart",
                                 output_path])
            # 尝试2: 视频转H.264 + 音频复制
            attempts.append(["ffmpeg", "-y",
                             "-i", video_mp4,
                             "-i", audio_mp4,
                             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                             "-c:a", "copy",
                             "-movflags", "+faststart",
                             output_path])
            # 尝试3: 都转码
            attempts.append(["ffmpeg", "-y",
                             "-i", video_mp4,
                             "-i", audio_mp4,
                             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                             "-c:a", "aac", "-b:a", "192k",
                             "-movflags", "+faststart",
                             output_path])
            if transcode:
                log.info(f"  视频编码 codecid={best_video.get('codecid')} 非 H.264，跳过直封装，强制转码 H.264")
            for attempt, cmd in enumerate(attempts):
                try:
                    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                    log.info(f"  合并完成 (尝试{attempt+1})")
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


# ── LRCLIB 免费歌词兜底(无需API key, 同步LRC歌词) ──────────────────

_LRC_UA = "OPENwebKTV/0.1 (LAN KTV)"


def guess_song_meta(title: str) -> tuple[str, str]:
    """从B站标题猜测 (track, artist): 优先《歌名》+ '- 歌手' 模式"""
    m = re.search(r"[《「]([^》」]{1,30})[》」]", title or "")
    track = m.group(1).strip() if m else ""
    artist = ""
    if m:
        rest = title[m.end():]
        am = re.search(r"[-–—|\s]\s*([A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff .·]{1,30})", rest)
        if am:
            artist = am.group(1).strip().rstrip("-–—")
    if not track:
        return "", ""
    return track, artist


def clean_title_query(s: str) -> str:
    """标题搜索前清洗: 去【】标签与常见杂质词(先词后标点, 避免 Hi-Res 拆散), 提升LRCLIB命中率"""
    s = s or ""
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)
    for w in ("Hi-Res", "无损", "高音质", "hires", "循环", "歌词版", "官方", "完整版",
              "现场", "live", "翻唱", "MV", "mv", "高清", "字幕", "伴奏", "播放", "音乐", "歌曲"):
        s = re.sub(re.escape(w), " ", s, flags=re.I)
    s = re.sub(r"[|｜·•\-–—,，。]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_lrc(lrc: str) -> list[SubtitleLine]:
    """LRC 文本 → SubtitleLine 列表(按时间排序, end取下一句start, 最后一句+2s)"""
    raw = []
    for line in (lrc or "").splitlines():
        mm = re.match(r"\[(\d+):(\d{1,2}(?:\.\d{1,3})?)\](.*)", line.strip())
        if mm:
            t = int(mm.group(1)) * 60 + float(mm.group(2))
            raw.append((t, mm.group(3).strip()))
    raw.sort(key=lambda x: x[0])
    out = []
    for i, (t, text) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else t + 2.0
        out.append(SubtitleLine(start=t, end=end, text=text))
    return out


async def _lrclib_search_pick(client: httpx.AsyncClient, q: str, duration: int):
    """搜索 q, 返回 (artistName, trackName, syncedLyrics) 或 None(按时长最接近)"""
    r = await client.get("https://lrclib.net/api/search", params={"q": q})
    if r.status_code != 200:
        return None
    best = None
    for it in r.json():
        if it.get("instrumental") or not it.get("syncedLyrics"):
            continue
        d = it.get("duration") or 0
        score = abs(d - duration) if duration else 0
        if best is None or score < best[0]:
            best = (score, it)
    if not best:
        return None
    it = best[1]
    return (it.get("artistName") or "", it.get("trackName") or "", it["syncedLyrics"])


async def fetch_lrclib_lyrics(title: str, duration: int = 0, keyword: str = "",
                              mode: str = "auto") -> list[SubtitleLine]:
    """LRCLIB(lrclib.net)免费歌词: 无key无限制(建议缓存), 失败/无歌词返回 []。

    mode:
      auto    — 播放器/下载默认: 标题解析(get) → 用户搜索词 → 原始标题(清洗)
      keyword — 预览选"第三方歌词(搜索词)": 搜索词 → 标题解析 → 原始标题
      title   — 预览选"第三方歌词(标题)": 标题解析(get) → 原始标题(清洗)
    搜索类按时长最接近择优。
    """
    track, artist = guess_song_meta(title)
    kw = re.sub(r"\s*歌曲$", "", (keyword or "").strip())
    headers = {"User-Agent": _LRC_UA}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=15) as c:
            # 1) 标题解析 → /api/get
            if mode in ("auto", "title") and track:
                params = {"artist_name": artist, "track_name": track}
                if duration:
                    params["duration"] = int(duration)
                r = await c.get("https://lrclib.net/api/get", params=params)
                if r.status_code == 200 and r.content:
                    d = r.json()
                    if d and d.get("syncedLyrics"):
                        log.info(f"LRCLIB歌词(标题解析): {artist} - {track}")
                        return parse_lrc(d["syncedLyrics"])
            # 2) 用户搜索词 → /api/search
            if mode in ("auto", "keyword") and kw:
                found = await _lrclib_search_pick(c, kw, duration)
                if found:
                    log.info(f"LRCLIB歌词(搜索词'{kw[:16]}'): {found[0]} - {found[1]}")
                    return parse_lrc(found[2])
            # 3) 原始标题(清洗) → /api/search (各模式兜底)
            qt = clean_title_query(title)
            if qt and qt != kw and qt != track:
                found = await _lrclib_search_pick(c, qt, duration)
                if found:
                    log.info(f"LRCLIB歌词(标题'{qt[:16]}'): {found[0]} - {found[1]}")
                    return parse_lrc(found[2])
    except Exception as e:
        log.warning(f"LRCLIB歌词获取失败: {e}")
    return []
