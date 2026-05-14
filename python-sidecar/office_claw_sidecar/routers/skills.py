"""
스킬 관리 라우터.

GET  /skills/installed      - 설치된 스킬 목록
POST /skills/install        - ClawHub에서 스킬 설치
GET  /skills/catalog        - 추천 스킬 카탈로그 (캐시 포함)
PUT  /skills/{name}/config  - 스킬별 설정 저장 (화이트리스트)

OpenClaw 게이트웨이가 실행 중이 아닐 경우 503 응답.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.openclaw_client import (
    OpenClawError,
    OpenClawUnavailableError,
    get_client,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["skills"])
_audit = AuditService()

# 카탈로그 캐시: 5분 TTL
_catalog_cache: list[dict] | None = None
_catalog_cached_at: float = 0.0
_CATALOG_TTL = 300.0  # 초


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class InstallRequest(BaseModel):
    skill_name: str = Field(..., description="ClawHub 스킬 이름 (예: gog-gmail)")


class SkillConfigRequest(BaseModel):
    config: dict = Field(default_factory=dict, description="스킬별 설정 값")
    whitelisted: bool = Field(True, description="스킬 화이트리스트 포함 여부")


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/installed")
async def list_installed_skills() -> dict:
    """현재 OpenClaw 세션에서 사용 가능한 스킬(도구) 목록을 반환한다."""
    client = get_client()
    try:
        # 세션이 없을 경우 빈 세션으로 조회
        sessions = await client.list_sessions()
        if not sessions:
            # 임시 세션 생성해서 도구 목록 조회
            try:
                session_info = await client.create_session()
                session_id = session_info.get("sessionId", "")
                tools = await client.list_tools(session_id)
            except OpenClawError:
                tools = []
        else:
            session_id = sessions[0].get("sessionId", "")
            tools = await client.list_tools(session_id)

        return {"skills": tools, "count": len(tools)}

    except OpenClawUnavailableError:
        return {
            "skills": [],
            "count": 0,
            "warning": "OpenClaw 게이트웨이가 실행되지 않았습니다",
        }
    except OpenClawError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/install")
async def install_skill(req: InstallRequest) -> dict:
    """
    ClawHub에서 스킬을 설치한다.

    설치 성공 시 감사 로그에 기록된다.
    """
    _audit.log(
        action="skills.install.request",
        target=req.skill_name,
        detail="ClawHub 스킬 설치 요청",
    )

    client = get_client()
    try:
        result = await client.install_skill(req.skill_name)

        _audit.log(
            action="skills.install.success",
            target=req.skill_name,
            detail=str(result),
        )

        return {
            "success": True,
            "skill_name": req.skill_name,
            "detail": result,
        }

    except OpenClawUnavailableError:
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenClaw 게이트웨이가 실행되지 않았습니다. "
                "'npm install -g openclaw@latest' 후 앱을 재시작해 주세요."
            ),
        )
    except OpenClawError as exc:
        _audit.log(
            action="skills.install.error",
            target=req.skill_name,
            detail=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"스킬 설치 실패: {exc}")


@router.get("/catalog")
async def get_skill_catalog() -> dict:
    """
    ClawHub 추천 스킬 카탈로그를 반환한다.

    5분 TTL 캐시를 사용한다.
    OpenClaw 게이트웨이가 없을 경우 기본 카탈로그를 반환한다.
    """
    global _catalog_cache, _catalog_cached_at

    now = time.monotonic()
    if _catalog_cache is not None and (now - _catalog_cached_at) < _CATALOG_TTL:
        return {"skills": _catalog_cache, "cached": True}

    client = get_client()
    try:
        catalog = await client.get_catalog()
        _catalog_cache = catalog
        _catalog_cached_at = now
        return {"skills": catalog, "cached": False}

    except (OpenClawUnavailableError, OpenClawError) as exc:
        logger.warning("[skills] 카탈로그 조회 실패, 기본 목록 반환: %s", exc)
        from office_claw_sidecar.services.openclaw_client import _default_catalog
        return {
            "skills": _default_catalog(),
            "cached": False,
            "warning": "OpenClaw 게이트웨이 미연결 — 기본 카탈로그 표시 중",
        }


@router.put("/{skill_name}/config")
async def update_skill_config(skill_name: str, req: SkillConfigRequest) -> dict:
    """
    스킬별 설정을 저장한다.

    현재는 감사 로그에 기록하는 것이 주목적이며,
    Phase 5에서 실제 화이트리스트 저장소와 연동된다.
    """
    _audit.log(
        action="skills.config.update",
        target=skill_name,
        detail=f"whitelisted={req.whitelisted} config_keys={list(req.config.keys())}",
    )

    # TODO(Phase 5): 실제 화이트리스트 저장소에 저장
    return {
        "success": True,
        "skill_name": skill_name,
        "whitelisted": req.whitelisted,
        "config": req.config,
    }
