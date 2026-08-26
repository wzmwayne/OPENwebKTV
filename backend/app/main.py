import os
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from .database import init_db
from .config import settings, FRONTEND_DIR
from .routers.api import router as api_router
from .routers.ws import router as ws_router
from .player_engine import player_engine

log = logging.getLogger("owk")

app = FastAPI(title="OPENwebKTV", description="OwK - Bilibili KTV System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    init_db()
    _reset_stuck_playing()
    _dedup_queue()
    player_engine.start_poll()
    log.info("数据库初始化完成")
    log.info(f"媒体目录: {settings.MEDIA_DIR}")
    log.info(f"前端目录: {FRONTEND_DIR}")


def _reset_stuck_playing():
    """服务重启后, 把残留 status=playing 的队列项复位为 waiting。
    否则 _load_next 只查询 waiting, 这些歌永远不会再被播放。"""
    from .database import SessionLocal
    from .models import QueueItem
    db = SessionLocal()
    try:
        n = db.query(QueueItem).filter(QueueItem.status == "playing").update({"status": "waiting"})
        db.commit()
        if n:
            log.info(f"启动复位: {n} 个卡在 playing 的队列项 → waiting")
    finally:
        db.close()


def _dedup_queue():
    from .database import SessionLocal
    from .models import QueueItem
    from sqlalchemy import func
    db = SessionLocal()
    try:
        dups = (
            db.query(QueueItem.song_id, func.count(QueueItem.id).label("cnt"))
            .filter(QueueItem.status.in_(["waiting", "playing"]))
            .group_by(QueueItem.song_id)
            .having(func.count(QueueItem.id) > 1)
            .all()
        )
        for song_id, cnt in dups:
            items = (
                db.query(QueueItem)
                .filter(QueueItem.song_id == song_id, QueueItem.status.in_(["waiting", "playing"]))
                .order_by(QueueItem.order)
                .all()
            )
            for item in items[1:]:
                db.delete(item)
                log.info(f"清理重复队列: id={item.id}, song_id={song_id}")
        db.commit()
        if dups:
            log.info(f"队列去重完成: 处理 {len(dups)} 组重复")
    finally:
        db.close()


app.include_router(api_router)
app.include_router(ws_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/player.html")


# 前端静态文件
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    log.info(f"前端已挂载: {FRONTEND_DIR}")
else:
    log.warning(f"前端目录不存在: {FRONTEND_DIR}")
    log.info("请创建 frontend/ 目录并放入 player.html / controller.html")
