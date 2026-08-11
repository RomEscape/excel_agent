"""Excel Live 라우터 — 자연어 기반 실시간 Excel(COM) 제어 API."""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse
from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.decision_trace import (
    Long,
)
from office_claw_sidecar.services.decision_trace import (
    note as trace_note,
)
from office_claw_sidecar.services.decision_trace import (
    route as trace_route,
)
from office_claw_sidecar.services.decision_trace import (
    plan_summary as trace_plan,
)
from office_claw_sidecar.services.decision_trace import (
    set_outcome_from_response,
    turn_scope,
)
from office_claw_sidecar.services.excel_actions import execute_excel_action
from office_claw_sidecar.services.excel_header_lexicon import (
    find_header_mentions,
    resolve_header,
)
from office_claw_sidecar.services.excel_live_agent import (
    COLUMN_LETTER_PATTERN,
    RANGE_REF_PATTERN,
    clarify_question_from_plan,
    extract_create_table_slot_hints,
    parse_command_plan_with_llm,
    parse_command_rule_based,
    parse_excel_live_command,
)
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
from office_claw_sidecar.services.excel_planner_escalation import (
    EscalationResult,
    plan_with_escalation,
    record_escalation,
)
from office_claw_sidecar.services.excel_live_service import (
    AmbiguousWorkbookError,
    ExcelConnectionError,
    ExcelDependencyError,
    ExcelLiveError,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
    get_excel_live_service,
)
from office_claw_sidecar.services.excel_live_table_presets import get_table_preset
from office_claw_sidecar.services.excel_macro_planner import (
    MacroStepPlan,
    decompose_macro_request,
    looks_like_macro_request,
)
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
    resolve_sheet_from_message,
    sheet_entry,
)
from office_claw_sidecar.services.excel_planner_prompt import render_conversation_history
from office_claw_sidecar.services.excel_result_verifier import verify_effect
from office_claw_sidecar.services.excel_workbook_digest import (
    build_workbook_digest,
    invalidate_digest_cache,
    render_workbook_digest,
)
from office_claw_sidecar.services.korean_number import (
    parse_condition as parse_korean_condition,
)
from office_claw_sidecar.services.llm_service import (
    LLMService,
    get_llm_service,
    get_macro_model_name,
    get_planner_model_name,
)
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool
from office_claw_sidecar.services.user_harness_service import (
    build_personalization_prompt,
    resolve_user_key,
)

router = APIRouter(tags=["excel-live"])
_audit = AuditService()


class ExcelLiveCommandRequest(BaseModel):
    message: str = Field(..., min_length=1, description="자연어 엑셀 명령")
    user_id: str | None = Field(None, description="사용자 식별자(개인화 하네스 키)")
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


class ExcelLiveMacroStepRequest(BaseModel):
    macro_id: str = Field(..., min_length=1, description="매크로 실행 ID")
    skip_indices: list[int] = Field(
        default_factory=list,
        description="첫 호출에서만 반영되는 제외 항목 번호(1-based)",
    )
    answer: str | None = Field(
        None,
        description="되묻기에 대한 사용자 답변. 있으면 멈춘 단계를 그 답으로 재개한다.",
    )
    skip_current: bool = Field(
        False,
        description="멈춰 선 단계를 건너뛰고 다음으로 진행할지 여부",
    )


class ExcelLiveMacroAbortRequest(BaseModel):
    macro_id: str = Field(..., min_length=1, description="매크로 실행 ID")
    rollback: bool = Field(False, description="매크로 시작 시점 백업으로 되돌릴지 여부")


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
class PlanExecution:
    """계획이 확정된 시점의 스냅샷. 실행 루프가 필요로 하는 전부다.

    명령 경로와 승인 경로가 **같은 실행 루프**를 타게 하려고 묶어 둔다. 승인 대기
    중에는 이 값이 그대로 보관됐다가, 사용자가 승인하면 재계획 없이 이어서
    실행된다. 승인 후에 다시 계획하면 사용자가 승인한 것과 다른 계획이 실행될 수
    있어서, 확정된 계획을 그대로 들고 있는 것이 요점이다.
    """

    req: ExcelLiveCommandRequest
    plan: list[PlanStep]
    session_key: str
    parsed: dict[str, Any] = field(default_factory=dict)
    base_context: dict[str, Any] = field(default_factory=dict)
    personalization_hint: str = ""
    bind_notes: list[dict[str, Any]] = field(default_factory=list)
    reasoning_mode: str = "fast"
    reasoning_complexity_score: float = 0.0
    reflection_attempted: bool = False
    reflection_applied: bool = False
    reflection_reason: str = ""
    # 이미 승인을 받았는가. True면 CONFIRM 게이트를 다시 세우지 않는다.
    approved: bool = False


@dataclass
class PendingExcelApproval:
    action: str
    params: dict[str, Any]
    workbook_id: str | None
    sheet_name: str | None
    created_at: str
    # 승인은 "단계"가 아니라 "계획"에 대한 것이다. 첫 CONFIRM 단계만 담고 나머지를
    # 버리면 표만 만들고 머리글은 안 넣거나, 수식만 넣고 검증은 건너뛰게 된다.
    # 더 나쁜 건 `_execute_action`을 직접 부르느라 검증·롤백·재계획이 통째로
    # 우회된다는 점이다. `resume`이 있으면 명령 경로와 같은 실행 루프로 이어 붙인다.
    # 단일 액션 승인(`/action` 경로)은 resume이 없고 예전처럼 한 단계만 실행한다.
    resume: PlanExecution | None = None


@dataclass
class PendingCreateTableSlots:
    session_id: str
    workbook_id: str | None
    sheet_name: str | None
    rows: int | None = None
    cols: int | None = None
    headers: list[str] | None = None
    values_2d: list[list[Any]] | None = None
    start_cell: str | None = None
    template_key: str | None = None
    template_follow_up_question: str | None = None
    ask_count: int = 0
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
class PendingClarification:
    """플래너가 스스로 되물은 질문 한 건.

    다음 턴의 문장("금액 열")은 그 질문에 대한 답변이라 그것만 보면 무슨 작업인지 알 수 없다.
    원래 요청과 질문을 함께 프롬프트로 돌려줘야 계획이 완성된다.
    """

    session_id: str
    original_message: str
    question: str
    ask_count: int = 1
    created_at_ts: float = 0.0


@dataclass
class MacroStepState:
    """매크로 하위 명령 하나의 진행 상태."""

    index: int
    command: str
    destructive: bool = False
    warnings: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | done | failed | skipped
    action: str = ""
    detail: str = ""


@dataclass
class MacroRun:
    """승인받은 매크로 한 건. 프론트가 /macro/step으로 한 걸음씩 당겨 쓴다."""

    macro_id: str
    message: str
    user_id: str | None
    session_id: str | None
    workbook_id: str | None
    sheet_name: str | None
    steps: list[MacroStepState]
    cursor: int = 0  # 다음에 실행할 0-based 위치
    status: str = "planned"  # planned | running | halted | done | aborted
    backup: dict[str, Any] | None = None
    follow_up_question: str = ""
    created_at_ts: float = 0.0
    updated_at_ts: float = 0.0

    @property
    def step_session_id(self) -> str:
        """하위 명령이 공유하는 세션 키.

        되묻기 슬롯이 이 키에 쌓이므로, 사용자의 답변도 같은 키로 보내야 이어진다.
        원래 채팅 세션과 섞으면 매크로가 끝난 뒤에도 슬롯이 남는다.
        """
        return f"excel-live::macro::{self.macro_id}"


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
_pending_clarifications: dict[str, PendingClarification] = {}
# 되묻기를 이만큼 연달아 하면 더 묻지 않고 가장 그럴듯한 대상으로 실행한다.
# 질문만 주고받는 대화는 아무것도 안 하는 것과 같다.
_MAX_CONSECUTIVE_CLARIFICATIONS = 2
_recent_range_by_workbook: dict[str, str] = {}
# 세션별 직전 집계 결과 시트. "그 결과로 그래프도" 같은 다음 턴이 원본 대신 집계표를 그리게 한다.
_last_aggregate_sheet: dict[str, str] = {}
_SLOT_TTL_SECONDS = 300
# 승인 대기·진행 중인 매크로. 슬롯보다 오래 사는데, 18단계를 사람이 확인하며 진행하면
# 5분으로는 모자라기 때문이다.
_macro_runs: dict[str, MacroRun] = {}
_MACRO_TTL_SECONDS = 600


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


# 분해는 계획보다 긴 출력을 낸다 — 실측(A.X-4.0-Light, 20단계)에서 25초가 걸렸으므로
# 부하가 걸린 상태를 감안해 넉넉히 잡는다. 넘기면 매크로를 포기하고 기존 단일 명령
# 경로로 떨어진다.
_MACRO_DECOMPOSE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_MACRO_TIMEOUT_SECONDS", 90.0, 5.0)
_COMMAND_PARSE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_PARSE_TIMEOUT_SECONDS", 10.0, 3.0)
_COMMAND_PARSE_MAX_ATTEMPTS = _env_int("EXCEL_LIVE_PARSE_MAX_ATTEMPTS", 2, 1)
_COMMAND_PARSE_RETRY_BACKOFF_SECONDS = _env_float(
    "EXCEL_LIVE_PARSE_RETRY_BACKOFF_SECONDS",
    0.5,
    0.0,
)
_COMMAND_DEEP_PARSE_TIMEOUT_SECONDS = _env_float(
    "EXCEL_LIVE_DEEP_PARSE_TIMEOUT_SECONDS",
    max(_COMMAND_PARSE_TIMEOUT_SECONDS + 4.0, 14.0),
    _COMMAND_PARSE_TIMEOUT_SECONDS,
)
_COMMAND_DEEP_PARSE_MAX_ATTEMPTS = _env_int(
    "EXCEL_LIVE_DEEP_PARSE_MAX_ATTEMPTS",
    max(_COMMAND_PARSE_MAX_ATTEMPTS, 2),
    1,
)
_COMMAND_DEEP_PARSE_RETRY_BACKOFF_SECONDS = _env_float(
    "EXCEL_LIVE_DEEP_PARSE_RETRY_BACKOFF_SECONDS",
    max(_COMMAND_PARSE_RETRY_BACKOFF_SECONDS, 0.7),
    0.0,
)
_COMMAND_REFLECTION_TIMEOUT_SECONDS = _env_float(
    "EXCEL_LIVE_REFLECTION_TIMEOUT_SECONDS",
    _COMMAND_DEEP_PARSE_TIMEOUT_SECONDS,
    3.0,
)
_COMMAND_REFLECTION_MAX_ATTEMPTS = _env_int("EXCEL_LIVE_REFLECTION_MAX_ATTEMPTS", 1, 1)
_EXCEL_QUEUE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS", 180.0, 10.0)
_EXCEL_QUEUE_LOCK = threading.RLock()
_ROLLBACK_MAX_CELLS = 50000
_SKIP_BACKUP_ACTIONS = {
    "excel_live.filter_rows",  # 뷰 필터가 중심이라 파일 백업 비용 대비 이득이 작다.
    "excel_live.refresh_power_query",  # 새로고침은 원본 값 덮어쓰기 성격이 약함.
    "excel_live.save_workbook",
}
_RECOVERY_BACKUP_ACTIONS = {name for name in EDIT_ACTIONS if name not in _SKIP_BACKUP_ACTIONS}
# "표/테이블을 만들어" 계열 요청인지 판정한다. 범위가 명시돼도 이 말이 있으면 표 생성이 맞다.
_TABLE_KEYWORD_PATTERN = re.compile(r"(표|테이블|table|양식|서식지)", re.IGNORECASE)

# 액션별로 "사용자가 이런 말을 했어야 이 액션이 나올 수 있다"는 최소 근거.
# 플래너는 모호할 때 그럴듯한 다른 액션으로 새는 일이 잦은데,
# 근거 없는 선택을 그대로 실행하면 사용자는 시키지 않은 색칠·표 생성을 보게 된다.
_ACTION_EVIDENCE: dict[str, re.Pattern[str]] = {
    "excel_live.create_table": re.compile(r"(표|테이블|table|양식|서식지)", re.IGNORECASE),
    "excel_live.fill_range": re.compile(r"(색|칠|배경|하이라이트|highlight|color|음영)", re.IGNORECASE),
    # 조건 표현(이상·미만)만으로는 근거가 되지 않는다. 수식·필터도 같은 말을 쓴다.
    "excel_live.highlight_by_condition": re.compile(
        r"(조건부|색|칠|강조|highlight|표시해|눈에)", re.IGNORECASE
    ),
    "excel_live.apply_border": re.compile(r"(테두리|괘선|border|윤곽|선을|선 )", re.IGNORECASE),
    "excel_live.protect_sheet": re.compile(r"(보호|잠금|잠가|protect|수정 ?못)", re.IGNORECASE),
    "excel_live.find_duplicates": re.compile(r"(중복|duplicate|겹치)", re.IGNORECASE),
    "excel_live.export_pdf": re.compile(r"(pdf|인쇄|출력물)", re.IGNORECASE),
    "excel_live.recalculate": re.compile(r"(갱신|새로고침|재계산|업데이트|refresh|recalc)", re.IGNORECASE),
    "excel_live.run_vba_macro": re.compile(r"(매크로|vba|macro)", re.IGNORECASE),
    "excel_live.refresh_power_query": re.compile(
        r"(파워\s*쿼리|power\s*query|쿼리|갱신|새로고침|refresh)", re.IGNORECASE
    ),
    "excel_live.consolidate_workbooks_from_folder": re.compile(
        r"(폴더|파일들|여러 파일|통합|합쳐|모아)", re.IGNORECASE
    ),
    "excel_live.create_chart": re.compile(r"(차트|그래프|chart|graph|시각화)", re.IGNORECASE),
    "excel_live.sort_range": re.compile(r"(정렬|sort|오름|내림|순으로|순서대로)", re.IGNORECASE),
    "excel_live.filter_rows": re.compile(r"(필터|filter|만 남|만 보|추출|골라)", re.IGNORECASE),
    "excel_live.dedupe_rows": re.compile(r"(중복|dedupe|duplicate)", re.IGNORECASE),
    "excel_live.pivot_table": re.compile(r"(피벗|pivot|집계|요약)", re.IGNORECASE),
    "excel_live.forecast_linear": re.compile(r"(예측|추세|forecast|전망)", re.IGNORECASE),
    "excel_live.compare_ranges": re.compile(r"(비교|차이|diff|대조)", re.IGNORECASE),
    "excel_live.consolidate_sheets": re.compile(r"(통합|합쳐|병합|모아)", re.IGNORECASE),
    "excel_live.protect_sheet": re.compile(r"(보호|잠금|잠가|protect|수정 ?못)", re.IGNORECASE),
    "excel_live.set_data_validation": re.compile(
        r"(드롭다운|유효성|목록|제한|validation|선택되도록)", re.IGNORECASE
    ),
    "excel_live.verify_formula_result": re.compile(r"(검증|확인|검사|verify|점검)", re.IGNORECASE),
    "excel_live.clear_range": re.compile(r"(지워|삭제|비워|clear|초기화)", re.IGNORECASE),
}


# 계획 앞뒤에 붙는 준비·마무리 동작. 사용자가 말하지 않아도 따라붙는 게 정상이다.
_PREPARATION_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.list_sheets",
    "excel_live.select_sheet",
    "excel_live.create_sheet",
    "excel_live.read_range",
    "excel_live.save_workbook",
}
# 값·수식을 넣는 건 "무엇을 넣을지"가 파라미터에 들어 있어야만 실행된다.
# 표현이 워낙 다양해서(써줘/넣어/fill/set) 단어로 거르면 정상 요청까지 막힌다.
_UNGATED_EDIT_ACTIONS = {
    "excel_live.write_range",
    "excel_live.set_formula",
    "excel_live.verify_formula_result",
}


def _action_lacks_evidence(action: str, message: str) -> bool:
    """사용자가 시키지 않은 편집을 걸러낸다.

    근거 표를 빠뜨린 액션(과거 apply_border가 그랬다)은 무조건 통과해 버려서
    "테두리 그려달라고 한 적 없는데 전체에 선이 그어지는" 사고가 났다.
    그래서 편집 액션은 근거 규칙이 없으면 근거 없음으로 본다.
    """
    name = str(action or "")
    pattern = _ACTION_EVIDENCE.get(name)
    if pattern is not None:
        return not pattern.search(str(message or ""))
    if name in _PREPARATION_ACTIONS or name in _UNGATED_EDIT_ACTIONS:
        return False
    return name in EDIT_ACTIONS
# 기준 열을 못 정한 채 실행하면 데이터가 조용히 뒤섞이는 액션들.
#
# filter_rows가 특히 위험하다. 기준 열을 못 정하면 검증기가 1번 열로 채우는데,
# 이 액션은 조건에 안 맞는 행을 지운다. 엉뚱한 열로 거르면 남아야 할 데이터가
# 사라지고, 사용자는 "필터 완료" 메시지만 본다.
_AMBIGUITY_SENSITIVE_SLOTS = {
    ("excel_live.sort_range", "key_column"),
    ("excel_live.dedupe_rows", "key_columns"),
    ("excel_live.create_chart", "chart_type"),
    ("excel_live.filter_rows", "column"),
}
_CHART_TYPE_MENTION = re.compile(
    r"(선\s*그래프|꺾은|라인|line|막대|bar|원형|파이|pie|영역|area|분산|scatter)", re.IGNORECASE
)
# 계획 끝에 관례적으로 붙는 마무리 단계. 응답 액션으로 보고하면 실제 작업이 가려진다.
_ANCILLARY_REPORT_ACTIONS = {
    "excel_live.save_workbook",
    "excel_live.select_sheet",
    "excel_live.select_workbook",
}
_ROLLBACK_SNAPSHOT_ACTIONS = {
    "excel_live.write_range",
    "excel_live.clear_range",
    "excel_live.set_formula",
    "excel_live.sort_range",
    "excel_live.dedupe_rows",
    "excel_live.create_table",
}
_COMPLEX_REASONING_KEYWORDS = {
    "피벗",
    "pivot",
    "차트",
    "그래프",
    "대시보드",
    "검증",
    "유효성",
    "자동화",
    "매크로",
    "vba",
    "power query",
    "파워쿼리",
    "비교",
    "diff",
    "예측",
    "시뮬레이션",
    "연결",
    "링크",
    "함수",
    "수식",
    "vlookup",
    "countif",
    "sumif",
    "여러 시트",
    "cross sheet",
}
_DEEP_REASONING_OPERATION_INTENTS = {
    "formula",
    "pivot",
    "chart",
    "protect",
    "consolidate",
    "automation",
    "compare",
    "forecast",
    "print",
    "general",
}
_EDIT_EXPECTED_OPERATION_INTENTS = {
    "formula",
    "sort",
    "filter",
    "dedupe",
    "pivot",
    "chart",
    "protect",
    "consolidate",
    "automation",
    "compare",
    "forecast",
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


def _ambiguous_workbook_response(exc: AmbiguousWorkbookError) -> ExcelLiveActionResponse:
    """대상 통합문서를 못 정했을 때 후보를 들고 되묻는 응답을 만든다."""
    candidates = list(exc.candidates)[:5]
    question = str(exc)
    if candidates:
        question = f"{question} 후보: {', '.join(candidates)}"
    return ExcelLiveActionResponse(
        ok=True,
        action="excel_live.clarify",
        reason=question,
        result={
            "ask_follow_up": True,
            "follow_up_question": question,
            "operation_intent": "clarify",
            "missing_slot": "workbook_id",
            "candidates": candidates,
        },
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


def _split_sheet_qualified_range(service, workbook_id: str | None, range_ref: str) -> tuple[str, str]:
    """`Sheet!A1:B2` 를 (시트명, `A1:B2`)로 나눈다.

    플래너는 범위를 시트까지 붙여 말하는 경우가 있는데, 셀 범위 파서는 접두어를 모른다.
    그대로 넘기면 "데이터가 비어 있다"는 엉뚱한 실패가 된다. 접두어가 실제 시트일 때만
    시트로 인정하고, 아니면 접두어만 떼어 낸다.
    """
    text = str(range_ref or "").strip()
    if "!" not in text:
        return "", text
    prefix, _, cell_part = text.rpartition("!")
    cell_part = cell_part.strip()
    if not cell_part:
        return "", text
    prefix = prefix.strip().strip("'\"")
    try:
        sheets = service.list_sheets(workbook_id).get("sheets", [])
    except Exception:
        sheets = []
    for name in sheets:
        if str(name).strip().lower() == prefix.lower():
            return str(name), cell_part
    return "", cell_part


def _edit_target_problem(workbook_id: str | None, sheet_name: str | None) -> str:
    """편집 대상이 실제로 존재하는지 확인하고, 아니면 되물을 문장을 만든다.

    플래너는 원문에 나온 낱말을 시트 이름으로 그대로 옮겨 적는다("학과운영비 작업" →
    sheet_name="학과운영비"). 그 시트가 없어도 승인 카드는 떠 버리고, 사용자가 승인한
    뒤에야 404로 죽는다. 승인을 요청하기 전에 여기서 걸러야 한다.
    """
    target_sheet = str(sheet_name or "").strip()
    if not target_sheet:
        return ""
    service = get_excel_live_service()
    try:
        resolved_wb = _resolve_workbook_id(service, workbook_id)
    except ExcelLiveError as exc:
        return f"{exc} 작업할 파일을 먼저 열거나 선택해 주세요."
    except Exception:
        return ""
    try:
        sheets = [str(name) for name in (service.list_sheets(resolved_wb).get("sheets") or [])]
    except ExcelLiveError as exc:
        return f"{exc} 작업할 파일을 먼저 열거나 선택해 주세요."
    except Exception:
        return ""
    if not sheets or target_sheet in sheets:
        return ""
    return (
        f"'{target_sheet}' 시트를 찾을 수 없습니다. 어느 시트에 작업할까요? "
        f"현재 시트: {', '.join(sheets[:8])}"
    )


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
    if text == "__USED_RANGE__":
        used = _normalize_range_text(service.get_used_range_ref(workbook_id, sheet_name))
        return _top_left_cell(used) if for_cell else used
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


def _cleanup_expired_clarifications() -> None:
    now = time.time()
    expired = [
        key
        for key, pending in _pending_clarifications.items()
        if (now - float(pending.created_at_ts or now)) > _SLOT_TTL_SECONDS
    ]
    for key in expired:
        _pending_clarifications.pop(key, None)


def _render_conversation_history(pending: PendingClarification | None) -> str:
    if pending is None:
        return ""
    return render_conversation_history(pending.original_message, pending.question)


def _cleanup_expired_macro_runs() -> None:
    now = time.time()
    expired = [
        key
        for key, run in _macro_runs.items()
        if (now - float(run.updated_at_ts or run.created_at_ts or now)) > _MACRO_TTL_SECONDS
    ]
    for key in expired:
        _macro_runs.pop(key, None)


def _macro_snapshot(run: MacroRun) -> dict[str, Any]:
    """프론트가 카드를 그리는 데 필요한 만큼만 담는다."""
    done = sum(1 for step in run.steps if step.status == "done")
    return {
        "macro_id": run.macro_id,
        "status": run.status,
        "total": len(run.steps),
        "completed": done,
        "cursor": run.cursor + 1 if run.cursor < len(run.steps) else len(run.steps),
        "steps": [
            {
                "index": step.index,
                "command": step.command,
                "destructive": step.destructive,
                "warnings": list(step.warnings),
                "status": step.status,
                "action": step.action,
                "detail": step.detail,
            }
            for step in run.steps
        ],
        "backup_path": str((run.backup or {}).get("backup_path") or ""),
    }


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
    if token in {"흰색", "하얀색", "하양", "white", "화이트", "백색"}:
        return "#FFFFFF"
    return "#FFFF00"


def _quick_extract_colors(text: str) -> list[str]:
    matches = re.findall(
        r"(노란색|노랑|yellow|빨간색|빨강|red|파란색|파랑|blue|초록색|초록|green|흰색|하얀색|하양|white|화이트|백색)",
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
    # "10만 원 미만"처럼 단위가 낀 임계값은 전용 파서가 맡는다(숫자만 보면 10이 된다).
    parsed = parse_korean_condition(lowered)
    if parsed is not None:
        operator, amount, _percent = parsed
        return operator, amount

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


# "현재고가 재주문점 이하" — 숫자 없이 두 열을 견주는 조건.
# "보다 적거나 같은"처럼 구어체 비교도 같은 뜻이라 함께 받는다.
_COLUMN_COMPARISON_PATTERN = re.compile(
    r"[가-힣A-Za-z_][가-힣A-Za-z0-9_ ]{0,12}\s*(?:이|가)\s*"
    r"[가-힣A-Za-z_][가-힣A-Za-z0-9_ ]{0,12}\s*"
    r"(?:이하|이상|미만|초과|보다\s*(?:작|크|적|많|낮|높))"
)


# "이 파일에 시트가 뭐뭐 있어?" — 목록이라는 단어 없이 묻는 쪽이 더 흔하다.
# 플래너는 "파일"에 끌려 list_workbooks를 고르곤 한다.
_SHEET_INVENTORY_QUESTION = re.compile(
    r"(?:시트|탭|sheet)[가는은이]?\s*(?:뭐|무슨|어떤|몇|얼마나|어떻게)"
    r"|(?:무슨|어떤|몇\s*개의?)\s*(?:시트|탭|sheet)",
    re.IGNORECASE,
)


def _looks_like_column_comparison(text: str) -> bool:
    match = _COLUMN_COMPARISON_PATTERN.search(str(text or ""))
    return bool(match) and not re.search(r"\d", match.group(0))


# fast path로 보내면 조건이 통째로 사라지는 액션들. "100만 미만은 빨갛게"가
# fill_range로 가면 조건을 잃고 열 전체를 칠한다.
_CONDITION_SENSITIVE_QUICK_ACTIONS = frozenset(
    {
        "excel_live.fill_range",
        "excel_live.clear_range",
        "excel_live.apply_border",
    }
)
# "수식 채워줘"의 "채워"가 색 채우기 규칙에 먼저 잡힌다.
_FORMULA_SENSITIVE_QUICK_ACTIONS = frozenset({"excel_live.fill_range", "excel_live.write_range"})
_FORMULA_MENTION_PATTERN = re.compile(r"(수식|함수|계산식|formula)", re.IGNORECASE)
# "지역별 매출 얼마", "채널 카테고리 교차표" — 집계 요청인데 표 만들기·정렬로 샌다.
_AGGREGATE_SENSITIVE_QUICK_ACTIONS = frozenset(
    {
        "excel_live.create_table",
        "excel_live.write_range",
        "excel_live.fill_range",
        "excel_live.sort_range",
    }
)
_AGGREGATE_REQUEST_PATTERN = re.compile(
    r"(교차표|피벗|pivot)"
    r"|별\s*(?:로|은|는)?[^\n]{0,20}?(합계|집계|평균|순위|총액|총합|건수|얼마|요약|실적)",
    re.IGNORECASE,
)


def _message_states_condition(text: str) -> bool:
    """"매출이 100만도 안 되는 건"처럼 대상을 좁히는 조건이 붙었는지."""
    message = str(text or "")
    return _quick_parse_condition(message) is not None or _looks_like_column_comparison(message)


def _quick_plan_underfits_message(quick_first_action: str, text: str) -> bool:
    """규칙이 고른 액션으로는 문장이 요구하는 것을 표현할 수 없는지.

    규칙은 동사 하나(칠해/채워/만들어)만 보고 액션을 정한다. 조건·수식·집계처럼
    목적어 쪽에 뜻이 실린 문장은 여기서 걸러 플래너로 넘긴다.
    """
    message = str(text or "")
    if quick_first_action in _CONDITION_SENSITIVE_QUICK_ACTIONS and _message_states_condition(message):
        return True
    if quick_first_action in _FORMULA_SENSITIVE_QUICK_ACTIONS and _FORMULA_MENTION_PATTERN.search(message):
        return True
    if quick_first_action in _PREPARATION_ONLY_QUICK_ACTIONS and _message_asks_for_more_work(message):
        return True
    return quick_first_action in _AGGREGATE_SENSITIVE_QUICK_ACTIONS and bool(
        _AGGREGATE_REQUEST_PATTERN.search(message)
    )


# 시트를 만들거나 고르는 건 보통 본 작업의 준비 단계다. 규칙이 이걸 계획 전체로 삼으면
# "Summary 시트 만들어서 B1에 합계 수식 넣어줘"가 빈 시트 하나만 남기고 끝난다.
_PREPARATION_ONLY_QUICK_ACTIONS = frozenset(
    {"excel_live.create_sheet", "excel_live.select_sheet", "excel_live.select_workbook"}
)
_FOLLOW_UP_WORK_PATTERN = re.compile(
    r"(써|쓰고|쓴|적어|입력|넣어|기입|채워|계산|합계|집계|정렬|필터|복사|옮겨|만들어서|하고\s|한\s*다음|후에|그리고)",
    re.IGNORECASE,
)


def _message_asks_for_more_work(text: str) -> bool:
    """시트 준비 말고도 할 일이 더 적혀 있는 문장인지."""
    return bool(_FOLLOW_UP_WORK_PATTERN.search(str(text or "")))


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
            "흰색",
            "하얀색",
            "하양",
            "white",
            "화이트",
            "백색",
        ]
    )
    has_format_verb = any(
        token in lowered
        for token in ["색칠", "칠해", "배경", "강조", "표시", "highlight", "구분", "색을", "색깔", "바꿔", "만들어"]
    )
    return has_color and has_format_verb


def _has_border_style_context(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(token in lowered for token in ["테두리", "경계선", "border", "보더"]):
        return True
    # "경계값" 같은 수학/통계 문맥은 제외하고, "경계들을" 같은 구어체는 경계선 요청으로 본다.
    return "경계" in lowered and "경계값" not in lowered


def _is_color_clear_request(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    has_color_context = any(
        token in text for token in ["색", "색깔", "배경색", "color", "컬러", "fill"]
    )
    if not has_color_context:
        return False

    has_color_clear_verb = bool(
        re.search(
            r"(색|색깔|배경색|color).{0,12}(없애|제거|지우|삭제|비우|초기화|리셋|reset|clear)",
            text,
            re.IGNORECASE,
        )
        or re.search(
            r"(없애|제거|지우|삭제|비우|초기화|리셋|reset|clear).{0,12}(색|색깔|배경색|color)",
            text,
            re.IGNORECASE,
        )
    )
    has_color_reset_phrase = bool(
        re.search(
            r"(색|색깔|배경색|color).{0,12}(원래|기본)",
            text,
            re.IGNORECASE,
        )
    )
    has_border_context = _has_border_style_context(text)
    has_background_fill_context = any(
        token in text for token in ["배경", "배경색", "색칠", "칠해", "채워", "채우", "fill"]
    )

    # "경계선 색을 기본으로"는 배경색이 아니라 border 요청으로 본다.
    if has_border_context and not has_background_fill_context and not has_color_clear_verb:
        return False

    if has_color_clear_verb:
        return True
    if has_color_reset_phrase:
        return True
    if re.search(
        r"(색|색깔|배경색|color).{0,12}(없애|제거|지우|삭제|비우|초기화|리셋|reset|clear|원래|기본)",
        text,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"(없애|제거|지우|삭제|비우|초기화|리셋|reset|clear).{0,12}(색|색깔|배경색|color)",
        text,
        re.IGNORECASE,
    ):
        return True
    return False


def _is_whole_sheet_style_request(
    lowered: str,
    normalized_ctx: str,
    explicit_range: str,
) -> bool:
    if normalized_ctx or explicit_range:
        return False
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in [
            "전체",
            "모든",
            "전부",
            "시트 전체",
            "전체 범위",
            "엑셀 전체",
            "엑셀 화면 전체",
            "통째로",
            "전반적으로",
        ]
    ) or bool(re.search(r"\b다\s*(색|테두리|경계선|보더)", text, re.IGNORECASE))


def _is_clear_reset_request(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    # "지우지 말고", "삭제 안 되게" 같은 금지/보호 문맥은 clear 의도에서 제외한다.
    if re.search(r"(지우|삭제|비우).{0,8}(지\s*마|지\s*말|않|안\s*되|못\s*하)", text):
        return False
    has_clear_verb = bool(
        re.search(
            r"(지우|지워|지울|삭제|비우|비워|초기화|리셋|reset|clear|wipe|erase|밀어|싹)",
            text,
            re.IGNORECASE,
        )
    )
    has_state_reset_phrase = any(
        token in text
        for token in [
            "원래 상태",
            "처음 상태",
            "초기 상태",
            "기본 상태",
            "원상복구",
            "처음으로",
        ]
    )
    if has_state_reset_phrase and any(token in text for token in ["최근", "마지막", "백업", "되돌리", "undo"]):
        return False
    return has_clear_verb or has_state_reset_phrase


# "지워줘"가 붙었다고 다 전체 삭제가 아니다. 아래 표현이 있으면 지울 대상이 따로 있다.
_CLEAR_COLUMN_TARGET = re.compile(
    r"(?:[A-Za-z0-9_]{2,24}|[가-힣]{2,12})\s*(?:열|칼럼|컬럼|column)(?=[\s을를은는도만과와,.]|$)",
    re.IGNORECASE,
)
_CLEAR_ROW_TARGET = re.compile(r"(중복|빈\s*(?:행|줄|칸)|\d+\s*(?:번째\s*)?(?:행|줄))")
_CLEAR_CONDITION_TARGET = re.compile(
    # "이상"만으로 잡으면 "뭔가 이상한 명령"이 조건절로 읽힌다. 비교어 앞에 수량이 있어야 한다.
    r"(?:된|인|한|일)\s*(?:것|건|행|줄|항목|데이터|주문|고객|제품)"
    r"|[\d%원개건만억]\s*(?:이상|이하|미만|초과)"
)


def _clear_request_targets_a_subset(lowered: str) -> bool:
    """지우기 요청이 시트 전체가 아니라 특정 열·행·조건을 겨냥하는지 본다.

    "Discount 열은 지워줘"를 전체 비우기로 처리하면 표가 통째로 날아간다. 실행은
    성공으로 보고되고 사용자는 되돌리기 전까지 알아채지 못한다. 이런 문장은 규칙 경로에
    맡기지 말고 플래너로 보내 drop_column·filter_rows 같은 제대로 된 도구를 고르게 한다.
    """
    text = str(lowered or "").strip()
    if not text:
        return False
    return bool(
        _CLEAR_COLUMN_TARGET.search(text)
        or _CLEAR_ROW_TARGET.search(text)
        or _CLEAR_CONDITION_TARGET.search(text)
    )


def _is_whole_sheet_reset_request(
    lowered: str,
    normalized_ctx: str,
    explicit_range: str,
) -> bool:
    if normalized_ctx or explicit_range:
        return False
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in [
            "전체",
            "모든",
            "전부",
            "엑셀",
            "시트 전체",
            "화면",
            "통째로",
            "싹",
            "깔끔하게",
            "깨끗하게",
            "원래 상태",
            "처음 상태",
            "초기 상태",
            "기본 상태",
        ]
    ) or bool(re.search(r"\b다\s*(지우|비우|삭제)", text, re.IGNORECASE))


# "Region_Chart 시트에"의 시트 이름. 의도 판정에서는 이름 속 단어를 지시어로 읽으면 안 된다
# ("Region_Chart"의 chart 때문에 집계 요청이 차트 요청이 되어버린다).
_SHEET_NAME_MENTION = re.compile(r"[A-Za-z0-9가-힣_]{1,24}\s*(?:시트|sheet)", re.IGNORECASE)


def _mask_sheet_names(text: str) -> str:
    return _SHEET_NAME_MENTION.sub(" 시트 ", str(text or ""))


def _normalized_message_views(message: str) -> tuple[str, str]:
    lowered = _mask_sheet_names(str(message or "").strip()).lower()
    compact = re.sub(r"[\s\-_]+", "", lowered)
    return lowered, compact


def _contains_any_keyword(lowered: str, compact: str, keywords: list[str]) -> bool:
    for keyword in keywords:
        token = str(keyword or "").strip().lower()
        if not token:
            continue
        if token in lowered:
            return True
        token_compact = re.sub(r"[\s\-_]+", "", token)
        if token_compact and token_compact in compact:
            return True
    return False


def _matches_any_pattern(lowered: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, lowered, re.IGNORECASE):
            return True
    return False


# "채널별", "영업담당자별" — 묶는 기준은 열 이름만큼 다양해서 열거할 수 없다.
# 대신 "무엇이든 -별"을 일반 패턴으로 잡고, 뒤에 붙는 동사가 아니라 집계어로 판정한다.
_GROUP_MARKER = re.compile(r"([가-힣a-z0-9_]{2,12})\s*별(?:로|\s|$|[가-힣])")
# 뜻이 "묶는 기준"이 아닌 -별 단어들. 이걸 거르지 않으면 "특별히 정렬해줘"가 집계가 된다.
_GROUP_MARKER_STOPWORDS = frozenset({"특별", "개별", "각별", "구별", "판별", "이별", "차별", "선별", "식별"})
_AGGREGATE_WORDS = [
    "합계",
    "총합",
    "총계",
    "소계",
    "누계",
    "평균",
    "건수",
    "개수",
    "집계",
    "집계표",
    "요약",
    "합산",
    "통계",
    "실적",
]


def _looks_like_group_aggregate(lowered: str, compact: str) -> bool:
    """ "채널별 매출 합계를 Channel_Sum 시트에 만들어줘" 처럼 묶어서 더하라는 요청인가.

    묶는 기준(-별)과 집계어가 함께 있어야만 참이다. 둘 중 하나만 있으면 다른 의도다.
    "월별 추이를 그려줘"는 차트, "부서별 달성률 계산해줘"는 수식이어야 한다.
    """
    if not _contains_any_keyword(lowered, compact, _AGGREGATE_WORDS):
        return False
    for match in _GROUP_MARKER.finditer(lowered):
        if match.group(1).strip() not in _GROUP_MARKER_STOPWORDS:
            return True
    return False


# 열 구조를 손보는 요청. "마진율로 바꿔줘"의 '마진율'이 수식 키워드에 먼저 잡히고,
# "열 하나 추가해줘"는 어떤 의도에도 걸리지 않아 400으로 떨어진다.
_COLUMN_STRUCTURE_EDIT_PATTERNS = (
    r"열\s*(?:이름|머리글|헤더|명)\s*(?:을|를|은|는)?\s*.{0,24}(?:바꿔|변경|고쳐|수정)",
    r"(?:열|칼럼|컬럼)\s*(?:을|를|은|는)?\s*.{0,24}(?:으?로)\s*(?:이름\s*)?(?:바꿔|변경|고쳐)",
    r"(?:열|칼럼|컬럼)\s*(?:하나|한\s*개|１개|1개)?\s*(?:더|새로)?\s*(?:추가|만들어|넣어|생성)",
    r"(?:새|신규)\s*(?:열|칼럼|컬럼)",
    r"(?:열|칼럼|컬럼)\s*(?:을|를|은|는|도)?\s*(?:통째로\s*)?(?:삭제|제거|없애|지워|빼)",
)


def _looks_like_column_structure_edit(lowered: str) -> bool:
    text = str(lowered or "")
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _COLUMN_STRUCTURE_EDIT_PATTERNS)


def _detect_operation_intent(message: str) -> str:
    lowered, compact = _normalized_message_views(message)
    color_format_request = _is_color_format_request(lowered)
    color_condition_request = color_format_request and (_quick_parse_condition(lowered) is not None)
    if _looks_like_column_structure_edit(lowered):
        # 열 이름 바꾸기·열 추가·열 삭제. 슬롯 대화로 끌고 가면 되묻기만 하고 끝난다.
        # 플래너에게 넘겨 rename_column/add_column/drop_column을 고르게 한다.
        return ""
    if _looks_like_named_formula(str(message or "")):
        # "이익률 열에 매출이익 나누기 매출" — 열 이름만으로 계산을 말한 경우.
        return "formula"
    if _looks_like_group_aggregate(lowered, compact):
        # 문장 끝 동사("만들어줘"/"정리해줘")보다 "-별 + 합계"가 우선이다.
        # 동사를 먼저 보면 같은 집계 요청이 시트 생성이나 정렬로 흩어진다.
        return "pivot"
    if (not color_format_request) and (
        _contains_any_keyword(
            lowered,
            compact,
            [
                "곱해서",
                "곱한",
                "곱해",
                "나눠",
                "나눗셈",
                "합산",
                "총합",
                "세금 포함",
                "부가세",
                "목표 대비",
                "부족한지",
                "자동으로 계산",
                "계산식",
                "계산해",
                "산출",
                "함수 적용",
                "수식 넣",
                "countif",
                "vlookup",
                "sumif",
                "if(",
                "건수",
                "개수",
                "찾아와",
                "찾아오",
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
            ],
        )
        or _matches_any_pattern(
            lowered,
            [
                r"(수량|단가|금액).{0,18}(계산|산출|자동)",
                r"(곱|더하|빼|나누).{0,8}(해|해서|하|되)",
                r"(if|vlookup|countif|sumif|averageif)\s*\(",
            ],
        )
    ):
        return "formula"
    # 함수/필터/수식 오류는 단순 필터/검증보다 디버그 의도로 우선 분기한다.
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "#n/a",
            "#value",
            "#div/0",
            "수식이 이상",
            "합계가 이상",
            "오류 고쳐",
            "filter 함수가 안",
            "필터함수가안",
            "함수 오류",
            "수식 오류",
        ],
    ) or _matches_any_pattern(
        lowered,
        [
            r"(필터|수식|함수).{0,10}(안\s*돼|안돼|안\s*됨|먹통|오류|이상)",
            r"(오류|에러).{0,8}(고쳐|수정|해결|잡아)",
        ],
    ):
        return "debug"
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "정렬",
            "높은 순",
            "낮은 순",
            "오름차순",
            "내림차순",
            "순으로",
            "순서대로",
            "순위대로",
            "재배치",
            "줄세워",
            "줄 세워",
            "상위",
            "하위",
            "rank",
            "제일 큰",
            "가장 큰",
            "제일 많이",
            "많이 했",
        ],
    ) or _matches_any_pattern(
        lowered,
        [
            r"(큰|높은|많은|작은|낮은|적은)\s*(값|순|순서)",
            r"(정렬|배치|재배치|줄세우).{0,8}(해|해줘|하|해봐)",
        ],
    ):
        return "sort"
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "필터",
            "완료만",
            "완료",
            "만 보여",
            "골라줘",
            "추려",
            "걸러",
            "남겨",
            "따로 보고",
            "위험한",
            "상태 열",
        ],
    ) or _matches_any_pattern(
        lowered,
        [
            r"(만)\s*(보여|남겨|추려|걸러)",
            r"(조건|기준).{0,10}(필터|추리|걸러)",
        ],
    ):
        return "filter"
    if _contains_any_keyword(lowered, compact, ["중복", "중복된", "중복값", "겹친 값", "겹치는"]):
        # "중복 찾아줘"와 "중복 지워줘"는 되돌릴 수 있는지가 다르다. 삭제는 명시적으로 말했을 때만.
        if _matches_any_pattern(lowered, [r"(중복|겹치).{0,10}(제거|삭제|없애|정리|지워|빼)"]):
            return "dedupe"
        if _matches_any_pattern(
            lowered, [r"(중복|겹치).{0,12}(찾|확인|알려|점검|검사|있는지|보여|표시|체크)"]
        ):
            return "dupescan"
        return "dupescan"
    if _contains_any_keyword(
        lowered,
        compact,
        ["피벗", "집계표", "월별", "부서별", "지역별", "담당자별", "카테고리별", "고객별"],
    ):
        return "pivot"
    if _contains_any_keyword(
        lowered,
        compact,
        ["차트", "그래프", "시각화", "도식화", "비율로 보고", "한눈에", "추이", "발표용"],
    ) or _matches_any_pattern(lowered, [r"(차트|그래프|시각화|도식화).{0,8}(만들|생성|그려|표시)"]):
        return "chart"
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "검증",
            "이상한 값",
            "오류",
            "형식 이상",
            "점검",
            "검토",
            "진단",
            "체크",
            "빠진 값",
            "형식 이상한",
            "계산이 맞는지",
            "문제점",
            "문제",
            "검산",
            "틀린 값",
        ],
    ):
        return "validate"
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "보호",
            "잠금",
            "잠가",
            "잠궈",
            "잠궈줘",
            "건들지",
            "건들지 못",
            "편집 막",
            "수정 금지",
            "입력 제한",
            "드롭다운",
            "유효성",
            "숫자만 입력",
            "날짜만 입력",
            "못 고치게",
            "수정 못",
            "목록에서 고르게",
            "잘못 입력 못",
        ],
    ):
        return "protect"
    if _contains_any_keyword(lowered, compact, ["파일 여러", "시트 여러", "합쳐", "merge", "폴더", "원본 파일"]) or _matches_any_pattern(
        lowered, [r"(파일|시트).{0,10}(통합|합치|병합)"]
    ):
        return "consolidate"
    if _contains_any_keyword(
        lowered, compact, ["vba", "매크로", "power query", "파워쿼리", "쿼리", "refreshall", "연결 데이터"]
    ):
        return "automation"
    if _contains_any_keyword(lowered, compact, ["새로고침", "재계산", "다시 계산"]) or _matches_any_pattern(
        lowered, [r"(대시보드|수식|시트|데이터).{0,10}(갱신|업데이트|최신)"]
    ):
        return "recalc"
    if _contains_any_keyword(lowered, compact, ["비교", "차이", "diff", "다른 값", "바뀐", "지난달", "전월", "전년"]):
        return "compare"
    if _contains_any_keyword(lowered, compact, ["예측", "추세", "시뮬레이션", "다음 달", "연말", "forecast", "앞으로"]):
        return "forecast"
    if _contains_any_keyword(lowered, compact, ["a4", "인쇄", "pdf", "출력", "제출"]):
        return "print"
    if _contains_any_keyword(
        lowered,
        compact,
        [
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
        ],
    ):
        return "safety"
    if _contains_any_keyword(lowered, compact, ["느려", "멈춰", "버벅", "업데이트가 안 돼"]):
        return "performance"
    if _contains_any_keyword(lowered, compact, ["피벗이 뭐", "power query가 뭐", "뭐야", "설명해줘", "무슨 뜻"]):
        return "explain"
    if _contains_any_keyword(
        lowered,
        compact,
        [
            "정리해줘",
            "알아서",
            "보기 좋게",
            "보기 편하게",
            "다듬어줘",
            "손봐줘",
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
            "포맷팅",
            "형식 맞춰",
        ],
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
    """ "단가는 D열" 처럼 항목명과 짝지어진 열 문자를 뽑는다.

    keyword는 "단가|가격"처럼 대안 목록이 올 수 있어 반드시 비캡처 그룹으로 감싼다.
    감싸지 않으면 대안 하나만 매칭돼 group(1)이 None이 되고, 그대로 "NONE" 열이 만들어진다.
    또 절 구분자(쉼표 등)를 넘어가면 앞 절의 열을 잘못 집어오므로 구분자 앞에서 끊는다.
    """
    alternatives = f"(?:{keyword})"
    separator_free = r"[^A-Z0-9,;·\n]"
    # "단가는 D열"처럼 항목명이 앞에 오는 형태를 우선한다.
    m = re.search(rf"{alternatives}{separator_free}{{0,8}}([A-Z])\s*열", text, re.IGNORECASE)
    if m and m.group(1):
        return str(m.group(1)).upper()
    m = re.search(rf"([A-Z])\s*열{separator_free}{{0,8}}{alternatives}", text, re.IGNORECASE)
    if m and m.group(1):
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


def _quote_sheet_for_formula(sheet_name: str) -> str:
    text = str(sheet_name or "").strip()
    if not text:
        return "Sheet1"
    escaped = text.replace("'", "''")
    if re.search(r"[^A-Z0-9_]", text, re.IGNORECASE):
        return f"'{escaped}'"
    return escaped


def _extract_formula_from_text(text: str) -> str | None:
    """문장에 직접 적힌 Excel 수식을 통째로 뽑는다.

    쉼표를 종료 문자로 보면 =IF(B2>=70,"통과","미달")이 =IF(B2>=70에서 잘리므로,
    괄호 깊이와 따옴표 상태를 추적해 수식의 실제 끝을 찾는다.
    """
    raw = str(text or "")
    start = -1
    for idx, ch in enumerate(raw):
        if ch != "=":
            continue
        # >=, <=, != 의 '='는 수식 시작이 아니다.
        if idx > 0 and raw[idx - 1] in "<>!=":
            continue
        start = idx
        break
    if start < 0:
        return None

    depth = 0
    in_quote = False
    end = len(raw)
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth <= 0:
                end = idx + 1
                break
        elif depth == 0 and ch.isspace() and idx > start:
            end = idx
            break
    formula = raw[start:end].strip()
    return formula if len(formula) > 1 else None


_SHEET_MENTION_PATTERN = re.compile(r"([^\s,]+)\s*(?:시트|sheet)", re.IGNORECASE)


def _extract_sheet_mentions(text: str) -> list[str]:
    return [str(name).strip().strip("\"'") for name in _SHEET_MENTION_PATTERN.findall(str(text or ""))]


def _extract_output_sheet_from_text(text: str) -> str | None:
    """결과를 써 넣을 시트명을 고른다.

    문장에서 첫 번째로 등장하는 시트는 대개 '원본'이다("매출 시트 ... 예측1 시트에 써줘").
    첫 매치를 쓰면 결과가 원본 데이터를 덮어써서 파괴하므로 마지막 언급을 택한다.
    """
    mentions = _extract_sheet_mentions(text)
    if len(mentions) < 2:
        return None
    return mentions[-1]


_HEADER_INTENT_PATTERN = re.compile(r"헤더|머리글|컬럼\s*명|열\s*이름|header", re.IGNORECASE)
_TABLE_CREATE_INTENT_PATTERN = re.compile(r"(?:표|테이블|table)\s*\S{0,4}\s*(?:만들|생성|작성|create)", re.IGNORECASE)


def _quick_header_write_step(text: str, preferred_cell: str) -> dict[str, Any] | None:
    """머리글 목록만 준 문장을 표 첫 행 쓰기로 바꾼다.

    "헤더에는 '날짜', '금액', ... 이렇게 만들어줘"는 이미 만들어 둔 표의 첫 줄을 채우라는 뜻인데,
    LLM에 맡기면 이름마다 add_column을 부르거나 엉뚱한 시트를 지어내서 열을 덧붙인다.
    목록이 명시된 문장은 추론할 게 없으므로 규칙으로 확정한다.
    """
    if not _HEADER_INTENT_PATTERN.search(text) or _TABLE_CREATE_INTENT_PATTERN.search(text):
        return None

    headers = extract_create_table_slot_hints(text).get("headers") or []
    if len(headers) < 2:
        return None

    return {
        "action": "excel_live.write_range",
        "params": {
            "start_cell": preferred_cell or "__USED_RANGE__",
            "values_2d": [[str(header) for header in headers]],
        },
        "reason": "머리글 목록을 표 첫 행에 기록",
    }


def _build_quick_action_plan(message: str, context_range: str | None) -> list[dict[str, Any]] | None:
    text = str(message or "").strip()
    lowered = text.lower()
    range_match = RANGE_REF_PATTERN.search(text)
    range_ref = str(range_match.group(1)).upper() if range_match else ""
    col_match = COLUMN_LETTER_PATTERN.search(text)
    col_range_ref = f"{str(col_match.group(1)).upper()}:{str(col_match.group(1)).upper()}" if col_match else ""
    normalized_ctx = _normalize_range_text(context_range)
    explicit_range = range_ref or col_range_ref

    header_step = _quick_header_write_step(text, explicit_range or normalized_ctx)
    if header_step is not None:
        return [header_step]

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

    if any(
        token in lowered
        for token in [
            "시트 목록",
            "탭 목록",
            "현재 시트 목록",
            "sheet list",
            "list sheets",
            "worksheet list",
        ]
    ) or _SHEET_INVENTORY_QUESTION.search(lowered):
        return [{"action": "excel_live.list_sheets", "params": {}, "reason": "빠른 규칙 기반 시트 목록 조회"}]

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

    create_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:만들|생성|추가|create|add)",
        text,
        re.IGNORECASE,
    )
    if create_sheet_match:
        sheet_name = str(create_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return [
                {
                    "action": "excel_live.create_sheet",
                    "params": {"sheet_name": sheet_name, "make_active": True},
                    "reason": "빠른 규칙 기반 시트 생성",
                }
            ]

    select_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:로|으로)?\s*(?:이동|전환|선택|활성화|switch|go)",
        text,
        re.IGNORECASE,
    )
    if select_sheet_match:
        sheet_name = str(select_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return [
                {
                    "action": "excel_live.select_sheet",
                    "params": {"sheet_name": sheet_name},
                    "reason": "빠른 규칙 기반 시트 전환",
                }
            ]

    # 시트 간 값 연결: "요약 시트 B2에 원본 시트 E2 값을 연결해줘"
    if any(token in lowered for token in ["연결", "링크", "참조", "link", "가져오", "불러오"]):
        target_match = re.search(
            r"([^\s,]+)\s*(?:시트|sheet)\s*(?:의\s*)?([A-Z]+\d+)\s*(?:셀|칸)?\s*에",
            text,
            re.IGNORECASE,
        )
        sheet_refs = re.findall(
            r"([^\s,]+)\s*(?:시트|sheet)\s*(?:의\s*)?([A-Z]+\d+(?::[A-Z]+\d+)?|[A-Z]+:[A-Z]+)\s*(?:셀|칸)?",
            text,
            re.IGNORECASE,
        )
        if target_match and sheet_refs:
            target_sheet = str(target_match.group(1)).strip().strip("\"'")
            target_cell = str(target_match.group(2)).strip().upper()
            source_sheet = ""
            source_ref = ""
            for raw_sheet, raw_ref in sheet_refs:
                candidate_sheet = str(raw_sheet).strip().strip("\"'")
                candidate_ref = str(raw_ref).strip().upper()
                if not candidate_sheet or not candidate_ref:
                    continue
                if candidate_sheet != target_sheet or candidate_ref != target_cell:
                    source_sheet = candidate_sheet
                    source_ref = candidate_ref
            if not source_sheet or not source_ref:
                first_sheet, first_ref = sheet_refs[0]
                source_sheet = str(first_sheet).strip().strip("\"'")
                source_ref = str(first_ref).strip().upper()
            if target_sheet and source_sheet and target_cell and source_ref:
                source_prefix = _quote_sheet_for_formula(source_sheet)
                if any(token in lowered for token in ["합계", "sum"]):
                    formula = f"=SUM({source_prefix}!{source_ref})"
                elif any(token in lowered for token in ["평균", "average", "avg"]):
                    formula = f"=AVERAGE({source_prefix}!{source_ref})"
                elif any(token in lowered for token in ["개수", "count", "건수"]):
                    formula = f"=COUNT({source_prefix}!{source_ref})"
                else:
                    formula = f"={source_prefix}!{source_ref}"
                return [
                    {
                        "action": "excel_live.select_sheet",
                        "params": {"sheet_name": target_sheet},
                        "reason": "연결 대상 시트 전환",
                    },
                    {
                        "action": "excel_live.set_formula",
                        "params": {"range_ref": target_cell, "formula_a1": formula},
                        "reason": "시트 간 참조 수식 연결",
                    },
                ]

    if any(token in lowered for token in ["비교", "차이", "diff", "다른 값", "바뀐"]):
        range_matches = re.findall(r"([A-Z]+\d+:[A-Z]+\d+)", text, re.IGNORECASE)
        sheet_matches = _extract_sheet_mentions(text)
        if len(range_matches) >= 2 and len(sheet_matches) >= 2:
            output_sheet = "비교결과"
            if len(sheet_matches) >= 3:
                output_sheet = sheet_matches[2]
            return [
                {
                    "action": "excel_live.compare_ranges",
                    "params": {
                        "left_sheet": sheet_matches[0],
                        "left_range": str(range_matches[0]).upper(),
                        "right_sheet": sheet_matches[1],
                        "right_range": str(range_matches[1]).upper(),
                        "output_sheet": output_sheet,
                    },
                    "reason": "빠른 규칙 기반 범위 비교",
                }
            ]

    if any(token in lowered for token in ["예측", "forecast", "추세", "앞으로"]):
        horizon_match = re.search(r"(\d{1,2})\s*(개월|달|월|주)", lowered)
        if explicit_range or normalized_ctx:
            return [
                {
                    "action": "excel_live.forecast_linear",
                    "params": {
                        "source_range": explicit_range or normalized_ctx or "__ACTIVE_SELECTION__",
                        "horizon": int(horizon_match.group(1)) if horizon_match else 3,
                        "output_sheet": _extract_output_sheet_from_text(text),
                        "output_start": "A1",
                    },
                    "reason": "빠른 규칙 기반 추세 예측",
                }
            ]

    # "C2:C8 수식 결과 확인해줘" — 값만 읽는 게 아니라 수식이 제대로 계산됐는지 본다.
    if (explicit_range or normalized_ctx) and re.search(r"(수식|함수|formula)", text, re.IGNORECASE):
        if re.search(r"(결과|확인|검증|검사|점검|verify)", text, re.IGNORECASE) and not re.search(
            r"(넣|입력|적용|작성|만들|써)", text
        ):
            return [
                {
                    "action": "excel_live.verify_formula_result",
                    "params": {"range_ref": explicit_range or normalized_ctx or "__ACTIVE_SELECTION__"},
                    "reason": "빠른 규칙 기반 수식 결과 검증",
                }
            ]

    # "완료,진행중,지연만 선택되도록 드롭다운으로 제한해줘"
    if any(token in lowered for token in ["드롭다운", "dropdown", "목록", "선택되도록", "선택만"]) and (
        explicit_range or normalized_ctx
    ):
        choices = re.search(r"([^\s,]{1,20}(?:\s*,\s*[^\s,]{1,20}){1,9})", text)
        if choices:
            items = [item.strip() for item in choices.group(1).split(",") if item.strip()]
            # "지연만"처럼 조사가 붙은 마지막 항목을 정리한다.
            items = [re.sub(r"(만|을|를|은|는)$", "", item) or item for item in items]
            if len(items) >= 2:
                return [
                    {
                        "action": "excel_live.set_data_validation",
                        "params": {
                            "target_range": explicit_range or normalized_ctx or "__ACTIVE_SELECTION__",
                            "validation_type": "list",
                            "source": ",".join(items),
                            "allow_blank": True,
                            "show_error": True,
                        },
                        "reason": "빠른 규칙 기반 드롭다운 목록 제한",
                    }
                ]

    if any(token in lowered for token in ["입력 제한", "숫자만", "숫자 입력", "유효성", "validation"]) and (explicit_range or normalized_ctx):
        number_range = re.search(
            r"(-?\d+(?:\.\d+)?)\s*(?:부터|~|에서|사이|to|-)\s*(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if number_range:
            min_raw = float(number_range.group(1))
            max_raw = float(number_range.group(2))
            validation_type = "whole" if min_raw.is_integer() and max_raw.is_integer() else "decimal"
            minimum: int | float = int(min_raw) if min_raw.is_integer() else min_raw
            maximum: int | float = int(max_raw) if max_raw.is_integer() else max_raw
            return [
                {
                    "action": "excel_live.set_data_validation",
                    "params": {
                        "target_range": explicit_range or normalized_ctx or "__ACTIVE_SELECTION__",
                        "validation_type": validation_type,
                        "minimum": minimum,
                        "maximum": maximum,
                        "allow_blank": True,
                        "show_error": True,
                    },
                    "reason": "빠른 규칙 기반 숫자 입력 제한",
                }
            ]

    formula_a1 = _extract_formula_from_text(text)
    if formula_a1 and (explicit_range or normalized_ctx):
        return [
            {
                "action": "excel_live.set_formula",
                "params": {
                    "range_ref": explicit_range or normalized_ctx or "__ACTIVE_SELECTION__",
                    "formula_a1": formula_a1,
                },
                "reason": "빠른 규칙 기반 수식 입력",
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

    if _is_color_clear_request(lowered):
        whole_sheet_color_clear = _is_whole_sheet_style_request(
            lowered=lowered,
            normalized_ctx=normalized_ctx,
            explicit_range=explicit_range,
        )
        target = normalized_ctx or explicit_range or ("__USED_RANGE__" if whole_sheet_color_clear else "__ACTIVE_SELECTION__")
        return [
            {
                "action": "excel_live.fill_range",
                "params": {"target_range": target, "fill_color": "#FFFFFF"},
                "reason": "빠른 규칙 기반 배경색 제거",
            }
        ]

    if _looks_like_column_comparison(text) and not _contains_any_keyword(
        lowered, "", ["필터", "걸러", "남겨", "추출", "정렬", "삭제", "지워"]
    ):
        # "현재고가 재주문점 이하인 제품만 표시해줘" — 기준이 다른 열이라 숫자가 없다.
        # 대상 열·비교 열은 머리글을 아는 바인더가 채운다.
        colors = _quick_extract_colors(lowered)
        return [
            {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": "__ACTIVE_SELECTION__",
                    "operator": "<=",
                    "threshold": 0,
                    "fill_color": colors[0] if colors else "#FFFF00",
                },
                "reason": "빠른 규칙 기반 열 간 비교 강조",
            }
        ]

    if _has_border_style_context(lowered):
        whole_sheet_border = _is_whole_sheet_style_request(
            lowered=lowered,
            normalized_ctx=normalized_ctx,
            explicit_range=explicit_range,
        )
        target = normalized_ctx or explicit_range or ("__USED_RANGE__" if whole_sheet_border else "__ACTIVE_SELECTION__")
        border_thin = any(token in lowered for token in ["얇", "thin"])
        border_light = any(token in lowered for token in ["옅", "연한", "회색", "그레이", "gray", "grey"])
        border_reset = any(
            token in lowered
            for token in ["기본값", "기본 상태", "기본", "원래 상태", "원래", "초기 상태", "초기", "없애", "제거", "지워", "reset"]
        )
        border_remove = any(token in lowered for token in ["없애", "제거", "지워", "remove"])
        line_style = "none" if border_remove else "continuous"
        weight = "thin" if (border_reset or border_thin) else "medium"
        color = "#D9D9D9" if (border_reset or border_light) else "#000000"
        reason = (
            "빠른 규칙 기반 경계선 제거"
            if border_remove
            else ("빠른 규칙 기반 경계선 기본값 복구" if border_reset else "빠른 규칙 기반 테두리 적용")
        )
        return [
            {
                "action": "excel_live.apply_border",
                "params": {
                    "target_range": target,
                    "line_style": line_style,
                    "weight": weight,
                    "color": color,
                },
                "reason": reason,
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
            "흰색",
            "하얀색",
            "하양",
            "white",
            "화이트",
            "백색",
        ]
    ):
        whole_sheet_color = _is_whole_sheet_style_request(
            lowered=lowered,
            normalized_ctx=normalized_ctx,
            explicit_range=explicit_range,
        )
        target = normalized_ctx or explicit_range or ("__USED_RANGE__" if whole_sheet_color else "__ACTIVE_SELECTION__")
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

    if _is_clear_reset_request(lowered):
        normalized_ctx = _normalize_range_text(context_range)
        explicit_range = range_ref or col_range_ref
        whole_sheet_reset = _is_whole_sheet_reset_request(
            lowered=lowered,
            normalized_ctx=normalized_ctx,
            explicit_range=explicit_range,
        )
        if not whole_sheet_reset and not explicit_range and _clear_request_targets_a_subset(lowered):
            # 지울 대상이 따로 지목된 문장. 규칙으로 밀면 시트가 통째로 비워진다.
            return None
        target = normalized_ctx or explicit_range or ("__USED_RANGE__" if whole_sheet_reset else "__ACTIVE_SELECTION__")
        return [
            {
                "action": "excel_live.clear_range",
                "params": {"target_range": target},
                "reason": "빠른 규칙 기반 내용 비우기",
            }
        ]

    if any(token in lowered for token in ["저장", "save"]):
        if "pdf" in lowered:
            # "PDF로 저장" 은 통합문서 저장이 아니라 내보내기다.
            return [
                {
                    "action": "excel_live.export_pdf",
                    "params": {},
                    "reason": "빠른 규칙 기반 PDF 내보내기",
                }
            ]
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
    if _clear_request_targets_a_subset(lowered):
        # "취소된 건은 지워줘" — 지울 대상을 지목한 문장은 그 자체로 엑셀 편집 요청이다.
        return True
    return _mentions_spreadsheet_structure(lowered)


# 명사 목록은 끝없이 늘어난다. "Discount 열은 지워줘"는 어느 낱말에도 안 걸려 400으로 떨어졌다.
# 표의 구조를 가리키는 말 + 편집 동사 조합이면 엑셀 요청으로 본다.
_STRUCTURE_WORD = re.compile(r"(열|칼럼|컬럼|column|행|줄|row|셀|cell|범위|머리글|헤더|header)")
_EDIT_VERB = re.compile(
    r"(지워|지우|삭제|제거|없애|추가|넣어|입력|바꿔|변경|고쳐|수정|만들어|생성|칠해|채워|막아|잠가|빼)"
)


def _mentions_spreadsheet_structure(lowered: str) -> bool:
    text = str(lowered or "")
    return bool(_STRUCTURE_WORD.search(text) and _EDIT_VERB.search(text))


def _first_action_from_parsed(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return ""
    action_plan = parsed.get("action_plan")
    if isinstance(action_plan, list) and action_plan:
        first = action_plan[0]
        if isinstance(first, dict):
            return str(first.get("action", "")).strip()
    action = parsed.get("action")
    if isinstance(action, str):
        return str(action).strip()
    return ""


def _is_explicit_list_workbooks_intent(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(
        token in lowered
        for token in [
            "열린 통합문서",
            "워크북 목록",
            "열린 파일 목록",
            "list workbooks",
            "workbook list",
        ]
    )


def _is_likely_edit_request(message: str, operation_hints: dict[str, Any]) -> bool:
    lowered = str(message or "").lower()
    hint_intent = str(operation_hints.get("intent") or "").strip()
    if hint_intent in _EDIT_EXPECTED_OPERATION_INTENTS:
        if hint_intent == "formula":
            # "수식 결과 확인" 류는 조회 성격이므로 편집 강제 대상에서 제외한다.
            if any(token in lowered for token in ["결과 확인", "검증", "맞는지", "검산"]):
                return False
        return True
    if _is_color_format_request(lowered):
        return True
    if _looks_like_column_structure_edit(lowered):
        return True
    return any(
        token in lowered
        for token in [
            "적용",
            "생성",
            "만들어",
            "채워",
            "지워",
            "정렬",
            "필터",
            "중복",
            "피벗",
            "차트",
            "수식",
            "계산",
            "저장",
        ]
    )


def _score_command_complexity(
    *,
    message: str,
    operation_hints: dict[str, Any],
    hints: dict[str, Any],
    quick_plan: list[PlanStep],
) -> int:
    text = str(message or "")
    lowered = text.lower()
    score = 0

    if len(text) >= 40:
        score += 1
    if len(text) >= 80:
        score += 1
    if "\n" in text or "\t" in text:
        score += 2
    if re.search(r"[A-Z]+\d+:[A-Z]+\d+", text, re.IGNORECASE):
        score += 1
    if any(token in lowered for token in _COMPLEX_REASONING_KEYWORDS):
        score += 2

    op_intent = str(operation_hints.get("intent") or "").strip()
    if op_intent in _DEEP_REASONING_OPERATION_INTENTS:
        score += 2
    elif op_intent in {"safety", "debug", "performance", "explain"}:
        score += 1

    if hints.get("table_intent") and hints.get("values_2d"):
        score += 2
    if len(quick_plan) > 1:
        score += 1
    return score


def _select_reasoning_mode(*, should_parse_with_llm: bool, complexity_score: int) -> str:
    if not should_parse_with_llm:
        return "rule"
    if complexity_score >= 3:
        return "deep"
    return "fast"


def _parse_budget_for_reasoning_mode(reasoning_mode: str) -> tuple[float, int, float]:
    if reasoning_mode == "deep":
        return (
            _COMMAND_DEEP_PARSE_TIMEOUT_SECONDS,
            _COMMAND_DEEP_PARSE_MAX_ATTEMPTS,
            _COMMAND_DEEP_PARSE_RETRY_BACKOFF_SECONDS,
        )
    return (
        _COMMAND_PARSE_TIMEOUT_SECONDS,
        _COMMAND_PARSE_MAX_ATTEMPTS,
        _COMMAND_PARSE_RETRY_BACKOFF_SECONDS,
    )


def _should_run_reflection_before_execute(
    *,
    parsed: dict[str, Any] | None,
    message: str,
    operation_hints: dict[str, Any],
    reasoning_mode: str,
) -> tuple[bool, str]:
    first_action = _first_action_from_parsed(parsed)
    if not first_action:
        return False, ""

    reasons: list[str] = []
    if str((parsed or {}).get("intent", "")).strip().lower() in {"", "unknown"} and _looks_like_excel_request(message):
        reasons.append("intent_unknown")
    if first_action == "excel_live.list_workbooks" and not _is_explicit_list_workbooks_intent(message):
        reasons.append("list_misclassify")

    op_hint_intent = str(operation_hints.get("intent") or "").strip()
    action_intent = _action_to_operation_intent(first_action)
    if op_hint_intent and op_hint_intent not in {"general", "safety", "debug", "performance", "explain"}:
        if action_intent and action_intent != op_hint_intent:
            reasons.append(f"intent_mismatch:{op_hint_intent}->{action_intent}")
        elif op_hint_intent in _EDIT_EXPECTED_OPERATION_INTENTS and first_action in {
            "excel_live.read_range",
            "excel_live.list_workbooks",
        }:
            reasons.append("passive_action_for_edit_intent")

    if _is_likely_edit_request(message, operation_hints) and first_action in {
        "excel_live.read_range",
        "excel_live.list_workbooks",
    }:
        reasons.append("passive_action_for_edit_request")

    if not reasons:
        return False, ""

    reason = ",".join(reasons[:2])
    if reasoning_mode == "deep":
        return True, reason
    # fast 모드는 오분류가 명확한 경우에만 1회 reflection을 허용한다.
    if any(item in {"intent_unknown", "list_misclassify"} for item in reasons):
        return True, reason
    return False, ""


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


def _ordered_column_letters(text: str) -> list[str]:
    """ "수량은 C열, 단가는 D열"처럼 문장에 등장한 열 문자를 등장 순서대로 모은다."""
    seen: list[str] = []
    for match in COLUMN_LETTER_PATTERN.finditer(str(text or "")):
        letter = str(match.group(1)).upper()
        if letter not in seen:
            seen.append(letter)
    return seen


_NAMED_FORMULA_OPERATION = re.compile(
    r"(나누기|나눈|곱하기|곱한|더하기|더한|빼기|뺀|빼서|차감|두\s*배|2배|절반)"
)
_NAMED_FORMULA_TARGET = re.compile(r"[가-힣A-Za-z0-9_]{2,14}\s*(?:열|칼럼|컬럼|필드)\s*(?:에|에다)")


def _looks_like_named_formula(text: str) -> bool:
    """ "이익률 열에 매출이익 나누기 매출" 처럼 열 이름으로만 말한 계산식인지 본다.

    열 문자(B열·C열)가 이미 나온 문장은 기존 규칙이 더 정확하므로 넘긴다.
    """
    if not _NAMED_FORMULA_TARGET.search(text) or not _NAMED_FORMULA_OPERATION.search(text):
        return False
    return len(_ordered_column_letters(text)) < 2


def _extract_formula_common_params(text: str) -> dict[str, Any]:
    lowered = text.lower()
    params: dict[str, Any] = {}
    letters = _ordered_column_letters(text)

    if _looks_like_named_formula(text):
        params["formula_mode"] = "named"
        params["named_formula_message"] = text
        return params

    if any(token in lowered for token in ["곱해서", "곱한", "곱해", "수량", "단가", "가격"]):
        params["formula_mode"] = "multiply"
        qty_col = _extract_column_for_keyword(text, "수량")
        price_col = _extract_column_for_keyword(text, "단가|가격")
        # 항목명과 짝지어지지 않았으면 "C열, D열, E열" 등장 순서를 그대로 쓴다.
        if not qty_col and len(letters) >= 2:
            qty_col = letters[0]
        if not price_col and len(letters) >= 2:
            price_col = letters[1]
        if qty_col:
            params["qty_column"] = qty_col
        if price_col:
            params["price_column"] = price_col
        if "qty_column" in params and "price_column" in params:
            result_col = _extract_column_for_keyword(text, "결과|결괏값|답")
            if not result_col and len(letters) >= 3:
                result_col = letters[2]
            params["result_column"] = result_col or _next_column(str(params["price_column"]))

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
        else:
            # "참조표는 조회표 시트 A:B" — 사람들이 실제로 쓰는 표기.
            sheet_cols = re.search(
                r"([가-힣A-Za-z0-9_]{1,20})\s*(?:시트|sheet)\s*([A-Z]{1,3})\s*:\s*([A-Z]{1,3})",
                text,
                re.IGNORECASE,
            )
            plain_cols = re.search(r"(?<![A-Za-z0-9])([A-Z])\s*:\s*([A-Z])(?![A-Za-z0-9])", text)
            if sheet_cols:
                params["table_sheet"] = sheet_cols.group(1)
                params["table_start_column"] = str(sheet_cols.group(2)).upper()
                params["table_end_column"] = str(sheet_cols.group(3)).upper()
            elif plain_cols:
                params["table_start_column"] = str(plain_cols.group(1)).upper()
                params["table_end_column"] = str(plain_cols.group(2)).upper()
        idx_match = re.search(r"반환[^\d]{0,8}(\d+)\s*열", text) or re.search(r"(\d+)\s*열\s*반환", text)
        if idx_match:
            params["return_index"] = int(idx_match.group(1))
        result_col = _extract_column_for_keyword(text, "결과|결괏값|답")
        if result_col:
            params["result_column"] = result_col

    if any(
        token in lowered
        for token in ["조건식", "if(", "조건에 따라", "이면", "미만이면", "이상이면", "등급", "합격", "불합격"]
    ):
        params["formula_mode"] = "if_compare"
        compare_col = _extract_column_for_keyword(text, "점수|실적|값|금액")
        # "C열이 70 미만이면"처럼 항목명 없이 열만 말하는 경우가 훨씬 흔하다.
        if not compare_col and letters:
            compare_col = letters[0]
        if compare_col:
            params["compare_column"] = compare_col
            result_col = _extract_column_for_keyword(text, "결과|결괏값|판정")
            if not result_col and len(letters) >= 2:
                result_col = letters[1]
            params["result_column"] = result_col or _next_column(compare_col)
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


# "금액 열 기준"처럼 '열' 앞에 붙는 머리글 이름을 뽑는다.
_COLUMN_LABEL_PATTERN = re.compile(r"([A-Za-z가-힣0-9_]{1,20}?)\s*열(?!쇠)")
_COLUMN_LABEL_PARTICLES = ("으로", "에서", "을", "를", "은", "는", "이", "가", "의", "로")
_COLUMN_KEYWORD_CANDIDATES = (
    "매출",
    "금액",
    "가격",
    "단가",
    "수량",
    "점수",
    "비용",
    "날짜",
    "이름",
    "상태",
    "코드",
    "전화번호",
    "이메일",
)
_SHEET_SUFFIX_PATTERN = re.compile(r"\s*(?:시트|sheet)", re.IGNORECASE)


def _extract_column_label_from_text(text: str) -> str | None:
    """정렬/중복 제거 기준 열 이름을 문장에서 추출한다.

    고정 토큰 목록을 순서대로 훑으면 시트명("매출 시트")이 실제 기준 열("금액")을
    이겨버리므로, 반드시 '열'이라는 단서 앞에 오는 단어만 후보로 삼는다.
    """
    raw = str(text or "")
    for match in _COLUMN_LABEL_PATTERN.finditer(raw):
        label = str(match.group(1) or "").strip()
        for particle in _COLUMN_LABEL_PARTICLES:
            if len(label) > len(particle) and label.endswith(particle):
                label = label[: -len(particle)]
                break
        if not label or label.endswith("시트"):
            continue
        if len(label) == 1 and label.isascii() and label.isalpha():
            return label.upper()
        return label

    # "금액 순서대로 재배치해줘"처럼 '열' 단서가 없는 문장을 위한 폴백.
    # 시트명으로 쓰인 언급("매출 시트")은 후보에서 제외해야 기준 열을 가로채지 않는다.
    best: tuple[int, str] | None = None
    for token in _COLUMN_KEYWORD_CANDIDATES:
        for match in re.finditer(re.escape(token), raw):
            if _SHEET_SUFFIX_PATTERN.match(raw[match.end() : match.end() + 6]):
                continue
            if best is None or match.start() < best[0]:
                best = (match.start(), token)
            break
    return best[1] if best else None


def _extract_operation_hints(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    lowered = text.lower()
    hints: dict[str, Any] = {
        "intent": _detect_operation_intent(text),
        "affirmative": any(token in lowered for token in ["응", "네", "좋아", "그래", "맞아", "yes", "ok"]),
        "params": {},
    }
    hints["params"]["raw_message"] = text

    range_match = RANGE_REF_PATTERN.search(text)
    if range_match:
        hints["params"]["target_range"] = str(range_match.group(1)).upper()
        hints["params"]["source_range"] = str(range_match.group(1)).upper()
    range_matches = re.findall(r"([A-Za-z]{1,3}\d{1,7}:[A-Za-z]{1,3}\d{1,7})", text)
    if len(range_matches) >= 2:
        hints["params"]["left_range"] = str(range_matches[0]).upper()
        hints["params"]["right_range"] = str(range_matches[1]).upper()

    if "formula" == hints["intent"]:
        hints["params"].update(_extract_formula_common_params(text))

    if "sort" == hints["intent"]:
        if any(token in lowered for token in ["높은", "내림", "내림차순", "큰 순", "많은 순"]):
            hints["params"]["order"] = "desc"
        elif any(token in lowered for token in ["낮은", "오름", "오름차순", "작은 순", "적은 순"]):
            hints["params"]["order"] = "asc"
        elif re.search(r"(큰|높은|많은)\s*(값|순|순서)", lowered):
            hints["params"]["order"] = "desc"
        elif re.search(r"(작은|낮은|적은)\s*(값|순|순서)", lowered):
            hints["params"]["order"] = "asc"
        top_match = re.search(r"상위\s*(\d{1,3})", lowered)
        if top_match:
            hints["params"]["top_n"] = int(top_match.group(1))
        key_column = _extract_column_label_from_text(text)
        if key_column:
            hints["params"]["key_column"] = key_column

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
        col_threshold_match = re.search(
            r"([A-Z])\s*열[^\n]{0,20}?(\d+(?:\.\d+)?)\s*(이상|초과|이하|미만)",
            text,
            re.IGNORECASE,
        )
        if col_threshold_match:
            op_map = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<"}
            hints["params"]["column"] = str(col_threshold_match.group(1)).upper()
            hints["params"]["operator"] = op_map.get(col_threshold_match.group(3), ">=")
            hints["params"]["value"] = float(col_threshold_match.group(2))
        score_match = re.search(r"(\d+(?:\.\d+)?)\s*점?\s*(이상|초과|이하|미만)", lowered)
        if score_match:
            op_map = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<"}
            hints["params"]["column"] = hints["params"].get("column", "점수")
            hints["params"]["operator"] = op_map.get(score_match.group(2), ">=")
            hints["params"]["value"] = float(score_match.group(1))

    if hints["intent"] in {"dedupe", "dupescan"}:
        dedupe_column = _extract_column_label_from_text(text)
        if dedupe_column:
            hints["params"]["key_columns"] = [dedupe_column]
        elif "주문번호" in lowered or "주문 번호" in lowered:
            hints["params"]["key_columns"] = ["주문번호"]
        elif "전화번호" in lowered:
            hints["params"]["key_columns"] = ["전화번호"]
        elif "이메일" in lowered:
            hints["params"]["key_columns"] = ["이메일"]
        elif "이름" in lowered:
            hints["params"]["key_columns"] = ["이름"]

    if "pivot" == hints["intent"]:
        pivot_output_sheet = _extract_output_sheet_from_text(text)
        if pivot_output_sheet:
            hints["params"]["output_sheet"] = pivot_output_sheet
        # "월을 행으로, 카테고리를 열로, 금액 합계" — 사람들이 피벗을 설명하는 기본 어순.
        row_field = re.search(r"([가-힣A-Za-z0-9_]{1,12})\s*(?:을|를|은|는)?\s*행\s*(?:으로|에|기준)", text)
        if row_field:
            hints["params"]["row_field"] = row_field.group(1)
        col_field = re.search(r"([가-힣A-Za-z0-9_]{1,12})\s*(?:을|를|은|는)?\s*열\s*(?:로|으로|에|기준)", text)
        if col_field:
            hints["params"]["column_field"] = col_field.group(1)
        value_field = re.search(r"([가-힣A-Za-z0-9_]{1,12})\s*(합계|평균|개수|최대|최소)", text)
        if value_field:
            hints["params"]["value_field"] = value_field.group(1)
            hints["params"]["agg"] = {
                "합계": "sum",
                "평균": "avg",
                "개수": "count",
                "최대": "max",
                "최소": "min",
            }[value_field.group(2)]
        if not hints["params"].get("row_field"):
            # "월을 행으로"보다 "지역별"이 훨씬 흔한 말투다. 여기서 못 잡으면 되묻기로 떨어진다.
            for marker in _GROUP_MARKER.finditer(lowered):
                term = marker.group(1).strip()
                if term and term not in _GROUP_MARKER_STOPWORDS:
                    hints["params"]["row_field"] = "월" if term == "월" else term
                    break
        if "상품" in lowered and not hints["params"].get("column_field"):
            hints["params"]["column_field"] = "상품명"
        if not hints["params"].get("value_field"):
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
            if any(token in lowered for token in ["잠궈", "잠궈줘", "건들지", "편집 막", "수정 금지"]):
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
        # "1분기,2분기,3분기 시트를 통합1 시트로 합쳐줘"
        listed = re.search(
            r"((?:[가-힣A-Za-z0-9_]{1,20}\s*,\s*)+[가-힣A-Za-z0-9_]{1,20})\s*(?:시트|sheet)",
            text,
            re.IGNORECASE,
        )
        if listed:
            sources = [part.strip() for part in listed.group(1).split(",") if part.strip()]
            if len(sources) >= 2:
                hints["params"]["source_sheets"] = sources
        target = re.search(
            r"([가-힣A-Za-z0-9_]{1,20})\s*(?:시트|sheet)\s*(?:으로|로|에)", text, re.IGNORECASE
        )
        if target:
            hints["params"]["output_sheet"] = target.group(1)

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
        if any(token in lowered for token in ["필터 함수", "필터함수"]):
            hints["params"]["issue"] = "filter_function_error"
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


_ROW_FIELD_PATTERNS = (
    re.compile(r"([가-힣A-Za-z0-9_]{1,12})\s*별"),
    re.compile(r"([가-힣A-Za-z0-9_]{1,12})\s*(?:을|를|은|는)?\s*행\s*(?:으로|에|기준)"),
)
_AGG_WORDS = {"합계": "sum", "총합": "sum", "평균": "avg", "개수": "count", "건수": "count"}


# 번호·코드 열은 더해 봐야 뜻이 없다. 첫 숫자 열을 그냥 집으면 주문번호 합계가 나온다.
_NON_MEASURE_HEADER = re.compile(r"(id|no|번호|코드|code|순번|index)$", re.IGNORECASE)


def _pick_pivot_value_field(text: str, columns: list[dict[str, Any]], row_field: str) -> str:
    """더할 열을 고른다. 문장이 지목한 열이 우선이고, 없으면 첫 측정값 열."""
    numeric = {
        str(col.get("header")): col
        for col in columns
        if col.get("numeric") and str(col.get("header") or "") != row_field
    }
    if not numeric:
        return ""
    for mention in find_header_mentions(text, list(numeric)):
        header = str(mention.get("header") or "")
        if header in numeric:
            return header
    for header in numeric:
        if not _NON_MEASURE_HEADER.search(header):
            return header
    return next(iter(numeric))


def _pivot_step_from_message(message: str, digest: dict[str, Any] | None, *, sheet_name: str | None):
    """ "담당자별 합계를 피벗으로" 같은 요청에서 피벗 단계를 직접 구성한다.

    작은 모델은 "필터하고 피벗하고 차트까지"를 한 번에 계획하지 못하고 첫 단계만 내놓을 때가 많다.
    뒷단이 조용히 사라지는 대신, 기준 열을 확정할 수 있을 때만 규칙으로 채운다.
    """
    text = str(message or "")
    term = ""
    for pattern in _ROW_FIELD_PATTERNS:
        found = pattern.search(text)
        if found:
            term = found.group(1)
            break
    if not term:
        return None

    entry = sheet_entry(digest or {}, sheet_name)
    columns = list(entry.get("columns") or []) if entry else []
    headers = [str(col.get("header") or "") for col in columns]
    # 사용자는 "지역별"이라 말하고 시트 머리글은 Region이다. 글자 그대로 비교하면
    # 한국어 명령은 전부 되묻기로 떨어진다.
    row_field = resolve_header(term, headers) if headers else term
    if not row_field:
        return None

    value_field = _pick_pivot_value_field(text, columns, row_field)
    if not value_field:
        return None

    agg = next((code for word, code in _AGG_WORDS.items() if word in text), "sum")
    return {
        "action": "excel_live.pivot_table",
        "params": {
            "source_range": "__ACTIVE_SELECTION__",
            "row_field": row_field,
            "value_field": value_field,
            "agg": agg,
            "output_sheet": _extract_output_sheet_from_text(text) or "피벗1",
        },
        "reason": "원문이 요청한 피벗 단계 보완",
    }


def _infer_formula_mode_from_digest(
    message: str,
    digest: dict[str, Any] | None,
    *,
    sheet_name: str | None,
) -> dict[str, Any]:
    """머리글만 봐도 뻔한 계산은 되묻지 않고 확정한다.

    "금액 계산해줘"라고 했는데 시트에 수량·단가 열이 있으면 사람이 뜻한 건 수량*단가다.
    여기서 되물으면 사용자는 이미 시트에 써둔 걸 말로 또 설명해야 한다.
    """
    if not digest:
        return {}
    entry = sheet_entry(digest, sheet_name)
    if not entry:
        return {}
    letters = {
        str(col.get("header") or "").strip(): str(col.get("letter") or "").strip()
        for col in entry.get("columns", [])
        if col.get("header") and col.get("letter")
    }
    text = str(message or "")
    wants_amount = bool(re.search(r"(금액|총액|합계|매출액|계산)", text))
    qty = next((letters[h] for h in ("수량", "개수", "판매량") if h in letters), "")
    price = next((letters[h] for h in ("단가", "가격", "판매가") if h in letters), "")
    if not (wants_amount and qty and price):
        return {}
    result = next((letters[h] for h in ("금액", "총액", "합계") if h in letters), "") or _next_column(price)
    return {
        "formula_mode": "multiply",
        "qty_column": qty,
        "price_column": price,
        "result_column": result,
    }


def _confident_group_key(message: str, digest: dict[str, Any] | None, *, sheet_name: str | None) -> str:
    """집계 기준 열을 시트 머리글에서 확정할 수 있으면 그 열 이름을 준다.

    "-별 + 집계어"만으로는 부족하다. 실제 시트에 그 열이 있어야 규칙만으로 끝까지 실행할 수
    있고, 그때만 플래너보다 규칙을 앞세울 자격이 생긴다.
    """
    lowered, compact = _normalized_message_views(message)
    if not _looks_like_group_aggregate(lowered, compact):
        return ""
    entry = sheet_entry(digest or {}, sheet_name)
    headers = [str(col.get("header") or "") for col in (entry.get("columns") or [])] if entry else []
    if not headers:
        return ""
    for marker in _GROUP_MARKER.finditer(lowered):
        term = marker.group(1).strip()
        if term in _GROUP_MARKER_STOPWORDS:
            continue
        resolved = resolve_header(term, headers)
        if resolved:
            return resolved
    return ""


def _merge_operation_slots(
    current: PendingExcelOperationSlots | None,
    *,
    session_key: str,
    req: ExcelLiveCommandRequest,
    hints: dict[str, Any],
    parsed: dict[str, Any] | None,
    digest: dict[str, Any] | None = None,
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
        # 다만 "코드 기준으로 단가 찾아와"를 단순 조회로 떨어뜨리는 것처럼
        # 편집 요청이 읽기로 격하되는 경우는 규칙이 짚은 의도를 따른다.
        planner_downgraded_to_read = first_action in {
            "excel_live.read_range",
            "excel_live.list_sheets",
        } and hint_intent not in {"", "general", "read"}
        # 규칙이 기준 열까지 확정한 집계 요청은 플래너에게 맡기지 않는다.
        # 같은 문장이 실행할 때마다 피벗/시트생성/셀칠하기로 갈리던 원인이 여기였다.
        confident_pivot = bool(_confident_group_key(req.message, digest, sheet_name=req.sheet_name))
        if first_action and not planner_downgraded_to_read and not confident_pivot:
            return None
        if confident_pivot:
            # 플래너가 같은 피벗을 말하더라도 기준 열까지 맡기지는 않는다.
            # 액션은 맞고 row_field만 주문번호로 잡히면 180행짜리 "집계"가 나온다.
            intent = "pivot"
        else:
            # LLM이 액션 계획을 냈다면 우선 신뢰하고, 없을 때만 룰 힌트로 폴백한다.
            intent = hint_intent if planner_downgraded_to_read else (parsed_intent or hint_intent)
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
        if not str(slot.params.get("formula_mode") or "").strip():
            slot.params.update(_infer_formula_mode_from_digest(req.message, digest, sheet_name=slot.sheet_name))

    if parsed and isinstance(parsed.get("action_plan"), list) and parsed["action_plan"]:
        first = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        if _action_to_operation_intent(first.get("action")) == slot.intent and isinstance(first.get("params"), dict):
            slot.params.update(first["params"])
    if slot.intent == "pivot":
        # 위에서 플래너 파라미터를 덮어썼더라도, 원문이 말한 기준 열은 되돌려 놓는다.
        confident_key = _confident_group_key(req.message, digest, sheet_name=slot.sheet_name)
        if confident_key:
            slot.params["row_field"] = confident_key
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
        if mode == "named":
            # 결과 열과 피연산자를 이름으로 다 말했다. 열 문자는 바인더가 찾는다.
            return ""
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
        # "PDF로 저장해줘"는 그 자체로 완결된 지시다. 인쇄 설정까지 캐물으면 일이 진행되지 않는다.
        msg = str(slot.params.get("raw_message") or "").lower()
        if "pdf" in msg or "저장" in msg or "내보내" in msg:
            return ""
        return "인쇄 기준을 알려주세요. 예: A4 가로/세로, 한 페이지 맞춤 여부, PDF 저장 여부."
    if intent in {"dupescan", "recalc"}:
        # 전체 범위를 훑는 점검·갱신이라 추가 정보 없이도 실행할 수 있다.
        return ""
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


def _slot_column(params: dict[str, Any], key: str, default: str) -> str:
    """슬롯에 열 값이 None으로 들어 있어도 "NONE" 같은 가짜 열을 만들지 않게 한다."""
    value = str(params.get(key) or "").strip().upper()
    return value or default


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
        if mode == "named":
            # 열 문자는 아직 모른다. 머리글을 아는 바인더가 range_ref/formula_a1을 채운다.
            return [
                {
                    "action": "excel_live.set_formula",
                    "params": {
                        "named_formula_message": p.get("named_formula_message") or "",
                        "formula_mode": "named",
                        "expect_numeric": True,
                    },
                    "reason": "열 이름으로 표현된 계산식 적용",
                }
            ]
        target_range = p.get("target_range", "__ACTIVE_SELECTION__")
        start_row, end_row = _parse_row_bounds_from_range(str(target_range))
        if mode == "multiply":
            qty = _slot_column(p, "qty_column", "B")
            price = _slot_column(p, "price_column", "C")
            result_col = _slot_column(p, "result_column", _next_column(price))
            formula = f"={qty}{start_row}*{price}{start_row}"
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = True
        elif mode == "tax":
            base = _slot_column(p, "base_column", "C")
            result_col = _slot_column(p, "result_column", _next_column(base))
            tax_rate = float(p.get("tax_rate", 0.1))
            formula = f"={base}{start_row}*(1+{tax_rate})"
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = True
        elif mode == "countif":
            count_col = _slot_column(p, "count_column", "B")
            cond = str(p.get("count_condition") or "완료").replace('"', "").strip() or "완료"
            result_col = _slot_column(p, "result_column", _next_column(count_col))
            formula = f'=COUNTIF(${count_col}${start_row}:${count_col}${end_row},"{cond}")'
            result_range = f"{result_col}{start_row}"
            expect_numeric = True
        elif mode == "vlookup":
            lookup_col = _slot_column(p, "lookup_column", "A")
            table_start = _slot_column(p, "table_start_column", "F")
            table_end = _slot_column(p, "table_end_column", "H")
            return_index = int(p.get("return_index") or 2)
            result_col = _slot_column(p, "result_column", _next_column(table_end))
            # 참조표가 다른 시트에 있으면 시트명을 붙여야 #REF!가 나지 않는다.
            table_sheet = str(p.get("table_sheet") or "").strip()
            sheet_prefix = f"'{table_sheet}'!" if table_sheet else ""
            formula = (
                f"=VLOOKUP({lookup_col}{start_row},"
                f"{sheet_prefix}${table_start}${start_row}:${table_end}${end_row},{return_index},FALSE)"
            )
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = False
        elif mode == "if_compare":
            compare_col = _slot_column(p, "compare_column", "C")
            compare_op = str(p.get("compare_op") or "<").strip()
            threshold = float(p.get("threshold") or 70)
            true_value = str(p.get("true_value") or "미달").replace('"', "").strip() or "미달"
            false_value = str(p.get("false_value") or "통과").replace('"', "").strip() or "통과"
            result_col = _slot_column(p, "result_column", _next_column(compare_col))
            formula = (
                f'=IF({compare_col}{start_row}{compare_op}{threshold},"{true_value}","{false_value}")'
            )
            result_range = f"{result_col}{start_row}:{result_col}{end_row}"
            expect_numeric = False
        else:
            target = _slot_column(p, "target_column", "C")
            actual = _slot_column(p, "actual_column", "D")
            result_col = _slot_column(p, "result_column", _next_column(actual))
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
    if intent == "dupescan":
        return [
            {
                "action": "excel_live.find_duplicates",
                "params": {
                    "target_range": p.get("target_range", "__ACTIVE_SELECTION__"),
                    "key_columns": p.get("key_columns", []),
                    "has_header": bool(p.get("has_header", True)),
                    "output_sheet": p.get("output_sheet"),
                },
                "reason": "중복 점검 실행",
            }
        ]
    if intent == "print":
        return [
            {
                "action": "excel_live.export_pdf",
                "params": {"output_path": p.get("output_path")},
                "reason": "PDF 내보내기 실행",
            }
        ]
    if intent == "recalc":
        return [
            {
                "action": "excel_live.recalculate",
                "params": {},
                "reason": "수식 재계산 표시",
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
        values_2d = payload.get("values_2d")
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
        if isinstance(values_2d, list):
            normalized_rows: list[list[Any]] = []
            inferred_cols = 0
            for raw_row in values_2d[:100]:
                row_cells: list[Any]
                if isinstance(raw_row, list):
                    row_cells = [str(v).strip() if v is not None else "" for v in raw_row]
                else:
                    row_cells = [str(raw_row).strip()]
                if not any(str(v).strip() for v in row_cells):
                    continue
                normalized_rows.append(row_cells)
                inferred_cols = max(inferred_cols, len(row_cells))
            if normalized_rows and inferred_cols > 0:
                inferred_cols = max(1, min(50, inferred_cols))
                padded_rows: list[list[Any]] = []
                for row in normalized_rows:
                    trimmed = row[:inferred_cols]
                    if len(trimmed) < inferred_cols:
                        trimmed = [*trimmed, *([""] * (inferred_cols - len(trimmed)))]
                    padded_rows.append(trimmed)
                slot.values_2d = padded_rows
                if allow_inferred_shape:
                    current_rows = int(slot.rows or 0)
                    current_cols = int(slot.cols or 0)
                    slot.rows = max(1, min(100, max(current_rows, len(padded_rows))))
                    slot.cols = max(1, min(50, max(current_cols, inferred_cols)))
                if not slot.headers and padded_rows:
                    normalized_header = [str(v).strip() for v in padded_rows[0] if str(v).strip()]
                    if normalized_header:
                        slot.headers = normalized_header
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
    # 헤더를 받았으면 열 수는 물어볼 필요가 없다. 머리글 개수가 곧 열 수다.
    if slot.cols is None and slot.headers:
        slot.cols = max(1, min(50, len(slot.headers)))
    slot.updated_at_ts = now
    return slot


# 같은 질문을 이 횟수만큼 하고도 크기를 못 받으면 기본값으로 만든다.
_MAX_TABLE_FOLLOW_UPS = 2


def _build_table_follow_up(slot: PendingCreateTableSlots, *, last_call: bool = False) -> str:
    if slot.rows is None and slot.cols is None and slot.template_follow_up_question:
        return slot.template_follow_up_question
    tail = " (다음 답변에도 크기가 없으면 기본값으로 만들게요)" if last_call else ""
    if slot.rows is None and slot.cols is None:
        return (
            "표 크기와 헤더를 알려주세요. 예: 5*5 또는 4행 3열, 금액, 장소, 날짜, 요건, 비고 "
            "(기준 셀 미지정 시 A1에서 생성)" + tail
        )
    if slot.rows is None:
        return f"열은 {slot.cols}개로 잡았습니다. 행은 몇 개로 할까요? 예: 10행{tail}"
    if slot.cols is None:
        return f"행은 {slot.rows}개로 잡았습니다. 열은 몇 개로 할까요? 예: 5열{tail}"
    if not slot.headers:
        return f"{slot.rows}*{slot.cols} 표로 생성할게요. 헤더를 넣을까요? 예: 금액, 장소, 날짜, 요건, 비고"
    return "표 생성 정보를 확인했습니다. 생성을 진행합니다."


def _apply_table_size_fallback(slot: PendingCreateTableSlots) -> str:
    """크기를 끝내 못 알아들었을 때 기본값으로 채운다. 같은 질문을 무한히 반복하지 않기 위함이다."""
    if slot.cols is None:
        slot.cols = len(slot.headers) if slot.headers else 5
    if slot.rows is None:
        slot.rows = 10 if slot.headers else 5
    return (
        f"표 크기를 확정하지 못해 {slot.rows}행 {slot.cols}열, "
        f"{slot.start_cell or 'A1'} 기준으로 만들었습니다. 다르면 크기를 알려주세요."
    )


def _build_create_table_steps(slot: PendingCreateTableSlots) -> list[dict[str, Any]]:
    tabular_values: list[list[Any]] = []
    if isinstance(slot.values_2d, list):
        for raw_row in slot.values_2d[:100]:
            if isinstance(raw_row, list):
                row_cells = list(raw_row)
            else:
                row_cells = [raw_row]
            if any(str(v).strip() for v in row_cells):
                tabular_values.append(row_cells)

    inferred_rows = len(tabular_values)
    inferred_cols = max((len(row) for row in tabular_values), default=0)
    rows = max(1, min(100, max(int(slot.rows or 5), inferred_rows)))
    cols = max(1, min(50, max(int(slot.cols or 5), inferred_cols)))
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

    if tabular_values:
        normalized_values: list[list[Any]] = []
        for row in tabular_values[:rows]:
            row_values = list(row[:cols])
            if len(row_values) < cols:
                row_values.extend([""] * (cols - len(row_values)))
            normalized_values.append(row_values)
        if normalized_values:
            steps.append(
                {
                    "action": "excel_live.write_range",
                    "params": {"start_cell": start_cell, "values_2d": normalized_values},
                    "reason": "표 데이터 입력",
                }
            )
            return steps

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

    if action == "excel_live.list_sheets":
        resolved_wb = _resolve_workbook_id(service, workbook_id or str(params.get("workbook_id", "")).strip() or None)
        return service.list_sheets(resolved_wb)

    if action == "excel_live.select_sheet":
        resolved_wb = _resolve_workbook_id(service, workbook_id or str(params.get("workbook_id", "")).strip() or None)
        target_sheet = str(params.get("sheet_name") or params.get("name") or "").strip()
        if not target_sheet:
            raise WorksheetNotFoundError("select_sheet에는 sheet_name이 필요합니다.")
        return service.select_sheet(
            workbook_id=resolved_wb,
            sheet_name=target_sheet,
        )

    if action == "excel_live.create_sheet":
        resolved_wb = _resolve_workbook_id(service, workbook_id or str(params.get("workbook_id", "")).strip() or None)
        target_sheet = str(params.get("sheet_name") or params.get("name") or "").strip()
        if not target_sheet:
            raise WorksheetNotFoundError("create_sheet에는 sheet_name이 필요합니다.")
        return service.create_sheet(
            workbook_id=resolved_wb,
            sheet_name=target_sheet,
            make_active=bool(params.get("make_active", True)),
        )

    if action == "excel_live.read_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == "__ACTIVE_SELECTION__":
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.read_range(resolved_wb, resolved_sheet, range_ref)

    if action == "excel_live.write_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        # 계획이 시트를 지목했으면 그쪽이다. 활성 시트로 밀면 방금 만든 Summary 대신
        # 원본 시트 A1에 써서 머리글을 덮어쓴다.
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        start_cell = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("start_cell", "")),
            for_cell=True,
        )
        if not start_cell:
            start_cell = _top_left_cell(service.get_active_selection_ref(resolved_wb, resolved_sheet))
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
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
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
        # "진행률이 80% 미만" 처럼 대상이 다른 시트면 플래너가 `Project_Plan!I2:I21`로 준다.
        qualified_sheet, target_range = _split_sheet_qualified_range(service, resolved_wb, target_range)
        if qualified_sheet:
            resolved_sheet = qualified_sheet
        operator = str(params.get("operator", ">=")).strip()
        threshold = float(params.get("threshold", 0))
        fill_color = str(params.get("fill_color", "#FFFF00"))
        compare_column = str(params.get("compare_column") or "").strip().upper() or None
        extra = {"compare_column": compare_column} if compare_column else {}
        return service.highlight_by_condition(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            operator=operator,
            threshold=threshold,
            fill_color=fill_color,
            **extra,
        )

    if action == "excel_live.fill_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range", "")).strip().upper()
        if target_range == "__USED_RANGE__":
            target_range = service.get_used_range_ref(resolved_wb, resolved_sheet)
        elif not target_range or target_range == "__ACTIVE_SELECTION__":
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
        if target_range == "__USED_RANGE__":
            target_range = service.get_used_range_ref(resolved_wb, resolved_sheet)
        elif not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.clear_range(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
        )

    if action == "excel_live.apply_border":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range", "")).strip().upper()
        if target_range == "__USED_RANGE__":
            target_range = service.get_used_range_ref(resolved_wb, resolved_sheet)
        elif not target_range or target_range == "__ACTIVE_SELECTION__":
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
        sheet_hint = str(params.get("sheet_name") or "").strip() or sheet_name
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_hint)
        range_ref = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("range_ref", "")),
            for_cell=False,
        ) or service.get_active_selection_ref(resolved_wb, resolved_sheet)
        formula_a1 = str(params.get("formula_a1", "")).strip()
        if not formula_a1.startswith("="):
            raise ExcelLiveError("formula_a1은 '='로 시작해야 합니다.")
        return service.set_formula(resolved_wb, resolved_sheet, range_ref, formula_a1)

    if action == "excel_live.verify_formula_result":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("range_ref", "")),
            for_cell=False,
        ) or service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.verify_formula_result(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            range_ref=range_ref,
        )

    if action == "excel_live.find_replace":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range", "")).strip().upper()
        if target_range == "__USED_RANGE__":
            target_range = service.get_used_range_ref(resolved_wb, resolved_sheet)
        elif not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.find_replace(
            resolved_wb,
            resolved_sheet,
            target_range,
            str(params.get("find_text") or ""),
            str(params.get("replace_text") or ""),
            match_case=bool(params.get("match_case", False)),
            whole_cell=bool(params.get("whole_cell", False)),
        )

    if action in {"excel_live.merge_cells", "excel_live.unmerge_cells"}:
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        method = service.merge_cells if action == "excel_live.merge_cells" else service.unmerge_cells
        return method(resolved_wb, resolved_sheet, target_range)

    if action == "excel_live.freeze_panes":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        return service.freeze_panes(resolved_wb, resolved_sheet, str(params.get("freeze_at") or "A2"))

    if action == "excel_live.autofit_columns":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range") or "__USED_RANGE__").strip().upper()
        return service.autofit_columns(resolved_wb, resolved_sheet, target_range)

    if action == "excel_live.define_named_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.define_named_range(
            resolved_wb, resolved_sheet, str(params.get("name") or ""), target_range
        )

    if action == "excel_live.set_print_area":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        print_area = params.get("print_area")
        if print_area:
            print_area = _resolve_runtime_range_ref(
                service, workbook_id=resolved_wb, sheet_name=resolved_sheet, raw_range=str(print_area), for_cell=False
            )
        return service.set_print_area(
            resolved_wb,
            resolved_sheet,
            print_area=print_area or None,
            orientation=params.get("orientation"),
            fit_to_page=bool(params.get("fit_to_page", False)),
        )

    if action == "excel_live.add_cell_comment":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_cell = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("target_range") or "__ACTIVE_CELL__"),
            for_cell=True,
        )
        return service.add_cell_comment(
            resolved_wb, resolved_sheet, target_cell, str(params.get("text") or ""), str(params.get("author") or "OfficeClaw AI")
        )

    if action in {"excel_live.apply_color_scale", "excel_live.apply_data_bar"}:
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("target_range") or "__ACTIVE_SELECTION__"),
            for_cell=False,
        )
        if action == "excel_live.apply_color_scale":
            return service.apply_color_scale(
                resolved_wb,
                resolved_sheet,
                target_range,
                min_color=str(params.get("min_color") or "#F8696B"),
                mid_color=str(params.get("mid_color") or "#FFEB84"),
                max_color=str(params.get("max_color") or "#63BE7B"),
            )
        return service.apply_data_bar(
            resolved_wb, resolved_sheet, target_range, color=str(params.get("color") or "#638EC6")
        )

    if action == "excel_live.set_number_format":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("target_range") or "__ACTIVE_SELECTION__"),
            for_cell=False,
        )
        return service.set_number_format(resolved_wb, resolved_sheet, target_range, str(params.get("format_code") or ""))

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
            mode=str(params.get("mode", "keep")),
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

    if action == "excel_live.find_duplicates":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.find_duplicates(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            key_columns=params.get("key_columns") or [],
            has_header=bool(params.get("has_header", True)),
            output_sheet=params.get("output_sheet"),
        )

    if action == "excel_live.recalculate":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.recalculate(workbook_id=resolved_wb, sheet_name=resolved_sheet)

    if action == "excel_live.export_pdf":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.export_pdf(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            output_path=str(params.get("output_path") or "").strip() or None,
        )

    if action == "excel_live.pivot_table":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        # 같은 계획에 create_sheet가 앞서 오면 활성 시트가 결과 시트로 바뀐다.
        # 원본 시트를 명시로 붙잡아 두지 않으면 빈 시트를 집계하려다 실패한다.
        resolved_sheet = str(params.get("source_sheet") or "").strip() or _resolve_sheet_name(
            service, resolved_wb, sheet_name
        )
        output_sheet = str(params.get("output_sheet") or "").strip()
        request_sheet = str(sheet_name or "").strip()
        source_range = str(params.get("source_range", "")).strip().upper()
        qualified_sheet, source_range = _split_sheet_qualified_range(service, resolved_wb, source_range)
        if qualified_sheet:
            resolved_sheet = qualified_sheet
        if output_sheet and request_sheet and resolved_sheet.lower() == output_sheet.lower():
            # 결과 시트를 원본으로 잡으면 방금 만든 빈 시트를 집계하게 된다.
            resolved_sheet = request_sheet
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
        # 앞 단계가 만든 집계표를 그리는 경우, 원본 시트가 아니라 결과 시트를 봐야 한다.
        chart_sheet = str(params.get("source_sheet") or "").strip() or sheet_name
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, chart_sheet)
        source_range = str(params.get("source_range", "")).strip().upper()
        qualified_sheet, source_range = _split_sheet_qualified_range(service, resolved_wb, source_range)
        if qualified_sheet:
            resolved_sheet = qualified_sheet
        if source_range == "__USED_RANGE__":
            source_range = service.get_used_range_ref(resolved_wb, resolved_sheet)
        elif not source_range or source_range == "__ACTIVE_SELECTION__":
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

    if action in _SHARED_DISPATCH_ACTIONS:
        # 열/집계 도구는 excel_actions가 이미 갖고 있다. 여기서 한 번 더 쓰면 두 벌이 어긋난다.
        return execute_excel_action(
            action=action,
            params=params,
            workbook_id=workbook_id,
            sheet_name=str(params.get("sheet_name") or "").strip() or sheet_name,
        )

    raise ExcelLiveError(f"지원하지 않는 action: {action}")


# 라우터가 자체 분기를 갖지 않고 공용 디스패처에 넘기는 액션.
_SHARED_DISPATCH_ACTIONS = frozenset(
    {
        "excel_live.sort_rows",
        "excel_live.drop_column",
        "excel_live.rename_column",
        "excel_live.add_column",
        "excel_live.group_by_aggregate",
        "excel_live.calculate_column_stat",
    }
)


_XLWINGS_TRACE_PARAM_KEYS = {
    "target_range",
    "range_ref",
    "start_cell",
    "rows",
    "cols",
    "fill_color",
    "line_style",
    "weight",
    "color",
    "operator",
    "threshold",
    "formula_a1",
    "sheet_name",
    "key_column",
    "order",
    "column",
    "value",
    "output_sheet",
    "output_start",
}
_XLWINGS_TRACE_RESULT_KEYS = {
    "address",
    "changed_cells",
    "written_cells",
    "cleared_cells",
    "matched_cells",
    "formula_applied_cells",
    "rows",
    "cols",
    "row_count",
    "col_count",
    "created",
    "selected",
    "sorted_rows",
    "filtered_rows",
    "removed_rows",
    "remaining_rows",
    "non_empty_cells",
    "numeric_cells",
    "sum",
    "average",
    "queue_wait_ms",
}


def _trace_compact_value(value: Any, *, limit: int = 180) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _trace_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw_key, value in params.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        include = (
            key in _XLWINGS_TRACE_PARAM_KEYS
            or key.endswith("_range")
            or key.endswith("_sheet")
            or key.endswith("_cell")
        )
        if not include:
            continue
        if key == "values_2d":
            if isinstance(value, list):
                row_count = len(value)
                col_count = max((len(row) if isinstance(row, list) else 1 for row in value), default=0)
                out["values_shape"] = f"{row_count}x{col_count}"
            continue
        out[key] = _trace_compact_value(value)
    return out


def _trace_result(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _XLWINGS_TRACE_RESULT_KEYS:
        if key not in result:
            continue
        out[key] = _trace_compact_value(result.get(key))
    return out


def _build_xlwings_trace_entry(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    service = get_excel_live_service()
    engine_name = str(getattr(service, "engine", "xlwings") or "xlwings").strip() or "xlwings"
    action_name = str(action or "").strip()
    method = action_name[11:] if action_name.startswith("excel_live.") else action_name
    target_range = _normalize_range_text(
        result.get("address")
        or params.get("target_range")
        or params.get("range_ref")
        or params.get("start_cell")
    )
    effective_sheet = str(params.get("sheet_name") or sheet_name or "").strip()
    return {
        "engine": engine_name,
        "action": action_name,
        "method": method,
        "workbook_id": str(workbook_id or ""),
        "sheet_name": effective_sheet,
        "target_range": target_range,
        "params": _trace_params(params),
        "result": _trace_result(result),
    }


def _append_xlwings_trace(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    out = dict(result or {})
    existing = out.get("xlwings_ops")
    ops = [row for row in existing if isinstance(row, dict)] if isinstance(existing, list) else []
    ops.append(
        _build_xlwings_trace_entry(
            action=action,
            params=params,
            workbook_id=workbook_id,
            sheet_name=sheet_name,
            result=out,
        )
    )
    out["xlwings_ops"] = ops
    return out


def _collect_xlwings_ops_from_execution(execution: Any) -> list[dict[str, Any]]:
    if execution is None:
        return []
    ops: list[dict[str, Any]] = []
    for step in getattr(execution, "steps", []) or []:
        result = getattr(step, "result", None)
        if not isinstance(result, dict):
            continue
        step_ops = result.get("xlwings_ops")
        if isinstance(step_ops, list):
            ops.extend([row for row in step_ops if isinstance(row, dict)])
    return ops


def _verify_step_result(
    *,
    action: str,
    params: dict[str, Any],
    result: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> bool | tuple[bool, str]:
    """
    단계 실행 후 최소 검증.
    - service의 range snapshot을 활용해 상태를 빠르게 점검한다.
    - 그 다음 파일을 다시 읽어 사후조건(정렬 순서·결과 시트 등)까지 확인한다.
    """
    service = get_excel_live_service()
    effect_ok, effect_detail = verify_effect(
        action=action,
        params=params,
        result=result,
        service=service,
        workbook_id=workbook_id,
        sheet_name=sheet_name,
    )
    if not effect_ok:
        return False, effect_detail

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

    if action == "excel_live.find_replace":
        return int(result.get("replaced_cells", 0) or 0) >= 0

    if action == "excel_live.merge_cells":
        return bool(result.get("merged"))

    if action == "excel_live.unmerge_cells":
        return int(result.get("unmerged_ranges", 0) or 0) >= 0

    if action == "excel_live.freeze_panes":
        return "frozen" in result

    if action == "excel_live.autofit_columns":
        return int(result.get("adjusted_columns", 0) or 0) >= 1

    if action == "excel_live.define_named_range":
        return bool(result.get("name"))

    if action == "excel_live.set_print_area":
        return "orientation" in result

    if action == "excel_live.add_cell_comment":
        return bool(result.get("comment_added"))

    if action in {"excel_live.apply_color_scale", "excel_live.apply_data_bar"}:
        return bool(result.get("applied"))

    if action == "excel_live.set_number_format":
        return int(result.get("formatted_cells", 0) or 0) >= 1

    if action == "excel_live.sort_range":
        return int(result.get("sorted_rows", 0) or 0) >= 0

    if action == "excel_live.filter_rows":
        return int(result.get("filtered_rows", 0) or 0) >= 0

    if action == "excel_live.dedupe_rows":
        return int(result.get("removed_rows", 0) or 0) >= 0

    if action == "excel_live.pivot_table":
        rows = int(result.get("rows", 0) or 0)
        source_rows = int(result.get("source_rows", 0) or 0)
        if source_rows and rows - 1 >= source_rows:
            # 그룹 기준이 고유값 열(주문번호 등)이면 원본 행이 그대로 복사된다. 집계가 아니다.
            return False
        return bool(result.get("created")) and rows >= 2

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

    if action == "excel_live.create_sheet":
        return bool(str(result.get("sheet_name", "")).strip())

    if action in {
        "excel_live.save_workbook",
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.list_sheets",
        "excel_live.select_sheet",
    }:
        return True

    return True


# 사후조건 검증 실패 코드 → 사용자가 다음에 뭘 해야 할지 알 수 있는 안내 문구.
_VERIFY_FAILURE_MESSAGES = {
    "sort_not_applied": "정렬을 적용했지만 기준 열이 요청한 순서로 바뀌지 않아 원래 상태로 되돌렸습니다. 기준 열을 다시 알려주세요.",
    "sort_key_out_of_range": "지정한 기준 열이 정렬 범위 밖입니다. 범위나 기준 열을 다시 알려주세요.",
    "sort_no_rows": "정렬할 데이터 행이 없습니다. 머리글 아래에 데이터가 있는 범위를 알려주세요.",
    "filter_no_match": "조건에 맞는 행이 하나도 없어 아무것도 남기지 않았습니다. 조건을 확인해 주세요.",
    "no_cells_changed": "조건에 해당하는 셀이 없어 서식이 적용되지 않았습니다. 기준 값을 확인해 주세요.",
    "formula_not_applied": "수식이 입력된 셀이 없습니다. 적용할 범위를 알려주세요.",
    "validation_not_applied": "입력 제한이 설정되지 않았습니다. 대상 범위와 허용 값을 알려주세요.",
    "output_sheet_missing": "결과를 쓸 시트가 만들어지지 않았습니다. 결과 시트 이름을 알려주세요.",
    "output_sheet_empty": "결과 시트가 비어 있어 작업을 되돌렸습니다. 원본 범위를 확인해 주세요.",
}


def _verification_failure_reason(detail: str) -> str:
    code = str(detail or "").split(":", 1)[0].split(";", 1)[0].strip()
    message = _VERIFY_FAILURE_MESSAGES.get(code)
    if message:
        return message
    return "작업 실행이 안정성 검증을 통과하지 못했습니다. 복구 정보로 원상 복원이 가능합니다."


def _action_summary(action: str) -> str:
    return _ACTION_SUMMARY.get(action, "엑셀 변경 작업을 실행합니다.")


def _build_approval(action: str, params: dict[str, Any]) -> ApprovalRequest:
    approval_id = str(uuid.uuid4())
    summary = _action_summary(action)
    return ApprovalRequest(
        approval_id=approval_id,
        tool_name=action,
        tool_display_name=action,
        summary=summary,
        args_preview=params,
        session_id="excel-live",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


_ACTION_SUMMARY = {
    "excel_live.write_range": "엑셀 셀 값을 수정합니다.",
    "excel_live.create_table": "엑셀에 표를 생성합니다.",
    "excel_live.highlight_by_condition": "조건에 맞는 셀 서식을 변경합니다.",
    "excel_live.fill_range": "선택 범위 전체 배경색을 변경합니다.",
    "excel_live.apply_border": "선택 범위에 경계선을 적용합니다.",
    "excel_live.set_formula": "지정 범위에 수식을 적용합니다.",
    "excel_live.sort_range": "지정 범위를 정렬합니다.",
    "excel_live.filter_rows": "조건에 맞는 행만 필터링합니다.",
    "excel_live.dedupe_rows": "중복 행을 제거합니다.",
    "excel_live.find_duplicates": "중복 값을 삭제하지 않고 찾아서 보고합니다.",
    "excel_live.recalculate": "수식을 다시 계산하도록 표시합니다.",
    "excel_live.export_pdf": "시트를 PDF로 내보냅니다.",
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
    "excel_live.find_replace": "선택 범위에서 텍스트를 찾아 바꿉니다.",
    "excel_live.merge_cells": "선택 범위의 셀을 병합합니다.",
    "excel_live.unmerge_cells": "선택 범위의 병합 셀을 해제합니다.",
    "excel_live.freeze_panes": "머리글 행/열을 고정합니다.",
    "excel_live.autofit_columns": "열 너비를 내용에 맞게 자동 조정합니다.",
    "excel_live.define_named_range": "선택 범위에 이름(정의된 이름)을 지정합니다.",
    "excel_live.set_print_area": "인쇄 영역/방향/페이지 맞춤을 설정합니다.",
    "excel_live.add_cell_comment": "셀에 메모(코멘트)를 추가합니다.",
    "excel_live.apply_color_scale": "선택 범위에 색조 조건부 서식을 적용합니다.",
    "excel_live.apply_data_bar": "선택 범위에 데이터 막대 조건부 서식을 적용합니다.",
    "excel_live.set_number_format": "선택 범위의 표시 형식(숫자/퍼센트/날짜 등)을 변경합니다.",
}


@router.get("/status")
def get_status():
    service = get_excel_live_service()
    available = service.is_available()
    return {
        "available": available,
        "engine": str(getattr(service, "engine", "xlwings") or "xlwings"),
        "workbooks": service.list_workbooks() if available else [],
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
            raw_result = _execute_action(
                action=req.action,
                params=req.params,
                workbook_id=req.workbook_id,
                sheet_name=req.sheet_name,
            )
            return _append_xlwings_trace(
                action=req.action,
                params=req.params,
                workbook_id=req.workbook_id,
                sheet_name=req.sheet_name,
                result=raw_result,
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


def _register_macro_run(
    req: ExcelLiveCommandRequest,
    planned: list[MacroStepPlan],
) -> MacroRun:
    now = time.time()
    run = MacroRun(
        macro_id=uuid.uuid4().hex,
        message=req.message,
        user_id=req.user_id,
        session_id=req.session_id,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        steps=[
            MacroStepState(
                index=item.index,
                command=item.command,
                destructive=item.destructive,
                warnings=list(item.warnings),
            )
            for item in planned
        ],
        created_at_ts=now,
        updated_at_ts=now,
    )
    _macro_runs[run.macro_id] = run
    return run


async def _plan_macro_response(
    req: ExcelLiveCommandRequest,
    llm: LLMService,
) -> ExcelLiveActionResponse | None:
    """
    고수준 요청을 하위 명령으로 펼쳐 승인 카드를 돌려준다.

    분해에 실패하면 None을 반환한다 — 그 경우 호출자는 기존 단일 명령 경로로 계속
    진행한다. 매크로는 편의 계층이므로, 여기서 막히면 예전만 못한 상태가 된다.
    """
    try:
        digest = build_workbook_digest(
            get_excel_live_service(),
            workbook_id=req.workbook_id,
            active_sheet_hint=req.sheet_name,
        )
    except Exception:
        digest = {}

    try:
        planned = await asyncio.wait_for(
            decompose_macro_request(
                req.message,
                llm,
                digest=digest,
                digest_text=render_workbook_digest(digest) if digest else "",
                model=get_macro_model_name(),
            ),
            timeout=_MACRO_DECOMPOSE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        trace_note("macro_decompose_failed", error_type=type(exc).__name__, error=str(exc))
        return None

    # 한 단계짜리 분해는 매크로가 아니다. 승인 카드만 늘어나므로 기존 경로에 맡긴다.
    if len(planned) < 2:
        trace_note("macro_skipped", step_count=len(planned), reason="단계가 2개 미만")
        return None

    trace_note(
        "macro_plan",
        model=get_macro_model_name(),
        steps=[
            {"index": s.index, "command": s.command, "destructive": s.destructive}
            for s in planned
        ],
    )

    run = _register_macro_run(req, planned)
    _audit.log(
        action="excel.live.macro.planned",
        target=run.macro_id,
        detail=f"steps={len(run.steps)} workbook={req.workbook_id or ''}",
    )
    snapshot = _macro_snapshot(run)
    snapshot["ask_macro_approval"] = True
    snapshot["original_message"] = req.message
    return ExcelLiveActionResponse(
        ok=True,
        action="excel_live.macro_plan",
        reason=f"{len(run.steps)}단계로 나눴습니다. 확인 후 실행해 주세요.",
        result=snapshot,
    )


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


def _validate_plan_for_request(steps, req: ExcelLiveCommandRequest):
    return validate_plan(
        steps,
        context=ValidationContext(
            message=req.message,
            workbook_id=req.workbook_id,
            sheet_name=req.sheet_name,
            context_range=req.context_range,
            recent_range=_recent_range_by_workbook.get(_context_key(req.workbook_id)),
        ),
    )


def _bind_plan_for_request(steps, *, digest: dict[str, Any], req: ExcelLiveCommandRequest):
    """실행 직전에 상징적 파라미터를 실제 머리글/시트로 확정한다."""
    try:
        return bind_plan_steps(
            steps,
            digest=digest,
            message=req.message,
            sheet_name=req.sheet_name,
        )
    except Exception:
        # 바인딩은 보조 레이어다. 실패해도 원본 플랜으로 계속 진행한다.
        return steps, []


def _chain_chart_to_pivot(steps: list[PlanStep], *, session_key: str) -> list[PlanStep]:
    """피벗 다음에 오는 차트는 원본이 아니라 집계 결과를 그린다.

    같은 계획 안이든("집계하고 차트도"), 다음 턴이든("그 결과로 막대 그래프도") 마찬가지다.
    직전 집계 결과를 기억해 두지 않으면 차트가 180행짜리 원본을 그리거나 엉뚱한 시트에 붙는다.
    """
    pivot_sheet = ""
    for step in steps:
        if step.action == "excel_live.pivot_table":
            pivot_sheet = str(step.params.get("output_sheet") or "").strip()
        elif step.action == "excel_live.create_chart":
            target = pivot_sheet or _last_aggregate_sheet.get(session_key, "")
            if not target:
                continue
            step.params["source_sheet"] = target
            step.params["source_range"] = "__USED_RANGE__"
            step.params["output_sheet"] = target
    return steps


def _drop_table_step_when_aggregating(steps: list[PlanStep]) -> list[PlanStep]:
    """집계 계획에 끼어든 표 생성 단계를 덜어낸다.

    플래너는 "집계표를 만들어줘"를 create_table로도 해석해, 빈 5x5 표를 새 시트에 만들고
    활성 시트를 그쪽으로 옮긴다. 그러면 뒤따르는 피벗이 빈 시트를 집계하려다 실패한다.
    집계 결과표는 피벗이 직접 쓰므로 이 단계는 필요 없다.
    """
    if not any(step.action == "excel_live.pivot_table" for step in steps):
        return steps
    return [step for step in steps if step.action != "excel_live.create_table"]


def _drop_trailing_verification(steps: list[PlanStep]) -> list[PlanStep]:
    """수식과 무관한 계획 끝에 붙은 결과 검증 단계를 덜어낸다.

    플래너는 색칠·정렬 계획 뒤에도 verify_formula_result를 붙이곤 한다. 각 단계는
    어차피 실행기가 검증하므로 하는 일이 없는데, 마지막 액션이 응답의 대표 액션이라
    "노란색으로 칠했습니다" 대신 "수식 결과를 확인했습니다"라고 답하게 된다.
    """
    if len(steps) < 2 or steps[-1].action != "excel_live.verify_formula_result":
        return steps
    if any(step.action == "excel_live.set_formula" for step in steps):
        return steps
    return steps[:-1]


@router.post("/command", response_model=ExcelLiveActionResponse)
async def post_command(
    req: ExcelLiveCommandRequest,
    llm: LLMService = Depends(get_llm_service),
):
    """자연어 명령 한 턴. 판단·계획·실행 과정은 logs/chat_log.jsonl에 한 줄로 남는다."""
    with turn_scope(
        endpoint="excel-live/command",
        message=req.message,
        session_id=_slot_session_key(req),
        request={
            "workbook_id": req.workbook_id,
            "sheet_name": req.sheet_name,
            "context_range": req.context_range,
            "approve": req.approve,
        },
    ):
        response = await _run_command(req, llm)
        set_outcome_from_response(response)
        return response


async def _run_command(
    req: ExcelLiveCommandRequest,
    llm: LLMService,
):
    _cleanup_expired_table_slots()
    _cleanup_expired_operation_slots()
    _cleanup_expired_clarifications()
    _cleanup_expired_macro_runs()
    session_key = _slot_session_key(req)
    pending_slot = _pending_create_table_slots.get(session_key)
    pending_operation = _pending_operation_slots.get(session_key)
    pending_clarification = _pending_clarifications.get(session_key)

    # "대시보드 만들어줘"류는 계획 한 번(4단계)에 담기지 않는다. 계획을 세우기 전에
    # 갈라내야 단순 명령이 왕복 비용을 물지 않는다.
    # 대기 슬롯이 있으면 그 문장은 되묻기에 대한 답변이고, approve=True는 매크로
    # 실행기가 하위 명령을 돌릴 때 쓰는 경로라 둘 다 여기로 들어오면 안 된다.
    if (
        not req.approve
        and pending_slot is None
        and pending_operation is None
        and looks_like_macro_request(req.message)
    ):
        macro_response = await _plan_macro_response(req, llm)
        if macro_response is not None:
            return macro_response

    hints = extract_create_table_slot_hints(req.message)
    operation_hints = _extract_operation_hints(req.message)
    user_key = resolve_user_key({"user_id": req.user_id, "session_id": req.session_id})
    personalization_hint = build_personalization_prompt(user_key)
    quick_action_plan = _build_quick_action_plan(req.message, req.context_range)
    rule_based_step = parse_command_rule_based(
        req.message,
        context_range=req.context_range,
    )
    fallback_rule_step: dict[str, Any] | None = (
        rule_based_step if isinstance(rule_based_step, dict) else None
    )
    operation_intent = str(operation_hints.get("intent") or "").strip()
    trace_note(
        "understand",
        operation_intent=operation_intent or "(없음)",
        table_intent=bool(hints.get("table_intent")),
        table_size={"rows": hints.get("rows"), "cols": hints.get("cols")},
        table_headers=hints.get("headers") or [],
        rule_action=str((rule_based_step or {}).get("action", "")) or "(규칙 해석 없음)",
        quick_plan=trace_plan(quick_action_plan),
        pending_table_slot=pending_slot is not None,
        pending_operation_slot=(pending_operation.intent if pending_operation else ""),
    )

    def _reads_concrete_range(step: Any) -> dict[str, Any] | None:
        """ "B9 값만 읽어줘"처럼 범위를 콕 집은 단순 조회인지 판정한다."""
        if not isinstance(step, dict):
            return None
        if str(step.get("action", "")) != "excel_live.read_range":
            return None
        range_ref = str((step.get("params") or {}).get("range_ref", ""))
        return step if RANGE_REF_PATTERN.fullmatch(range_ref) else None

    # 범위를 직접 지목한 조회는 이전 멀티턴의 답도, 플래너가 재해석할 대상도 아니다.
    # 규칙이 이미 확실히 아는 명령을 모델 기분에 맡기면 같은 문장이 매번 다르게 동작한다.
    # "D2:D20 수식 값 확인해줘"는 범위가 있어도 단순 조회가 아니라 수식 검증이다.
    reads_only = not re.search(r"(수식|함수|formula)", req.message, re.IGNORECASE)
    standalone_read_step = (
        None
        if operation_intent or not reads_only
        else (
            _reads_concrete_range(rule_based_step)
            or _reads_concrete_range(
                quick_action_plan[0] if isinstance(quick_action_plan, list) and quick_action_plan else None
            )
        )
    )
    if pending_operation is not None:
        starts_new_command = bool(standalone_read_step)
        if starts_new_command:
            _pending_operation_slots.pop(session_key, None)
            pending_operation = None
        else:
            fallback_rule_step = None
    if pending_slot is not None or pending_operation is not None:
        quick_action_plan = None
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
    elif (
        fallback_rule_step
        and str(fallback_rule_step.get("action", "")).strip() == "excel_live.read_range"
        and operation_intent in _EDIT_EXPECTED_OPERATION_INTENTS
    ):
        # 편집 의도로 분류된 요청에서 read_range 폴백은 "보여줘" 같은 표현 때문에
        # 오동작을 만들 수 있으므로 멀티턴 슬롯 경로를 우선한다.
        fallback_rule_step = None

    parsed: dict[str, Any] | None = None
    reflection_attempted = False
    reflection_applied = False
    reflection_reason = ""
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
    quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan) if quick_action_plan else []
    quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
    # "전체 지우기" 같은 고신뢰 퀵 액션은 LLM 변환 오차보다 규칙 우선이 안정적이다.
    if quick_first_action in {
        "excel_live.clear_range",
        "excel_live.apply_border",
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.list_sheets",
        "excel_live.select_sheet",
        "excel_live.create_sheet",
        "excel_live.save_workbook",
        "excel_live.compare_ranges",
        "excel_live.forecast_linear",
        "excel_live.set_data_validation",
        "excel_live.set_formula",
    }:
        should_parse_with_llm = False
    elif quick_first_action == "excel_live.fill_range":
        # 단순 색 채우기 요청은 fast path로 즉시 실행하는 편이 안정적이다.
        should_parse_with_llm = False
    if _quick_plan_underfits_message(quick_first_action, req.message):
        # 규칙이 표현하지 못하는 요청은 플래너에게 넘긴다.
        should_parse_with_llm = True
    trace_route(
        "quick_rule:hit" if not should_parse_with_llm else "quick_rule:miss",
        why=(
            f"규칙이 {quick_first_action}로 확정"
            if not should_parse_with_llm
            else "규칙으로 확정하지 못해 플래너로 넘김"
        ),
        quick_first_action=quick_first_action or "(없음)",
    )
    reasoning_complexity_score = _score_command_complexity(
        message=req.message,
        operation_hints=operation_hints,
        hints=hints,
        quick_plan=quick_plan_for_parse,
    )
    reasoning_mode = _select_reasoning_mode(
        should_parse_with_llm=should_parse_with_llm,
        complexity_score=reasoning_complexity_score,
    )
    parse_timeout_seconds, parse_max_attempts, parse_retry_backoff_seconds = _parse_budget_for_reasoning_mode(
        reasoning_mode
    )
    # 플래너가 시트/머리글을 모른 채 파라미터를 추측하지 않도록 실제 파일 상태를 먼저 읽는다.
    workbook_digest = build_workbook_digest(
        get_excel_live_service(),
        workbook_id=req.workbook_id,
        active_sheet_hint=req.sheet_name,
    )
    # "매출 시트 ~"처럼 원문이 대상 시트를 지목하면 실행 시트를 그쪽으로 맞춘다.
    # 액션 파라미터에는 시트 슬롯이 없어서, 여기서 정하지 않으면 활성 시트에 잘못 적용된다.
    message_sheet = resolve_sheet_from_message(req.message, workbook_digest, default=req.sheet_name)
    if message_sheet and message_sheet != req.sheet_name:
        req.sheet_name = message_sheet
        workbook_digest = build_workbook_digest(
            get_excel_live_service(),
            workbook_id=req.workbook_id,
            active_sheet_hint=req.sheet_name,
            use_cache=False,
        )
    def _validate_steps(steps):
        return _validate_plan_for_request(steps, req)

    def _bind_steps(steps):
        return _bind_plan_for_request(steps, digest=workbook_digest, req=req)

    def _bind_and_validate(steps, *, require_binding_evidence: bool = False) -> list[PlanStep] | None:
        """바인딩까지 마친 뒤 실행 가능한 계획이면 돌려주고, 아니면 None.

        되묻기 전에 "이 계획 그냥 돌려도 되나?"를 판정하는 데 쓴다.
        미해결 슬롯이 하나라도 있으면 추측 실행 대신 되묻는 쪽을 택한다.

        require_binding_evidence=True면 바인더가 실제로 원문·머리글에서 값을 확정한
        경우에만 통과시킨다. 슬롯 규칙이 채워 넣은 기본값(B열·C열 같은)은 검증을 통과해도
        근거가 없으므로, 그대로 실행하면 조용히 엉뚱한 열을 건드리게 된다.
        """
        if not steps:
            return None
        bound, notes = _bind_steps(steps)
        if any(note.get("status") == "unresolved" for note in notes):
            return None
        if require_binding_evidence and not any(note.get("status") == "bound" for note in notes):
            return None
        try:
            return _chain_chart_to_pivot(
                _drop_trailing_verification(_drop_table_step_when_aggregating(_validate_steps(bound))),
                session_key=session_key,
            )
        except Exception:
            return None

    parse_context_base = {
        "context_range": req.context_range,
        "workbook_id": req.workbook_id,
        "sheet_name": req.sheet_name,
        "reasoning_mode": reasoning_mode,
        "complexity_score": reasoning_complexity_score,
        "personalization_hint": personalization_hint,
        "workbook_digest": workbook_digest,
        "workbook_digest_text": render_workbook_digest(workbook_digest),
        "conversation_history_text": _render_conversation_history(pending_clarification),
        # 같은 질문을 반복하지 않도록, 이미 되물은 세션에서는 되묻기를 막고 실행을 강제한다.
        "forbid_clarify": (
            pending_clarification is not None
            and pending_clarification.ask_count >= _MAX_CONSECUTIVE_CLARIFICATIONS
        ),
    }
    # 모델이 무엇을 보고 판단했는지가 실패 원인 분류의 출발점이다. "매출 열이 없다"는
    # 계획을 세웠을 때, 모델에게 매출 열을 안 보여준 것인지 보여줬는데 못 고른 것인지를
    # 이 기록 없이는 가를 수 없다.
    _digest_sheets = [s for s in (workbook_digest.get("sheets") or []) if isinstance(s, dict)]
    _target_sheet = str(req.sheet_name or workbook_digest.get("active_sheet") or "")
    _sheet_digest = next(
        (s for s in _digest_sheets if str(s.get("name", "")) == _target_sheet),
        _digest_sheets[0] if _digest_sheets else {},
    )
    trace_note(
        "observation",
        workbook_id=req.workbook_id or "(선택된 통합문서)",
        sheet_name=_target_sheet or "(활성 시트)",
        context_range=req.context_range or "(없음)",
        sheets=[s.get("name") for s in _digest_sheets],
        headers=[
            str(c.get("header") or "")
            for c in (_sheet_digest.get("columns") or [])
            if isinstance(c, dict)
        ],
        used_range=_sheet_digest.get("used_range") or "",
        digest_text=Long(parse_context_base["workbook_digest_text"]),
        conversation_history=parse_context_base["conversation_history_text"],
        forbid_clarify=parse_context_base["forbid_clarify"],
    )

    parse_error: Exception | None = None
    parse_timeout_count = 0

    def _validate_for_escalation(raw_steps: Any) -> tuple[bool, str]:
        """계획이 실행 직전 검증을 통과하는지. 실패 사유는 자가 수정 프롬프트로 간다."""
        steps = _normalize_plan_or_empty(raw_steps)
        if not steps:
            return False, "계획을 실행 단계로 정규화하지 못했습니다."
        bound, notes = _bind_steps(steps)
        unresolved = [str(n.get("slot") or n.get("param") or "") for n in notes if n.get("status") == "unresolved"]
        if unresolved:
            return False, f"파라미터를 확정하지 못했습니다: {', '.join(filter(None, unresolved)) or '대상 불명확'}"
        try:
            _validate_steps(bound)
        except Exception as exc:  # noqa: BLE001 - 검증기가 낸 문구를 그대로 모델에 돌려준다
            return False, str(exc)
        return True, ""

    async def _parse_tier(_message: str, ctx: dict[str, Any]) -> dict[str, Any]:
        """한 단계 파싱. 타임아웃 재시도는 이 안에서 소진한다."""
        nonlocal parse_error, parse_timeout_count
        last: Exception | None = None
        for parse_attempt in range(parse_max_attempts):
            try:
                result = await asyncio.wait_for(
                    parse_excel_live_command(req.message, llm_service=llm, context=dict(ctx)),
                    timeout=parse_timeout_seconds,
                )
                parse_error = None
                return result
            except asyncio.TimeoutError as exc:
                last = exc
                parse_timeout_count += 1
                if parse_attempt + 1 < parse_max_attempts:
                    backoff = parse_retry_backoff_seconds * float(parse_attempt + 1)
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                    continue
                break
            except ValueError as exc:
                last = exc
                break
        parse_error = last
        raise last or ValueError("엑셀 명령을 해석하지 못했습니다.")

    escalation: EscalationResult | None = None
    if should_parse_with_llm:
        # 예전에는 여기서 실패하면 곧장 사용자에게 되물었다. 이제 자가 수정 →
        # 강한 모델까지 올라가 보고, 그래도 안 되면 그때 묻는다.
        escalation = await plan_with_escalation(
            req.message,
            parse=_parse_tier,
            validate=_validate_for_escalation,
            context=parse_context_base,
            local_model=get_planner_model_name(),
            # 규칙 계획이나 슬롯 의도가 이미 잡혀 있으면 그쪽이 답한다.
            # 그런 요청까지 LLM에 두세 번 더 태우면 지연만 늘어난다.
            allow_repair=not (
                bool(quick_action_plan)
                or fallback_rule_step is not None
                or bool(operation_hints.get("intent"))
            ),
        )
        parsed = escalation.parsed or escalation.best_effort
        if parsed is not None:
            parse_error = None
        record_escalation(
            message=req.message, result=escalation, workbook_digest=workbook_digest
        )

        trace_note(
            "planner",
            model=get_planner_model_name(),
            reasoning_mode=reasoning_mode,
            complexity_score=reasoning_complexity_score,
            timeout_count=parse_timeout_count,
            error=str(parse_error) if parse_error else escalation.last_error,
            intent=str((parsed or {}).get("intent", "")),
            reason=str((parsed or {}).get("reason", "")),
            action_plan=trace_plan((parsed or {}).get("action_plan")),
            follow_up_question=str((parsed or {}).get("follow_up_question", "")),
            escalation_tier=escalation.final_tier,
            escalation_attempts=escalation.trace(),
        )
        trace_route(
            f"planner:{escalation.final_tier or 'none'}",
            why=(
                escalation.last_error
                or (f"{str((parsed or {}).get('intent', '')) or '의도 미상'} 계획 확보")
            ),
            plan_obtained=parsed is not None,
        )

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
                        "reasoning_mode": reasoning_mode,
                        "complexity_score": reasoning_complexity_score,
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

    if should_parse_with_llm and parsed is not None:
        # 플래너가 "이대로 실행하면 위험하다"고 판단해 되물은 경우.
        # 실행 계층으로 내려보내면 안 되므로 여기서 질문만 돌려주고 턴을 끝낸다.
        clarify_question = clarify_question_from_plan(parsed.get("action_plan"))
        if clarify_question:
            _pending_clarifications[session_key] = PendingClarification(
                session_id=session_key,
                # 되묻기가 이어질 때 최초 요청을 잃지 않는다 — 답변만 남으면 문맥이 사라진다.
                original_message=(
                    pending_clarification.original_message
                    if pending_clarification is not None
                    else req.message
                ),
                question=clarify_question,
                ask_count=(pending_clarification.ask_count + 1) if pending_clarification else 1,
                created_at_ts=time.time(),
            )
            trace_note("clarify", question=clarify_question, source="planner")
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=str(parsed.get("reason", "")) or clarify_question,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": clarify_question,
                    "operation_intent": "clarify",
                    "clarify_source": "planner",
                },
            )
        # 되묻지 않고 계획을 세웠다면 그 대화 줄기는 닫힌다.
        _pending_clarifications.pop(session_key, None)

        should_reflect, reflection_reason = _should_run_reflection_before_execute(
            parsed=parsed,
            message=req.message,
            operation_hints=operation_hints,
            reasoning_mode=reasoning_mode,
        )
        if should_reflect:
            reflection_attempted = True
            reflection_context = dict(parse_context_base)
            reflection_context["reasoning_mode"] = "reflect"
            reflection_context["reflection_note"] = reflection_reason
            reflection_context["previous_first_action"] = _first_action_from_parsed(parsed)
            for reflection_try in range(_COMMAND_REFLECTION_MAX_ATTEMPTS):
                try:
                    reflected = await asyncio.wait_for(
                        parse_excel_live_command(
                            req.message,
                            llm_service=llm,
                            context=dict(reflection_context),
                        ),
                        timeout=_COMMAND_REFLECTION_TIMEOUT_SECONDS,
                    )
                    if reflected and reflected.get("action_plan"):
                        parsed = reflected
                        reflection_applied = True
                    break
                except (asyncio.TimeoutError, ValueError):
                    if reflection_try + 1 < _COMMAND_REFLECTION_MAX_ATTEMPTS:
                        backoff = _COMMAND_DEEP_PARSE_RETRY_BACKOFF_SECONDS * float(reflection_try + 1)
                        if backoff > 0:
                            await asyncio.sleep(backoff)
                        continue
                    break

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

    # "B2:D2에 이름,수량,금액 입력"처럼 범위와 값이 다 나온 명령은 표 생성 인터뷰 대상이 아니다.
    # 플래너가 create_table로 답하는 날에만 되묻기가 뜨면 같은 문장이 실행되기도 하고 안 되기도 한다.
    if standalone_read_step and parsed and parsed.get("action_plan"):
        planner_steps = [s for s in parsed["action_plan"] if isinstance(s, dict)]
        planner_actions = [str(s.get("action", "")) for s in planner_steps]
        rule_range = str((standalone_read_step.get("params") or {}).get("range_ref", ""))
        planner_range = (
            str((planner_steps[0].get("params") or {}).get("range_ref", "")) if planner_steps else ""
        )
        # 조회 한 줄이면 될 명령에 플래너가 검증·저장 단계를 덧붙이면
        # 보고되는 액션까지 바뀌어 사용자는 묻지도 않은 작업을 본다.
        #
        # 액션이 같아도 범위가 다르면 원문에서 뽑은 쪽을 쓴다. "B9 값만 읽어줘"에
        # 플래너가 선택 영역 전체를 답하는 일이 있는데, 그대로 두면 콕 집어 물은
        # 한 칸 대신 표 전체가 읽힌다.
        if (
            planner_actions != ["excel_live.read_range"]
            or planner_range.upper() != rule_range.upper()
        ):
            read_steps = _normalize_plan_or_empty([standalone_read_step])
            if read_steps:
                parsed = {
                    "action_plan": [s.__dict__ for s in read_steps],
                    "action": read_steps[0].action,
                    "params": read_steps[0].params,
                    "reason": "범위를 지목한 단순 조회",
                    "intent": "read",
                }

    def _write_steps_from(raw: Any) -> list[PlanStep]:
        steps = _normalize_plan_or_empty(raw) if raw else []
        return steps if steps and steps[0].action == "excel_live.write_range" else []

    preferred_write = _write_steps_from(quick_action_plan) or _write_steps_from(
        [fallback_rule_step] if fallback_rule_step else None
    )
    # 범위를 콕 집어 "여기에 써줘"라고 한 명령은 표 생성 인터뷰 대상이 아니다.
    explicit_write = bool(preferred_write) and (
        bool(RANGE_REF_PATTERN.search(req.message)) or bool(req.context_range)
    )
    if explicit_write and _TABLE_KEYWORD_PATTERN.search(req.message):
        # "B2부터 3행 3열 표 만들어줘"는 범위가 있어도 표 생성이 맞다.
        explicit_write = False

    # 범위와 값을 다 말한 쓰기는 계획이 이미 정해진 것이다. 그런데 값에 "총매출",
    # "평균주문금액" 같은 집계어가 섞이면 플래너가 통계 조회로 알아들어, 라벨은
    # 한 글자도 안 써지고 엉뚱한 액션이 실패로 돌아온다. 규칙이 확실히 아는 쓰기는
    # 플래너가 뒤집지 못하게 고정한다. (수식·표 생성은 규칙 단계에서 이미 갈린다.)
    if (
        explicit_write
        and pending_operation is None
        and pending_slot is None
        and parsed
        and parsed.get("action_plan")
    ):
        planner_actions = [
            str(s.get("action", "")) for s in parsed["action_plan"] if isinstance(s, dict)
        ]
        if planner_actions != ["excel_live.write_range"]:
            parsed = {
                "action_plan": [step.__dict__ for step in preferred_write],
                "action": preferred_write[0].action,
                "params": preferred_write[0].params,
                "reason": "범위와 값을 지목한 쓰기",
                "intent": "edit",
            }

    # 플래너가 고른 액션이 사용자의 말에 근거가 없으면, 근거 있는 규칙 후보로 되돌린다.
    # 같은 문장이 실행될 때마다 색칠·표생성·조건부서식으로 튀는 문제를 여기서 끊는다.
    if parsed and parsed.get("action_plan"):
        planner_first = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        planner_action = str(planner_first.get("action", ""))
        # 첫 단계만 보면 [create_sheet, pivot_table]처럼 준비 단계 뒤에 숨은 오작동을 놓친다.
        ungrounded_step = next(
            (
                str(s.get("action", ""))
                for s in parsed["action_plan"]
                if isinstance(s, dict) and _action_lacks_evidence(str(s.get("action", "")), req.message)
            ),
            "",
        )
        grounded_steps = [
            s
            for s in parsed["action_plan"]
            if isinstance(s, dict) and not _action_lacks_evidence(str(s.get("action", "")), req.message)
        ]
        # 남은 단계가 create_sheet 같은 준비 동작뿐이면 실행할 의미가 없다.
        # 원문 근거를 요구하는 액션이 하나라도 남아야 "덜어내기"가 성립한다.
        keeps_real_work = any(
            str(s.get("action", "")) not in _PREPARATION_ACTIONS for s in grounded_steps
        )
        if ungrounded_step and grounded_steps and keeps_real_work:
            # "필터 → 피벗 → 차트"처럼 여러 단계 중 하나만 근거가 없을 때
            # 계획 전체를 규칙 한 줄로 갈아끼우면 나머지 요청이 조용히 사라진다.
            # 근거 없는 단계만 덜어내고 나머지는 그대로 실행한다.
            parsed = {
                "action_plan": grounded_steps,
                "action": str(grounded_steps[0].get("action", "")),
                "params": dict(grounded_steps[0].get("params", {}) or {}),
                "reason": "원문 근거가 없는 단계를 덜어낸 계획",
                "intent": parsed.get("intent", "edit"),
            }
            ungrounded_step = ""

        if ungrounded_step:
            planner_action = ungrounded_step
            replaced = False
            for raw_candidate in (quick_action_plan, [fallback_rule_step] if fallback_rule_step else None):
                grounded = _normalize_plan_or_empty(raw_candidate) if raw_candidate else []
                if not grounded or grounded[0].action == planner_action:
                    continue
                if _action_lacks_evidence(grounded[0].action, req.message):
                    continue
                parsed = {
                    "action_plan": [s.__dict__ for s in grounded],
                    "action": grounded[0].action,
                    "params": grounded[0].params,
                    "reason": "원문 근거가 있는 규칙 해석",
                    "intent": "read" if grounded[0].action == "excel_live.read_range" else "edit",
                }
                replaced = True
                break
            if not replaced and operation_intent not in {"", "general", "safety"}:
                # 규칙은 "통합" 같은 구체 의도를 짚었는데 플래너가 엉뚱한 액션을 냈다.
                # 규칙만으로 실행 가능한 계획이 나오면 그쪽을 쓴다.
                # (부족하면 플래너 계획을 유지해 검증·재계획 루프에 맡긴다.)
                rule_slot = _merge_operation_slots(
                    None,
                    session_key=session_key,
                    req=req,
                    hints=operation_hints,
                    parsed=None,
                    digest=workbook_digest,
                )
                if rule_slot is not None and not _operation_follow_up(rule_slot):
                    slot_steps = _normalize_plan_or_empty(_operation_action_plan(rule_slot))
                    # 슬롯이 고른 액션에도 같은 잣대를 적용한다. 근거 없으면 쓰지 않는다.
                    if slot_steps and _action_lacks_evidence(slot_steps[0].action, req.message):
                        slot_steps = []
                    if slot_steps:
                        parsed = {
                            "action_plan": [s.__dict__ for s in slot_steps],
                            "action": slot_steps[0].action,
                            "params": slot_steps[0].params,
                            "reason": f"규칙이 확정한 {rule_slot.intent} 실행 계획",
                            "intent": "edit",
                        }

    table_intent = (bool(hints.get("table_intent")) and not explicit_write) or pending_slot is not None
    if pending_operation is not None:
        # 진행 중인 멀티턴 답변("수량은 C열, 단가는 D열")을 표 생성으로 끌고 가면 대화가 원점으로 돌아간다.
        table_intent = pending_slot is not None
    if parsed and parsed.get("action_plan") and not explicit_write and pending_operation is None:
        first_step = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        # 플래너는 모호할 때 create_table로 도피하는 경향이 있다.
        # 규칙이 통합/비교/예측처럼 구체적인 의도를 이미 짚었다면 그쪽을 신뢰한다.
        if first_step.get("action") == "excel_live.create_table" and operation_intent in {
            "",
            "general",
        }:
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
        fallback_notice = ""
        if need_follow_up and slot.ask_count >= _MAX_TABLE_FOLLOW_UPS:
            # 물어본 만큼 물었는데도 크기를 못 받았다. 여기서 또 되물으면 대화가 제자리를 돈다.
            fallback_notice = _apply_table_size_fallback(slot)
            need_follow_up = False
        trace_note(
            "table_slot",
            rows=slot.rows,
            cols=slot.cols,
            headers=slot.headers or [],
            start_cell=slot.start_cell or "A1",
            ask_count=slot.ask_count,
            need_follow_up=need_follow_up,
            fallback=fallback_notice,
        )
        if need_follow_up:
            slot.ask_count += 1
            _pending_create_table_slots[session_key] = slot
            follow_up_question = (
                str(parsed.get("follow_up_question", "")).strip()
                if isinstance(parsed, dict)
                else ""
            ) or _build_table_follow_up(slot, last_call=slot.ask_count >= _MAX_TABLE_FOLLOW_UPS)
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
            "reason": fallback_notice or "대화 슬롯 기반 표 생성 계획",
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
            # 다른 액션이 나왔더라도, 그 계획이 바인딩·검증을 다 통과할 만큼 완결된 경우에만
            # "새 명령"으로 보고 pending을 해제한다. 반쯤 비어 있는 계획은 슬롯 답변으로 취급한다.
            if (
                has_parsed_plan
                and not _action_to_operation_intent(first_action)
                and not _action_lacks_evidence(first_action, req.message)
            ):
                standalone = _bind_and_validate(_normalize_plan_or_empty(parsed.get("action_plan")))
                if standalone:
                    _pending_operation_slots.pop(session_key, None)
                    pending_operation = None

        op_slot = _merge_operation_slots(
            pending_operation,
            session_key=session_key,
            req=req,
            hints=operation_hints,
            parsed=parsed,
            digest=workbook_digest,
        )
        if op_slot is not None:
            follow_up = _operation_follow_up(op_slot)
            rescued_plan: list[PlanStep] | None = None
            if follow_up:
                # 되묻기 전에 바인더에게 기회를 준다.
                # 슬롯 규칙이 못 채운 값이라도 실제 머리글·시트를 보면 확정되는 경우가 많고,
                # 그때까지 질문하면 사용자는 이미 말한 내용을 또 말해야 한다.
                rescued_plan = _bind_and_validate(
                    _normalize_plan_or_empty(_operation_action_plan(op_slot)),
                    require_binding_evidence=True,
                )
                if rescued_plan:
                    follow_up = ""
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
            if rescued_plan or op_plan_raw:
                action_plan = rescued_plan or _normalize_plan_or_empty(op_plan_raw)
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

    def _recovery_plans() -> list[list[PlanStep]]:
        """LLM 플랜이 검증을 통과하지 못했을 때 대신 쓸 수 있는 후보들.

        플래너가 빈 수식 같은 쓰레기 플랜을 내놨다고 해서 되묻기로 떨어지면,
        규칙이 이미 정확히 이해한 명령까지 실패한다.
        """
        rows: list[list[PlanStep]] = []
        if quick_action_plan:
            rows.append(_normalize_plan_or_empty(quick_action_plan))
        if isinstance(fallback_rule_step, dict) and fallback_rule_step.get("action"):
            rows.append(_normalize_plan_or_empty([fallback_rule_step]))
        recovery_slot = _merge_operation_slots(
            None,
            session_key=session_key,
            req=req,
            hints=operation_hints,
            parsed=None,
            digest=workbook_digest,
        )
        if recovery_slot is not None and not _operation_follow_up(recovery_slot):
            rows.append(_normalize_plan_or_empty(_operation_action_plan(recovery_slot)))
        return [row for row in rows if row]

    current_plan: list[PlanStep] | None = None
    bind_notes: list[dict[str, Any]] = []
    validation_error: Exception | None = None
    # 검증기는 value 누락 같은 빈 슬롯을 예외로 막으므로, 바인딩을 먼저 돌려 채운다.
    for candidate in [action_plan, *_recovery_plans()]:
        bound_candidate, candidate_notes = _bind_steps(candidate)
        try:
            current_plan = _validate_steps(bound_candidate)
            bind_notes = candidate_notes
            validation_error = None
            break
        except Exception as exc:
            validation_error = exc

    if current_plan is None:
        # 플랜까지 만들어졌다면 이미 Excel 명령으로 판정된 것이므로,
        # 파라미터가 부족하다는 이유로 500을 던지지 말고 되물어야 한다.
        # 규칙이 의도(수식/정렬/피벗...)를 알고 있다면 일반 되묻기 대신
        # 그 작업에 필요한 값을 콕 집어 묻고, 답을 이어받을 슬롯도 남긴다.
        if pending_operation is None and operation_intent not in {"", "general"}:
            intent_slot = _merge_operation_slots(
                None,
                session_key=session_key,
                req=req,
                hints=operation_hints,
                parsed=None,
                digest=workbook_digest,
            )
            intent_follow_up = _operation_follow_up(intent_slot) if intent_slot else ""
            if intent_slot is not None and intent_follow_up:
                _pending_operation_slots[session_key] = intent_slot
                return ExcelLiveActionResponse(
                    ok=True,
                    action=f"excel_live.{intent_slot.intent}",
                    reason=intent_follow_up,
                    result={
                        "ask_follow_up": True,
                        "follow_up_question": intent_follow_up,
                        "slot_state": dict(intent_slot.params),
                        "operation_intent": intent_slot.intent,
                        "validation_error": str(validation_error),
                    },
                )
        follow = _build_generic_excel_follow_up(req.message)
        if _looks_like_excel_request(req.message) or action_plan:
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=follow,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": follow,
                    "operation_intent": operation_hints.get("intent") or "clarify",
                    "validation_error": str(validation_error),
                },
            )
        raise _map_error(validation_error or ValueError("계획 검증 실패"))

    # 기준 열을 못 정했는데 그대로 실행하면 엉뚱한 열로 정렬/삭제된다.
    # 이럴 때는 추측하지 말고 멀티턴 슬롯으로 넘겨 되묻는다.
    unresolved_pairs = {
        (str(note.get("action")), str(note.get("slot")))
        for note in bind_notes
        if note.get("status") == "unresolved"
    }
    # "완료 행만 남기고 담당자별 합계를 피벗으로" — 작은 모델은 이런 연쇄 지시의 첫 단계만 계획한다.
    # 원문이 분명히 요청한 피벗이 빠졌다면 규칙으로 채워 넣는다(기준 열을 확정할 수 있을 때만).
    if (
        pending_operation is None
        and _ACTION_EVIDENCE["excel_live.pivot_table"].search(req.message)
        and not any(step.action == "excel_live.pivot_table" for step in current_plan)
    ):
        pivot_raw = _pivot_step_from_message(req.message, workbook_digest, sheet_name=req.sheet_name)
        if pivot_raw:
            extra = _bind_and_validate(_normalize_plan_or_empty([pivot_raw]))
            if extra:
                current_plan = current_plan + extra

    # 차트 종류는 결과물의 성격 자체를 바꾼다. 원문에 없으면 기본값(선)으로 밀지 않고 물어본다.
    # 단 "필터 → 피벗 → 차트"처럼 차트가 파이프라인의 마지막 단계일 뿐이라면
    # 여기서 멈춰 세우면 앞 단계 작업까지 통째로 미뤄지므로 그냥 진행한다.
    chart_only = {step.action for step in current_plan if step.action in _ACTION_EVIDENCE} == {
        "excel_live.create_chart"
    }
    if (
        pending_operation is None
        and chart_only
        and not _CHART_TYPE_MENTION.search(req.message)
    ):
        unresolved_pairs = unresolved_pairs | {("excel_live.create_chart", "chart_type")}

    if pending_operation is None and unresolved_pairs & _AMBIGUITY_SENSITIVE_SLOTS:
        ambiguous_action = next(
            action for action, slot in unresolved_pairs if (action, slot) in _AMBIGUITY_SENSITIVE_SLOTS
        )
        ambiguity_slot = _merge_operation_slots(
            None,
            session_key=session_key,
            req=req,
            hints={
                "intent": _action_to_operation_intent(ambiguous_action),
                "params": dict(operation_hints.get("params", {})),
            },
            parsed=None,
        )
        follow_up = _operation_follow_up(ambiguity_slot) if ambiguity_slot else ""
        if ambiguity_slot is not None and follow_up:
            _pending_operation_slots[session_key] = ambiguity_slot
            return ExcelLiveActionResponse(
                ok=True,
                action=f"excel_live.{ambiguity_slot.intent}",
                reason=follow_up,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": follow_up,
                    "slot_state": dict(ambiguity_slot.params),
                    "operation_intent": ambiguity_slot.intent,
                    "unresolved_slots": sorted(f"{a}.{s}" for a, s in unresolved_pairs),
                },
            )

    trace_note("plan_final", approve=req.approve, steps=trace_plan(current_plan))

    return await _execute_plan_and_respond(
        PlanExecution(
            req=req,
            plan=current_plan,
            session_key=session_key,
            parsed=parsed or {},
            base_context=base_context,
            personalization_hint=personalization_hint,
            bind_notes=bind_notes,
            reasoning_mode=reasoning_mode,
            reasoning_complexity_score=reasoning_complexity_score,
            reflection_attempted=reflection_attempted,
            reflection_applied=reflection_applied,
            reflection_reason=reflection_reason,
            approved=bool(req.approve),
        ),
        llm,
    )


def _plan_approval_gate(ctx: PlanExecution, plan: list[PlanStep]) -> ExcelLiveActionResponse | None:
    """계획에 승인이 필요한 단계가 있으면 **계획 전체**를 승인 대기로 돌린다.

    예전에는 첫 CONFIRM 단계 하나만 저장하고 나머지를 버렸다. 쓰기 계열 액션이
    전부 CONFIRM이라, 다단계 계획은 승인 직후 첫 단계만 실행되고 끝났다.
    승인 대상은 단계가 아니라 계획이므로 CONFIRM 단계를 한 번에 모아 보여주고,
    승인되면 같은 실행 루프로 이어 붙인다.
    """
    req = ctx.req
    for step in plan:
        tool_def = get_tool(step.action)
        if tool_def and tool_def.permission == PermissionLevel.DENIED:
            return ExcelLiveActionResponse(
                ok=False,
                action=step.action,
                reason="보안 정책에 의해 거부된 작업입니다.",
            )

    if ctx.approved:
        return None

    confirm_steps = [
        step
        for step in plan
        if (tool := get_tool(step.action)) and tool.permission == PermissionLevel.CONFIRM
    ]
    if not confirm_steps:
        return None

    head = confirm_steps[0]
    target_problem = _edit_target_problem(
        req.workbook_id, head.params.get("sheet_name") or req.sheet_name
    )
    if target_problem:
        trace_note("target_missing", action=head.action, detail=target_problem)
        return ExcelLiveActionResponse(
            ok=True,
            action="excel_live.clarify",
            reason=target_problem,
            result={
                "ask_follow_up": True,
                "follow_up_question": target_problem,
                "operation_intent": "clarify",
                "missing_slot": "sheet_name",
                "blocked_action": head.action,
            },
        )

    pending = _build_approval(head.action, head.params)
    if len(plan) > 1:
        # 첫 단계만 보여주면 사용자는 나머지를 모른 채 승인한다. 승인 대상이
        # 계획 전체가 된 이상, 다이얼로그도 계획 전체를 보여줘야 한다.
        steps_text = "\n".join(
            f"{idx}. {_action_summary(step.action)}" for idx, step in enumerate(plan, start=1)
        )
        pending.summary = f"다음 {len(plan)}단계를 실행합니다.\n{steps_text}"
    _pending_approvals[pending.approval_id] = PendingExcelApproval(
        action=head.action,
        params=head.params,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        created_at=pending.created_at,
        resume=replace(ctx, plan=list(plan), approved=True),
    )
    trace_route(
        "approval:required",
        why=f"CONFIRM {len(confirm_steps)}단계 — 계획 {len(plan)}단계를 통째로 보관",
        action=head.action,
        planned_steps=len(plan),
    )
    return ExcelLiveActionResponse(
        ok=True,
        action=head.action,
        approval_required=True,
        pending_approval=pending,
        reason=head.reason or "승인이 필요한 작업입니다.",
    )


async def _execute_plan_and_respond(
    ctx: PlanExecution,
    llm: LLMService,
) -> ExcelLiveActionResponse:
    """확정된 계획을 실행하고 응답을 만든다.

    `/command`와 `/approval`이 **같은 함수**를 탄다. 승인 경로가 이 루프를 우회하던
    시절에는 검증(`_verify_step_result`)도 스냅샷 롤백도 재계획도 지나가지 않아,
    실행기가 거짓말해도 틀린 값이 파일에 그대로 남았다.
    """
    req = ctx.req
    session_key = ctx.session_key
    parsed = ctx.parsed
    base_context = ctx.base_context
    personalization_hint = ctx.personalization_hint
    bind_notes = ctx.bind_notes
    reasoning_mode = ctx.reasoning_mode
    reasoning_complexity_score = ctx.reasoning_complexity_score
    reflection_attempted = ctx.reflection_attempted
    reflection_applied = ctx.reflection_applied
    reflection_reason = ctx.reflection_reason
    current_plan = list(ctx.plan)

    execution = None
    replan_count = 0
    max_replans = 1
    recovery_backup_info: dict[str, Any] | None = None
    rollback_events: list[dict[str, Any]] = []
    queue_wait_total_ms = 0
    snapshot_holder: dict[str, ActionRollbackSnapshot | None] = {"current": None}
    while True:
        gate = _plan_approval_gate(ctx, current_plan)
        if gate is not None:
            return gate

        try:
            def _guarded_execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
                snapshot_holder["current"] = _snapshot_target_range_for_action(
                    action=action,
                    params=params,
                    workbook_id=req.workbook_id,
                    sheet_name=req.sheet_name,
                )
                try:
                    raw_result = _execute_action(
                        action=action,
                        params=params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                    if action == "excel_live.pivot_table":
                        produced = str((raw_result or {}).get("sheet_name") or "").strip()
                        if produced:
                            _last_aggregate_sheet[session_key] = produced
                    return _append_xlwings_trace(
                        action=action,
                        params=params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                        result=raw_result,
                    )
                except Exception as exc:
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
                    trace_route(
                        "execute:error",
                        why=f"{type(exc).__name__}: {exc}",
                        action=action,
                        rolled_back=bool(restored),
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
                    trace_route(
                        "verify:failed",
                        why=str(detail or "사후조건 불일치"),
                        action=action,
                        rolled_back=bool(restored),
                    )
                return bool(is_ok), str(detail or "")

            def _run_execute_once():
                nonlocal recovery_backup_info
                if recovery_backup_info is None and _plan_needs_recovery_backup(current_plan):
                    recovery_backup_info = _create_recovery_backup_if_possible(
                        workbook_id=req.workbook_id,
                        label="command",
                    )
                return execute_plan(
                    steps=_chain_chart_to_pivot(
                        _drop_trailing_verification(_drop_table_step_when_aggregating(current_plan)),
                        session_key=session_key,
                    ),
                    execute_action=_guarded_execute,
                    verify_step=_guarded_verify,
                    max_attempts=2,
                    abort_on_failure=True,
                    reraise=(AmbiguousWorkbookError,),
                )

            execution, queue_wait_ms = _run_in_excel_queue("command-plan", _run_execute_once)
            queue_wait_total_ms += queue_wait_ms
            # 파일이 바뀌었으므로 다음 턴이 낡은 다이제스트를 보지 않게 한다.
            invalidate_digest_cache(req.workbook_id)
        except AmbiguousWorkbookError as exc:
            # 대상을 못 정한 건 실패가 아니라 정보 부족이다. 404로 끊으면 사용자는
            # 무엇을 더 말해야 하는지 알 수 없으므로 후보를 들고 되묻는다.
            return _ambiguous_workbook_response(exc)
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
        replan_context["personalization_hint"] = personalization_hint
        trace_route(
            f"replan:{replan_count}",
            why=str(replan_context.get("failed_error") or "직전 단계가 검증을 통과하지 못함"),
            failed_action=replan_context.get("failed_action", ""),
            failed_args=replan_context.get("failed_args", {}),
        )
        # 재계획은 이미 일부 실행된 뒤이므로 파일 상태를 다시 읽어 최신 다이제스트를 준다.
        invalidate_digest_cache(req.workbook_id)
        replan_digest = build_workbook_digest(
            get_excel_live_service(),
            workbook_id=req.workbook_id,
            active_sheet_hint=req.sheet_name,
        )
        replan_context["workbook_digest"] = replan_digest
        replan_context["workbook_digest_text"] = render_workbook_digest(replan_digest)
        try:
            replanned = await parse_command_plan_with_llm(
                req.message,
                llm,
                context=replan_context,
                forbid_list_action=True,
                require_edit_action=True,
            )
            replan_steps, _ = _bind_plan_for_request(
                _normalize_plan_or_empty(replanned.get("action_plan")),
                digest=replan_digest,
                req=req,
            )
            current_plan = _validate_plan_for_request(replan_steps, req)
            parsed["reason"] = replanned.get("reason", "") or parsed.get("reason", "")
        except Exception as exc:
            # 재계획은 이미 한 번 실행한 뒤의 보정 시도다. 여기서 실패했다고 400을 던지면
            # 첫 실행 결과까지 사라지므로, 원래 실행 결과를 그대로 보고하고 끝낸다.
            last_step = execution.last if execution else None
            if last_step is not None:
                last_step.verify_detail = (
                    f"{last_step.verify_detail or ''};replan_failed:{exc}".strip(";")
                )
            break

    trace_note(
        "executed",
        replans=replan_count,
        steps=[
            {
                "index": s.index,
                "action": s.action,
                "ok": not s.error,
                "verified": s.verified,
                "retried": s.retried,
                "error": s.error or "",
                "verify_detail": s.verify_detail or "",
                "result": {
                    key: (s.result or {}).get(key)
                    for key in ("address", "written_cells", "applied_cells", "rows", "matched_cells")
                    if isinstance(s.result, dict) and key in s.result
                },
            }
            for s in (execution.steps if execution else [])
        ],
    )

    if execution is None or not execution.steps:
        raise HTTPException(status_code=400, detail="실행 가능한 계획(step)을 생성하지 못했습니다.")

    last = execution.last
    if last is None:
        raise HTTPException(status_code=400, detail="실행 결과를 생성하지 못했습니다.")
    # 계획 끝에 붙는 저장/조회 단계가 "무슨 작업을 했는지"를 가리면 안 된다.
    # 사용자와 로그가 보는 액션은 실제 편집을 한 마지막 단계여야 한다.
    primary = next(
        (s for s in reversed(execution.steps) if s.action not in _ANCILLARY_REPORT_ACTIONS),
        last,
    )
    last_result = dict(primary.result or {})
    if primary is not last:
        last_result["closing_action"] = last.action
    execution_xlwings_ops = _collect_xlwings_ops_from_execution(execution)
    if execution_xlwings_ops:
        last_result["xlwings_ops"] = execution_xlwings_ops
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
    if bind_notes:
        last_result["param_bindings"] = bind_notes
    reasoning_profile = {
        "mode": reasoning_mode,
        "complexity_score": reasoning_complexity_score,
        "reflection_attempted": reflection_attempted,
        "reflection_applied": reflection_applied,
    }
    if reflection_reason:
        reasoning_profile["reflection_reason"] = reflection_reason
    last_result["reasoning_profile"] = reasoning_profile

    if last.error or not last.verified:
        failure_detail = last.error or last.verify_detail or "unknown_failure"
        return ExcelLiveActionResponse(
            ok=False,
            action=last.action,
            reason=_verification_failure_reason(failure_detail),
            result={
                "failed_action": last.action,
                "failed_step_index": last.index,
                "failure_detail": failure_detail,
                "executed_steps": len(execution.steps),
                "auto_rollbacks": list(rollback_events),
                "recovery_backup": recovery_backup_info,
                "queue_wait_ms": queue_wait_total_ms,
                "reasoning_profile": reasoning_profile,
                "xlwings_ops": execution_xlwings_ops,
                # 실패 원인은 대개 계획 자체에 있다. 어떤 단계였는지 남겨야 재현이 된다.
                "planned_steps": [
                    {"action": step.action, "params": dict(step.params or {})} for step in current_plan
                ],
            },
        )

    # 수식 적용 후 검증 단계에서 숫자 결과가 0개면 자동 재시도 1회를 수행한다.
    # 재시도에도 개선이 없을 때만 follow-up 질문으로 전환한다.
    has_formula_step = any(s.action == "excel_live.set_formula" for s in execution.steps)
    if (
        has_formula_step
        and primary.action == "excel_live.verify_formula_result"
        and int(last_result.get("numeric_cells", 0) or 0) == 0
    ):
        formula_step = next((s for s in execution.steps if s.action == "excel_live.set_formula"), None)
        formula_a1 = str((formula_step.params or {}).get("formula_a1", "")) if formula_step else ""
        retry_formula = _build_formula_retry_variant(formula_a1)
        if retry_formula and _is_numeric_formula_candidate(formula_a1):
            try:
                def _run_formula_retry_once():
                    retry_set_params = {
                        "range_ref": str((formula_step.params or {}).get("range_ref", "__ACTIVE_SELECTION__")),
                        "formula_a1": retry_formula,
                    }
                    retry_set_raw = _execute_action(
                        action="excel_live.set_formula",
                        params=retry_set_params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                    retry_set_local = _append_xlwings_trace(
                        action="excel_live.set_formula",
                        params=retry_set_params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                        result=retry_set_raw,
                    )
                    retry_verify_params = {
                        "range_ref": str((formula_step.params or {}).get("range_ref", "__ACTIVE_SELECTION__"))
                    }
                    retry_verify_raw = _execute_action(
                        action="excel_live.verify_formula_result",
                        params=retry_verify_params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                    )
                    retry_verify_local = _append_xlwings_trace(
                        action="excel_live.verify_formula_result",
                        params=retry_verify_params,
                        workbook_id=req.workbook_id,
                        sheet_name=req.sheet_name,
                        result=retry_verify_raw,
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
                    retry_verify["reasoning_profile"] = reasoning_profile
                    retry_ops = [row for row in retry_verify.get("xlwings_ops", []) if isinstance(row, dict)]
                    if execution_xlwings_ops:
                        retry_verify["xlwings_ops"] = execution_xlwings_ops + retry_ops
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
                "reasoning_profile": reasoning_profile,
                "xlwings_ops": execution_xlwings_ops,
            },
        )

    return ExcelLiveActionResponse(
        ok=True,
        action=primary.action,
        result=last_result,
        reason=parsed.get("reason", "") or primary.reason,
    )


@router.post("/approval", response_model=ExcelLiveActionResponse)
async def post_approval(
    req: ApprovalResponse,
    llm: LLMService = Depends(get_llm_service),
):
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

    if pending.resume is not None:
        # 명령 경로가 세운 계획을 그대로 이어서 실행한다. 재계획하지 않으므로
        # 사용자가 승인한 계획과 실행되는 계획이 같다.
        _audit.log(
            action="excel.live.approval.executed",
            target=pending.action,
            detail=f"approval_id={req.approval_id} steps={len(pending.resume.plan)}",
        )
        with turn_scope(
            endpoint="excel-live/approval",
            message=pending.resume.req.message,
            session_id=pending.resume.session_key,
            request={
                "approval_id": req.approval_id,
                "workbook_id": pending.workbook_id,
                "sheet_name": pending.sheet_name,
                "planned_steps": len(pending.resume.plan),
            },
        ):
            trace_route(
                "approval:resumed",
                why=f"승인된 계획 {len(pending.resume.plan)}단계를 이어서 실행",
                action=pending.action,
            )
            response = await _execute_plan_and_respond(pending.resume, llm)
            set_outcome_from_response(response)
            return response

    # 단일 액션 승인(`/action` 경로). 계획이 없으므로 예전처럼 한 단계만 실행한다.
    recovery_backup: dict[str, Any] | None = None
    try:
        def _run_approval_once():
            nonlocal recovery_backup
            recovery_backup = (
                _create_recovery_backup_if_possible(workbook_id=pending.workbook_id, label="approval")
                if _action_needs_recovery_backup(pending.action)
                else None
            )
            raw_result = _execute_action(
                action=pending.action,
                params=pending.params,
                workbook_id=pending.workbook_id,
                sheet_name=pending.sheet_name,
            )
            return _append_xlwings_trace(
                action=pending.action,
                params=pending.params,
                workbook_id=pending.workbook_id,
                sheet_name=pending.sheet_name,
                result=raw_result,
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
    except (WorkbookNotFoundError, WorksheetNotFoundError) as exc:
        # 승인까지 누른 사용자에게 404를 던지면 "요청한 정보를 찾을 수 없습니다"만 남는다.
        # 무엇이 없어서 못 했는지, 무엇을 알려주면 되는지 문장으로 돌려준다.
        detail = f"{exc} 작업할 파일과 시트를 알려주시면 다시 진행하겠습니다."
        _audit.log(
            action="excel.live.approval.target_missing",
            target=pending.action,
            detail=f"approval_id={req.approval_id} {exc}",
        )
        return ExcelLiveActionResponse(
            ok=False,
            action=pending.action,
            reason=detail,
            result={
                "ask_follow_up": True,
                "follow_up_question": detail,
                "operation_intent": "clarify",
                "missing_slot": "sheet_name",
                "attempted_sheet": pending.sheet_name or "",
                "attempted_workbook": pending.workbook_id or "",
            },
        )
    except Exception as exc:
        mapped = _map_error(exc)
        if recovery_backup and recovery_backup.get("backup_path"):
            mapped.detail = f"{mapped.detail} (복구 백업: {recovery_backup.get('backup_path')})"
        raise mapped


def _apply_macro_skips(run: MacroRun, skip_indices: list[int]) -> None:
    """승인 화면에서 체크를 해제한 항목을 실행 대상에서 뺀다."""
    wanted = {int(value) for value in skip_indices or [] if isinstance(value, (int, float))}
    if not wanted:
        return
    for step in run.steps:
        if step.index in wanted:
            step.status = "skipped"
            step.detail = "사용자가 제외함"


def _clear_macro_session_slots(run: MacroRun) -> None:
    """매크로 전용 세션에 남은 되묻기 슬롯을 치운다."""
    _pending_create_table_slots.pop(run.step_session_id, None)
    _pending_operation_slots.pop(run.step_session_id, None)


def _macro_step_response(
    run: MacroRun,
    *,
    reason: str,
    ok: bool = True,
    step_result: dict[str, Any] | None = None,
) -> ExcelLiveActionResponse:
    payload = _macro_snapshot(run)
    payload["step_result"] = step_result
    if run.status == "waiting_input" and run.follow_up_question:
        payload["ask_follow_up"] = True
        payload["follow_up_question"] = run.follow_up_question
    run.updated_at_ts = time.time()
    return ExcelLiveActionResponse(
        ok=ok,
        action="excel_live.macro_step",
        reason=reason,
        result=payload,
    )


def _next_macro_step(run: MacroRun) -> MacroStepState | None:
    """아직 실행하지 않은 첫 단계로 커서를 옮기고 그 단계를 돌려준다."""
    while run.cursor < len(run.steps) and run.steps[run.cursor].status in {
        "done",
        "skipped",
        "failed",
    }:
        run.cursor += 1
    if run.cursor >= len(run.steps):
        return None
    return run.steps[run.cursor]


@router.post("/macro/step", response_model=ExcelLiveActionResponse)
async def post_macro_step(
    req: ExcelLiveMacroStepRequest,
    llm: LLMService = Depends(get_llm_service),
):
    """
    승인된 매크로를 한 단계 진행한다.

    한 요청으로 전부 돌리지 않는 이유는 두 가지다. 18단계면 30초를 훌쩍 넘겨 프론트
    타임아웃에 걸리고, 진행률을 보여줄 방법도 없다.
    """
    _cleanup_expired_macro_runs()
    run = _macro_runs.get(req.macro_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="매크로 실행 정보를 찾을 수 없습니다. 요청을 다시 보내 주세요.",
        )
    if run.status in {"done", "aborted"}:
        return _macro_step_response(run, reason="이미 종료된 매크로입니다.")

    if run.status == "planned":
        # 첫 호출이 곧 사용자의 승인이다. 되돌릴 기준점은 이때 한 번만 뜬다.
        _apply_macro_skips(run, req.skip_indices)
        run.backup, _ = _run_in_excel_queue(
            "macro-backup",
            lambda: _create_recovery_backup_if_possible(
                workbook_id=run.workbook_id,
                label="macro",
            ),
        )
        run.status = "running"
        _audit.log(
            action="excel.live.macro.approved",
            target=run.macro_id,
            detail=f"steps={len(run.steps)} skipped={len(req.skip_indices or [])}",
        )

    answer = str(req.answer or "").strip()
    if run.status in {"waiting_input", "halted"}:
        if req.skip_current:
            current = run.steps[run.cursor] if run.cursor < len(run.steps) else None
            if current is not None and current.status == "pending":
                current.status = "skipped"
                current.detail = "사용자가 건너뜀"
            run.cursor += 1
            run.follow_up_question = ""
            run.status = "running"
        elif answer:
            run.follow_up_question = ""
            run.status = "running"
        else:
            # 답도 없고 건너뛰라고도 하지 않았다 — 상태만 다시 알려준다.
            return _macro_step_response(
                run,
                reason=run.follow_up_question or "멈춰 있습니다. 이어서 할지 되돌릴지 알려주세요.",
                ok=run.status != "halted",
            )

    step = _next_macro_step(run)
    if step is None:
        run.status = "done"
        _clear_macro_session_slots(run)
        done = sum(1 for item in run.steps if item.status == "done")
        _audit.log(
            action="excel.live.macro.completed",
            target=run.macro_id,
            detail=f"done={done}/{len(run.steps)}",
        )
        return _macro_step_response(run, reason=f"{done}단계를 마쳤습니다.")

    sub_request = ExcelLiveCommandRequest(
        message=answer or step.command,
        user_id=run.user_id,
        workbook_id=run.workbook_id,
        sheet_name=run.sheet_name,
        session_id=run.step_session_id,
        # 매크로 승인 한 번이 하위 명령 전체의 승인이다.
        approve=True,
    )
    try:
        response = await post_command(sub_request, llm)
    except HTTPException as exc:
        step.status = "failed"
        step.detail = str(exc.detail)
        run.status = "halted"
        _audit.log(
            action="excel.live.macro.step_failed",
            target=run.macro_id,
            detail=f"index={step.index} detail={step.detail[:120]}",
        )
        return _macro_step_response(
            run,
            reason=f"{step.index}단계에서 멈췄습니다: {step.detail}",
            ok=False,
        )

    result = response.result if isinstance(response.result, dict) else {}
    step.action = str(response.action or "")

    if result.get("ask_follow_up"):
        question = str(result.get("follow_up_question") or response.reason or "").strip()
        run.status = "waiting_input"
        run.follow_up_question = question
        return _macro_step_response(
            run,
            reason=question or "추가 정보가 필요합니다.",
            step_result=result,
        )

    if not response.ok:
        step.status = "failed"
        step.detail = str(response.reason or "실행에 실패했습니다.")
        run.status = "halted"
        return _macro_step_response(
            run,
            reason=f"{step.index}단계에서 멈췄습니다: {step.detail}",
            ok=False,
            step_result=result,
        )

    step.status = "done"
    step.detail = str(response.reason or "")
    run.cursor += 1
    if _next_macro_step(run) is None:
        run.status = "done"
        _clear_macro_session_slots(run)
        done = sum(1 for item in run.steps if item.status == "done")
        _audit.log(
            action="excel.live.macro.completed",
            target=run.macro_id,
            detail=f"done={done}/{len(run.steps)}",
        )
        return _macro_step_response(run, reason=f"{done}단계를 마쳤습니다.", step_result=result)

    return _macro_step_response(
        run,
        reason=f"{step.index}/{len(run.steps)} 완료",
        step_result=result,
    )


@router.post("/macro/abort", response_model=ExcelLiveActionResponse)
def post_macro_abort(req: ExcelLiveMacroAbortRequest):
    """매크로를 중단한다. rollback이면 시작 시점 백업으로 되돌린다."""
    _cleanup_expired_macro_runs()
    run = _macro_runs.get(req.macro_id)
    if run is None:
        raise HTTPException(status_code=404, detail="매크로 실행 정보를 찾을 수 없습니다.")

    restored: dict[str, Any] | None = None
    if req.rollback:
        backup_path = str((run.backup or {}).get("backup_path") or "")
        if not backup_path:
            raise HTTPException(
                status_code=400,
                detail="되돌릴 매크로 백업이 없습니다. 개별 작업 백업으로 복구해 주세요.",
            )
        service = get_excel_live_service()
        try:
            def _run_macro_rollback():
                resolved_wb = _resolve_workbook_id(service, run.workbook_id)
                return service.restore_workbook_from_backup(resolved_wb, backup_path=backup_path)

            restored, _ = _run_in_excel_queue("macro-rollback", _run_macro_rollback)
            invalidate_digest_cache(run.workbook_id)
        except Exception as exc:
            raise _map_error(exc)

    run.status = "aborted"
    snapshot = _macro_snapshot(run)
    snapshot["rolled_back"] = bool(req.rollback)
    snapshot["restore_result"] = restored
    _clear_macro_session_slots(run)
    _macro_runs.pop(run.macro_id, None)
    _audit.log(
        action="excel.live.macro.aborted",
        target=run.macro_id,
        detail=f"rollback={bool(req.rollback)}",
    )
    return ExcelLiveActionResponse(
        ok=True,
        action="excel_live.macro_abort",
        reason="되돌렸습니다." if req.rollback else "여기서 멈췄습니다.",
        result=snapshot,
    )

