"""Excel Live 라우터 — 자연어(tool-calling) 기반 실시간 Excel(COM) 제어 API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse
from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.excel_actions import execute_excel_action
from office_claw_sidecar.services.excel_live_service import (
    ExcelConnectionError,
    ExcelDependencyError,
    ExcelLiveError,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
    get_excel_live_service,
)
from office_claw_sidecar.services.excel_tool_agent import (
    resume_excel_tool_turn,
    run_excel_tool_turn,
)
from office_claw_sidecar.services.llm_service import (
    LLMConfigError,
    LLMService,
    LLMToolsNotSupportedError,
    get_llm_service,
)
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool

router = APIRouter(tags=["excel-live"])
_audit = AuditService()


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ExcelLiveCommandRequest(BaseModel):
    message: str = Field(..., min_length=1, description="자연어 엑셀 명령 또는 일반 대화")
    workbook_id: str | None = Field(None, description="대상 통합문서 ID")
    sheet_name: str | None = Field(None, description="대상 시트명 (생략 시 active sheet)")
    approve: bool = Field(False, description="CONFIRM 작업 승인 여부")
    history: list[ChatTurn] = Field(
        default_factory=list, description="이전 대화 턴 (멀티턴 맥락 유지용)"
    )


class ExcelLiveActionRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    workbook_id: str | None = None
    sheet_name: str | None = None
    approve: bool = False


class ExcelLiveActionResponse(BaseModel):
    ok: bool
    action: str
    approval_required: bool = False
    pending_approval: ApprovalRequest | None = None
    result: dict[str, Any] | None = None
    reason: str = ""
    # tool-calling 경로 확장 필드
    assistant_text: str = Field("", description="LLM의 자연어 최종 답변 (일반 대화 포함)")
    executed_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="이번 턴에 즉시 실행된 SAFE 액션 목록 [{action, params, result}]",
    )


@dataclass
class PendingExcelApproval:
    action: str
    params: dict[str, Any]
    workbook_id: str | None
    sheet_name: str | None
    created_at: str
    # B안 재개용 대화 상태 {"messages": [...], "tool_call_id": str}.
    # tool-calling 경로에서 설정되며, /action 직접 호출 경로에서는 None(단순 실행).
    resume: dict[str, Any] | None = None


_pending_approvals: dict[str, PendingExcelApproval] = {}


def _register_and_respond(
    turn: dict[str, Any], workbook_id: str | None
) -> ExcelLiveActionResponse:
    """에이전트 턴 결과를 응답으로 변환한다. approval이면 승인 대기(재개 상태 포함)를 등록."""
    executed = turn.get("executed", [])

    if turn["type"] == "approval":
        approval = _build_approval(turn["action"], turn["params"])
        _pending_approvals[approval.approval_id] = PendingExcelApproval(
            action=turn["action"],
            params=turn["params"],
            workbook_id=workbook_id,
            sheet_name=turn.get("sheet_name"),
            created_at=approval.created_at,
            resume=turn.get("resume"),
        )
        return ExcelLiveActionResponse(
            ok=True,
            action=turn["action"],
            approval_required=True,
            pending_approval=approval,
            reason=turn.get("reason", ""),
            executed_actions=executed,
        )

    last = executed[-1] if executed else None
    return ExcelLiveActionResponse(
        ok=True,
        action=last["action"] if last else "chat",
        result=last["result"] if last else None,
        assistant_text=turn.get("assistant_text", ""),
        executed_actions=executed,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ExcelDependencyError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, ExcelConnectionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (WorkbookNotFoundError, WorksheetNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ExcelLiveError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Excel Live 오류: {exc}")


def _map_llm_error(exc: Exception) -> HTTPException:
    """LLM 호출 실패를 원인별로 구분한다 (연결/모델없음/미설정을 뭉뚱그리지 않는다)."""
    # 모델 미설정 — 설정에서 모델을 고르면 해결.
    if isinstance(exc, LLMConfigError):
        return HTTPException(status_code=400, detail=str(exc))
    # 도구 미지원 provider.
    if isinstance(exc, LLMToolsNotSupportedError):
        return HTTPException(status_code=400, detail=str(exc))
    # 서버는 응답했으나 오류 — 대표적으로 모델 미설치(404).
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return HTTPException(
                status_code=400,
                detail=(
                    "요청한 LLM 모델을 Ollama에서 찾을 수 없습니다. 설정에서 선택한 "
                    "모델이 설치돼 있는지 확인해 주세요 (예: `ollama pull <model>`)."
                ),
            )
        return HTTPException(
            status_code=502,
            detail=f"LLM 서버가 오류를 반환했습니다 (HTTP {code}).",
        )
    # 그 외 httpx 오류 = 진짜 연결 실패.
    if isinstance(exc, httpx.HTTPError):
        return HTTPException(
            status_code=503,
            detail=f"Ollama 서버에 연결할 수 없습니다. Ollama가 실행 중인지 확인해 주세요. ({exc})",
        )
    return HTTPException(status_code=500, detail=f"LLM 오류: {exc}")


def _build_approval(action: str, params: dict[str, Any]) -> ApprovalRequest:
    approval_id = str(uuid.uuid4())
    summary = {
        "excel_live.write_range": "엑셀 셀 값을 수정합니다.",
        "excel_live.highlight_by_condition": "조건에 맞는 셀 서식을 변경합니다.",
        "excel_live.apply_border": "선택 범위에 경계선을 적용합니다.",
        "excel_live.set_formula": "지정 범위에 수식을 적용합니다.",
        "excel_live.filter_rows": "조건에 맞지 않는 행을 삭제합니다 (조건 행만 유지).",
        "excel_live.sort_rows": "데이터 행 순서를 정렬로 재배열합니다.",
        "excel_live.dedupe_rows": "중복 데이터 행을 삭제합니다.",
        "excel_live.drop_column": "지정 열을 삭제합니다.",
        "excel_live.rename_column": "열 머리글 이름을 변경합니다.",
        "excel_live.add_column": "테이블에 새 열을 추가합니다.",
    }.get(action, "엑셀 변경 작업을 실행합니다.")
    return ApprovalRequest(
        approval_id=approval_id,
        tool_name=action,
        tool_display_name=action,
        summary=summary,
        args_preview=params,
        session_id="excel-live",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/status")
def get_status():
    service = get_excel_live_service()
    return {
        "available": service.is_available(),
        "workbooks": service.list_workbooks() if service.is_available() else [],
    }


@router.post("/action", response_model=ExcelLiveActionResponse)
def post_action(req: ExcelLiveActionRequest):
    tool_def = get_tool(req.action)
    if tool_def and tool_def.permission == PermissionLevel.DENIED:
        return ExcelLiveActionResponse(
            ok=False,
            action=req.action,
            reason="보안 정책에 의해 거부된 작업입니다.",
        )

    if tool_def and tool_def.permission == PermissionLevel.CONFIRM and not req.approve:
        pending = _build_approval(req.action, req.params)
        _pending_approvals[pending.approval_id] = PendingExcelApproval(
            action=req.action,
            params=req.params,
            workbook_id=req.workbook_id,
            sheet_name=req.sheet_name,
            created_at=pending.created_at,
        )
        return ExcelLiveActionResponse(
            ok=True,
            action=req.action,
            approval_required=True,
            pending_approval=pending,
            reason="승인이 필요한 작업입니다.",
        )

    try:
        result = execute_excel_action(
            action=req.action,
            params=req.params,
            workbook_id=req.workbook_id,
            sheet_name=req.sheet_name,
        )
        _audit.log(
            action="excel.live.action",
            target=req.action,
            detail=f"ok=True workbook={req.workbook_id or ''}",
        )
        return ExcelLiveActionResponse(ok=True, action=req.action, result=result)
    except Exception as exc:
        raise _map_error(exc)


@router.post("/command", response_model=ExcelLiveActionResponse)
async def post_command(
    req: ExcelLiveCommandRequest,
    llm: LLMService = Depends(get_llm_service),
):
    """
    자연어 명령/대화 통합 엔드포인트.

    LLM이 tools(function calling)로 Excel 작업을 선택한다:
      - SAFE 도구는 즉시 실행되고 결과 기반 답변(assistant_text)이 돌아온다
      - CONFIRM 도구는 approval_required=True로 승인 대기를 반환한다 (승인 시 /approval에서 루프 재개)
      - 도구가 필요 없는 일반 대화는 assistant_text만 채워진다
    """
    try:
        turn = await run_excel_tool_turn(
            message=req.message,
            llm_service=llm,
            workbook_id=req.workbook_id,
            sheet_name=req.sheet_name,
            history=[t.model_dump() for t in req.history],
        )
    except (LLMConfigError, LLMToolsNotSupportedError, httpx.HTTPError) as exc:
        raise _map_llm_error(exc)

    return _register_and_respond(turn, req.workbook_id)


@router.post("/approval", response_model=ExcelLiveActionResponse)
async def post_approval(
    req: ApprovalResponse,
    llm: LLMService = Depends(get_llm_service),
):
    pending = _pending_approvals.pop(req.approval_id, None)
    if pending is None:
        raise HTTPException(status_code=404, detail="승인 대기 작업을 찾을 수 없습니다.")

    if not req.approved:
        # 거부 시 나머지 계획은 중단하고 종료한다 (에이전트 루프를 재개하지 않음).
        _audit.log(
            action="excel.live.approval.rejected",
            target=pending.action,
            detail=f"approval_id={req.approval_id}",
        )
        return ExcelLiveActionResponse(
            ok=True,
            action=pending.action,
            approval_required=False,
            reason="사용자가 작업을 거부했습니다.",
            result={"approved": False},
        )

    # B안: 재개 상태가 있으면 승인 작업 실행 후 에이전트 루프를 이어간다.
    if pending.resume is not None:
        try:
            turn = await resume_excel_tool_turn(
                resume=pending.resume,
                action=pending.action,
                params=pending.params,
                workbook_id=pending.workbook_id,
                sheet_name=pending.sheet_name,
                llm_service=llm,
            )
        except (LLMConfigError, LLMToolsNotSupportedError, httpx.HTTPError) as exc:
            raise _map_llm_error(exc)
        return _register_and_respond(turn, pending.workbook_id)

    # 재개 상태 없음 (/action 직접 승인 경로) — 단순 실행.
    try:
        result = execute_excel_action(
            action=pending.action,
            params=pending.params,
            workbook_id=pending.workbook_id,
            sheet_name=pending.sheet_name,
        )
        _audit.log(
            action="excel.live.approval.executed",
            target=pending.action,
            detail=f"approval_id={req.approval_id}",
        )
        return ExcelLiveActionResponse(
            ok=True,
            action=pending.action,
            result=result,
            reason="승인 후 작업이 실행되었습니다.",
        )
    except Exception as exc:
        raise _map_error(exc)
