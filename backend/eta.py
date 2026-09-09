"""Step countdown estimates.

Platforms often omit a remaining-time field (HLS, ffmpeg pulling a URL, first
Whisper chunk). We always start from a prior, then blend in whatever we do
measure. The number is for expectation, not a SLA: it may stall in the last
few seconds rather than hit 0 while work is still running.
"""
from __future__ import annotations

import time
from typing import Optional

STALL_SECONDS = 8.0

# 64 kbps AAC mono @ 16 kHz — matches the local transcode target.
AAC_BYTES_PER_SEC = 8000


def format_remain(seconds: Optional[float]) -> str:
    """Figma copy: 剩余约30s / 剩余约5分钟 / 即将完成."""
    if seconds is None:
        return ""
    s = int(round(max(0.0, float(seconds))))
    if s < STALL_SECONDS:
        return "即将完成"
    if s < 90:
        return f"剩余约{s}s"
    return f"剩余约{max(1, int(round(s / 60)))}分钟"


def stamp(remain: Optional[float], now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    if remain is None:
        return {"eta_seconds": None, "eta_at": None, "eta_deadline": None}
    remain = max(0.0, float(remain))
    return {
        "eta_seconds": remain,
        "eta_at": now,
        "eta_deadline": now + remain,
    }


def remain_from_detail(detail: Optional[dict], now: Optional[float] = None) -> Optional[float]:
    if not detail:
        return None
    now = time.time() if now is None else now
    deadline = detail.get("eta_deadline")
    if deadline is not None:
        return max(0.0, float(deadline) - now)
    raw = detail.get("eta_seconds")
    if raw is None:
        return None
    remain = float(raw)
    at = detail.get("eta_at")
    if at:
        remain -= now - float(at)
    return max(0.0, remain)


def prior_parse() -> float:
    return 12.0


def prior_model() -> float:
    return 120.0


def prior_finalize() -> float:
    return 2.0


def prior_download(duration: Optional[float], kind: str = "video") -> float:
    """Wall-clock guess before any byte/speed sample.

    Podcast path is ffmpeg reading a CDN URL while transcoding to 16 kHz AAC,
    so network and encode overlap — encode (~25–40×) usually wins.
    Bilibili/YouTube go through yt-dlp (HLS, throttling) and are slower.
    """
    d = float(duration or 120.0)
    if kind == "podcast":
        return max(12.0, d / 28.0)
    # yt-dlp often finishes in a few seconds when the CDN is warm.
    return max(8.0, d / 45.0)


def prior_transcribe(
    duration: Optional[float],
    processed: float = 0.0,
    speed_x: Optional[float] = None,
    default_speed: float = 2.85,
) -> float:
    if not duration:
        return 90.0
    remain_audio = max(float(duration) - float(processed or 0.0), 0.0)
    speed = float(speed_x or default_speed)
    return remain_audio / max(speed, 0.1)


def total_wait(
    duration: Optional[float],
    kind: str = "video",
    origin: Optional[str] = None,
    whisper_speed: float = 2.85,
    include_download: bool = True,
) -> Optional[float]:
    """Frozen whole-job prior once duration is known. Dominant cost is Whisper."""
    if origin == "subtitle":
        return 20.0
    if not duration:
        return None
    wait = prior_transcribe(duration, 0.0, None, whisper_speed) + prior_finalize()
    if include_download:
        wait += prior_download(duration, kind)
    return wait


def trailing(
    stage: str,
    duration: Optional[float],
    kind: str = "video",
    origin: Optional[str] = None,
    processed: float = 0.0,
    speed_x: Optional[float] = None,
    whisper_speed: float = 2.85,
) -> float:
    """Priors for steps still ahead of `stage`, so ETA is job remaining."""
    if origin == "subtitle":
        return 0.0 if stage in ("finalizing", "completed", "subtitles") else prior_finalize()
    transcribe = prior_transcribe(duration, processed, speed_x, whisper_speed)
    download = prior_download(duration, kind)
    fin = prior_finalize()
    if stage in ("downloading", "converting"):
        return transcribe + fin
    if stage in ("transcribing", "planning"):
        # Whisper's own remaining already covers the job; finalize is seconds.
        return 0.0
    if stage in ("finalizing", "completed", "subtitles"):
        return 0.0
    return download + transcribe + fin


def budget_remain(detail: Optional[dict], now: Optional[float] = None) -> Optional[float]:
    if not detail or detail.get("eta_budget") is None or detail.get("eta_budget_at") is None:
        return None
    now = time.time() if now is None else now
    return max(0.0, float(detail["eta_budget"]) - (now - float(detail["eta_budget_at"])))


def expected_aac_bytes(duration: Optional[float]) -> Optional[int]:
    if not duration:
        return None
    return max(32_000, int(float(duration) * AAC_BYTES_PER_SEC))


def from_bytes(done: int, total: int, elapsed: float) -> Optional[float]:
    if total <= 0 or done <= 0 or elapsed < 0.4:
        return None
    speed = done / elapsed
    if speed < 1024:
        return None
    return max(0.0, (total - done) / speed)


def from_rate(done: int, total: int, rate: float) -> Optional[float]:
    if total <= 0 or rate < 1024:
        return None
    return max(0.0, (total - done) / rate)


def smooth(prev: Optional[float], measured: Optional[float], dt: float) -> Optional[float]:
    """Countdown that only goes down.

    Ahead of schedule: ease toward the better number. Behind: keep ticking
    down in real time, never jump up toward a worse sample.
    """
    if measured is None:
        if prev is None:
            return None
        return max(0.0, float(prev) - max(dt, 0.0))
    measured = max(0.0, float(measured))
    if prev is None:
        return measured
    decayed = max(float(prev) - max(dt, 0.0), 0.0)
    if measured < decayed:
        return decayed * 0.35 + measured * 0.65
    return decayed
