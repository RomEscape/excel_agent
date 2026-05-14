"""Pydantic models for LLM chat operations."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    model: str | None = None
    engine: str = "ollama"  # "ollama" or "claude"


class ChatResponse(BaseModel):
    response: str
