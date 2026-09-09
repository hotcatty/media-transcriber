"""
Cloud transcription backend speaking the OpenAI `/v1/audio/transcriptions`
protocol.

Optional by design: local transcription is the default and needs no key. This
exists because the user may legitimately prefer a stronger remote model, and the
protocol is the de-facto standard — OpenAI, Groq, and most Chinese gateways
implement it. Groq in particular serves whisper-large-v3 far faster than any
local Mac can.

Audio is uploaded chunk by chunk, so provider size limits (25 MB is typical)
never bite: an 8-minute mono 64 kbps chunk is under 4 MB.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import requests

from .base import (
    AudioChunk,
    Capabilities,
    ChunkResult,
    EngineInfo,
    Segment,
    TranscriptionEngine,
    Word,
)

logger = logging.getLogger(__name__)

# Presets so users do not have to remember base URLs.
PROVIDERS = {
    "groq": {
        "label": "Groq（速度最快，兼容 OpenAI 协议）",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["whisper-large-v3-turbo", "whisper-large-v3"],
        "docs": "https://console.groq.com/keys",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
        "docs": "https://platform.openai.com/api-keys",
    },
    "custom": {
        "label": "自定义（任何兼容 /v1/audio/transcriptions 的服务）",
        "base_url": "",
        "models": [],
        "docs": "",
    },
}


class CloudTranscriptionEngine(TranscriptionEngine):
    def __init__(self, base_url: str, api_key: str, model: str,
                 provider: str = "custom", timeout: float = 300.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.provider = provider
        self.timeout = timeout

    @property
    def info(self) -> EngineInfo:
        host = self.base_url.split("//")[-1].split("/")[0] or "cloud"
        return EngineInfo(
            backend="cloud",
            model=self.model,
            device=host,
            precision="remote",
            display=f"云端 · {self.model} · {host}",
        )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            segment_timestamps=True,
            word_timestamps=False,
            # Cloud Whisper endpoints generally do punctuate Chinese.
            native_punctuation=True,
            requires_network=True,
            max_chunk_seconds=1500.0,
        )

    def load(self, progress: Optional[Callable[[str, int, int], None]] = None,
             cancel: Optional[Any] = None) -> None:
        if not self.base_url:
            raise RuntimeError("云端转录未配置 base_url")
        if not self.api_key:
            raise RuntimeError("云端转录未配置 API Key")
        if not self.model:
            raise RuntimeError("云端转录未指定模型")

    def transcribe_chunk(
        self,
        chunk: AudioChunk,
        language: Optional[str] = None,
        word_timestamps: bool = False,
        condition_on_previous_text: bool = False,
    ) -> ChunkResult:
        self.load()
        path = chunk.as_file(suffix=".m4a")

        data = {
            "model": self.model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language.split("-")[0]

        with open(path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (path.name, f, "audio/mp4")},
                data=data,
                timeout=self.timeout,
            )

        if resp.status_code != 200:
            raise RuntimeError(self._explain_error(resp))

        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise RuntimeError(f"云端返回了非 JSON 响应：{resp.text[:200]}")

        segments = []
        for s in payload.get("segments") or []:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            segments.append(Segment(
                text=text,
                start=s.get("start"),
                end=s.get("end"),
                no_speech_prob=s.get("no_speech_prob"),
                avg_logprob=s.get("avg_logprob"),
            ))

        # Some providers return only a flat `text` field.
        if not segments and (payload.get("text") or "").strip():
            segments = [Segment(text=payload["text"].strip(), start=0.0,
                                end=chunk.duration)]

        return ChunkResult(
            segments=segments,
            language=payload.get("language") or language,
        )

    def _explain_error(self, resp: requests.Response) -> str:
        detail = resp.text[:300]
        try:
            body = resp.json()
            detail = (body.get("error") or {}).get("message") or detail
        except Exception:
            pass
        if resp.status_code in (401, 403):
            return f"云端转录鉴权失败（{resp.status_code}）：请检查 API Key。{detail}"
        if resp.status_code == 429:
            return f"云端转录触发限流（429）：请稍后重试或降低并发。{detail}"
        if resp.status_code == 413:
            return "音频分片过大，被云端拒绝。请减小 MT_CHUNK_SECONDS 后重试。"
        return f"云端转录失败（HTTP {resp.status_code}）：{detail}"


def probe_models(base_url: str, api_key: str, timeout: float = 20.0) -> list[str]:
    """List models offered by an OpenAI-compatible endpoint."""
    resp = requests.get(
        f"{(base_url or '').rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"获取模型列表失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    data = resp.json().get("data") or []
    return sorted(m.get("id", "") for m in data if m.get("id"))
