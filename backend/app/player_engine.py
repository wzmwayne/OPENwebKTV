import asyncio
import logging
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Song, QueueItem
from .schemas import SongOut, QueueOut, PlayState
from .ws_manager import ws_manager
from .config import settings

log = logging.getLogger("owk.player")


class PlayerEngine:
    def __init__(self):
        self._status: str = "idle"
        self._position: float = 0
        self._volume: float = 0.8
        self._play_lock = asyncio.Lock()
        self._poll_task: asyncio.Task | None = None
        self._dl_broadcast_task: asyncio.Task | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def position(self) -> float:
        return self._position

    @property
    def volume(self) -> float:
        return self._volume

    def _playing_item(self, db: Session) -> QueueItem | None:
        return db.query(QueueItem).filter(QueueItem.status == "playing").order_by(QueueItem.order).first()

    def _ensure_playing_head(self, db: Session):
        items = db.query(QueueItem).filter(QueueItem.status.in_(["waiting", "playing"])).order_by(QueueItem.order).all()
        changed = False
        for i, item in enumerate(items):
            if i == 0 and item.status != "playing":
                item.status = "playing"
                changed = True
            elif i > 0 and item.status == "playing":
                item.status = "waiting"
                changed = True
        if changed:
            db.commit()

    def get_state(self) -> PlayState:
        db = SessionLocal()
        try:
            self._ensure_playing_head(db)
            queue = db.query(QueueItem).filter(QueueItem.status.in_(["waiting", "playing"])).order_by(QueueItem.order).all()
            cur = self._playing_item(db)
            return PlayState(
                current=SongOut.model_validate(cur.song) if cur and cur.song else None,
                queue=[QueueOut.model_validate(q) for q in queue if q.song],
                status=self._status,
                position=self._position,
                volume=self._volume,
            )
        finally:
            db.close()

    async def broadcast_state(self):
        state = self.get_state()
        await ws_manager.broadcast({"type": "state", "state": state.model_dump()})

    async def play(self):
        async with self._play_lock:
            db = SessionLocal()
            try:
                cur = self._playing_item(db)
            finally:
                db.close()
            if not cur and not await self._load_next():
                log.info("队列为空，无法播放")
                return
            self._position = 0
            self._status = "playing"
            state = self.get_state()
            await ws_manager.send_to_players({
                "type": "play",
                "song": state.current.model_dump() if state.current else None,
            })
            await ws_manager.send_to_controllers({"type": "state", "state": state.model_dump()})
            if state.current:
                log.info(f"▶ 播放: {state.current.title}")

    async def pause(self):
        self._status = "paused"
        await ws_manager.send_to_players({"type": "pause"})
        await self.broadcast_state()
        log.info("⏸ 暂停")

    async def resume(self):
        if self._status == "paused":
            self._status = "playing"
            await ws_manager.send_to_players({"type": "resume"})
            await self.broadcast_state()
            log.info("▶ 恢复播放")

    async def next(self):
        db = SessionLocal()
        try:
            cur = self._playing_item(db)
            if cur:
                cur.status = "played"
                db.commit()
        finally:
            db.close()
        await self.play()

    async def prev(self):
        db = SessionLocal()
        try:
            cur = self._playing_item(db)
            # 找到上一首：最近一个已播歌曲
            prev_item = db.query(QueueItem).filter(QueueItem.status == "played").order_by(QueueItem.order.desc()).first()
            if not prev_item:
                # 没有上一首，无操作
                return
            # 取出当前和上一首，重新排序：上一首放前，当前放后
            items = db.query(QueueItem).filter(QueueItem.id.in_([cur.id, prev_item.id])).all() if cur else [prev_item]
            # 标记上一首为 playing，当前为 waiting
            prev_item.status = "playing"
            if cur:
                cur.status = "waiting"
            # 交换 order 确保 playing 在头部
            tmp = prev_item.order
            prev_item.order = cur.order if cur else 0
            if cur:
                cur.order = tmp
            db.commit()
        finally:
            db.close()
        self._position = 0
        self._status = "playing"
        state = self.get_state()
        await ws_manager.send_to_players({
            "type": "play",
            "song": state.current.model_dump() if state.current else None,
        })
        await ws_manager.send_to_controllers({"type": "state", "state": state.model_dump()})
        if state.current:
            log.info(f"▶ 上一首: {state.current.title}")

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
        if self._position < 10:
            log.warning(f"歌曲播放不足10秒({self._position:.0f}s)，忽略song_end")
            return
        log.info("当前歌曲结束")
        db = SessionLocal()
        try:
            cur = self._playing_item(db)
            if cur:
                cur.status = "played"
                db.commit()
        finally:
            db.close()
        await self.play()
        if self._status == "idle":
            log.info("队列已空，停止播放")

    async def add_to_queue(self, song_id: int, db: Session) -> tuple[int | None, str]:
        count = db.query(QueueItem).filter(QueueItem.status.in_(["waiting", "playing"])).count()
        if count >= settings.MAX_QUEUE_SIZE:
            return None, "full"
        existing = db.query(QueueItem).filter(QueueItem.song_id == song_id, QueueItem.status.in_(["waiting", "playing"])).first()
        if existing:
            return existing.id, "exists"
        max_order = db.query(QueueItem).filter(QueueItem.status.in_(["waiting", "playing"])).order_by(QueueItem.order.desc()).first()
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
        was_playing = item.status == "playing"
        db.delete(item)
        db.commit()
        await self.broadcast_state()
        if was_playing:
            await self.play()

    async def reorder_queue(self, order: list[int], db: Session):
        for i, item_id in enumerate(order):
            db.query(QueueItem).filter(QueueItem.id == item_id).update({"order": i})
        db.commit()
        await self.broadcast_state()

    async def _load_next(self) -> bool:
        db = SessionLocal()
        try:
            items = db.query(QueueItem).filter(QueueItem.status.in_(["waiting", "playing"])).order_by(QueueItem.order).all()
            if not items:
                return False
            # 队列首项标记为 playing，其余为 waiting
            for i, item in enumerate(items):
                item.status = "playing" if i == 0 else "waiting"
            db.commit()
            if items[0].song:
                log.info(f"加载下一首: {items[0].song.title}")
                return True
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
