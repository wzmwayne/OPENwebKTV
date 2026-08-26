import asyncio
import logging
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Song, QueueItem
from .schemas import SongOut, QueueOut, PlayState
from .ws_manager import ws_manager
from .config import settings
from . import admin_auth

log = logging.getLogger("owk.player")


class PlayerEngine:
    def __init__(self):
        self._current_song: Song | None = None
        self._status: str = "idle"
        self._position: float = 0
        self._volume: float = 0.8
        self._play_lock = asyncio.Lock()
        self._poll_task: asyncio.Task | None = None
        self._dl_broadcast_task: asyncio.Task | None = None

    @property
    def current_song(self) -> Song | None:
        return self._current_song

    @property
    def status(self) -> str:
        return self._status

    @property
    def position(self) -> float:
        return self._position

    @property
    def volume(self) -> float:
        return self._volume

    def get_state(self) -> PlayState:
        db = SessionLocal()
        try:
            queue = (
                db.query(QueueItem)
                .filter(QueueItem.status.in_(["waiting", "playing"]))
                .order_by(QueueItem.order)
                .all()
            )
            current = self._current_song
            return PlayState(
                current=SongOut.model_validate(current) if current else None,
                queue=[QueueOut.model_validate(q) for q in queue if q.song],
                status=self._status,
                position=self._position,
                volume=self._volume,
                admin=admin_auth.status(),   # 高级操作全局状态(是否允许)
            )
        finally:
            db.close()

    async def broadcast_state(self):
        state = self.get_state()
        await ws_manager.broadcast({"type": "state", "state": state.model_dump()})

    async def play(self):
        async with self._play_lock:
            if self._status == "blocked":
                return   # 阻塞状态(高级操作验证)不自动起播
            if not self._current_song and not await self._load_next():
                # 队列为空: 进入空闲并广播通知前端; 探测循环(poll_loop)会持续探测新歌,
                # 与启动时行为一致(仅在状态转换时广播, 避免每秒刷屏)
                if self._status != "idle":
                    self._status = "idle"
                    log.info("队列为空，进入空闲等待")
                    await self.broadcast_state()
                return
            self._position = 0
            self._status = "playing"
            state = self.get_state()
            await ws_manager.send_to_players({
                "type": "play",
                "song": state.current.model_dump() if state.current else None,
            })
            await ws_manager.send_to_controllers({"type": "state", "state": state.model_dump()})
            log.info(f"▶ 播放: {self._current_song.title if self._current_song else '?'}")

    async def pause(self):
        self._status = "paused"
        await ws_manager.send_to_players({"type": "pause"})
        await self.broadcast_state()
        log.info("⏸ 暂停")

    async def resume(self):
        if self._status == "blocked":
            return
        if self._status == "paused":
            self._status = "playing"
            await ws_manager.send_to_players({"type": "resume"})
            await self.broadcast_state()
            log.info("▶ 恢复播放")

    async def next(self):
        if self._status == "blocked":
            return
        db = SessionLocal()
        try:
            if self._current_song:
                db.query(QueueItem).filter(
                    QueueItem.song_id == self._current_song.id,
                    QueueItem.status == "playing",
                ).update({"status": "played"})
                db.commit()
            self._current_song = None
        finally:
            db.close()
        await self.play()

    async def prev(self):
        if self._status == "blocked":
            return
        db = SessionLocal()
        try:
            if self._current_song:
                db.query(QueueItem).filter(
                    QueueItem.song_id == self._current_song.id,
                    QueueItem.status == "playing",
                ).update({"status": "waiting"})
                db.commit()
            prev_item = (
                db.query(QueueItem)
                .filter(QueueItem.status == "played")
                .order_by(QueueItem.order.desc())
                .first()
            )
            if prev_item:
                prev_item.status = "waiting"
                db.commit()
                self._current_song = prev_item.song
            else:
                self._current_song = None
        finally:
            db.close()
        if self._current_song:
            await self.play()
        else:
            self._status = "idle"
            await self.broadcast_state()

    async def seek(self, position: float):
        self._position = position
        await ws_manager.send_to_players({"type": "seek", "position": position})

    async def set_volume(self, volume: float):
        self._volume = max(0, min(1, volume))
        await ws_manager.broadcast({"type": "volume", "volume": self._volume})

    async def update_position(self, position: float, duration: float | None = None):
        self._position = position
        msg = {"type": "position", "position": position}
        if duration is not None:
            msg["duration"] = duration
        await ws_manager.send_to_controllers(msg)

    async def on_song_end(self):
        log.info("当前歌曲结束")
        db = SessionLocal()
        try:
            if self._current_song:
                db.query(QueueItem).filter(
                    QueueItem.song_id == self._current_song.id,
                    QueueItem.status == "playing",
                ).update({"status": "played"})
                db.commit()
            self._current_song = None
        finally:
            db.close()
        await self.play()
        if self._status == "idle":
            log.info("队列已空，停止播放")

    async def add_to_queue(self, song_id: int, db: Session) -> tuple[int | None, str]:
        if self._status == "blocked":
            return None, "blocked"   # 播放端阻塞期间拒绝点歌(维护中)
        count = db.query(QueueItem).filter(
            QueueItem.status.in_(["waiting", "playing"])
        ).count()
        if count >= settings.MAX_QUEUE_SIZE:
            return None, "full"
        existing = db.query(QueueItem).filter(
            QueueItem.song_id == song_id,
            QueueItem.status.in_(["waiting", "playing"]),
        ).first()
        if existing:
            return existing.id, "exists"
        max_order = db.query(QueueItem).filter(
            QueueItem.status.in_(["waiting", "playing"])
        ).order_by(QueueItem.order.desc()).first()
        next_order = (max_order.order + 1) if max_order else 0
        item = QueueItem(song_id=song_id, order=next_order, status="waiting")
        db.add(item)
        db.commit()
        db.refresh(item)
        log.info(f"加入队列: song_id={song_id}, order={next_order}")
        await self.broadcast_state()
        if self._status == "idle":
            await self.play()
        return item.id, "added"

    async def remove_from_queue(self, item_id: int, db: Session):
        item = db.query(QueueItem).filter(QueueItem.id == item_id).first()
        if not item:
            return
        is_current = self._current_song and item.song_id == self._current_song.id and item.status == "playing"
        db.delete(item)
        db.commit()
        await self.broadcast_state()
        if is_current:
            self._current_song = None
            await self.play()

    async def reorder_queue(self, order: list[int], db: Session):
        for i, item_id in enumerate(order):
            db.query(QueueItem).filter(QueueItem.id == item_id).update({"order": i})
        db.commit()
        await self.broadcast_state()

    # ── 高级操作: 阻塞状态 ──────────────────────────

    async def enter_admin_block(self):
        """进入阻塞状态(高级操作验证): 清空播放列表 + 停止当前播放"""
        async with self._play_lock:
            db = SessionLocal()
            try:
                db.query(QueueItem).filter(
                    QueueItem.status.in_(["waiting", "playing"])
                ).delete()
                db.commit()
            finally:
                db.close()
            self._current_song = None
            self._position = 0
            self._status = "blocked"
            await self.broadcast_state()
            log.info("⛔ 播放端进入阻塞状态(高级操作验证)")

    async def release_admin_block(self):
        """解除阻塞: 回到空闲(由状态机到期/取消触发)"""
        async with self._play_lock:
            if self._status != "blocked":
                return
            self._status = "idle"
            await self.broadcast_state()
            log.info("✅ 阻塞解除, 播放端回到空闲")

    async def clear_queue(self):
        """清空播放列表(高级操作): 停当前播放, 队列清空, 回空闲"""
        async with self._play_lock:
            db = SessionLocal()
            try:
                db.query(QueueItem).filter(
                    QueueItem.status.in_(["waiting", "playing"])
                ).delete()
                db.commit()
            finally:
                db.close()
            self._current_song = None
            self._position = 0
            self._status = "idle"
            await self.broadcast_state()
            log.info("🧹 播放列表已清空")

    async def stop_current(self):
        """停止当前播放(如删除正在播放的歌曲): 播下一首或回空闲"""
        db = SessionLocal()
        try:
            if self._current_song:
                db.query(QueueItem).filter(
                    QueueItem.song_id == self._current_song.id,
                    QueueItem.status == "playing",
                ).update({"status": "played"})
                db.commit()
            self._current_song = None
        finally:
            db.close()
        await self.play()

    async def _load_next(self) -> bool:
        db = SessionLocal()
        try:
            next_item = (
                db.query(QueueItem)
                .filter(QueueItem.status == "waiting")
                .order_by(QueueItem.order)
                .first()
            )
            if next_item and next_item.song:
                next_item.status = "playing"
                db.commit()
                self._current_song = next_item.song
                log.info(f"加载下一首: {self._current_song.title}")
                return True
            self._current_song = None
            return False
        finally:
            db.close()

    async def _poll_loop(self):
        while True:
            try:
                if self._status == "idle":
                    await self.play()
            except Exception as e:
                log.exception("轮询循环异常")
            await asyncio.sleep(1)

    async def _broadcast_dl_loop(self):
        while True:
            try:
                from .bilibili import get_all_dl_progress
                progress = get_all_dl_progress()
                if progress:
                    await ws_manager.send_to_controllers({
                        "type": "download_progress",
                        "downloads": progress,
                    })
            except Exception as e:
                log.exception("下载进度广播异常")
            await asyncio.sleep(2)

    def start_poll(self):
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())
        if self._dl_broadcast_task is None:
            self._dl_broadcast_task = asyncio.create_task(self._broadcast_dl_loop())


player_engine = PlayerEngine()
