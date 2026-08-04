"""
로컬 스택 프리셋 — JS `src/lib/localStack`와 동일한 기본값 유지.
"""

from __future__ import annotations

from typing import TypedDict


class LlmConfig(TypedDict):
    provider: str
    model: str


class LocalStackPreset(TypedDict):
    id: str
    label: str
    hf_model: str
    ollama_model: str
    multimodal: bool
    llm: LlmConfig


QWEN3_OPENCLAW: LocalStackPreset = {
    "id": "qwen3-openclaw",
    "label": "A.Dot 4.0 Light + OpenClaw (로컬)",
    "hf_model": "skt/A.X-4.0-Light",
    "ollama_model": "skt/A.X-4.0-Light:latest",
    "multimodal": False,
    "llm": {"provider": "ollama", "model": "skt/A.X-4.0-Light:latest"},
}

# 이전 코드/테스트 호환용 별칭
QWEN3_LOCAL = QWEN3_OPENCLAW

PRESETS: dict[str, LocalStackPreset] = {
    QWEN3_OPENCLAW["id"]: QWEN3_OPENCLAW,
}

DEFAULT_PRESET_ID = QWEN3_OPENCLAW["id"]


def get_preset(preset_id: str = DEFAULT_PRESET_ID) -> LocalStackPreset:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise KeyError(f"unknown local stack preset: {preset_id}")
    return preset


def get_default_llm_config() -> LlmConfig:
    return dict(get_preset()["llm"])
