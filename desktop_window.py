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


def classify_ping(body) -> str:
    """ready, preparing, or down. preparing is a visible window, not a failure."""
    if not isinstance(body, dict) or not body.get("ok"):
        return "down"
    if body.get("app") not in (None, "media-transcriber"):
        return "down"
    if body.get("preparing") is True:
        return "preparing"
    return "ready"


def _read_ping(url: str):
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def wait_until_up(base: str = "", seconds: float = 90) -> str:
    """Wait until a window can be shown.

    `preparing` is enough: the waiting page stays up and switches itself when
    the real service is ready. This never exits the process. A slow download
    must not look like the app failed to open.
    """
    del base
    ping = _origin() + "/api/ping"
    deadline = time.time() + seconds
    while time.time() < deadline:
        kind = classify_ping(_read_ping(ping))
        if kind == "ready":
            return "ready"
        if kind == "preparing":
            return "preparing"
        time.sleep(0.25)
    return "down"


def _holding_html(url: str) -> str:
    origin = _origin()
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>猫听转文字</title>
<style>
  html, body {{ height: 100%; margin: 0; background: #000; color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
  body {{ display: flex; align-items: center; justify-content: center; }}
  .card {{ background: #fff; color: #000; border-radius: 20px; padding: 32px 40px;
    width: min(520px, calc(100% - 48px)); }}
  h1 {{ font-size: 18px; margin: 0 0 12px; }}
  p {{ font-size: 16px; line-height: 1.5; color: rgba(0,0,0,.6); margin: 0; }}
</style>
</head>
<body>
  <div class="card">
    <h1>正在打开</h1>
    <p>正在准备本机服务，好了会自动进入。</p>
  </div>
  <script>
    async function hop() {{
      try {{
        const r = await fetch("{origin}/api/ping", {{ cache: "no-store" }});
        const j = await r.json();
        if (j && j.ok && j.preparing !== true) location.replace("{url}");
      }} catch (e) {{}}
    }}
    hop();
    setInterval(hop, 2000);
  </script>
</body>
</html>"""


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
    kind = wait_until_up(base)
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
    params = inspect.signature(webview.create_window).parameters
    # Older pywebview (common on first Mac install) has no `icon=` argument.
    if icon and "icon" in params:
        window_opts["icon"] = icon
    if kind == "down" and "html" in params:
        webview.create_window("猫听转文字", html=_holding_html(base), **window_opts)
    else:
        webview.create_window("猫听转文字", base, **window_opts)
    webview.start(private_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
