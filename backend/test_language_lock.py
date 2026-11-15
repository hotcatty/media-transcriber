"""Language lock: a silent first chunk must not freeze English.

These tests stub the engine. They do not load Whisper.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import transcription
from audio import Chunk
from engines import Segment, TranscriptionEngine
from engines.base import Capabilities, ChunkResult, EngineInfo
from transcription import (
    LANGUAGE_LOCK_MIN_PROBABILITY,
    is_prompt_dump,
    looks_chinese_context,
    run_transcription,
)

TITLE = "【直播回放】TLW的直播间 2026年11月13日20点场"
CONTEXT = "节目：知行合一的TLW"
PROMPT_LINE = "专名：知行合一的TLW"


class _Engine(TranscriptionEngine):
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    @property
    def info(self) -> EngineInfo:
        return EngineInfo("fake", "small", "cpu", "int8", "fake")

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities()

    def load(self, progress=None, cancel=None) -> None:
        return None

    def transcribe_chunk(self, chunk, language=None, word_timestamps=False,
                         condition_on_previous_text=False, initial_prompt=None):
        self.calls.append({"language": language, "prompt": initial_prompt})
        if not self._results:
            raise AssertionError("engine ran out of scripted chunks")
        return self._results.pop(0)


def _seg(text: str, no_speech: float = 0.1) -> Segment:
    return Segment(text=text, start=0.0, end=1.0, no_speech_prob=no_speech)


def _run(engine, chunks, title=TITLE, context=CONTEXT):
    transcription.audio_utils.probe_duration = lambda *_a, **_k: chunks[-1].end
    transcription.audio_utils.plan_chunks = lambda *_a, **_k: chunks
    transcription.settings_store.whisper_speed = lambda: 3.0
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "clip.m4a"
        audio.write_bytes(b"not-audio")
        return asyncio.run(run_transcription(
            audio,
            engine=engine,
            language=None,
            work_dir=Path(tmp) / "work",
            title=title,
            source_context=context,
        ))


class LanguageLockTests(unittest.TestCase):
    def test_threshold_rejects_the_reported_probability(self):
        self.assertLess(0.35, LANGUAGE_LOCK_MIN_PROBABILITY)
        self.assertGreaterEqual(0.93, LANGUAGE_LOCK_MIN_PROBABILITY)
        self.assertTrue(looks_chinese_context(TITLE, CONTEXT))
        self.assertFalse(looks_chinese_context("Weekly hardware notes", ""))

    def test_prompt_repetition_is_a_dump(self):
        pieces = ["专名知行合一的TLW", "知行合一的TLW", "专名"]
        self.assertTrue(is_prompt_dump(PROMPT_LINE * 4, pieces))
        self.assertFalse(is_prompt_dump("今天聊聊知行合一的TLW这个节目怎么做", pieces))

    def test_silent_first_chunk_does_not_lock_english(self):
        engine = _Engine([
            ChunkResult(segments=[], language="en", language_probability=0.35),
            ChunkResult(
                segments=[_seg("你还在做技术吗，除了电脑不用买硬件")],
                language="zh",
                language_probability=1.0,
            ),
        ])
        result = _run(engine, [Chunk(0, 0.0, 480.0), Chunk(1, 480.0, 960.0)])
        self.assertEqual([c["language"] for c in engine.calls], [None, None])
        self.assertTrue((result.language or "").lower().startswith("zh"))
        text = "".join(s.text for s in result.segments)
        self.assertIn("电脑", text)
        self.assertNotIn("专名", text)

    def test_prompt_only_first_chunk_is_dropped(self):
        engine = _Engine([
            ChunkResult(
                segments=[_seg(PROMPT_LINE, 0.2), _seg(PROMPT_LINE + PROMPT_LINE, 0.2)],
                language="en",
                language_probability=0.35,
            ),
            ChunkResult(
                segments=[_seg("后面这段是中文口播")],
                language="zh",
                language_probability=0.99,
            ),
        ])
        result = _run(engine, [Chunk(0, 0.0, 480.0), Chunk(1, 480.0, 960.0)])
        self.assertEqual(engine.calls[1]["language"], None)
        self.assertTrue((result.language or "").lower().startswith("zh"))
        text = "".join(s.text for s in result.segments)
        self.assertNotIn("专名", text)
        self.assertIn("中文口播", text)

    def test_low_confidence_english_on_chinese_page_is_redone(self):
        engine = _Engine([
            ChunkResult(
                segments=[_seg("Are you still in the technology?")],
                language="en",
                language_probability=0.35,
            ),
            ChunkResult(
                segments=[_seg("你还在做技术吗")],
                language="zh",
                language_probability=1.0,
            ),
        ])
        result = _run(engine, [Chunk(0, 0.0, 30.0)])
        self.assertEqual([c["language"] for c in engine.calls], [None, "zh"])
        self.assertTrue((result.language or "").lower().startswith("zh"))
        text = "".join(s.text for s in result.segments)
        self.assertIn("技术", text)
        self.assertNotIn("technology", text.lower())
        self.assertNotIn("专名", text)

    def test_confident_english_is_not_forced_to_chinese(self):
        english = "You do not need any hardware besides a computer."
        engine = _Engine([
            ChunkResult(
                segments=[_seg(english)],
                language="en",
                language_probability=0.93,
            ),
        ])
        result = _run(engine, [Chunk(0, 0.0, 30.0)])
        self.assertEqual([c["language"] for c in engine.calls], [None])
        self.assertTrue((result.language or "").lower().startswith("en"))
        text = "".join(s.text for s in result.segments)
        self.assertIn("hardware", text)
        self.assertNotIn("专名", text)

    def test_low_confidence_english_without_chinese_page_stays_english_text(self):
        english = "Still talking about hardware."
        engine = _Engine([
            ChunkResult(
                segments=[_seg(english)],
                language="en",
                language_probability=0.35,
            ),
        ])
        result = _run(
            engine,
            [Chunk(0, 0.0, 20.0)],
            title="Weekly hardware notes",
            context="",
        )
        self.assertEqual([c["language"] for c in engine.calls], [None])
        self.assertFalse((result.language or "").lower().startswith("zh"))
        self.assertFalse((result.language or "").lower().startswith("en"))
        text = "".join(s.text for s in result.segments)
        self.assertIn("hardware", text)


if __name__ == "__main__":
    unittest.main()
