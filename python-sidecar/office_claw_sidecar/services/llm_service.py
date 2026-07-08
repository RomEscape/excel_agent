"""
LLM integration layer — unified abstraction over Ollama and Claude API.

Architecture:
  LLMProvider (ABC)
    ├── OllamaProvider   — wraps OllamaService
    └── ClaudeProvider   — wraps ClaudeService

  LLMService            — holds the active provider, delegates calls
  get_llm_service()     — singleton factory; reads provider from config file
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.local_stack import get_default_llm_config
from office_claw_sidecar.services.ollama_service import OllamaService
from office_claw_sidecar.services.claude_service import ClaudeService

logger = logging.getLogger(__name__)

# Config file lives next to audit.jsonl in the app data directory
_CONFIG_FILENAME = "llm_config.json"

_DEFAULT_CONFIG: dict = get_default_llm_config()


# ── Config helpers ────────────────────────────────────────────────────────


def _config_path():
    return get_data_dir() / _CONFIG_FILENAME


def load_llm_config() -> dict:
    """Read LLM config from disk; fall back to defaults if missing or corrupt."""
    path = _config_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # Validate required keys
            if "provider" in data:
                return {**_DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read LLM config: %s — using defaults", exc)
    return dict(_DEFAULT_CONFIG)


def save_llm_config(config: dict) -> None:
    """Persist LLM config to disk."""
    path = _config_path()
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.info("LLM config saved: provider=%s model=%s", config.get("provider"), config.get("model"))


# ── Abstract provider ─────────────────────────────────────────────────────


class LLMToolsNotSupportedError(RuntimeError):
    """현재 provider가 OpenAI 호환 tools(function calling)를 지원하지 않음."""


class LLMProvider(ABC):
    """Common interface for all LLM backends."""

    @abstractmethod
    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        """
        Send a list of messages and return the assistant reply as a string.

        messages format follows the OpenAI convention:
          [{"role": "user", "content": "..."}, ...]
        """
        ...

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> dict:
        """
        OpenAI 호환 tools 배열과 함께 대화를 전송한다.

        반환: {"content": str, "tool_calls": list, "finish_reason": str}
        지원하지 않는 provider는 LLMToolsNotSupportedError를 던진다.
        """
        raise LLMToolsNotSupportedError(
            f"'{self.provider_name}' provider는 tools(function calling)를 지원하지 않습니다."
        )

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier."""
        ...


# ── Concrete providers ────────────────────────────────────────────────────


class OllamaProvider(LLMProvider):
    """Delegates to the existing OllamaService."""

    def __init__(self) -> None:
        self._svc = OllamaService()

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        # 전체 대화 히스토리를 그대로 Ollama에 전달 (멀티턴 지원)
        default_model = get_default_llm_config()["model"]
        return await self._svc.chat_messages(messages, model=model or default_model)

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> dict:
        # Ollama OpenAI 호환 API로 tools 포함 호출 (function calling)
        default_model = get_default_llm_config()["model"]
        return await self._svc.chat_completions(
            messages,
            model=model or default_model,
            tools=tools,
            # 함수 선택/인자 생성의 일관성을 위해 낮은 온도 고정
            temperature=0.2,
        )


class ClaudeProvider(LLMProvider):
    """Delegates to the existing ClaudeService."""

    def __init__(self) -> None:
        self._svc = ClaudeService()

    @property
    def provider_name(self) -> str:
        return "claude"

    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        # 전체 대화 히스토리를 그대로 Claude에 전달 (멀티턴 지원)
        return await self._svc.chat_messages(messages, model=model)


# ── LLM Service ───────────────────────────────────────────────────────────


class LLMService:
    """
    Holds the currently active LLM provider and delegates calls.

    Provider 교체는 config 저장 후 reload_llm_service()로 싱글톤을 재생성한다.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    @property
    def current_provider(self) -> str:
        return self._provider.provider_name

    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        """Send messages to the active provider and return the reply."""
        return await self._provider.chat(messages, model=model)

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> dict:
        """tools(function calling) 포함 호출을 provider에 위임한다."""
        return await self._provider.chat_with_tools(messages, tools, model=model)


# ── Singleton factory ─────────────────────────────────────────────────────

_llm_service_instance: LLMService | None = None


def get_llm_service() -> LLMService:
    """
    Return the singleton LLMService, initialising it from the config file on
    first call.  Subsequent calls return the same instance so state (e.g. a
    conversation context) is preserved across requests.
    """
    global _llm_service_instance
    if _llm_service_instance is None:
        cfg = load_llm_config()
        provider_name = cfg.get("provider", "ollama")
        provider: LLMProvider = (
            ClaudeProvider() if provider_name == "claude" else OllamaProvider()
        )
        _llm_service_instance = LLMService(provider)
        logger.info("LLMService initialised with provider: %s", provider_name)
    return _llm_service_instance


def reload_llm_service() -> LLMService:
    """
    Force-reload the singleton from disk config.
    Call this after saving new settings so the running service picks them up.
    """
    global _llm_service_instance
    _llm_service_instance = None
    return get_llm_service()
