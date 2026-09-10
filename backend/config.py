"""Central configuration. Everything overridable via environment variables."""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# turbo is ~1.6G; leave a little headroom so a nearly-full disk is skipped.
_MODEL_NEED_BYTES = 2 * 1024 * 1024 * 1024


def default_models_dir() -> Path:
    """Where Whisper weights live.

    Mac/Linux: ~/.cache/media-transcriber/models
    Windows: a fixed drive that is not C: when one has enough space
    (D:\\media-transcriber\\models, or the roomiest other disk). Only C: is
    used when there is no other suitable drive.
    """
    override = os.getenv("MT_MODELS_DIR")
    if override:
        return Path(override)
    if platform.system() != "Windows":
        return Path.home() / ".cache" / "media-transcriber" / "models"
    return _windows_models_dir()


def _windows_models_dir() -> Path:
    import ctypes

    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    drive_fixed = 3
    scored: list[tuple[int, int, str]] = []
    for code in range(ord("D"), ord("Z") + 1):
        letter = chr(code)
        root = f"{letter}:\\"
        if get_drive_type(root) != drive_fixed:
            continue
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        if free < _MODEL_NEED_BYTES:
            continue
        scored.append((0 if letter == "D" else 1, -free, letter))
    if scored:
        scored.sort()
        return Path(f"{scored[0][2]}:/media-transcriber/models")
    return Path.home() / ".cache" / "media-transcriber" / "models"


TEMP_DIR = Path(os.getenv("MT_TEMP_DIR", PROJECT_ROOT / "temp"))
TRANSCRIPTS_DIR = TEMP_DIR / "transcripts"
AUDIO_DIR = TEMP_DIR / "audio"
UPLOAD_DIR = TEMP_DIR / "uploads"
TASKS_INDEX = TEMP_DIR / "tasks.json"
COOKIE_FILE = Path(os.getenv("MT_COOKIE_FILE", PROJECT_ROOT / "cookies.txt"))

MODELS_DIR = default_models_dir()

HOST = os.getenv("MT_HOST", "127.0.0.1")
PORT = int(os.getenv("MT_PORT", "8766"))

# HuggingFace is unreachable from mainland China; keep the mirror as the default
# for any library that still resolves models through the Hub.
os.environ.setdefault("HF_ENDPOINT", os.getenv("HF_ENDPOINT", "https://hf-mirror.com"))

IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# Engine selection: "auto" picks MLX on Apple Silicon, faster-whisper elsewhere.
ENGINE = os.getenv("MT_ENGINE", "auto").lower()

# Default models per backend. Apple Silicon uses MLX large-v3-turbo (~10×).
# Intel/Windows CPU cannot run MLX; large-v3 is ~2× (half an hour for an
# 80-minute show). `small` is the CPU default so wall time stays near 10×.
# `base` was measured to produce unusable Chinese (审美→神媒, 认知→任志).
MLX_MODEL = os.getenv("MT_MLX_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_WHISPER_MODEL = os.getenv("MT_FW_MODEL", "small")

# Chunking: long audio is split on silence so we can report real progress,
# support resume, and keep memory bounded.
CHUNK_TARGET_SECONDS = float(os.getenv("MT_CHUNK_SECONDS", "480"))   # 8 min
CHUNK_SEARCH_WINDOW = float(os.getenv("MT_CHUNK_SEARCH", "60"))      # ±60 s for a silence
MIN_CHUNK_SECONDS = float(os.getenv("MT_MIN_CHUNK_SECONDS", "120"))

# Wall-clock speed vs audio length. MLX turbo on Apple GPU is ~9–12×.
# faster-whisper `small` on CPU is the Intel/Windows target (~10×).
WHISPER_SPEED_X = float(
    os.getenv("MT_WHISPER_SPEED_X")
    or ("9.5" if IS_APPLE_SILICON else "10")
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
