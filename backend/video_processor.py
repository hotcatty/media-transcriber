import os
import re
import json
import shutil
import uuid
import asyncio
import subprocess
import urllib.request
import yt_dlp
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import config
from engines import Segment
from text_utils import (
    CHARGE_NEED_LOGIN_HINT,
    CHARGE_VIDEO_HINT,
    is_charge_gated,
    strip_ansi as _clean,
    to_simplified as _to_simplified,
)

logger = logging.getLogger(__name__)


class VideoProcessor:
    """视频/音频处理器，使用 yt-dlp 下载和转换视频"""

    COOKIE_FILE = str(config.COOKIE_FILE)
    _UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # ffmpeg 路径（自动探测，兼容 Homebrew on Apple Silicon）
    @staticmethod
    def _find_ffmpeg() -> str:
        import shutil
        for candidate in [
            '/opt/homebrew/bin/ffmpeg',   # Apple Silicon Homebrew
            '/usr/local/bin/ffmpeg',       # Intel Homebrew
            shutil.which('ffmpeg') or '',
        ]:
            if candidate and __import__('os').path.isfile(candidate):
                return candidate
        return 'ffmpeg'  # 回退到 PATH 查找

    def __init__(self):
        ffmpeg_path = self._find_ffmpeg()
        logger.info(f"使用 ffmpeg: {ffmpeg_path}")
        opts: dict = {
            'format': 'bestaudio/best',
            'ffmpeg_location': ffmpeg_path,
            'outtmpl': '%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192'
            }],
            'postprocessor_args': ['-ac', '1', '-ar', '16000', '-movflags', '+faststart'],
            'prefer_ffmpeg': True,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'http_headers': {
                'User-Agent': self._UA,
            },
        }
        self.ydl_opts = opts
        if Path(self.COOKIE_FILE).exists():
            logger.info(f"已加载 Cookie 文件: {self.COOKIE_FILE}")
        else:
            logger.info("未找到 cookies.txt，以匿名模式访问（B站等平台可能受限）")

    def _headers_for(self, url: str) -> dict:
        headers = {"User-Agent": self._UA}
        u = (url or "").lower()
        if "bilibili.com" in u or "b23.tv" in u:
            headers["Referer"] = "https://www.bilibili.com/"
        elif "youtube.com" in u or "youtu.be" in u:
            headers["Referer"] = "https://www.youtube.com/"
        return headers

    def _ydl_opts(self, url: str, extra: dict) -> dict:
        opts = dict(extra)
        opts["http_headers"] = self._headers_for(url)
        if Path(self.COOKIE_FILE).exists():
            opts["cookiefile"] = self.COOKIE_FILE
        else:
            opts.pop("cookiefile", None)
        u = (url or "").lower()
        if "youtube.com" in u or "youtu.be" in u:
            opts["extractor_args"] = {
                "youtube": {"player_client": ["android", "ios", "web"]}
            }
        return opts

    def _is_youtube(self, url: str) -> bool:
        u = (url or "").lower()
        return "youtube.com" in u or "youtu.be" in u

    def _is_bilibili(self, url: str) -> bool:
        u = (url or "").lower()
        return "bilibili.com" in u or "b23.tv" in u

    def _bvid(self, url: str, info: Optional[dict] = None) -> Optional[str]:
        for raw in (url, (info or {}).get("webpage_url"), (info or {}).get("id")):
            m = re.search(r"(BV[0-9A-Za-z]+)", str(raw or ""))
            if m:
                return m.group(1)
        return None

    def _bilibili_cookie_header(self) -> str:
        path = Path(self.COOKIE_FILE)
        if not path.exists():
            return ""
        try:
            pairs = []
            for line in path.read_text(errors="replace").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                if raw.startswith("#HttpOnly_"):
                    raw = raw[len("#HttpOnly_"):]
                elif raw.startswith("#"):
                    continue
                parts = raw.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, _path, _secure, _exp, name, value = parts[:7]
                if "bilibili" in (domain or "").lower():
                    pairs.append(f"{name}={value}")
            return "; ".join(pairs)
        except Exception:
            return ""

    def _bilibili_is_charge(self, url: str, info: Optional[dict] = None) -> bool:
        """充电专属且当前账号不能完播（B 站 is_upower_*）。"""
        if not self._is_bilibili(url) and not self._bvid(url, info):
            return False
        bvid = self._bvid(url, info)
        if not bvid:
            return False
        try:
            headers = self._headers_for("https://www.bilibili.com/")
            cookie = self._bilibili_cookie_header()
            if cookie:
                headers["Cookie"] = cookie
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            video = (payload or {}).get("data") or {}
            exclusive = bool(
                video.get("is_upower_exclusive") or video.get("is_upower_preview")
            )
            can_play = bool(video.get("is_upower_play"))
            if exclusive and not can_play:
                logger.info("B站充电视频（仅试看）: %s", bvid)
                return True
        except Exception:
            logger.info("无法确认是否充电视频，按普通视频继续")
        return False

    def _is_429(self, err: BaseException) -> bool:
        low = str(err).lower()
        return "429" in low or "too many requests" in low

    def _retryable(self, err: BaseException) -> bool:
        if self._is_429(err):
            return False
        low = str(err).lower()
        return any(
            k in low
            for k in ("403", "forbidden", "not a bot", "confirm you")
        )

    def _subtitle_priority(self, url: str) -> list:
        zh = ["zh-Hans", "zh-CN", "zh-hans", "zh-Hant", "zh-TW", "zh-hant", "zh"]
        en = ["en-orig", "en", "en-US", "en-GB"]
        other = ["ja", "ko", "fr", "de", "es"]
        # YouTube auto zh-Hans is usually a bad machine translation.
        if self._is_youtube(url):
            return en + zh + other
        return zh + en + other

    def _rank_subtitle_langs(self, url: str, available: dict) -> list:
        ranked = [lang for lang in self._subtitle_priority(url) if lang in available]
        if ranked:
            return ranked[:3]
        if self._is_youtube(url):
            return []
        return list(available.keys())[:1]

    async def _ydl_thread(self, fn):
        try:
            return await asyncio.to_thread(fn)
        except Exception as e:
            if not self._retryable(e):
                raise
            logger.info("平台第一次拒绝访问，1.2 秒后重试一次")
            await asyncio.sleep(1.2)
            return await asyncio.to_thread(fn)

    async def normalize_local_media_to_m4a(self, input_path: Path, output_dir: Path) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        unique_id = str(uuid.uuid4())[:8]
        out_path = output_dir / f"upload_norm_{unique_id}.m4a"

        cmd = [
            "ffmpeg", "-y", "-nostdin", "-i", str(input_path.resolve()),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

        def _run():
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                raise Exception(f"FFmpeg 转换失败: {err[:800]}")
            if not out_path.exists():
                raise Exception("FFmpeg 未生成输出文件")

        await asyncio.to_thread(_run)
        return str(out_path)

    async def download_audio_from_url(self, audio_url: str, output_dir: Path, title: str = "podcast") -> str:
        """
        直接下载音频直链并转换为 m4a，供 Whisper 使用。
        适用于播客提取后拿到的直链。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        unique_id = str(uuid.uuid4())[:8]
        safe_title = re.sub(r'[^\w\-]', '_', title)[:40]
        out_path = output_dir / f"podcast_{safe_title}_{unique_id}.m4a"

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "-i", audio_url,
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(out_path.resolve()),
        ]

        def _run():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                raise Exception(f"FFmpeg 下载音频失败: {err[:800]}")
            if not out_path.exists():
                raise Exception("FFmpeg 未生成输出文件")

        await asyncio.to_thread(_run)
        logger.info(f"音频已下载并转换: {out_path}")
        return str(out_path)

    async def _download_subs_once(self, url: str, dl_opts: dict) -> bool:
        def _dl():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])

        try:
            await asyncio.to_thread(_dl)
            return True
        except Exception as e:
            if not self._is_429(e):
                logger.info(f"字幕下载失败: {e}")
                return False
            logger.info("字幕接口限流，4 秒后重试一次")
            await asyncio.sleep(4.0)
            try:
                await asyncio.to_thread(_dl)
                return True
            except Exception as e2:
                logger.info(f"字幕重试仍失败: {e2}")
                return False

    async def fetch_subtitles(self, url: str, output_dir: Path) -> tuple:
        output_dir.mkdir(exist_ok=True)
        unique_id = str(uuid.uuid4())[:8]
        sub_dir = output_dir / f"subs_{unique_id}"

        video_title = None
        video_duration = None
        charge = False
        try:
            check_opts = self._ydl_opts(url, {
                "quiet": True, "no_warnings": True, "noplaylist": True,
            })

            def _extract():
                with yt_dlp.YoutubeDL(check_opts) as ydl:
                    return ydl.extract_info(url, False)

            info = await self._ydl_thread(_extract)

            video_title = info.get("title", "unknown")
            video_duration = info.get("duration")
            charge = await asyncio.to_thread(self._bilibili_is_charge, url, info)
            manual_subs: dict = info.get("subtitles") or {}
            auto_caps: dict = info.get("automatic_captions") or {}

            manual_langs = [k for k in manual_subs if not k.startswith("live_chat")]
            auto_langs = [k for k in auto_caps if not k.startswith("live_chat")]

            if not manual_langs and not auto_langs:
                logger.info(f"视频无可用字幕: {url}")
                return None, video_title, None, video_duration, charge

            all_langs_auto = {lang: "auto" for lang in auto_langs}
            all_langs_manual = {lang: "manual" for lang in manual_langs}
            all_langs = {**all_langs_auto, **all_langs_manual}
            ranked = self._rank_subtitle_langs(url, all_langs)
            if not ranked:
                logger.info(f"没有可优先使用的字幕语言，回退音频: {url}")
                return None, video_title, None, video_duration, charge

            sub_dir.mkdir(exist_ok=True)
            for prefer_lang in ranked:
                prefer_manual = all_langs.get(prefer_lang) == "manual"
                logger.info(
                    f"字幕选择: lang={prefer_lang}, "
                    f"来源={'手动' if prefer_manual else '自动（ASR）'}, "
                    f"手动可选={manual_langs}, 自动可选={auto_langs}"
                )
                for leftover in sub_dir.glob("*"):
                    try:
                        leftover.unlink()
                    except OSError:
                        pass

                dl_opts = self._ydl_opts(url, {
                    "writesubtitles": prefer_manual,
                    "writeautomaticsub": not prefer_manual,
                    "subtitlesformat": "vtt/srt/best",
                    "subtitleslangs": [prefer_lang],
                    "skip_download": True,
                    "outtmpl": str(sub_dir / "sub.%(ext)s"),
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                })
                if not await self._download_subs_once(url, dl_opts):
                    continue

                sub_files = list(sub_dir.glob("*.vtt")) + list(sub_dir.glob("*.srt"))
                if not sub_files:
                    continue

                sub_file = sub_files[0]
                stem_parts = sub_file.stem.split(".")
                file_lang = stem_parts[-1] if len(stem_parts) > 1 else prefer_lang
                if sub_file.suffix == ".vtt":
                    entries = self._parse_vtt(str(sub_file))
                else:
                    entries = self._parse_srt(str(sub_file))
                if not entries:
                    continue

                segments = self._entries_to_segments(entries)
                logger.info(f"字幕获取成功: lang={file_lang}, {len(segments)} 条")
                return segments, video_title, file_lang, video_duration, charge

            logger.info("字幕均不可用，将回退至音频下载")
            return None, video_title, None, video_duration, charge

        except Exception as e:
            logger.warning(f"字幕获取失败（将回退至音频下载）: {e}")
            return None, video_title, None, video_duration, charge
        finally:
            if sub_dir.exists():
                try:
                    shutil.rmtree(str(sub_dir))
                except Exception:
                    pass

    def _parse_vtt(self, filepath: str) -> list:
        raw_entries = []
        seen_texts: set = set()

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取 VTT 文件失败: {e}")
            return []

        content = re.sub(r"^WEBVTT[^\n]*\n", "", content)
        blocks = re.split(r"\n{2,}", content.strip())

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            lines = block.split("\n")
            timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if timing_idx < 0:
                continue

            timing_line = lines[timing_idx]
            text_lines = lines[timing_idx + 1:]

            match = re.match(
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*"
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)",
                timing_line,
            )
            if not match:
                continue

            start_str = self._normalize_time(match.group(1))
            end_str = self._normalize_time(match.group(2))

            raw_text = " ".join(text_lines)
            text = re.sub(r"<[^>]+>", "", raw_text)
            text = (
                text.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&nbsp;", " ")
                    .replace("&#39;", "'")
                    .replace("&quot;", '"')
                    .strip()
            )
            text = re.sub(r"\s+", " ", text).strip()

            if not text or text in seen_texts:
                continue

            seen_texts.add(text)
            raw_entries.append({"start": start_str, "end": end_str, "text": text})

        if not raw_entries:
            return []

        entries = []
        for i, entry in enumerate(raw_entries):
            text = entry["text"]
            if len(text) < 2:
                continue
            is_intermediate = False
            for j in range(i + 1, min(i + 4, len(raw_entries))):
                next_text = raw_entries[j]["text"]
                if next_text.startswith(text) and len(next_text) > len(text):
                    is_intermediate = True
                    break
            if not is_intermediate:
                entries.append(entry)

        return entries

    def _parse_srt(self, filepath: str) -> list:
        entries = []
        seen_texts: set = set()

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取 SRT 文件失败: {e}")
            return []

        blocks = re.split(r"\n{2,}", content.strip())

        for block in blocks:
            lines = block.strip().split("\n")
            timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if timing_idx < 0:
                continue

            timing_line = lines[timing_idx]
            text_lines = lines[timing_idx + 1:]

            match = re.match(
                r"(\d{1,2}:\d{2}:\d{2}[.,]\d+)\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d+)",
                timing_line,
            )
            if not match:
                continue

            start_str = self._normalize_time(match.group(1))
            end_str = self._normalize_time(match.group(2))

            text = " ".join(text_lines)
            text = re.sub(r"<[^>]+>", "", text).strip()

            if not text or text in seen_texts:
                continue

            seen_texts.add(text)
            entries.append({"start": start_str, "end": end_str, "text": text})

        return entries

    def _normalize_time(self, time_str: str) -> str:
        time_str = re.sub(r"[.,]\d+$", "", time_str)
        parts = time_str.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{h * 60 + m:02d}:{s:02d}"
        elif len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return f"{m:02d}:{s:02d}"
        return time_str

    def _display_time_to_seconds(self, s: str) -> float:
        parts = (s or "0").split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(parts[0])
        except ValueError:
            return 0.0

    def _entries_to_segments(self, entries: list) -> List[Segment]:
        segs: List[Segment] = []
        for entry in entries:
            text = _to_simplified((entry.get("text") or "").strip())
            if not text:
                continue
            segs.append(Segment(
                text=text,
                start=self._display_time_to_seconds(entry.get("start") or "0"),
                end=self._display_time_to_seconds(entry.get("end") or "0"),
            ))
        return segs

    def _format_subtitle_entries(self, entries: list, language: str) -> str:
        lines = [
            "# Transcription",
            "",
            f"**Detected Language:** {language}",
            "**Language Probability:** 1.00",
            "",
            "## Transcription Content",
            "",
        ]
        for entry in entries:
            lines.append(f"**[{entry['start']} - {entry['end']}]**")
            lines.append("")
            lines.append(_to_simplified(entry["text"]))  # 繁 → 简
            lines.append("")
        return "\n".join(lines)

    async def download_and_convert(
        self,
        url: str,
        output_dir: Path,
        prefetched_title: Optional[str] = None,
        on_progress: Optional[Callable] = None,
    ) -> tuple:
        try:
            output_dir.mkdir(exist_ok=True)
            unique_id = str(uuid.uuid4())[:8]
            output_template = str(output_dir / f"audio_{unique_id}.%(ext)s")

            ydl_opts = self._ydl_opts(url, self.ydl_opts.copy())
            ydl_opts['outtmpl'] = output_template
            if on_progress:
                def _hook(d):
                    status = d.get("status")
                    if status == "downloading":
                        on_progress({
                            "phase": "download",
                            "downloaded_bytes": int(d.get("downloaded_bytes") or 0),
                            "total_bytes": int(
                                d.get("total_bytes")
                                or d.get("total_bytes_estimate")
                                or 0
                            ),
                            "eta": d.get("eta"),
                            "speed": d.get("speed"),
                        })
                    elif status == "finished":
                        on_progress({"phase": "convert", "fraction": 0.95})
                ydl_opts["progress_hooks"] = [_hook]
                ydl_opts["quiet"] = True
                ydl_opts["noprogress"] = True

            logger.info(f"开始下载视频: {url}")

            def _extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, False)

            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            if prefetched_title:
                video_title = prefetched_title
                logger.info(f"复用预取标题，跳过 extract_info: {video_title}")
            else:
                info = await self._ydl_thread(_extract)
                video_title = info.get('title', 'unknown')
                logger.info(f"视频标题: {video_title}")

            await self._ydl_thread(_download)

            audio_file = str(output_dir / f"audio_{unique_id}.m4a")

            if not os.path.exists(audio_file):
                for ext in ['webm', 'mp4', 'mp3', 'wav']:
                    potential_file = str(output_dir / f"audio_{unique_id}.{ext}")
                    if os.path.exists(potential_file):
                        audio_file = potential_file
                        break
                else:
                    raise Exception("未找到下载的音频文件")

            logger.info(f"音频文件已保存: {audio_file}")
            return audio_file, video_title

        except Exception as e:
            clean_err = _clean(str(e))
            logger.error(f"下载视频失败: {clean_err}")
            gated = is_charge_gated(clean_err)
            if not gated:
                gated = await asyncio.to_thread(self._bilibili_is_charge, url)
            if gated:
                if Path(self.COOKIE_FILE).exists():
                    raise Exception(CHARGE_VIDEO_HINT)
                raise Exception(CHARGE_NEED_LOGIN_HINT)
            raise Exception(f"下载视频失败: {clean_err}")
