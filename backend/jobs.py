"""
Job orchestration: one transcription at a time, progress on the event loop,
cancellation via a thread Event the worker thread actually checks.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import audio as audio_utils
import config
import cookie_import
import engines
import eta
import formats
import settings_store
from engines import Segment
from podcast_extractor import extract_podcast_audio, is_podcast_url
from task_store import (
    CANCELLED,
    COMPLETED,
    FAILED,
    INTERRUPTED,
    NEEDS_LOGIN,
    QUEUED,
    RUNNING,
    Task,
    TaskStore,
    _now,
)
from text_utils import (
    CHARGE_NEED_LOGIN_HINT,
    LOGIN_PARSE_HINT,
    canonical_source,
    display_title,
    format_slash_when,
    friendly_error,
    humanize_duration,
    is_charge_gated,
    is_charge_need_login,
    is_login_required,
    is_real_title,
    fail_hint_for_task,
    platform_label,
    safe_filename,
    strip_ansi,
)
from transcription import Cancelled, Progress, run_transcription
from video_processor import VideoProcessor

logger = logging.getLogger(__name__)

FIRST_MODEL_HINT = "首次使用需要下载语音分析模型，可能需要几分钟"


def _fmt_model_size(done: int, total: int) -> str:
    def one(n: int) -> str:
        n = max(int(n or 0), 0)
        if n >= 1024 ** 3:
            g = n / (1024 ** 3)
            return f"{g:.1f}G".replace(".0G", "G")
        return f"{n / (1024 ** 2):.0f}M"

    if not total:
        return f"{one(done)} / 1.6G"
    return f"{one(done)} / {one(total)}"


def _has_audio_file(task: Task) -> bool:
    if task.origin == "subtitle":
        return False
    if not task.audio_path:
        return False
    try:
        return Path(task.audio_path).is_file()
    except Exception:
        return False


def pipeline_steps(task: Task) -> list:
    """Fixed to-do list for the progress card. Only real pipeline stages."""
    upload = task.source_type == "upload"
    used_subs = task.origin == "subtitle"
    has_audio = bool(task.audio_path)
    stage = task.stage or ""
    status = task.status
    detail = task.detail or {}
    show_model = (
        stage == "downloading_model"
        or detail.get("model_step") in ("current", "done")
    )

    if upload:
        defs = [
            ("prepare", "准备文件"),
            ("transcribe", "语音识别"),
            ("finalize", "整理文稿"),
        ]
    elif used_subs:
        defs = [
            ("parse", "解析链接"),
            ("download", "读取字幕"),
            ("finalize", "整理文稿"),
        ]
    else:
        defs = [
            ("parse", "解析链接"),
            ("download", "下载音频"),
            ("transcribe", "语音识别"),
            ("finalize", "整理文稿"),
        ]
    if show_model:
        defs = [("model", "下载turbo模型")] + defs
    ids = [d[0] for d in defs]

    def cursor() -> int:
        if status == COMPLETED or stage == "completed":
            return len(ids)
        if stage == "downloading_model" and "model" in ids:
            return ids.index("model")
        if stage == "finalizing":
            return ids.index("finalize")
        if stage in ("planning", "transcribing"):
            return ids.index("transcribe") if "transcribe" in ids else ids.index("finalize")
        if used_subs:
            return ids.index("finalize")
        if stage == "downloading" or (stage == "converting" and not upload):
            return ids.index("download")
        if stage in ("parse", "subtitles", "extracting", "preparing"):
            return ids.index("parse") if "parse" in ids else 0
        if upload:
            return ids.index("prepare")
        if has_audio and "transcribe" in ids:
            return ids.index("transcribe")
        return ids.index("parse") if "parse" in ids else 0

    cur = cursor()
    if status == FAILED:
        raw = (task.error or task.message or "").lower()
        media = "unable to download video data" in raw
        parsed_then_died = (
            is_real_title(task.title or "")
            and not has_audio
            and stage not in ("transcribing", "planning", "finalizing")
        )
        if (stage in ("downloading", "converting") or media or parsed_then_died) and "download" in ids:
            cur = ids.index("download")
        elif stage in ("transcribing", "planning") and "transcribe" in ids:
            cur = ids.index("transcribe")
        elif stage == "finalizing" and "finalize" in ids:
            cur = ids.index("finalize")
    if status == NEEDS_LOGIN:
        charge_dl = is_charge_gated(task.error or task.message or "")
        if charge_dl and "download" in ids:
            cur = ids.index("download")
        else:
            cur = ids.index("parse") if "parse" in ids else 0
    out = []
    for i, (sid, label) in enumerate(defs):
        if cur >= len(ids):
            state = "done"
        elif i < cur:
            state = "done"
        elif i == cur:
            if status == NEEDS_LOGIN:
                state = "needs_login"
            elif status == FAILED:
                state = "failed"
            else:
                state = "current"
        else:
            state = "pending"
        if sid == "parse" and state in ("failed", "needs_login"):
            label = "解析失败"
        item = {"id": sid, "label": label, "state": state}
        if state == "failed":
            reason = fail_hint_for_task(
                task.source,
                task.source_type,
                task.error or task.message or "",
                FAILED,
                stage,
                title=task.title or "",
                has_audio=has_audio,
            )
            if reason:
                item["hint"] = reason
        elif state == "needs_login":
            item["hint"] = (
                CHARGE_NEED_LOGIN_HINT
                if is_charge_gated(task.error or task.message or "")
                else LOGIN_PARSE_HINT
            )
        elif sid == "model" and state == "current":
            d = task.detail or {}
            item["size"] = _fmt_model_size(
                int(d.get("downloaded_bytes") or 0),
                int(d.get("total_bytes") or 0),
            )
            item["hint"] = FIRST_MODEL_HINT
        out.append(item)
    return out


def _download_kind(task: Task) -> str:
    if task.source_type == "podcast":
        return "podcast"
    return "video"


def _prior_remain(task: Task) -> float:
    detail = task.detail or {}
    stage = task.stage or ""
    duration = task.duration or detail.get("total_seconds")
    processed = detail.get("processed_seconds") or 0
    speed = detail.get("speed_x")
    if stage == "downloading_model":
        return eta.prior_model()
    if stage in ("downloading", "converting"):
        return eta.prior_download(duration, _download_kind(task))
    if stage in ("transcribing", "planning"):
        return eta.prior_transcribe(duration, processed, speed, settings_store.whisper_speed())
    if stage == "finalizing":
        return eta.prior_finalize()
    return eta.prior_parse()


def _trailing_remain(task: Task) -> float:
    detail = task.detail or {}
    return eta.trailing(
        stage=task.stage or "",
        duration=task.duration or detail.get("total_seconds"),
        kind=_download_kind(task),
        origin=task.origin,
        processed=detail.get("processed_seconds") or 0,
        speed_x=detail.get("speed_x"),
        whisper_speed=settings_store.whisper_speed(),
    )


def _live_remain(task: Task) -> float:
    """Job remaining: never above leftover of the frozen total."""
    now = time.time()
    measured = eta.remain_from_detail(task.detail, now)
    cap = eta.budget_remain(task.detail, now)
    if cap is not None:
        if measured is None:
            return cap
        return min(measured, cap)
    if measured is not None:
        return measured
    return _prior_remain(task)


def _total_wait_seconds(task: Task) -> Optional[float]:
    """Whole-job estimate: original prior, shrunk when we run ahead."""
    detail = task.detail or {}
    frozen = detail.get("eta_budget")
    remain = _live_remain(task)
    started = detail.get("eta_budget_at")
    elapsed = (time.time() - float(started)) if started else 0.0
    projected = max(elapsed, 0.0) + max(remain, 0.0)
    if frozen is not None:
        return min(float(frozen), projected if projected else float(frozen))
    have_file = bool(task.audio_path) and Path(str(task.audio_path)).exists()
    return eta.total_wait(
        task.duration or detail.get("total_seconds"),
        _download_kind(task),
        task.origin,
        settings_store.whisper_speed(),
        include_download=not have_file,
    )


def _format_total_wait(seconds: Optional[float]) -> str:
    """Figma 23:428: 预计总用时12分钟，伸伸懒腰，请稍等."""
    if seconds is None or seconds < 0:
        return ""
    s = int(round(seconds))
    span = "不到1分钟" if s < 60 else f"{max(1, int(round(s / 60)))}分钟"
    return f"预计总用时{span}，伸伸懒腰，请稍等"


def _attach_step_progress(task: Task, steps: list) -> list:
    """Attach countdown copy to the current pipeline step. No graphical bar."""
    remain = _live_remain(task)
    stamped = eta.stamp(remain)
    for s in steps:
        if s.get("state") != "current":
            continue
        s["eta_seconds"] = stamped["eta_seconds"]
        s["eta_at"] = stamped["eta_at"]
        s["eta_deadline"] = stamped["eta_deadline"]
        s["eta"] = eta.format_remain(remain)
        break
    still_running = task.status not in (COMPLETED, FAILED, CANCELLED, NEEDS_LOGIN)
    if still_running and task.stage != "completed":
        wait = _format_total_wait(_total_wait_seconds(task))
        if wait:
            for s in steps:
                if s.get("id") == "parse" and s.get("state") == "done":
                    s["hint"] = wait
                    break
    return steps


def public_task(task: Task) -> dict:
    d = task.to_dict()
    d["resumable"] = task.resumable
    d["title"] = display_title(task.title, task.source, task.source_type, task.created_at)
    d["has_audio"] = _has_audio_file(task)
    d["origin"] = task.origin
    d["created_label"] = format_slash_when(task.created_at)
    site = cookie_import.site_from_url(task.source)
    meta = cookie_import.site_info(site)
    d["login_site"] = meta["id"]
    d["login_label"] = meta["label"]
    d["login_host"] = meta["host"]
    d["login_url"] = meta["url"]
    steps = _attach_step_progress(task, pipeline_steps(task))
    if _has_audio_file(task):
        for s in steps:
            if s.get("id") == "download" and s.get("state") == "done":
                s["action"] = "view_audio"
                s["action_label"] = "查看音频"
                break
    d["steps"] = steps
    if task.status in (FAILED, NEEDS_LOGIN):
        hint = fail_hint_for_task(
            task.source,
            task.source_type,
            task.error or task.message or "",
            task.status,
            task.stage or "",
            title=task.title or "",
            has_audio=_has_audio_file(task),
        )
        if d.get("error"):
            d["error"] = hint
        if d.get("message"):
            d["message"] = hint
    elif d.get("error"):
        d["error"] = friendly_error(d["error"])
    return d


def _map_progress(p: Progress) -> float:
    """Fold transcription-stage fraction into the whole-pipeline 0–100 bar."""
    if p.stage == "planning":
        return 22.0
    if p.stage == "downloading_model":
        return 18.0
    if p.stage == "transcribing":
        return 22.0 + max(0.0, min(1.0, p.fraction)) * 70.0
    if p.stage == "finalizing":
        return 95.0
    return 20.0


class JobManager:
    def __init__(self, store: TaskStore):
        self.store = store
        self.video = VideoProcessor()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.cancel_events: Dict[str, threading.Event] = {}
        self.sse: Dict[str, List[asyncio.Queue]] = {}
        self._worker: Optional[asyncio.Task] = None
        self._queued: set[str] = set()

    def reload_cookies(self) -> None:
        self.video = VideoProcessor()

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._loop(), name="job-worker")

    def stop(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()

    async def broadcast(self, task: Task) -> None:
        payload = public_task(task)
        queues = list(self.sse.get(task.id, []))
        dead = []
        for q in queues:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            self.sse[task.id].remove(q)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.sse.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        buckets = self.sse.get(task_id)
        if not buckets:
            return
        if q in buckets:
            buckets.remove(q)
        if not buckets:
            self.sse.pop(task_id, None)

    def find_active(self, source: str) -> Optional[Task]:
        key = canonical_source(source)
        for t in self.store.active():
            if canonical_source(t.source) == key:
                return t
        return None

    def find_completed(self, source: str) -> Optional[Task]:
        key = canonical_source(source)
        if not key:
            return None
        for t in self.store.list(limit=200):
            if t.status != COMPLETED:
                continue
            if canonical_source(t.source) != key:
                continue
            if self.store.read_export(t.id, "txt") or self.store.read_export(t.id, "json"):
                return t
        return None

    async def submit_url(self, url: str) -> Task:
        existing = self.find_active(url)
        if existing:
            return existing
        done = self.find_completed(url)
        if done:
            logger.info("复用已有文稿 %s ← %s", done.id[:8], url)
            return done
        source_type = "podcast" if is_podcast_url(url) else "url"
        now = datetime.now()
        fallback = f"{platform_label(url, source_type)} · {now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"
        task = self.store.create(
            source=url,
            source_type=source_type,
            title=fallback,
            status=QUEUED,
            stage="queued",
            message="已加入队列…",
        )
        await self._enqueue(task.id)
        return task

    async def submit_upload(self, saved_path: Path, original_name: str) -> Task:
        title = safe_filename(Path(original_name).stem) or original_name
        task = self.store.create(
            source=f"upload:{original_name}",
            source_type="upload",
            title=title,
            status=QUEUED,
            stage="queued",
            message="已加入队列…",
            audio_path=str(saved_path),
        )
        await self._enqueue(task.id)
        return task

    async def resume(self, task_id: str) -> Task:
        task = self.store.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        if not task.resumable:
            raise ValueError("该任务无法继续")
        if task.status in (QUEUED, RUNNING):
            return task
        self.store.update(
            task_id, status=QUEUED, stage="queued", error=None,
            message="准备继续上次进度…", flush=True,
        )
        await self._enqueue(task_id)
        return self.store.get(task_id)

    async def cancel(self, task_id: str) -> None:
        ev = self.cancel_events.get(task_id)
        if ev:
            ev.set()
        task = self.store.get(task_id)
        if task and task.status in (QUEUED, RUNNING, INTERRUPTED, NEEDS_LOGIN):
            self.store.update(
                task_id, status=CANCELLED, stage="cancelled",
                message="已取消", finished_at=task.finished_at,
                flush=True,
            )
            await self.broadcast(self.store.get(task_id))

    async def _enqueue(self, task_id: str) -> None:
        if task_id in self._queued:
            return
        self._queued.add(task_id)
        await self.queue.put(task_id)

    async def _loop(self) -> None:
        while True:
            task_id = await self.queue.get()
            self._queued.discard(task_id)
            try:
                await self._run(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"job {task_id} crashed")
            finally:
                self.queue.task_done()
                self.cancel_events.pop(task_id, None)

    async def _run(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if task is None:
            return
        if task.status == CANCELLED:
            return

        cancel = threading.Event()
        self.cancel_events[task_id] = cancel
        self.store.update(
            task_id, status=RUNNING, stage="preparing", started_at=task.started_at or _now(),
            message="开始处理…", progress=4, error=None, flush=True,
        )
        await self.broadcast(self.store.get(task_id))

        try:
            await self._execute(task_id, cancel)
        except Cancelled:
            self.store.update(
                task_id, status=CANCELLED, stage="cancelled",
                message="已取消", flush=True,
            )
            await self.broadcast(self.store.get(task_id))
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}")
            raw = strip_ansi(str(e))
            err = friendly_error(raw)
            task = self.store.get(task_id)
            fail_stage = (task.stage if task else None) or "parse"
            if fail_stage == "converting":
                fail_stage = "downloading"
            if fail_stage in ("queued", "preparing", "failed"):
                fail_stage = "parse"
            auth = is_login_required(raw) or is_login_required(err)
            if is_charge_need_login(raw) or is_charge_need_login(err):
                err = CHARGE_NEED_LOGIN_HINT
                self.store.update(
                    task_id, status=NEEDS_LOGIN, stage="downloading",
                    error=err, message=err, flush=True,
                )
            elif auth:
                err = LOGIN_PARSE_HINT
                self.store.update(
                    task_id, status=NEEDS_LOGIN, stage="parse",
                    error=err, message=err, flush=True,
                )
            else:
                self.store.update(
                    task_id, status=FAILED, stage=fail_stage,
                    error=err, message=err, finished_at=_now(), flush=True,
                )
            await self.broadcast(self.store.get(task_id))

    def _merge_detail(self, task_id: str, **fields) -> dict:
        task = self.store.get(task_id)
        base = dict(task.detail or {}) if task else {}
        keep = base.get("model_step")
        if "eta_seconds" in fields and "eta_deadline" not in fields:
            fields.update(eta.stamp(fields.get("eta_seconds")))
        base.update(fields)
        if keep and "model_step" not in fields:
            base["model_step"] = keep
        return base

    def _freeze_budget(self, task_id: str, duration: Optional[float] = None) -> None:
        """Stamp the whole-job prior once, when media duration is first known."""
        task = self.store.get(task_id)
        if not task:
            return
        detail = dict(task.detail or {})
        if detail.get("eta_budget") is not None:
            return
        dur = duration or task.duration or detail.get("total_seconds")
        have_file = bool(task.audio_path) and Path(str(task.audio_path)).exists()
        total = eta.total_wait(
            dur, _download_kind(task), task.origin, settings_store.whisper_speed(),
            include_download=not have_file,
        )
        if not total:
            return
        now = time.time()
        detail.update(eta.stamp(total, now))
        detail["eta_budget"] = float(total)
        detail["eta_budget_at"] = now
        extra = {"detail": detail}
        if duration and not task.duration:
            extra["duration"] = duration
        self.store.update(task_id, flush=True, **extra)

    def _blend_eta(self, task_id: str, measured: Optional[float]) -> float:
        """Job remaining: stage sample + later steps, capped, never increases."""
        self._freeze_budget(task_id)
        task = self.store.get(task_id)
        now = time.time()
        prev = eta.remain_from_detail(task.detail if task else None, now)
        prev_at = (task.detail or {}).get("eta_at") if task else None
        dt = (now - float(prev_at)) if prev_at else 0.0
        trailing = 0.0
        if task and (task.detail or {}).get("eta_budget") is not None:
            trailing = _trailing_remain(task)
        measured_job = None if measured is None else max(0.0, float(measured) + trailing)
        blended = eta.smooth(prev, measured_job, dt)
        cap = eta.budget_remain(task.detail if task else None, now)
        if blended is None:
            blended = cap if cap is not None else (measured_job if measured_job is not None else (prev or 0.0))
        elif cap is not None:
            blended = min(blended, cap)
        return float(max(0.0, blended))

    async def _ensure_model(self, task_id: str, cancel: threading.Event) -> None:
        """Download local weights before parsing, so the user can quit early."""
        settings = settings_store.get_all()
        engine = engines.resolve_engine(settings)
        backend = engine.info.backend
        if backend not in ("mlx", "faster-whisper"):
            return
        if backend == "mlx":
            import model_fetch
            repo = getattr(engine, "model_repo", config.MLX_MODEL)
            if model_fetch.mlx_model_ready(repo, config.MODELS_DIR):
                return
        elif getattr(engine, "_model", None) is not None or getattr(engine, "_loaded", False):
            return

        started = time.time()
        loop = asyncio.get_running_loop()
        last = [0.0]

        def report(filename: str, done: int, total: int) -> None:
            if cancel.is_set():
                return
            now = time.time()
            if now - last[0] < 0.35 and total and done < total:
                return
            last[0] = now
            elapsed = max(now - started, 0.4)
            measured = eta.from_bytes(done, total or 0, elapsed)
            if measured is None:
                measured = max(eta.prior_model() - (now - started), 0.0)
            frac = (done / total) if total else None

            def apply() -> None:
                remain = self._blend_eta(task_id, measured)
                self.store.update(
                    task_id,
                    stage="downloading_model",
                    progress=round(5 + (12 * (frac or 0)), 1),
                    message="正在下载语音模型…",
                    detail=self._merge_detail(
                        task_id,
                        model_step="current",
                        downloaded_bytes=done,
                        total_bytes=total,
                        fraction=frac,
                        eta_seconds=remain,
                    ),
                    flush=True,
                )
                task = self.store.get(task_id)
                if task:
                    asyncio.create_task(self.broadcast(task))

            loop.call_soon_threadsafe(apply)

        self.store.update(
            task_id,
            stage="downloading_model",
            progress=5,
            message="正在准备语音模型…",
            detail=self._merge_detail(task_id, model_step="current", fraction=0, eta_seconds=eta.prior_model()),
            flush=True,
        )
        await self.broadcast(self.store.get(task_id))

        def load() -> None:
            if cancel.is_set():
                raise Cancelled()
            engine.load(progress=report, cancel=cancel)
            if cancel.is_set():
                raise Cancelled()

        try:
            await asyncio.to_thread(load)
        except InterruptedError:
            raise Cancelled()
        except Exception:
            if cancel.is_set():
                raise Cancelled()
            raise

        latest = self.store.get(task_id)
        next_stage = "prepare" if latest and latest.source_type == "upload" else "parse"
        next_msg = "模型已就绪，正在准备文件…" if next_stage == "prepare" else "模型已就绪，正在解析链接…"
        self.store.update(
            task_id,
            stage=next_stage,
            progress=14,
            message=next_msg,
            detail=self._merge_detail(
                task_id,
                model_step="done",
                fraction=None,
                eta_seconds=None,
                downloaded_bytes=None,
                total_bytes=None,
            ),
            flush=True,
        )
        await self.broadcast(self.store.get(task_id))

    async def _run_with_eta(
        self,
        task_id: str,
        stage: str,
        message: str,
        estimate: float,
        work,
        watch_path: Optional[Path] = None,
        expected_bytes: Optional[int] = None,
    ):
        start = time.time()
        running = True
        last_size = [0]
        size_t = [start]

        async def tick() -> None:
            while running:
                now = time.time()
                measured = max(estimate - (now - start), 0.0)
                if watch_path and expected_bytes:
                    try:
                        sz = Path(watch_path).stat().st_size if Path(watch_path).exists() else 0
                    except OSError:
                        sz = 0
                    if sz > last_size[0] + 8192:
                        dt = max(now - size_t[0], 0.3)
                        rate = (sz - last_size[0]) / dt
                        last_size[0] = sz
                        size_t[0] = now
                        sample = eta.from_rate(sz, expected_bytes, rate)
                        if sample is not None:
                            measured = sample
                remain = self._blend_eta(task_id, measured)
                frac = min(0.9, (now - start) / max(estimate, 1.0))
                self.store.update(
                    task_id,
                    stage=stage,
                    message=message,
                    detail=self._merge_detail(
                        task_id,
                        fraction=frac,
                        eta_seconds=remain,
                    ),
                    flush=True,
                )
                await self.broadcast(self.store.get(task_id))
                await asyncio.sleep(1.0)

        ticker = asyncio.create_task(tick())
        try:
            return await work()
        finally:
            running = False
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker

    async def _execute(self, task_id: str, cancel: threading.Event) -> None:
        task = self.store.get(task_id)
        assert task is not None
        if cancel.is_set():
            raise Cancelled()

        hint = audio_utils.check_ffmpeg()
        if hint:
            raise RuntimeError(hint)

        await self._ensure_model(task_id, cancel)
        if cancel.is_set():
            raise Cancelled()
        task = self.store.get(task_id)
        assert task is not None

        segments: Optional[List[Segment]] = None
        origin = "whisper"
        language = task.language
        duration = task.duration
        title = task.title
        engine_display = None
        elapsed = None
        speed_x = None
        punctuates = False

        # Fast path: already have audio from a previous interrupted run.
        audio_path = Path(task.audio_path) if task.audio_path else None
        have_audio = audio_path is not None and audio_path.exists()

        if not have_audio:
            if task.source_type == "upload":
                if not audio_path or not audio_path.exists():
                    raise RuntimeError("上传文件已丢失，请重新上传")
                await self._note(task_id, 10, "converting", "正在转换音频格式…")
                dest = config.AUDIO_DIR / f"{task_id}.m4a"
                converted = await asyncio.to_thread(
                    audio_utils.transcode_to_m4a, audio_path, dest
                )
                audio_path = Path(converted)
                title = title or safe_filename(audio_path.stem)
            elif task.source_type == "podcast" or is_podcast_url(task.source):
                async def _parse_podcast():
                    return await extract_podcast_audio(task.source)
                info = await self._run_with_eta(
                    task_id, "parse", "正在解析分享链接…", eta.prior_parse(), _parse_podcast,
                )
                title = info.title if is_real_title(info.title or "") else title
                duration = info.duration or duration
                if duration:
                    self.store.update(task_id, duration=duration, title=title, flush=True)
                self._freeze_budget(task_id, duration)
                dl_guess = eta.prior_download(duration, "podcast")
                dest = config.AUDIO_DIR / f"{task_id}.m4a"
                await self._note(
                    task_id, 16, "downloading",
                    f"正在下载音频：{(title or '')[:40]}",
                    title=title, duration=duration,
                    detail=self._merge_detail(task_id, fraction=0),
                )
                async def _dl_podcast():
                    return await asyncio.to_thread(
                        audio_utils.download_audio_url_to_m4a,
                        info.audio_url, dest,
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    )
                converted = await self._run_with_eta(
                    task_id, "downloading", "正在下载音频…", dl_guess, _dl_podcast,
                    watch_path=dest,
                    expected_bytes=eta.expected_aac_bytes(duration),
                )
                audio_path = Path(converted)
            else:
                async def _parse_url():
                    return await self.video.fetch_subtitles(task.source, config.TEMP_DIR)
                sub_segs, sub_title, sub_lang, sub_dur, charge = await self._run_with_eta(
                    task_id, "parse", "正在解析链接…", eta.prior_parse(), _parse_url,
                )
                title = sub_title if is_real_title(sub_title or "") else title
                duration = sub_dur or duration
                if duration:
                    self.store.update(task_id, duration=duration, title=title, flush=True)
                if sub_segs:
                    segments = sub_segs
                    origin = "subtitle"
                    language = sub_lang or language
                    punctuates = True
                    self.store.update(task_id, origin=origin, flush=True)
                    self._freeze_budget(task_id, duration)
                    await self._note(
                        task_id, 55, "subtitles",
                        f"已提取字幕（{sub_lang or '未知'}），正在整理段落…",
                        title=title, language=language, origin=origin,
                        duration=duration,
                    )
                else:
                    if charge:
                        await self._note(
                            task_id, 16, "downloading", CHARGE_NEED_LOGIN_HINT,
                            title=title, duration=duration,
                        )
                        raise RuntimeError(CHARGE_NEED_LOGIN_HINT)
                    self._freeze_budget(task_id, duration)
                    loop = asyncio.get_running_loop()
                    last = [0.0]
                    t0 = [time.time()]
                    guess = [eta.prior_download(duration, "video")]

                    def on_dl(info: dict) -> None:
                        now = time.time()
                        if now - last[0] < 0.4 and info.get("phase") != "convert":
                            return
                        last[0] = now

                        def apply() -> None:
                            done = int(info.get("downloaded_bytes") or 0)
                            total = int(info.get("total_bytes") or 0)
                            ytdlp_eta = info.get("eta")
                            speed = info.get("speed")
                            frac = info.get("fraction")
                            if total:
                                frac = min(1.0, done / total)
                            if info.get("phase") == "convert":
                                measured = eta.prior_finalize()
                            elif ytdlp_eta is not None:
                                try:
                                    measured = float(ytdlp_eta)
                                except (TypeError, ValueError):
                                    measured = None
                            else:
                                measured = eta.from_bytes(done, total, max(time.time() - t0[0], 0.2))
                                if measured is None and speed and duration:
                                    expected = max(int(float(duration) * 20000), done + 1)
                                    measured = eta.from_rate(done, expected, float(speed))
                                if measured is None:
                                    measured = max(guess[0] - (time.time() - t0[0]), 0.0)
                            remain = self._blend_eta(task_id, measured)
                            self.store.update(
                                task_id,
                                stage="converting" if info.get("phase") == "convert" else "downloading",
                                progress=round(18 + 12 * (frac or 0), 1),
                                message="正在转码音频…" if info.get("phase") == "convert" else "正在下载音频…",
                                title=title,
                                detail=self._merge_detail(
                                    task_id,
                                    downloaded_bytes=done or None,
                                    total_bytes=total or None,
                                    fraction=frac,
                                    eta_seconds=remain,
                                    phase=info.get("phase") or "download",
                                ),
                                flush=True,
                            )
                            latest = self.store.get(task_id)
                            if latest:
                                asyncio.create_task(self.broadcast(latest))

                        loop.call_soon_threadsafe(apply)

                    await self._note(
                        task_id, 16, "downloading", "正在下载音频…",
                        title=title, duration=duration,
                        detail=self._merge_detail(task_id, fraction=0),
                    )
                    downloaded, dl_title = await self.video.download_and_convert(
                        task.source, config.AUDIO_DIR,
                        prefetched_title=title if is_real_title(title or "") else None,
                        on_progress=on_dl,
                    )
                    audio_path = Path(downloaded)
                    title = dl_title if is_real_title(dl_title or "") else title

            if audio_path:
                self.store.update(task_id, audio_path=str(audio_path), title=title, flush=True)

        if segments is None:
            if audio_path is None or not audio_path.exists():
                raise RuntimeError("没有可转录的音频")
            if duration is None:
                try:
                    duration = await asyncio.to_thread(audio_utils.probe_duration, audio_path)
                except Exception:
                    duration = None
            self.store.update(
                task_id, duration=duration, origin="whisper",
                title=title, audio_path=str(audio_path), stage="transcribing",
                flush=True,
            )
            self._freeze_budget(task_id, duration)
            transcribe_eta = self._blend_eta(
                task_id,
                eta.prior_transcribe(duration, 0, None, settings_store.whisper_speed()),
            )
            await self._note(
                task_id, 20, "transcribing",
                f"开始转录{(' · ' + humanize_duration(duration)) if duration else ''}…",
                detail=self._merge_detail(
                    task_id,
                    stage="transcribing",
                    total_seconds=duration,
                    processed_seconds=0,
                    speed_x=settings_store.whisper_speed(),
                    eta_seconds=transcribe_eta,
                    fraction=0,
                    downloaded_bytes=None,
                    total_bytes=None,
                    phase=None,
                ),
            )

            settings = settings_store.get_all()
            engine = engines.resolve_engine(settings)

            async def on_progress(p: Progress):
                if cancel.is_set():
                    return
                payload = p.to_dict()
                if payload.get("eta_seconds") is not None:
                    payload["eta_seconds"] = self._blend_eta(
                        task_id, float(payload["eta_seconds"]),
                    )
                msg = p.message
                if payload.get("eta_seconds"):
                    msg = f"{p.message} · 预计剩余 {humanize_duration(payload['eta_seconds'])}"
                self.store.update(
                    task_id,
                    progress=round(_map_progress(p), 1),
                    stage=p.stage,
                    message=msg,
                    detail=self._merge_detail(task_id, **payload),
                    segment_count=p.segment_count,
                    engine=engine.info.display,
                    flush=True,
                )
                await self.broadcast(self.store.get(task_id))

            result = await run_transcription(
                audio_path,
                engine=engine,
                language=language if (language or "").lower().startswith("zh") else None,
                work_dir=config.TEMP_DIR / "chunks" / task_id,
                partial_path=self.store.partial_path(task_id),
                cancel_event=cancel,
                on_progress=on_progress,
                word_timestamps=bool(settings.get("word_timestamps")),
                condition_on_previous_text=bool(settings.get("condition_on_previous_text")),
            )
            segments = result.segments
            language = result.language or language
            duration = result.duration or duration
            engine_display = result.engine_display
            elapsed = result.elapsed_seconds
            speed_x = result.speed_x
            punctuates = result.engine_punctuates
            origin = "whisper"
            if speed_x:
                settings_store.remember_whisper_speed(speed_x)

        if cancel.is_set():
            raise Cancelled()

        await self._note(
            task_id, 94, "finalizing", "正在整理段落并导出…",
            detail=self._merge_detail(
                task_id, fraction=0.5, eta_seconds=self._blend_eta(task_id, eta.prior_finalize()),
            ),
        )
        meta = formats.TranscriptMeta(
            title=title or "未命名",
            source=task.source,
            language=language,
            duration=duration,
            engine=engine_display or origin,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            elapsed_seconds=elapsed,
            engine_punctuates=punctuates,
            paragraph_min_chars=int(settings_store.get("paragraph_min_chars") or 120),
            paragraph_max_chars=int(settings_store.get("paragraph_max_chars") or 300),
        )
        latest = self.store.get(task_id)
        files = self.store.write_exports(latest, segments, meta)
        self.store.clear_partial(task_id)
        self.store.update(
            task_id,
            status=COMPLETED,
            stage="completed",
            progress=100,
            message="转录完成",
            title=meta.title,
            language=language,
            duration=duration,
            engine=engine_display or origin,
            origin=origin,
            files=files,
            segment_count=len(segments),
            elapsed_seconds=elapsed,
            speed_x=speed_x,
            finished_at=_now(),
            detail={},
            flush=True,
        )
        self.store.prune()
        await self.broadcast(self.store.get(task_id))

    async def _note(self, task_id: str, progress: float, stage: str, message: str, **extra) -> None:
        self.store.update(
            task_id, progress=progress, stage=stage, message=message, flush=True, **extra
        )
        task = self.store.get(task_id)
        if task:
            await self.broadcast(task)
