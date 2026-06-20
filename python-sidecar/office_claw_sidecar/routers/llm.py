"""LLM chat endpoints - Ollama (local) and Claude (cloud)."""

from fastapi import APIRouter, HTTPException

from office_claw_sidecar.local_stack import get_default_llm_config
from office_claw_sidecar.models.llm import ChatRequest, ChatResponse
from office_claw_sidecar.services.ollama_service import OllamaService
from office_claw_sidecar.services.claude_service import ClaudeService

router = APIRouter()
ollama_svc = OllamaService()
claude_svc = ClaudeService()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a chat message to the configured LLM."""
    try:
        if request.engine == "claude":
            response = await claude_svc.chat(request.message, request.model)
        else:
            response = await ollama_svc.chat(
                request.message,
                request.model or get_default_llm_config()["model"],
            )
        return ChatResponse(response=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
