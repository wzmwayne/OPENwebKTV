import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ..ws_manager import ws_manager
from ..player_engine import player_engine


router = APIRouter()
log = logging.getLogger("owk.ws")


@router.websocket("/ws/player")
async def ws_player(ws: WebSocket):
    await ws_manager.register_player(ws)
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

    except WebSocketDisconnect:
        ws_manager.remove(ws)
