"""
faster-whisper backend — the cross-platform fallback.

CTranslate2 ships no Metal backend, so on Apple Silicon this is CPU-only and one
to two orders of magnitude slower than MLX. It exists so the tool still runs on
Intel Macs, Linux, and Windows, and it is genuinely fast on CUDA hardware.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from .base import (
    AudioChunk,
    Capabilities,
    ChunkResult,
    EngineInfo,
    Segment,
    TranscriptionEngine,
    Word,
    prompt_for_language,
    temperature_chain,
)

logger = logging.getLogger(__name__)


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _pick_device() -> tuple[str, str]:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


class FasterWhisperEngine(TranscriptionEngine):
    def __init__(self, model_size: str, download_root: Optional[Any] = None):
        self.model_size = model_size
        self.download_root = download_root
        self._model = None
        self._device, self._compute = _pick_device()

    @property
    def info(self) -> EngineInfo:
        label = "CUDA GPU" if self._device == "cuda" else "CPU"
        return EngineInfo(
            backend="faster-whisper",
            model=self.model_size,
            device=label,
            precision=self._compute,
            display=f"faster-whisper · {self.model_size} · {label}",
        )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            segment_timestamps=True, word_timestamps=True,
            native_punctuation=False, requires_network=False,
        )

    def load(self, progress: Optional[Callable[[str, int, int], None]] = None,
             cancel: Optional[Any] = None) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        kwargs: dict = {"device": self._device, "compute_type": self._compute}
        if self.download_root:
            kwargs["download_root"] = str(self.download_root)
        if self._device == "cpu":
            kwargs["cpu_threads"] = max(1, os.cpu_count() or 4)

        self._model = WhisperModel(self.model_size, **kwargs)
        logger.info(f"faster-whisper ready: {self.model_size} on {self._device}/{self._compute}")

    def transcribe_chunk(
        self,
        chunk: AudioChunk,
        language: Optional[str] = None,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = False,
    ) -> ChunkResult:
        if self._model is None:
            self.load()

        segments_iter, info = self._model.transcribe(
            chunk.as_array(),
            language=language,
            # Greedy decoding: measured 2.2x faster than beam_size=5 on this
            # workload, with no meaningful accuracy change for Chinese.
            beam_size=1,
            best_of=1,
            temperature=temperature_chain(),
            condition_on_previous_text=condition_on_previous_text,
            initial_prompt=prompt_for_language(language),
            word_timestamps=word_timestamps,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 250},
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
            hallucination_silence_threshold=2.0 if word_timestamps else None,
        )

        # `segments_iter` is lazy: the real inference happens in this loop. That
        # is exactly why it must stay on a worker thread — running it on the
        # event loop was what froze the server and stalled progress at 40%.
        segments = []
        for s in segments_iter:
            text = (s.text or "").strip()
            if not text:
                continue
            words = [
                Word(word=w.word, start=w.start, end=w.end, probability=w.probability)
                for w in (s.words or [])
            ] if word_timestamps else []
            segments.append(Segment(
                text=text, start=float(s.start), end=float(s.end), words=words,
                no_speech_prob=getattr(s, "no_speech_prob", None),
                avg_logprob=getattr(s, "avg_logprob", None),
            ))

        return ChunkResult(
            segments=segments,
            language=getattr(info, "language", None) or language,
        )

    def unload(self) -> None:
        self._model = None
