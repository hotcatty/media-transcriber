"""Text helpers: traditional→simplified conversion, error cleanup, filenames."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

try:
    import opencc as _opencc

    _T2S = _opencc.OpenCC("t2s")

    def to_simplified(text: str) -> str:
        return _T2S.convert(text) if text else text

except Exception:  # opencc unavailable: pass through unchanged
    def to_simplified(text: str) -> str:
        return text


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ANSI_BARE_RE = re.compile(r"\[(?:\d{1,3};)*\d{1,3}m")
_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_TZ_CHINA = timezone(timedelta(hours=8))


def strip_ansi(text: str) -> str:
    """yt-dlp writes ANSI colour codes into exception messages; drop them."""
    s = _ANSI_RE.sub("", text or "")
    s = _ANSI_BARE_RE.sub("", s)
    return s.strip()


def looks_like_url(text: str) -> bool:
    t = (text or "").strip()
    if _URL_RE.match(t):
        return True
    return "spm_id_from=" in t or "vd_source=" in t


def canonical_source(url: str) -> str:
    """Stable id for a share link, ignoring tracking params and www."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("upload:"):
        return raw.lower()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return (url or "").strip().lower()
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    qs = parse_qs(parsed.query)

    if host in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        vid = (qs.get("v") or [None])[0]
        if not vid:
            m = re.match(r"^/(?:shorts|embed|live)/([^/?]+)", path)
            vid = m.group(1) if m else None
        if vid:
            return f"youtube:{vid}"
    if host in ("youtu.be", "m.youtu.be"):
        vid = path.strip("/").split("/")[0]
        if vid:
            return f"youtube:{vid}"

    if host.endswith("bilibili.com"):
        m = re.search(r"(BV[0-9A-Za-z]+)", path)
        if m:
            return f"bilibili:{m.group(1)}"
        m = re.search(r"/video/av(\d+)", path, re.I)
        if m:
            return f"bilibili:av{m.group(1)}"
    if host == "b23.tv":
        slug = path.strip("/")
        if slug:
            return f"b23:{slug}"

    if "xiaoyuzhou" in host:
        m = re.search(r"/episode/([^/?]+)", path)
        if m:
            return f"xiaoyuzhou:{m.group(1)}"

    return f"{host}{path.rstrip('/')}".lower()


_FALLBACK_TITLE = re.compile(
    r"^(B站视频|小宇宙播客|YouTube 视频|Spotify 播客|喜马拉雅|播客|分享内容) · \d+月"
)


def is_real_title(title: str) -> bool:
    t = (title or "").strip()
    if not t or t.lower() in ("unknown", "untitled", "未命名"):
        return False
    if looks_like_url(t):
        return False
    if _FALLBACK_TITLE.match(t):
        return False
    return True


def platform_label(source: str, source_type: str = "url") -> str:
    if source_type == "upload" or (source or "").startswith("upload:"):
        name = source.split(":", 1)[-1] if source else ""
        return Path(name).stem or "本地文件"
    u = (source or "").lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "B站视频"
    if "xiaoyuzhou" in u:
        return "小宇宙播客"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube 视频"
    if "spotify.com" in u:
        return "Spotify 播客"
    if "ximalaya.com" in u:
        return "喜马拉雅"
    if source_type == "podcast":
        return "播客"
    return "分享内容"


def format_local_when(iso: str) -> str:
    """'8月29日 00:12' in China time, from an ISO timestamp."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_TZ_CHINA)
        return f"{dt.month}月{dt.day}日 {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return ""


def format_slash_when(iso: str) -> str:
    """'2026/9/9 00:17' in China time, matching the Figma history/transcript copy."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_TZ_CHINA)
        return f"{dt.year}/{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return ""


LOGIN_PARSE_HINT = "该视频需在登录状态下才能解析"
INVALID_LINK_HINT = "链接无效，请重新填写"
TEMPORARY_PARSE_HINT = "暂时无法解析，请稍后重试"
DOWNLOAD_FAIL_HINT = "暂时无法下载音频，请稍后重试"
CHARGE_VIDEO_HINT = "该视频为充电视频，暂时无法下载音频/视频"
CHARGE_NEED_LOGIN_HINT = "该视频为充电视频，请读取已充电账号的登录状态"


def is_login_required(text: str) -> bool:
    raw = strip_ansi(text or "")
    # Charge-gated copy talks about 登录状态 but is not the parse-login bucket.
    if "充电视频" in raw:
        return False
    # Old mixed copy is not a login signal.
    raw = raw.replace("可能需要登录", "")
    low = raw.lower().replace("’", "'").replace("‘", "'")
    if "412" in raw or "precondition failed" in low:
        return True
    if "登录状态" in raw or "需要登录后" in raw:
        return True
    return any(
        k in low
        for k in (
            "sign in",
            "login required",
            "please log in",
            "cookies are needed",
            "not a bot",
            "confirm you",
            "use --cookies",
            "age-restricted",
            "members-only",
            "join this channel",
        )
    )


def is_invalid_link(text: str) -> bool:
    low = strip_ansi(text or "").lower()
    return any(
        k in low
        for k in (
            "no valid video url",
            "unsupported url",
            "not a valid url",
            "invalid url",
            "malformed url",
            "http error 404",
            " 404",
            "unable to extract",
        )
    )


def is_charge_need_login(text: str) -> bool:
    raw = strip_ansi(text or "")
    if CHARGE_NEED_LOGIN_HINT in raw:
        return True
    return "充电视频" in raw and ("登录" in raw or "充电账号" in raw)


def is_charge_gated(text: str) -> bool:
    raw = strip_ansi(text or "")
    if CHARGE_VIDEO_HINT in raw or "充电视频" in raw or "充电专属" in raw:
        return True
    low = raw.lower()
    return any(
        k in low
        for k in (
            "supporter-only",
            "supporter only",
            "is_upower_exclusive",
            "upower exclusive",
            "upower_preview",
        )
    )


def parse_fail_hint(source: str, source_type: str, error: str, status: str = "") -> str:
    """Independent parse-fail copy: invalid link, needs login, or temporary."""
    if is_charge_need_login(error) or (status == "needs_login" and is_charge_gated(error)):
        return CHARGE_NEED_LOGIN_HINT
    if status == "needs_login" or is_login_required(error):
        return LOGIN_PARSE_HINT
    if source_type == "url" and not looks_like_url(source or ""):
        return INVALID_LINK_HINT
    return friendly_error(error or "")


def fail_hint_for_task(
    source: str,
    source_type: str,
    error: str,
    status: str,
    stage: str,
    title: str = "",
    has_audio: bool = False,
) -> str:
    """Copy for the failed step: parse vs download stay independent."""
    st = stage or ""
    low = (error or "").lower()
    if is_charge_need_login(error) or (status == "needs_login" and is_charge_gated(error)):
        return CHARGE_NEED_LOGIN_HINT
    if is_charge_gated(error):
        return CHARGE_VIDEO_HINT
    if st in ("downloading", "converting") or "unable to download video data" in low:
        return DOWNLOAD_FAIL_HINT
    if (
        status == "failed"
        and is_real_title(title)
        and not has_audio
        and st not in ("transcribing", "planning", "finalizing")
        and (error or "") in (DOWNLOAD_FAIL_HINT, TEMPORARY_PARSE_HINT)
    ):
        return DOWNLOAD_FAIL_HINT
    return parse_fail_hint(source, source_type, error, status)


def display_title(
    title: str,
    source: str = "",
    source_type: str = "url",
    created_at: str = "",
) -> str:
    """Human title for cards: real name, else「B站视频 · 8月29日 00:12」."""
    if is_real_title(title):
        return title.strip()
    label = platform_label(source, source_type)
    if source_type == "upload":
        return (title or "").strip() or label
    when = format_local_when(created_at)
    return f"{label} · {when}" if when else label


def friendly_error(text: str) -> str:
    """Turn yt-dlp / HTTP dumps into a short Chinese sentence.

    Parse-fail copy stays in independent buckets: invalid link, needs login,
    or a temporary failure. Never mix 链接 and 登录 in one sentence.
    """
    raw = strip_ansi(text or "")
    if not raw:
        return TEMPORARY_PARSE_HINT

    keep_as_is = ("转录完成", "已取消", "服务重启", "已加入队列", "准备继续")
    if any(k in raw for k in keep_as_is) and len(raw) < 80:
        return raw

    low = raw.lower()
    if "请检查链接或登录信息" in raw or "可能需要登录" in raw:
        return TEMPORARY_PARSE_HINT
    if is_charge_need_login(raw):
        return CHARGE_NEED_LOGIN_HINT
    if is_charge_gated(raw):
        return CHARGE_VIDEO_HINT
    if "unable to download video data" in low:
        return DOWNLOAD_FAIL_HINT
    if is_login_required(raw):
        return LOGIN_PARSE_HINT
    if is_invalid_link(raw):
        return INVALID_LINK_HINT
    if "unable to download webpage" in low or "timed out" in low or "timeout" in low:
        return TEMPORARY_PARSE_HINT
    if "connection" in low or "network" in low or "errno" in low:
        return TEMPORARY_PARSE_HINT
    if "http error 403" in low or " 403" in raw or "forbidden" in low:
        return TEMPORARY_PARSE_HINT
    if "private video" in low:
        return "视频不公开"
    if "video unavailable" in low:
        return "视频不可用"
    if "ffmpeg" in low and ("not found" in low or "找不到" in raw):
        return "本机缺少音频处理工具"
    if "没有可转录的音频" in raw:
        return "没有可转录的音频"
    if "上传文件已丢失" in raw:
        return "上传的文件已丢失，请重新上传"

    cleaned = re.sub(r"^处理失败[:：]\s*", "", raw)
    cleaned = re.sub(r"^下载视频失败[:：]\s*", "", cleaned)
    cleaned = re.sub(r"^ERROR:\s*", "", cleaned, flags=re.I).strip()
    if (
        re.search(r"[\u4e00-\u9fff]", cleaned)
        and len(cleaned) <= 100
        and "链接" not in cleaned
        and "登录" not in cleaned
    ):
        return cleaned
    return TEMPORARY_PARSE_HINT


_UNSAFE_FILENAME = re.compile(r"[^\w\-\u4e00-\u9fff]+", re.UNICODE)


def safe_filename(title: str, max_len: int = 80, fallback: str = "untitled") -> str:
    if not title:
        return fallback
    safe = _UNSAFE_FILENAME.sub("_", title).strip("._-")
    return safe[:max_len] or fallback


def format_timestamp(seconds: float, always_hours: bool = False, decimals: int = 0,
                     separator: str = ".") -> str:
    """Format seconds as HH:MM:SS[.mmm] / MM:SS."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    frac = seconds - int(seconds)

    if hours or always_hours:
        base = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        base = f"{minutes:02d}:{secs:02d}"
    if decimals:
        base += f"{separator}{int(round(frac * (10 ** decimals))):0{decimals}d}"
    return base


def humanize_duration(seconds: Optional[float]) -> str:
    """'1 小时 38 分钟' style, for UI copy."""
    if seconds is None or seconds < 0:
        return "未知"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {secs} 秒" if secs else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"


_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def is_cjk_text(text: str, sample: int = 400) -> bool:
    head = (text or "")[:sample]
    if not head:
        return False
    return len(_CJK.findall(head)) / max(len(head), 1) > 0.15


def join_segment_texts(texts: list[str]) -> str:
    """
    Join segment texts into a paragraph. CJK needs no spaces between segments;
    Latin scripts do.
    """
    parts = [t.strip() for t in texts if t and t.strip()]
    if not parts:
        return ""
    if is_cjk_text("".join(parts[:5])):
        return "".join(parts)
    return " ".join(parts)
