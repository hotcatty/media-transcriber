#!/usr/bin/env python3
"""Open the local UI in a native window. The backend must already be running."""
import os
import sys
import time
import urllib.error
import urllib.request


def _port() -> int:
    return int(os.getenv("MT_PORT") or os.getenv("PORT") or "8766")


def _url() -> str:
    host = os.getenv("MT_HOST") or os.getenv("HOST") or "127.0.0.1"
    return os.getenv("MT_DESKTOP_URL") or f"http://{host}:{_port()}"


def wait_until_up(base: str, seconds: float = 90) -> None:
    ping = base.rstrip("/") + "/api/ping"
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(ping, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(0.25)
    raise SystemExit(f"本机服务没有起来：{last or ping}")


def main() -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    base = _url()
    wait_until_up(base)
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先安装依赖。", file=sys.stderr)
        return 1
    webview.create_window(
        "转录小工具",
        base,
        width=1200,
        height=800,
        min_size=(720, 520),
        background_color="#000000",
    )
    webview.start(private_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
