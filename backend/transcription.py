"""
The transcription pipeline.

Two properties matter here:

1. **Nothing heavy runs on the event loop.** The previous implementation called
   `asyncio.to_thread(model.transcribe)`, but faster-whisper returns a *lazy
   generator*: the call returned in milliseconds and the real inference then ran
   inside `for segment in segments` — on the event loop thread. That froze the
   entire FastAPI server for the length of the job, so SSE stopped, the progress
   bar sat at 40%, and the page looked dead while work was actually happening.
   Here the whole chunk loop lives on a worker thread and only small progress
   events cross back via `loop.call_soon_threadsafe`.

2. **Progress is measured, not guessed.** Audio is chunked on silence, so
   progress is the fraction of audio seconds completed and the ETA comes from
   throughput actually observed on this machine for this file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import re

import audio as audio_utils
import config
import settings_store
from engines import AudioChunk, Segment, TranscriptionEngine
from engines.base import ZH_PROMPT, whisper_initial_prompt
from hotwords import apply_hotword_fixes, extract_hotwords
from text_utils import to_simplified

logger = logging.getLogger(__name__)

# A silent or VAD-empty chunk often comes back as English around 0.35.
# Real speech on this model is typically near 1.0. 0.35 must not win.
LANGUAGE_LOCK_MIN_PROBABILITY = 0.6
_NO_SPEECH = 0.6
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SQUASH_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


class Cancelled(Exception):
    """Raised inside the worker when the caller asks it to stop."""


@dataclass
class Progress:
    stage: str            # planning | downloading_model | transcribing | finalizing
    message: str
    fraction: float       # 0.0–1.0 within the transcription stage
    processed_seconds: float = 0.0
    total_seconds: float = 0.0
    chunk_index: int = 0
    chunk_count: int = 0
    segment_count: int = 0
    speed_x: Optional[float] = None       # multiples of realtime
    eta_seconds: Optional[float] = None
    preview: Optional[str] = None
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "message": self.message,
            "fraction": round(self.fraction, 4),
            "processed_seconds": round(self.processed_seconds, 1),
            "total_seconds": round(self.total_seconds, 1),
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "segment_count": self.segment_count,
            "speed_x": round(self.speed_x, 2) if self.speed_x else None,
            "eta_seconds": int(self.eta_seconds) if self.eta_seconds else None,
            "preview": self.preview,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
        }


@dataclass
class TranscriptResult:
    segments: List[Segment]
    language: Optional[str]
    duration: float
    engine_display: str
    engine_backend: str
    elapsed_seconds: float
    speed_x: Optional[float] = None
    resumed_seconds: float = 0.0
    engine_punctuates: bool = False


# ── partial-result sidecar (enables resume) ──────────────────────────────────

def _load_partial(path: Path) -> tuple[List[Segment], set, Optional[str]]:
    if not path.exists():
        return [], set(), None
    segments: List[Segment] = []
    done: set = set()
    language: Optional[str] = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            done.add(rec["chunk"])
            language = rec.get("language") or language
            segments.extend(Segment.from_dict(s) for s in rec.get("segments", []))
    except Exception as e:
        # A sidecar truncated by a kill must not poison the task.
        logger.warning(f"partial results unreadable, starting over: {e}")
        return [], set(), None
    segments.sort(key=lambda s: (s.start if s.start is not None else 0.0))
    return segments, done, language


def _append_partial(path: Path, chunk_index: int, segments: List[Segment],
                    language: Optional[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "chunk": chunk_index,
            "language": language,
            "segments": [s.to_dict() for s in segments],
        }, ensure_ascii=False) + "\n")
        f.flush()


# ── helpers ──────────────────────────────────────────────────────────────────

def _force_simplified(segments: List[Segment], language: Optional[str]) -> None:
    """Whisper emits traditional characters for some speakers; normalise."""
    if not (language or "").lower().startswith("zh"):
        return
    for s in segments:
        s.text = to_simplified(s.text)
        for w in s.words:
            w.word = to_simplified(w.word)


def _apply_hotwords(
    segments: List[Segment],
    title: Optional[str],
    source_context: Optional[str],
) -> None:
    words = extract_hotwords(title=title or "", source_context=source_context or "")
    if not words:
        return
    for s in segments:
        s.text = apply_hotword_fixes(s.text, words)
        for w in s.words:
            w.word = apply_hotword_fixes(w.word, words)


def looks_chinese_context(title: Optional[str], source_context: Optional[str]) -> bool:
    """True when the page title or notes are written in Chinese."""
    blob = f"{title or ''}\n{source_context or ''}"
    return len(_CJK_RE.findall(blob)) >= 2


def _squash(text: str) -> str:
    return _SQUASH_RE.sub("", text or "")


def _echo_pieces(prompt: Optional[str], hotwords: List[str]) -> list:
    pieces = []
    for raw in (prompt, ZH_PROMPT, "专名", *hotwords):
        piece = _squash(raw or "")
        if len(piece) >= 2 and piece not in pieces:
            pieces.append(piece)
    pieces.sort(key=len, reverse=True)
    return pieces


def is_prompt_dump(text: str, pieces: list) -> bool:
    """True when the segment is just the hotword prompt, repeated or not."""
    leftover = _squash(text)
    if not leftover:
        return True
    for piece in pieces:
        if piece:
            leftover = leftover.replace(piece, "")
    return len(leftover) <= 1


def without_prompt_dumps(segments: List[Segment], pieces: list) -> List[Segment]:
    return [s for s in segments if not is_prompt_dump(s.text, pieces)]


def has_usable_speech(segments: List[Segment]) -> bool:
    """False for an empty chunk or one Whisper itself marked as no speech."""
    if not segments:
        return False
    if all(
        s.no_speech_prob is not None and s.no_speech_prob >= _NO_SPEECH
        for s in segments
    ):
        return False
    return True


def _language_probability(value) -> Optional[float]:
    if value is None:
        return None
    try:
        prob = float(value)
    except (TypeError, ValueError):
        return None
    if prob < 0.0 or prob > 1.0:
        return None
    return prob


def _preview_of(segments: List[Segment], chars: int = 90) -> Optional[str]:
    if not segments:
        return None
    tail = "".join(s.text for s in segments[-4:]).strip()
    return tail[-chars:] if tail else None


async def run_transcription(
    audio_path: str | Path,
    *,
    engine: TranscriptionEngine,
    language: Optional[str] = None,
    work_dir: Optional[Path] = None,
    partial_path: Optional[Path] = None,
    cancel_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[Progress], None]] = None,
    word_timestamps: Optional[bool] = None,
    condition_on_previous_text: Optional[bool] = None,
    source_context: Optional[str] = None,
    title: Optional[str] = None,
) -> TranscriptResult:
    """
    Transcribe a file, reporting progress as it goes.

    `on_progress` runs on the event loop, so it may touch shared state and
    schedule I/O. Pass `partial_path` to make the job resumable.
    """
    loop = asyncio.get_running_loop()
    audio_path = Path(audio_path)
    cancel_event = cancel_event or threading.Event()
    caps = engine.capabilities

    if word_timestamps is None:
        word_timestamps = config.WORD_TIMESTAMPS
    word_timestamps = bool(word_timestamps and caps.word_timestamps)
    if condition_on_previous_text is None:
        condition_on_previous_text = config.CONDITION_ON_PREVIOUS_TEXT

    chunk_dir = Path(work_dir) if work_dir else (config.TEMP_DIR / "chunks" / audio_path.stem)
    events: asyncio.Queue = asyncio.Queue()
    DONE, ERR = "__done__", "__err__"

    def emit(p: Progress) -> None:
        loop.call_soon_threadsafe(events.put_nowait, ("progress", p))

    def worker() -> TranscriptResult:
        if cancel_event.is_set():
            raise Cancelled()

        duration = audio_utils.probe_duration(audio_path)

        emit(Progress("planning", "正在分析音频结构…", 0.0, total_seconds=duration))

        target = config.CHUNK_TARGET_SECONDS
        if caps.max_chunk_seconds:
            target = min(target, caps.max_chunk_seconds)
        plan = audio_utils.plan_chunks(
            audio_path, duration, target=target,
            search_window=config.CHUNK_SEARCH_WINDOW,
            min_chunk=config.MIN_CHUNK_SECONDS,
        )
        chunks = [
            AudioChunk(index=c.index, start=c.start, end=c.end,
                       source_path=audio_path, work_dir=chunk_dir)
            for c in plan
        ]

        def dl_progress(filename: str, done: int, total: int) -> None:
            emit(Progress(
                "downloading_model",
                f"首次运行，正在下载语音模型（{done / 1e6:.0f}/{total / 1e6:.0f} MB）",
                0.0, total_seconds=duration,
                downloaded_bytes=done, total_bytes=total,
            ))

        engine.load(progress=dl_progress)
        if cancel_event.is_set():
            raise Cancelled()

        all_segments, done_chunks, prior_lang = (
            _load_partial(partial_path) if partial_path else ([], set(), None)
        )
        # `language` is an explicit request (only zh is passed in today).
        # A resumed "en" from a silent first chunk must not stick when the
        # page itself is Chinese.
        forced = (language or "").strip() or None
        chinese = looks_chinese_context(title, source_context)
        locked = forced
        if not locked and prior_lang:
            if chinese and prior_lang.lower().startswith("en"):
                locked = None
            else:
                locked = prior_lang
        hotwords = extract_hotwords(title=title or "", source_context=source_context or "")
        resumed = sum(c.duration for c in chunks if c.index in done_chunks)
        if done_chunks:
            logger.info(f"resuming: {len(done_chunks)}/{len(chunks)} chunks done "
                        f"({resumed / 60:.1f} min)")

        started = time.time()
        processed = resumed
        fresh = 0.0

        guess_speed = settings_store.whisper_speed()
        remain0 = max((duration or 0.0) - processed, 0.0)
        emit(Progress(
            "transcribing",
            "开始转录…" if not done_chunks else f"继续上次进度（已完成 {resumed / 60:.0f} 分钟）…",
            (processed / duration) if duration else 0.0,
            processed_seconds=processed, total_seconds=duration,
            chunk_count=len(chunks), segment_count=len(all_segments),
            speed_x=guess_speed,
            eta_seconds=(remain0 / guess_speed) if remain0 else None,
        ))

        chunk_t0 = [time.time()]
        stop_tick = threading.Event()

        def tick_loop() -> None:
            while not stop_tick.wait(4.0):
                if cancel_event.is_set():
                    return
                elapsed = max(time.time() - started, 0.01)
                speed = (fresh / elapsed) if fresh > 2 else guess_speed
                remain_audio = max((duration or 0.0) - processed, 0.0)
                elapsed_c = max(time.time() - chunk_t0[0], 0.0)
                cur_chunk = min(remain_audio, config.CHUNK_TARGET_SECONDS) if remain_audio else 0.0
                later = max(remain_audio - cur_chunk, 0.0)
                this_wall = max(cur_chunk / max(speed, 0.1) - elapsed_c, 1.5)
                later_wall = later / max(speed, 0.1)
                remain_wall = this_wall + later_wall if remain_audio else 0.0
                done_s = processed + min(cur_chunk, speed * elapsed_c)
                if duration:
                    done_s = min(done_s, max(duration - 0.05, 0.0))
                emit(Progress(
                    "transcribing",
                    f"正在转录 {done_s / 60:.0f}/{(duration or 0) / 60:.0f} 分钟" if duration else "正在转录…",
                    (done_s / duration) if duration else 0.0,
                    processed_seconds=done_s, total_seconds=duration,
                    chunk_count=len(chunks), segment_count=len(all_segments),
                    speed_x=speed,
                    eta_seconds=remain_wall if remain_audio else None,
                ))

        threading.Thread(target=tick_loop, name="eta-tick", daemon=True).start()
        try:
            for chunk in chunks:
                if cancel_event.is_set():
                    raise Cancelled()
                if chunk.index in done_chunks:
                    continue
                chunk_t0[0] = time.time()

                def _decode(lang: Optional[str]):
                    prompt = whisper_initial_prompt(
                        lang, source_context=source_context, title=title,
                    )
                    decoded = engine.transcribe_chunk(
                        chunk,
                        language=lang,
                        word_timestamps=word_timestamps,
                        condition_on_previous_text=condition_on_previous_text,
                        initial_prompt=prompt,
                    )
                    pieces = _echo_pieces(prompt, hotwords)
                    kept_segs = without_prompt_dumps(decoded.segments, pieces)
                    return decoded, kept_segs

                result, kept = _decode(locked)
                speech = has_usable_speech(kept)
                if locked is None:
                    cand = (result.language or "").strip() or None
                    prob = _language_probability(result.language_probability)
                    confident = bool(
                        speech and cand and (
                            prob is None or prob >= LANGUAGE_LOCK_MIN_PROBABILITY
                        )
                    )
                    # Chinese page + low-confidence English is the live-replay
                    # failure (Detected language 'en' with probability 0.35).
                    # Redo this chunk in Chinese. A confident English detection
                    # is left alone, including when the title is Chinese.
                    low_en = bool(
                        speech
                        and chinese
                        and cand
                        and cand.lower().startswith("en")
                        and prob is not None
                        and prob < LANGUAGE_LOCK_MIN_PROBABILITY
                    )
                    if not speech:
                        logger.info(
                            "not locking language %s prob=%s: chunk has no usable speech",
                            cand, prob,
                        )
                        kept = []
                    elif low_en:
                        logger.info(
                            "ignoring low-confidence en (%.2f) on a Chinese page",
                            prob,
                        )
                        result, kept = _decode("zh")
                        if has_usable_speech(kept):
                            locked = "zh"
                        else:
                            kept = []
                    elif speech and cand and cand.lower().startswith("zh") and chinese:
                        locked = "zh"
                    elif confident:
                        # Missing probability plus Chinese context must not
                        # freeze English before a later chunk can be heard.
                        if (
                            prob is None
                            and chinese
                            and cand.lower().startswith("en")
                        ):
                            pass
                        else:
                            locked = cand
                    # Low confidence without a Chinese page: keep this chunk's
                    # text, but do not freeze the language for the rest.

                shifted = [s.shifted(chunk.start) for s in kept]
                _force_simplified(shifted, locked or result.language)
                _apply_hotwords(shifted, title, source_context)
                all_segments.extend(shifted)

                if partial_path:
                    _append_partial(partial_path, chunk.index, shifted, locked)

                processed += chunk.duration
                fresh += chunk.duration
                elapsed = max(time.time() - started, 0.01)
                speed = fresh / elapsed
                eta = (max(duration - processed, 0.0) / speed) if speed > 0 else None

                emit(Progress(
                    "transcribing",
                    f"正在转录 {processed / 60:.0f}/{duration / 60:.0f} 分钟",
                    (processed / duration) if duration else 1.0,
                    processed_seconds=processed, total_seconds=duration,
                    chunk_index=chunk.index + 1, chunk_count=len(chunks),
                    segment_count=len(all_segments),
                    speed_x=speed, eta_seconds=eta,
                    preview=_preview_of(all_segments),
                ))
        finally:
            stop_tick.set()

        all_segments.sort(key=lambda s: (s.start if s.start is not None else 0.0))
        total_elapsed = time.time() - started
        speed_x = (fresh / total_elapsed) if total_elapsed > 0 and fresh else None

        shutil.rmtree(chunk_dir, ignore_errors=True)

        return TranscriptResult(
            segments=all_segments,
            language=locked,
            duration=duration,
            engine_display=engine.info.display,
            engine_backend=engine.info.backend,
            elapsed_seconds=total_elapsed,
            speed_x=speed_x,
            resumed_seconds=resumed,
            engine_punctuates=caps.native_punctuation,
        )

    def thread_main() -> None:
        try:
            loop.call_soon_threadsafe(events.put_nowait, (DONE, worker()))
        except BaseException as e:  # noqa: BLE001 — forwarded to the awaiting caller
            loop.call_soon_threadsafe(events.put_nowait, (ERR, e))

    threading.Thread(target=thread_main, name="transcribe", daemon=True).start()

    while True:
        kind, payload = await events.get()
        if kind == "progress":
            if on_progress:
                try:
                    maybe = on_progress(payload)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception as e:
                    logger.warning(f"progress callback failed: {e}")
        elif kind == DONE:
            return payload
        else:
            raise payload
