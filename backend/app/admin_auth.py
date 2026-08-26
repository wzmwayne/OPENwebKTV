"""
高级操作全局状态机(静态密码 + 动态验证码 双因子)。

设计约定(用户指定): **不做接口级鉴权**, 整个系统只有一个全局状态
「是否允许高级操作」:
  phase: none → code(播放端阻塞, 显示8位动态码) → active(允许高级操作, 60s) → none
- active 期间前端显示/启用高级操作(退出登录B站/删除歌曲/清空播放列表等)
- 到期或取消自动回 none 并广播, 前端据此禁用
- LAN 信任模型: 高级操作 REST 端点本身不挂鉴权依赖, 由前端按本状态门控
"""
import asyncio
import logging
import secrets
import time

from .config import settings

log = logging.getLogger("owk.admin")

CODE_TTL = 120   # 8位动态码有效期(秒), 过期自动换新码
ACTIVE_TTL = 60  # 高级操作授权时长(秒)

_phase = "none"          # none | code | active
_code = ""
_code_expires = 0.0
_admin_until = 0.0
_code_attempts = 0
_loop_task: asyncio.Task | None = None


def check_static(pwd: str) -> bool:
    """静态密码校验(常量时间比较)"""
    return secrets.compare_digest(str(pwd or ""), str(settings.ADMIN_STATIC_PASSWORD))


def begin_verification() -> str:
    """静态密码通过后调用: 生成8位动态码, 进入 code 阶段"""
    global _phase, _code, _code_expires, _code_attempts
    _code = f"{secrets.randbelow(10 ** 8):08d}"
    _code_expires = time.time() + CODE_TTL
    _code_attempts = 0
    _phase = "code"
    return _code


def rotate_code() -> str:
    """动态码过期自动换新码"""
    global _code, _code_expires, _code_attempts
    _code = f"{secrets.randbelow(10 ** 8):08d}"
    _code_expires = time.time() + CODE_TTL
    _code_attempts = 0
    return _code


def submit_code(code: str) -> bool:
    """提交动态码: 单次有效; 成功后进入 active 阶段(授权窗口)"""
    global _phase, _admin_until, _code
    if _phase != "code" or time.time() > _code_expires:
        return False
    if secrets.compare_digest(str(code or ""), _code):
        _phase = "active"
        _admin_until = time.time() + ACTIVE_TTL
        _code = ""
        return True
    return False


def cancel() -> None:
    global _phase, _code
    _phase = "none"
    _code = ""


def status(with_code: bool = False) -> dict:
    """全局状态(默认不含动态码, 防访客控制器窃码; 播放端传 with_code=True)"""
    global _phase
    now = time.time()
    if _phase == "active" and now > _admin_until:   # 到期惰性回 none
        _phase = "none"
    st = {
        "phase": _phase,
        "allowed": _phase == "active",   # 「是否允许高级操作」
        "code_ttl": max(0, int(_code_expires - now)) if _phase == "code" else 0,
        "admin_remaining": max(0, int(_admin_until - now)) if _phase == "active" else 0,
    }
    if with_code and _phase == "code":
        st["code"] = _code
    return st


async def _tick_loop():
    """服务器权威驱动: 1s 一轮。
    - active 到期 → phase 置 none + 播放端解除阻塞 + 广播
    - code 过期 → 自动换新码 + 广播(播放端刷新显示)
    """
    from .ws_manager import ws_manager
    from .player_engine import player_engine
    last_phase = None
    while True:
        try:
            st = status()   # 内部会做 active 到期惰性回 none
            phase = st["phase"]
            if phase != last_phase:
                if last_phase in ("code", "active") and phase == "none":
                    await player_engine.release_admin_block()
                last_phase = phase
                await _broadcast()
            elif phase == "code" and st["code_ttl"] <= 0:
                rotate_code()
                await _broadcast()
        except Exception as e:
            log.exception("高级操作状态循环异常")
        await asyncio.sleep(1)


async def _broadcast(st: dict | None = None):
    """播放端带动态码(需在电视屏读取), 控制端不带(防访客控制器窃码)"""
    from .ws_manager import ws_manager
    await ws_manager.send_to_players({"type": "admin", **status(with_code=True)})
    await ws_manager.send_to_controllers({"type": "admin", **status(with_code=False)})


def start_loop():
    global _loop_task
    if _loop_task is None:
        _loop_task = asyncio.create_task(_tick_loop())
        log.info("高级操作状态循环已启动")
