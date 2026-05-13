"""
routers/chat.py — Sprint 5 채팅 세션 영속화 API.

엔드포인트:
  POST /chat/messages               메시지 저장
  GET  /chat/sessions               세션 목록 (최근순)
  GET  /chat/sessions/{session_id}/messages  세션 메시지 전체
  DELETE /chat/sessions/{session_id}         세션 삭제
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from office_claw_sidecar import chat_history

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveMessageRequest(BaseModel):
    session_id: str
    role: str                          # user | agent | system
    text: Optional[str] = None
    tool_calls: Optional[object] = None
    masked_count: int = 0
    masked_types: Optional[list] = None
    error_text: Optional[str] = None


@router.post("/messages")
def post_save_message(req: SaveMessageRequest):
    """채팅 메시지를 영속 저장한다."""
    try:
        msg_id = chat_history.save_message(
            session_id=req.session_id,
            role=req.role,
            text=req.text,
            tool_calls=req.tool_calls,
            masked_count=req.masked_count,
            masked_types=req.masked_types,
            error_text=req.error_text,
        )
        return {"ok": True, "id": msg_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("메시지 저장 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"메시지 저장 실패: {exc}")


@router.get("/sessions")
def get_sessions(limit: int = Query(default=20, ge=1, le=200)):
    """세션 목록을 최근 활동순으로 반환한다."""
    try:
        sessions = chat_history.list_sessions(limit=limit)
        return sessions
    except Exception as exc:
        logger.error("세션 목록 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"세션 목록 조회 실패: {exc}")


@router.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """세션의 전체 메시지를 시간순으로 반환한다."""
    try:
        messages = chat_history.get_messages(session_id=session_id)
        return messages
    except Exception as exc:
        logger.error("메시지 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"메시지 조회 실패: {exc}")


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """세션과 그 하위 메시지 전체를 삭제한다."""
    try:
        deleted_count = chat_history.delete_session(session_id=session_id)
        return {"ok": True, "deleted_count": deleted_count}
    except Exception as exc:
        logger.error("세션 삭제 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"세션 삭제 실패: {exc}")
