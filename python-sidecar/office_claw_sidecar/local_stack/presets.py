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


QWEN3_LOCAL: LocalStackPreset = {
    "id": "qwen3-local",
    "label": "Qwen 3 (로컬 Ollama)",
    "hf_model": "Qwen/Qwen3-4B-Instruct",
    "ollama_model": "qwen3:4b",
    "multimodal": False,
    "llm": {"provider": "ollama", "model": "qwen3:4b"},
}

PRESETS: dict[str, LocalStackPreset] = {
    QWEN3_LOCAL["id"]: QWEN3_LOCAL,
}

DEFAULT_PRESET_ID = QWEN3_LOCAL["id"]


def get_preset(preset_id: str = DEFAULT_PRESET_ID) -> LocalStackPreset:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise KeyError(f"unknown local stack preset: {preset_id}")
    return preset


def get_default_llm_config() -> LlmConfig:
    return dict(get_preset()["llm"])
