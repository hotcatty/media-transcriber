#!/usr/bin/env python3
"""One-shot CLI for the AI skill: URL or local file -> transcript files."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import audio as audio_utils  # noqa: E402
import config  # noqa: E402
from jobs import JobManager  # noqa: E402
from task_store import (  # noqa: E402
    CANCELLED,
    COMPLETED,
    FAILED,
    NEEDS_LOGIN,
    TaskStore,
)

DONE = {COMPLETED, FAILED, NEEDS_LOGIN, CANCELLED}
COPY_AS = {
    "txt": "transcript.txt",
    "md": "transcript.md",
    "srt": "transcript.srt",
    "json": "meta.json",
}


def _default_out() -> Path:
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    return Path.home() / "transcripts" / stamp


def _is_file(raw: str) -> bool:
    p = Path(raw).expanduser()
    return p.exists() and p.is_file()


def _write_info(out: Path, task) -> None:
    lines = [
        f"# {task.title or '未命名'}",
        "",
        f"- 来源：{task.source}",
        f"- 状态：{task.status}",
    ]
    if task.duration:
        lines.append(f"- 时长：{task.duration:.0f} 秒")
    if task.language:
        lines.append(f"- 语言：{task.language}")
    if task.engine:
        lines.append(f"- 引擎：{task.engine}")
    if task.origin:
        lines.append(f"- 来源类型：{task.origin}")
    if task.error:
        lines.append(f"- 说明：{task.error}")
    (out / "info.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_timed(out: Path) -> None:
    meta_path = out / "meta.json"
    if not meta_path.exists():
        return
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    segs = data.get("segments") or []
    title = data.get("title") or "文字稿"
    source = data.get("source") or ""
    lines = [f"# {title}", "", f"来源：{source}", "", "## 正文（带时间戳）", ""]
    for s in segs:
        start = s.get("start")
        text = (s.get("text") or "").strip()
        if start is None:
            stamp = "--:--"
        else:
            total = int(float(start))
            stamp = f"{total // 60:02d}:{total % 60:02d}"
        lines.append(f"**[{stamp}]** {text}")
        lines.append("")
    (out / "transcript-timed.md").write_text("\n".join(lines), encoding="utf-8")


def _copy_exports(store: TaskStore, task, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for fmt, name in COPY_AS.items():
        src = store.read_export(task.id, fmt)
        if src and src.exists():
            shutil.copy2(src, out / name)
    _write_info(out, task)
    _write_timed(out)


async def _wait(store: TaskStore, task_id: str):
    last = ""
    while True:
        task = store.get(task_id)
        if task is None:
            raise SystemExit("任务丢失")
        msg = task.message or task.stage or task.status
        if msg != last:
            print(msg, file=sys.stderr, flush=True)
            last = msg
        if task.status in DONE:
            return task
        await asyncio.sleep(0.8)


async def _run(source: str, out: Path) -> int:
    hint = audio_utils.check_ffmpeg()
    if hint:
        print(hint, file=sys.stderr)
        return 1

    config.ensure_dirs()
    store = TaskStore()
    jobs = JobManager(store)
    jobs.start()
    try:
        if _is_file(source):
            src = Path(source).expanduser().resolve()
            dest = config.UPLOAD_DIR / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.resolve() != src:
                shutil.copy2(src, dest)
            task = await jobs.submit_upload(dest, src.name)
        else:
            task = await jobs.submit_url(source)
        task = await _wait(store, task.id)
    finally:
        jobs.stop()

    _copy_exports(store, task, out)

    if task.status == COMPLETED:
        txt = out / "transcript.txt"
        body = txt.read_text(encoding="utf-8") if txt.exists() else ""
        print(f"OK\nout: {out}")
        print(f"title: {task.title or ''}")
        print("--- transcript.txt ---")
        print(body.rstrip() or "(空)")
        if len(body.strip()) < 30:
            print(
                "WARN: 文稿过短，多半是没口播或纯字卡。不要把这段当内容。",
                file=sys.stderr,
            )
        return 0

    err = task.error or task.message or task.status
    print(err, file=sys.stderr)
    print(f"out: {out}")
    if task.status == NEEDS_LOGIN:
        return 2
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="把链接或本地音视频转成文字稿")
    parser.add_argument("source", help="分享链接或本地文件路径")
    parser.add_argument("--out", default="", help="输出目录，默认 ~/transcripts/<时间>/")
    args = parser.parse_args()
    out = Path(args.out).expanduser() if args.out else _default_out()
    return asyncio.run(_run(args.source.strip(), out))


if __name__ == "__main__":
    raise SystemExit(main())
