"""
Turn raw recogniser output into text that reads well and feeds an LLM cleanly.

Whisper hands back 5–15 second fragments and, for Mandarin, almost no
punctuation at all (measured on real material: 0.0–0.1% of characters). Written
straight out, that produces the fragmented wall this tool used to emit:

    新天的课程里面来了

    我们的课程是今天要分享的东西

    就是我们在今天体验设计这个

Fragments like that waste tokens and break sentences apart mid-clause, which
costs accuracy when the transcript is pasted into a model. This module

  1. collapses runaway repetitions (a known Whisper failure mode),
  2. restores punctuation from pause lengths when the engine supplied none,
  3. reassembles everything into paragraphs of roughly 120–300 characters,
     breaking on real pauses and sentence ends.

Engines that already punctuate (SenseVoice, some cloud APIs) skip step 2.
Engines without timestamps still work; they just rely on punctuation alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from engines import Segment
from text_utils import is_cjk_text

SENTENCE_END_CJK = "。！？…"
CLAUSE_CJK = "，、；："
SENTENCE_END_LATIN = ".!?"
CLAUSE_LATIN = ",;:"

_ALL_PUNCT = re.compile(r"[。！？…，、；：.!?,;:]")
_SENTENCE_SPLIT_CJK = re.compile(r"(?<=[。！？…])")
_SENTENCE_SPLIT_LATIN = re.compile(r"(?<=[.!?])\s+")
_LATIN_NOSPACE_AFTER = set(" \n\t([{\"'")
_LATIN_NOSPACE_BEFORE = set(" \n\t.,!?;:)]}…\"'")


def _glue(left: str, right: str, cjk: bool) -> str:
    """Join fragments. Latin needs a space; CJK does not."""
    if not left:
        return right
    if not right:
        return left
    if cjk:
        return left + right
    if left[-1] in _LATIN_NOSPACE_AFTER or right[0] in _LATIN_NOSPACE_BEFORE:
        return left + right
    return left + " " + right


@dataclass
class ReflowOptions:
    target_min_chars: int = 120
    target_max_chars: int = 300
    clause_pause: float = 0.30       # gap ≥ this → clause break (，)
    sentence_pause: float = 0.70     # gap ≥ this → sentence break (。)
    paragraph_pause: float = 2.00    # gap ≥ this → force a new paragraph
    max_repeat_run: int = 2          # identical fragments kept in a row


@dataclass
class ReflowResult:
    paragraphs: List[str]
    punctuation_restored: bool
    repetitions_collapsed: int
    character_count: int

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


# ── step 1: collapse repetition loops ────────────────────────────────────────

def _collapse_repeats(segments: Sequence[Segment], max_run: int) -> tuple[List[Segment], int]:
    """
    Whisper can lock into repeating one phrase for minutes. Keeping a couple of
    occurrences preserves genuine repetition while discarding the loop.
    """
    out: List[Segment] = []
    removed = 0
    run_text: Optional[str] = None
    run_len = 0

    for seg in segments:
        key = seg.text.strip()
        if not key:
            continue
        if key == run_text:
            run_len += 1
            if run_len > max_run:
                removed += 1
                continue
        else:
            run_text, run_len = key, 1
        out.append(seg)
    return out, removed


# ── step 2: punctuation from pauses ──────────────────────────────────────────

def _punctuation_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_ALL_PUNCT.findall(text)) / len(text)


def _gap_between(a: Segment, b: Segment) -> Optional[float]:
    if a.end is None or b.start is None:
        return None
    return max(0.0, b.start - a.end)


def _stitch_with_pauses(segments: Sequence[Segment], opts: ReflowOptions,
                        cjk: bool) -> List[tuple[str, bool]]:
    """
    Join fragments, inserting punctuation at pauses.

    Returns (text, hard_break) pairs where `hard_break` marks a pause long
    enough to justify starting a new paragraph regardless of length.
    """
    sent_end = "。" if cjk else ". "
    clause = "，" if cjk else ", "
    pieces: List[tuple[str, bool]] = []

    buffer = ""
    for i, seg in enumerate(segments):
        text = seg.text.strip()
        if not text:
            continue
        buffer = _glue(buffer, text, cjk)

        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if nxt is None:
            if buffer and buffer[-1] not in SENTENCE_END_CJK + SENTENCE_END_LATIN:
                buffer += "。" if cjk else "."
            pieces.append((buffer, True))
            buffer = ""
            break

        gap = _gap_between(seg, nxt)
        ends_with_punct = buffer[-1] in (
            SENTENCE_END_CJK + CLAUSE_CJK + SENTENCE_END_LATIN + CLAUSE_LATIN
        )

        if gap is None:
            # No timing: trust the segment boundary as a clause boundary.
            if not ends_with_punct:
                buffer += clause
            continue

        if gap >= opts.paragraph_pause:
            if not buffer[-1] in SENTENCE_END_CJK + SENTENCE_END_LATIN:
                buffer += sent_end
            pieces.append((buffer.strip(), True))
            buffer = ""
        elif gap >= opts.sentence_pause:
            if not ends_with_punct or buffer[-1] in CLAUSE_CJK + CLAUSE_LATIN:
                buffer = buffer.rstrip(CLAUSE_CJK + CLAUSE_LATIN) + sent_end
            pieces.append((buffer.strip(), False))
            buffer = ""
        elif gap >= opts.clause_pause or cjk:
            # Mandarin Whisper segments are usually back-to-back (gap ≈ 0).
            # Treat every fragment as a clause so we don't emit an unpunctuated wall.
            if not ends_with_punct:
                if cjk and len(buffer) >= 72:
                    buffer += sent_end
                    pieces.append((buffer.strip(), False))
                    buffer = ""
                else:
                    buffer += clause
        # Latin + very short gaps: continue the clause with no separator

    if buffer.strip():
        pieces.append((buffer.strip(), True))
    return pieces


def _split_sentences(text: str, cjk: bool) -> List[str]:
    parts = (_SENTENCE_SPLIT_CJK if cjk else _SENTENCE_SPLIT_LATIN).split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _force_split_long(sentence: str, limit: int, cjk: bool) -> List[str]:
    """Break an over-long sentence at the nearest clause mark before the limit."""
    if len(sentence) <= limit:
        return [sentence]
    marks = CLAUSE_CJK if cjk else CLAUSE_LATIN
    out, rest = [], sentence
    while len(rest) > limit:
        window = rest[:limit]
        cut = max((window.rfind(m) for m in marks), default=-1)
        if cut < limit // 3:
            cut = limit - 1
        out.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].lstrip()
    if rest:
        out.append(rest)
    return out


# ── step 3: paragraph assembly ───────────────────────────────────────────────

def _assemble(pieces: Sequence[tuple[str, bool]], opts: ReflowOptions,
              cjk: bool) -> List[str]:
    paragraphs: List[str] = []
    current = ""

    def flush():
        nonlocal current
        if current.strip():
            paragraphs.append(current.strip())
        current = ""

    for text, hard_break in pieces:
        for sentence in _split_sentences(text, cjk):
            for part in _force_split_long(sentence, opts.target_max_chars, cjk):
                if current and len(current) + len(part) > opts.target_max_chars:
                    flush()
                current = _glue(current, part, cjk)
                if len(current) >= opts.target_min_chars:
                    flush()
        if hard_break:
            flush()
    flush()

    return _merge_stragglers(paragraphs, opts, cjk)


def _merge_stragglers(paragraphs: List[str], opts: ReflowOptions,
                      cjk: bool) -> List[str]:
    """Fold very short paragraphs into their neighbour to avoid choppiness."""
    floor = max(24, opts.target_min_chars // 4)
    out: List[str] = []
    for para in paragraphs:
        if out and len(para) < floor and len(out[-1]) + len(para) <= opts.target_max_chars * 1.3:
            out[-1] = _glue(out[-1], para, cjk)
        else:
            out.append(para)
    return out


# ── entry point ──────────────────────────────────────────────────────────────

def reflow_segments(segments: Sequence[Segment],
                    options: Optional[ReflowOptions] = None,
                    engine_punctuates: bool = False) -> ReflowResult:
    """
    Rebuild readable, LLM-friendly paragraphs from recogniser segments.

    `engine_punctuates` skips pause-based punctuation insertion for engines that
    already produce it.
    """
    opts = options or ReflowOptions()
    segments = [s for s in segments if s.text and s.text.strip()]
    if not segments:
        return ReflowResult([], False, 0, 0)

    segments, collapsed = _collapse_repeats(segments, opts.max_repeat_run)

    raw = "".join(s.text for s in segments)
    cjk = is_cjk_text(raw)
    already_punctuated = engine_punctuates or _punctuation_ratio(raw) >= 0.02

    if already_punctuated:
        pieces: List[tuple[str, bool]] = []
        buffer = ""
        for i, seg in enumerate(segments):
            buffer = _glue(buffer, seg.text.strip(), cjk)
            nxt = segments[i + 1] if i + 1 < len(segments) else None
            gap = _gap_between(seg, nxt) if nxt else None
            if nxt is None or (gap is not None and gap >= opts.paragraph_pause):
                pieces.append((buffer, True))
                buffer = ""
        if buffer.strip():
            pieces.append((buffer, True))
    else:
        pieces = _stitch_with_pauses(segments, opts, cjk)

    paragraphs = _assemble(pieces, opts, cjk)
    return ReflowResult(
        paragraphs=paragraphs,
        punctuation_restored=not already_punctuated,
        repetitions_collapsed=collapsed,
        character_count=sum(len(p) for p in paragraphs),
    )
