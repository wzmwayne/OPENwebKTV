#!/usr/bin/env python3
"""OwK 启动入口（项目根目录）"""

import os
import sys
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(BASE, "backend")

if __name__ == "__main__":
    os.chdir(BACKEND)
    try:
        sys.exit(subprocess.call([sys.executable, "run.py"]))
    except KeyboardInterrupt:
        sys.exit(0)
