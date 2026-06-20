"""로컬 AI 스택 프리셋 (Ollama + OpenClaw)."""

from office_claw_sidecar.local_stack.presets import (
    DEFAULT_PRESET_ID,
    QWEN3_OPENCLAW,
    get_default_llm_config,
    get_preset,
)

__all__ = [
    "DEFAULT_PRESET_ID",
    "QWEN3_OPENCLAW",
    "get_default_llm_config",
    "get_preset",
]
