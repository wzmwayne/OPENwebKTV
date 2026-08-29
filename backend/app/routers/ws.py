import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ..ws_manager import ws_manager
from ..player_engine import player_engine
from .. import admin_auth
from ..lyric_settings import load_settings


router = APIRouter()
log = logging.getLogger("owk.ws")


@router.websocket("/ws/player")
async def ws_player(ws: WebSocket):
    await ws_manager.register_player(ws)
    # 连接后立即推送当前状态, 新打开的播放器页能同步当前歌曲/播放状态
    state = player_engine.get_state()
    await ws.send_json({"type": "state", "state": state.model_dump()})
    # 若处于高级操作验证/授权阶段, 补推 admin 消息(播放端需显示动态码; 中途重连场景)
    ast = admin_auth.status()
    if ast["phase"] in ("code", "active"):
        await ws.send_json({"type": "admin", **admin_auth.status(with_code=True)})
    # 推送歌词视觉效果设置
    await ws.send_json({"type": "lyric_settings", **load_settings()})
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "position_update":
                await player_engine.update_position(data.get("position", 0), data.get("duration"))
            elif msg_type == "song_end":
                await player_engine.on_song_end()
            elif msg_type == "volume":
                await player_engine.set_volume(data.get("volume", 0.8))
            elif msg_type == "seek":
                await player_engine.seek(data.get("position", 0))
    except WebSocketDisconnect:
        ws_manager.remove(ws)


@router.websocket("/ws/controller")
async def ws_controller(ws: WebSocket):
    await ws_manager.register_controller(ws)
    # 连接后立即推送完整状态
    state = player_engine.get_state()
    await ws.send_json({"type": "state", "state": state.model_dump()})
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "control":
                action = data.get("action", "")
                if action == "play":
                    await player_engine.play()
                elif action == "pause":
                    await player_engine.pause()
                elif action == "resume":
                    await player_engine.resume()
                elif action == "next":
                    await player_engine.next()
                elif action == "prev":
                    await player_engine.prev()
            elif msg_type == "seek":
                await player_engine.seek(data.get("position", 0))
            elif msg_type == "volume":
                await player_engine.set_volume(data.get("volume", 0.8))
            elif msg_type == "lyric_settings":
                from ..lyric_settings import save_settings
                saved = save_settings(data)
                await ws_manager.send_to_players({"type": "lyric_settings", **saved})

    except WebSocketDisconnect:
        ws_manager.remove(ws)
