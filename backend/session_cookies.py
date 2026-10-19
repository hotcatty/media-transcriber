"""Ephemeral per-visitor login cookies.

Public trial must never write cookies.txt. Netscape text lives in memory keyed
by mt_sid, and is materialized to a NamedTemporaryFile only while yt-dlp runs.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

TTL_SEC = 6 * 3600
MAX_BYTES = 64_000
MAX_SESSIONS = 400

_lock = threading.Lock()
_store: dict[str, tuple[str, float]] = {}


def _prune_locked(now: float) -> None:
    dead = [key for key, (_text, exp) in _store.items() if exp <= now]
    for key in dead:
        _store.pop(key, None)
    overflow = len(_store) - MAX_SESSIONS
    if overflow <= 0:
        return
    oldest = sorted(_store.items(), key=lambda item: item[1][1])[:overflow]
    for key, _ in oldest:
        _store.pop(key, None)


def put(session_id: str, text: str) -> None:
    sid = (session_id or "").strip()
    blob = (text or "").strip()
    if not sid or not blob:
        raise ValueError("empty")
    raw = blob if blob.endswith("\n") else blob + "\n"
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("too large")
    now = time.time()
    with _lock:
        _prune_locked(now)
        _store[sid] = (raw, now + TTL_SEC)


def get(session_id: str) -> Optional[str]:
    sid = (session_id or "").strip()
    if not sid:
        return None
    now = time.time()
    with _lock:
        item = _store.get(sid)
        if not item:
            return None
        text, exp = item
        if exp <= now:
            _store.pop(sid, None)
            return None
        return text


def clear(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        _store.pop(sid, None)


def exists(session_id: str) -> bool:
    return get(session_id) is not None


def size(session_id: str) -> int:
    text = get(session_id)
    return len(text.encode("utf-8")) if text else 0


@contextmanager
def materialize(session_id: str) -> Iterator[Optional[str]]:
    text = get(session_id)
    if not text:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="mt-ck-", suffix=".txt")
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        fd = -1
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass
