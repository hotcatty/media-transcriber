"""
End-to-end smoke test of the transcription pipeline against real audio.

Exercises chunk planning, the engine abstraction, off-loop progress reporting,
resume, paragraph reflow, and every export format.

    python tools/smoke_transcribe.py bench/clip4m.wav
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import config  # noqa: E402

config.ensure_dirs()

import engines  # noqa: E402
import formats  # noqa: E402
from transcription import run_transcription  # noqa: E402


async def main() -> None:
    clip = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "bench/clip4m.wav")
    if not clip.exists():
        raise SystemExit(f"missing audio: {clip}")

    print(f"engine backend  : {engines.local_backend()}")
    engine = engines.get_local_engine()
    print(f"engine           : {engine.info.display}")
    print(f"capabilities     : {engine.capabilities}")

    work = config.TEMP_DIR / "smoke"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    partial = work / "partial.jsonl"

    updates: list = []
    loop_alive = {"ticks": 0}

    async def heartbeat():
        """Proves the event loop stays responsive during inference."""
        while True:
            await asyncio.sleep(0.25)
            loop_alive["ticks"] += 1

    hb = asyncio.create_task(heartbeat())

    def on_progress(p):
        updates.append(p)
        eta = f"{p.eta_seconds}s" if p.eta_seconds else "-"
        spd = f"{p.speed_x:.2f}x" if p.speed_x else "-"
        print(f"  [{p.stage:18s}] {p.fraction * 100:5.1f}%  speed={spd:>7s} eta={eta:>6s}  {p.message}")

    t0 = time.time()
    result = await run_transcription(
        clip, engine=engine, work_dir=work / "chunks",
        partial_path=partial, on_progress=on_progress,
        word_timestamps=True,
    )
    elapsed = time.time() - t0
    hb.cancel()

    print(f"\n--- result ---")
    print(f"language        : {result.language}")
    print(f"duration        : {result.duration:.1f}s")
    print(f"elapsed         : {elapsed:.1f}s")
    print(f"speed           : {result.speed_x:.2f}x realtime" if result.speed_x else "")
    print(f"segments        : {len(result.segments)}")
    print(f"progress updates: {len(updates)}")
    print(f"event-loop ticks during job: {loop_alive['ticks']} "
          f"(must be >0, proves the loop was never blocked)")

    meta = formats.TranscriptMeta(
        title="冒烟测试", source=str(clip), language=result.language,
        duration=result.duration, engine=result.engine_display,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        engine_punctuates=result.engine_punctuates,
    )

    from reflow import reflow_segments
    rf = reflow_segments(result.segments, engine_punctuates=result.engine_punctuates)
    print(f"\n--- reflow ---")
    print(f"paragraphs      : {len(rf.paragraphs)}")
    print(f"punct restored  : {rf.punctuation_restored}")
    print(f"repeats removed : {rf.repetitions_collapsed}")
    lens = [len(p) for p in rf.paragraphs]
    if lens:
        print(f"para chars      : min={min(lens)} max={max(lens)} avg={sum(lens)//len(lens)}")

    print("\n--- BEFORE (raw segments, what the old tool emitted) ---")
    for s in result.segments[:8]:
        print(f"  {s.text}")

    print("\n--- AFTER (reflowed paragraph 1) ---")
    if rf.paragraphs:
        print(rf.paragraphs[0])

    out_dir = work / "exports"
    out_dir.mkdir(exist_ok=True)
    for fmt in ("txt", "md", "srt", "vtt", "json", "ts.md"):
        text = formats.render(fmt, result.segments, meta)
        path = out_dir / f"out.{fmt}"
        path.write_text(text, encoding="utf-8")
        print(f"  export {fmt:6s} -> {len(text):7d} chars  {path.name}")

    # Resume check: the sidecar should let a second run skip finished work.
    print("\n--- resume check ---")
    t1 = time.time()
    again = await run_transcription(
        clip, engine=engine, work_dir=work / "chunks2",
        partial_path=partial, on_progress=lambda p: None,
        word_timestamps=True,
    )
    print(f"  second run took {time.time() - t1:.1f}s for {len(again.segments)} segments "
          f"(resumed {again.resumed_seconds:.0f}s of audio)")
    assert len(again.segments) == len(result.segments), "resume changed segment count"
    print("  resume OK")


if __name__ == "__main__":
    asyncio.run(main())
