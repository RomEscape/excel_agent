"""LLM chat endpoints - Ollama (local) and Claude (cloud)."""

from fastapi import APIRouter, HTTPException

from office_claw_sidecar.models.llm import ChatRequest, ChatResponse
from office_claw_sidecar.services.llm_service import load_llm_config
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
            # 저장된 설정의 모델을 사용한다 (하드코딩 기본값 대체 없음).
            model = request.model or load_llm_config().get("model")
            if not model:
                raise HTTPException(
                    status_code=400,
                    detail="LLM 모델이 설정되지 않았습니다. 설정에서 모델을 선택해 주세요.",
                )
            response = await ollama_svc.chat(request.message, model)
        return ChatResponse(response=response)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
