"""Import site login state from a local browser via yt-dlp.

Writes Netscape cookies to the project cookies.txt. Never returns cookie
values to callers — only ok/error strings.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
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
    "xiaohongshu": {
        "id": "xiaohongshu",
        "label": "小红书",
        "host": "xiaohongshu.com",
        "url": "https://www.xiaohongshu.com",
        "probe": "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
        "domains": ("xiaohongshu.com",),
        "cookie_names": frozenset({"web_session"}),
    },
    "douyin": {
        "id": "douyin",
        "label": "抖音",
        "host": "douyin.com",
        "url": "https://www.douyin.com",
        "probe": "https://www.douyin.com",
        "domains": ("douyin.com", "iesdouyin.com"),
        "cookie_names": frozenset(
            {"sessionid", "sessionid_ss", "sid_tt", "s_v_web_id", "ttwid"}
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
    if "xiaohongshu.com" in u or "xhslink.com" in u or "xhslink.cn" in u:
        return "xiaohongshu"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
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


_PRIMARY_COOKIE = {
    "bilibili": "SESSDATA",
    "youtube": "LOGIN_INFO",
    "xiaohongshu": "web_session",
    "douyin": "sessionid",
}


def build_netscape_text(text: str, site: str = "bilibili") -> str:
    """Validate pasted SESSDATA / cookies.txt and return Netscape text.

    Never writes cookies.txt. Callers decide whether to persist on disk
    (local helper) or keep the blob in memory (public trial).
    """
    raw = (text or "").replace("\r\n", "\n").strip()
    if len(raw) > 256_000:
        raise CookieImportError("内容太大")
    meta = site_info(site)
    primary = _PRIMARY_COOKIE.get(meta["id"], "SESSDATA")
    if not _looks_like_netscape(raw):
        raw = _as_netscape(raw, site)
    if not raw or not _cookie_text_has_login(raw, meta):
        raise CookieImportError(f"没读到 {primary}")
    raw = _refresh_session_expiry(raw)
    return raw if raw.endswith("\n") else raw + "\n"


def login_report(text: str, site: str = "bilibili") -> dict:
    """Primary login-cookie name and length. Never includes the value."""
    meta = site_info(site)
    primary = _PRIMARY_COOKIE.get(meta["id"], "SESSDATA")
    netscape = text or ""
    if netscape and not _looks_like_netscape(netscape):
        netscape = _as_netscape(netscape, site)
    length = 0
    for line in (netscape or "").splitlines():
        raw = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 7:
            continue
        name, value = parts[5], parts[6]
        if name == primary and value:
            length = len(value)
            break
    found = length > 0
    message = f"读到了 {primary}（{length} 字符）" if found else f"没读到 {primary}"
    return {"name": primary, "found": found, "length": length, "message": message}


def save_netscape_text(text: str, site: str = "bilibili") -> dict:
    """Save pasted SESSDATA or a cookies.txt. Never returns cookie values."""
    raw = build_netscape_text(text, site)
    meta = site_info(site)
    tmp = config.COOKIE_FILE.with_name(f".cookies-upload-{os.getpid()}.tmp")
    try:
        tmp.write_text(raw, encoding="utf-8")
        _commit_cookie_file(tmp)
    except CookieImportError:
        _unlink_quiet(tmp)
        raise
    except Exception:
        _unlink_quiet(tmp)
        raise CookieImportError("上传失败，请稍后再试") from None
    return {"ok": True, "message": "已读取登录状态", "site": meta["id"]}


def _session_expiry() -> str:
    return str(int(time.time()) + 86400 * 400)


def _netscape_line(domain: str, name: str, value: str) -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t{_session_expiry()}\t{name}\t{value}\n"


def _cookie_header_from_curl(text: str) -> str:
    raw = text or ""
    header = re.search(
        r"""(?:-H|--header)\s+['"]Cookie:\s*([^'"]+)['"]""", raw, re.I
    )
    if header:
        return header.group(1).strip()
    cookie = re.search(r"""(?:-b|--cookie)\s+['"]([^'"]+)['"]""", raw, re.I)
    if cookie:
        return cookie.group(1).strip()
    return ""


def _as_netscape(text: str, site: str) -> str:
    meta = site_info(site)
    raw = (text or "").strip().strip('"').strip("'")
    raw = _cookie_header_from_curl(raw) or raw
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    wanted = set(meta["cookie_names"])
    primary = _PRIMARY_COOKIE.get(meta["id"], "SESSDATA")
    wanted.add(primary)
    found: dict[str, str] = {}
    for match in re.finditer(r"(?:^|[;\s])([A-Za-z0-9_.-]+)\s*=\s*([^\s;]+)", raw):
        name, value = match.group(1), match.group(2).strip().strip('"').strip("'")
        if name in wanted and value:
            found[name] = value
    if not found:
        skip = {"name", "value", "domain", "path", "expires", "size"}
        for line in raw.splitlines():
            cells = [c.strip() for c in re.split(r"[\t]+", line.strip()) if c.strip()]
            if len(cells) < 2:
                cells = [c.strip() for c in re.split(r"[ ]{2,}", line.strip()) if c.strip()]
            if len(cells) < 2:
                continue
            name, value = cells[0], cells[1]
            if name.lower() in skip or value.lower() in skip:
                continue
            if name in wanted and value:
                found[name] = value
    if not found and "\n" not in raw and "=" not in raw:
        if re.fullmatch(r"[A-Za-z0-9_\-%*.,]{8,}", raw):
            found[primary] = raw
    if not found:
        return ""
    domain = "." + meta["host"].lstrip(".")
    lines = ["# Netscape HTTP Cookie File\n"]
    for name, value in found.items():
        lines.append(_netscape_line(domain, name, value))
    return "".join(lines)


def _refresh_session_expiry(text: str) -> str:
    """cookiejar treats Netscape expiry 0 as already expired."""
    exp = _session_expiry()
    out = []
    for line in (text or "").splitlines():
        prefix = ""
        raw = line
        if raw.startswith("#HttpOnly_"):
            prefix = "#HttpOnly_"
            raw = raw[len(prefix):]
        if not raw or raw.startswith("#"):
            out.append(line)
            continue
        parts = raw.split("\t")
        if len(parts) >= 7 and parts[4] in ("0", ""):
            parts[4] = exp
            out.append(prefix + "\t".join(parts))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _looks_like_netscape(text: str) -> bool:
    lines = (text or "").splitlines()
    if any("HTTP Cookie File" in line or line.startswith("# Netscape") for line in lines[:8]):
        return True
    for line in lines:
        raw = line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) >= 7 and "." in parts[0]:
            return True
    return False


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
    return _cookie_text_has_login(text, meta)


def _cookie_text_has_login(text: str, meta: dict) -> bool:
    for line in (text or "").splitlines():
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
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
