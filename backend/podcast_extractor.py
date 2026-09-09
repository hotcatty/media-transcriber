"""
podcast_extractor.py
小宇宙播客音频提取模块。

给定小宇宙分享链接（如 https://www.xiaoyuzhoufm.com/episode/xxx），
解析页面中嵌入的音频直链，返回：
  - audio_url: 音频直链（通常是 mp3/m4a）
  - title: 节目标题
  - podcast_name: 播客名称
  - duration: 时长（秒，可能为 None）
  - cover_url: 封面图片 URL（可能为 None）

同时也支持其他常见播客平台的通用 yt-dlp 降级路径。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 平台识别
# ──────────────────────────────────────────────────────────────────────────────

def detect_podcast_platform(url: str) -> Optional[str]:
    """
    识别播客平台。
    返回平台标识字符串，或 None（不是已知播客 URL）。
    """
    host = urlparse(url).netloc.lower()
    if "xiaoyuzhoufm.com" in host:
        return "xiaoyuzhou"
    if "podcasts.apple.com" in host or "itunes.apple.com" in host:
        return "apple_podcasts"
    if "open.spotify.com" in host and "/episode/" in url:
        return "spotify"
    if "ximalaya.com" in host:
        return "ximalaya"
    return None


def is_podcast_url(url: str) -> bool:
    return detect_podcast_platform(url) is not None


# ──────────────────────────────────────────────────────────────────────────────
# 通用结果结构
# ──────────────────────────────────────────────────────────────────────────────

class PodcastInfo:
    def __init__(
        self,
        audio_url: str,
        title: str,
        podcast_name: str = "",
        duration: Optional[int] = None,
        cover_url: Optional[str] = None,
        description: str = "",
    ):
        self.audio_url = audio_url
        self.title = title
        self.podcast_name = podcast_name
        self.duration = duration
        self.cover_url = cover_url
        self.description = description

    def __repr__(self):
        return f"<PodcastInfo title={self.title!r} audio_url={self.audio_url[:60]}...>"


# ──────────────────────────────────────────────────────────────────────────────
# 小宇宙提取
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_xiaoyuzhou(url: str) -> PodcastInfo:
    """
    解析小宇宙页面，提取音频直链。
    策略：
      1. 优先用 yt-dlp 提取（最稳定）
      2. 降级到 requests + HTML 解析（yt-dlp 不支持时）
    """
    # ── 方案 1：yt-dlp ──────────────────────────────────────────────────────
    try:
        info = await _ytdlp_extract_info(url)
        if info and info.get("url"):
            return PodcastInfo(
                audio_url=info["url"],
                title=info.get("title", "播客节目"),
                podcast_name=info.get("uploader", ""),
                duration=info.get("duration"),
                cover_url=info.get("thumbnail"),
                description=info.get("description", ""),
            )
    except Exception as e:
        logger.warning(f"yt-dlp 提取小宇宙失败，降级到 HTML 解析: {e}")

    # ── 方案 2：HTML 解析 ────────────────────────────────────────────────────
    return await _parse_xiaoyuzhou_html(url)


async def _ytdlp_extract_info(url: str) -> Optional[dict]:
    """用 yt-dlp 提取单条音频信息，只获取 URL 不下载。"""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "skip_download": True,
    }
    try:
        import config as _cfg
        if _cfg.COOKIE_FILE.exists():
            opts["cookiefile"] = str(_cfg.COOKIE_FILE)
    except Exception:
        pass

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # 展平 formats，取第一条可用 url
            if info:
                direct_url = info.get("url")
                if not direct_url and info.get("formats"):
                    for fmt in reversed(info["formats"]):
                        if fmt.get("url") and fmt.get("acodec") != "none":
                            direct_url = fmt["url"]
                            break
                if not direct_url and info.get("formats"):
                    direct_url = info["formats"][-1].get("url")
                info["url"] = direct_url
            return info

    return await asyncio.to_thread(_run)


async def _parse_xiaoyuzhou_html(url: str) -> PodcastInfo:
    """
    降级方案：requests 抓取页面，从 HTML / JSON-LD / meta 标签中提取音频信息。
    """
    import json

    try:
        import requests
    except ImportError:
        raise RuntimeError("requests 库未安装，请运行 pip install requests")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.xiaoyuzhoufm.com/",
    }

    def _get():
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text

    html = await asyncio.to_thread(_get)

    title = _extract_og_meta(html, "title") or _extract_title_tag(html) or "播客节目"
    podcast_name = _extract_og_meta(html, "site_name") or ""
    cover_url = _extract_og_meta(html, "image")
    description = _extract_og_meta(html, "description") or ""

    # ── 尝试从 JSON-LD 提取 contentUrl ───────────────────────────────────────
    audio_url = _extract_audio_from_jsonld(html)

    # ── 尝试从页面内 __NEXT_DATA__ 提取 ─────────────────────────────────────
    if not audio_url:
        audio_url = _extract_audio_from_next_data(html)

    # ── 尝试直接正则匹配 audio src ──────────────────────────────────────────
    if not audio_url:
        audio_url = _extract_audio_regex(html)

    if not audio_url:
        raise ValueError(
            f"无法从页面提取音频链接，请检查 URL 是否正确: {url}\n"
            "提示：小宇宙部分节目可能需要登录或 App 内才能播放。"
        )

    logger.info(f"从 HTML 提取到音频链接: {audio_url[:80]}...")
    return PodcastInfo(
        audio_url=audio_url,
        title=title,
        podcast_name=podcast_name,
        duration=None,
        cover_url=cover_url,
        description=description,
    )


def _extract_og_meta(html: str, prop: str) -> Optional[str]:
    """提取 og: meta 标签。"""
    pattern = rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']'
    m = re.search(pattern, html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # 反向属性顺序
    pattern2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']'
    m2 = re.search(pattern2, html, re.IGNORECASE)
    return m2.group(1).strip() if m2 else None


def _extract_title_tag(html: str) -> Optional[str]:
    m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_audio_from_jsonld(html: str) -> Optional[str]:
    """从 JSON-LD 结构化数据中提取 contentUrl 或 url。"""
    import json

    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html, re.IGNORECASE):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                data = data[0]
            # 可能是 AudioObject 或 PodcastEpisode
            for key in ("contentUrl", "url", "associatedMedia"):
                val = data.get(key)
                if isinstance(val, dict):
                    val = val.get("contentUrl") or val.get("url")
                if isinstance(val, str) and _looks_like_audio_url(val):
                    return val
        except Exception:
            continue
    return None


def _extract_audio_from_next_data(html: str) -> Optional[str]:
    """从 Next.js __NEXT_DATA__ 全局变量中提取音频 URL。"""
    import json

    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([\s\S]*?)</script>', html, re.IGNORECASE)
    if not m:
        return None

    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    # 深度搜索包含音频 URL 的字段
    return _deep_find_audio_url(data)


def _deep_find_audio_url(obj, depth: int = 0) -> Optional[str]:
    """递归搜索字典/列表，找到疑似音频直链的字段。"""
    if depth > 10:
        return None

    if isinstance(obj, str):
        if _looks_like_audio_url(obj):
            return obj
        return None

    if isinstance(obj, dict):
        # 优先检查语义化字段名
        for key in ("enclosure", "mediaKey", "audioUrl", "audio_url", "playUrl", "enclosureUrl", "url", "src"):
            val = obj.get(key)
            if isinstance(val, str) and _looks_like_audio_url(val):
                return val
            if isinstance(val, dict):
                result = _deep_find_audio_url(val, depth + 1)
                if result:
                    return result
        # 遍历所有值
        for v in obj.values():
            result = _deep_find_audio_url(v, depth + 1)
            if result:
                return result

    if isinstance(obj, list):
        for item in obj[:20]:  # 限制遍历数量，避免爆栈
            result = _deep_find_audio_url(item, depth + 1)
            if result:
                return result

    return None


def _extract_audio_regex(html: str) -> Optional[str]:
    """正则匹配 audio src 属性或常见 CDN 音频 URL。"""
    # audio 标签
    m = re.search(r'<audio[^>]+src=["\']([^"\']+\.(?:mp3|m4a|ogg|aac|wav)[^"\']*)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1)

    # 常见 CDN 模式（小宇宙使用 cdn.xyzcdn.net 等）
    patterns = [
        r'https://[^\s"\']+\.(?:mp3|m4a)(?:\?[^\s"\']*)?',
        r'https://cdn[^\s"\']+/[^\s"\']+\.(?:mp3|m4a)(?:\?[^\s"\']*)?',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            url = m.group(0).rstrip("'\">,;")
            if _looks_like_audio_url(url):
                return url

    return None


def _looks_like_audio_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    lower = url.lower()
    # 扩展名检查
    if any(ext in lower for ext in (".mp3", ".m4a", ".ogg", ".aac", ".flac", ".wav")):
        return True
    # CDN 特征
    if any(cdn in lower for cdn in ("cdn.xyzcdn", "aod.cos", "audiomack", "podtrac")):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# 喜马拉雅
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_ximalaya(url: str) -> PodcastInfo:
    """喜马拉雅走 yt-dlp。"""
    try:
        info = await _ytdlp_extract_info(url)
        if info and info.get("url"):
            return PodcastInfo(
                audio_url=info["url"],
                title=info.get("title", "喜马拉雅音频"),
                podcast_name=info.get("uploader", ""),
                duration=info.get("duration"),
                cover_url=info.get("thumbnail"),
                description=info.get("description", ""),
            )
    except Exception as e:
        logger.error(f"yt-dlp 提取喜马拉雅失败: {e}")
    raise ValueError(f"无法从喜马拉雅提取音频，请手动下载后上传: {url}")


# ──────────────────────────────────────────────────────────────────────────────
# 统一入口
# ──────────────────────────────────────────────────────────────────────────────

async def extract_podcast_audio(url: str) -> PodcastInfo:
    """
    根据 URL 自动选择提取策略，返回 PodcastInfo。
    调用方捕获异常并处理降级逻辑。
    """
    platform = detect_podcast_platform(url)
    logger.info(f"播客平台识别: {platform} | URL: {url}")

    if platform == "xiaoyuzhou":
        return await _fetch_xiaoyuzhou(url)
    elif platform == "ximalaya":
        return await _fetch_ximalaya(url)
    else:
        # Apple Podcasts / Spotify / 其他：优先 yt-dlp
        try:
            info = await _ytdlp_extract_info(url)
            if info and info.get("url"):
                return PodcastInfo(
                    audio_url=info["url"],
                    title=info.get("title", "播客节目"),
                    podcast_name=info.get("uploader", ""),
                    duration=info.get("duration"),
                    cover_url=info.get("thumbnail"),
                    description=info.get("description", ""),
                )
        except Exception as e:
            logger.error(f"yt-dlp 提取失败: {e}")
        raise ValueError(f"不支持的播客 URL 或无法自动提取音频: {url}")
