#!/usr/bin/env python3
"""OwK 启动入口"""

import os
import sys
import re
import subprocess
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


# ── 端口占用诊断 ────────────────────────────────────


def _pids_on_port(port: int) -> list[int]:
    """找出监听该端口的进程 PID(Linux)。

    ss -p → lsof → /proc/net/tcp(.6) inode 扫描 多路兜底。
    返回空列表表示未获取到(权限不足或端口已释放)。
    """
    pids: set[int] = set()
    port_hex = f":{port:04X}"

    # 1) ss -p
    try:
        r = subprocess.run(
            ["ss", "-tlnpH", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
        for m in re.finditer(r"pid=(\d+)", r.stdout):
            pids.add(int(m.group(1)))
    except Exception:
        pass

    # 2) lsof
    try:
        r = subprocess.run(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.split():
            if line.isdigit():
                pids.add(int(line))
    except Exception:
        pass

    # 3) /proc/net/tcp(.6) socket inode → /proc/<pid>/fd 匹配
    try:
        inodes: set[str] = set()
        for fname in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(fname) as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 10 and parts[3] == "0A" and parts[1].endswith(port_hex):
                            inodes.add(parts[9])
            except OSError:
                continue
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            fd_dir = f"/proc/{entry}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(f"{fd_dir}/{fd}")
                    except OSError:
                        continue
                    if target.startswith("socket:[") and target[8:-1] in inodes:
                        pids.add(int(entry))
            except OSError:
                continue
    except Exception:
        pass

    return sorted(pids)


def _proc_field(pid: int, name: str) -> str:
    try:
        with open(f"/proc/{pid}/{name}") as f:
            return f.read().strip()
    except Exception:
        return "?"


def list_port_occupiers(port: int) -> list[dict]:
    """列出占用端口的程序: [{pid, comm, cmdline}]"""
    result = []
    for pid in _pids_on_port(port):
        comm = _proc_field(pid, "comm")
        cmdline = ""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except Exception:
            pass
        result.append({"pid": pid, "comm": comm or "?", "cmdline": cmdline or f"<pid {pid}>"})
    return result

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
        for p in ports:
            occ = list_port_occupiers(p)
            if occ:
                print(f"  端口 {p} 被以下程序占用:")
                for o in occ:
                    print(f"    PID {o['pid']:<8} {o['comm']}  [{o['cmdline']}]")
            else:
                print(f"  端口 {p}: 未获取到占用程序信息(权限不足, 可用 lsof -i :{p} 查看)")
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
