"""Sweep MLX Whisper decode options to find the fastest config that still holds up.

Temperature fallback re-decodes any 30s window that trips the quality thresholds,
which can cost several x of throughput. This isolates that cost.
"""
import os, sys, time, json, resource

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import mlx.core as mx
import mlx_whisper

MODEL = sys.argv[1] if len(sys.argv) > 1 else "bench/models/whisper-large-v3-turbo"
TAG = sys.argv[2] if len(sys.argv) > 2 else "turbo"
CLIP = sys.argv[3] if len(sys.argv) > 3 else "bench/clip4m.wav"
AUDIO_SECONDS = float(sys.argv[4]) if len(sys.argv) > 4 else 240.0
FULL_SECONDS = 5931.84

BASE = dict(language="zh", condition_on_previous_text=False,
            word_timestamps=False, verbose=None)

VARIANTS = [
    ("fallback_3temp", dict(temperature=(0.0, 0.2, 0.4),
                            compression_ratio_threshold=2.3,
                            logprob_threshold=-1.0, no_speech_threshold=0.7)),
    ("greedy_t0_thresh", dict(temperature=0.0,
                              compression_ratio_threshold=2.3,
                              logprob_threshold=-1.0, no_speech_threshold=0.7)),
    ("greedy_t0_nothresh", dict(temperature=0.0,
                                compression_ratio_threshold=None,
                                logprob_threshold=None, no_speech_threshold=0.7)),
]

# warm the weights + Metal kernels so variant 1 isn't penalised
mlx_whisper.transcribe(CLIP, path_or_hf_repo=MODEL, language="zh",
                       temperature=0.0, verbose=None,
                       clip_timestamps=[0.0, 30.0])
mx.clear_cache()

results = []
for name, opts in VARIANTS:
    mx.reset_peak_memory()
    t0 = time.time()
    out = mlx_whisper.transcribe(CLIP, path_or_hf_repo=MODEL, **BASE, **opts)
    dt = time.time() - t0
    peak = mx.get_peak_memory() / 1e9
    mx.clear_cache()
    rtf = AUDIO_SECONDS / dt
    rec = dict(tag=TAG, variant=name, seconds=round(dt, 1), rtf=round(rtf, 2),
               est_98min_minutes=round(FULL_SECONDS / rtf / 60, 1),
               est_60min_minutes=round(3600 / rtf / 60, 1),
               mlx_peak_gb=round(peak, 2),
               rss_peak_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 2),
               n_segments=len(out["segments"]), text=out["text"],
               segments=[dict(start=round(s["start"], 2), end=round(s["end"], 2),
                              text=s["text"]) for s in out["segments"]])
    print(f"[{TAG}/{name}] {dt:.1f}s  {rtf:.2f}x  est98={rec['est_98min_minutes']}min  "
          f"est60={rec['est_60min_minutes']}min  peak={peak:.2f}GB  "
          f"segs={rec['n_segments']}", flush=True)
    results.append(rec)
    with open(f"bench/text_mlx_{TAG}_{name}.txt", "w") as f:
        f.write(out["text"].strip() + "\n")

with open(f"bench/results_mlx_sweep_{TAG}.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"saved bench/results_mlx_sweep_{TAG}.json", flush=True)
