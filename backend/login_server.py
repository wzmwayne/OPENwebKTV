#!/usr/bin/env python3
"""
OwK - Bilibili QR Code Login Server
独立运行的B站二维码登录服务器，用于获取B站登录凭证。
"""

import json
import os
import logging
import sys
import uvicorn
from http.cookies import SimpleCookie
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import httpx

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("owk-login")

app = FastAPI(title="OwK - B站登录", description="Bilibili QR Code Login Server")

COOKIE_FILE = "bilibili_cookie.json"
QR_STATE: dict = {}

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def load_cookies() -> dict:
    try:
        with open(COOKIE_FILE) as f:
            data = json.load(f)
            log.info(f"已加载Cookie文件, 包含keys: {list(data.keys())}")
            return data
    except FileNotFoundError:
        log.info("Cookie文件不存在")
        return {}
    except json.JSONDecodeError as e:
        log.warning(f"Cookie文件损坏: {e}")
        return {}


def save_cookies(cookies: dict) -> None:
    os.makedirs(os.path.dirname(COOKIE_FILE) or ".", exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f, indent=2, ensure_ascii=False)
    log.info(f"Cookie已保存到 {COOKIE_FILE}, keys: {list(cookies.keys())}")


def extract_cookies(response: httpx.Response) -> dict:
    cookies = {}
    log.debug(f"提取Cookie, 原始Set-Cookie headers数量: {len([h for h in response.headers.raw if h[0].lower() == b'set-cookie'])}")
    for name, value in response.headers.raw:
        if name.lower() == b"set-cookie":
            raw = value.decode("utf-8") if isinstance(value, bytes) else value
            C = SimpleCookie(raw)
            for key, morsel in C.items():
                log.debug(f"  提取到Cookie: {key} = {morsel.value[:20]}...")
                cookies[key] = morsel.value
    if not cookies:
        log.warning("从响应中未提取到任何Cookie!")
        log.debug(f"所有响应头: {dict(response.headers)}")
    return cookies


@app.get("/api/qrcode/generate")
async def generate_qrcode():
    log.info("=" * 50)
    log.info("请求: 生成B站二维码")
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    log.info(f"请求B站API: GET {url}")

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=BILI_HEADERS)
        log.info(f"B站响应状态码: {resp.status_code}")
        log.info(f"B站响应头: {dict(resp.headers)}")

        data = resp.json()
        log.info(f"B站响应体: {json.dumps(data, ensure_ascii=False)}")

        if data["code"] != 0:
            log.error(f"B站API返回错误: code={data['code']}, message={data.get('message', '')}")
            raise HTTPException(502, f"B站API错误: {data.get('message', 'unknown')}")

        QR_STATE["qrcode_key"] = data["data"]["qrcode_key"]
        QR_STATE["url"] = data["data"]["url"]
        QR_STATE["generated_at"] = __import__("time").time()

        log.info(f"二维码生成成功: key={data['data']['qrcode_key']}")
        log.info(f"二维码URL: {data['data']['url']}")

        return data["data"]


@app.get("/api/qrcode/poll")
async def poll_qrcode():
    key = QR_STATE.get("qrcode_key")
    if not key:
        log.warning("轮询失败: 未生成二维码")
        return {"status": "expired", "message": "请先生成二维码"}

    url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}"
    log.debug(f"轮询B站: GET {url}")

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=BILI_HEADERS)
        log.debug(f"B站轮询响应状态码: {resp.status_code}")

        try:
            body = resp.json()
            log.debug(f"B站轮询响应体: {json.dumps(body, ensure_ascii=False)}")
        except Exception as e:
            log.error(f"解析B站响应JSON失败: {e}")
            log.error(f"原始响应内容: {resp.text[:500]}")
            return {"status": "error", "message": f"解析B站响应失败: {e}"}

        # B站新API: 外层code是接口状态(0=成功), 内层data.code是二维码状态
        outer_code = body.get("code")
        data_block = body.get("data", {})
        qr_code = data_block.get("code") if isinstance(data_block, dict) else None
        qr_message = data_block.get("message", "") if isinstance(data_block, dict) else body.get("message", "")
        log.debug(f"B站外层code={outer_code}, data.code={qr_code}, message={qr_message}")

        # 使用二维码状态码(data.code)，如果没有则回退到外层code
        code = qr_code if qr_code is not None else outer_code
        message = qr_message

        if code == 0 and outer_code == 0:
            log.info("=" * 40)
            log.info("B站返回登录成功! (code=0)")
            cookies = extract_cookies(resp)
            log.info(f"提取到 {len(cookies)} 个Cookie")

            if not cookies:
                log.warning("B站返回code=0但未提取到Cookie!")
                log.warning("尝试从响应头中手动解析...")
                log.debug(f"所有响应头: {[(k.decode() if isinstance(k,bytes) else k, v.decode() if isinstance(v,bytes) else v) for k,v in resp.headers.raw]}")

            if cookies:
                save_cookies(cookies)
                QR_STATE["cookies"] = cookies
                QR_STATE["logged_in"] = True
                log.info(f"登录成功! Cookie包含: {list(cookies.keys())}")
            else:
                log.warning("未提取到Cookie, 登录可能不完整")

            return {
                "status": "success",
                "message": "登录成功",
                "cookies": list(cookies.keys()),
            }

        elif code == 86101:
            log.info("→ 未扫码 (code=86101)")
            return {"status": "pending", "message": "等待扫码"}

        elif code == 86090:
            log.info("→ 已扫码待确认 (code=86090)")
            return {"status": "scanned", "message": "已扫码，请在手机上确认"}

        elif code == 86038:
            log.warning("→ 二维码已过期 (code=86038)")
            QR_STATE.clear()
            return {"status": "expired", "message": "二维码已过期，请重新生成"}

        else:
            log.warning(f"→ 未知状态码: code={code}, message={message}")
            return {"status": "error", "message": f"B站返回未知状态: {message}"}


@app.get("/api/qrcode/debug")
async def debug_state():
    """调试端点: 查看当前状态"""
    return {
        "has_qrcode_key": "qrcode_key" in QR_STATE,
        "qrcode_key": QR_STATE.get("qrcode_key", ""),
        "has_cookies_in_state": "cookies" in QR_STATE,
        "logged_in_flag": QR_STATE.get("logged_in", False),
        "cookie_file_exists": os.path.exists(COOKIE_FILE),
        "cookie_file_keys": list(load_cookies().keys()),
        "qr_state_keys": list(QR_STATE.keys()),
    }


@app.get("/api/cookie/status")
async def cookie_status():
    cookies = load_cookies()
    logged_in = bool(cookies.get("SESSDATA"))
    log.info(f"查询登录状态: logged_in={logged_in}, user_id={cookies.get('DedeUserID', '')}")
    return {
        "logged_in": logged_in,
        "user_id": cookies.get("DedeUserID", ""),
        "cookies_keys": list(cookies.keys()),
        "cookies": cookies if logged_in else {},
    }


@app.get("/api/cookie/clear")
async def clear_cookies():
    log.info("清除所有Cookie和状态")
    QR_STATE.clear()
    if os.path.exists(COOKIE_FILE):
        os.remove(COOKIE_FILE)
        log.info(f"已删除Cookie文件: {COOKIE_FILE}")
    return {"status": "cleared"}


@app.get("/api/cookie/raw")
async def raw_cookies():
    return load_cookies()


@app.get("/", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_HTML)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OwK - B站登录</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0a0a0f;color:#e0e0e0;min-height:100vh;
  display:flex;align-items:center;justify-content:center
}
.card{
  background:#14141f;border:1px solid #2a2a3a;border-radius:16px;
  padding:40px;width:440px;max-width:92vw;text-align:center
}
h1{font-size:24px;margin-bottom:6px;color:#fff}
.sub{font-size:14px;color:#888;margin-bottom:28px}
.qr-wrap{display:flex;justify-content:center;margin-bottom:22px;min-height:264px;align-items:center}
#qrcode{background:#fff;padding:12px;border-radius:12px;display:inline-block}
#qrcode img,#qrcode canvas{display:block}
.st{font-size:15px;padding:12px 16px;border-radius:8px;margin-bottom:18px}
.st.pending{background:#132313;color:#8f8}
.st.scanned{background:#232313;color:#ff8}
.st.success{background:#132323;color:#8ff}
.st.expired,.st.error{background:#231313;color:#f88}
.st.hidden{display:none}
.btn{
  display:inline-block;padding:12px 32px;border-radius:8px;border:none;
  font-size:15px;cursor:pointer;background:#7c5cfc;color:#fff;
  transition:background .2s;margin:4px
}
.btn:hover{background:#6a4aee}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.sec{background:#2a2a3a;color:#ccc}
.btn.sec:hover{background:#3a3a4a}
.box{
  background:#1a1a2a;border:1px solid #2a2a3a;border-radius:8px;
  padding:14px;margin-bottom:16px;text-align:left;font-size:13px;word-break:break-all
}
.box label{color:#888;font-size:12px;display:block;margin-bottom:2px}
.box .val{color:#7c5cfc;margin-top:2px}
.hidden{display:none}
.tip{font-size:12px;color:#555;margin-top:8px}
.dbg{font-size:11px;color:#555;margin-top:12px;cursor:pointer}
.dbg:hover{color:#888}
#dbgPanel{font-size:11px;text-align:left;background:#0e0e16;border:1px solid #2a2a3a;border-radius:8px;padding:12px;margin-top:12px;max-height:200px;overflow:auto;white-space:pre-wrap;word-break:break-all}
</style>
</head>
<body>
<div class="card">
  <h1>OwK B站登录</h1>
  <p class="sub">扫描二维码以登录Bilibili账号</p>

  <div id="loggedIn" class="hidden">
    <div class="st success">已登录</div>
    <div class="box"><label>用户ID</label><div class="val" id="uid"></div></div>
    <div class="box"><label>Cookies Keys</label><div class="val" id="ckKeys"></div></div>
    <div class="box"><label>Cookies (完整)</label><div class="val" id="ckInfo" style="font-size:11px"></div></div>
    <button class="btn sec" onclick="location.reload()">刷新</button>
    <button class="btn sec" onclick="clearCookie()">清除登录</button>
  </div>

  <div id="loginBox">
    <div class="qr-wrap" id="qrcode"></div>
    <div id="st" class="st hidden"></div>
    <button id="genBtn" class="btn" onclick="generate()">生成二维码</button>
    <button id="regBtn" class="btn sec hidden" onclick="generate()">重新生成</button>
    <p id="tip" class="tip hidden">请使用B站手机App扫码</p>
  </div>

  <div class="dbg" onclick="toggleDbg()">[ 调试信息 ]</div>
  <div id="dbgPanel" class="hidden"></div>
</div>

<script>
var pollTimer = null;
var pollCount = 0;

async function check(){
  var r=await fetch('/api/cookie/status'), d=await r.json();
  dbg('check() -> ' + JSON.stringify(d));
  if(d.logged_in){
    document.getElementById('loggedIn').classList.remove('hidden');
    document.getElementById('loginBox').classList.add('hidden');
    document.getElementById('uid').textContent = d.user_id || 'Unknown';
    document.getElementById('ckKeys').textContent = (d.cookies_keys || []).join(', ');
    var txt = JSON.stringify(d.cookies, null, 2);
    if(txt.length > 200) txt = txt.slice(0,200) + '...';
    document.getElementById('ckInfo').textContent = txt;
  }
}

async function generate(){
  var gen=document.getElementById('genBtn'), reg=document.getElementById('regBtn');
  var st=document.getElementById('st'), tip=document.getElementById('tip');
  var qr=document.getElementById('qrcode');
  gen.disabled=true; qr.innerHTML='<span style="color:#555">生成中...</span>';
  st.classList.add('hidden'); reg.classList.add('hidden');
  pollCount = 0;

  dbg('正在请求 /api/qrcode/generate ...');
  var r=await fetch('/api/qrcode/generate');
  dbg('generate响应 status=' + r.status);
  if(!r.ok){
    var errTxt = await r.text();
    st.textContent='生成失败: ' + errTxt;
    st.className='st error'; st.classList.remove('hidden');
    gen.disabled=false;
    dbg('生成失败: ' + errTxt);
    return;
  }
  var d=await r.json();
  dbg('generate响应数据: ' + JSON.stringify(d));

  qr.innerHTML='';
  new QRCode(qr,{text:d.url,width:256,height:256,correctLevel:QRCode.CorrectLevel.H});

  tip.classList.remove('hidden'); gen.classList.add('hidden'); reg.classList.remove('hidden');
  st.className='st pending'; st.classList.remove('hidden'); st.textContent='等待扫码...';

  if(pollTimer) clearInterval(pollTimer);
  pollTimer=setInterval(poll,2000);
}

async function poll(){
  pollCount++;
  var st=document.getElementById('st');
  var r=await fetch('/api/qrcode/poll'), d=await r.json();
  dbg('poll #' + pollCount + ': ' + JSON.stringify(d));
  st.className='st '+d.status;
  if(d.status=='success'){
    st.textContent='登录成功！';
    clearInterval(pollTimer); pollTimer=null;
    dbg('登录成功! 即将刷新页面...');
    setTimeout(check,600);
  }else if(d.status=='scanned'){
    st.textContent=d.message;
  }else if(d.status=='expired'){
    st.textContent=d.message;
    clearInterval(pollTimer); pollTimer=null;
    document.getElementById('genBtn').disabled=false;
  }else if(d.status=='pending'){
    st.textContent=d.message;
  }else{
    st.textContent='错误: ' + d.message;
  }
}

async function clearCookie(){
  await fetch('/api/cookie/clear');
  location.reload();
}

function dbg(msg){
  var p = document.getElementById('dbgPanel');
  p.textContent += '[' + new Date().toLocaleTimeString() + '] ' + msg + '\\n';
}

function toggleDbg(){
  var p = document.getElementById('dbgPanel');
  p.classList.toggle('hidden');
}

check();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8888))
    print()
    print("=" * 52)
    print("  OwK - B站二维码登录服务器")
    print(f"  监听端口: {port}")
    print("  请用浏览器打开: http://localhost:" + str(port))
    print("=" * 52)
    print()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
