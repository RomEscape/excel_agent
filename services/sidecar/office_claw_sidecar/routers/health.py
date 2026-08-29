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

    # 설정이 기대하는 모델이 설치 목록에 없으면 이름을 밝힌다 — 모델 부재는
    # 오류 없이 '플래너 무성공·매크로 전멸'로만 나타난 전례가 있다(개발일지
    # 2026-08 blob 소실: 300턴 동안 API는 멀쩡했고 플래너 성공 0건).
    missing_models: list[str] = []
    if ollama_status == "connected":
        try:
            from office_claw_sidecar.services.llm_service import load_llm_config
            from office_claw_sidecar.local_stack import get_default_llm_config

            cfg = load_llm_config() or {}
            defaults = get_default_llm_config()
            expected = {
                str(cfg.get("model") or defaults.get("model") or "").strip(),
                str(cfg.get("planner_model") or defaults.get("planner_model") or "").strip(),
            }
            installed = set(ollama_models)
            missing_models = sorted(m for m in expected if m and m not in installed)
        except Exception:
            pass

    return {
        "status": "ok",
        "ollama_status": ollama_status,
        "ollama_models": ollama_models,
        "missing_models": missing_models,
    }
