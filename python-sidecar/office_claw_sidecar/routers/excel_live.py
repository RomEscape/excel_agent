"""Excel Live 라우터 — 자연어 기반 실시간 Excel(COM) 제어 API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse
from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.excel_live_agent import parse_excel_live_command
from office_claw_sidecar.services.excel_live_service import (
    ExcelConnectionError,
    ExcelDependencyError,
    ExcelLiveError,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
    get_excel_live_service,
)
from office_claw_sidecar.services.llm_service import LLMService, get_llm_service
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool

router = APIRouter(tags=["excel-live"])
_audit = AuditService()


class ExcelLiveCommandRequest(BaseModel):
    message: str = Field(..., min_length=1, description="자연어 엑셀 명령")
    workbook_id: str | None = Field(None, description="대상 통합문서 ID")
    sheet_name: str | None = Field(None, description="대상 시트명 (생략 시 active sheet)")
    approve: bool = Field(False, description="CONFIRM 작업 승인 여부")


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


@dataclass
class PendingExcelApproval:
    action: str
    params: dict[str, Any]
    workbook_id: str | None
    sheet_name: str | None
    created_at: str


_pending_approvals: dict[str, PendingExcelApproval] = {}


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


def _resolve_sheet_name(service, workbook_id: str | None, sheet_name: str | None) -> str:
    if sheet_name:
        return sheet_name
    rows = service.list_workbooks()
    if not rows:
        raise ExcelConnectionError("열린 통합문서가 없습니다.")
    if workbook_id:
        lowered = workbook_id.lower()
        for row in rows:
            if row["workbook_id"].lower() == lowered or row["name"].lower() == lowered:
                return row.get("active_sheet") or "Sheet1"
    return rows[0].get("active_sheet") or "Sheet1"


def _resolve_workbook_id(service, workbook_id: str | None) -> str:
    if workbook_id:
        return workbook_id
    selected = service.get_selected_workbook_id()
    if selected:
        return selected
    rows = service.list_workbooks()
    if not rows:
        raise WorkbookNotFoundError("열린 통합문서가 없습니다.")
    return rows[0]["workbook_id"]


def _top_left_cell(range_ref: str) -> str:
    text = str(range_ref or "").strip().upper()
    if not text:
        return "A1"
    return text.split(":")[0]


def _execute_action(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> dict[str, Any]:
    service = get_excel_live_service()

    if action == "excel_live.list_workbooks":
        return {"workbooks": service.list_workbooks()}

    if action == "excel_live.select_workbook":
        target = params.get("workbook_id") or params.get("name") or workbook_id
        if not isinstance(target, str) or not target.strip():
            raise WorkbookNotFoundError("select_workbook에는 workbook_id 또는 name이 필요합니다.")
        return service.select_workbook(target.strip())

    if action == "excel_live.read_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == "__ACTIVE_SELECTION__":
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.read_range(resolved_wb, resolved_sheet, range_ref)

    if action == "excel_live.write_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        start_cell = str(params.get("start_cell", "")).strip().upper()
        if not start_cell or start_cell in {"__ACTIVE_CELL__", "__ACTIVE_SELECTION__"}:
            selected = service.get_active_selection_ref(resolved_wb, resolved_sheet)
            start_cell = _top_left_cell(selected)
        values_2d = params.get("values_2d")
        if not isinstance(values_2d, list):
            raise ExcelLiveError("write_range에는 values_2d(2차원 배열)가 필요합니다.")
        normalized_rows: list[list[Any]] = []
        for row in values_2d:
            if isinstance(row, list):
                normalized_rows.append(row)
            else:
                normalized_rows.append([row])
        return service.write_range(resolved_wb, resolved_sheet, start_cell, normalized_rows)

    if action == "excel_live.highlight_by_condition":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "A:A")).strip().upper()
        operator = str(params.get("operator", ">=")).strip()
        threshold = float(params.get("threshold", 0))
        fill_color = str(params.get("fill_color", "#FFFF00"))
        return service.highlight_by_condition(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            operator=operator,
            threshold=threshold,
            fill_color=fill_color,
        )

    if action == "excel_live.apply_border":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        line_style = str(params.get("line_style", "continuous")).strip().lower()
        weight = str(params.get("weight", "medium")).strip().lower()
        color = str(params.get("color", "#000000")).strip()
        return service.apply_border(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            line_style=line_style,
            weight=weight,
            color=color,
        )

    if action == "excel_live.set_formula":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == "__ACTIVE_SELECTION__":
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        formula_a1 = str(params.get("formula_a1", "")).strip()
        if not formula_a1.startswith("="):
            raise ExcelLiveError("formula_a1은 '='로 시작해야 합니다.")
        return service.set_formula(resolved_wb, resolved_sheet, range_ref, formula_a1)

    if action == "excel_live.save_workbook":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.save_workbook(resolved_wb)

    raise ExcelLiveError(f"지원하지 않는 action: {action}")


def _build_approval(action: str, params: dict[str, Any]) -> ApprovalRequest:
    approval_id = str(uuid.uuid4())
    summary = {
        "excel_live.write_range": "엑셀 셀 값을 수정합니다.",
        "excel_live.highlight_by_condition": "조건에 맞는 셀 서식을 변경합니다.",
        "excel_live.apply_border": "선택 범위에 경계선을 적용합니다.",
        "excel_live.set_formula": "지정 범위에 수식을 적용합니다.",
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
        result = _execute_action(
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
    try:
        parsed = await parse_excel_live_command(req.message, llm_service=llm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    action = parsed["action"]
    params = dict(parsed.get("params", {}))
    action_req = ExcelLiveActionRequest(
        action=action,
        params=params,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        approve=req.approve,
    )
    result = post_action(action_req)
    result.reason = parsed.get("reason", "")
    return result


@router.post("/approval", response_model=ExcelLiveActionResponse)
def post_approval(req: ApprovalResponse):
    pending = _pending_approvals.pop(req.approval_id, None)
    if pending is None:
        raise HTTPException(status_code=404, detail="승인 대기 작업을 찾을 수 없습니다.")

    if not req.approved:
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

    try:
        result = _execute_action(
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

