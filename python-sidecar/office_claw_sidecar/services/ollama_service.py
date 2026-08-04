"""Ollama local LLM client — OpenAI 호환 API(/v1/chat/completions) 사용."""

import logging
from typing import Any

import httpx

from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
audit = AuditService()

OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaService:
    """Communicate with the local Ollama server via the OpenAI-compatible API."""

    async def chat(self, message: str, model: str = "llama3.2") -> str:
        """Send a single chat message to Ollama and return the full response."""
        return await self.chat_messages(
            [{"role": "user", "content": message}], model=model
        )

    async def chat_messages(
        self, messages: list[dict], model: str = "llama3.2", temperature: float | None = None
    ) -> str:
        """Send a full conversation history to Ollama and return the reply text."""
        reply = await self.chat_completions(messages, model=model, temperature=temperature)
        return reply.get("content") or ""

    async def chat_completions(
        self,
        messages: list[dict],
        model: str = "llama3.2",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """
        OpenAI 호환 엔드포인트(/v1/chat/completions)로 대화를 전송한다.

        tools가 주어지면 LLM이 함수 호출(tool_calls)로 응답할 수 있다.

        반환:
          {
            "content": str,          # 어시스턴트 텍스트 (없으면 "")
            "tool_calls": [          # 함수 호출 목록 (없으면 [])
              {"id": ..., "type": "function",
               "function": {"name": ..., "arguments": "<JSON 문자열>"}},
            ],
            "finish_reason": str,    # "stop" | "tool_calls" | ...
          }
        """
        audit.log(
            "llm_chat",
            f"ollama/{model}",
            detail=f"turns={len(messages)} tools={len(tools or [])}",
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return self.parse_chat_completions_response(data)

    @staticmethod
    def parse_chat_completions_response(data: dict) -> dict[str, Any]:
        """OpenAI 호환 응답 JSON에서 content/tool_calls/finish_reason을 추출한다."""
        choices = data.get("choices") or []
        if not choices:
            return {"content": "", "tool_calls": [], "finish_reason": "stop"}
        choice = choices[0] or {}
        message = choice.get("message") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": choice.get("finish_reason") or "stop",
        }
