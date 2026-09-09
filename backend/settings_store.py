"""
User-adjustable settings that persist across restarts.

Kept out of the task index and out of git. The file holds an optional cloud API
key, so it is created with owner-only permissions and lives under temp/.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

import config

logger = logging.getLogger(__name__)

SETTINGS_FILE = config.TEMP_DIR / "settings.json"

DEFAULTS: Dict[str, Any] = {
    "engine": "local",          # "local" | "cloud"
    "model": "",                # empty → backend default
    "cloud_provider": "groq",
    "cloud_base_url": "",
    "cloud_api_key": "",
    "cloud_model": "",
    "word_timestamps": False,   # off by default: transcripts are for LLMs
    "condition_on_previous_text": False,
    "reflow": True,             # merge fragments into paragraphs
    "paragraph_min_chars": 120,
    "paragraph_max_chars": 300,
    "whisper_speed_x": None,    # measured local realtime multiple; None → config default
}

_lock = threading.Lock()
_cache: Dict[str, Any] | None = None


def _read() -> Dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except Exception as e:
        logger.warning(f"settings unreadable, using defaults: {e}")
        return dict(DEFAULTS)


def get_all(redact_secrets: bool = False) -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read()
        out = dict(_cache)
    if redact_secrets and out.get("cloud_api_key"):
        out["cloud_api_key"] = "••••" + out["cloud_api_key"][-4:]
        out["cloud_api_key_set"] = True
    else:
        out["cloud_api_key_set"] = bool(out.get("cloud_api_key"))
    return out


def get(key: str, default: Any = None) -> Any:
    return get_all().get(key, DEFAULTS.get(key, default))


def whisper_speed() -> float:
    """Realtime multiple for ETA: this machine's last runs, else platform default."""
    raw = get("whisper_speed_x")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    if 1.5 <= v <= 25:
        return v
    return config.WHISPER_SPEED_X


def remember_whisper_speed(measured: float) -> None:
    """Blend a finished job's speed into the local prior. No UI, no cloud."""
    try:
        m = float(measured)
    except (TypeError, ValueError):
        return
    if m < 1.5 or m > 25:
        return
    prev = get("whisper_speed_x")
    try:
        p = float(prev)
    except (TypeError, ValueError):
        p = 0.0
    if 1.5 <= p <= 25:
        blended = 0.45 * p + 0.55 * m
    else:
        blended = m * 0.92
    update({"whisper_speed_x": round(blended, 2)})
    logger.info("本机转写速度记为 %.2f×（本趟 %.2f×）", blended, m)


def update(values: Dict[str, Any]) -> Dict[str, Any]:
    global _cache
    with _lock:
        current = dict(_cache) if _cache is not None else _read()
        for k, v in values.items():
            if k in DEFAULTS and v is not None:
                current[k] = v
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)   # the file can hold an API key
        tmp.replace(SETTINGS_FILE)
        _cache = current
    return get_all(redact_secrets=True)
