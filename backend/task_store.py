"""
Task persistence.

The previous version stored the entire transcript inside every task record in
`tasks.json`, which had grown to 657 KB and would grow without bound; the whole
file was rewritten on every progress tick. Here the index holds only metadata,
transcript bodies live in per-task directories, and finished tasks are pruned.

The index is also the recovery log: a task still marked `running` when the
process starts can only be a leftover from a crash or a restart, so it is
demoted to `interrupted` and offered for resume.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import config
from engines import Segment
from formats import TranscriptMeta, render
from text_utils import safe_filename

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
NEEDS_LOGIN = "needs_login"

ACTIVE_STATUSES = {QUEUED, RUNNING}
EXPORT_FORMATS = ("txt", "md", "srt", "vtt", "json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    id: str
    status: str = QUEUED
    stage: str = "queued"
    progress: float = 0.0            # 0–100, whole pipeline
    message: str = "排队中…"
    source_type: str = "url"         # url | podcast | upload
    source: str = ""
    title: str = ""
    duration: Optional[float] = None
    language: Optional[str] = None
    engine: Optional[str] = None
    model: Optional[str] = None
    origin: Optional[str] = None     # subtitle | whisper
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    segment_count: int = 0
    audio_path: Optional[str] = None
    files: Dict[str, str] = field(default_factory=dict)
    detail: Dict = field(default_factory=dict)   # latest Progress payload
    elapsed_seconds: Optional[float] = None
    speed_x: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def resumable(self) -> bool:
        if self.status == NEEDS_LOGIN:
            return True
        return self.status in (INTERRUPTED, FAILED, CANCELLED) and bool(self.audio_path)


class TaskStore:
    def __init__(self, index_path: Path = None, transcripts_dir: Path = None):
        self.index_path = index_path or config.TASKS_INDEX
        self.transcripts_dir = transcripts_dir or config.TRANSCRIPTS_DIR
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_flush = 0.0
        self.load()

    # ── index I/O ────────────────────────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            self._tasks = {}
            if not self.index_path.exists():
                return
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception as e:
                backup = self.index_path.with_suffix(".corrupt.json")
                logger.error(f"task index unreadable ({e}); moved to {backup.name}")
                try:
                    self.index_path.replace(backup)
                except Exception:
                    pass
                return

            records = raw.get("tasks", raw) if isinstance(raw, dict) else {}
            migrated = 0
            for task_id, rec in records.items():
                if not isinstance(rec, dict):
                    continue
                task, was_legacy = self._coerce(task_id, rec)
                migrated += was_legacy
                # Anything still 'running'/'queued' at startup died with the
                # previous process; surface it instead of leaving a zombie.
                if task.status in ACTIVE_STATUSES:
                    json_export = next(self.task_dir(task.id).glob("*.json"), None)
                    if json_export and json_export.is_file():
                        task.status = COMPLETED
                        task.stage = "completed"
                        task.progress = 100
                        task.message = "转录完成"
                        found = {}
                        for p in self.task_dir(task.id).iterdir():
                            ext = p.suffix.lstrip(".").lower()
                            if ext in EXPORT_FORMATS:
                                found[ext] = p.name
                        if found:
                            task.files = found
                    else:
                        task.status = INTERRUPTED
                        task.stage = "interrupted"
                        task.message = "服务重启导致任务中断，可继续转录"
                self._tasks[task_id] = task

            if migrated:
                logger.info(f"migrated {migrated} legacy task record(s) out of the index")
            self._dirty = True
            self.flush(force=True)

    def _coerce(self, task_id: str, rec: dict) -> tuple[Task, bool]:
        """Accept both the new schema and the legacy inline-transcript schema."""
        known = {f for f in Task.__dataclass_fields__}
        legacy = "script" in rec or "summary" in rec or "video_title" in rec

        if legacy:
            status = rec.get("status") or QUEUED
            status = {"processing": RUNNING, "error": FAILED}.get(status, status)
            data = {
                "id": task_id,
                "status": status,
                "progress": float(rec.get("progress") or 0),
                "message": rec.get("message") or "",
                "source": rec.get("source_url") or rec.get("url") or "",
                "title": rec.get("video_title") or "",
                "language": rec.get("detected_language"),
                "error": rec.get("error"),
                "audio_path": rec.get("audio_path"),
                "source_type": "podcast" if rec.get("type") == "podcast" else "url",
            }
            if str(data["source"]).startswith("upload:"):
                data["source_type"] = "upload"
            task = Task(**data)
            # The legacy .md/.txt files on disk are still useful for history.
            for key, fmt in (("script_path", "md"), ("script_txt_path", "txt")):
                p = rec.get(key)
                if p and Path(p).exists():
                    task.files[fmt] = Path(p).name
            return task, True

        data = {k: v for k, v in rec.items() if k in known}
        data["id"] = task_id
        return Task(**data), False

    def flush(self, force: bool = False, min_interval: float = 1.0) -> None:
        """Write the index atomically, debounced so progress ticks stay cheap."""
        with self._lock:
            if not self._dirty:
                return
            if not force and (time.time() - self._last_flush) < min_interval:
                return
            payload = {
                "version": 2,
                "updated_at": _now(),
                "tasks": {tid: t.to_dict() for tid, t in self._tasks.items()},
            }
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(tmp, self.index_path)
                self._dirty = False
                self._last_flush = time.time()
            except Exception as e:
                logger.error(f"failed to persist task index: {e}")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create(self, **kwargs) -> Task:
        task_id = kwargs.pop("id", None) or str(uuid.uuid4())
        task = Task(id=task_id, **kwargs)
        with self._lock:
            self._tasks[task_id] = task
            self._dirty = True
            self.flush(force=True)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def update(self, task_id: str, *, flush: bool = True, **fields) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for k, v in fields.items():
                if hasattr(task, k):
                    setattr(task, k, v)
            self._dirty = True
            if flush:
                self.flush()
            return task

    def list(self, limit: int = 100, include_active: bool = True) -> List[Task]:
        with self._lock:
            items = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        if not include_active:
            items = [t for t in items if t.status not in ACTIVE_STATUSES]
        return items[:limit]

    def active(self) -> List[Task]:
        return [t for t in self._tasks.values() if t.status in ACTIVE_STATUSES]

    def delete(self, task_id: str, remove_files: bool = True) -> bool:
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return False
            self._dirty = True
            self.flush(force=True)
        if remove_files:
            shutil.rmtree(self.task_dir(task_id), ignore_errors=True)
            if task.audio_path and task.source_type == "upload":
                Path(task.audio_path).unlink(missing_ok=True)
        return True

    # ── transcript files ─────────────────────────────────────────────────────

    def task_dir(self, task_id: str) -> Path:
        return self.transcripts_dir / task_id

    def partial_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "partial.jsonl"

    def clear_partial(self, task_id: str) -> None:
        self.partial_path(task_id).unlink(missing_ok=True)

    def write_exports(self, task: Task, segments: List[Segment],
                      meta: TranscriptMeta) -> Dict[str, str]:
        """Render every export format to the task directory."""
        out_dir = self.task_dir(task.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(meta.title or "transcript", max_len=60) or "transcript"

        files: Dict[str, str] = {}
        for fmt in EXPORT_FORMATS:
            try:
                body = render(fmt, segments, meta)
            except Exception as e:
                logger.warning(f"export {fmt} failed for {task.id}: {e}")
                continue
            name = f"{stem}.{fmt}"
            (out_dir / name).write_text(body, encoding="utf-8")
            files[fmt] = name
        return files

    def read_export(self, task_id: str, fmt: str) -> Optional[Path]:
        task = self.get(task_id)
        if task is None:
            return None
        name = task.files.get(fmt)
        if not name:
            return None
        # Legacy records point at files that lived directly in temp/.
        for candidate in (self.task_dir(task_id) / name, config.TEMP_DIR / name):
            if candidate.exists():
                return candidate
        return None

    def load_segments(self, task_id: str) -> List[Segment]:
        """Recover segments from the JSON export, for re-rendering other formats."""
        path = self.read_export(task_id, "json")
        if path is None:
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [Segment.from_dict(s) for s in data.get("segments", [])]
        except Exception as e:
            logger.warning(f"could not load segments for {task_id}: {e}")
            return []

    # ── housekeeping ─────────────────────────────────────────────────────────

    def prune(self, keep: int = None) -> int:
        """Drop the oldest finished tasks beyond `keep`, with their files."""
        keep = keep or config.MAX_TASK_HISTORY
        with self._lock:
            finished = sorted(
                (t for t in self._tasks.values() if t.status not in ACTIVE_STATUSES),
                key=lambda t: t.created_at,
                reverse=True,
            )
        removed = 0
        for task in finished[keep:]:
            if self.delete(task.id):
                removed += 1
        if removed:
            logger.info(f"pruned {removed} old task(s)")
        return removed
