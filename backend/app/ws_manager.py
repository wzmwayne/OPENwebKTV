import json
import logging
from fastapi import WebSocket

log = logging.getLogger("owk.ws")


class WSManager:
    def __init__(self):
        self._players: dict[int, WebSocket] = {}
        self._controllers: dict[int, WebSocket] = {}
        self._ws_to_id: dict[int, tuple[str, int]] = {}  # id(ws) -> (type, id)
        self._player_id = 0
        self._controller_id = 0

    async def register_player(self, ws: WebSocket) -> int:
        await ws.accept()
        self._player_id += 1
        self._players[self._player_id] = ws
        self._ws_to_id[id(ws)] = ("p", self._player_id)
        log.info(f"播放器连接: id={self._player_id}")
        return self._player_id

    async def register_controller(self, ws: WebSocket) -> int:
        await ws.accept()
        self._controller_id += 1
        self._controllers[self._controller_id] = ws
        self._ws_to_id[id(ws)] = ("c", self._controller_id)
        log.info(f"控制器连接: id={self._controller_id}")
        return self._controller_id

    def remove(self, ws: WebSocket):
        pair = self._ws_to_id.pop(id(ws), None)
        if not pair:
            return
        typ, wid = pair
        if typ == "p":
            del self._players[wid]
            log.info(f"播放器断开: id={wid}")
        else:
            del self._controllers[wid]
            log.info(f"控制器断开: id={wid}")

    async def broadcast(self, msg: dict):
        """广播给所有播放器和控制器"""
        data = json.dumps(msg, ensure_ascii=False)
        for wid, ws in list(self._players.items()):
            try:
                await ws.send_text(data)
            except Exception:
                self.remove(ws)
        for wid, ws in list(self._controllers.items()):
            try:
                await ws.send_text(data)
            except Exception:
                self.remove(ws)

    async def send_to_players(self, msg: dict):
        """只发播放器"""
        data = json.dumps(msg, ensure_ascii=False)
        for wid, ws in list(self._players.items()):
            try:
                await ws.send_text(data)
            except Exception:
                self.remove(ws)

    async def send_to_controllers(self, msg: dict):
        """只发控制器"""
        data = json.dumps(msg, ensure_ascii=False)
        for wid, ws in list(self._controllers.items()):
            try:
                await ws.send_text(data)
            except Exception:
                self.remove(ws)

    @property
    def player_count(self) -> int:
        return len(self._players)

    @property
    def controller_count(self) -> int:
        return len(self._controllers)


ws_manager = WSManager()
