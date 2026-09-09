"""
MLX Whisper backend — runs on the Apple GPU via Metal.

Default on Apple Silicon. CTranslate2 (faster-whisper) has no Metal backend, so
on a Mac it is CPU-bound: `medium` was measured at 0.78x realtime with the old
settings, meaning a 98-minute recording needed over two hours. MLX moves the
same work onto the GPU, which is what makes large-v3-turbo viable — and turbo is
necessary, because `base` produced unusable Chinese (审美→神媒, 认知→任志).
"""
from __future__ import annotations

import logging
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
        import mlx.core  # noqa: F401
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


class MLXWhisperEngine(TranscriptionEngine):
    def __init__(self, model_repo: str, models_dir: Optional[Any] = None):
        self.model_repo = model_repo
        self.models_dir = models_dir
        self._local_path: Optional[str] = None
        self._loaded = False

    @property
    def info(self) -> EngineInfo:
        short = self.model_repo.split("/")[-1].replace("whisper-", "")
        return EngineInfo(
            backend="mlx",
            model=self.model_repo,
            device="Apple GPU (Metal)",
            precision="float16",
            display=f"MLX · {short} · Apple GPU",
        )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            segment_timestamps=True,
            word_timestamps=True,
            native_punctuation=False,   # Mandarin output is effectively unpunctuated
            requires_network=False,
        )

    def load(self, progress: Optional[Callable[[str, int, int], None]] = None,
             cancel: Optional[Any] = None) -> None:
        if self._loaded:
            return
        import numpy as np

        import config
        from model_fetch import ensure_mlx_model

        target_dir = self.models_dir or config.MODELS_DIR
        path = ensure_mlx_model(
            self.model_repo, models_dir=target_dir, progress=progress, cancel=cancel,
        )
        self._local_path = str(path)

        # Warm up so Metal kernel compilation lands here rather than inside the
        # first user-visible chunk, keeping the reported ETA honest from the start.
        import mlx_whisper

        mlx_whisper.transcribe(
            np.zeros(16000, dtype=np.float32),
            path_or_hf_repo=self._local_path,
            language="zh", temperature=0.0, verbose=None,
        )
        self._loaded = True
        logger.info(f"MLX model ready: {self.model_repo}")

    def transcribe_chunk(
        self,
        chunk: AudioChunk,
        language: Optional[str] = None,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = False,
    ) -> ChunkResult:
        if not self._loaded:
            self.load()
        import mlx_whisper

        result = mlx_whisper.transcribe(
            chunk.as_array(),
            path_or_hf_repo=self._local_path,
            language=language,
            temperature=temperature_chain(),
            condition_on_previous_text=condition_on_previous_text,
            initial_prompt=prompt_for_language(language),
            word_timestamps=word_timestamps,
            compression_ratio_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            # Suppresses the "thanks for watching" style loops Whisper invents
            # over music and silence. Only effective with word timestamps.
            hallucination_silence_threshold=2.0 if word_timestamps else None,
            verbose=None,
        )

        segments = []
        for s in result.get("segments", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            words = [
                Word(word=w["word"], start=w.get("start"), end=w.get("end"),
                     probability=w.get("probability"))
                for w in (s.get("words") or [])
            ]
            segments.append(Segment(
                text=text, start=float(s["start"]), end=float(s["end"]), words=words,
                no_speech_prob=s.get("no_speech_prob"),
                avg_logprob=s.get("avg_logprob"),
            ))

        return ChunkResult(segments=segments, language=result.get("language") or language)
