#!/usr/bin/env python3
"""
测试 bilibili.py 模块: 搜索 / 详情 / 歌词 / 下载
"""

import asyncio
import sys
import logging
import json
import time
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

COOKIE = os.path.join(os.path.dirname(__file__), "bilibili_cookie.json")
sys.path.insert(0, "app")
from bilibili import BilibiliClient


async def test_search(client):
    print("\n" + "=" * 60)
    print("  [1] 搜索测试")
    print("=" * 60)
    keyword = "周杰伦 七里香"
    items = await client.search(keyword)
    print(f"  关键词: {keyword}")
    print(f"  结果数: {len(items)}")
    for i, item in enumerate(items[:5]):
        m, s = divmod(item.duration, 60)
        print(f"    [{i+1}] {item.bvid}")
        print(f"          {item.title[:50]}")
        print(f"          UP: {item.author} | {m}:{s:02d} | {item.play_count} 播放")
    if items:
        print(f"\n  取第1个BVID用于后续测试: {items[0].bvid}")
        return items[0]
    return None


async def test_video_info(client, bvid):
    print("\n" + "=" * 60)
    print("  [2] 视频详情测试")
    print("=" * 60)
    info = await client.video_info(bvid)
    print(f"  BVID:    {info.bvid}")
    print(f"  标题:    {info.title}")
    print(f"  UP主:    {info.uploader} (uid={info.uploader_uid})")
    print(f"  CID:     {info.cid}")
    print(f"  时长:    {info.duration}秒 ({info.duration//60}分{info.duration%60}秒)")
    print(f"  封面:    {info.cover}")
    print(f"  分P:     {len(info.pages)}")
    print(f"  简介:    {info.description[:80]}..." if len(info.description) > 80 else f"  简介:    {info.description}")
    return info


async def test_lyrics(client, bvid):
    print("\n" + "=" * 60)
    print("  [3] 歌词/字幕测试")
    print("=" * 60)
    
    # 先列字幕轨
    tracks = await client.subtitles(bvid)
    if tracks:
        print(f"  字幕轨 ({len(tracks)}):")
        for t in tracks:
            print(f"    - {t.lan_doc} ({t.lan}): {t.url[:60]}...")
    
    # 获取歌词
    lyrics = await client.get_lyrics(bvid)
    if lyrics:
        print(f"\n  歌词预览 ({len(lyrics)} 行):")
        for line in lyrics[:10]:
            print(f"    [{line.start:6.1f}s - {line.end:6.1f}s] {line.text}")
        if len(lyrics) > 10:
            print(f"    ... 共{len(lyrics)}行")
    else:
        print("  无歌词")


async def test_play_info(client, bvid):
    print("\n" + "=" * 60)
    print("  [4] 播放地址测试")
    print("=" * 60)
    pi = await client.play_info(bvid)
    print(f"  当前画质: {pi.quality}")
    print(f"  可用画质: {pi.accept_quality}")
    print(f"  时长: {pi.duration}秒")
    
    if pi.audio_streams:
        print(f"\n  音频流 ({len(pi.audio_streams)}):")
        for a in sorted(pi.audio_streams, key=lambda x: -x.bandwidth):
            print(f"    id={a.id} | {a.bandwidth//1000}kbps | {a.codec} | {a.url[:60]}...")
    
    if pi.video_streams:
        print(f"\n  视频流 ({len(pi.video_streams)}):")
        for v in sorted(pi.video_streams, key=lambda x: -x["height"]):
            print(f"    id={v['id']} | {v['width']}x{v['height']} | {v['bandwidth']//1000}kbps | {v['url'][:60]}...")


async def test_download(client, bvid):
    print("\n" + "=" * 60)
    print("  [5] 下载测试 (仅下载一个短视频)")
    print("=" * 60)
    
    # 找一个短的视频来下载
    items = await client.search("测试 短视频", 1)
    test_bvid = None
    for item in items[:10]:
        if 10 < item.duration < 120:  # 10秒到2分钟
            test_bvid = item.bvid
            break
    if not test_bvid:
        # 用原BVID, 只下载音频
        test_bvid = bvid
        print(f"  未找到短视频, 使用原BVID: {test_bvid} (只测试音频下载)")
    
    info = await client.video_info(test_bvid)
    print(f"  下载: {info.title} ({info.duration}秒)")
    
    ts = time.time()
    path = await client.download_video(test_bvid, output_dir="test_downloads", merge=False)
    elapsed = time.time() - ts
    
    size_kb = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
    print(f"  结果: {path}")
    print(f"  大小: {size_kb}KB")
    print(f"  耗时: {elapsed:.1f}秒")
    if elapsed > 0:
        print(f"  速度: {size_kb // elapsed:.0f} KB/s")


async def main():
    import os
    print("=" * 60)
    print("  bilibili.py 模块测试")
    print("=" * 60)
    
    # 检查cookie
    if os.path.exists(COOKIE):
        print(f"  Cookie: {COOKIE} ✓")
    else:
        print(f"  Cookie: {COOKIE} ✗ (未找到)")
        return
    
    # 检查ffmpeg
    has_ffmpeg = False
    try:
        import subprocess
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        has_ffmpeg = True
        print("  FFmpeg: ✓")
    except:
        print("  FFmpeg: ✗ (跳过下载测试)")
    
    async with BilibiliClient(cookie_path=COOKIE) as client:
        # 测试搜索
        top = await test_search(client)
        if not top:
            print("\n搜索失败!")
            return
        
        bvid = top.bvid
        
        # 测试视频详情
        await test_video_info(client, bvid)
        
        # 测试歌词
        await test_lyrics(client, bvid)
        
        # 测试播放地址
        await test_play_info(client, bvid)
        
        # 测试下载 (需要FFmpeg)
        if has_ffmpeg:
            await test_download(client, bvid)
        else:
            print("\n" + "=" * 60)
            print("  [5] 下载测试: 跳过 (需要FFmpeg)")
            print("=" * 60)
    
    print("\n" + "=" * 60)
    print("  全部测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
