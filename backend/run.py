#!/usr/bin/env python3
"""OwK 启动入口"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from app.config import settings

if __name__ == "__main__":
    import socket
    import sys

    host = settings.HOST
    port = settings.PORT

    ports = [port, 8080] if port != 8080 else [8080]
    chose_port = 0
    for p in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, p))
                chose_port = p
                break
        except (PermissionError, OSError):
            continue
    if not chose_port:
        print("错误: 所有端口均无法绑定，请检查端口是否被占用")
        sys.exit(1)

    print()
    lan = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.254.254.254", 1))
        lan = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print("=" * 52)
    print("  OPENwebKTV (OwK)")
    print(f"  内网: http://{lan}:{chose_port}")
    print(f"  本机: http://127.0.0.1:{chose_port}")
    print(f"  播放器: http://{lan}:{chose_port}/player.html")
    print(f"  控制器: http://{lan}:{chose_port}/controller.html")
    print("=" * 52)
    print()

    uvicorn.run("app.main:app", host=host, port=chose_port, log_level="info")
