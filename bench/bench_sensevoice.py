"""Benchmark FunASR SenseVoice-Small (+FSMN-VAD) on the real Chinese clip.

Models are pulled from ModelScope (better throughput from mainland China than HF).

Usage:
    python bench/bench_sensevoice.py [device] [audio] [audio_seconds]
"""
import os, sys, time, json, resource

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("MODELSCOPE_CACHE", os.path.expanduser("~/.cache/modelscope"))

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "cpu"
CLIP = sys.argv[2] if len(sys.argv) > 2 else "bench/clip4m.wav"
AUDIO_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0
FULL_SECONDS = 5931.84

from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess


def rss_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


print(f"device={DEVICE} clip={CLIP}", flush=True)

t0 = time.time()
model = AutoModel(
    model="iic/SenseVoiceSmall",
    vad_model="fsmn-vad",
    vad_kwargs={"max_single_segment_time": 30000},  # SenseVoice caps at 30s input
    device=DEVICE,
    hub="ms",
    disable_update=True,
)
load_t = time.time() - t0
print(f"load(+download) = {load_t:.1f}s  rss={rss_gb():.2f}GB", flush=True)


def run(label, **kw):
    t = time.time()
    res = model.generate(
        input=CLIP, cache={}, language="zh", use_itn=True,
        batch_size_s=60, **kw,
    )
    dt = time.time() - t
    rtf = AUDIO_SECONDS / dt
    peak = rss_gb()
    print(f"[{label}] {dt:.1f}s  {rtf:.2f}x realtime  "
          f"est_98min={FULL_SECONDS / rtf / 60:.1f}min  rss_peak={peak:.2f}GB  "
          f"n_items={len(res)}", flush=True)
    return res, dt, rtf, peak


# --- merged output (one blob of text, what you'd feed an LLM) ---
res_m, dt_m, rtf_m, peak_m = run("merged/cold", merge_vad=True, merge_length_s=15)
res_m2, dt_m2, rtf_m2, peak_m2 = run("merged/warm", merge_vad=True, merge_length_s=15)
merged_raw = res_m2[0]["text"]
merged_text = rich_transcription_postprocess(merged_raw)
print(f"raw head : {merged_raw[:180]}", flush=True)
print(f"clean    : {merged_text[:180]}", flush=True)

# --- unmerged: one item per VAD segment, to inspect timestamp availability ---
res_u, dt_u, rtf_u, peak_u = run("per-vad-seg", merge_vad=False)
print(f"per-seg item keys: {list(res_u[0].keys())}", flush=True)
print(f"per-seg sample   : {json.dumps(res_u[0], ensure_ascii=False)[:400]}", flush=True)

segments = []
for it in res_u:
    key = it.get("key", "")
    txt = rich_transcription_postprocess(it.get("text", ""))
    # FunASR encodes the VAD window into the key as ..._<start_ms>_<end_ms>
    start = end = None
    parts = key.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        start, end = int(parts[1]) / 1000.0, int(parts[2]) / 1000.0
    segments.append(dict(key=key, start=start, end=end, text=txt,
                         raw=it.get("text", "")))

out = dict(
    engine="sensevoice-small", device=DEVICE,
    load_s=round(load_t, 1),
    merged_cold=dict(seconds=round(dt_m, 1), rtf=round(rtf_m, 2),
                     est_98min_minutes=round(FULL_SECONDS / rtf_m / 60, 1),
                     rss_peak_gb=round(peak_m, 2)),
    merged_warm=dict(seconds=round(dt_m2, 1), rtf=round(rtf_m2, 2),
                     est_98min_minutes=round(FULL_SECONDS / rtf_m2 / 60, 1),
                     rss_peak_gb=round(peak_m2, 2)),
    cold_total_s=round(load_t + dt_m, 1),
    per_seg=dict(seconds=round(dt_u, 1), rtf=round(rtf_u, 2),
                 n_segments=len(segments), rss_peak_gb=round(peak_u, 2)),
    raw_text=merged_raw, text=merged_text, segments=segments,
)

os.makedirs("bench", exist_ok=True)
tag = f"sensevoice_{DEVICE}"
with open(f"bench/results_{tag}.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
with open(f"bench/text_{tag}.txt", "w") as f:
    f.write(merged_text.strip() + "\n")
with open(f"bench/segs_{tag}.txt", "w") as f:
    for s in segments:
        st = f"{s['start']:7.2f}" if s["start"] is not None else "   ?   "
        en = f"{s['end']:7.2f}" if s["end"] is not None else "   ?   "
        f.write(f"[{st} - {en}] {s['text']}\n")
print(f"saved bench/results_{tag}.json", flush=True)
