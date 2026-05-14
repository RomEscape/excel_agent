"""
Legacy 라우터 — Graceful Deprecation 응답.

Phase 4에서 gmail / excel / document 직접 API 엔드포인트를 즉시 삭제하지 않고
호출 시 명확한 안내 메시지를 반환한다.

모든 메서드(GET/POST/PUT/DELETE)의 모든 경로를 받아 410 Gone으로 응답한다.
프론트엔드가 구버전 IPC 커맨드를 호출하더라도 앱이 충돌하지 않는다.

이전: /gmail/*, /excel/*, /document/*
이후: /agent/chat 으로 동일한 작업을 AI 에이전트를 통해 수행
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["legacy"])

_DEPRECATION_MESSAGE = (
    "이 기능은 Phase 4에서 OpenClaw 에이전트로 이전되었습니다. "
    "'에이전트 채팅' 탭에서 동일한 작업을 수행할 수 있습니다. "
    "(API: POST /agent/chat)"
)


async def _deprecated_response(_request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "deprecated": True,
            "message": _DEPRECATION_MESSAGE,
            "alternative": "/agent/chat",
        },
    )


# 와일드카드 경로로 모든 하위 경로 수신
# FastAPI는 path 파라미터로 슬래시 포함 경로를 받으려면 :path 접미사 필요
router.add_api_route(
    "/{full_path:path}",
    _deprecated_response,
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
