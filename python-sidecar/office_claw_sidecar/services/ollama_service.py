"""Ollama local LLM client."""

import logging

import httpx

from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
audit = AuditService()

OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaService:
    """Communicate with the local Ollama server."""

    async def chat(self, message: str, model: str = "llama3.2") -> str:
        """Send a single chat message to Ollama and return the full response."""
        return await self.chat_messages(
            [{"role": "user", "content": message}], model=model
        )

    async def chat_messages(
        self, messages: list[dict], model: str = "llama3.2"
    ) -> str:
        """Send a full conversation history to Ollama and return the reply."""
        audit.log("llm_chat", f"ollama/{model}", detail=f"turns={len(messages)}")

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return data.get("message", {}).get("content", "")
