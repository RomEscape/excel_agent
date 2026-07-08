"""Excel Live 라우터 — 자연어 기반 실시간 Excel(COM) 제어 API."""

from __future__ import annotations
import asyncio
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse
from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.excel_live_agent import extract_create_table_slot_hints
from office_claw_sidecar.services.excel_live_agent import parse_excel_live_command
from office_claw_sidecar.services.excel_live_agent import parse_command_plan_with_llm
from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based
from office_claw_sidecar.services.excel_live_executor import (
    PlanStep,
    execute_plan,
    normalize_plan_steps,
)
from office_claw_sidecar.services.excel_live_plan_critic import (
    build_replan_context,
    should_replan_after_execution,
)
from office_claw_sidecar.services.excel_live_plan_validator import (
    EDIT_ACTIONS,
    ValidationContext,
    validate_plan,
)
from office_claw_sidecar.services.excel_live_service import (
    ExcelConnectionError,
    ExcelDependencyError,
    ExcelLiveError,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
    get_excel_live_service,
)
from office_claw_sidecar.services.excel_live_table_presets import get_table_preset
from office_claw_sidecar.services.llm_service import LLMService, get_llm_service
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool

router = APIRouter(tags=["excel-live"])
_audit = AuditService()


class ExcelLiveCommandRequest(BaseModel):
    message: str = Field(..., min_length=1, description="자연어 엑셀 명령")
    workbook_id: str | None = Field(None, description="대상 통합문서 ID")
    sheet_name: str | None = Field(None, description="대상 시트명 (생략 시 active sheet)")
    session_id: str | None = Field(None, description="채팅 세션 ID (멀티턴 슬롯필링 상태 식별)")
    context_range: str | None = Field(None, description="직전 단계에서 확정된 범위 주소(A1:B2)")
    approve: bool = Field(False, description="CONFIRM 작업 승인 여부")


class ExcelLiveActionRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    workbook_id: str | None = None
    sheet_name: str | None = None
    approve: bool = False


class ExcelLiveRestoreLastRequest(BaseModel):
    workbook_id: str | None = Field(None, description="복구 대상 통합문서 ID")
    backup_path: str | None = Field(None, description="지정 백업 경로 (없으면 최신 백업)")


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


@dataclass
class PendingCreateTableSlots:
    session_id: str
    workbook_id: str | None
    sheet_name: str | None
    rows: int | None = None
    cols: int | None = None
    headers: list[str] | None = None
    start_cell: str | None = None
    template_key: str | None = None
    template_follow_up_question: str | None = None
    created_at_ts: float = 0.0
    updated_at_ts: float = 0.0


@dataclass
class PendingExcelOperationSlots:
    session_id: str
    intent: str
    workbook_id: str | None
    sheet_name: str | None
    params: dict[str, Any]
    created_at_ts: float = 0.0
    updated_at_ts: float = 0.0


@dataclass
class ActionRollbackSnapshot:
    action: str
    workbook_id: str
    sheet_name: str
    range_ref: str
    values_2d: list[list[Any]]


_pending_approvals: dict[str, PendingExcelApproval] = {}
_pending_create_table_slots: dict[str, PendingCreateTableSlots] = {}
_pending_operation_slots: dict[str, PendingExcelOperationSlots] = {}
_recent_range_by_workbook: dict[str, str] = {}
_SLOT_TTL_SECONDS = 300


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return max(minimum, float(default))
    try:
        return max(minimum, float(raw))
    except Exception:
        return max(minimum, float(default))


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, int(default))


_COMMAND_PARSE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_PARSE_TIMEOUT_SECONDS", 10.0, 3.0)
_COMMAND_PARSE_MAX_ATTEMPTS = _env_int("EXCEL_LIVE_PARSE_MAX_ATTEMPTS", 2, 1)
_COMMAND_PARSE_RETRY_BACKOFF_SECONDS = _env_float(
    "EXCEL_LIVE_PARSE_RETRY_BACKOFF_SECONDS",
    0.5,
    0.0,
)
_EXCEL_QUEUE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS", 180.0, 10.0)
_EXCEL_QUEUE_LOCK = threading.RLock()
_ROLLBACK_MAX_CELLS = 50000
_SKIP_BACKUP_ACTIONS = {
    "excel_live.filter_rows",  # 뷰 필터가 중심이라 파일 백업 비용 대비 이득이 작다.
    "excel_live.refresh_power_query",  # 새로고침은 원본 값 덮어쓰기 성격이 약함.
    "excel_live.save_workbook",
}
_RECOVERY_BACKUP_ACTIONS = {name for name in EDIT_ACTIONS if name not in _SKIP_BACKUP_ACTIONS}
_ROLLBACK_SNAPSHOT_ACTIONS = {
    "excel_live.write_range",
    "excel_live.clear_range",
    "excel_live.set_formula",
    "excel_live.sort_range",
    "excel_live.dedupe_rows",
    "excel_live.create_table",
}


def _run_in_excel_queue(task_name: str, fn):
    queued_at = time.time()
    acquired = _EXCEL_QUEUE_LOCK.acquire(timeout=_EXCEL_QUEUE_TIMEOUT_SECONDS)
    if not acquired:
        raise ExcelLiveError(
            f"Excel 작업 큐 대기 시간이 초과되었습니다({int(_EXCEL_QUEUE_TIMEOUT_SECONDS)}초): {task_name}"
        )
    wait_ms = int((time.time() - queued_at) * 1000)
    try:
        return fn(), wait_ms
    finally:
        _EXCEL_QUEUE_LOCK.release()


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


def _normalize_range_text(range_ref: str | None) -> str:
    text = str(range_ref or "").strip().upper().replace("$", "")
    return text


def _col_to_idx(col: str) -> int:
    value = str(col or "").strip().upper()
    if not value:
        raise ValueError("열 식별자가 비어 있습니다.")
    n = 0
    for ch in value:
        if ch < "A" or ch > "Z":
            raise ValueError(f"유효하지 않은 열 식별자: {col}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _idx_to_col(idx: int) -> str:
    n = max(1, int(idx))
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _parse_a1_cell(cell_ref: str) -> tuple[str, int] | None:
    m = re.match(r"^([A-Z]+)(\d+)$", str(cell_ref or "").strip().upper())
    if not m:
        return None
    return str(m.group(1)), int(m.group(2))


def _range_from_start_shape(start_cell: str, rows: int, cols: int) -> str:
    parsed = _parse_a1_cell(start_cell)
    if parsed is None:
        return ""
    start_col, start_row = parsed
    row_n = max(1, int(rows))
    col_n = max(1, int(cols))
    end_col = _idx_to_col(_col_to_idx(start_col) + col_n - 1)
    end_row = start_row + row_n - 1
    if row_n == 1 and col_n == 1:
        return f"{start_col}{start_row}"
    return f"{start_col}{start_row}:{end_col}{end_row}"


def _resolve_runtime_range_ref(
    service,
    *,
    workbook_id: str,
    sheet_name: str,
    raw_range: str | None,
    for_cell: bool,
) -> str:
    text = _normalize_range_text(raw_range)
    if not text:
        return ""
    if text == "__ACTIVE_SELECTION__":
        selected = _normalize_range_text(service.get_active_selection_ref(workbook_id, sheet_name))
        return _top_left_cell(selected) if for_cell else selected
    if text == "__ACTIVE_CELL__":
        selected = _normalize_range_text(service.get_active_selection_ref(workbook_id, sheet_name))
        return _top_left_cell(selected)
    if for_cell:
        return _top_left_cell(text)
    return text


def _estimate_a1_cells(range_ref: str) -> int | None:
    text = _normalize_range_text(range_ref)
    if not text:
        return None
    if re.match(r"^[A-Z]+:[A-Z]+$", text):
        return None
    if ":" not in text:
        return 1 if _parse_a1_cell(text) else None
    left, right = text.split(":", 1)
    left_parsed = _parse_a1_cell(left)
    right_parsed = _parse_a1_cell(right)
    if left_parsed is None or right_parsed is None:
        return None
    l_col, l_row = left_parsed
    r_col, r_row = right_parsed
    rows = max(1, abs(r_row - l_row) + 1)
    cols = max(1, abs(_col_to_idx(r_col) - _col_to_idx(l_col)) + 1)
    return rows * cols


def _action_needs_recovery_backup(action: str) -> bool:
    return str(action or "").strip() in _RECOVERY_BACKUP_ACTIONS


def _plan_needs_recovery_backup(plan_steps: list[Any]) -> bool:
    for step in plan_steps:
        action = getattr(step, "action", None)
        if action is None and isinstance(step, dict):
            action = step.get("action")
        if _action_needs_recovery_backup(str(action or "")):
            return True
    return False


def _create_recovery_backup_if_possible(
    *,
    workbook_id: str | None,
    label: str,
) -> dict[str, Any] | None:
    service = get_excel_live_service()
    if not hasattr(service, "create_workbook_backup"):
        return None
    try:
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.create_workbook_backup(resolved_wb, label=label)
    except Exception as exc:
        return {
            "backup_created": False,
            "backup_error": str(exc),
        }


def _snapshot_target_range_for_action(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> ActionRollbackSnapshot | None:
    action_name = str(action or "").strip()
    if action_name not in _ROLLBACK_SNAPSHOT_ACTIONS:
        return None
    service = get_excel_live_service()
    resolved_wb = _resolve_workbook_id(service, workbook_id)
    resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)

    range_ref = ""
    if action_name == "excel_live.write_range":
        values_2d = params.get("values_2d", [])
        if not isinstance(values_2d, list) or not values_2d:
            return None
        row_n = len(values_2d)
        col_n = max((len(r) for r in values_2d if isinstance(r, list)), default=0)
        if col_n <= 0:
            return None
        start_cell = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("start_cell", "__ACTIVE_CELL__")),
            for_cell=True,
        )
        if not start_cell:
            return None
        range_ref = _range_from_start_shape(start_cell, row_n, col_n)
    elif action_name == "excel_live.create_table":
        row_n = int(params.get("rows", 0) or 0)
        col_n = int(params.get("cols", 0) or 0)
        if row_n <= 0 or col_n <= 0:
            return None
        start_cell = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("start_cell", "__ACTIVE_CELL__")),
            for_cell=True,
        )
        if not start_cell:
            return None
        range_ref = _range_from_start_shape(start_cell, row_n, col_n)
    elif action_name == "excel_live.set_formula":
        range_ref = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("range_ref", "")),
            for_cell=False,
        )
    else:
        range_ref = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("target_range", "__ACTIVE_SELECTION__")),
            for_cell=False,
        )
    if not range_ref:
        return None

    estimated = _estimate_a1_cells(range_ref)
    if estimated is None or estimated > _ROLLBACK_MAX_CELLS:
        return None

    snapshot = service.read_range(resolved_wb, resolved_sheet, range_ref)
    values = snapshot.get("values")
    if not isinstance(values, list):
        return None
    row_count = int(snapshot.get("row_count", 0) or 0)
    col_count = int(snapshot.get("col_count", 0) or 0)
    if row_count * col_count > _ROLLBACK_MAX_CELLS:
        return None
    return ActionRollbackSnapshot(
        action=action_name,
        workbook_id=resolved_wb,
        sheet_name=resolved_sheet,
        range_ref=range_ref,
        values_2d=values,
    )


def _restore_action_snapshot(snapshot: ActionRollbackSnapshot | None) -> bool:
    if snapshot is None:
        return False
    try:
        service = get_excel_live_service()
        service.write_range(
            workbook_id=snapshot.workbook_id,
            sheet_name=snapshot.sheet_name,
            start_cell=_top_left_cell(snapshot.range_ref),
            values_2d=snapshot.values_2d,
        )
        return True
    except Exception:
        return False


def _context_key(workbook_id: str | None) -> str:
    return str(workbook_id or "__selected__").strip().lower() or "__selected__"


def _slot_session_key(req: ExcelLiveCommandRequest) -> str:
    sid = str(req.session_id or "").strip()
    if sid:
        return sid
    # session_id가 없으면 요청 간 슬롯 상태를 공유하지 않는다.
    return f"excel-live::stateless::{uuid.uuid4().hex}"


def _cleanup_expired_table_slots() -> None:
    now = time.time()
    expired_keys = [
        key
        for key, slot in _pending_create_table_slots.items()
        if (now - float(slot.updated_at_ts or slot.created_at_ts or now)) > _SLOT_TTL_SECONDS
    ]
    for key in expired_keys:
        _pending_create_table_slots.pop(key, None)


def _cleanup_expired_operation_slots() -> None:
    now = time.time()
    expired_keys = [
        key
        for key, slot in _pending_operation_slots.items()
        if (now - float(slot.updated_at_ts or slot.created_at_ts or now)) > _SLOT_TTL_SECONDS
    ]
    for key in expired_keys:
        _pending_operation_slots.pop(key, None)


def _quick_color_hex(word: str) -> str:
    token = str(word or "").strip().lower()
    if token in {"노란색", "노랑", "yellow"}:
        return "#FFFF00"
    if token in {"빨간색", "빨강", "red"}:
        return "#FF4D4F"
    if token in {"파란색", "파랑", "blue"}:
        return "#4F8CFF"
    if token in {"초록색", "초록", "green"}:
        return "#6AC36A"
    return "#FFFF00"


def _quick_extract_colors(text: str) -> list[str]:
    matches = re.findall(
        r"(노란색|노랑|yellow|빨간색|빨강|red|파란색|파랑|blue|초록색|초록|green)",
        str(text or ""),
        re.IGNORECASE,
    )
    out: list[str] = []
    for raw in matches:
        color = _quick_color_hex(raw)
        if not out or out[-1] != color:
            out.append(color)
    return out


def _quick_parse_condition(text: str) -> tuple[str, float] | None:
    lowered = str(text or "").lower()
    sym_match = re.search(r"(>=|<=|>|<|==|!=)\s*(-?\d+(?:\.\d+)?)", lowered)
    if sym_match:
        return sym_match.group(1), float(sym_match.group(2))

    text_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만|같지 않음|같음)",
        lowered,
    )
    if text_match:
        op_map = {
            "이상": ">=",
            "초과": ">",
            "이하": "<=",
            "미만": "<",
            "같음": "==",
            "같지 않음": "!=",
        }
        return op_map.get(text_match.group(2), ">="), float(text_match.group(1))

    than_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*보다\s*(크거나\s*같|작거나\s*같|큰|작은|크|작)",
        lowered,
    )
    if than_match:
        key = than_match.group(2).replace(" ", "")
        op_map = {
            "크": ">",
            "큰": ">",
            "작": "<",
            "작은": "<",
            "크거나같": ">=",
            "작거나같": "<=",
        }
        op = op_map.get(key)
        if op:
            return op, float(than_match.group(1))
    return None


def _is_color_format_request(lowered: str) -> bool:
    has_color = any(
        token in lowered
        for token in [
            "노란색",
            "노랑",
            "yellow",
            "빨간색",
            "빨강",
            "red",
            "파란색",
            "파랑",
            "blue",
            "초록색",
            "초록",
            "green",
        ]
    )
    has_format_verb = any(
        token in lowered
        for token in ["색칠", "칠해", "배경", "강조", "표시", "highlight", "구분"]
    )
    return has_color and has_format_verb


def _detect_operation_intent(message: str) -> str:
    lowered = str(message or "").lower()
    color_format_request = _is_color_format_request(lowered)
    color_condition_request = color_format_request and (_quick_parse_condition(lowered) is not None)
    if (not color_format_request) and any(
        token in lowered
        for token in [
            "곱해서",
            "곱한",
            "세금 포함",
            "부가세",
            "목표 대비",
            "부족한지",
            "자동으로 계산",
            "계산식",
            "함수 적용",
            "수식 넣",
            "countif",
            "vlookup",
            "if(",
            "건수",
            "개수",
            "찾아와",
            "조회값",
            "조건식",
            "미만이면",
            "수식 결과",
            "결과 확인",
            "달성률",
            "사용률",
            "증감률",
            "마진",
            "마진율",
            "할인",
            "자동으로 들어오",
            "자동으로 나오",
            "정보 나오게",
            "상품명 나오게",
            "진행률",
            "합격",
            "불합격",
            "등급",
        ]
    ):
        return "formula"
    if any(
        token in lowered
        for token in [
            "정렬",
            "높은 순",
            "낮은 순",
            "오름차순",
            "내림차순",
            "순으로",
            "상위",
            "하위",
            "rank",
            "제일 큰",
            "가장 큰",
            "제일 많이",
            "많이 했",
        ]
    ):
        return "sort"
    if any(
        token in lowered
        for token in ["필터", "완료만", "완료", "만 보여", "조건", "골라줘", "추려", "따로 보고", "위험한", "상태 열"]
    ):
        return "filter"
    if any(token in lowered for token in ["중복", "중복된", "중복 제거", "중복 없애"]):
        return "dedupe"
    if any(
        token in lowered
        for token in ["피벗", "집계표", "월별", "부서별", "지역별", "담당자별", "카테고리별", "고객별"]
    ):
        return "pivot"
    if any(
        token in lowered
        for token in ["차트", "그래프", "시각화", "비율로 보고", "한눈에", "추이", "발표용"]
    ):
        return "chart"
    if any(
        token in lowered
        for token in [
            "검증",
            "이상한 값",
            "오류",
            "형식 이상",
            "점검",
            "빠진 값",
            "형식 이상한",
            "계산이 맞는지",
            "문제점",
            "문제",
            "검산",
            "틀린 값",
        ]
    ):
        return "validate"
    if any(
        token in lowered
        for token in [
            "보호",
            "잠금",
            "잠가",
            "입력 제한",
            "드롭다운",
            "유효성",
            "숫자만 입력",
            "날짜만 입력",
            "못 고치게",
            "수정 못",
            "목록에서 고르게",
            "잘못 입력 못",
        ]
    ):
        return "protect"
    if any(
        token in lowered
        for token in ["파일 여러", "시트 여러", "합쳐", "merge", "폴더", "원본 파일"]
    ):
        return "consolidate"
    if any(token in lowered for token in ["vba", "매크로", "power query", "refreshall", "새로고침"]):
        return "automation"
    if any(token in lowered for token in ["비교", "차이", "diff", "다른 값", "바뀐", "지난달", "전월", "전년"]):
        return "compare"
    if any(token in lowered for token in ["예측", "추세", "시뮬레이션", "다음 달", "연말", "forecast", "앞으로"]):
        return "forecast"
    if any(token in lowered for token in ["a4", "인쇄", "pdf", "출력", "제출"]):
        return "print"
    if any(
        token in lowered
        for token in [
            "읽기 전용",
            "보호된 보기",
            "편집이 안",
            "강제로 수정",
            "외부 링크",
            "개인정보",
            "백업",
            "덮어써",
            "권한",
            "되돌릴",
            "되돌려",
        ]
    ):
        return "safety"
    if any(
        token in lowered
        for token in ["#n/a", "#value", "#div/0", "수식이 이상", "합계가 이상", "오류 고쳐", "filter 함수가 안"]
    ):
        return "debug"
    if any(token in lowered for token in ["느려", "멈춰", "버벅", "업데이트가 안 돼"]):
        return "performance"
    if any(token in lowered for token in ["피벗이 뭐", "power query가 뭐", "뭐야", "설명해줘", "무슨 뜻"]):
        return "explain"
    if any(
        token in lowered
        for token in [
            "정리해줘",
            "알아서",
            "보기 좋게",
            "보고용",
            "중요한 내용",
            "요약해줘",
            "자동으로",
            "시트 나눠",
            "양식만",
            "색깔로 구분",
            "월만 따로",
            "버튼 누르면",
            "반복되는 작업",
            "재고 언제",
            "예산 초과",
            "지출 내역",
        ]
    ) and (not color_condition_request):
        return "general"
    return ""


def _action_to_operation_intent(action: str) -> str:
    mapping = {
        "excel_live.set_formula": "formula",
        "excel_live.verify_formula_result": "formula",
        "excel_live.sort_range": "sort",
        "excel_live.filter_rows": "filter",
        "excel_live.dedupe_rows": "dedupe",
        "excel_live.pivot_table": "pivot",
        "excel_live.create_chart": "chart",
        "excel_live.validate_data": "validate",
        "excel_live.protect_sheet": "protect",
        "excel_live.set_data_validation": "protect",
        "excel_live.consolidate_sheets": "consolidate",
        "excel_live.consolidate_workbooks_from_folder": "consolidate",
        "excel_live.refresh_power_query": "automation",
        "excel_live.run_vba_macro": "automation",
        "excel_live.compare_ranges": "compare",
        "excel_live.forecast_linear": "forecast",
    }
    return mapping.get(str(action or ""), "")


def _extract_column_for_keyword(text: str, keyword: str) -> str | None:
    m = re.search(rf"([A-Z])\s*열[^A-Z0-9]{{0,8}}{keyword}", text, re.IGNORECASE)
    if m:
        return str(m.group(1)).upper()
    m = re.search(rf"{keyword}[^A-Z0-9]{{0,8}}([A-Z])\s*열", text, re.IGNORECASE)
    if m:
        return str(m.group(1)).upper()
    return None


def _parse_row_bounds_from_range(range_ref: str | None) -> tuple[int, int]:
    text = str(range_ref or "").strip().upper()
    m = re.match(r"[A-Z]+(\d+):[A-Z]+(\d+)", text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        if start == 1:
            start = 2
        return start, max(start, end)
    m = re.match(r"[A-Z]+(\d+)", text)
    if m:
        start = int(m.group(1))
        return max(2, start), max(2, start + 30)
    return 2, 200


def _next_column(col: str) -> str:
    value = str(col or "").strip().upper()
    if not value:
        return "D"
    n = 0
    for ch in value:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    n += 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _build_quick_action_plan(message: str, context_range: str | None) -> list[dict[str, Any]] | None:
    text = str(message or "").strip()
    lowered = text.lower()
    range_match = re.search(r"\b([A-Z]+\d+:[A-Z]+\d+|[A-Z]+:[A-Z]+|[A-Z]+\d+)\b", text, re.IGNORECASE)
    range_ref = str(range_match.group(1)).upper() if range_match else ""
    col_match = re.search(r"\b([A-Z])\s*열\b", text, re.IGNORECASE)
    col_range_ref = f"{str(col_match.group(1)).upper()}:{str(col_match.group(1)).upper()}" if col_match else ""

    if any(
        token in lowered
        for token in [
            "열린 통합문서",
            "통합문서 목록",
            "워크북 목록",
            "열린 파일 목록",
            "열린 엑셀 파일",
            "열려 있는 엑셀 파일",
            "엑셀 파일 확인",
            "list workbooks",
            "workbook list",
        ]
    ):
        return [{"action": "excel_live.list_workbooks", "params": {}, "reason": "빠른 규칙 기반 워크북 목록 조회"}]

    select_match = re.search(
        r"(?:워크북|통합문서|파일|workbook)\s+([^\s]+\.xlsx|[^\s]+)\s*(?:선택|전환|열어|열기|select|switch)",
        text,
        re.IGNORECASE,
    )
    if select_match:
        target = str(select_match.group(1)).strip().strip("\"'")
        if target:
            return [
                {
                    "action": "excel_live.select_workbook",
                    "params": {"workbook_id": target},
                    "reason": "빠른 규칙 기반 워크북 선택",
                }
            ]

    if (range_ref or col_range_ref) and any(
        token in lowered for token in ["읽어", "보여", "확인", "조회", "read", "show", "display"]
    ):
        target_range = range_ref or col_range_ref
        return [
            {
                "action": "excel_live.read_range",
                "params": {"range_ref": target_range},
                "reason": "빠른 규칙 기반 범위 조회",
            }
        ]

    if any(token in lowered for token in ["테두리", "경계선", "border"]):
        target = _normalize_range_text(context_range) or range_ref or "__ACTIVE_SELECTION__"
        return [
            {
                "action": "excel_live.apply_border",
                "params": {
                    "target_range": target,
                    "line_style": "continuous",
                    "weight": "medium",
                    "color": "#000000",
                },
                "reason": "빠른 규칙 기반 테두리 적용",
            }
        ]

    if any(
        token in lowered
        for token in [
            "배경색",
            "색칠",
            "칠해",
            "강조",
            "highlight",
            "노란색",
            "노랑",
            "yellow",
            "빨간색",
            "빨강",
            "red",
            "파란색",
            "파랑",
            "blue",
            "초록색",
            "초록",
            "green",
        ]
    ):
        target = _normalize_range_text(context_range) or range_ref or "__ACTIVE_SELECTION__"
        colors = _quick_extract_colors(lowered)
        primary = colors[0] if colors else "#FFFF00"
        condition = _quick_parse_condition(lowered)
        if condition is not None:
            operator, threshold = condition
            has_else_branch = any(token in lowered for token in ["아니면", "나머지", "그 외", "그외", "else"])
            if has_else_branch and len(colors) >= 2:
                return [
                    {
                        "action": "excel_live.fill_range",
                        "params": {"target_range": target, "fill_color": colors[1]},
                        "reason": "빠른 규칙 기반 조건 외 기본 색상 채우기",
                    },
                    {
                        "action": "excel_live.highlight_by_condition",
                        "params": {
                            "target_range": target,
                            "operator": operator,
                            "threshold": threshold,
                            "fill_color": primary,
                        },
                        "reason": "빠른 규칙 기반 조건부 강조",
                    },
                ]
            return [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {
                        "target_range": target,
                        "operator": operator,
                        "threshold": threshold,
                        "fill_color": primary,
                    },
                    "reason": "빠른 규칙 기반 조건부 강조",
                }
            ]
        return [
            {
                "action": "excel_live.fill_range",
                "params": {"target_range": target, "fill_color": primary},
                "reason": "빠른 규칙 기반 배경색 적용",
            }
        ]

    if any(
        token in lowered
        for token in ["내용 전부 지우", "전부 지워", "싹 지워", "비워", "깨끗하게", "clear", "wipe"]
    ):
        target = _normalize_range_text(context_range) or range_ref or "__ACTIVE_SELECTION__"
        return [
            {
                "action": "excel_live.clear_range",
                "params": {"target_range": target},
                "reason": "빠른 규칙 기반 내용 비우기",
            }
        ]

    if any(token in lowered for token in ["저장", "save"]):
        return [{"action": "excel_live.save_workbook", "params": {}, "reason": "빠른 규칙 기반 저장"}]
    return None


def _looks_like_excel_request(message: str) -> bool:
    lowered = str(message or "").lower()
    tokens = [
        "엑셀",
        "통합문서",
        "워크북",
        "표",
        "테이블",
        "양식",
        "정리",
        "정렬",
        "필터",
        "중복",
        "빈칸",
        "이상치",
        "오류",
        "합계",
        "평균",
        "개수",
        "계산",
        "함수",
        "수식",
        "피벗",
        "집계",
        "요약",
        "그래프",
        "차트",
        "대시보드",
        "검증",
        "드롭다운",
        "자동화",
        "매크로",
        "vba",
        "power query",
        "파일",
        "시트",
        "인쇄",
        "pdf",
        "비교",
        "추세",
        "예측",
        "매출",
        "비용",
        "재고",
        "고객",
        "근태",
        "성적",
        "출석",
        "보고",
        "상태",
        "문제",
        "등급",
        "합격",
        "불합격",
        "지연",
        "마감",
        "조회",
        "찾아",
        "자동",
        "드롭다운",
        "유효성",
        "보호",
        "잠금",
        "입력 제한",
        "분리",
        "결합",
        "텍스트",
        "월별",
        "분기",
        "전월",
        "전년",
        "가계부",
        "근무시간",
        "연차",
        "야근",
        "crm",
        "vip",
        "보기 좋게",
        "알아서",
        "프린트",
        "a4",
        "출력",
        "읽기 전용",
        "보호된 보기",
        "편집이 안",
        "강제로 수정",
        "외부 링크",
        "개인정보",
        "백업",
        "덮어써",
        "되돌릴",
        "복구",
        "#n/a",
        "#value",
        "#div/0",
        "느려",
        "멈춰",
        "버벅",
        "설명",
        "뭐야",
        "권한",
        "잠가",
        "수정 못",
        "합쳐줘",
        "나눠줘",
        "색깔",
        "월만",
        "제일 큰",
        "제일 많이",
    ]
    if any(token in lowered for token in tokens):
        return True
    if re.search(r"\b([a-z]{1,3}\d{1,7}:[a-z]{1,3}\d{1,7}|[a-z]{1,3}\d{1,7})\b", lowered, re.IGNORECASE):
        return True
    if re.search(r"\b[a-z]\s*열\b", lowered, re.IGNORECASE):
        return True
    if any(token in lowered for token in ["read", "show", "display", "workbook", "sheet", "formula"]):
        return True
    return False


def _build_generic_excel_follow_up(message: str) -> str:
    lowered = str(message or "").lower()
    if any(token in lowered for token in ["읽기 전용", "보호된 보기", "편집이 안", "강제로 수정"]):
        return (
            "파일 상태를 먼저 확인해야 합니다. 읽기 전용/보호된 보기면 직접 수정은 불가하니, "
            "편집 가능 사본을 만들어 같은 작업을 진행할까요?"
        )
    if any(token in lowered for token in ["외부 링크", "개인정보", "백업", "덮어써", "권한"]):
        return (
            "이 요청은 안전 정책 확인이 필요합니다. 원본 백업 후 진행할지, "
            "원본 덮어쓰기 허용 여부를 먼저 알려주세요."
        )
    if any(token in lowered for token in ["#n/a", "#value", "#div/0", "수식이 이상", "합계가 이상", "오류 고쳐"]):
        return (
            "디버깅을 위해 오류가 나는 열/범위를 알려주세요. 예: D2:D200. "
            "기대하는 계산식(예: 수량*단가)도 함께 주시면 바로 점검할 수 있어요."
        )
    if any(token in lowered for token in ["느려", "멈춰", "버벅"]):
        return (
            "성능 진단을 위해 어떤 작업에서 느린지 알려주세요. 예: 피벗 새로고침/대용량 수식/조건부서식. "
            "원하면 원본 보존 상태로 진단용 요약 시트를 먼저 만들겠습니다."
        )
    if any(token in lowered for token in ["a4", "인쇄", "pdf", "출력", "제출"]):
        return (
            "출력 형식을 정할게요. A4 가로/세로, 한 페이지 맞춤 여부, PDF 저장 필요 여부를 알려주세요."
        )
    if any(token in lowered for token in ["뭐야", "설명", "무슨 뜻"]):
        if "피벗" in lowered:
            return (
                "피벗은 많은 데이터를 기준별로 요약 집계하는 기능입니다. "
                "원하면 지금 데이터로 월별/부서별 합계 피벗을 바로 만들어드릴까요?"
            )
        if "power query" in lowered or "파워쿼리" in lowered:
            return (
                "Power Query는 여러 파일을 가져오고 정리 과정을 자동화하는 기능입니다. "
                "원하면 현재 파일 기준으로 새로고침 흐름을 바로 구성해드릴까요?"
            )
        return "원하는 기능을 쉬운 예시로 설명해드릴게요. 피벗/필터/수식/차트 중 어떤 항목이 궁금한가요?"
    if any(
        token in lowered
        for token in [
            "표",
            "양식",
            "가계부",
            "출석부",
            "근태",
            "회의록",
            "체크리스트",
            "관리표",
            "견적서",
            "청구서",
        ]
    ):
        return (
            "표/양식 목적을 먼저 정할게요. 예: 가계부, 매출, 근태, 재고. "
            "일별/월별 중 어떤 기준으로 만들지와 자동 계산 항목 포함 여부도 알려주세요."
        )
    if any(
        token in lowered
        for token in ["서식", "깔끔", "보기 좋게", "테두리", "정렬", "열 너비", "행 높이", "제목 고정", "필터", "색깔"]
    ):
        return (
            "서식 기준을 정해볼까요? 예: 제목행 강조, 열 너비 자동, 금액 콤마, 날짜 형식 통일. "
            "원본 데이터는 유지하고 서식만 바꿀지도 알려주세요."
        )
    if any(
        token in lowered
        for token in ["데이터 정리", "지저분", "중복", "빈칸", "클리닝", "공백", "형식 맞춰", "통일"]
    ):
        return (
            "정리 기준을 알려주세요. 예: 중복 제거(기준 열), 빈칸 처리(삭제/대체), 형식 통일(날짜/전화번호). "
            "원본 보존 후 새 시트에 정리본 생성도 가능합니다."
        )
    if any(
        token in lowered
        for token in ["분리", "합쳐", "합치", "텍스트", "괄호", "하이픈", "쉼표", "도메인", "앞의", "뒤의"]
    ):
        return "텍스트 가공 기준을 알려주세요. 예: 공백/하이픈/쉼표 기준 분리, 앞 3자리 추출, 열 결합."
    if any(
        token in lowered
        for token in ["날짜", "오늘", "월별", "월만", "분기", "근속", "나이", "마감", "주말", "평일", "기간"]
    ):
        return (
            "날짜 기준을 알려주세요. 예: 어떤 날짜 열을 기준으로 볼지, 이번달/전월/분기 중 어떤 집계를 원하는지."
        )
    if any(token in lowered for token in ["대시보드", "보고", "요약"]):
        return (
            "보고/대시보드 방향을 정해볼까요? 예: 월별 매출 요약, 담당자별 실적, 핵심 차트 3개. "
            "원본 유지 여부(유지/수정)도 알려주세요."
        )
    if any(token in lowered for token in ["자동화", "매크로", "vba", "power query"]):
        return (
            "자동화 요구를 확인할게요. 예: 어떤 작업 순서를 자동화할지, 입력 데이터 위치, 결과 시트 이름. "
            "현재는 기본 엑셀 작업 자동화(정리/계산/집계/차트)부터 단계적으로 구성합니다."
        )
    if any(token in lowered for token in ["차트", "그래프"]):
        return "차트 목적을 알려주세요. 예: 월별 추이(선), 항목 비교(막대), 비율(원형). 기준 열도 함께 알려주세요."
    if any(token in lowered for token in ["피벗", "집계", "요약"]):
        return "집계 기준을 알려주세요. 예: 행=월, 값=매출 합계. 필요하면 열 기준도 지정해 주세요."
    if any(token in lowered for token in ["정렬", "필터", "중복"]):
        return "기준 열이 필요합니다. 예: 매출 열 내림차순, 상태=완료 필터, 전화번호 기준 중복 제거."
    if any(token in lowered for token in ["비교", "차이", "전월", "전년", "바뀐", "증감"]):
        return "비교 기준을 알려주세요. 예: 기준 열(ID/코드), 비교 대상(지난달/작년/다른 시트), 출력 방식."
    if any(
        token in lowered
        for token in ["보호", "잠금", "드롭다운", "유효성", "숫자만", "날짜만", "입력 제한", "필수 입력"]
    ):
        return (
            "입력 제어 범위를 알려주세요. 예: 어떤 열을 드롭다운/숫자제한/날짜제한으로 둘지, "
            "수식 셀 잠금 여부."
        )
    if any(token in lowered for token in ["합계", "평균", "계산", "수식", "함수"]):
        return "계산 기준 열을 알려주세요. 예: B열 수량, C열 단가, 결과 D열. 조건 계산이면 기준값도 알려주세요."
    return (
        "어떤 작업을 원하시는지 한 단계만 더 구체화해 주세요. "
        "예: 표 생성 / 정렬·필터 / 계산 수식 / 피벗 집계 / 차트 / 검증 중 하나와 기준 열."
    )


def _extract_formula_common_params(text: str) -> dict[str, Any]:
    lowered = text.lower()
    params: dict[str, Any] = {}

    if any(token in lowered for token in ["곱해서", "곱한", "곱해", "수량", "단가", "가격"]):
        params["formula_mode"] = "multiply"
        qty_col = _extract_column_for_keyword(text, "수량")
        price_col = _extract_column_for_keyword(text, "단가|가격")
        if qty_col:
            params["qty_column"] = qty_col
        if price_col:
            params["price_column"] = price_col
        if "qty_column" in params and "price_column" in params:
            params["result_column"] = _next_column(str(params["price_column"]))

    if any(token in lowered for token in ["세금", "부가세", "세율", "tax"]):
        params["formula_mode"] = "tax"
        base_col = _extract_column_for_keyword(text, "가격|단가|금액")
        if base_col:
            params["base_column"] = base_col
            params["result_column"] = _next_column(base_col)
        rate_match = re.search(r"(\d+(?:\.\d+)?)\s*%", lowered)
        if rate_match:
            params["tax_rate"] = float(rate_match.group(1)) / 100.0

    if any(token in lowered for token in ["목표 대비", "부족한지", "목표", "실제"]):
        params["formula_mode"] = "gap"
        target_col = _extract_column_for_keyword(text, "목표")
        actual_col = _extract_column_for_keyword(text, "실제")
        if target_col:
            params["target_column"] = target_col
        if actual_col:
            params["actual_column"] = actual_col
            params["result_column"] = _next_column(actual_col)

    if any(token in lowered for token in ["건수", "개수", "countif", "몇 개", "몇개"]):
        params["formula_mode"] = "countif"
        count_col = (
            _extract_column_for_keyword(text, "상태|구분|카테고리|분류")
            or _extract_column_for_keyword(text, "완료|미완료|지연|승인|반려")
        )
        if count_col:
            params["count_column"] = count_col
            params["result_column"] = _next_column(count_col)
        cond_match = re.search(r"['\"]([^'\"]+)['\"]", text)
        if cond_match:
            params["count_condition"] = str(cond_match.group(1)).strip()
        else:
            for token in ["완료", "미완료", "지연", "승인", "반려"]:
                if token in text:
                    params["count_condition"] = token
                    break

    if any(
        token in lowered
        for token in [
            "vlookup",
            "조회값",
            "찾아와",
            "찾아와줘",
            "매칭",
            "자동으로 들어오",
            "자동으로 나오",
            "이름 넣으면",
            "코드 넣으면",
            "정보 나오게",
            "상품명 나오게",
        ]
    ):
        params["formula_mode"] = "vlookup"
        lookup_col = _extract_column_for_keyword(text, "조회값|키|코드")
        if lookup_col:
            params["lookup_column"] = lookup_col
        table_match = re.search(r"([A-Z])\s*열\s*부터\s*([A-Z])\s*열", text, re.IGNORECASE)
        if table_match:
            params["table_start_column"] = str(table_match.group(1)).upper()
            params["table_end_column"] = str(table_match.group(2)).upper()
        idx_match = re.search(r"반환[^\d]{0,8}(\d+)\s*열", text)
        if idx_match:
            params["return_index"] = int(idx_match.group(1))

    if any(
        token in lowered
        for token in ["조건식", "if(", "조건에 따라", "이면", "미만이면", "이상이면", "등급", "합격", "불합격"]
    ):
        params["formula_mode"] = "if_compare"
        compare_col = _extract_column_for_keyword(text, "점수|실적|값|금액")
        if compare_col:
            params["compare_column"] = compare_col
            params["result_column"] = _next_column(compare_col)
        threshold_match = re.search(r"(\d+(?:\.\d+)?)\s*(미만|이하|초과|이상)", text)
        if threshold_match:
            params["threshold"] = float(threshold_match.group(1))
            op_map = {"미만": "<", "이하": "<=", "초과": ">", "이상": ">="}
            params["compare_op"] = op_map.get(threshold_match.group(2), "<")
        label_match = re.search(r"['\"]([^'\"]+)['\"]\s*[,/]\s*['\"]([^'\"]+)['\"]", text)
        if label_match:
            params["true_value"] = str(label_match.group(1))
            params["false_value"] = str(label_match.group(2))
    return params


def _build_formula_retry_variant(formula_a1: str) -> str | None:
    formula = str(formula_a1 or "").strip()
    if not formula.startswith("="):
        return None
    if formula.upper().startswith("=IFERROR("):
        return None
    return f"=IFERROR({formula[1:]},0)"


def _is_numeric_formula_candidate(formula_a1: str) -> bool:
    formula = str(formula_a1 or "").upper()
    if "COUNTIF(" in formula or "SUM(" in formula or "AVERAGE(" in formula:
        return True
    if any(op in formula for op in ["*", "/", "+", "-"]):
        return True
    return False


def _extract_operation_hints(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    lowered = text.lower()
    hints: dict[str, Any] = {
        "intent": _detect_operation_intent(text),
        "affirmative": any(token in lowered for token in ["응", "네", "좋아", "그래", "맞아", "yes", "ok"]),
        "params": {},
    }
    hints["params"]["raw_message"] = text

    range_match = re.search(r"\b([A-Z]+\d+:[A-Z]+\d+|[A-Z]+:[A-Z]+|[A-Z]+\d+)\b", text, re.IGNORECASE)
    if range_match:
        hints["params"]["target_range"] = str(range_match.group(1)).upper()
        hints["params"]["source_range"] = str(range_match.group(1)).upper()
    range_matches = re.findall(r"\b([A-Z]+\d+:[A-Z]+\d+)\b", text, re.IGNORECASE)
    if len(range_matches) >= 2:
        hints["params"]["left_range"] = str(range_matches[0]).upper()
        hints["params"]["right_range"] = str(range_matches[1]).upper()

    if "formula" == hints["intent"]:
        hints["params"].update(_extract_formula_common_params(text))

    if "sort" == hints["intent"]:
        if any(token in lowered for token in ["높은", "내림", "내림차순", "큰 순"]):
            hints["params"]["order"] = "desc"
        elif any(token in lowered for token in ["낮은", "오름", "오름차순", "작은 순"]):
            hints["params"]["order"] = "asc"
        top_match = re.search(r"상위\s*(\d{1,3})", lowered)
        if top_match:
            hints["params"]["top_n"] = int(top_match.group(1))
        if "매출" in lowered:
            hints["params"]["key_column"] = "매출"
        elif "수량" in lowered:
            hints["params"]["key_column"] = "수량"
        elif "점수" in lowered:
            hints["params"]["key_column"] = "점수"
        elif "비용" in lowered:
            hints["params"]["key_column"] = "비용"

    if "filter" == hints["intent"]:
        if "완료" in lowered:
            hints["params"]["column"] = "상태"
            hints["params"]["operator"] = "=="
            hints["params"]["value"] = "완료"
        elif "미완료" in lowered:
            hints["params"]["column"] = "상태"
            hints["params"]["operator"] = "!="
            hints["params"]["value"] = "완료"
        if "위험" in lowered and "지연" in lowered:
            hints["params"]["column"] = hints["params"].get("column", "상태")
            hints["params"]["operator"] = hints["params"].get("operator", "!=")
            hints["params"]["value"] = hints["params"].get("value", "완료")
        score_match = re.search(r"(\d+(?:\.\d+)?)\s*점?\s*(이상|초과|이하|미만)", lowered)
        if score_match:
            op_map = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<"}
            hints["params"]["column"] = hints["params"].get("column", "점수")
            hints["params"]["operator"] = op_map.get(score_match.group(2), ">=")
            hints["params"]["value"] = float(score_match.group(1))

    if "dedupe" == hints["intent"]:
        if "전화번호" in lowered:
            hints["params"]["key_columns"] = ["전화번호"]
        elif "이메일" in lowered:
            hints["params"]["key_columns"] = ["이메일"]
        elif "이름" in lowered:
            hints["params"]["key_columns"] = ["이름"]

    if "pivot" == hints["intent"]:
        if "월별" in lowered:
            hints["params"]["row_field"] = "월"
        if "상품" in lowered:
            hints["params"]["column_field"] = "상품명"
        if "매출" in lowered:
            hints["params"]["value_field"] = "매출"
            hints["params"]["agg"] = "sum"
        elif "비용" in lowered:
            hints["params"]["value_field"] = "비용"
            hints["params"]["agg"] = "sum"

    if "chart" == hints["intent"]:
        if any(token in lowered for token in ["선 그래프", "추이", "변화"]):
            hints["params"]["chart_type"] = "line"
        elif any(token in lowered for token in ["막대", "비교"]):
            hints["params"]["chart_type"] = "bar"
        elif any(token in lowered for token in ["원형", "비율"]):
            hints["params"]["chart_type"] = "pie"
        if "발표" in lowered:
            hints["params"]["title"] = "발표용 핵심 차트"
        if "매출" in lowered:
            hints["params"]["title"] = "매출 차트"

    if "validate" == hints["intent"]:
        checks: list[str] = []
        if any(token in lowered for token in ["빈칸", "빈 값", "누락"]):
            checks.append("empty")
        if any(token in lowered for token in ["음수"]):
            checks.append("negative")
        if any(token in lowered for token in ["이상치", "튀는 값"]):
            checks.append("outlier")
        if any(token in lowered for token in ["날짜"]):
            checks.append("date_range")
            year_match = re.search(r"(20\d{2})", lowered)
            if year_match:
                year = int(year_match.group(1))
                hints["params"]["date_min"] = f"{year}-01-01"
                hints["params"]["date_max"] = f"{year}-12-31"
        if checks:
            hints["params"]["checks"] = checks

    if "protect" == hints["intent"]:
        if any(token in lowered for token in ["드롭다운", "목록", "선택", "목록에서 고르게"]):
            hints["params"]["mode"] = "validation_list"
            if any(token in lowered for token in ["완료", "진행", "지연", "보류"]):
                hints["params"]["source"] = "완료,진행중,지연,보류"
        elif any(token in lowered for token in ["숫자만", "잘못 입력 못", "숫자 입력"]):
            hints["params"]["mode"] = "validation_numeric"
            hints["params"]["minimum"] = 0
            hints["params"]["maximum"] = 1000000000
        elif any(token in lowered for token in ["날짜만", "날짜 입력"]):
            hints["params"]["mode"] = "validation_date"
            hints["params"]["minimum"] = 20000101
            hints["params"]["maximum"] = 20991231
        else:
            hints["params"]["mode"] = "sheet_protect"
            if any(token in lowered for token in ["수식", "잠가", "잠금", "못 고치", "수정 못"]):
                hints["params"]["lock_formula_cells"] = True
            if (
                any(token in lowered for token in ["입력칸", "입력 범위", "입력만"])
                and hints["params"].get("target_range")
            ):
                hints["params"]["unlock_range"] = hints["params"]["target_range"]

    if "consolidate" == hints["intent"]:
        if "파일" in lowered or "폴더" in lowered:
            hints["params"]["scope"] = "workbooks"
        else:
            hints["params"]["scope"] = "sheets"
        if "원본" in lowered and "유지" in lowered:
            hints["params"]["output_sheet"] = "통합결과"

    if "automation" == hints["intent"]:
        if "power query" in lowered or "새로고침" in lowered:
            hints["params"]["mode"] = "refresh"
        else:
            hints["params"]["mode"] = "vba"
        macro_match = re.search(r"매크로\s*([A-Za-z_][A-Za-z0-9_\.]*)", text)
        if macro_match is None:
            macro_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*매크로", text)
        if macro_match:
            hints["params"]["macro_name"] = str(macro_match.group(1))

    if "compare" == hints["intent"]:
        sheet_matches = re.findall(r"([^\s,]+)\s*시트", text)
        if len(sheet_matches) >= 2:
            hints["params"]["left_sheet"] = sheet_matches[0]
            hints["params"]["right_sheet"] = sheet_matches[1]
        if "주문번호" in lowered:
            hints["params"]["compare_key"] = "주문번호"
        if "금액" in lowered:
            hints["params"]["compare_fields"] = ["금액"]

    if "forecast" == hints["intent"]:
        horizon_match = re.search(r"(\d{1,2})\s*(개월|달|월|주)", lowered)
        if horizon_match:
            hints["params"]["horizon"] = int(horizon_match.group(1))

    if "print" == hints["intent"]:
        if "a4" in lowered:
            hints["params"]["paper"] = "A4"
        if "가로" in lowered:
            hints["params"]["orientation"] = "landscape"
        elif "세로" in lowered:
            hints["params"]["orientation"] = "portrait"
        if "pdf" in lowered:
            hints["params"]["output_format"] = "pdf"

    if "safety" == hints["intent"]:
        if "읽기 전용" in lowered:
            hints["params"]["state"] = "read_only"
        elif "보호된 보기" in lowered:
            hints["params"]["state"] = "protected_view"
        elif "덮어써" in lowered:
            hints["params"]["state"] = "overwrite"
        elif "백업" in lowered:
            hints["params"]["state"] = "backup"
        elif "외부 링크" in lowered:
            hints["params"]["state"] = "external_link"
        elif "개인정보" in lowered:
            hints["params"]["state"] = "pii"

    if "debug" == hints["intent"]:
        if "#n/a" in lowered:
            hints["params"]["error_code"] = "#N/A"
        elif "#value" in lowered:
            hints["params"]["error_code"] = "#VALUE!"
        elif "#div/0" in lowered:
            hints["params"]["error_code"] = "#DIV/0!"
        if "합계" in lowered:
            hints["params"]["issue"] = "sum_mismatch"

    if "performance" == hints["intent"]:
        if "피벗" in lowered:
            hints["params"]["suspect"] = "pivot_refresh"
        elif "수식" in lowered:
            hints["params"]["suspect"] = "formula_recalc"
        elif "조건부서식" in lowered:
            hints["params"]["suspect"] = "conditional_formatting"

    if "explain" == hints["intent"]:
        if "피벗" in lowered:
            hints["params"]["topic"] = "pivot"
        elif "power query" in lowered or "파워쿼리" in lowered:
            hints["params"]["topic"] = "power_query"
        elif "filter" in lowered or "필터" in lowered:
            hints["params"]["topic"] = "filter"
        else:
            hints["params"]["topic"] = "general"

    return hints


def _extract_formula_params_freeform(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    return _extract_formula_common_params(text)


def _merge_operation_slots(
    current: PendingExcelOperationSlots | None,
    *,
    session_key: str,
    req: ExcelLiveCommandRequest,
    hints: dict[str, Any],
    parsed: dict[str, Any] | None,
) -> PendingExcelOperationSlots | None:
    hint_intent = str(hints.get("intent") or "").strip()
    parsed_intent = ""
    first_action = ""
    if parsed and isinstance(parsed.get("action_plan"), list) and parsed["action_plan"]:
        first = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        first_action = str(first.get("action", "")).strip()
        parsed_intent = _action_to_operation_intent(first.get("action"))

    if current is None:
        # 파서가 이미 구체 액션을 만들었으면(예: write/read/set_formula 등)
        # 키워드 기반 operation 슬롯으로 다시 감싸지 않는다.
        if first_action:
            return None
        # LLM이 액션 계획을 냈다면 우선 신뢰하고, 없을 때만 룰 힌트로 폴백한다.
        intent = parsed_intent or hint_intent
    else:
        intent = str(current.intent or "").strip()
        # 일반(general) 슬롯에서 구체 인텐트가 들어오면 즉시 승격해 멀티턴을 마무리한다.
        if intent in {"", "general"}:
            if parsed_intent and parsed_intent != "general":
                intent = parsed_intent
            elif hint_intent and hint_intent != "general":
                intent = hint_intent
    if not intent:
        return None

    now = time.time()
    slot = current or PendingExcelOperationSlots(
        session_id=session_key,
        intent=intent,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        params={},
        created_at_ts=now,
        updated_at_ts=now,
    )
    slot.intent = intent
    slot.workbook_id = req.workbook_id or slot.workbook_id
    slot.sheet_name = req.sheet_name or slot.sheet_name
    slot.params.update(hints.get("params", {}))
    if slot.intent == "formula":
        slot.params.update(_extract_formula_params_freeform(req.message))

    if parsed and isinstance(parsed.get("action_plan"), list) and parsed["action_plan"]:
        first = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        if _action_to_operation_intent(first.get("action")) == slot.intent and isinstance(first.get("params"), dict):
            slot.params.update(first["params"])
    slot.updated_at_ts = now
    return slot


def _operation_follow_up(slot: PendingExcelOperationSlots) -> str:
    intent = slot.intent
    if intent == "formula":
        if slot.params.get("range_ref"):
            return ""
        mode = str(slot.params.get("formula_mode") or "").strip()
        if not mode:
            return "어떤 계산을 원하시나요? 예: 수량*단가 / 세금 포함 / 목표 대비 차이"
        if mode == "multiply":
            if not slot.params.get("qty_column") or not slot.params.get("price_column"):
                return "수량 열과 단가 열이 필요합니다. 예: B열이 수량, C열이 단가"
        if mode == "tax":
            if not slot.params.get("base_column"):
                return "기준 가격 열을 알려주세요. 예: C열"
            if slot.params.get("tax_rate") is None:
                return "세율이 필요합니다. 예: 10%"
        if mode == "gap":
            if not slot.params.get("target_column") or not slot.params.get("actual_column"):
                return "목표 열과 실제 열을 알려주세요. 예: C열이 목표, D열이 실제"
        if mode == "countif":
            if not slot.params.get("count_column"):
                return "건수를 셀 기준 열을 알려주세요. 예: 상태가 있는 B열"
        if mode == "vlookup":
            if not slot.params.get("lookup_column"):
                return "조회값 열이 필요합니다. 예: 코드가 있는 A열"
            if not slot.params.get("table_start_column") or not slot.params.get("table_end_column"):
                return "참조 표의 열 범위를 알려주세요. 예: F열부터 H열"
            if not slot.params.get("return_index"):
                return "반환할 열 번호를 알려주세요. 예: 2열"
        if mode == "if_compare":
            if not slot.params.get("compare_column"):
                return "조건 비교 열이 필요합니다. 예: 점수 C열"
            if slot.params.get("threshold") is None:
                return "기준값이 필요합니다. 예: 70 미만이면"
        return ""
    if intent == "sort" and not slot.params.get("key_column"):
        return "어떤 열 기준으로 정렬할까요? 예: 매출 열 기준 내림차순"
    if intent == "filter":
        if not slot.params.get("column"):
            return "어떤 열을 기준으로 필터할까요? 예: 상태 열"
        if slot.params.get("value") is None:
            return "필터 값이 필요합니다. 예: 완료만 / 60점 미만"
    if intent == "dedupe" and not slot.params.get("key_columns"):
        return "중복 기준 열을 알려주세요. 예: 전화번호 기준"
    if intent == "pivot":
        if not slot.params.get("row_field"):
            return "피벗의 행 기준이 필요합니다. 예: 월"
        if not slot.params.get("value_field"):
            return "집계할 값 열이 필요합니다. 예: 매출"
    if intent == "chart" and not slot.params.get("chart_type"):
        return "차트 종류를 선택해 주세요. 예: 선 그래프 / 막대 그래프 / 원형 차트"
    if intent == "protect":
        mode = str(slot.params.get("mode") or "").strip()
        if not mode:
            return "보호 유형을 알려주세요. 예: 수식 셀 잠금 / 드롭다운 / 숫자만 입력 제한"
        if mode == "validation_list" and not slot.params.get("source"):
            return "드롭다운 목록 값을 알려주세요. 예: 완료,진행중,지연,보류"
        return ""
    if intent == "consolidate":
        scope = str(slot.params.get("scope") or "").strip()
        if not scope:
            return "통합 대상을 알려주세요. 예: 시트 여러 개 / 폴더의 파일 여러 개"
        if scope == "workbooks" and not slot.params.get("folder_path"):
            return "통합할 폴더 경로가 필요합니다. 예: C:/data/monthly"
        if scope == "sheets" and not slot.params.get("source_sheets"):
            return "통합할 시트명을 알려주세요. 예: 1월,2월,3월"
        return ""
    if intent == "automation":
        mode = str(slot.params.get("mode") or "").strip()
        if mode == "vba" and not slot.params.get("macro_name"):
            return "실행할 매크로 이름을 알려주세요. 예: Module1.RefreshReport"
        if not mode:
            return "자동화 방식을 알려주세요. 예: VBA 매크로 실행 / Power Query 새로고침"
        return ""
    if intent == "compare":
        if not slot.params.get("left_sheet") or not slot.params.get("right_sheet"):
            return "비교할 두 시트명을 알려주세요. 예: 원본시트와 변경시트"
        if not slot.params.get("left_range") or not slot.params.get("right_range"):
            return "비교할 두 범위를 알려주세요. 예: A2:D100 과 F2:I100"
        return ""
    if intent == "forecast":
        if not slot.params.get("source_range"):
            return "예측할 원본 범위를 알려주세요. 예: B2:B25"
        return ""
    if intent == "print":
        return "인쇄 기준을 알려주세요. 예: A4 가로/세로, 한 페이지 맞춤 여부, PDF 저장 여부."
    if intent == "safety":
        msg = str(slot.params.get("raw_message") or "").lower()
        if any(token in msg for token in ["읽기 전용", "보호된 보기", "편집이 안", "강제로 수정"]):
            return "읽기 전용/보호된 보기 상태로 보입니다. 편집 가능한 사본 생성 후 같은 작업을 진행할까요?"
        return "위험 작업입니다. 원본 백업 후 실행할지, 원본 덮어쓰기를 허용할지 먼저 선택해 주세요."
    if intent == "debug":
        return (
            "오류가 나는 열/범위를 알려주세요. 예: D2:D200. "
            "기대 계산식(예: 수량*단가)도 함께 주시면 원인 점검 후 수정안을 제시할게요."
        )
    if intent == "performance":
        return (
            "느린 구간을 알려주세요. 예: 피벗 새로고침, 대량 수식, 조건부서식. "
            "원하면 원본 보존 상태로 진단용 요약 시트를 먼저 만들겠습니다."
        )
    if intent == "explain":
        topic = str(slot.params.get("topic") or "")
        if topic == "pivot":
            return (
                "피벗은 많은 데이터를 기준별로 요약 집계하는 기능입니다. "
                "원하면 지금 시트 기준으로 월별 합계 피벗을 바로 만들어드릴까요?"
            )
        if topic == "power_query":
            return (
                "Power Query는 여러 파일 가져오기/정리/병합을 반복 가능하게 자동화하는 기능입니다. "
                "원하면 현재 통합문서에 맞춘 새로고침 흐름을 바로 설정해드릴까요?"
            )
        return "궁금한 기능을 쉬운 예시로 설명해드릴게요. 피벗/필터/수식/차트 중 무엇이 궁금한가요?"
    if intent == "general":
        return _build_generic_excel_follow_up(str(slot.params.get("raw_message") or ""))
    return ""


def _operation_action_plan(slot: PendingExcelOperationSlots) -> list[dict[str, Any]]:
    intent = slot.intent
    p = dict(slot.params)
    if intent == "formula":
        if p.get("range_ref"):
            return [
                {
                    "action": "excel_live.verify_formula_result",
                    "params": {"range_ref": p.get("range_ref")},
                    "reason": "수식 결과 검증",
                }
            ]
        mode = str(p.get("formula_mode") or "").strip()
        target_range = p.get("target_range", "__ACTIVE_SELECTION__")
        start_row, end_row = _parse_row_bounds_from_range(str(target_range))
        if mode == "multiply":
            qty = str(p.get("qty_column", "B")).upper()
            price = str(p.get("price_column", "C")).upper()
            result_col = str(p.get("result_column") or _next_column(price)).upper()
            formula = f"={qty}{start_row}*{price}{start_row}"
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = True
        elif mode == "tax":
            base = str(p.get("base_column", "C")).upper()
            result_col = str(p.get("result_column") or _next_column(base)).upper()
            tax_rate = float(p.get("tax_rate", 0.1))
            formula = f"={base}{start_row}*(1+{tax_rate})"
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = True
        elif mode == "countif":
            count_col = str(p.get("count_column", "B")).upper()
            cond = str(p.get("count_condition", "완료")).replace('"', "").strip() or "완료"
            result_col = str(p.get("result_column") or _next_column(count_col)).upper()
            formula = f'=COUNTIF(${count_col}${start_row}:${count_col}${end_row},"{cond}")'
            result_range = f"{result_col}{start_row}"
            expect_numeric = True
        elif mode == "vlookup":
            lookup_col = str(p.get("lookup_column", "A")).upper()
            table_start = str(p.get("table_start_column", "F")).upper()
            table_end = str(p.get("table_end_column", "H")).upper()
            return_index = int(p.get("return_index", 2))
            result_col = str(p.get("result_column") or _next_column(table_end)).upper()
            formula = (
                f"=VLOOKUP({lookup_col}{start_row},"
                f"${table_start}${start_row}:${table_end}${end_row},{return_index},FALSE)"
            )
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = False
        elif mode == "if_compare":
            compare_col = str(p.get("compare_column", "C")).upper()
            compare_op = str(p.get("compare_op", "<")).strip()
            threshold = float(p.get("threshold", 70))
            true_value = str(p.get("true_value", "미달")).replace('"', "").strip() or "미달"
            false_value = str(p.get("false_value", "통과")).replace('"', "").strip() or "통과"
            result_col = str(p.get("result_column") or _next_column(compare_col)).upper()
            formula = (
                f'=IF({compare_col}{start_row}{compare_op}{threshold},"{true_value}","{false_value}")'
            )
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = False
        else:
            target = str(p.get("target_column", "C")).upper()
            actual = str(p.get("actual_column", "D")).upper()
            result_col = str(p.get("result_column") or _next_column(actual)).upper()
            formula = f"={actual}{start_row}-{target}{start_row}"
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = True
        return [
            {
                "action": "excel_live.set_formula",
                "params": {
                    "range_ref": result_range,
                    "formula_a1": formula,
                    "formula_mode": mode or "gap",
                    "expect_numeric": expect_numeric,
                },
                "reason": "멀티턴 계산 수식 적용",
            },
            {
                "action": "excel_live.verify_formula_result",
                "params": {"range_ref": result_range},
                "reason": "수식 결과 검증",
            },
        ]
    if intent == "sort":
        return [
            {
                "action": "excel_live.sort_range",
                "params": {
                    "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                    "key_column": p.get("key_column", 1),
                    "order": p.get("order", "desc"),
                    "has_header": bool(p.get("has_header", True)),
                },
                "reason": "멀티턴 정렬 실행",
            }
        ]
    if intent == "filter":
        return [
            {
                "action": "excel_live.filter_rows",
                "params": {
                    "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                    "column": p.get("column", 1),
                    "operator": p.get("operator", "=="),
                    "value": p.get("value"),
                    "has_header": bool(p.get("has_header", True)),
                },
                "reason": "멀티턴 필터 실행",
            }
        ]
    if intent == "dedupe":
        return [
            {
                "action": "excel_live.dedupe_rows",
                "params": {
                    "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                    "key_columns": p.get("key_columns", []),
                    "has_header": bool(p.get("has_header", True)),
                },
                "reason": "멀티턴 중복 제거 실행",
            }
        ]
    if intent == "pivot":
        return [
            {
                "action": "excel_live.pivot_table",
                "params": {
                    "source_range": p.get("source_range", p.get("target_range", "__ACTIVE_SELECTION__")),
                    "row_field": p.get("row_field", 1),
                    "value_field": p.get("value_field", 2),
                    "column_field": p.get("column_field"),
                    "agg": p.get("agg", "sum"),
                    "output_sheet": p.get("output_sheet"),
                    "output_start": p.get("output_start", "A1"),
                    "has_header": bool(p.get("has_header", True)),
                },
                "reason": "멀티턴 피벗 실행",
            }
        ]
    if intent == "chart":
        return [
            {
                "action": "excel_live.create_chart",
                "params": {
                    "source_range": p.get("source_range", p.get("target_range", "__ACTIVE_SELECTION__")),
                    "chart_type": p.get("chart_type", "line"),
                    "title": p.get("title", "데이터 차트"),
                    "output_sheet": p.get("output_sheet"),
                },
                "reason": "멀티턴 차트 생성",
            }
        ]
    if intent == "validate":
        return [
            {
                "action": "excel_live.validate_data",
                "params": {
                    "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                    "checks": p.get("checks", ["empty", "negative", "outlier"]),
                    "has_header": bool(p.get("has_header", True)),
                    "date_min": p.get("date_min"),
                    "date_max": p.get("date_max"),
                },
                "reason": "멀티턴 데이터 검증",
            }
        ]
    if intent == "protect":
        mode = str(p.get("mode") or "sheet_protect")
        if mode == "validation_list":
            return [
                {
                    "action": "excel_live.set_data_validation",
                    "params": {
                        "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                        "validation_type": "list",
                        "source": p.get("source", "완료,진행중,지연,보류"),
                    },
                    "reason": "멀티턴 입력 제한(드롭다운) 적용",
                }
            ]
        if mode == "validation_numeric":
            return [
                {
                    "action": "excel_live.set_data_validation",
                    "params": {
                        "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                        "validation_type": "whole",
                        "minimum": p.get("minimum", 0),
                        "maximum": p.get("maximum", 1000000000),
                    },
                    "reason": "멀티턴 숫자 입력 제한 적용",
                }
            ]
        if mode == "validation_date":
            return [
                {
                    "action": "excel_live.set_data_validation",
                    "params": {
                        "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                        "validation_type": "date",
                        "minimum": p.get("minimum", 20000101),
                        "maximum": p.get("maximum", 20991231),
                    },
                    "reason": "멀티턴 날짜 입력 제한 적용",
                }
            ]
        return [
            {
                "action": "excel_live.protect_sheet",
                "params": {
                    "lock_formula_cells": bool(p.get("lock_formula_cells", True)),
                    "unlock_range": p.get("unlock_range"),
                },
                "reason": "멀티턴 시트 보호 적용",
            }
        ]
    if intent == "consolidate":
        scope = str(p.get("scope") or "sheets")
        if scope == "workbooks":
            return [
                {
                    "action": "excel_live.consolidate_workbooks_from_folder",
                    "params": {
                        "folder_path": p.get("folder_path"),
                        "pattern": p.get("pattern", "*.xlsx"),
                        "source_sheet": p.get("source_sheet"),
                        "output_sheet": p.get("output_sheet", "파일통합결과"),
                    },
                    "reason": "멀티턴 파일 통합 실행",
                }
            ]
        return [
            {
                "action": "excel_live.consolidate_sheets",
                "params": {
                    "source_sheets": p.get("source_sheets", []),
                    "output_sheet": p.get("output_sheet", "통합결과"),
                },
                "reason": "멀티턴 시트 통합 실행",
            }
        ]
    if intent == "automation":
        mode = str(p.get("mode") or "refresh")
        if mode == "vba":
            return [
                {
                    "action": "excel_live.run_vba_macro",
                    "params": {"macro_name": p.get("macro_name"), "args": p.get("args", [])},
                    "reason": "멀티턴 VBA 매크로 실행",
                }
            ]
        return [
            {
                "action": "excel_live.refresh_power_query",
                "params": {},
                "reason": "멀티턴 Power Query 새로고침",
            }
        ]
    if intent == "compare":
        return [
            {
                "action": "excel_live.compare_ranges",
                "params": {
                    "left_sheet": p.get("left_sheet"),
                    "left_range": p.get("left_range"),
                    "right_sheet": p.get("right_sheet"),
                    "right_range": p.get("right_range"),
                    "output_sheet": p.get("output_sheet", "비교결과"),
                },
                "reason": "멀티턴 범위 비교 실행",
            }
        ]
    if intent == "forecast":
        return [
            {
                "action": "excel_live.forecast_linear",
                "params": {
                    "source_range": p.get("source_range", p.get("target_range", "__ACTIVE_SELECTION__")),
                    "horizon": p.get("horizon", 3),
                    "output_sheet": p.get("output_sheet"),
                    "output_start": p.get("output_start", "A1"),
                },
                "reason": "멀티턴 추세 예측 실행",
            }
        ]
    return []
def _merge_create_table_slots(
    current: PendingCreateTableSlots | None,
    *,
    hints: dict[str, Any],
    parsed: dict[str, Any] | None,
    req: ExcelLiveCommandRequest,
    session_key: str,
) -> PendingCreateTableSlots:
    now = time.time()
    slot = current or PendingCreateTableSlots(
        session_id=session_key,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        headers=[],
        created_at_ts=now,
        updated_at_ts=now,
    )
    slot.workbook_id = req.workbook_id or slot.workbook_id
    slot.sheet_name = req.sheet_name or slot.sheet_name
    template_key = hints.get("template_key")
    if isinstance(template_key, str) and template_key.strip():
        slot.template_key = template_key.strip()
    template_q = hints.get("template_follow_up_question")
    if isinstance(template_q, str) and template_q.strip():
        slot.template_follow_up_question = template_q.strip()

    user_shape_explicit = hints.get("rows") is not None or hints.get("cols") is not None
    user_header_explicit = bool(hints.get("headers"))
    first_turn = current is None

    def _merge_payload(payload: dict[str, Any], *, from_user: bool = False) -> None:
        rows = payload.get("rows")
        cols = payload.get("cols")
        headers = payload.get("headers")
        start_cell = payload.get("start_cell")
        allow_inferred_shape = from_user or not first_turn or user_shape_explicit or user_header_explicit
        if rows is not None and allow_inferred_shape:
            slot.rows = max(1, min(100, int(rows)))
        if cols is not None and allow_inferred_shape:
            slot.cols = max(1, min(50, int(cols)))
        if isinstance(headers, list):
            normalized = [str(h).strip() for h in headers if str(h).strip()]
            if normalized:
                slot.headers = normalized
        if isinstance(start_cell, str) and start_cell.strip():
            slot.start_cell = _normalize_range_text(start_cell).split(":")[0] or slot.start_cell

    _merge_payload(hints, from_user=True)
    if hints.get("blank_table"):
        slot.headers = []
    if parsed:
        if isinstance(parsed.get("slot_fill"), dict):
            _merge_payload(parsed.get("slot_fill", {}))
        if isinstance(parsed.get("partial_params"), dict):
            _merge_payload(parsed.get("partial_params", {}))
    slot.updated_at_ts = now
    return slot


def _build_table_follow_up(slot: PendingCreateTableSlots) -> str:
    if slot.rows is None and slot.cols is None and slot.template_follow_up_question:
        return slot.template_follow_up_question
    if slot.rows is None or slot.cols is None:
        return (
            "표 크기와 헤더를 알려주세요. 예: 5*5, 금액, 장소, 날짜, 요건, 비고 "
            "(기준 셀 미지정 시 A1에서 생성)"
        )
    if not slot.headers:
        return f"{slot.rows}*{slot.cols} 표로 생성할게요. 헤더를 넣을까요? 예: 금액, 장소, 날짜, 요건, 비고"
    return "표 생성 정보를 확인했습니다. 생성을 진행합니다."


def _build_create_table_steps(slot: PendingCreateTableSlots) -> list[dict[str, Any]]:
    rows = max(1, min(100, int(slot.rows or 5)))
    cols = max(1, min(50, int(slot.cols or 5)))
    start_cell = _normalize_range_text(slot.start_cell) or "A1"
    steps: list[dict[str, Any]] = [
        {
            "action": "excel_live.create_table",
            "params": {
                "start_cell": start_cell,
                "rows": rows,
                "cols": cols,
                "with_border": True,
            },
            "reason": "대화 슬롯 기반 표 생성",
        }
    ]

    headers = [str(h).strip() for h in (slot.headers or []) if str(h).strip()]
    if headers:
        row_values = headers[:cols]
        if len(row_values) < cols:
            row_values.extend([""] * (cols - len(row_values)))
        steps.append(
            {
                "action": "excel_live.write_range",
                "params": {"start_cell": start_cell, "values_2d": [row_values]},
                "reason": "헤더 행 입력",
            }
        )
    return steps


def _apply_template_defaults_if_confirmed(
    slot: PendingCreateTableSlots,
    *,
    hints: dict[str, Any],
) -> None:
    preset = get_table_preset(slot.template_key)
    if preset is None:
        return
    if not hints.get("affirmative"):
        return
    if slot.rows is None:
        slot.rows = preset.default_rows
    if slot.cols is None:
        slot.cols = preset.default_cols
    if not slot.headers and not hints.get("blank_table"):
        slot.headers = list(preset.headers)

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

    if action == "excel_live.create_table":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        start_cell = str(params.get("start_cell", "")).strip().upper()
        if not start_cell or start_cell in {"__ACTIVE_CELL__", "__ACTIVE_SELECTION__"}:
            selected = service.get_active_selection_ref(resolved_wb, resolved_sheet)
            start_cell = _top_left_cell(selected)
        rows = int(params.get("rows", 5))
        cols = int(params.get("cols", 5))
        with_border = bool(params.get("with_border", True))
        return service.create_table(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            start_cell=start_cell,
            rows=rows,
            cols=cols,
            with_border=with_border,
        )

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

    if action == "excel_live.fill_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        fill_color = str(params.get("fill_color", "#FFFF00"))
        return service.fill_range(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            fill_color=fill_color,
        )

    if action == "excel_live.clear_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.clear_range(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
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

    if action == "excel_live.verify_formula_result":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == "__ACTIVE_SELECTION__":
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.verify_formula_result(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            range_ref=range_ref,
        )

    if action == "excel_live.sort_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.sort_range(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            key_column=params.get("key_column", 1),
            order=str(params.get("order", "asc")),
            has_header=bool(params.get("has_header", True)),
        )

    if action == "excel_live.filter_rows":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.filter_rows(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            column=params.get("column", 1),
            operator=str(params.get("operator", "==")),
            value=params.get("value"),
            has_header=bool(params.get("has_header", True)),
        )

    if action == "excel_live.dedupe_rows":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.dedupe_rows(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            key_columns=params.get("key_columns", []),
            has_header=bool(params.get("has_header", True)),
        )

    if action == "excel_live.pivot_table":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        source_range = str(params.get("source_range", "")).strip().upper()
        if not source_range or source_range == "__ACTIVE_SELECTION__":
            source_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.pivot_table(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            source_range=source_range,
            row_field=params.get("row_field", 1),
            value_field=params.get("value_field", 2),
            agg=str(params.get("agg", "sum")),
            column_field=params.get("column_field"),
            output_sheet=params.get("output_sheet"),
            output_start=str(params.get("output_start", "A1")),
            has_header=bool(params.get("has_header", True)),
        )

    if action == "excel_live.create_chart":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        source_range = str(params.get("source_range", "")).strip().upper()
        if not source_range or source_range == "__ACTIVE_SELECTION__":
            source_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.create_chart(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            source_range=source_range,
            chart_type=str(params.get("chart_type", "line")),
            title=str(params.get("title", "데이터 차트")),
            output_sheet=params.get("output_sheet"),
        )

    if action == "excel_live.validate_data":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.validate_data(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            checks=params.get("checks", ["empty", "negative", "outlier"]),
            has_header=bool(params.get("has_header", True)),
            date_min=params.get("date_min"),
            date_max=params.get("date_max"),
        )

    if action == "excel_live.protect_sheet":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.protect_sheet(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            password=str(params.get("password", "")).strip() or None,
            lock_formula_cells=bool(params.get("lock_formula_cells", True)),
            unlock_range=str(params.get("unlock_range", "")).strip() or None,
        )

    if action == "excel_live.set_data_validation":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.set_data_validation(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            validation_type=str(params.get("validation_type", "list")),
            source=str(params.get("source", "")).strip() or None,
            minimum=params.get("minimum"),
            maximum=params.get("maximum"),
            allow_blank=bool(params.get("allow_blank", True)),
            show_error=bool(params.get("show_error", True)),
            error_message=str(params.get("error_message", "")).strip() or None,
        )

    if action == "excel_live.consolidate_sheets":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.consolidate_sheets(
            workbook_id=resolved_wb,
            source_sheets=params.get("source_sheets", []),
            output_sheet=str(params.get("output_sheet", "통합결과")),
            include_header_once=bool(params.get("include_header_once", True)),
            add_source_sheet_col=bool(params.get("add_source_sheet_col", True)),
        )

    if action == "excel_live.consolidate_workbooks_from_folder":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.consolidate_workbooks_from_folder(
            workbook_id=resolved_wb,
            folder_path=str(params.get("folder_path", "")),
            pattern=str(params.get("pattern", "*.xlsx")),
            source_sheet=str(params.get("source_sheet", "")).strip() or None,
            output_sheet=str(params.get("output_sheet", "파일통합결과")),
            include_header_once=bool(params.get("include_header_once", True)),
            add_source_file_col=bool(params.get("add_source_file_col", True)),
        )

    if action == "excel_live.refresh_power_query":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.refresh_power_query(resolved_wb)

    if action == "excel_live.run_vba_macro":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.run_vba_macro(
            workbook_id=resolved_wb,
            macro_name=str(params.get("macro_name", "")),
            args=params.get("args", []),
        )

    if action == "excel_live.compare_ranges":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        left_sheet = str(params.get("left_sheet") or sheet_name or "").strip()
        right_sheet = str(params.get("right_sheet") or sheet_name or "").strip()
        if not left_sheet or not right_sheet:
            raise ExcelLiveError("compare_ranges에는 left/right 시트명이 필요합니다.")
        return service.compare_ranges(
            workbook_id=resolved_wb,
            left_sheet=left_sheet,
            left_range=str(params.get("left_range", "")),
            right_sheet=right_sheet,
            right_range=str(params.get("right_range", "")),
            output_sheet=str(params.get("output_sheet", "")).strip() or None,
        )

    if action == "excel_live.forecast_linear":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        source_range = str(params.get("source_range", "")).strip().upper()
        if not source_range or source_range == "__ACTIVE_SELECTION__":
            source_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.forecast_linear(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            source_range=source_range,
            horizon=int(params.get("horizon", 3)),
            output_sheet=str(params.get("output_sheet", "")).strip() or None,
            output_start=str(params.get("output_start", "A1")),
        )

    if action == "excel_live.save_workbook":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        return service.save_workbook(resolved_wb)

    raise ExcelLiveError(f"지원하지 않는 action: {action}")


def _verify_step_result(
    *,
    action: str,
    params: dict[str, Any],
    result: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> bool:
    """
    단계 실행 후 최소 검증.
    - service의 range snapshot을 활용해 상태를 빠르게 점검한다.
    """
    service = get_excel_live_service()
    if action == "excel_live.write_range":
        written_cells = int(result.get("written_cells", 0) or 0)
        if written_cells >= 1:
            return True
        address = str(result.get("address", "")).strip()
        if not address:
            return False
        snap = service.get_range_snapshot(workbook_id, sheet_name, address)
        return int(snap.get("row_count", 0)) >= 1 and int(snap.get("col_count", 0)) >= 1

    if action == "excel_live.create_table":
        result_rows = int(result.get("rows", 0) or 0)
        result_cols = int(result.get("cols", 0) or 0)
        expected_rows = int(params.get("rows", 0) or 0)
        expected_cols = int(params.get("cols", 0) or 0)
        if result_rows >= max(1, expected_rows) and result_cols >= max(1, expected_cols):
            return True
        address = str(result.get("address", "")).strip()
        if not address:
            return False
        snap = service.get_range_snapshot(workbook_id, sheet_name, address)
        return (
            int(snap.get("row_count", 0)) >= max(1, expected_rows)
            and int(snap.get("col_count", 0)) >= max(1, expected_cols)
        )

    if action in {"excel_live.highlight_by_condition", "excel_live.fill_range", "excel_live.apply_border"}:
        return int(result.get("changed_cells", 0) or 0) >= 1

    if action == "excel_live.clear_range":
        return int(result.get("cleared_cells", 0) or 0) >= 1

    if action == "excel_live.set_formula":
        return int(result.get("formula_applied_cells", 0) or 0) >= 1

    if action == "excel_live.verify_formula_result":
        return "non_empty_cells" in result

    if action == "excel_live.sort_range":
        return int(result.get("sorted_rows", 0) or 0) >= 0

    if action == "excel_live.filter_rows":
        return int(result.get("filtered_rows", 0) or 0) >= 0

    if action == "excel_live.dedupe_rows":
        return int(result.get("removed_rows", 0) or 0) >= 0

    if action == "excel_live.pivot_table":
        return bool(result.get("created")) and int(result.get("rows", 0) or 0) >= 2

    if action == "excel_live.create_chart":
        return bool(result.get("created")) and bool(str(result.get("chart_name", "")).strip())

    if action == "excel_live.validate_data":
        return "issues" in result

    if action == "excel_live.protect_sheet":
        return bool(result.get("protected"))

    if action == "excel_live.set_data_validation":
        return bool(result.get("applied"))

    if action in {"excel_live.consolidate_sheets", "excel_live.consolidate_workbooks_from_folder"}:
        return bool(result.get("created")) and int(result.get("rows", 0) or 0) >= 1

    if action == "excel_live.refresh_power_query":
        return bool(result.get("refreshed"))

    if action == "excel_live.run_vba_macro":
        return bool(result.get("executed"))

    if action == "excel_live.compare_ranges":
        return "diff_cells" in result

    if action == "excel_live.forecast_linear":
        return bool(result.get("created"))

    if action == "excel_live.read_range":
        return int(result.get("row_count", 0) or 0) >= 1 and int(result.get("col_count", 0) or 0) >= 1

    if action in {"excel_live.save_workbook", "excel_live.list_workbooks", "excel_live.select_workbook"}:
        return True

    return True


def _build_approval(action: str, params: dict[str, Any]) -> ApprovalRequest:
    approval_id = str(uuid.uuid4())
    summary = {
        "excel_live.write_range": "엑셀 셀 값을 수정합니다.",
        "excel_live.create_table": "엑셀에 표를 생성합니다.",
        "excel_live.highlight_by_condition": "조건에 맞는 셀 서식을 변경합니다.",
        "excel_live.fill_range": "선택 범위 전체 배경색을 변경합니다.",
        "excel_live.apply_border": "선택 범위에 경계선을 적용합니다.",
        "excel_live.set_formula": "지정 범위에 수식을 적용합니다.",
        "excel_live.sort_range": "지정 범위를 정렬합니다.",
        "excel_live.filter_rows": "조건에 맞는 행만 필터링합니다.",
        "excel_live.dedupe_rows": "중복 행을 제거합니다.",
        "excel_live.pivot_table": "집계표(피벗 형태)를 생성합니다.",
        "excel_live.create_chart": "차트를 생성합니다.",
        "excel_live.protect_sheet": "시트 보호/잠금 규칙을 적용합니다.",
        "excel_live.set_data_validation": "입력 제한(드롭다운/숫자/날짜)을 설정합니다.",
        "excel_live.consolidate_sheets": "여러 시트를 하나로 통합합니다.",
        "excel_live.consolidate_workbooks_from_folder": "폴더의 여러 파일을 통합합니다.",
        "excel_live.refresh_power_query": "Power Query/연결 데이터를 새로고침합니다.",
        "excel_live.run_vba_macro": "VBA 매크로를 실행합니다.",
        "excel_live.compare_ranges": "두 범위를 비교해 차이를 정리합니다.",
        "excel_live.forecast_linear": "추세 기반 예측값을 생성합니다.",
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


@router.get("/backups")
def get_backups(workbook_id: str | None = None, limit: int = 20):
    service = get_excel_live_service()
    if not service.is_available():
        return {
            "available": False,
            "workbook_id": workbook_id,
            "source_path": "",
            "backup_dir": "",
            "backups": [],
        }
    try:
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        listed = service.list_workbook_backups(resolved_wb, limit=max(1, min(200, int(limit))))
        listed["available"] = True
        return listed
    except Exception as exc:
        raise _map_error(exc)


@router.post("/restore-last", response_model=ExcelLiveActionResponse)
def post_restore_last(req: ExcelLiveRestoreLastRequest):
    service = get_excel_live_service()
    try:
        def _run_restore():
            resolved_wb = _resolve_workbook_id(service, req.workbook_id)
            restored = service.restore_workbook_from_backup(
                resolved_wb,
                backup_path=req.backup_path,
            )
            return resolved_wb, restored

        (resolved_wb, restored), queue_wait_ms = _run_in_excel_queue("restore-last", _run_restore)
        if isinstance(restored, dict):
            restored["queue_wait_ms"] = queue_wait_ms
        _audit.log(
            action="excel.live.restore_last_backup",
            target=resolved_wb,
            detail=f"backup={req.backup_path or 'latest'}",
        )
        return ExcelLiveActionResponse(
            ok=True,
            action="excel_live.restore_last_backup",
            result=restored,
            reason="최근 백업 기준으로 복구했습니다.",
        )
    except Exception as exc:
        raise _map_error(exc)


@router.post("/action", response_model=ExcelLiveActionResponse)
def post_action(req: ExcelLiveActionRequest):
    try:
        validated_single = validate_plan(
            normalize_plan_steps([{"action": req.action, "params": req.params, "reason": ""}]),
            context=ValidationContext(
                message=req.action,
                workbook_id=req.workbook_id,
                sheet_name=req.sheet_name,
                context_range=None,
                recent_range=_recent_range_by_workbook.get(_context_key(req.workbook_id)),
            ),
        )
    except Exception as exc:
        raise _map_error(exc)
    if not validated_single:
        raise HTTPException(status_code=400, detail="실행 가능한 action을 생성하지 못했습니다.")
    req.action = validated_single[0].action
    req.params = validated_single[0].params

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

    recovery_backup: dict[str, Any] | None = None
    try:
        def _run_action_once():
            nonlocal recovery_backup
            recovery_backup = (
                _create_recovery_backup_if_possible(workbook_id=req.workbook_id, label="action")
                if _action_needs_recovery_backup(req.action)
                else None
            )
            return _execute_action(
                action=req.action,
                params=req.params,
                workbook_id=req.workbook_id,
                sheet_name=req.sheet_name,
            )

        result, queue_wait_ms = _run_in_excel_queue("action", _run_action_once)
        if isinstance(result, dict):
            result["queue_wait_ms"] = queue_wait_ms
            if recovery_backup:
                result["recovery_backup"] = recovery_backup
        _audit.log(
            action="excel.live.action",
            target=req.action,
            detail=f"ok=True workbook={req.workbook_id or ''}",
        )
        return ExcelLiveActionResponse(ok=True, action=req.action, result=result)
    except Exception as exc:
        mapped = _map_error(exc)
        if recovery_backup and recovery_backup.get("backup_path"):
            mapped.detail = f"{mapped.detail} (복구 백업: {recovery_backup.get('backup_path')})"
        raise mapped


@router.post("/command", response_model=ExcelLiveActionResponse)
async def post_command(
    req: ExcelLiveCommandRequest,
    llm: LLMService = Depends(get_llm_service),
):
    _cleanup_expired_table_slots()
    _cleanup_expired_operation_slots()
    session_key = _slot_session_key(req)
    pending_slot = _pending_create_table_slots.get(session_key)
    pending_operation = _pending_operation_slots.get(session_key)
    hints = extract_create_table_slot_hints(req.message)
    operation_hints = _extract_operation_hints(req.message)
    quick_action_plan = _build_quick_action_plan(req.message, req.context_range)
    if pending_slot is not None or pending_operation is not None:
        quick_action_plan = None
    rule_based_step = parse_command_rule_based(
        req.message,
        context_range=req.context_range,
    )
    fallback_rule_step: dict[str, Any] | None = (
        rule_based_step if isinstance(rule_based_step, dict) else None
    )
    if pending_operation is not None:
        fallback_rule_step = None
    operation_intent = str(operation_hints.get("intent") or "").strip()
    if operation_intent in {"general", "safety"}:
        fallback_rule_step = None
    elif operation_intent == "formula":
        rule_params = dict(fallback_rule_step.get("params", {})) if fallback_rule_step else {}
        has_explicit_formula = (
            bool(fallback_rule_step)
            and str(fallback_rule_step.get("action", "")).strip() == "excel_live.set_formula"
            and str(rule_params.get("formula_a1", "")).strip().startswith("=")
            and bool(re.search(r"\b[A-Z]+\d+(?::[A-Z]+\d+)?\b", str(req.message or ""), re.IGNORECASE))
        )
        if not has_explicit_formula:
            fallback_rule_step = None

    def _normalize_plan_or_empty(raw_steps: Any) -> list[PlanStep]:
        prepared_steps = raw_steps
        if isinstance(raw_steps, list):
            prepared: list[dict[str, Any]] = []
            for step in raw_steps:
                if isinstance(step, PlanStep):
                    action = step.action
                    params = dict(step.params)
                    reason = step.reason
                elif isinstance(step, dict):
                    action = str(step.get("action", "")).strip()
                    params = dict(step.get("params", {}))
                    reason = str(step.get("reason", ""))
                else:
                    continue
                if action == "excel_live.set_formula":
                    formula = str(params.get("formula_a1", "")).strip()
                    if formula and not formula.startswith("="):
                        params["formula_a1"] = f"={formula}"
                prepared.append({"action": action, "params": params, "reason": reason})
            prepared_steps = prepared
        try:
            return normalize_plan_steps(prepared_steps)
        except Exception:
            return []
    fast_operation_intents = {
        "sort",
        "filter",
        "dedupe",
        "pivot",
        "validate",
        "formula",
        "protect",
        "consolidate",
        "automation",
        "compare",
        "forecast",
        "print",
        "safety",
        "debug",
        "performance",
        "explain",
        "general",
    }

    parsed: dict[str, Any] | None = None
    # 멀티턴 슬롯이 이미 잡혔거나, 규칙 기반 힌트로 충분히 분기 가능한 경우에는
    # LLM 파서를 생략해 첫 응답 지연을 줄인다.
    # 기본 전략: LLM 우선 해석, 룰은 실패/타임아웃 시 폴백.
    # 다만 이미 진행 중인 슬롯 멀티턴(create_table/operation)은 문맥 일관성을 위해
    # 기존 슬롯 경로를 우선한다.
    should_parse_with_llm = not (
        pending_slot is not None
        or pending_operation is not None
        or hints.get("table_intent")
    )
    parse_error: Exception | None = None
    parse_timeout_count = 0
    if should_parse_with_llm:
        for parse_attempt in range(_COMMAND_PARSE_MAX_ATTEMPTS):
            try:
                parsed = await asyncio.wait_for(
                    parse_excel_live_command(
                        req.message,
                        llm_service=llm,
                        context={
                            "context_range": req.context_range,
                            "workbook_id": req.workbook_id,
                            "sheet_name": req.sheet_name,
                        },
                    ),
                    timeout=_COMMAND_PARSE_TIMEOUT_SECONDS,
                )
                parse_error = None
                break
            except asyncio.TimeoutError as exc:
                parse_error = exc
                parse_timeout_count += 1
                if parse_attempt + 1 < _COMMAND_PARSE_MAX_ATTEMPTS:
                    backoff = _COMMAND_PARSE_RETRY_BACKOFF_SECONDS * float(parse_attempt + 1)
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                    continue
                break
            except ValueError as exc:
                parse_error = exc
                break

        if (
            parsed is None
            and not quick_action_plan
            and fallback_rule_step is None
            and not operation_hints.get("intent")
        ):
            if isinstance(parse_error, asyncio.TimeoutError):
                follow = _build_generic_excel_follow_up(req.message)
                return ExcelLiveActionResponse(
                    ok=True,
                    action="excel_live.clarify",
                    reason=follow,
                    result={
                        "ask_follow_up": True,
                        "follow_up_question": follow,
                        "operation_intent": "clarify",
                        "parse_timeout": True,
                        "parse_attempts": max(1, parse_timeout_count),
                    },
                )
            if isinstance(parse_error, ValueError):
                if _looks_like_excel_request(req.message):
                    follow = _build_generic_excel_follow_up(req.message)
                    return ExcelLiveActionResponse(
                        ok=True,
                        action="excel_live.clarify",
                        reason=follow,
                        result={
                            "ask_follow_up": True,
                            "follow_up_question": follow,
                            "operation_intent": "clarify",
                        },
                    )
                raise HTTPException(status_code=400, detail=str(parse_error))

    if parsed is None and quick_action_plan:
        action_plan = _normalize_plan_or_empty(quick_action_plan)
        parsed = {
            "action_plan": [s.__dict__ for s in action_plan],
            "action": action_plan[0].action if action_plan else "excel_live.read_range",
            "params": action_plan[0].params if action_plan else {},
            "reason": "빠른 규칙 기반 실행 계획",
            "intent": "edit" if action_plan and action_plan[0].action != "excel_live.read_range" else "read",
        }

    if parsed is None and not quick_action_plan:
        if isinstance(fallback_rule_step, dict) and fallback_rule_step.get("action"):
            action_plan = _normalize_plan_or_empty([fallback_rule_step])
            parsed = {
                "action_plan": [s.__dict__ for s in action_plan],
                "action": action_plan[0].action if action_plan else "",
                "params": action_plan[0].params if action_plan else {},
                "reason": "룰 기반 폴백 실행 계획",
                "intent": "edit" if action_plan and action_plan[0].action != "excel_live.read_range" else "read",
            }

    # create_table 멀티턴 슬롯필링 오케스트레이션
    if pending_slot is not None and parsed and parsed.get("action_plan"):
        first_step = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        if first_step.get("action") != "excel_live.create_table" and not hints.get("table_intent"):
            _pending_create_table_slots.pop(session_key, None)
            pending_slot = None

    table_intent = bool(hints.get("table_intent")) or pending_slot is not None
    if parsed and parsed.get("action_plan"):
        first_step = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        if first_step.get("action") == "excel_live.create_table":
            table_intent = True
    if table_intent:
        slot = _merge_create_table_slots(
            pending_slot,
            hints=hints,
            parsed=parsed,
            req=req,
            session_key=session_key,
        )
        _apply_template_defaults_if_confirmed(slot, hints=hints)
        need_follow_up = slot.rows is None or slot.cols is None
        if need_follow_up:
            _pending_create_table_slots[session_key] = slot
            follow_up_question = (
                str(parsed.get("follow_up_question", "")).strip()
                if isinstance(parsed, dict)
                else ""
            ) or _build_table_follow_up(slot)
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.create_table",
                reason=follow_up_question,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": follow_up_question,
                    "slot_state": {
                        "rows": slot.rows,
                        "cols": slot.cols,
                        "headers": slot.headers or [],
                        "start_cell": slot.start_cell or "A1",
                    },
                },
            )
        _pending_create_table_slots.pop(session_key, None)
        action_plan = normalize_plan_steps(_build_create_table_steps(slot))
        parsed = {
            "action_plan": [s.__dict__ for s in action_plan],
            "action": action_plan[0].action if action_plan else "excel_live.create_table",
            "params": action_plan[0].params if action_plan else {},
            "reason": "대화 슬롯 기반 표 생성 계획",
            "intent": "edit",
        }
        _pending_operation_slots.pop(session_key, None)
    else:
        if pending_operation is not None:
            first_action = ""
            has_parsed_plan = False
            if parsed and isinstance(parsed.get("action_plan"), list) and parsed["action_plan"]:
                has_parsed_plan = True
                first = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
                first_action = str(first.get("action", ""))
            # 후속 답변은 키워드가 없어도 기존 pending 슬롯을 유지해야 한다.
            # 명시적으로 다른(비 operation) 액션이 나온 경우에만 pending을 해제한다.
            if has_parsed_plan and not _action_to_operation_intent(first_action):
                _pending_operation_slots.pop(session_key, None)
                pending_operation = None

        op_slot = _merge_operation_slots(
            pending_operation,
            session_key=session_key,
            req=req,
            hints=operation_hints,
            parsed=parsed,
        )
        if op_slot is not None:
            follow_up = _operation_follow_up(op_slot)
            # 새 멀티턴 시작이거나 기존 멀티턴 이어서 파라미터가 부족하면 질문한다.
            if follow_up:
                _pending_operation_slots[session_key] = op_slot
                return ExcelLiveActionResponse(
                    ok=True,
                    action=f"excel_live.{op_slot.intent}",
                    reason=follow_up,
                    result={
                        "ask_follow_up": True,
                        "follow_up_question": follow_up,
                        "slot_state": dict(op_slot.params),
                        "operation_intent": op_slot.intent,
                    },
                )

            op_plan_raw = _operation_action_plan(op_slot)
            if op_plan_raw:
                action_plan = _normalize_plan_or_empty(op_plan_raw)
                if (
                    not action_plan
                    and isinstance(fallback_rule_step, dict)
                    and fallback_rule_step.get("action")
                ):
                    action_plan = _normalize_plan_or_empty([fallback_rule_step])
                parsed = {
                    "action_plan": [s.__dict__ for s in action_plan],
                    "action": action_plan[0].action if action_plan else "excel_live.read_range",
                    "params": action_plan[0].params if action_plan else {},
                    "reason": f"대화 슬롯 기반 {op_slot.intent} 실행 계획",
                    "intent": "edit" if op_slot.intent != "validate" else "read",
                }
                _pending_operation_slots.pop(session_key, None)
            else:
                action_plan = _normalize_plan_or_empty(parsed.get("action_plan")) if parsed else []
        else:
            action_plan = _normalize_plan_or_empty(parsed.get("action_plan")) if parsed else []

        if not action_plan and parsed and parsed.get("action"):
            action_plan = _normalize_plan_or_empty(
                [
                    {
                        "action": parsed["action"],
                        "params": dict(parsed.get("params", {})),
                        "reason": parsed.get("reason", ""),
                    }
                ]
            )
        if (
            not action_plan
            and isinstance(fallback_rule_step, dict)
            and fallback_rule_step.get("action")
        ):
            action_plan = _normalize_plan_or_empty([fallback_rule_step])

    if not action_plan:
        if _looks_like_excel_request(req.message):
            follow = _build_generic_excel_follow_up(req.message)
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=follow,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": follow,
                    "operation_intent": operation_hints.get("intent") or "clarify",
                },
            )
        raise HTTPException(status_code=400, detail="실행 가능한 action을 생성하지 못했습니다.")

    base_context = {
        "context_range": req.context_range,
        "workbook_id": req.workbook_id,
        "sheet_name": req.sheet_name,
    }

    def _validate_steps(steps):
        return validate_plan(
            steps,
            context=ValidationContext(
                message=req.message,
                workbook_id=req.workbook_id,
                sheet_name=req.sheet_name,
                context_range=base_context.get("context_range"),
                recent_range=_recent_range_by_workbook.get(_context_key(req.workbook_id)),
            ),
        )

    try:
        current_plan = _validate_steps(action_plan)
    except Exception as exc:
        if _looks_like_excel_request(req.message):
            follow = _build_generic_excel_follow_up(req.message)
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=follow,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": follow,
                    "operation_intent": operation_hints.get("intent") or "clarify",
                },
            )
        raise _map_error(exc)

    execution = None
    replan_count = 0
    max_replans = 1
    recovery_backup_info: dict[str, Any] | None = None
    rollback_events: list[dict[str, Any]] = []
    queue_wait_total_ms = 0
    snapshot_holder: dict[str, ActionRollbackSnapshot | None] = {"current": None}
    while True:
        # CONFIRM이 필요한 단계가 있으면 기존 승인 UX를 유지하기 위해
        # 첫 CONFIRM 단계에서 즉시 반환한다.
        for step in current_plan:
            action = step.action
            params = step.params
            tool_def = get_tool(action)
            if tool_def and tool_def.permission == PermissionLevel.DENIED:
                return ExcelLiveActionResponse(
                    ok=False,
                    action=action,
                    reason="보안 정책에 의해 거부된 작업입니다.",
                )
            if tool_def and tool_def.permission == PermissionLevel.CONFIRM and not req.approve:
                pending = _build_approval(action, params)
                _pending_approvals[pending.approval_id] = PendingExcelApproval(
                    action=action,
                    params=params,
                    workbook_id=req.workbook_id,
                    sheet_name=req.sheet_name,
                    created_at=pending.created_at,
                )
                return ExcelLiveActionResponse(
                    ok=True,
                    action=action,
                    approval_required=True,
                    pending_approval=pending,
                    reason=step.reason or "승인이 필요한 작업입니다.",
                )

        try:
            def _guarded_execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
                snapshot_holder["current"] = _snapshot_target_range_for_action(
                    action=action,
                    params=params,
                    workbook_id=req.workbook_id,
                    sheet_name=req.sheet_name,
                )
                try:
                    return _execute_action(
                        action=action,
                        params=params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                except Exception:
                    restored = _restore_action_snapshot(snapshot_holder.get("current"))
                    current_snapshot = snapshot_holder.get("current")
                    if restored and current_snapshot is not None:
                        rollback_events.append(
                            {
                                "action": action,
                                "range_ref": current_snapshot.range_ref,
                                "reason": "execute_error",
                            }
                        )
                    raise

            def _guarded_verify(action: str, params: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
                checked = _verify_step_result(
                    action=action,
                    params=params,
                    result=result,
                    workbook_id=req.workbook_id,
                    sheet_name=req.sheet_name,
                )
                if isinstance(checked, tuple):
                    is_ok, detail = checked
                else:
                    is_ok, detail = bool(checked), ""

                if not is_ok:
                    restored = _restore_action_snapshot(snapshot_holder.get("current"))
                    current_snapshot = snapshot_holder.get("current")
                    if restored and current_snapshot is not None:
                        rollback_events.append(
                            {
                                "action": action,
                                "range_ref": current_snapshot.range_ref,
                                "reason": "verify_failed",
                            }
                        )
                        detail = f"{detail};auto_rollback_applied" if detail else "auto_rollback_applied"
                return bool(is_ok), str(detail or "")

            def _run_execute_once():
                nonlocal recovery_backup_info
                if recovery_backup_info is None and _plan_needs_recovery_backup(current_plan):
                    recovery_backup_info = _create_recovery_backup_if_possible(
                        workbook_id=req.workbook_id,
                        label="command",
                    )
                return execute_plan(
                    steps=current_plan,
                    execute_action=_guarded_execute,
                    verify_step=_guarded_verify,
                    max_attempts=2,
                    abort_on_failure=True,
                )

            execution, queue_wait_ms = _run_in_excel_queue("command-plan", _run_execute_once)
            queue_wait_total_ms += queue_wait_ms
        except Exception as exc:
            mapped = _map_error(exc)
            if recovery_backup_info and recovery_backup_info.get("backup_path"):
                mapped.detail = f"{mapped.detail} (복구 백업: {recovery_backup_info.get('backup_path')})"
            raise mapped

        if not should_replan_after_execution(
            execution,
            intent=str((parsed or {}).get("intent", "unknown")),
            replan_count=replan_count,
            max_replans=max_replans,
        ):
            break

        replan_count += 1
        replan_context = build_replan_context(base_context=base_context, execution=execution)
        try:
            replanned = await parse_command_plan_with_llm(
                req.message,
                llm,
                context=replan_context,
                forbid_list_action=True,
                require_edit_action=True,
            )
            current_plan = _validate_steps(_normalize_plan_or_empty(replanned.get("action_plan")))
            parsed["reason"] = replanned.get("reason", "") or parsed.get("reason", "")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"재계획 실패: {exc}")

    if execution is None or not execution.steps:
        raise HTTPException(status_code=400, detail="실행 가능한 계획(step)을 생성하지 못했습니다.")

    last = execution.last
    if last is None:
        raise HTTPException(status_code=400, detail="실행 결과를 생성하지 못했습니다.")
    last_result = dict(last.result or {})
    address = _normalize_range_text(last_result.get("address"))
    if address:
        _recent_range_by_workbook[_context_key(req.workbook_id)] = address
    if rollback_events:
        last_result["auto_rollbacks"] = list(rollback_events)
    if recovery_backup_info:
        last_result["recovery_backup"] = recovery_backup_info
    if queue_wait_total_ms > 0:
        last_result["queue_wait_ms"] = queue_wait_total_ms
    if len(execution.steps) > 1:
        last_result["executed_steps"] = len(execution.steps)
        last_result["plan"] = [
            {
                "index": s.index,
                "action": s.action,
                "reason": s.reason,
                "retried": s.retried,
                "verified": s.verified,
                "result": s.result,
            }
            for s in execution.steps
        ]

    if last.error or not last.verified:
        failure_detail = last.error or last.verify_detail or "unknown_failure"
        return ExcelLiveActionResponse(
            ok=False,
            action=last.action,
            reason="작업 실행이 안정성 검증을 통과하지 못했습니다. 복구 정보로 원상 복원이 가능합니다.",
            result={
                "failed_action": last.action,
                "failed_step_index": last.index,
                "failure_detail": failure_detail,
                "executed_steps": len(execution.steps),
                "auto_rollbacks": list(rollback_events),
                "recovery_backup": recovery_backup_info,
                "queue_wait_ms": queue_wait_total_ms,
            },
        )

    # 수식 적용 후 검증 단계에서 숫자 결과가 0개면 자동 재시도 1회를 수행한다.
    # 재시도에도 개선이 없을 때만 follow-up 질문으로 전환한다.
    has_formula_step = any(s.action == "excel_live.set_formula" for s in execution.steps)
    if (
        has_formula_step
        and last.action == "excel_live.verify_formula_result"
        and int(last_result.get("numeric_cells", 0) or 0) == 0
    ):
        formula_step = next((s for s in execution.steps if s.action == "excel_live.set_formula"), None)
        formula_a1 = str((formula_step.params or {}).get("formula_a1", "")) if formula_step else ""
        retry_formula = _build_formula_retry_variant(formula_a1)
        if retry_formula and _is_numeric_formula_candidate(formula_a1):
            try:
                def _run_formula_retry_once():
                    retry_set_local = _execute_action(
                        action="excel_live.set_formula",
                        params={
                            "range_ref": str((formula_step.params or {}).get("range_ref", "__ACTIVE_SELECTION__")),
                            "formula_a1": retry_formula,
                        },
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                    retry_verify_local = _execute_action(
                        action="excel_live.verify_formula_result",
                        params={"range_ref": str((formula_step.params or {}).get("range_ref", "__ACTIVE_SELECTION__"))},
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                    return retry_set_local, retry_verify_local

                (retry_set, retry_verify), retry_queue_wait_ms = _run_in_excel_queue(
                    "formula-retry",
                    _run_formula_retry_once,
                )
                queue_wait_total_ms += retry_queue_wait_ms
                retried_numeric = int(retry_verify.get("numeric_cells", 0) or 0)
                if retried_numeric > 0:
                    retry_verify["auto_retry_applied"] = True
                    retry_verify["retry_formula"] = retry_formula
                    retry_verify["retry_set_result"] = retry_set
                    if rollback_events:
                        retry_verify["auto_rollbacks"] = list(rollback_events)
                    if recovery_backup_info:
                        retry_verify["recovery_backup"] = recovery_backup_info
                    if queue_wait_total_ms > 0:
                        retry_verify["queue_wait_ms"] = queue_wait_total_ms
                    return ExcelLiveActionResponse(
                        ok=True,
                        action="excel_live.verify_formula_result",
                        reason="수식 검증 실패를 자동 보정해 재실행했습니다.",
                        result=retry_verify,
                    )
            except Exception:
                pass

        follow = "수식 결과가 숫자로 계산되지 않았습니다. 기준 열/범위를 다시 지정해 주세요. 예: B열 수량, C열 단가, 결과 D열"
        return ExcelLiveActionResponse(
            ok=True,
            action="excel_live.verify_formula_result",
            reason=follow,
            result={
                "ask_follow_up": True,
                "follow_up_question": follow,
                "slot_state": {"last_range": last_result.get("address", "")},
                "operation_intent": "formula",
                "auto_rollbacks": list(rollback_events),
                "recovery_backup": recovery_backup_info,
                "queue_wait_ms": queue_wait_total_ms,
            },
        )

    return ExcelLiveActionResponse(
        ok=True,
        action=last.action,
        result=last_result,
        reason=parsed.get("reason", "") or last.reason,
    )


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

    recovery_backup: dict[str, Any] | None = None
    try:
        def _run_approval_once():
            nonlocal recovery_backup
            recovery_backup = (
                _create_recovery_backup_if_possible(workbook_id=pending.workbook_id, label="approval")
                if _action_needs_recovery_backup(pending.action)
                else None
            )
            return _execute_action(
                action=pending.action,
                params=pending.params,
                workbook_id=pending.workbook_id,
                sheet_name=pending.sheet_name,
            )

        result, queue_wait_ms = _run_in_excel_queue("approval", _run_approval_once)
        if isinstance(result, dict):
            result["queue_wait_ms"] = queue_wait_ms
            if recovery_backup:
                result["recovery_backup"] = recovery_backup
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
        mapped = _map_error(exc)
        if recovery_backup and recovery_backup.get("backup_path"):
            mapped.detail = f"{mapped.detail} (복구 백업: {recovery_backup.get('backup_path')})"
        raise mapped

