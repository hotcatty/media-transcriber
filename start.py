#!/usr/bin/env python3
"""
媒体转录器启动脚本
用法：
  python start.py          # 开发模式（自动重载）
  python start.py --prod   # 生产模式
"""
import os
import subprocess
import sys


def main():
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    prod = "--prod" in sys.argv
    port = int(os.getenv("MT_PORT") or os.getenv("PORT") or "8766")
    host = os.getenv("MT_HOST") or os.getenv("HOST") or "127.0.0.1"

    cmd = [
        sys.executable, "-m", "uvicorn",
        "main:app",
        "--host", host,
        "--port", str(port),
    ]
    if prod:
        cmd += ["--workers", "1"]
        print(f"[媒体转录器] 生产模式，访问 http://{host}:{port}")
    else:
        cmd += ["--reload"]
        print(f"[媒体转录器] 开发模式，访问 http://{host}:{port}")

    backend_dir = os.path.join(os.path.dirname(__file__), "backend")
    os.chdir(backend_dir)
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
