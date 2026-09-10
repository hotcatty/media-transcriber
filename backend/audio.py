"""
Audio utilities built directly on ffmpeg/ffprobe.

Long recordings are split into chunks so that the transcription pipeline can
report genuine progress, resume after an interruption, and keep peak memory
bounded. Splits are placed inside detected silences so we never cut a word in
half.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

_HELPER_BIN = Path.home() / "Library" / "Application Support" / "media-transcriber" / "bin"


def find_binary(name: str) -> str:
    env_key = {"ffmpeg": "MT_FFMPEG", "ffprobe": "MT_FFPROBE"}.get(name, "")
    env_path = os.getenv(env_key) if env_key else None
    for candidate in (
        env_path or "",
        str(_HELPER_BIN / name),
        f"/opt/homebrew/bin/{name}",   # Apple Silicon Homebrew
        f"/usr/local/bin/{name}",      # Intel Homebrew
        shutil.which(name) or "",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return name


FFMPEG = find_binary("ffmpeg")
FFPROBE = find_binary("ffprobe")


class FFmpegMissing(RuntimeError):
    pass


def check_ffmpeg() -> Optional[str]:
    """Return a human-readable install hint if ffmpeg is unusable, else None."""
    try:
        subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=10, check=True)
        return None
    except Exception:
        return "未检测到 ffmpeg。macOS 请执行：brew install ffmpeg；Debian/Ubuntu：sudo apt install ffmpeg"


def probe_duration(path: str | Path, timeout: float = 120) -> float:
    """Duration in seconds. Raises if the file (or URL) is not decodable."""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=timeout,
    )
    raw = (r.stdout or "").strip()
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"无法读取音频时长：{(r.stderr or raw)[:300]}")


def decode_pcm(path: str | Path, start: float = 0.0,
               duration: Optional[float] = None) -> np.ndarray:
    """
    Decode a slice to mono float32 at 16 kHz — the format every Whisper
    implementation expects. Memory stays proportional to the slice, not the file.
    """
    cmd = [FFMPEG, "-nostdin", "-threads", "0", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "f32le", "-"]

    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"音频解码失败：{(r.stderr or b'').decode(errors='replace')[:300]}")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


def detect_silences(path: str | Path, noise_db: int = -32,
                    min_silence: float = 0.45) -> List[tuple[float, float]]:
    """Return (start, end) silence intervals via ffmpeg's silencedetect."""
    cmd = [
        FFMPEG, "-nostdin", "-v", "info", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        logger.warning("silencedetect timed out; falling back to fixed-size chunks")
        return []

    silences: List[tuple[float, float]] = []
    pending_start: Optional[float] = None
    for kind, value in _SILENCE_RE.findall(r.stderr or ""):
        v = float(value)
        if kind == "start":
            pending_start = v
        elif pending_start is not None:
            silences.append((pending_start, v))
            pending_start = None
    return silences


@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_chunks(path: str | Path, total_duration: float, target: float = 480.0,
                search_window: float = 60.0, min_chunk: float = 120.0) -> List[Chunk]:
    """
    Build a chunk plan whose boundaries sit in silence where possible.

    Audio shorter than `target + min_chunk` stays a single chunk so short clips
    take the simplest path.
    """
    if total_duration <= target + min_chunk:
        return [Chunk(0, 0.0, total_duration)]

    silences = detect_silences(path)
    # Prefer the middle of a silence, and prefer longer silences when several
    # sit near the same target boundary.
    candidates = sorted(
        ((s + e) / 2.0, e - s) for s, e in silences if e - s >= 0.45
    )

    boundaries: List[float] = [0.0]
    cursor = 0.0
    while total_duration - cursor > target + min_chunk:
        ideal = cursor + target
        lo, hi = ideal - search_window, ideal + search_window
        in_window = [(mid, dur) for mid, dur in candidates if lo <= mid <= hi and mid > cursor + min_chunk]
        if in_window:
            # Longest silence wins; ties break toward the one closest to ideal.
            best = max(in_window, key=lambda t: (round(t[1], 2), -abs(t[0] - ideal)))
            cut = best[0]
        else:
            cut = ideal  # no silence nearby: accept a hard cut
        boundaries.append(cut)
        cursor = cut
    boundaries.append(total_duration)

    return [
        Chunk(i, boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
        if boundaries[i + 1] - boundaries[i] > 0.05
    ]


def transcode_to_m4a(src: str | Path, dest: str | Path) -> str:
    """Normalise arbitrary media to mono 16 kHz m4a (small, Whisper-ready)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-nostdin", "-v", "error", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(dest),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"音频转码失败：{(r.stderr or '')[:400]}")
    return str(dest)


def download_audio_url_to_m4a(url: str, dest: str | Path,
                              user_agent: Optional[str] = None) -> str:
    """Fetch a direct audio URL and normalise it in one ffmpeg pass."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-nostdin", "-v", "error"]
    if user_agent:
        cmd += ["-user_agent", user_agent]
    cmd += [
        "-i", url, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(dest),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"音频下载失败：{(r.stderr or '')[:400]}")
    return str(dest)
