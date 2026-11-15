"""
Pull a short proper-noun list from page context and use it twice:

  1. Whisper's initial_prompt only keeps ~220 characters. Names must go in
     first or they get trimmed away.
  2. After decoding, swap near-miss spellings back to the page's writing.

This does not make `small` hear better. It only steadies guest names, show
titles, and brands that the page already spelled out.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_LABEL_RE = re.compile(
    r"(嘉宾|飞行嘉宾|特邀嘉宾|常驻嘉宾|主持|主播|制作人|"
    r"节目|频道|出品|嘉宾主持|分集|标签)[:：]\s*(.+)"
)
_QUOTED_RE = re.compile(r"[《「『\"“]([^《」』\"”]{2,20})[》」』\"”]")
_LATIN_NAME_RE = re.compile(r"\b[A-Z][a-z]{1,20}(?:[ \-][A-Z][a-z]{1,20}){0,3}\b")
_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
_SPLIT_RE = re.compile(r"[、，,;；/｜|]\s*")
_SPACE_RE = re.compile(r"\s+")

_STOP = {
    "我们", "你们", "他们", "这个", "那个", "什么", "怎么", "可以", "一个",
    "没有", "还是", "因为", "所以", "如果", "但是", "然后", "今天", "现在",
    "自己", "大家", "问题", "时候", "知道", "觉得", "看到", "这样", "那样",
    "东西", "内容", "分享", "视频", "播客", "节目", "订阅", "点赞", "收藏",
    "评论", "转发", "链接", "关注", "更新", "官方", "频道", "播放", "音频",
    "文字", "全文", "转写", "转录", "感谢", "欢迎", "本期", "介绍", "更多",
    "查看", "点击", "搜索", "下载", "免费", "会员", "充电", "原创", "作者",
    "标题", "背景", "来源", "小宇宙", "哔哩哔哩", "小红书", "油管",
    "就是", "其实", "不是", "已经", "可能", "应该", "这么", "那么",
    "而且", "或者", "真的", "当然", "不过", "怎么样", "为什么",
}

HOTWORD_CAP = 24


def _caption_body(source_context: str) -> str:
    lines = []
    for line in (source_context or "").splitlines():
        if line.startswith("字幕："):
            lines.append(line.split("：", 1)[1])
    return "\n".join(lines)


def _without_caption(source_context: str) -> str:
    lines = [
        line for line in (source_context or "").splitlines()
        if not line.startswith("字幕：")
    ]
    return "\n".join(lines)


def extract_hotwords(
    *,
    title: str = "",
    source_context: str = "",
    extra: Optional[Iterable[str]] = None,
) -> list[str]:
    """Deduped proper nouns. Names first, then topic tags, then repeats.

    The whisper prompt is trimmed from the end, so names have to lead.
    """
    names: list[str] = []
    topics: list[str] = []
    repeats: list[str] = []

    def add(bucket: list[str], raw: str) -> None:
        word = _SPACE_RE.sub(" ", (raw or "").strip()).strip(" ·•-—|/")
        if not _usable(word):
            return
        if word in names or word in topics or word in repeats or word in bucket:
            return
        bucket.append(word)

    title = (title or "").strip()
    if title and 2 <= len(title) <= 24:
        add(names, title)
    # Shorter names inside the title (王慧文 in 「和王慧文对谈」) are the
    # ones Whisper actually misspells. Keep them when the page repeats them.
    ctx_text = source_context or ""
    if title:
        for n in (4, 3):
            for i in range(0, len(title) - n + 1):
                piece = title[i:i + n]
                if piece[0] in "和与的了在是把被从对":
                    continue
                if _CJK_TOKEN_RE.fullmatch(piece) and piece in ctx_text:
                    add(names, piece)

    blob = f"{title}\n{source_context or ''}"
    for m in _LABEL_RE.finditer(blob):
        bucket = topics if m.group(1) == "标签" else names
        for part in _SPLIT_RE.split(m.group(2).strip()):
            add(bucket, part.split()[0] if "http" in part else part)
    for m in _QUOTED_RE.finditer(blob):
        add(names, m.group(1))
    for m in _LATIN_NAME_RE.finditer(blob):
        add(names, m.group(0))

    if extra:
        for item in extra:
            add(topics, item)

    # Unlabeled CJK runs only when they already look like a name-sized token
    # (2–4 chars, not a stopword) and the page repeats them. Caption lines are
    # counted on their own so a spoken name still lands, without letting one
    # long transcript promote every filler word ahead of the names.
    body = _without_caption(source_context or "")
    caption = _caption_body(source_context or "")
    for token in _CJK_TOKEN_RE.findall(body):
        if 2 <= len(token) <= 4 and token not in _STOP and body.count(token) >= 2:
            add(repeats, token)
    for token in _CJK_TOKEN_RE.findall(caption):
        if 2 <= len(token) <= 4 and token not in _STOP and caption.count(token) >= 2:
            add(repeats, token)

    picked: list[str] = []
    for group in (names, topics, repeats):
        for word in sorted(group, key=len, reverse=True):
            if word in picked:
                continue
            picked.append(word)
            if len(picked) >= HOTWORD_CAP:
                return picked
    return picked


def apply_hotword_fixes(text: str, hotwords: Iterable[str]) -> str:
    """Replace same-length / one-edit near misses with the page's spelling."""
    if not text:
        return text
    words = [w for w in hotwords if _usable(w)]
    if not words:
        return text
    out = text
    for word in words:
        out = _fix_one(out, word)
    return out


def _usable(word: str) -> bool:
    if not word or word in _STOP:
        return False
    if word.startswith("http") or "/" in word:
        return False
    letters = re.sub(r"[\s\-\.]", "", word)
    if not (2 <= len(letters) <= 24):
        return False
    if letters.isdigit():
        return False
    return True


def _near(a: str, b: str) -> bool:
    if a == b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if min(len(a), len(b)) < 2:
        return False
    if len(a) == len(b):
        diffs = sum(x != y for x, y in zip(a, b))
        if diffs != 1:
            return False
        return a[0] == b[0] or len(a) >= 3
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    i = j = miss = 0
    while i < len(longer) and j < len(shorter):
        if longer[i] == shorter[j]:
            i += 1
            j += 1
            continue
        miss += 1
        if miss > 1:
            return False
        i += 1
    return longer[0] == shorter[0]


def _fix_one(text: str, word: str) -> str:
    if word in text:
        return text
    n = len(word)
    if n < 2 or n > len(text):
        return text
    # Same-length windows first (the usual 王会文 → 王慧文 case).
    pieces: list[str] = []
    i = 0
    while i < len(text):
        window = text[i:i + n]
        if len(window) == n and _near(window, word) and _window_ok(window):
            pieces.append(word)
            i += n
            continue
        pieces.append(text[i])
        i += 1
    return "".join(pieces)


def _window_ok(window: str) -> bool:
    if window in _STOP:
        return False
    # Don't rewrite punctuation-laden scraps.
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", window))
