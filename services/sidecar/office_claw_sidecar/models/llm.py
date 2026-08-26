"""Pydantic models for LLM chat operations."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    model: str | None = None
    engine: str = "ollama"  # Ollama 단일 경로 (Claude API 경로는 제거됨)


class ChatResponse(BaseModel):
    response: str
