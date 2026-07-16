"""Ollama local LLM client — OpenAI 호환 API(/v1/chat/completions) 사용."""

import json
import logging
from collections.abc import Awaitable, Callable
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
        self, messages: list[dict], model: str = "llama3.2"
    ) -> str:
        """Send a full conversation history to Ollama and return the reply text."""
        reply = await self.chat_completions(messages, model=model)
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

    async def chat_completions_stream(
        self,
        messages: list[dict],
        model: str = "llama3.2",
        tools: list[dict] | None = None,
        temperature: float | None = None,
        on_content: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """
        chat_completions의 스트리밍 변형(stream=True, SSE).

        content 조각은 도착 즉시 on_content(piece)로 흘려보내고(사용자 실시간 표시용),
        tool_calls 델타는 조용히 누적한다. 스트림 종료 후 chat_completions와 동일한
        {content, tool_calls, finish_reason} dict를 반환한다(툴 루프 로직 불변).
        """
        audit.log(
            "llm_chat_stream",
            f"ollama/{model}",
            detail=f"turns={len(messages)} tools={len(tools or [])}",
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{OLLAMA_BASE_URL}/v1/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        if on_content is not None:
                            await on_content(piece)
                    for tc in delta.get("tool_calls") or []:
                        self._accumulate_tool_call(tool_calls_acc, tc)

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
        }

    @staticmethod
    def _accumulate_tool_call(acc: dict[int, dict], delta: dict) -> None:
        """스트리밍 tool_call 델타(index별 조각)를 누적한다."""
        idx = delta.get("index", 0) or 0
        slot = acc.setdefault(
            idx,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if delta.get("id"):
            slot["id"] = delta["id"]
        if delta.get("type"):
            slot["type"] = delta["type"]
        fn = delta.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]

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
