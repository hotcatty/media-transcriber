#!/usr/bin/env python3
"""Open the local UI in a native window. The backend must already be running."""
import inspect
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _port() -> int:
    return int(os.getenv("MT_PORT") or os.getenv("PORT") or "8766")


def _origin() -> str:
    host = os.getenv("MT_HOST") or os.getenv("HOST") or "127.0.0.1"
    return (os.getenv("MT_DESKTOP_URL") or f"http://{host}:{_port()}").rstrip("/")


def _url() -> str:
    url = _origin()
    # Skip the WebGL sky in pywebview — on macOS it composites over the card
    # and the window looks empty/black.
    if "desktop=" not in url:
        url += ("&" if "?" in url else "/?") + "desktop=1"
    return url


def wait_until_up(base: str, seconds: float = 90) -> None:
    ping = _origin() + "/api/ping"
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(ping, timeout=1.5) as resp:
                if resp.status != 200:
                    last = f"HTTP {resp.status}"
                    continue
                raw = resp.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError as err:
                    last = err
                    continue
                # Bootstrap waiting page also answers 200 with preparing=true.
                if body.get("ok") and body.get("preparing") is not True:
                    return
                last = "still preparing"
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = err
        time.sleep(0.25)
    raise SystemExit(f"本机服务没有起来：{last or ping}")


def _icon_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cands = (
        os.path.join(here, "app-icon.png"),
        os.path.join(here, "packaging", "icons", "app-icon.png"),
        os.path.join(here, "..", "Resources", "AppIcon.icns"),
        os.path.join(here, "..", "Resources", "app", "app-icon.png"),
    )
    for path in cands:
        if os.path.isfile(path):
            return path
    return ""


def main() -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    base = _url()
    wait_until_up(base)
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先安装依赖。", file=sys.stderr)
        return 1
    window_opts = dict(
        width=1200,
        height=800,
        min_size=(720, 520),
        background_color="#000000",
    )
    icon = _icon_path()
    # Older pywebview (common on first Mac install) has no `icon=` argument.
    if icon and "icon" in inspect.signature(webview.create_window).parameters:
        window_opts["icon"] = icon
    webview.create_window("猫听转文字", base, **window_opts)
    webview.start(private_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
