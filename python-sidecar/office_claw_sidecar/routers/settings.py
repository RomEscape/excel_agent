"""Settings endpoints — LLM provider configuration."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from office_claw_sidecar.services.llm_service import (
    load_llm_config,
    save_llm_config,
    reload_llm_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────


class LLMConfig(BaseModel):
    """LLM provider selection persisted to disk."""

    provider: str  # "ollama" | "claude"
    model: str

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"ollama", "claude"}
        if v not in allowed:
            raise ValueError(f"provider must be one of {allowed}, got '{v}'")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model must not be empty")
        return v


class LLMConfigResponse(BaseModel):
    provider: str
    model: str


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/llm", response_model=LLMConfigResponse)
async def get_llm_config() -> LLMConfigResponse:
    """Return the currently saved LLM provider + model."""
    cfg = load_llm_config()
    return LLMConfigResponse(provider=cfg["provider"], model=cfg["model"])


@router.post("/llm", response_model=LLMConfigResponse)
async def set_llm_config(config: LLMConfig) -> LLMConfigResponse:
    """
    Save LLM provider + model to the app data directory and
    hot-reload the singleton LLMService so the change takes effect immediately.
    """
    try:
        save_llm_config({"provider": config.provider, "model": config.model})
        svc = reload_llm_service()
        logger.info(
            "LLM config updated via API: provider=%s model=%s active_provider=%s",
            config.provider,
            config.model,
            svc.current_provider,
        )
        return LLMConfigResponse(provider=config.provider, model=config.model)
    except Exception as exc:
        logger.error("Failed to save LLM config: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
