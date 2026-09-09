"""
Export formats.

The primary consumer of these transcripts is a language model, not a reader
scrolling a page, so the default text and Markdown outputs carry reflowed
paragraphs and no inline timestamps. SRT/VTT remain available for subtitle use,
and a timestamped Markdown variant is there when someone needs to cite a moment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from engines import Segment
from reflow import ReflowOptions, reflow_segments
from text_utils import format_timestamp, humanize_duration, is_cjk_text

# Subtitle cues longer than this get split on word boundaries to stay readable.
MAX_CUE_CHARS_CJK = 20
MAX_CUE_CHARS_LATIN = 44
MAX_CUE_SECONDS = 6.0


@dataclass
class TranscriptMeta:
    title: str
    source: str
    language: Optional[str] = None
    duration: Optional[float] = None
    engine: Optional[str] = None
    created_at: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    engine_punctuates: bool = False
    paragraph_min_chars: int = 120
    paragraph_max_chars: int = 300


def _reflow(segments: List[Segment], meta: TranscriptMeta):
    return reflow_segments(
        segments,
        options=ReflowOptions(
            target_min_chars=meta.paragraph_min_chars,
            target_max_chars=meta.paragraph_max_chars,
        ),
        engine_punctuates=meta.engine_punctuates,
    )


# ── text & markdown ──────────────────────────────────────────────────────────

def to_plain_text(segments: List[Segment], meta: TranscriptMeta) -> str:
    """Bare transcript — nothing but the words. Ideal for pasting into a model."""
    return _reflow(segments, meta).text + "\n"


def to_markdown(segments: List[Segment], meta: TranscriptMeta) -> str:
    """
    Reflowed paragraphs under a compact metadata header, so a model receives the
    provenance (what this is, where it came from, how long) alongside the text.
    """
    result = _reflow(segments, meta)

    facts = []
    if meta.duration:
        facts.append(f"- 时长：{humanize_duration(meta.duration)}")
    if meta.language:
        facts.append(f"- 语言：{meta.language}")
    facts.append(f"- 来源：{meta.source}")
    if meta.engine:
        facts.append(f"- 转录引擎：{meta.engine}")
    if meta.created_at:
        facts.append(f"- 转录时间：{meta.created_at}")

    return "\n".join([
        f"# {meta.title}", "",
        *facts, "",
        "## 正文", "",
        result.text, "",
    ])


def to_markdown_timestamped(segments: List[Segment], meta: TranscriptMeta) -> str:
    lines = [f"# {meta.title}", "", f"来源：{meta.source}", "", "## 正文（带时间戳）", ""]
    for s in segments:
        stamp = format_timestamp(s.start) if s.has_timing else "--:--"
        lines.append(f"**[{stamp}]** {s.text.strip()}")
        lines.append("")
    return "\n".join(lines)


# ── subtitles ────────────────────────────────────────────────────────────────

def _split_for_cues(segment: Segment, max_chars: int) -> List[Segment]:
    text = segment.text.strip()
    if not text or not segment.has_timing:
        return [segment] if text else []
    if len(text) <= max_chars and (segment.end - segment.start) <= MAX_CUE_SECONDS:
        return [segment]

    timed_words = [w for w in segment.words if w.start is not None and w.end is not None]
    if timed_words:
        cues: List[Segment] = []
        buf: List = []
        for w in timed_words:
            buf.append(w)
            joined = "".join(x.word for x in buf).strip()
            if len(joined) >= max_chars or (buf[-1].end - buf[0].start) >= MAX_CUE_SECONDS:
                cues.append(Segment(text=joined, start=buf[0].start, end=buf[-1].end,
                                    words=list(buf)))
                buf = []
        if buf:
            joined = "".join(x.word for x in buf).strip()
            if joined:
                cues.append(Segment(text=joined, start=buf[0].start, end=buf[-1].end,
                                    words=list(buf)))
        return cues or [segment]

    pieces = max(1, -(-len(text) // max_chars))
    step_text = -(-len(text) // pieces)
    step_time = (segment.end - segment.start) / pieces
    out = []
    for i in range(pieces):
        part = text[i * step_text:(i + 1) * step_text]
        if part:
            out.append(Segment(text=part,
                               start=segment.start + i * step_time,
                               end=segment.start + (i + 1) * step_time))
    return out


def _cues(segments: List[Segment]) -> List[Segment]:
    timed = [s for s in segments if s.has_timing]
    if not timed:
        return []
    max_chars = (MAX_CUE_CHARS_CJK if is_cjk_text("".join(s.text for s in timed[:5]))
                 else MAX_CUE_CHARS_LATIN)
    cues: List[Segment] = []
    for s in timed:
        cues.extend(_split_for_cues(s, max_chars))
    return cues


def to_srt(segments: List[Segment]) -> str:
    out = []
    for i, c in enumerate(_cues(segments), start=1):
        start = format_timestamp(c.start, always_hours=True, decimals=3, separator=",")
        end = format_timestamp(max(c.end, c.start + 0.2), always_hours=True,
                               decimals=3, separator=",")
        out += [str(i), f"{start} --> {end}", c.text.strip(), ""]
    return "\n".join(out)


def to_vtt(segments: List[Segment]) -> str:
    out = ["WEBVTT", ""]
    for c in _cues(segments):
        start = format_timestamp(c.start, always_hours=True, decimals=3, separator=".")
        end = format_timestamp(max(c.end, c.start + 0.2), always_hours=True,
                               decimals=3, separator=".")
        out += [f"{start} --> {end}", c.text.strip(), ""]
    return "\n".join(out)


def to_json(segments: List[Segment], meta: TranscriptMeta) -> str:
    result = _reflow(segments, meta)
    return json.dumps({
        "title": meta.title,
        "source": meta.source,
        "language": meta.language,
        "duration": meta.duration,
        "engine": meta.engine,
        "created_at": meta.created_at,
        "text": result.text,
        "paragraphs": result.paragraphs,
        "segments": [s.to_dict() for s in segments],
    }, ensure_ascii=False, indent=2)


FORMATS = {
    "txt":  {"label": "纯文本（喂 AI 首选）", "media_type": "text/plain; charset=utf-8"},
    "md":   {"label": "Markdown（带元信息）", "media_type": "text/markdown; charset=utf-8"},
    "srt":  {"label": "SRT 字幕", "media_type": "application/x-subrip; charset=utf-8"},
    "vtt":  {"label": "WebVTT 字幕", "media_type": "text/vtt; charset=utf-8"},
    "json": {"label": "JSON（含分段与词级时间戳）", "media_type": "application/json; charset=utf-8"},
    "ts.md": {"label": "Markdown（带时间戳）", "media_type": "text/markdown; charset=utf-8"},
}


def render(fmt: str, segments: List[Segment], meta: TranscriptMeta) -> str:
    if fmt == "txt":
        return to_plain_text(segments, meta)
    if fmt == "md":
        return to_markdown(segments, meta)
    if fmt == "ts.md":
        return to_markdown_timestamped(segments, meta)
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    if fmt == "json":
        return to_json(segments, meta)
    raise ValueError(f"unsupported format: {fmt}")
