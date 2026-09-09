"""Central configuration. Everything overridable via environment variables."""
from __future__ import annotations

import os
import platform
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEMP_DIR = Path(os.getenv("MT_TEMP_DIR", PROJECT_ROOT / "temp"))
TRANSCRIPTS_DIR = TEMP_DIR / "transcripts"
AUDIO_DIR = TEMP_DIR / "audio"
UPLOAD_DIR = TEMP_DIR / "uploads"
TASKS_INDEX = TEMP_DIR / "tasks.json"
COOKIE_FILE = Path(os.getenv("MT_COOKIE_FILE", PROJECT_ROOT / "cookies.txt"))

MODELS_DIR = Path(
    os.getenv("MT_MODELS_DIR", Path.home() / ".cache" / "media-transcriber" / "models")
)

HOST = os.getenv("MT_HOST", "127.0.0.1")
PORT = int(os.getenv("MT_PORT", "8766"))

# HuggingFace is unreachable from mainland China; keep the mirror as the default
# for any library that still resolves models through the Hub.
os.environ.setdefault("HF_ENDPOINT", os.getenv("HF_ENDPOINT", "https://hf-mirror.com"))

IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# Engine selection: "auto" picks MLX on Apple Silicon, faster-whisper elsewhere.
ENGINE = os.getenv("MT_ENGINE", "auto").lower()

# Default models per backend. large-v3-turbo is the quality/speed sweet spot;
# `base` was measured to produce unusable Chinese (审美→神媒, 认知→任志).
MLX_MODEL = os.getenv("MT_MLX_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_WHISPER_MODEL = os.getenv("MT_FW_MODEL", "large-v3")

# Chunking: long audio is split on silence so we can report real progress,
# support resume, and keep memory bounded.
CHUNK_TARGET_SECONDS = float(os.getenv("MT_CHUNK_SECONDS", "480"))   # 8 min
CHUNK_SEARCH_WINDOW = float(os.getenv("MT_CHUNK_SEARCH", "60"))      # ±60 s for a silence
MIN_CHUNK_SECONDS = float(os.getenv("MT_MIN_CHUNK_SECONDS", "120"))

# Wall-clock speed vs audio length. MLX turbo on Apple GPU is ~9–12×;
# faster-whisper on CPU was benched near 2.85×.
WHISPER_SPEED_X = float(
    os.getenv("MT_WHISPER_SPEED_X")
    or ("9.5" if IS_APPLE_SILICON else "2.85")
)

WORD_TIMESTAMPS = os.getenv("MT_WORD_TIMESTAMPS", "1") not in ("0", "false", "False")
CONDITION_ON_PREVIOUS_TEXT = os.getenv("MT_CONDITION_ON_PREV", "0") not in (
    "0", "false", "False",
)

UPLOAD_MAX_MB = int(os.getenv("MT_UPLOAD_MAX_MB", "2048"))
UPLOAD_ALLOWED_EXT = frozenset({
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v",
})

# Keep at most this many finished tasks in the index (their files are pruned too).
MAX_TASK_HISTORY = int(os.getenv("MT_MAX_HISTORY", "200"))


def ensure_dirs() -> None:
    for d in (TEMP_DIR, TRANSCRIPTS_DIR, AUDIO_DIR, UPLOAD_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
