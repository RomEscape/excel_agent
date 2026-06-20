"""로컬 스택 프리셋 기본값 검증."""

from office_claw_sidecar.local_stack import (
    DEFAULT_PRESET_ID,
    QWEN3_OPENCLAW,
    get_default_llm_config,
    get_preset,
)


def test_default_preset_is_qwen3_openclaw():
    assert DEFAULT_PRESET_ID == "qwen3-openclaw"
    assert get_preset()["id"] == QWEN3_OPENCLAW["id"]


def test_default_llm_config_matches_ollama_qwen3():
    cfg = get_default_llm_config()
    assert cfg == {"provider": "ollama", "model": "qwen3:4b"}
