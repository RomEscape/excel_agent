"""Health check endpoint."""

import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Check sidecar and Ollama connectivity. Returns installed model list."""
    ollama_status = "disconnected"
    ollama_models: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                ollama_status = "connected"
                data = resp.json()
                ollama_models = [m["name"] for m in data.get("models", [])]
    except Exception:
        pass

    return {
        "status": "ok",
        "ollama_status": ollama_status,
        "ollama_models": ollama_models,
    }
