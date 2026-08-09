"""
로컬 스택 프리셋 — JS `src/lib/localStack`와 동일한 기본값 유지.

단 `planner_model`은 JS 쪽에 대응물을 두지 않는다. 이 태그는 레지스트리에서
pull할 수 있는 모델이 아니라 로컬에서 직접 만든 파인튜닝 산출물이라
(`deploy/ollama/Modelfile.ax7b-planner-v2`), 설치 마법사가 다룰 수 있는 대상이
아니다. 플래너 경로는 sidecar 안에서만 돌기 때문에 여기서만 알면 된다.
"""

from __future__ import annotations

from typing import TypedDict


class LlmConfig(TypedDict):
    provider: str
    model: str
    planner_model: str


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
    # model은 범용 대화용, planner_model은 Excel Live 계획 수립 전용이다.
    # 파인튜닝본은 계획 JSON만 뱉도록 학습돼 일반 대화에는 쓸 수 없으므로 분리한다.
    # held-out 18건 체크포인트 비교: v3 최종본 seq 일치 67% (v2: 56% at ckpt-125, 50% at ckpt-250).
    "llm": {
        "provider": "ollama",
        "model": "skt/A.X-4.0-Light:latest",
        "planner_model": "ax7bplanner-v3:latest",
    },
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
