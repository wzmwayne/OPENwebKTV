#!/usr/bin/env python3
"""
测试 download_video 的合成功能 (merge=True)
"""

import asyncio, logging, sys, os, subprocess
sys.path.insert(0, "app")
from bilibili import BilibiliClient

COOKIE = os.path.join(os.path.dirname(__file__), "bilibili_cookie.json")
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "周杰伦 七里香"
    async with BilibiliClient(cookie_path=COOKIE) as client:
        # 找短视频
        items = await client.search(keyword)
        test_bvid = None
        for item in items:
            if 30 < item.duration < 300:
                test_bvid = item.bvid
                break
        if not test_bvid:
            test_bvid = items[0].bvid

        info = await client.video_info(test_bvid)
        print(f"\n{'='*55}")
        print(f"  下载+合成测试: {info.title}")
        print(f"  BVID: {test_bvid} | 时长: {info.duration}秒")
        print(f"{'='*55}\n")

        path = await client.download_video(
            test_bvid, output_dir="test_downloads",
            merge=True, keep_temp=False
        )

        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"\n  ✅ 输出: {path}")
        print(f"  📦 大小: {size_mb:.1f}MB")

        # 验证文件
        if path.endswith(".mp4"):
            r = subprocess.run([
                "ffprobe", "-v", "quiet", "-show_entries",
                "format=duration,size,bit_rate",
                "-of", "default=noprint_wrappers=1",
                path,
            ], capture_output=True, text=True)
            print(f"  🔍 ffprobe:")
            for line in r.stdout.strip().split("\n"):
                print(f"     {line}")

            r2 = subprocess.run([
                "ffprobe", "-v", "quiet", "-show_entries",
                "stream=codec_name,codec_type,width,height",
                "-of", "default=noprint_wrappers=1",
                path,
            ], capture_output=True, text=True)
            for line in r2.stdout.strip().split("\n"):
                print(f"     {line}")


if __name__ == "__main__":
    asyncio.run(main())
