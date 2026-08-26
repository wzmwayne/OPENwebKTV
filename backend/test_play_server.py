#!/usr/bin/env python3
"""
OwK 播放测试服务器 (端口 8090)

用途: 最小化复现"自动切歌后无声音/不播放"问题。
行为: 网页连接 WS 后服务器下发 play 1(自动播放被浏览器策略阻止时, 点击按钮即可);
      第一首播完自动切第二首(可在页面关闭"自动切歌")。

页面提供指令控制(按钮/键盘), 无需依赖自动播放策略:
  ▶ 播放 / ⏸ 暂停 / ⏮ SONG 1 / ⏭ SONG 2 / 静音切换 / 自动切歌开关 / ↻ 重载
  快捷键: Space=播放暂停, 1=SONG1, 2=SONG2, M=静音, R=重载

状态面板实时显示: 当前歌曲 / muted / volume / paused / readyState / 进度 / play()是否被拒。
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
<title>OwK 播放测试(指令控制)</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#000;color:#fff;font-family:sans-serif}
video{position:fixed;top:0;left:0;width:100%;height:100%;object-fit:contain}
#status{position:fixed;top:12px;left:12px;z-index:9;background:rgba(0,0,0,.75);
  padding:8px 14px;border-radius:8px;font-size:14px;line-height:1.6;white-space:pre}
#status .ok{color:#8f8}#status .bad{color:#f88}
#warn{position:fixed;top:64px;left:12px;z-index:9;background:rgba(120,0,0,.85);
  padding:6px 12px;border-radius:8px;font-size:13px;color:#fcc;display:none}
#hint{position:fixed;bottom:88px;left:50%;transform:translateX(-50%);z-index:9;
  background:rgba(124,92,252,.92);color:#fff;padding:10px 22px;border-radius:10px;
  font-size:15px;display:none;white-space:nowrap;animation:hintblink 1.2s infinite}
@keyframes hintblink{0%,100%{opacity:1}50%{opacity:.55}}
#btns{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);z-index:9;
  display:flex;gap:8px;flex-wrap:wrap;justify-content:center;background:rgba(0,0,0,.7);
  padding:10px 14px;border-radius:12px;max-width:92vw}
#btns button{background:#2a2a3a;color:#fff;border:none;border-radius:8px;
  padding:10px 16px;font-size:14px;cursor:pointer}
#btns button:hover{background:#7c5cfc}
#btns button.hot{background:#7c5cfc}
#mode{position:fixed;top:12px;right:12px;z-index:9;background:rgba(0,0,0,.7);
  padding:8px 14px;border-radius:8px;font-size:12px;color:#aaa}
</style>
</head>
<body>
<video id="v" playsinline controls></video>
<div id="mode">指令控制版 — 快捷键: Space=播放/暂停 1=SONG1 2=SONG2 M=静音 R=重载</div>
<div id="warn">⚠ 自动播放被浏览器阻止 — 请点击底部【▶ 播放】</div>
<div id="hint">⬆ 按上方向键解除静音</div>
<div id="status"></div>
<div id="btns">
  <button class="hot" onclick="cmdPlay()">▶ 播放</button>
  <button onclick="cmdPause()">⏸ 暂停</button>
  <button onclick="loadVideo(1)">⏮ SONG 1</button>
  <button onclick="loadVideo(2)">⏭ SONG 2</button>
  <button id="muteBtn" onclick="toggleMute()">🔇 静音</button>
  <button id="autoBtn" onclick="toggleAuto()">⚡ 自动切歌: 开</button>
  <button onclick="reloadCur()">↻ 重载</button>
</div>
<script>
const v = document.getElementById('v');
const ws = new WebSocket('ws://' + location.host + '/ws');
let cur = 0, endedFlag = false, autoSwitch = true, playRejected = false;

function showWarn(on) {
  document.getElementById('warn').style.display = on ? 'block' : 'none';
}

let muteFallback = false, playRetries = 0;
const MAX_PLAY_RETRIES = 3;

function showWarn(on) {
  document.getElementById('warn').style.display = on ? 'block' : 'none';
}
function showHint(on) {
  document.getElementById('hint').style.display = on ? 'block' : 'none';
}

// 静音方案: 先多次尝试直接有声播放; 全部失败则静音开播并提示"按上方向键解除"
function loadVideo(id) {
  v.pause();
  v.removeAttribute('src');
  v.load();
  cur = id; endedFlag = false; playRejected = false;
  muteFallback = false; playRetries = 0;
  showWarn(false);
  showHint(false);
  v.muted = false;          // 第一步: 先尝试非静音直接播放
  v.src = `/media/${id}`;
  v.load();
  try { v.currentTime = 0; } catch(e) {}
  attemptPlay();
  document.title = 'SONG ' + id;
}

// 多次尝试自动播放(自动解除静音); 全部失败则转静音开播
function attemptPlay() {
  const p = v.play();
  if (!p || !p.catch) return;
  p.then(() => { playRetries = 0; }).catch(() => {
    playRetries++;
    if (playRetries < MAX_PLAY_RETRIES) {
      setTimeout(attemptPlay, 300);
    } else {
      playRejected = true;
      startMutedFallback();
    }
  });
}

// 静音开播 + 显示"按上方向键解除静音"
function startMutedFallback() {
  muteFallback = true;
  v.muted = true;
  const p = v.play();
  if (p && p.catch) p.catch(() => {});
  showHint(true);
}

// 用户手势解除静音(上方向键 / 播放按钮)
function unmuteByUser() {
  if (!muteFallback && !v.muted) return;
  muteFallback = false;
  v.muted = false;
  showHint(false);
  if (v.paused) v.play().catch(() => {});
}
function cmdPlay() {
  if (cur === 0) { loadVideo(1); return; }
  showWarn(false);
  unmuteByUser();           // 按钮点击=用户手势, 顺带解除静音回退
  v.play().catch(() => {});
}
function cmdPause() { v.pause(); }
function toggleMute() {
  v.muted = !v.muted;
  if (!v.muted) { muteFallback = false; showHint(false); }
  document.getElementById('muteBtn').textContent = v.muted ? '🔇 静音' : '🔊 已开声';
}
function toggleAuto() {
  autoSwitch = !autoSwitch;
  document.getElementById('autoBtn').textContent = '⚡ 自动切歌: ' + (autoSwitch ? '开' : '关');
}
function reloadCur() { if (cur > 0) loadVideo(cur); }

// 键盘指令
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space') { e.preventDefault(); (v.paused ? cmdPlay() : cmdPause()); }
  else if (e.key === '1') loadVideo(1);
  else if (e.key === '2') loadVideo(2);
  else if (e.key.toLowerCase() === 'm') toggleMute();
  else if (e.key.toLowerCase() === 'r') reloadCur();
  else if (e.key === 'ArrowUp') unmuteByUser();
});

// WS: 服务器指令(连接即 play 1; ended(id=1) 后 play 2)
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.type === 'play') loadVideo(m.id);
};
ws.onclose = () => { document.getElementById('status').textContent += '\\n[WS断开]'; };

// 播完上报(自动切歌开启时)
v.ontimeupdate = () => {
  const d = v.duration || 1;
  if (d - v.currentTime < 0.5 && d > 0 && !endedFlag) {
    endedFlag = true;
    if (autoSwitch && ws.readyState === 1) {
      ws.send(JSON.stringify({type:'ended', id:cur}));
    }
  }
};
v.onerror = () => { document.getElementById('status').textContent += '\\n[视频错误]'; };

// 状态面板
setInterval(() => {
  const el = document.getElementById('status');
  const bad = v.muted || playRejected;
  el.innerHTML =
    `<span class="${bad ? 'bad' : 'ok'}">当前: SONG ${cur || '-'} ${playRejected ? '(play被拒!)' : ''}</span>
muted: ${v.muted} | volume: ${v.volume.toFixed(2)} | paused: ${v.paused}
readyState: ${v.readyState} | 进度: ${v.currentTime.toFixed(1)}s / ${(v.duration||0).toFixed(1)}s
播放尝试: ${playRetries}/${MAX_PLAY_RETRIES} | 静音回退: ${muteFallback ? '是(按↑解除)' : '否'}
自动切歌: ${autoSwitch ? '开' : '关'} | WS: ${ws.readyState === 1 ? '已连' : '断开'}`;
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
    await ws.send_json({"type": "play", "id": 1})  # 连接即下发第一首(是否播取决于浏览器策略, 可点按钮)
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
    print("  OwK 播放测试服务器 (指令控制版)")
    print(f"  地址: http://127.0.0.1:{PORT}")
    print("  按钮: 播放/暂停/SONG1/SONG2/静音/自动切歌/重载")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
