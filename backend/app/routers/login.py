"""
B站登录(集成在主服务内):
- 播放端未登录时显示二维码(/api/login/qr → PNG)
- 播放端轮询 /api/login/poll, 扫码确认后自动保存 cookie
- /api/login/status 供前端判断是否已登录

逻辑与 backend/login_server.py(独立8888端口工具)一致。
登录成功后 cookie 写入 settings.BILIBILI_COOKIE(bilibili_cookie.json),
主服务的 BilibiliClient 每次请求都会重新读取该文件, 无需重启进程。
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

QR_STATE: dict = {}  # qrcode_key / url / generated_at


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
    """生成B站登录二维码, 返回 PNG"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            headers=BILI_HEADERS, timeout=15,
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
    img = qrcode.make(QR_STATE["url"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/poll")
async def login_poll():
    """轮询二维码状态; 扫码确认成功后自动保存 cookie"""
    key = QR_STATE.get("qrcode_key")
    if not key:
        return {"status": "expired", "message": "请先获取二维码"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}",
            headers=BILI_HEADERS, timeout=15,
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
        QR_STATE.clear()
        return {"status": "expired", "message": "二维码已过期，请刷新"}
    return {"status": "error", "message": f"未知状态: {message}"}
