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
import os
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.local_stack import get_default_llm_config
from office_claw_sidecar.services.ollama_service import OllamaService

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


def _is_planner_model_applicable(cfg: dict, planner_model: str) -> bool:
    """
    설정된 planner_model을 현재 프로바이더에 그대로 넘겨도 되는지 판단한다.

    프리셋 기본 planner_model(`ax7bplanner-v2:latest`)은 로컬 Ollama에만 존재하는
    파인튜닝 태그다. 사용자가 프로바이더를 Claude로 바꾼 상태에서 이 태그를 넘기면
    존재하지 않는 모델을 호출하게 된다. 반대로 사용자가 직접 지정한 값이라면
    의도가 있는 것으로 보고 프로바이더와 무관하게 존중한다.
    """
    if str(cfg.get("provider", "") or "").strip().lower() == "ollama":
        return True
    default_planner = str(_DEFAULT_CONFIG.get("planner_model", "") or "").strip()
    return planner_model != default_planner


def get_planner_model_name(*, fallback: str | None = None) -> str:
    """
    Excel 플래너 전용 모델명을 반환한다.
    우선순위:
      1) OFFICECLAW_PLANNER_MODEL 환경변수
      2) llm_config.json의 planner_model (현재 프로바이더에서 쓸 수 있을 때만)
      3) llm_config.json의 model
      4) fallback / 기본값
    """
    env_model = str(os.getenv("OFFICECLAW_PLANNER_MODEL", "")).strip()
    if env_model:
        return env_model
    cfg = load_llm_config()
    cfg_planner = str(cfg.get("planner_model", "") or "").strip()
    if cfg_planner and _is_planner_model_applicable(cfg, cfg_planner):
        return cfg_planner
    cfg_model = str(cfg.get("model", "") or "").strip()
    if cfg_model:
        return cfg_model
    if fallback:
        return str(fallback).strip()
    return str(_DEFAULT_CONFIG.get("model", "") or "").strip()


def _is_planner_model(name: str) -> bool:
    """계획 JSON 전용 SFT 모델인가. 이 모델은 분해·대화·정규화 태스크에 못 쓴다."""
    return str(name or "").strip().lower().startswith("ax7bplanner")


def get_macro_model_name(*, fallback: str | None = None) -> str:
    """
    매크로 분해 전용 모델명을 반환한다.

    플래너 모델(`ax7bplanner-*`)은 계획 JSON만 뱉도록 파인튜닝돼 있어 "요청을 하위 명령
    문장으로 펼쳐라"는 다른 태스크에는 맞지 않는다. 그래서 planner_model이 아니라
    일반 대화 모델을 기본으로 쓴다.

    2026-08-17 실측: 설정 파일의 `model` 자체가 ax7bplanner-v3로 잘못 저장돼 있어
    매크로 분해·정규화·채팅이 전부 플래너로 돌고 있었다(분해 JSON 실패 실측).
    설정이 또 틀어져도 이 태스크로는 새지 않게 플래너 패턴이면 기본 모델로 떨어진다.
    우선순위:
      1) OFFICECLAW_MACRO_MODEL 환경변수
      2) llm_config.json의 model (플래너 모델이 아닐 때만)
      3) fallback / 기본값
    """
    env_model = str(os.getenv("OFFICECLAW_MACRO_MODEL", "")).strip()
    if env_model:
        return env_model
    cfg = load_llm_config()
    cfg_model = str(cfg.get("model", "") or "").strip()
    if cfg_model and not _is_planner_model(cfg_model):
        return cfg_model
    if fallback:
        return str(fallback).strip()
    return str(_DEFAULT_CONFIG.get("model", "") or "").strip()


# ── Abstract provider ─────────────────────────────────────────────────────


class LLMToolsNotSupportedError(RuntimeError):
    """현재 provider가 OpenAI 호환 tools(function calling)를 지원하지 않음."""


class LLMConfigError(RuntimeError):
    """LLM 설정이 불완전함 (예: 사용할 모델이 선택되지 않음)."""


class LLMProvider(ABC):
    """Common interface for all LLM backends."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        json_only: bool = False,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        """
        Send a list of messages and return the assistant reply as a string.

        messages format follows the OpenAI convention:
          [{"role": "user", "content": "..."}, ...]

        json_only는 "응답이 JSON이어야 한다"는 요청이지 보장이 아니다. 지원하지 않는
        백엔드는 무시하므로, 호출부는 어느 쪽이든 파싱에 실패할 수 있다고 보고 짜야 한다.

        timeout은 HTTP 계층 상한이다. 호출자가 `asyncio.wait_for`로 감쌀 때는 그
        예산보다 짧게 줘야 한다 — 바깥에서만 끊으면 요청은 백그라운드에 살아남는다.
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
        # 저장된 설정에서 주입받은 모델. 없으면 chat은 설정 기본값으로(플래너 오염
        # 가드 포함), tools 경로는 명확히 오류를 낸다.
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

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        json_only: bool = False,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        # 전체 대화 히스토리를 그대로 Ollama에 전달 (멀티턴 지원)
        cfg = load_llm_config()
        default_model = str(cfg.get("model") or get_default_llm_config()["model"])
        if _is_planner_model(default_model):
            # 기본 모델 자리에 플래너가 들어오면(설정 오염 — 2026-08-17에 평가
            # 스크립트가 실제로 일으켰다) 채팅·정규화·분해가 전부 계획-JSON 전용
            # SFT로 돈다. 명시 지정 없는 호출은 항상 범용 모델로 떨어진다.
            default_model = str(get_default_llm_config()["model"])
        return await self._svc.chat_messages(
            messages,
            model=model or self._model or default_model,
            temperature=temperature,
            json_only=json_only,
            json_schema=json_schema,
            timeout=timeout,
        )

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

    Swap providers at runtime by calling set_provider().
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def set_provider(self, provider: LLMProvider) -> None:
        self._provider = provider
        logger.info("LLM provider switched to: %s", provider.provider_name)

    @property
    def current_provider(self) -> str:
        return self._provider.provider_name

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        json_only: bool = False,
        json_schema: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send messages to the active provider and return the reply.

        temperature를 주면 그대로 넘긴다. 계획 수립처럼 같은 입력에 같은 답이 나와야 하는
        호출은 0을 쓴다 — 기본 샘플링(0.8)에서는 같은 문장이 실행마다 다른 계획이 된다.

        json_only는 백엔드가 지원할 때만 적용된다(현재 Ollama). 자세한 내용은
        `LLMProvider.chat`.
        """
        return await self._provider.chat(
            messages,
            model=model,
            temperature=temperature,
            json_only=json_only,
            json_schema=json_schema,
            timeout=timeout,
        )

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
    ) -> dict:
        """활성 provider로 tools 포함 대화를 보낸다."""
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


# ── 승격용 강한 모델 ───────────────────────────────────────────────────────
#
# 활성 프로바이더와 **별개**로 유지한다. 평소 대화는 로컬(Ollama)로 돌리면서
# 계획 수립이 막혔을 때만 강한 모델로 올려야 하는데, 싱글턴 프로바이더를
# 갈아끼우면 그 사이 들어온 다른 요청까지 클라우드로 새어 나간다.

_strong_llm_service_instance: LLMService | None = None


def get_strong_llm_service() -> LLMService | None:
    """에스컬레이션 단계에서 쓸 강한 모델. 사용할 수 없으면 None."""
    # Claude API 경로가 제거되면서(dev 2026-08 리팩터) 강한 모델 프로바이더가 없다.
    # 호출부는 전부 None을 "로컬 단계까지만"으로 처리한다.
    return _strong_llm_service_instance


def get_strong_planner_model_name() -> str:
    """승격 시 쓸 모델명. 지정이 없으면 프로바이더 기본값을 쓴다."""
    return str(os.environ.get("OFFICECLAW_STRONG_MODEL", "")).strip()
