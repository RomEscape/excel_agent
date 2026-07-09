"""
보안 대시보드 라우터.

기존 (Phase 5) 엔드포인트:
  GET  /security/stats          — 마스킹/차단 통계 (오늘, 이번 주, 전체)
  GET  /security/blocked-log    — 최근 차단 이력 (limit 파라미터)
  GET  /security/whitelist      — 현재 스킬별 권한 설정
  PUT  /security/whitelist      — 스킬별 권한 수정 (SAFE/CONFIRM/DENIED)
  GET  /security/masking-settings — 마스킹 설정 조회
  POST /security/masking-settings — 마스킹 설정 업데이트

Phase 2 추가 엔드포인트:
  GET  /security/audit          — 명령 감사 로그 목록 (limit, offset)
  GET  /security/audit/stats    — 등급별 명령 통계 (SAFE/CONFIRM/DENIED 건수)
  GET  /security/audit/{id}     — 특정 명령 이벤트 상세
  DELETE /security/audit        — 명령 감사 로그 초기화 (관리자용)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.masking_service import get_masking_service, reset_masking_service
from office_claw_sidecar.services.tool_registry import (
    get_whitelist_state,
    save_whitelist,
)
from office_claw_sidecar.command_audit import get_command_audit_logger

logger = logging.getLogger(__name__)
router = APIRouter(tags=["security"])
_audit = AuditService()

# ── UI 승인 대기 큐 (텔레그램 미연결 시 앱 UI로 승인 요청) ──────────────────────
# key: approval_id (str), value: {"command": str, "reason": str, "audit_id": int, "responded": bool, "approved": bool}
_pending_ui_approvals: dict[str, dict[str, Any]] = {}


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class WhitelistUpdateRequest(BaseModel):
    """스킬 권한 일괄 업데이트 요청."""
    overrides: dict[str, str]  # {"gog.gmail.send": "safe", "gog.sheets.write": "confirm"}


class MaskingSettingsRequest(BaseModel):
    """마스킹 설정 업데이트 요청."""
    mask_email: bool = False
    mask_phone: bool = False


class ApprovalRespondRequest(BaseModel):
    """UI 승인 응답 요청."""
    approved: bool
    rejection_reason: str | None = None  # 거부 시 사용자 입력 사유 (선택, Sprint 3)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/masking-settings")
async def get_masking_settings() -> dict:
    """현재 마스킹 설정(mask_email, mask_phone)을 반환한다."""
    svc = get_masking_service()
    return {
        "mask_email": svc.mask_email,
        "mask_phone": svc.mask_phone,
    }


@router.post("/masking-settings")
async def update_masking_settings(req: MaskingSettingsRequest) -> dict:
    """마스킹 설정을 업데이트한다. 변경은 즉시 적용된다."""
    reset_masking_service(mask_email=req.mask_email, mask_phone=req.mask_phone)
    _audit.log(
        action="masking.settings_updated",
        target="security_layer",
        detail=f"mask_email={req.mask_email} mask_phone={req.mask_phone}",
    )
    logger.info("[security] 마스킹 설정 변경: mask_email=%s mask_phone=%s", req.mask_email, req.mask_phone)
    return {"ok": True, "mask_email": req.mask_email, "mask_phone": req.mask_phone}


@router.get("/stats")
async def security_stats() -> dict:
    """
    마스킹 및 차단 통계를 반환한다.

    감사 로그(audit.jsonl)에서 집계하므로 별도 DB가 필요없다.
    """
    stats = _audit.get_masking_stats()
    blocked = _audit.get_blocked_log(limit=1000)

    # 차단 건수 집계
    today_blocked = 0
    week_blocked = 0
    total_blocked = len(blocked)

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    for entry in blocked:
        try:
            ts = datetime.fromisoformat(entry.get("timestamp", ""))
            if ts >= today_start:
                today_blocked += 1
            if ts >= week_start:
                week_blocked += 1
        except Exception:
            pass

    return {
        "masking": stats,
        "blocked_count": {
            "today": today_blocked,
            "week": week_blocked,
            "total": total_blocked,
        },
        "last_blocked_at": _audit.get_last_blocked_at(),
        "last_approval_at": _audit.get_last_approval_at(),
    }


@router.get("/blocked-log")
async def security_blocked_log(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """최근 보안 차단/거부 이벤트 목록을 반환한다."""
    logs = _audit.get_blocked_log(limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.get("/whitelist")
async def security_get_whitelist() -> dict:
    """현재 스킬별 권한 설정을 반환한다."""
    skills = get_whitelist_state()
    return {"skills": skills}


@router.put("/whitelist")
async def security_update_whitelist(req: WhitelistUpdateRequest) -> dict:
    """
    스킬별 권한을 업데이트하고 저장한다.

    변경 사항은 skill_whitelist.json에 영속되며 즉시 적용된다.
    """
    try:
        save_whitelist(req.overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        logger.error("[security] 화이트리스트 저장 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # 감사 로그에 변경 기록
    for skill_name, level in req.overrides.items():
        _audit.log(
            action="whitelist.updated",
            target=skill_name,
            detail=f"permission={level}",
        )

    logger.info("[security] 화이트리스트 업데이트: %s", req.overrides)
    return {"ok": True, "updated": len(req.overrides)}


# ── Phase 2: 명령 감사 로그 엔드포인트 ──────────────────────────────────────────


@router.get("/audit/stats")
async def command_audit_stats() -> dict:
    """
    명령 분석 등급별 통계를 반환한다.

    confirm_pending = SQLite DB의 CONFIRM+미결 건수 + UI 승인 대기 큐 건수.
    Dashboard "승인 대기" 카드와 StatusBar 보안 배지가 동일한 값을 사용한다.

    Returns::

        {
            "total": 42,
            "safe": 30,
            "confirm": 8,
            "denied": 4,
            "confirm_approved": 5,
            "confirm_rejected": 3,
            "confirm_pending": 2,
        }
    """
    cmd_audit = get_command_audit_logger()
    # UI 승인 대기 큐의 실제 미처리 건수를 함께 집계
    ui_pending = sum(
        1 for e in _pending_ui_approvals.values() if not e["responded"]
    )
    return cmd_audit.get_stats(extra_pending=ui_pending)


@router.get("/audit")
async def command_audit_list(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    명령 감사 로그 목록을 최신순으로 반환한다.

    Query parameters:
      - limit: 최대 반환 건수 (기본 50, 최대 500)
      - offset: 건너뛸 건수 (페이지네이션용)
    """
    cmd_audit = get_command_audit_logger()
    logs = cmd_audit.get_recent(limit=limit, offset=offset)
    return {"logs": logs, "count": len(logs), "offset": offset}


@router.get("/audit/{log_id}")
async def command_audit_detail(log_id: int) -> dict:
    """특정 명령 감사 이벤트 상세를 반환한다."""
    cmd_audit = get_command_audit_logger()
    entry = cmd_audit.get_by_id(log_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"로그 ID {log_id}를 찾을 수 없습니다.")
    return entry


@router.delete("/audit")
async def command_audit_clear() -> dict:
    """
    명령 감사 로그 전체를 초기화한다. (관리자용)

    주의: 복구 불가능합니다.
    """
    cmd_audit = get_command_audit_logger()
    deleted = cmd_audit.clear_all()
    _audit.log(
        action="command_audit.cleared",
        target="command_log",
        detail=f"deleted={deleted}",
    )
    logger.info("[security] 명령 감사 로그 초기화: %d건 삭제", deleted)
    return {"ok": True, "deleted": deleted}


# ── UI 승인 요청 엔드포인트 (텔레그램 미연결 시 앱 UI 대체 수단) ──────────────────

@router.post("/approval")
async def create_approval_request(
    command: str,
    reason: str,
    audit_id: int | None = None,
) -> dict:
    """
    앱 UI에서 처리할 승인 요청을 생성한다.

    텔레그램 봇이 연결되지 않은 경우 이 엔드포인트를 통해 UI에 HITL 승인 요청을 전달한다.

    Returns::

        {"approval_id": str, "command": str, "reason": str}
    """
    approval_id = uuid.uuid4().hex
    _pending_ui_approvals[approval_id] = {
        "command": command,
        "reason": reason,
        "audit_id": audit_id,
        "responded": False,
        "approved": False,
    }
    logger.info("[approval] UI 승인 요청 생성: id=%s reason=%s", approval_id, reason)
    return {"approval_id": approval_id, "command": command, "reason": reason}


@router.get("/approval/pending")
async def get_pending_approvals() -> dict:
    """
    대기 중인 UI 승인 요청 목록을 반환한다 (폴링용).

    Returns::

        {"pending": [{"approval_id": str, "command": str, "reason": str, "audit_id": int|null}]}
    """
    pending = [
        {
            "approval_id": aid,
            "command": entry["command"],
            "reason": entry["reason"],
            "audit_id": entry.get("audit_id"),
        }
        for aid, entry in _pending_ui_approvals.items()
        if not entry["responded"]
    ]
    return {"pending": pending}


@router.post("/approval/{approval_id}/respond")
async def respond_to_approval(
    approval_id: str,
    req: ApprovalRespondRequest,
) -> dict:
    """
    앱 UI에서 승인 또는 거부 응답을 전달한다.

    - 승인 시: approved=true, 감사 로그 업데이트
    - 거부 시: approved=false, 감사 로그 업데이트

    Returns::

        {"ok": bool, "approved": bool, "approval_id": str}
    """
    entry = _pending_ui_approvals.get(approval_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"승인 요청 {approval_id}를 찾을 수 없습니다.")

    if entry["responded"]:
        raise HTTPException(status_code=409, detail="이미 처리된 승인 요청입니다.")

    entry["responded"] = True
    entry["approved"] = req.approved

    # 감사 로그 업데이트 (거부 시 rejection_reason 함께 저장 — Sprint 3)
    reason_recorded = False
    if entry.get("audit_id") is not None:
        cmd_audit = get_command_audit_logger()
        cmd_audit.update_approval(entry["audit_id"], req.approved, req.rejection_reason)
        if not req.approved and req.rejection_reason:
            reason_recorded = True

    status_label = "승인" if req.approved else "거부"
    detail = f"approved={req.approved}"
    if not req.approved and req.rejection_reason:
        detail += f" reason={req.rejection_reason[:100]}"
    _audit.log(
        action=f"ui_approval.{status_label}",
        target=approval_id,
        detail=detail,
    )
    logger.info("[approval] UI 승인 응답: id=%s approved=%s reason_recorded=%s",
                approval_id, req.approved, reason_recorded)

    # 메신저 폴백 대기 중인 asyncio.Event가 있으면 깨움
    resp_event = entry.get("_event")
    if resp_event is not None:
        resp_event.set()

    # 처리된 요청은 큐에서 제거 (event가 없는 경우만 — event 있으면 push_ui_approval에서 제거)
    if resp_event is None:
        _pending_ui_approvals.pop(approval_id, None)

    return {"ok": True, "approved": req.approved, "approval_id": approval_id, "reason_recorded": reason_recorded}
