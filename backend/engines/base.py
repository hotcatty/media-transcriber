"""
Engine-agnostic transcription types and interface.

Deliberately free of Whisper-specific assumptions. Engines may or may not
provide segment timestamps, word timestamps, or language probabilities, and may
be local or remote. Anything the upper layers need — progress, ETA, resume,
paragraph reflow — is derived from the chunk plan rather than from engine
internals, so adding a backend such as SenseVoice or a cloud API is additive.
"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

import numpy as np


# ── audio handed to engines ──────────────────────────────────────────────────

@dataclass
class AudioChunk:
    """
    A slice of the source audio, materialisable either as samples (local
    engines) or as a compact file (cloud engines that take multipart uploads).
    """
    index: int
    start: float
    end: float
    source_path: Path
    work_dir: Path

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_array(self) -> np.ndarray:
        from audio import decode_pcm
        return decode_pcm(self.source_path, self.start, self.duration)

    def as_file(self, suffix: str = ".m4a", bitrate: str = "64k") -> Path:
        """Extract this slice to a small mono 16 kHz file."""
        from audio import FFMPEG

        self.work_dir.mkdir(parents=True, exist_ok=True)
        out = self.work_dir / f"chunk_{self.index:04d}{suffix}"
        if out.exists() and out.stat().st_size > 0:
            return out
        cmd = [
            FFMPEG, "-y", "-nostdin", "-v", "error",
            "-ss", f"{self.start:.3f}", "-i", str(self.source_path),
            "-t", f"{self.duration:.3f}",
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", bitrate, str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"切分音频失败：{(r.stderr or '')[:300]}")
        return out


# ── results ──────────────────────────────────────────────────────────────────

@dataclass
class Word:
    word: str
    start: Optional[float] = None
    end: Optional[float] = None
    probability: Optional[float] = None

    def shifted(self, offset: float) -> "Word":
        return Word(
            self.word,
            None if self.start is None else self.start + offset,
            None if self.end is None else self.end + offset,
            self.probability,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    """
    A recognised span. `start`/`end` are optional: engines without timestamps
    (or configured without them) still produce usable transcripts, and the
    reflow/export layers degrade gracefully.
    """
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    words: List[Word] = field(default_factory=list)
    no_speech_prob: Optional[float] = None
    avg_logprob: Optional[float] = None

    @property
    def has_timing(self) -> bool:
        return self.start is not None and self.end is not None

    def shifted(self, offset: float) -> "Segment":
        return Segment(
            text=self.text,
            start=None if self.start is None else self.start + offset,
            end=None if self.end is None else self.end + offset,
            words=[w.shifted(offset) for w in self.words],
            no_speech_prob=self.no_speech_prob,
            avg_logprob=self.avg_logprob,
        )

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "start": None if self.start is None else round(self.start, 3),
            "end": None if self.end is None else round(self.end, 3),
            "words": [w.to_dict() for w in self.words],
            "no_speech_prob": self.no_speech_prob,
            "avg_logprob": self.avg_logprob,
        }

    @staticmethod
    def from_dict(d: dict) -> "Segment":
        return Segment(
            text=d["text"],
            start=d.get("start"),
            end=d.get("end"),
            words=[Word(**w) for w in (d.get("words") or [])],
            no_speech_prob=d.get("no_speech_prob"),
            avg_logprob=d.get("avg_logprob"),
        )


@dataclass
class ChunkResult:
    segments: List[Segment]
    language: Optional[str] = None


@dataclass
class Capabilities:
    segment_timestamps: bool = True
    word_timestamps: bool = False
    native_punctuation: bool = False   # engine already emits 。，？！
    requires_network: bool = False
    max_chunk_seconds: Optional[float] = None   # cloud upload limits
    languages: str = "multilingual"             # or e.g. "zh,yue,en,ja,ko"


@dataclass
class EngineInfo:
    backend: str          # mlx | faster-whisper | cloud | sensevoice | …
    model: str
    device: str
    precision: str
    display: str


class TranscriptionEngine(ABC):
    """
    Turns audio chunks into segments.

    Chunking, ordering, progress, resume, and cancellation are the pipeline's
    job. An engine only needs to transcribe one chunk at a time and report what
    it can do.
    """

    @property
    @abstractmethod
    def info(self) -> EngineInfo: ...

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities()

    @abstractmethod
    def load(
        self,
        progress: Optional[Callable[[str, int, int], None]] = None,
        cancel: Optional[Any] = None,
    ) -> None:
        """Prepare the engine (fetch weights, validate credentials). Idempotent."""

    @abstractmethod
    def transcribe_chunk(
        self,
        chunk: AudioChunk,
        language: Optional[str] = None,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = False,
    ) -> ChunkResult: ...

    def unload(self) -> None:
        """Free resources. Default: nothing."""


# ── shared decoding hints ────────────────────────────────────────────────────

# Whisper emits almost no punctuation for Mandarin (measured: 0.0–0.1% of
# characters on real material), and sometimes emits traditional characters.
# Priming with a punctuated simplified-Chinese sentence pushes it toward both.
ZH_PROMPT = "以下是普通话的转录内容，请使用简体中文，并保留标准标点符号。"


def prompt_for_language(language: Optional[str]) -> Optional[str]:
    if language and language.lower().startswith("zh"):
        return ZH_PROMPT
    return None


def temperature_chain() -> tuple:
    """
    Short fallback ladder. Extra rungs only fire for segments failing the
    compression-ratio / logprob checks, so this buys hallucination recovery at
    little average cost, while a long ladder mostly wastes time.
    """
    return (0.0, 0.2, 0.4)
