"""Benchmark faster-whisper configurations on a real Chinese audio clip."""
import os, sys, time, json
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from faster_whisper import WhisperModel

CLIP = sys.argv[1] if len(sys.argv) > 1 else "bench/clip4m.wav"
AUDIO_SECONDS = 240.0

CURRENT = dict(
    beam_size=5, best_of=5, temperature=[0.0, 0.2, 0.4],
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 900, "speech_pad_ms": 300},
    no_speech_threshold=0.7, compression_ratio_threshold=2.3,
    log_prob_threshold=-1.0, condition_on_previous_text=False,
)
GREEDY = dict(CURRENT, beam_size=1, best_of=1, temperature=[0.0])

CONFIGS = [
    ("base",   "current(beam5)", CURRENT),
    ("base",   "greedy(beam1)",  GREEDY),
    ("medium", "current(beam5)", CURRENT),
    ("medium", "greedy(beam1)",  GREEDY),
]

results = []
for model_size, label, params in CONFIGS:
    print(f"\n{'='*60}\n{model_size} / {label}", flush=True)
    t0 = time.time()
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    load_t = time.time() - t0

    t1 = time.time()
    segments, info = model.transcribe(CLIP, language="zh", **params)
    text = "".join(s.text for s in segments)   # generator drains here
    infer_t = time.time() - t1

    rtf = AUDIO_SECONDS / infer_t
    print(f"  load={load_t:.1f}s  infer={infer_t:.1f}s  speed={rtf:.2f}x realtime")
    print(f"  est. for 98min audio: {5932/rtf/60:.1f} min")
    print(f"  sample: {text[:160]}")
    results.append(dict(model=model_size, config=label, load_s=round(load_t, 1),
                        infer_s=round(infer_t, 1), rtf=round(rtf, 2),
                        est_98min_minutes=round(5932 / rtf / 60, 1),
                        text=text))
    del model

with open("bench/results_fw.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\nsaved bench/results_fw.json")
