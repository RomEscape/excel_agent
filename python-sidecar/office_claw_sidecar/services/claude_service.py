"""Claude API client (optional cloud LLM)."""

import logging

import httpx

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.keyring_service import KeyringService

logger = logging.getLogger(__name__)
audit = AuditService()

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeService:
    """Communicate with the Claude API (requires API key stored in keyring)."""

    def __init__(self) -> None:
        self._keyring = KeyringService()

    async def chat(self, message: str, model: str | None = None) -> str:
        """Send a single chat message to Claude API."""
        return await self.chat_messages(
            [{"role": "user", "content": message}], model=model
        )

    async def chat_messages(
        self, messages: list[dict], model: str | None = None
    ) -> str:
        """Send a full conversation history to Claude API and return the reply."""
        api_key = self._keyring.retrieve("claude_api_key")
        if not api_key:
            raise ValueError(
                "Claude API key not configured. "
                "Store it via the credentials manager with key 'claude_api_key'."
            )

        model = model or "claude-sonnet-4-20250514"
        audit.log("llm_chat", f"claude/{model}", detail=f"turns={len(messages)}")

        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(CLAUDE_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content_blocks = data.get("content", [])
        if content_blocks:
            return content_blocks[0].get("text", "")
        return ""
