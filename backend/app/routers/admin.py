"""
高级操作(静态密码 + 动态验证码 双因子)。

流程(用户确认):
1. POST /api/admin/verify {static_password} — 静态密码正确 → 自动清空播放列表 +
   播放端进入阻塞状态并显示8位动态码(120s, 过期自动换新码, 阻塞持续到完成或取消)
2. POST /api/admin/code {code} — 输入动态码 → 获得1分钟「是否允许高级操作」授权窗口
3. 到期自动回 none(admin_auth 状态循环驱动): 播放端解除阻塞回空闲屏, 前端禁用高级操作

约定(用户指定): **不做接口级鉴权**, 以 admin_auth 全局状态为准,
高级操作端点本身不挂鉴权依赖, 由前端按状态门控(LAN 信任模型)。
"""
import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import admin_auth
from ..database import SessionLocal
from ..models import Song, QueueItem, PlaylistSong
from .api import clear_lyrics_cache
from ..player_engine import player_engine
from ..ws_manager import ws_manager

router = APIRouter(prefix="/api/admin")
log = logging.getLogger("owk.admin")


class VerifyReq(BaseModel):
    static_password: str = ""


class CodeReq(BaseModel):
    code: str = ""


async def _broadcast_admin(st: dict | None = None):
    """播放端带动态码(需在电视屏读取), 控制端不带(防访客控制器窃码)"""
    await ws_manager.send_to_players({"type": "admin", **admin_auth.status(with_code=True)})
    await ws_manager.send_to_controllers({"type": "admin", **admin_auth.status(with_code=False)})


@router.post("/verify")
async def admin_verify(body: VerifyReq):
    """静态密码验证: 通过 → 清空播放列表 + 阻塞播放端 + 生成8位动态码"""
    if not admin_auth.check_static(body.static_password):
        raise HTTPException(401, "密码错误")
    await player_engine.enter_admin_block()   # 清空播放列表 + 阻塞
    code = admin_auth.begin_verification()
    st = admin_auth.status()
    await _broadcast_admin(st)
    log.info(f"高级操作验证开始: 播放端已阻塞, 动态码={code}, 有效期{st['code_ttl']}s")
    return {"status": "ok", "phase": "code", "code_ttl": st["code_ttl"]}


@router.post("/code")
async def admin_code(body: CodeReq):
    """提交动态码: 成功 → active(1分钟授权窗口)"""
    if not admin_auth.submit_code(body.code):
        raise HTTPException(401, "验证码错误")
    st = admin_auth.status()
    await _broadcast_admin(st)
    log.info(f"高级操作授权成功: 剩余 {st['admin_remaining']}s")
    return {"status": "ok", "phase": "active", "admin_remaining": st["admin_remaining"]}


@router.get("/status")
async def admin_status_endpoint():
    """全局状态: {phase, allowed, code_ttl, admin_remaining}(无动态码)"""
    return admin_auth.status()


@router.post("/cancel")
async def admin_cancel():
    """取消验证/授权: 回 none, 播放端解除阻塞"""
    was = admin_auth.status()["phase"]
    admin_auth.cancel()
    st = admin_auth.status()
    await _broadcast_admin(st)
    if was in ("code", "active"):
        await player_engine.release_admin_block()
    log.info("高级操作已取消")
    return {"status": "ok", "phase": "none"}


# ── 高级操作(前端按全局状态门控, 不做接口级鉴权) ──


@router.post("/queue/clear")
async def admin_clear_queue():
    """清空播放列表"""
    await player_engine.clear_queue()
    return {"status": "ok"}


@router.delete("/songs/{song_id}")
async def admin_delete_song(song_id: int):
    """删除本地歌曲: 数据库行 + 媒体文件, 并清理队列/歌单引用"""
    db = SessionLocal()
    title = ""
    file_path = ""
    try:
        song = db.query(Song).filter(Song.id == song_id).first()
        if not song:
            raise HTTPException(404, "歌曲不存在")
        title = song.title
        file_path = song.file_path or ""
        bvid = song.bvid
        db.query(QueueItem).filter(QueueItem.song_id == song_id).delete()
        db.query(PlaylistSong).filter(PlaylistSong.song_id == song_id).delete()
        if player_engine.current_song and player_engine.current_song.id == song_id:
            await player_engine.stop_current()   # 正在播放则停播并切下一首/空闲
        db.delete(song)
        clear_lyrics_cache(bvid)   # 内存歌词缓存同步清理(避免重下后命中旧歌词)
        db.commit()
    finally:
        db.close()
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            log.warning(f"删除媒体文件失败 {file_path}: {e}")
    log.info(f"已删除歌曲: {title} (id={song_id})")
    return {"status": "ok"}
