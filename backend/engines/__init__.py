"""
Engine registry.

Resolution order: an explicitly configured cloud backend wins; otherwise MLX on
Apple Silicon, faster-whisper elsewhere. Local transcription always works
without credentials — cloud is strictly opt-in.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

import config

from .base import (
    AudioChunk,
    Capabilities,
    ChunkResult,
    EngineInfo,
    Segment,
    TranscriptionEngine,
    Word,
)
from . import cloud_engine, fw_engine, mlx_engine

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: Dict[tuple, TranscriptionEngine] = {}

MLX_CHOICES = [
    {"id": "mlx-community/whisper-large-v3-turbo", "label": "large-v3-turbo（推荐）",
     "size_mb": 1614, "note": "速度与准确率平衡，中文表现好"},
    {"id": "mlx-community/whisper-large-v3-mlx", "label": "large-v3（最准）",
     "size_mb": 3100, "note": "准确率略高，速度约慢一半"},
    {"id": "mlx-community/whisper-medium-mlx", "label": "medium",
     "size_mb": 1530, "note": "中文可用，明显不如 large 系列"},
    {"id": "mlx-community/whisper-small-mlx", "label": "small（快速预览）",
     "size_mb": 484, "note": "仅适合预览，中文错字较多"},
]

FW_CHOICES = [
    {"id": "small", "label": "small（推荐）", "size_mb": 484,
     "note": "CPU 上大约 10 倍速，中文会有错字"},
    {"id": "medium", "label": "medium", "size_mb": 1530,
     "note": "更准，CPU 上大约慢一倍"},
    {"id": "large-v3-turbo", "label": "large-v3-turbo", "size_mb": 1620,
     "note": "更准，Intel CPU 上往往要二三十分钟"},
    {"id": "large-v3", "label": "large-v3（最准）", "size_mb": 3090,
     "note": "CPU 上很慢，不适合本机先听为快"},
]


def local_backend() -> str:
    """Which local backend this machine should use."""
    requested = config.ENGINE
    if requested == "mlx":
        if not mlx_engine.is_available():
            raise RuntimeError("已指定 MT_ENGINE=mlx，但未安装 mlx-whisper。请执行：pip install mlx-whisper")
        return "mlx"
    if requested in ("faster-whisper", "faster_whisper", "fw"):
        if not fw_engine.is_available():
            raise RuntimeError("已指定 MT_ENGINE=faster-whisper，但未安装。请执行：pip install faster-whisper")
        return "faster-whisper"

    if config.IS_APPLE_SILICON and mlx_engine.is_available():
        return "mlx"
    if fw_engine.is_available():
        if config.IS_APPLE_SILICON:
            logger.warning(
                "Apple Silicon 上未安装 mlx-whisper，回退到 CPU 版 faster-whisper，"
                "速度会慢一到两个数量级。建议执行：pip install mlx-whisper"
            )
        return "faster-whisper"
    if mlx_engine.is_available():
        return "mlx"
    raise RuntimeError("未安装任何本地转录引擎。请执行：pip install -r requirements.txt")


def default_model(backend: Optional[str] = None) -> str:
    backend = backend or local_backend()
    return config.MLX_MODEL if backend == "mlx" else config.FASTER_WHISPER_MODEL


def available_models(backend: Optional[str] = None) -> List[dict]:
    backend = backend or local_backend()
    return MLX_CHOICES if backend == "mlx" else FW_CHOICES


def get_local_engine(model: Optional[str] = None,
                     backend: Optional[str] = None) -> TranscriptionEngine:
    backend = backend or local_backend()
    model = model or default_model(backend)
    key = (backend, model)

    with _lock:
        engine = _cache.get(key)
        if engine is None:
            if backend == "mlx":
                engine = mlx_engine.MLXWhisperEngine(model, models_dir=config.MODELS_DIR)
            else:
                engine = fw_engine.FasterWhisperEngine(model, download_root=config.MODELS_DIR)
            # Only one local model resident at a time; weights are 0.5–3 GB.
            for other in list(_cache):
                _cache.pop(other).unload()
            _cache[key] = engine
        return engine


def resolve_engine(settings: Optional[dict] = None) -> TranscriptionEngine:
    """
    Build the engine the user's settings ask for, falling back to local when a
    cloud configuration is incomplete.
    """
    import settings_store

    s = settings or settings_store.get_all()

    if s.get("engine") == "cloud":
        provider = s.get("cloud_provider") or "custom"
        preset = cloud_engine.PROVIDERS.get(provider, {})
        base_url = s.get("cloud_base_url") or preset.get("base_url") or ""
        model = s.get("cloud_model") or (preset.get("models") or [""])[0]
        api_key = s.get("cloud_api_key") or ""
        if base_url and api_key and model:
            return cloud_engine.CloudTranscriptionEngine(
                base_url=base_url, api_key=api_key, model=model, provider=provider,
            )
        logger.warning("云端转录配置不完整，本次回退到本地引擎")

    return get_local_engine(model=s.get("model") or None)


def describe() -> dict:
    """Backend summary for the UI and /api/health."""
    info: dict = {
        "apple_silicon": config.IS_APPLE_SILICON,
        "mlx_installed": mlx_engine.is_available(),
        "faster_whisper_installed": fw_engine.is_available(),
        "cloud_providers": [
            {"id": k, **{kk: vv for kk, vv in v.items() if kk != "base_url"},
             "base_url": v["base_url"]}
            for k, v in cloud_engine.PROVIDERS.items()
        ],
    }
    try:
        backend = local_backend()
        info.update({
            "ok": True,
            "local_backend": backend,
            "default_model": default_model(backend),
            "models": available_models(backend),
        })
    except RuntimeError as e:
        info.update({"ok": False, "error": str(e)})
    return info


__all__ = [
    "AudioChunk", "Capabilities", "ChunkResult", "EngineInfo", "Segment",
    "TranscriptionEngine", "Word",
    "available_models", "default_model", "describe", "get_local_engine",
    "local_backend", "resolve_engine",
]
