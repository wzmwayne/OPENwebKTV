#!/usr/bin/env python3
"""
诊断 "Shed a Light" 歌词时间错误: 对比 B站字幕 vs LRCLIB vs 数据库存储
"""
import asyncio, sys, os, json, re
sys.path.insert(0, "app")
from bilibili import BilibiliClient, parse_lrc, SubtitleLine

COOKIE = os.path.join(os.path.dirname(__file__), "bilibili_cookie.json")

def lines_info(lines, label, max_show=8):
    print(f"\n  {label} ({len(lines)} 行):")
    for i, line in enumerate(lines[:max_show]):
        print(f"    [{line.start:6.1f}s - {line.end:6.1f}s] {line.text[:50]}")
    if len(lines) > max_show:
        print(f"    ... (省略 {len(lines)-max_show} 行)")

def detect_malformed(lines):
    mixed = 0
    short = 0
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)
        has_alpha = any(c.isalpha() for c in text)
        if has_cjk and has_alpha:
            mixed += 1
        if len(text) <= 2 and not text.isdigit():
            short += 1
    total = len(lines) or 1
    return {
        "mixed_ratio": mixed / total,
        "short_ratio": short / total,
        "likely_bad": (mixed / total > 0.3) or (short / total > 0.15),
    }

def clean_title_query(s):
    s = s or ""
    s = re.sub(r"[【\[][^】\]]*[】\]]", " ", s)
    for w in ("Hi-Res", "无损", "高音质", "hires", "循环", "歌词版", "官方", "完整版",
              "现场", "live", "翻唱", "MV", "mv", "高清", "字幕", "伴奏", "播放", "音乐", "歌曲"):
        s = re.sub(re.escape(w), " ", s, flags=re.I)
    s = re.sub(r"[|｜·•\-–—,，。]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

async def fetch_lrclib_fallback(client, title, duration):
    clean = clean_title_query(title)
    print(f"  LRCLIB 搜索词: '{clean}' (时长={duration}s)")
    c = client._ensure_client()
    r = await c.get("https://lrclib.net/api/search", params={"q": clean})
    if r.status_code != 200:
        print("  LRCLIB: 请求失败")
        return []
    best = None
    for it in r.json():
        if it.get("instrumental") or not it.get("syncedLyrics"):
            continue
        d = it.get("duration") or 0
        score = abs(d - duration) if duration else 0
        if best is None or score < best[0]:
            best = (score, it)
    if not best:
        print("  LRCLIB: 未命中")
        return []
    it = best[1]
    print(f"  LRCLIB 命中: {it.get('artistName','')} - {it.get('trackName','')}")
    return parse_lrc(it["syncedLyrics"])

async def diagnose_bvid(client, bvid, label):
    print(f"\n{'='*60}")
    print(f"  {label} ({bvid})")
    print(f"{'='*60}")
    
    info = await client.video_info(bvid)
    print(f"  标题: {info.title}")
    print(f"  时长: {info.duration}秒")
    
    tracks = await client.subtitles(bvid)
    print(f"  B站字幕轨: {len(tracks)} 条")
    
    bilibili_lines = []
    for t in tracks:
        print(f"    -> 轨: lan={t.lan} ai={t.ai} url={t.url[:60]}")
        bilibili_lines = await client.subtitle_content(t.url)
        if bilibili_lines:
            lines_info(bilibili_lines, "B站字幕内容")
            diag = detect_malformed(bilibili_lines)
            print(f"    损坏检测: 混合语言率={diag['mixed_ratio']:.0%} 短行率={diag['short_ratio']:.0%} 疑似损坏={diag['likely_bad']}")
    
    lrc_lines = await fetch_lrclib_fallback(client, info.title, info.duration)
    lines_info(lrc_lines, "LRCLIB 兜底歌词")
    
    print(f"\n  时间对比:")
    if bilibili_lines:
        print(f"    B站字幕首句: {bilibili_lines[0].start:.1f}s | 末句: {bilibili_lines[-1].start:.1f}s")
    if lrc_lines:
        print(f"    LRCLIB 首句: {lrc_lines[0].start:.1f}s | 末句: {lrc_lines[-1].start:.1f}s")
    if bilibili_lines and lrc_lines:
        offset = lrc_lines[0].start - bilibili_lines[0].start
        print(f"    首句偏移: {offset:+.1f}s (LRCLIB - B站)")
    
    return bilibili_lines, lrc_lines

async def main():
    print("=" * 60)
    print("  Shed a Light 歌词时间错误诊断")
    print("=" * 60)
    
    if not os.path.exists(COOKIE):
        print(f"Cookie 不存在: {COOKIE}")
        return
    
    async with BilibiliClient(cookie_path=COOKIE) as client:
        b1, l1 = await diagnose_bvid(client, "BV1wK4y1m7bH", "【官方MV】光明之中的舞曲盛宴")
        b2, l2 = await diagnose_bvid(client, "BV1yp4y1e7n3", "[官方MV] Shed A Light 启动小曲原版")
        
        print(f"\n{'='*60}")
        print("  结论")
        print(f"{'='*60}")
        for bvid, blines, llines in [("BV1wK4y1m7bH", b1, l1), ("BV1yp4y1e7n3", b2, l2)]:
            diag = detect_malformed(blines) if blines else None
            if blines and diag and diag["likely_bad"]:
                print(f"  {bvid}: B站字幕疑似损坏(混合语言/乱码), 应降级使用 LRCLIB")
            elif not blines:
                print(f"  {bvid}: 无B站字幕, 已使用 LRCLIB (正常)")
            else:
                print(f"  {bvid}: B站字幕看起来正常")

if __name__ == "__main__":
    asyncio.run(main())
