"""Benchmark MLX Whisper on Apple GPU against the real Chinese clip.

Usage:
    python bench/bench_mlx.py <repo> <tag> [audio] [audio_seconds]
"""
import os, sys, time, json, resource

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import mlx.core as mx
import mlx_whisper

REPO = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/whisper-large-v3-turbo"
TAG = sys.argv[2] if len(sys.argv) > 2 else "turbo"
CLIP = sys.argv[3] if len(sys.argv) > 3 else "bench/clip4m.wav"
AUDIO_SECONDS = float(sys.argv[4]) if len(sys.argv) > 4 else 240.0
FULL_SECONDS = 5931.84

OPTS = dict(
    language="zh",
    temperature=(0.0, 0.2, 0.4),
    condition_on_previous_text=False,
    compression_ratio_threshold=2.3,
    logprob_threshold=-1.0,
    no_speech_threshold=0.7,
    word_timestamps=False,
    verbose=None,
)


def rss_gb():
    # macOS reports ru_maxrss in bytes
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def run(label):
    mx.reset_peak_memory()
    t0 = time.time()
    out = mlx_whisper.transcribe(CLIP, path_or_hf_repo=REPO, **OPTS)
    dt = time.time() - t0
    mlx_peak = mx.get_peak_memory() / 1e9
    proc_peak = rss_gb()
    # https://github.com/ml-explore/mlx-examples/issues/1254 -- GPU cache leak
    mx.clear_cache()

    rtf = AUDIO_SECONDS / dt
    segs = [
        dict(start=round(s["start"], 2), end=round(s["end"], 2), text=s["text"])
        for s in out["segments"]
    ]
    rec = dict(
        engine="mlx-whisper", repo=REPO, tag=TAG, label=label,
        seconds=round(dt, 1), rtf=round(rtf, 2),
        est_98min_minutes=round(FULL_SECONDS / rtf / 60, 1),
        mlx_peak_gb=round(mlx_peak, 2), proc_peak_rss_gb=round(proc_peak, 2),
        n_segments=len(segs), text=out["text"], segments=segs,
    )
    print(f"[{TAG}/{label}] {dt:.1f}s  {rtf:.2f}x realtime  "
          f"est_98min={rec['est_98min_minutes']}min  "
          f"mlx_peak={mlx_peak:.2f}GB  rss_peak={proc_peak:.2f}GB  "
          f"segs={len(segs)}", flush=True)
    print(f"[{TAG}/{label}] {out['text'][:200]}", flush=True)
    return rec


print(f"repo={REPO}  clip={CLIP}  dur={AUDIO_SECONDS}s", flush=True)
results = [run("cold"), run("warm")]

os.makedirs("bench", exist_ok=True)
with open(f"bench/results_mlx_{TAG}.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
with open(f"bench/text_mlx_{TAG}.txt", "w") as f:
    f.write(results[-1]["text"].strip() + "\n")
with open(f"bench/segs_mlx_{TAG}.txt", "w") as f:
    for s in results[-1]["segments"]:
        f.write(f"[{s['start']:7.2f} - {s['end']:7.2f}] {s['text'].strip()}\n")
print(f"saved bench/results_mlx_{TAG}.json", flush=True)
