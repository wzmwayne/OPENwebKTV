"""
B站登录(集成在主服务内):
- 播放端未登录时显示二维码(/api/login/qr → PNG), 轮询 /api/login/poll
- 扫码确认后自动保存 cookie; /api/login/qr/refresh 由控制端触发重新生成
  (生成新 key 后广播 login_refresh 给播放端, 播放端同步刷新二维码)
- /api/login/logout 退出登录(删除 cookie + 广播)

可靠性要点(解决"一直等待扫码"):
1. 使用**持久 httpx 会话**, buvid3 等 cookie 自动保留并在 poll 时携带
   (每次新建 client 会丢失 buvid3, B站可能无法关联扫码状态)
2. /api/login/qr 在有效期内**复用同一 key**, 避免多端并发生成互相覆盖
"""

import io
import os
import time
import json
import logging
from http.cookies import SimpleCookie

import httpx
import qrcode
from fastapi import APIRouter, HTTPException, Response

from ..config import settings
from ..ws_manager import ws_manager

router = APIRouter(prefix="/api/login")
log = logging.getLogger("owk.login")

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

QR_STATE: dict = {}
QR_TTL = 150  # 秒: 有效期内 /api/login/qr 复用同一二维码(B站 key 有效期约180s)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """持久客户端: 自动保留/携带 buvid3 等 cookie"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers=BILI_HEADERS, timeout=15, follow_redirects=True
        )
    return _client


async def _ensure_buvid3(client: httpx.AsyncClient):
    """B站扫码需要 buvid3 cookie(指纹接口获取, 标准做法)"""
    if client.cookies.get("buvid3"):
        return
    try:
        r = await client.get("https://api.bilibili.com/x/frontend/finger/spi")
        d = r.json().get("data", {})
        if d.get("b_3"):
            client.cookies.set("buvid3", d["b_3"])
        if d.get("b_4"):
            client.cookies.set("b_4", d["b_4"])
    except Exception:
        pass


async def _new_qr():
    """强制生成新二维码 key"""
    c = _get_client()
    await _ensure_buvid3(c)
    resp = await c.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    )
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(502, "B站二维码接口响应异常")
    if data.get("code") != 0:
        raise HTTPException(502, f"B站API错误: {data.get('message', 'unknown')}")
    QR_STATE["qrcode_key"] = data["data"]["qrcode_key"]
    QR_STATE["url"] = data["data"]["url"]
    QR_STATE["generated_at"] = time.time()
    log.info(f"生成新登录二维码: key={QR_STATE['qrcode_key']}")


def load_cookies() -> dict:
    try:
        with open(settings.BILIBILI_COOKIE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cookies(cookies: dict) -> None:
    os.makedirs(os.path.dirname(settings.BILIBILI_COOKIE) or ".", exist_ok=True)
    with open(settings.BILIBILI_COOKIE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2, ensure_ascii=False)
    log.info(f"B站登录Cookie已保存: {list(cookies.keys())}")


def extract_cookies(response: httpx.Response) -> dict:
    cookies = {}
    for name, value in response.headers.raw:
        if name.lower() == b"set-cookie":
            raw = value.decode("utf-8") if isinstance(value, bytes) else value
            c = SimpleCookie(raw)
            for key, morsel in c.items():
                cookies[key] = morsel.value
    return cookies


@router.get("/status")
async def login_status():
    """是否已登录B站"""
    cookies = load_cookies()
    logged_in = bool(cookies.get("SESSDATA"))
    return {"logged_in": logged_in, "user_id": cookies.get("DedeUserID", "")}


@router.get("/qr")
async def login_qr():
    """生成/复用B站登录二维码, 返回 PNG"""
    if not QR_STATE.get("qrcode_key") or time.time() - QR_STATE.get("generated_at", 0) > QR_TTL:
        await _new_qr()
    img = qrcode.make(QR_STATE["url"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/qr/refresh")
async def login_qr_refresh():
    """控制端刷新二维码: 强制生成新 key, 并广播让播放端同步刷新"""
    await _new_qr()
    try:
        await ws_manager.send_to_players({"type": "login_refresh"})
    except Exception:
        pass
    return {"status": "ok"}


@router.post("/logout")
async def login_logout():
    """退出登录: 删除 cookie 文件并广播(播放端回到扫码状态)"""
    QR_STATE.clear()
    try:
        if os.path.exists(settings.BILIBILI_COOKIE):
            os.remove(settings.BILIBILI_COOKIE)
    except OSError as e:
        raise HTTPException(500, f"删除Cookie失败: {e}")
    log.info("已退出B站登录")
    try:
        await ws_manager.send_to_players({"type": "login_refresh"})
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/poll")
async def login_poll():
    """轮询二维码状态; 扫码确认成功后自动保存 cookie"""
    key = QR_STATE.get("qrcode_key")
    if not key:
        return {"status": "expired", "message": "请先获取二维码"}
    c = _get_client()
    resp = await c.get(
        f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}"
    )
    try:
        body = resp.json()
    except Exception:
        return {"status": "error", "message": "B站响应异常"}
    outer = body.get("code")
    db = body.get("data", {})
    code = db.get("code") if isinstance(db, dict) else outer
    message = (db.get("message", "") if isinstance(db, dict) else body.get("message", ""))

    if code == 0 and outer == 0:
        cookies = extract_cookies(resp)
        if cookies:
            save_cookies(cookies)
            log.info("B站登录成功")
            return {"status": "success", "message": "登录成功",
                    "user_id": cookies.get("DedeUserID", "")}
        return {"status": "error", "message": "未获取到Cookie"}
    if code == 86101:
        return {"status": "pending", "message": "等待扫码"}
    if code == 86090:
        return {"status": "scanned", "message": "已扫码，请在手机上确认"}
    if code == 86038:
        log.warning("二维码已过期, 等待前端刷新")
        return {"status": "expired", "message": "二维码已过期"}
    log.warning(f"扫码轮询未知状态: code={code} message={message}")
    return {"status": "error", "message": f"未知状态: {message}"}
