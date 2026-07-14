"""
Agent 라우터 — OpenClaw 게이트웨이를 경유하는 보안 프록시.

POST /agent/chat     - 메시지 전송 + 보안 검증 + 감사 로그
GET  /agent/sessions - 활성 세션 목록 조회
POST /agent/approval - CONFIRM 스킬 승인/거부 결정 전달

보안 레이어 처리 순서:
  1. is_denied_intent() — DENIED 키워드 선제 차단
  2. masking_service.mask() — 민감 데이터 마스킹
  3. audit_service.log() — 요청 감사 기록 (마스킹된 텍스트로)
  4. openclaw_client.send_message() — 마스킹된 텍스트로 게이트웨이 전달
  5. 응답 감사 기록 (tool 실행 포함)
  6. 결과 반환 (마스킹 발생 시 안내 메시지 추가)

OpenClaw 게이트웨이가 실행 중이 아닌 경우:
  OpenClawUnavailableError를 잡아 503 응답 반환.
  사용자에게 명확한 한국어 안내 메시지 포함.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.llm_service import get_llm_service
from office_claw_sidecar.services.masking_service import get_masking_service
from office_claw_sidecar.services.openclaw_client import (
    OpenClawError,
    OpenClawUnavailableError,
    get_client,
)
from office_claw_sidecar.services.tool_registry import (
    PermissionLevel,
    get_tool,
    is_denied_intent,
)
from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])
_audit = AuditService()

# 승인 대기 중인 ApprovalRequest 딕셔너리: approval_id → ApprovalRequest
_pending_approvals: dict[str, "ApprovalRequest"] = {}
_OPENCLAW_DEGRADED_REPLY_TOKENS = (
    "context overflow",
    "prompt too large for the model",
    "auto-compaction could not recover",
    "use /compact",
    "use /new",
    "non_deliverable_terminal_turn",
)


# ── 요청/응답 모델 ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="사용자 메시지")
    session_id: Optional[str] = Field(None, description="기존 세션 ID (None이면 새 세션 생성)")


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict] = Field(default_factory=list)
    masked_count: int = 0          # 마스킹된 항목 수 (0이면 미표시)
    masked_types: list[str] = Field(default_factory=list)  # 마스킹된 유형 목록
    approval_required: bool = False
    pending_approval: "ApprovalRequest | None" = None


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(req: ChatRequest) -> ChatResponse:
    """
    OpenClaw 게이트웨이를 통해 AI 에이전트와 대화한다.

    보안 레이어:
    - DENIED 키워드 포함 시 즉시 거부 (OpenClaw에 전달하지 않음)
    - 모든 요청/응답 감사 로그 기록
    """
    # 1. DENIED 키워드 선제 차단
    denied, matched_trigger = is_denied_intent(req.message)
    if denied:
        hint = f" (키워드: '{matched_trigger}')" if matched_trigger else ""
        _audit.log(
            action="agent.chat.denied",
            target="security_layer",
            detail=f"차단 키워드 감지{hint}: {req.message[:100]}",
        )
        raise HTTPException(
            status_code=403,
            detail="이 요청은 보안 정책에 의해 차단되었습니다.",
        )

    # 2. 민감 데이터 마스킹
    masking_svc = get_masking_service()
    mask_result = masking_svc.mask(req.message)
    send_message = mask_result.masked_text

    if mask_result.was_modified:
        _audit.log(
            action="masking.applied",
            target="security_layer",
            detail=f"{mask_result.count}건 마스킹: {', '.join(mask_result.types)}",
        )
        logger.info("[agent] 민감 데이터 마스킹: %d건 (%s)", mask_result.count, mask_result.types)

    # 3. 요청 감사 기록 (마스킹된 텍스트 기준)
    _audit.log(
        action="agent.chat.request",
        target="openclaw_gateway",
        detail=f"session={req.session_id or 'new'} msg_len={len(send_message)} masked={mask_result.count}",
    )

    # 4. OpenClaw 게이트웨이로 전달
    client = get_client()
    collected_parts: list[str] = []
    tool_calls: list[dict] = []
    session_id = req.session_id
    pending_approval: ApprovalRequest | None = None

    try:
        async for frame in client.send_message(
            message=send_message,
            session_id=session_id,
        ):
            frame_type = frame.get("type", "")

            # 세션 ID 확보 (첫 번째 프레임에서)
            if session_id is None:
                session_id = frame.get("sessionId")

            if frame_type == "sessions.message":
                # LLM 텍스트 응답 프레임
                content = frame.get("content") or frame.get("text") or frame.get("message", "")
                if content:
                    collected_parts.append(str(content))

            elif frame_type == "exec.approval":
                # CONFIRM 권한 스킬 실행 전 사용자 확인 요청
                tool_name = frame.get("toolName", "unknown")
                tool_def = get_tool(tool_name)

                if tool_def and tool_def.permission == PermissionLevel.CONFIRM:
                    # 승인 필요: 실행 중단하고 React에 확인 요청 반환
                    import uuid as _uuid
                    from datetime import datetime, timezone
                    from office_claw_sidecar.services.tool_registry import TOOL_DISPLAY_NAMES

                    approval_id = str(_uuid.uuid4())
                    args_raw: dict = frame.get("args", {})
                    # args_preview에도 마스킹 적용 (값 문자열 대상)
                    args_preview: dict = {}
                    for k, v in args_raw.items():
                        if isinstance(v, str):
                            args_preview[k] = masking_svc.mask(v).masked_text
                        else:
                            args_preview[k] = v

                    pending_approval = ApprovalRequest(
                        approval_id=approval_id,
                        tool_name=tool_name,
                        tool_display_name=TOOL_DISPLAY_NAMES.get(tool_name, tool_name),
                        summary=_build_approval_summary(tool_name, args_preview),
                        args_preview=args_preview,
                        session_id=session_id or "",
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    # 승인 대기 큐에 저장
                    _pending_approvals[approval_id] = pending_approval

                    _audit.log(
                        action="approval.pending",
                        target=tool_name,
                        detail=f"approval_id={approval_id} session={session_id}",
                    )
                    # 현재 스트리밍 중단 — 승인 대기 응답 반환
                    break

                elif tool_def and tool_def.permission == PermissionLevel.DENIED:
                    # DENIED 스킬 자동 거부
                    _audit.log(
                        action="approval.auto_rejected",
                        target=tool_name,
                        detail="거부 권한 스킬 자동 차단",
                    )
                    collected_parts.append(
                        f"'{tool_name}' 스킬은 보안 정책에 의해 실행이 거부되었습니다."
                    )
                    break

            elif frame_type == "tool.result":
                # 도구 실행 결과 — 감사 로그에 기록
                tool_name = frame.get("toolName", "unknown")
                tool_calls.append(frame)
                _audit.log(
                    action="agent.tool.executed",
                    target=tool_name,
                    detail=f"session={session_id}",
                )

    except OpenClawUnavailableError as exc:
        logger.warning("[agent] OpenClaw 게이트웨이 사용 불가: %s", exc)
        exc_text = str(exc).lower()
        if "gateway token mismatch" in exc_text or "token mismatch" in exc_text:
            raise HTTPException(
                status_code=503,
                detail=(
                    "OpenClaw 게이트웨이 인증 토큰이 sidecar와 일치하지 않습니다. "
                    "앱을 완전히 재시작한 뒤 LocalAISetupWizard에서 OpenClaw 재시작을 한 번 더 실행해 주세요."
                ),
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenClaw 게이트웨이가 실행되지 않았습니다. "
                "'npm install -g openclaw@latest' 를 실행한 후 앱을 재시작해 주세요."
            ),
        )

    except OpenClawError as exc:
        logger.error("[agent] OpenClaw 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {exc}")

    response_text = "".join(collected_parts).strip()

    degraded_reason: str | None = None
    if pending_approval is not None:
        # 승인 대기 상태: 빈 응답 대신 안내 메시지
        if not response_text:
            response_text = "작업 실행 전 확인이 필요합니다."
    elif not tool_calls:
        if not response_text:
            degraded_reason = "empty_response"
        elif _is_openclaw_degraded_reply(response_text):
            degraded_reason = "gateway_degraded_reply"

    if degraded_reason is not None:
        fallback_text = await _fallback_chat_via_llm(send_message)
        if fallback_text:
            response_text = fallback_text
            _audit.log(
                action="agent.chat.fallback_llm",
                target="llm_service",
                detail=f"session={session_id} reason={degraded_reason}",
            )
        elif not response_text:
            response_text = "(응답 없음)"

    # 마스킹 안내 메시지 append (마스킹된 경우)
    if mask_result.was_modified:
        notice = f"\n\n> 민감 정보 {mask_result.count}건({', '.join(mask_result.types)})이 LLM 전송 전 자동 마스킹되었습니다."
        response_text = response_text + notice

    # 5. 응답 감사 기록
    _audit.log(
        action="agent.chat.response",
        target="openclaw_gateway",
        detail=(
            f"session={session_id} response_len={len(response_text)} "
            f"tools={len(tool_calls)} approval={pending_approval is not None}"
        ),
    )

    return ChatResponse(
        response=response_text,
        session_id=session_id or "",
        tool_calls=tool_calls,
        masked_count=mask_result.count,
        masked_types=mask_result.types,
        approval_required=pending_approval is not None,
        pending_approval=pending_approval,
    )


@router.post("/approval")
async def submit_approval(req: ApprovalResponse) -> dict:
    """
    사용자의 승인/거부 결정을 처리한다.

    승인된 경우 OpenClaw에 승인 프레임을 전송한다.
    거부된 경우 rejection_reason을 command_log에 저장하고 완료 응답을 반환한다.

    N-1 (Sprint 4): rejection_reason을 command_log.rejection_reason 컬럼에 영속화한다.
    """
    from office_claw_sidecar.command_audit import get_command_audit_logger

    pending = _pending_approvals.pop(req.approval_id, None)
    if pending is None:
        raise HTTPException(status_code=404, detail="승인 요청을 찾을 수 없습니다.")

    # command_log에 승인/거부 결과 및 rejection_reason 저장 (N-1)
    reason_recorded = False
    cmd_audit = get_command_audit_logger()
    # pending.tool_name으로 가장 최근 CONFIRM 로그를 찾아 업데이트
    # 로그 연결: ApprovalRequest에는 audit_id가 없으므로 tool_name+session_id로 최근 항목 조회
    try:
        recent_logs = cmd_audit.get_recent(limit=20)
        audit_id: int | None = None
        for log in recent_logs:
            if (
                log.get("tool_name") == pending.tool_name
                and log.get("session_id") == pending.session_id
                and log.get("approved") is None
            ):
                audit_id = log["id"]
                break

        if audit_id is not None:
            cmd_audit.update_approval(audit_id, req.approved, req.rejection_reason if not req.approved else None)
            if not req.approved and req.rejection_reason:
                reason_recorded = True
    except Exception as exc:
        # audit 업데이트 실패는 비치명적 — 승인 처리는 계속
        logger.warning("[agent] command_log 업데이트 실패 (비치명): %s", exc)

    detail = f"approval_id={req.approval_id} session={pending.session_id}"
    if not req.approved and req.rejection_reason:
        detail += f" reason={req.rejection_reason[:100]}"
    action = "approval.approved" if req.approved else "approval.rejected"
    _audit.log(
        action=action,
        target=pending.tool_name,
        detail=detail,
    )

    if req.approved:
        # OpenClaw에 승인 프레임 전송
        client = get_client()
        try:
            await client._send_frame({
                "type": "exec.approval.response",
                "approvalId": req.approval_id,
                "sessionId": pending.session_id,
                "approved": True,
            })
            logger.info("[agent] 스킬 승인: %s (id=%s)", pending.tool_name, req.approval_id)
        except Exception as exc:
            logger.warning("[agent] 승인 프레임 전송 실패: %s", exc)
            return {"ok": True, "approved": True, "reason_recorded": False, "tool_name": pending.tool_name, "warning": "승인 전달 실패 — 세션이 만료되었을 수 있습니다."}
    else:
        logger.info("[agent] 스킬 거부: %s (id=%s) reason_recorded=%s", pending.tool_name, req.approval_id, reason_recorded)

    return {"ok": True, "approved": req.approved, "reason_recorded": reason_recorded, "tool_name": pending.tool_name}


@router.get("/approval/pending")
async def get_pending_approvals() -> dict:
    """대기 중인 승인 요청 목록을 반환한다.

    호출 시점에 생성 후 90초(60초 타임아웃 + 30초 여유)를 초과한 항목을 자동 정리한다.
    별도 백그라운드 태스크 없이 폴링 호출 시 만료 항목을 제거한다.
    """
    from datetime import datetime, timezone, timedelta

    TTL_SECONDS = 90
    now = datetime.now(timezone.utc)
    expired_ids = []

    for approval_id, req in list(_pending_approvals.items()):
        try:
            created_at = datetime.fromisoformat(req.created_at)
            if (now - created_at) > timedelta(seconds=TTL_SECONDS):
                expired_ids.append(approval_id)
        except Exception:
            # created_at 파싱 실패 시 안전하게 유지
            pass

    for approval_id in expired_ids:
        expired = _pending_approvals.pop(approval_id, None)
        if expired:
            _audit.log(
                action="approval.expired",
                target=expired.tool_name,
                detail=f"approval_id={approval_id} ttl={TTL_SECONDS}s 초과",
            )
            logger.info("[agent] 만료된 승인 요청 정리: %s (tool=%s)", approval_id, expired.tool_name)

    return {
        "pending": [a.model_dump() for a in _pending_approvals.values()],
        "expired_count": len(expired_ids),
    }


@router.get("/sessions")
async def agent_sessions() -> dict:
    """활성 OpenClaw 세션 목록을 반환한다."""
    client = get_client()
    try:
        sessions = await client.list_sessions()
        return {"sessions": sessions}
    except OpenClawUnavailableError:
        return {"sessions": [], "error": "OpenClaw 게이트웨이가 실행되지 않았습니다"}
    except OpenClawError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _build_approval_summary(tool_name: str, args: dict) -> str:
    """CONFIRM 스킬 실행 요약 문자열을 한국어로 생성한다."""
    if tool_name == "gog.gmail.send":
        to = args.get("to", "")
        subject = args.get("subject", "")
        return f"'{to}'에게 이메일을 전송합니다 (제목: {subject})"
    if tool_name == "gog.sheets.write":
        sheet = args.get("spreadsheetId", args.get("sheetName", ""))
        return f"Google Sheets '{sheet}'을 수정합니다"
    # 기본 요약
    from office_claw_sidecar.services.tool_registry import TOOL_DISPLAY_NAMES
    display = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
    return f"'{display}' 작업을 실행합니다"


def _is_openclaw_degraded_reply(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in _OPENCLAW_DEGRADED_REPLY_TOKENS)


async def _fallback_chat_via_llm(masked_message: str) -> str:
    try:
        llm = get_llm_service()
        reply = await llm.chat(
            [
                {"role": "user", "content": masked_message},
            ]
        )
        return str(reply or "").strip()
    except Exception as exc:
        logger.warning("[agent] LLM fallback 실패: %s", exc)
        return ""


@router.get("/status")
async def agent_status() -> dict:
    """OpenClaw 연결 상태를 반환한다."""
    client = get_client()
    if client.is_connected():
        return {"state": "connected", "message": "OpenClaw 게이트웨이와 연결되어 있습니다"}

    # 연결 시도
    try:
        await client.ensure_connected()
        return {"state": "connected", "message": "OpenClaw 게이트웨이와 연결되었습니다"}
    except OpenClawUnavailableError as exc:
        return {
            "state": "unavailable",
            "message": str(exc),
            "hint": "npm install -g openclaw@latest 를 실행해 주세요",
        }
