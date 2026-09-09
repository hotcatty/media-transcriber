"""Import site login state from a local browser via yt-dlp.

Writes Netscape cookies to the project cookies.txt. Never returns cookie
values to callers — only ok/error strings.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import config

BROWSERS = {
    "chrome": "Chrome",
    "safari": "Safari",
    "edge": "Edge",
    "firefox": "Firefox",
}

SITES = {
    "bilibili": {
        "id": "bilibili",
        "label": "B 站",
        "host": "bilibili.com",
        "url": "https://www.bilibili.com",
        "probe": "https://www.bilibili.com/video/BV1GJ411x7h7",
        "domains": ("bilibili.com",),
        "cookie_names": frozenset(
            {"SESSDATA", "DedeUserID", "DedeUserID__ckMd5", "bili_jct"}
        ),
    },
    "youtube": {
        "id": "youtube",
        "label": "YouTube",
        "host": "youtube.com",
        "url": "https://www.youtube.com",
        "probe": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "domains": ("youtube.com", "google.com"),
        "cookie_names": frozenset(
            {
                "SID",
                "SSID",
                "HSID",
                "SAPISID",
                "APISID",
                "LOGIN_INFO",
                "__Secure-1PSID",
                "__Secure-3PSID",
            }
        ),
    },
}

_APP_PATHS = {
    "chrome": (
        "/Applications/Google Chrome.app",
        os.path.expanduser("~/Applications/Google Chrome.app"),
    ),
    "safari": ("/System/Applications/Safari.app", "/Applications/Safari.app"),
    "edge": (
        "/Applications/Microsoft Edge.app",
        os.path.expanduser("~/Applications/Microsoft Edge.app"),
    ),
    "firefox": (
        "/Applications/Firefox.app",
        os.path.expanduser("~/Applications/Firefox.app"),
    ),
}

_PROCESS_NAMES = {
    "chrome": ("Google Chrome",),
    "safari": ("Safari",),
    "edge": ("Microsoft Edge",),
    "firefox": ("firefox", "Firefox"),
}


class CookieImportError(Exception):
    """User-facing import failure. str(e) is safe to show in the UI."""


def site_from_url(url: str) -> str:
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "bilibili"


def site_info(site: str) -> dict:
    return SITES.get((site or "").strip().lower()) or SITES["bilibili"]


def list_installed_browsers() -> list[dict]:
    out = []
    for key, label in BROWSERS.items():
        if sys.platform != "darwin" or _app_installed(key):
            out.append({"id": key, "label": label})
    return out


def import_from_browser(browser: str, site: str = "bilibili") -> dict:
    key = (browser or "").strip().lower()
    if key not in BROWSERS:
        raise CookieImportError("请选择 Chrome、Safari、Edge 或 Firefox")
    label = BROWSERS[key]
    meta = site_info(site)
    site_label = meta["label"]

    if sys.platform == "darwin" and not _app_installed(key):
        raise CookieImportError(f"这台电脑上好像没装 {label}")

    tmp = config.COOKIE_FILE.with_name(f".cookies-import-{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--cookies-from-browser",
        key,
        "--cookies",
        str(tmp),
        "--skip-download",
        "--simulate",
        "--no-progress",
        "--no-warnings",
        "--ignore-no-formats-error",
        "--",
        meta["probe"],
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(config.PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "PYTHONWARNINGS": "ignore"},
        )
    except subprocess.TimeoutExpired:
        _unlink_quiet(tmp)
        raise CookieImportError(
            "等太久了。如果弹出了钥匙串权限，选允许后再点一次"
        ) from None
    except FileNotFoundError:
        _unlink_quiet(tmp)
        raise CookieImportError("本机缺少导入工具") from None
    except Exception:
        _unlink_quiet(tmp)
        raise CookieImportError("读取失败，请稍后再试") from None

    combined = _safe_text((result.stderr or "") + "\n" + (result.stdout or ""))
    has_login = _cookie_file_has_login(tmp, meta)

    if has_login:
        _commit_cookie_file(tmp)
        return {
            "ok": True,
            "browser": key,
            "site": meta["id"],
            "message": f"已从 {label} 读取 {site_label} 登录状态",
        }

    _unlink_quiet(tmp)

    if _looks_like_extracted_ok(combined, result.returncode):
        raise CookieImportError(
            f"看起来还没在 {label} 登录{site_label}。"
            f"请先打开 {meta['host']} 并登录，再回来点读取"
        )

    raise CookieImportError(_friendly_error(combined, key, label, meta))


def _commit_cookie_file(tmp: Path) -> None:
    config.COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, config.COOKIE_FILE)
    try:
        os.chmod(config.COOKIE_FILE, 0o600)
    except OSError:
        pass


def _cookie_file_has_login(path: Path, meta: dict) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        domain, name = parts[0], parts[5]
        if any(d in domain.lower() for d in meta["domains"]) and name in meta["cookie_names"]:
            return True
    return False


def _looks_like_extracted_ok(text: str, returncode: int) -> bool:
    low = text.lower()
    if "extracted" in low and "cookie" in low:
        return True
    if returncode == 0:
        return True
    return False


def _friendly_error(text: str, key: str, label: str, meta: dict) -> str:
    low = text.lower()
    running = _browser_is_running(key)
    site_label = meta["label"]

    if _is_lock_error(low) or (running and _is_copy_error(low)):
        return f"请先完全退出 {label} 再点一次"

    if _is_decrypt_or_keychain(low):
        return "需要允许访问钥匙串。如果弹出权限，选允许后再点一次"

    if _is_missing_browser(low):
        if not _app_installed(key):
            return f"这台电脑上好像没装 {label}"
        return (
            f"读不到 {label} 的登录数据。"
            f"请先用它打开过 {meta['host']}，或换一个已登录的浏览器"
        )

    if "operation not permitted" in low or "errno 1" in low:
        if key == "safari":
            return "读不了 Safari 的登录状态。请在系统设置里允许访问后再试"
        return f"读不了 {label} 的登录状态。请先完全退出 {label} 再点一次"

    if running and key in ("chrome", "edge", "firefox"):
        return f"读取失败。请先完全退出 {label} 再点一次"

    return f"没能从 {label} 读取。请确认已登录{site_label}，或换一个浏览器试试"


def _is_lock_error(low: str) -> bool:
    return any(
        p in low
        for p in (
            "database is locked",
            "database locked",
            "cookie database is locked",
            "could not copy",
            "failed to copy",
            "unable to copy",
        )
    )


def _is_copy_error(low: str) -> bool:
    return "copy" in low and ("cookie" in low or "database" in low or "permission" in low)


def _is_decrypt_or_keychain(low: str) -> bool:
    return any(
        p in low
        for p in (
            "decrypt",
            "keychain",
            "find-generic-password",
            "safe storage",
            "dpapi",
            "no key found",
            "key is wrong",
            "钥匙串",
        )
    )


def _is_missing_browser(low: str) -> bool:
    return any(
        p in low
        for p in (
            "could not find",
            "cookies database",
            "no such file",
            "unsupported browser",
        )
    )


def _app_installed(key: str) -> bool:
    if sys.platform != "darwin":
        return True
    paths = _APP_PATHS.get(key) or ()
    if not paths:
        return True
    return any(Path(p).exists() for p in paths)


def _browser_is_running(key: str) -> bool:
    for name in _PROCESS_NAMES.get(key, ()):
        try:
            r = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True,
                timeout=2,
            )
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _safe_text(text: str) -> str:
    """Drop cookie-like tokens so logs/errors never leak session values."""
    cleaned = re.sub(r"(?i)\x1b\[[0-9;]*[A-Za-z]", "", text or "")
    cleaned = re.sub(r"[A-Za-z0-9_-]{24,}", "[redacted]", cleaned)
    return cleaned[:4000]


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
