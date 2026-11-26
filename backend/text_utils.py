"""Text helpers: traditional→simplified conversion, error cleanup, filenames."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlunparse

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


_SHARE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_SHARE_URL_CUT_RE = re.compile(r"[，。；、！？,);]+|[\u4e00-\u9fff]")


def extract_share_url(text: str) -> str:
    """First http(s) URL in a paste. Xiaohongshu shares often wrap the link in copy."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = _SHARE_URL_RE.search(raw)
    if m:
        return _SHARE_URL_CUT_RE.split(m.group(0), 1)[0].rstrip(").,;]")
    if raw.lower().startswith("www."):
        return "https://" + raw
    return raw


def looks_like_url(text: str) -> bool:
    t = extract_share_url(text)
    if _URL_RE.match(t):
        return True
    return "spm_id_from=" in t or "vd_source=" in t or "xsec_token=" in t


def is_apple_podcasts_url(url: str) -> bool:
    u = (url or "").lower()
    return "podcasts.apple.com" in u or "itunes.apple.com" in u


def apple_episode_id(url: str) -> str:
    raw = extract_share_url(url)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    eid = (parse_qs(parsed.query).get("i") or [""])[0]
    return eid if eid.isdigit() else ""


def apple_show_id(url: str) -> str:
    """Podcast id from /id123 in the path. The episode id lives in ?i=."""
    raw = extract_share_url(url)
    if not raw:
        return ""
    try:
        path = urlparse(raw).path or ""
    except Exception:
        return ""
    match = re.search(r"/id(\d+)", path, re.I)
    return match.group(1) if match else ""


def apple_storefront(url: str) -> str:
    """Two-letter Apple storefront in the path, such as de in /de/podcast/…."""
    raw = extract_share_url(url)
    if not raw:
        return ""
    try:
        parts = [p for p in (urlparse(raw).path or "").split("/") if p]
    except Exception:
        return ""
    if not parts:
        return ""
    code = parts[0].lower()
    if re.fullmatch(r"[a-z]{2}", code):
        return code
    return ""


def is_bilibili_bangumi_url(url: str) -> bool:
    """Bilibili bangumi play links (/bangumi/play/ep… or /ss…), not ordinary BV videos."""
    raw = extract_share_url(url) or (url or "").strip()
    if not raw:
        return False
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    try:
        path = urlparse(raw).path or ""
    except Exception:
        return False
    return re.search(r"/bangumi/play/(?:ep|ss)\d+", path, re.I) is not None


def is_xiaohongshu_url(url: str) -> bool:
    u = (url or "").lower()
    return "xiaohongshu.com" in u or "xhslink.com" in u or "xhslink.cn" in u


def is_douyin_url(url: str) -> bool:
    u = (url or "").lower()
    return "douyin.com" in u or "iesdouyin.com" in u


def is_weixin_url(url: str) -> bool:
    u = (url or "").lower()
    return "weixin.qq.com" in u or "channels.weixin.qq.com" in u


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def normalize_xiaohongshu_url(url: str) -> str:
    """www + explore path so yt-dlp's XiaoHongShu extractor matches. Keep xsec_token."""
    raw = extract_share_url(url)
    if not raw or not is_xiaohongshu_url(raw):
        return raw or (url or "")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host in ("xiaohongshu.com", "m.xiaohongshu.com") or host.endswith(".xiaohongshu.com"):
        if host != "www.xiaohongshu.com":
            parsed = parsed._replace(netloc="www.xiaohongshu.com")
    path = parsed.path or ""
    m = re.match(r"^/user/profile/[^/]+/([\da-f]+)/?", path, re.I)
    if m:
        parsed = parsed._replace(path=f"/explore/{m.group(1)}")
    return urlunparse(parsed)


def bilibili_part_query(url: str) -> str:
    """Later-part index from a Bilibili ``p`` query. Empty means the first part.

    ``p`` is 1-based. A missing ``p``, ``p=1``, and a non-numeric ``p`` are the
    first part. ``p=2`` and ``p=02`` are both part 2. Tracking params are ignored.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        page = (parse_qs(urlparse(raw).query).get("p") or [""])[0]
    except Exception:
        return ""
    text = str(page).strip()
    if not text.isdigit():
        return ""
    number = int(text)
    if number <= 1:
        return ""
    return str(number)


def canonical_source(url: str) -> str:
    """Stable id for a share link, ignoring tracking params and www.

    A Bilibili multi-part URL keeps ``p`` when it is not the first part, so
    P2 of a BV is not the same task as P1.
    """
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
        part = bilibili_part_query(raw)
        suffix = f":p{part}" if part else ""
        m = re.search(r"(BV[0-9A-Za-z]+)", path)
        if m:
            return f"bilibili:{m.group(1)}{suffix}"
        m = re.search(r"/video/av(\d+)", path, re.I)
        if m:
            return f"bilibili:av{m.group(1)}{suffix}"
    if host == "b23.tv":
        slug = path.strip("/")
        if slug:
            part = bilibili_part_query(raw)
            suffix = f":p{part}" if part else ""
            return f"b23:{slug}{suffix}"

    if "xiaoyuzhou" in host:
        m = re.search(r"/episode/([^/?]+)", path)
        if m:
            return f"xiaoyuzhou:{m.group(1)}"

    if host.endswith("xiaohongshu.com"):
        m = re.search(r"/(?:explore|discovery/item|video)/([\da-f]+)", path, re.I)
        if m:
            return f"xiaohongshu:{m.group(1)}"
        m = re.search(r"/user/profile/[^/]+/([\da-f]+)", path, re.I)
        if m:
            return f"xiaohongshu:{m.group(1)}"
    if host.endswith("xhslink.com") or host.endswith("xhslink.cn"):
        slug = path.strip("/")
        if slug:
            return f"xhslink:{slug}"

    if "podcasts.apple.com" in host or "itunes.apple.com" in host:
        eid = (qs.get("i") or [None])[0]
        if eid and str(eid).isdigit():
            return f"apple:{eid}"

    return f"{host}{path.rstrip('/')}".lower()


_FALLBACK_TITLE = re.compile(
    r"^(B站视频|小宇宙播客|小红书视频|YouTube 视频|苹果播客|Spotify 播客|喜马拉雅|播客|分享内容) · \d+月"
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
    if "xiaohongshu.com" in u or "xhslink.com" in u or "xhslink.cn" in u:
        return "小红书视频"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube 视频"
    if "podcasts.apple.com" in u or "itunes.apple.com" in u:
        return "苹果播客"
    if "spotify.com" in u:
        return "Spotify 播客"
    if "ximalaya.com" in u:
        return "喜马拉雅"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "抖音视频"
    if "weixin.qq.com" in u or "channels.weixin.qq.com" in u:
        return "微信视频"
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


LOGIN_PARSE_HINT = "需要登录后再试"
INVALID_LINK_HINT = "链接无效，请重新填写"
TEMPORARY_PARSE_HINT = "暂时无法解析，请稍后重试"
DOWNLOAD_FAIL_HINT = "暂时无法下载音频，请稍后重试"
CHARGE_VIDEO_HINT = "当前账号无法观看本充电专属"
CHARGE_NEED_LOGIN_HINT = "充电视频，请读取已充电账号"
BILI_SHORT_LINK_HINT = "请粘贴完整视频网址，不要用短链"
BILI_RISK_HINT = "B站暂时拒绝访问，请稍后再试"
NO_MEDIA_HINT = "这篇笔记没有可转录的视频"
APPLE_EPISODE_HINT = "请打开某一集，再粘贴这一集的苹果播客链接"
# A real episode link whose audio still could not be resolved. Not an invalid URL.
APPLE_EXTRACT_HINT = "苹果播客暂时解析不到，请稍后再试"
# Bangumi play links are usually premium. We do not pretend the audio is available.
BANGUMI_PAID_HINT = "这是 B 站番剧，多半是付费内容，暂时拿不到音频"
YOUTUBE_UNREACHABLE_HINT = "这台服务器连不上 YouTube，暂时没法解析这条链接"
YOUTUBE_DISABLED_HINT = "线上体验版暂不支持 YouTube，请下载本地版（GitHub Release）"
PUBLIC_HOST_HINT = "线上体验版只支持小宇宙、B 站、小红书、抖音、微信、苹果播客"
WECHAT_HINT = "微信视频号暂时解析不到"
# Old public-web copy; keep matching historical errors, surface as LOGIN_PARSE_HINT.
PUBLIC_COOKIE_HINT = "线上体验版不接受登录 Cookie，请只转录公开内容，或改用本地版"
PUBLIC_RATE_HINT = "提交太频繁，请稍后再试"
PUBLIC_BUSY_HINT = "您有任务正在进行，请等完成后再提交"


_PUBLIC_HOSTS = (
    "xiaoyuzhoufm.com",
    "bilibili.com",
    "b23.tv",
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
    "douyin.com",
    "iesdouyin.com",
    "weixin.qq.com",
    "channels.weixin.qq.com",
    "podcasts.apple.com",
    "itunes.apple.com",
)


def host_allowed_on_public(url: str) -> bool:
    """http(s) links on the trial allowlist only — no IPs, file URLs, or random hosts."""
    raw = extract_share_url(url)
    if not raw:
        return False
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if (parsed.scheme or "").lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return False
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", host):
        return False
    if ":" in host:
        return False
    return any(host == h or host.endswith("." + h) for h in _PUBLIC_HOSTS)


def bili_block_hint(text: str) -> str:
    """User copy after Bilibili rejects the request. No status code, no 登录."""
    raw = strip_ansi(text or "")
    if BILI_SHORT_LINK_HINT in raw:
        return BILI_SHORT_LINK_HINT
    if BILI_RISK_HINT in raw:
        return BILI_RISK_HINT
    low = raw.lower()
    # yt-dlp's Bilibili extractor hides some risk-control replies behind
    # these sentences. They are not a login prompt and not a generic timeout.
    if (
        "412" in raw
        or "precondition failed" in low
        or "请求被拦截" in raw
        or "风控" in raw
        or "-352" in raw
        or "rejected by server (352)" in low
        or "blocked by server (401)" in low
        or "exceeded the rate limit. try again later" in low
    ):
        return BILI_RISK_HINT
    return ""


def is_login_required(text: str) -> bool:
    raw = strip_ansi(text or "")
    # Charge-gated copy talks about 登录状态 but is not the parse-login bucket.
    if "充电视频" in raw:
        return False
    # Risk-control copy is not a login prompt, including the short-link wording.
    if bili_block_hint(raw):
        return False
    # Old mixed copy is not a login signal.
    raw = raw.replace("可能需要登录", "")
    low = raw.lower().replace("’", "'").replace("‘", "'")
    # B 站风控、yt-dlp 随口提 --cookies，不能当成要登录。
    if "412" in raw or "precondition failed" in low:
        return False
    # 抖音要的是浏览器访客 Cookie，不一定已登录。
    if "fresh cookies" in low or "not necessarily logged in" in low:
        return True
    if "300031" in raw or "this page isn't available" in low:
        return True
    if "登录状态" in raw or "需要登录后" in raw:
        return True
    if "不接受登录 Cookie" in raw:
        return True
    return any(
        k in low
        for k in (
            "sign in",
            "login required",
            "please log in",
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


def is_charge_blocked(text: str) -> bool:
    """Cookie was used, and this account still cannot download the charge video."""
    raw = strip_ansi(text or "")
    if CHARGE_VIDEO_HINT in raw or "无法观看本充电专属" in raw:
        return True
    return any(k in raw for k in ("仍无法下载", "仍下不了", "当前账号仍"))


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
    if is_bilibili_bangumi_url(source):
        return BANGUMI_PAID_HINT
    blocked = bili_block_hint(error)
    if blocked:
        return blocked
    if is_charge_blocked(error):
        return CHARGE_VIDEO_HINT
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
    if is_bilibili_bangumi_url(source):
        return BANGUMI_PAID_HINT
    st = stage or ""
    low = (error or "").lower()
    blocked = bili_block_hint(error)
    if blocked:
        return blocked
    if is_charge_blocked(error):
        return CHARGE_VIDEO_HINT
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
    if NO_MEDIA_HINT in raw:
        return NO_MEDIA_HINT
    if YOUTUBE_DISABLED_HINT in raw:
        return YOUTUBE_DISABLED_HINT
    if PUBLIC_HOST_HINT in raw or "线上体验版只支持" in raw:
        return PUBLIC_HOST_HINT
    if PUBLIC_COOKIE_HINT in raw:
        return LOGIN_PARSE_HINT
    if PUBLIC_RATE_HINT in raw:
        return PUBLIC_RATE_HINT
    if PUBLIC_BUSY_HINT in raw:
        return PUBLIC_BUSY_HINT
    if YOUTUBE_UNREACHABLE_HINT in raw:
        return YOUTUBE_UNREACHABLE_HINT
    if APPLE_EPISODE_HINT in raw:
        return APPLE_EPISODE_HINT
    if APPLE_EXTRACT_HINT in raw or "unable to extract js asset path" in low or "unable to extract server data" in low:
        return APPLE_EXTRACT_HINT
    if BANGUMI_PAID_HINT in raw:
        return BANGUMI_PAID_HINT
    if WECHAT_HINT in raw:
        return WECHAT_HINT
    blocked = bili_block_hint(raw)
    if blocked:
        return blocked
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


_HTML_TAG_RE = re.compile(r"<[^>]+>")
SOURCE_CONTEXT_MAX = 8000
# Leave room for the short labeled lines and a caption excerpt.
DESC_CONTEXT_MAX = 4000
CAPTION_CONTEXT_MAX = 1200

# Engagement tags that are not topic words. Real names and subjects stay.
_GENERIC_TAGS = {
    "日常", "生活", "记录", "分享", "原创", "转载", "搞笑", "高能",
    "必剪", "转发", "关注", "vlog", "VLOG", "Vlog",
}


def clean_page_text(raw: str) -> str:
    """Strip tags and collapse whitespace from yt-dlp / OG / shownotes HTML."""
    if not raw:
        return ""
    text = unescape(_HTML_TAG_RE.sub(" ", raw))
    return re.sub(r"\s+", " ", text).strip()


def _clean_meta_list(raw) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        text = clean_page_text(str(item or ""))
        if text and text not in out:
            out.append(text)
    return out


def merge_page_meta(primary: Optional[dict], fallback: Optional[dict]) -> dict:
    """Prefer official fields. Fill only the blanks from a later source."""
    primary = primary or {}
    fallback = fallback or {}
    out: dict = {}
    for key in ("show", "description", "part", "captions"):
        first = clean_page_text(str(primary.get(key) or ""))
        second = clean_page_text(str(fallback.get(key) or ""))
        out[key] = first or second
    out["people"] = _clean_meta_list(primary.get("people")) or _clean_meta_list(fallback.get("people"))
    out["tags"] = _clean_meta_list(primary.get("tags")) or _clean_meta_list(fallback.get("tags"))
    return out


def format_source_context(*, show: str = "", description: str = "",
                          tags: Optional[list] = None, part: str = "",
                          people: Optional[list] = None,
                          captions: str = "") -> str:
    """Compact page context persisted on the task for Whisper and copy-for-AI.

    Empty fields are omitted. The same lines are not written twice.
    """
    show = clean_page_text(show)
    part = clean_page_text(part)
    desc = clean_page_text(description)
    cap = clean_page_text(captions)
    parts: list[str] = []

    def add(line: str) -> None:
        line = (line or "").strip()
        if not line or line in parts:
            return
        parts.append(line)

    if show:
        add(f"节目：{show}")
    guest_names = []
    for raw in people or []:
        name = clean_page_text(str(raw))
        if name and name != show and name not in guest_names:
            guest_names.append(name)
    if guest_names:
        add("嘉宾：" + "、".join(guest_names[:8]))
    if part and part not in (show, desc):
        add(f"分集：{part}")
    label_tags = []
    for raw in tags or []:
        tag = clean_page_text(str(raw))
        if not tag or tag in (show, part) or tag in _GENERIC_TAGS:
            continue
        if not (2 <= len(tag) <= 24):
            continue
        if tag not in label_tags:
            label_tags.append(tag)
    if label_tags:
        add("标签：" + "、".join(label_tags[:16]))
    if desc:
        if show and desc.startswith(show):
            desc = desc[len(show):].lstrip(" ：:-—|/|")
        if desc and desc not in (show, part):
            add(desc[:DESC_CONTEXT_MAX].rstrip())
    if cap and cap not in desc and cap != show and cap != part:
        add("字幕：" + cap[:CAPTION_CONTEXT_MAX].rstrip())
    text = "\n".join(parts).strip()
    if len(text) > SOURCE_CONTEXT_MAX:
        text = text[:SOURCE_CONTEXT_MAX].rstrip()
    return text


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
