#!/usr/bin/env python3
"""
OwK 播放测试服务器 (端口 8090)

用途: 最小化复现"自动切歌后无声音"问题。
行为: 网页连接 WS 后立即播放第一首; 第一首播完(客户端上报 ended)后自动切第二首。
播放逻辑与 frontend/player.html 完全一致(含 muted 启动→100ms 后取消静音技巧)。

页面显示: 当前歌曲号 / muted / volume / paused / readyState / 播放进度,
并标注 play() 是否被浏览器拒绝(autoplay 策略)。
可用 ?mode=direct 关闭静音技巧(直接播放), 与默认模式对比。

依赖: fastapi + uvicorn (项目已有)
"""

import json
import os
import logging

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, HTMLResponse

BASE = os.path.dirname(os.path.abspath(__file__))
MEDIA = {
    1: os.path.join(BASE, "data", "test_media", "song1.mp4"),
    2: os.path.join(BASE, "data", "test_media", "song2.mp4"),
}
PORT = int(os.environ.get("TEST_PORT", 8090))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("owk-test")

app = FastAPI(title="OwK 播放测试服务器")

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>OwK 播放测试</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#000;color:#fff;font-family:sans-serif}
video{position:fixed;top:0;left:0;width:100%;height:100%;object-fit:contain}
#status{position:fixed;top:12px;left:12px;z-index:9;background:rgba(0,0,0,.7);
  padding:8px 14px;border-radius:8px;font-size:14px;line-height:1.6;white-space:pre}
#status .ok{color:#8f8}#status .bad{color:#f88}
#mode{position:fixed;top:12px;right:12px;z-index:9;background:rgba(0,0,0,.7);
  padding:8px 14px;border-radius:8px;font-size:12px;color:#aaa}
</style>
</head>
<body>
<video id="v" playsinline></video>
<div id="mode"></div>
<div id="status"></div>
<script>
const v = document.getElementById('v');
const mode = new URLSearchParams(location.search).get('mode') || 'muted';
document.getElementById('mode').textContent =
  mode === 'direct' ? '模式: direct(无静音技巧)' : '模式: muted(静音启动→100ms 取消)';

const ws = new WebSocket('ws://' + location.host + '/ws');
let cur = 0, endedFlag = false, playRejected = false, lastUnmute = '-';

// 与真实 player.html 相同的换源逻辑
function loadVideo(id) {
  v.pause();
  v.removeAttribute('src');
  v.load();
  cur = id; endedFlag = false; playRejected = false; lastUnmute = '-';
  if (mode === 'muted') v.muted = true;
  v.src = `/media/${id}`;
  v.load();
  try { v.currentTime = 0; } catch(e) {}
  const p = v.play();
  if (p && p.catch) {
    p.then(() => {
      if (mode === 'muted') {
        setTimeout(() => { v.muted = false; lastUnmute = '已取消'; }, 100);
      }
    }).catch(() => { playRejected = true; });
  }
}

v.ontimeupdate = () => {
  const d = v.duration || 1;
  if (d - v.currentTime < 0.5 && d > 0 && !endedFlag) {
    endedFlag = true;
    ws.send(JSON.stringify({type:'ended', id:cur}));
  }
};
v.onerror = () => { document.getElementById('status').textContent += '\\n[视频错误]'; };
v.onvolumechange = () => { lastUnmute = v.muted ? 'muted=true' : 'muted=false'; };

ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.type === 'play') loadVideo(m.id);
};
ws.onclose = () => { document.getElementById('status').textContent += '\\n[WS断开]'; };

setInterval(() => {
  const el = document.getElementById('status');
  const cls = (v.muted || playRejected) ? 'bad' : 'ok';
  el.innerHTML =
    `<span class="${cls}">当前歌曲: SONG ${cur}  (${mode})</span>
muted: ${v.muted} | volume: ${v.volume.toFixed(2)} | paused: ${v.paused}
readyState: ${v.readyState} | 进度: ${v.currentTime.toFixed(1)}s / ${(v.duration||0).toFixed(1)}s
play() 被拒: ${playRejected} | 取消静音: ${lastUnmute} | ended 已报: ${endedFlag}`;
}, 200);
</script>
</body>
</html>"""


@app.get("/")
async def index():
    return HTMLResponse(PAGE)


@app.get("/media/{idx}")
async def media(idx: int):
    path = MEDIA.get(idx)
    if not path or not os.path.exists(path):
        return HTMLResponse("404", status_code=404)
    return FileResponse(path, media_type="video/mp4")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("播放器连接")
    await ws.send_json({"type": "play", "id": 1})  # 连接即播第一首
    try:
        while True:
            data = json.loads(await ws.receive_text())
            if data.get("type") == "ended" and data.get("id") == 1:
                log.info("第一首播完 → 切第二首")
                await ws.send_json({"type": "play", "id": 2})
    except Exception:
        pass
    finally:
        log.info("播放器断开")


if __name__ == "__main__":
    print("=" * 50)
    print("  OwK 播放测试服务器")
    print(f"  地址: http://127.0.0.1:{PORT}")
    print("  模式: muted(默认, 复现真实播放器) / ?mode=direct")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
