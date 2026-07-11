#!/usr/bin/env python3
"""Bilibili API 集成测试脚本 - 验证各个B站API接口"""

import json
import os
import sys
import httpx
import re
from urllib.parse import quote, urlencode

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "bilibili_cookie.json")

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def load_cookies():
    try:
        with open(COOKIE_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] 加载Cookie失败: {e}")
        return {}


def build_headers():
    h = dict(BILI_HEADERS)
    cookies = load_cookies()
    if cookies.get("SESSDATA"):
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        h["Cookie"] = cookie_str
        print(f"  [Cookie] 已注入 {len(cookies)} 个cookie项")
    else:
        print("  [Cookie] 无有效Cookie")
    return h


async def log_api(prefix, resp, show_body=False):
    ct = resp.headers.get("content-type", "")
    cl = resp.headers.get("content-length", "?")
    print(f"  {prefix}: HTTP {resp.status_code}, type={ct}, len={cl}")
    if show_body:
        try:
            j = resp.json()
            print(f"  Body: {json.dumps(j, ensure_ascii=False)[:600]}")
        except Exception:
            print(f"  Body(raw): {resp.text[:300]}")


async def test_search(client):
    print(f"\n{'='*65}")
    print(f"  [1] 搜索: /x/web-interface/search/all/v2")
    print(f"{'='*65}")

    url = "https://api.bilibili.com/x/web-interface/search/all/v2"
    params = {"keyword": "周杰伦", "page": 1}

    resp = await client.get(url, params=params)
    await log_api("响应", resp)

    try:
        data = resp.json()
    except Exception as e:
        print(f"  [!] JSON解析失败: {e}")
        return None

    if data.get("code") != 0:
        print(f"  [!] API错误: {data.get('message')}")
        return None

    result = data.get("data", {})
    sections = result.get("result", [])

    bvids = []
    for section in sections:
        items = section.get("data", []) if isinstance(section.get("data"), list) else []
        for item in items:
            if "bvid" in item:
                bvids.append(item["bvid"])

    print(f"  共找到 {len(bvids)} 个视频结果")
    print()
    for i, bvid in enumerate(bvids[:8]):
        print(f"    [{i+1}] {bvid}")

    if bvids:
        print(f"\n  取第1个BVID: {bvids[0]}")
    return bvids[:5] if bvids else None


async def test_video_info(client, bvid):
    print(f"\n{'='*65}")
    print(f"  [2] 视频信息: /x/web-interface/view")
    print(f"      BVID: {bvid}")
    print(f"{'='*65}")

    url = "https://api.bilibili.com/x/web-interface/view"
    params = {"bvid": bvid}

    resp = await client.get(url, params=params)
    await log_api("响应", resp, show_body=True)

    try:
        data = resp.json()
    except Exception as e:
        print(f"  [!] JSON解析失败: {e}")
        return None

    if data.get("code") != 0:
        print(f"  [!] API错误: {data.get('message')}")
        return None

    v = data.get("data", {})
    title = v.get("title", "")
    cid = v.get("cid", 0)
    duration = v.get("duration", 0)
    owner = v.get("owner", {})
    stat = v.get("stat", {})

    print(f"  标题: {title}")
    print(f"  UP主: {owner.get('name', '')} (uid={owner.get('mid', '')})")
    print(f"  CID: {cid}")
    print(f"  时长: {duration}秒 ({duration//60}分{duration%60}秒)")
    print(f"  播放: {stat.get('view', 0)}")
    print(f"  封面: {v.get('pic', '')}")

    return {"bvid": bvid, "cid": cid, "title": title, "duration": duration,
            "uploader": owner.get("name", ""), "cover": v.get("pic", "")}


async def test_subtitles(client, bvid, cid):
    print(f"\n{'='*65}")
    print(f"  [3] 字幕: /x/player/v2")
    print(f"      BVID: {bvid}, CID: {cid}")
    print(f"{'='*65}")

    url = "https://api.bilibili.com/x/player/v2"
    params = {"bvid": bvid, "cid": cid}

    resp = await client.get(url, params=params)
    await log_api("响应", resp, show_body=True)

    try:
        data = resp.json()
    except Exception as e:
        print(f"  [!] JSON解析失败: {e}")
        return

    if data.get("code") != 0:
        print(f"  [!] API错误: {data.get('message')}")
        return

    player_data = data.get("data", {})
    subtitle_info = player_data.get("subtitle", {})
    subtitles = subtitle_info.get("subtitles", [])

    if subtitles:
        print(f"  找到 {len(subtitles)} 条字幕:")
        for sub in subtitles:
            lan = sub.get("lan_doc", "?")
            sub_url = sub.get("subtitle_url", "")
            if sub_url and not sub_url.startswith("http"):
                sub_url = "https:" + sub_url
            print(f"    - 语言: {lan}")
            print(f"      URL: {sub_url}")

            if sub_url:
                try:
                    sub_resp = await client.get(sub_url)
                    if sub_resp.status_code == 200:
                        sub_data = sub_resp.json()
                        bodies = sub_data.get("body", [])
                        print(f"      字幕内容: {len(bodies)} 条")
                        for b in bodies[:6]:
                            print(f"        [{b.get('from', 0):.1f}s - {b.get('to', 0):.1f}s] {b.get('content', '')}")
                        if len(bodies) > 6:
                            print(f"        ... 共{len(bodies)}条")
                except Exception as e:
                    print(f"      下载字幕失败: {e}")
    else:
        print(f"  无可用字幕")


async def test_playurl(client, bvid, cid):
    print(f"\n{'='*65}")
    print(f"  [4] 播放地址: /x/player/playurl")
    print(f"      BVID: {bvid}, CID: {cid}")
    print(f"{'='*65}")

    url = "https://api.bilibili.com/x/player/playurl"
    params = {"bvid": bvid, "cid": cid, "fnver": 0, "fnval": 4048, "fourk": 1}

    resp = await client.get(url, params=params)
    await log_api("响应", resp)

    try:
        data = resp.json()
    except Exception as e:
        print(f"  [!] JSON解析失败: {e}")
        print(f"  Raw: {resp.text[:300]}")
        return

    if data.get("code") != 0:
        print(f"  [!] API错误: {data.get('message')}")
        print(f"  {json.dumps(data, ensure_ascii=False)[:300]}")
        return

    play_data = data.get("data", {})
    print(f"  视频质量: {play_data.get('quality', 0)}")
    print(f"  可用质量: {play_data.get('accept_quality', [])}")

    dash = play_data.get("dash")
    if dash:
        videos = dash.get("video", [])
        audios = dash.get("audio", [])
        print(f"  DASH流: {len(videos)} 视频轨, {len(audios)} 音频轨")

        if videos:
            v = max(videos, key=lambda x: x.get("bandwidth", 0))
            print(f"  最佳视频: {v.get('width')}x{v.get('height')} | {v.get('bandwidth', 0)//1000}kbps")
            print(f"    codecid={v.get('codecid')} | base_url前80: {v.get('base_url', '')[:80]}")

        if audios:
            a = max(audios, key=lambda x: x.get("bandwidth", 0))
            print(f"  最佳音频: id={a.get('id')} | {a.get('bandwidth', 0)//1000}kbps")
            print(f"    codecid={a.get('codecid')} | base_url前80: {a.get('base_url', '')[:80]}")

            # 检查音频格式
            mime = a.get("mimeType", "")
            codecs = a.get("codecs", "")
            print(f"    格式: {mime} | codec: {codecs}")
    else:
        durl = play_data.get("durl", [])
        if durl:
            print(f"  非DASH流: {len(durl)} 分片")
            print(f"    首片: {durl[0].get('url', '')[:100]}")


async def test_uplay_info(client, bvid, cid):
    print(f"\n{'='*65}")
    print(f"  [5] UPlay信息: /x/player/wbi/v2")
    print(f"      BVID: {bvid}, CID: {cid}")
    print(f"{'='*65}")

    url = "https://api.bilibili.com/x/player/wbi/v2"
    params = {"bvid": bvid, "cid": cid}

    resp = await client.get(url, params=params)
    await log_api("响应", resp, show_body=True)


async def main():
    print("=" * 65)
    print("         Bilibili API 集成测试")
    print("=" * 65)

    headers = build_headers()
    keyword = sys.argv[1] if len(sys.argv) > 1 else "周杰伦"

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # 1. 搜索
        bvids = await test_search(client)
        if not bvids:
            print("\n[!] 搜索失败，使用预设BVID")
            bvids = ["BV1FPjy6TEiE"]

        bvid = bvids[0]

        # 2. 视频信息
        info = await test_video_info(client, bvid)
        if not info:
            print("\n[!] 获取视频信息失败")
            return

        cid = info["cid"]

        # 3. 字幕
        await test_subtitles(client, bvid, cid)

        # 4. 播放地址
        await test_playurl(client, bvid, cid)

        # 5. 额外: wbi/v2
        await test_uplay_info(client, bvid, cid)

    print(f"\n{'='*65}")
    print("  测试完成")
    print(f"{'='*65}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
