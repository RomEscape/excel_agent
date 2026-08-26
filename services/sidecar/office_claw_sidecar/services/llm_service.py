"""
LLM integration layer — Ollama(OpenAI 호환 API) 단일 경로.

Architecture:
  LLMProvider (ABC)
    ├── OllamaProvider   — wraps OllamaService

  LLMService            — holds the active provider, delegates calls
  get_llm_service()     — singleton factory; reads provider from config file
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.local_stack import get_default_llm_config
from office_claw_sidecar.services.ollama_service import OllamaService

logger = logging.getLogger(__name__)

# Config file lives next to audit.jsonl in the app data directory
_CONFIG_FILENAME = "llm_config.json"

# 최초 실행(설정 파일 없음) 기본값 — provider만 정한다.
# 모델은 하드코딩 기본값을 두지 않는다: 저장된 설정(llm_config.json)이 유일한
# 소스이며, 미설정 시 조용히 임의 모델을 부르지 않고 명확히 오류를 낸다.
_DEFAULT_CONFIG: dict = {"provider": get_default_llm_config()["provider"]}


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


class LLMConfigError(RuntimeError):
    """LLM 설정이 불완전함 (예: 사용할 모델이 선택되지 않음)."""


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

    async def chat_with_tools_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        on_content: Callable[[str], Awaitable[None]] | None = None,
        model: str | None = None,
    ) -> dict:
        """스트리밍 tool 호출 — 미지원 provider는 비스트리밍으로 처리(on_content 미호출)."""
        return await self.chat_with_tools(messages, tools, model=model)

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier."""
        ...


# ── Concrete providers ────────────────────────────────────────────────────


class OllamaProvider(LLMProvider):
    """Delegates to the existing OllamaService."""

    def __init__(self, model: str | None = None) -> None:
        self._svc = OllamaService()
        # 저장된 설정에서 주입받은 모델. 없으면 호출 시 명확히 오류를 낸다
        # (하드코딩 기본 모델로 조용히 대체하지 않는다).
        self._model = (model or "").strip() or None

    @property
    def provider_name(self) -> str:
        return "ollama"

    def _resolve_model(self, model: str | None) -> str:
        chosen = (model or "").strip() or self._model
        if not chosen:
            raise LLMConfigError(
                "LLM 모델이 설정되지 않았습니다. 설정에서 사용할 Ollama 모델을 선택해 주세요."
            )
        return chosen

    async def chat(self, messages: list[dict], model: str | None = None) -> str:
        # 전체 대화 히스토리를 그대로 Ollama에 전달 (멀티턴 지원)
        return await self._svc.chat_messages(messages, model=self._resolve_model(model))

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> dict:
        # Ollama OpenAI 호환 API로 tools 포함 호출 (function calling)
        return await self._svc.chat_completions(
            messages,
            model=self._resolve_model(model),
            tools=tools,
            # 함수 선택/인자 생성의 일관성을 위해 낮은 온도 고정
            temperature=0.2,
        )

    async def chat_with_tools_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        on_content: Callable[[str], Awaitable[None]] | None = None,
        model: str | None = None,
    ) -> dict:
        # 스트리밍 function calling — content는 on_content로 흘리고 tool_calls는 누적
        return await self._svc.chat_completions_stream(
            messages,
            model=self._resolve_model(model),
            tools=tools,
            temperature=0.2,
            on_content=on_content,
        )


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

    async def chat_with_tools_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        on_content: Callable[[str], Awaitable[None]] | None = None,
        model: str | None = None,
    ) -> dict:
        """스트리밍 tools 호출을 provider에 위임한다."""
        return await self._provider.chat_with_tools_stream(
            messages, tools, on_content=on_content, model=model
        )


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
        model = cfg.get("model")
        # provider는 ollama 하나뿐이다 — Claude API 경로는 제거됐다(아래 모듈 주석).
        provider: LLMProvider = OllamaProvider(model=model)
        _llm_service_instance = LLMService(provider)
        logger.info(
            "LLMService initialised with provider=%s model=%s", provider_name, model
        )
    return _llm_service_instance


def reload_llm_service() -> LLMService:
    """
    Force-reload the singleton from disk config.
    Call this after saving new settings so the running service picks them up.
    """
    global _llm_service_instance
    _llm_service_instance = None
    return get_llm_service()
