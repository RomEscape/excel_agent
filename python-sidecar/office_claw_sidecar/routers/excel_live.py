"""Excel Live 라우터 — 자연어 기반 실시간 Excel(COM) 제어 API."""

from __future__ import annotations

import asyncio
import contextvars
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from office_claw_sidecar.models.approval import ApprovalRequest, ApprovalResponse
from office_claw_sidecar.services import excel_observation
from office_claw_sidecar.services import excel_rank_limit as rank_limit
from office_claw_sidecar.services.aggregate_lexicon import AGG_FUNC, AGG_WORD_PATTERN
from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.chat_routing_guard import (
    CRISIS_REPLY,
    classify_off_topic,
    detect_crisis_intent,
)
from office_claw_sidecar.services.color_lexicon import COLOR_TOKEN_PATTERN, color_hex
from office_claw_sidecar.services.decision_trace import (
    Long,
    set_outcome_from_response,
    turn_scope,
)
from office_claw_sidecar.services.decision_trace import (
    note as trace_note,
)
from office_claw_sidecar.services.decision_trace import (
    origin as trace_origin,
)
from office_claw_sidecar.services.decision_trace import (
    plan_summary as trace_plan,
)
from office_claw_sidecar.services.decision_trace import (
    route as trace_route,
)
from office_claw_sidecar.services.excel_actions import execute_excel_action
from office_claw_sidecar.services.excel_aggregate_below import (
    _norm_header,
    build_aggregate_below_plan,
    build_cross_sheet_aggregate_plan,
    match_aggregate_below,
    match_aggregate_columns,
)
from office_claw_sidecar.services.excel_correction_context import (
    build_below_formula_plan,
    build_correction_plan,
)
from office_claw_sidecar.services.excel_correction_context import (
    recall_formula as recall_last_formula,
)
from office_claw_sidecar.services.excel_correction_context import (
    recall_write as recall_last_write,
)
from office_claw_sidecar.services.excel_correction_context import (
    record_formula as record_last_formula,
)
from office_claw_sidecar.services.excel_correction_context import (
    record_write as record_last_write,
)
from office_claw_sidecar.services.excel_edit_precheck import (
    ExcelEditBlockedError,
    evaluate_write_block,
    read_protection_flags,
)
from office_claw_sidecar.services.excel_header_lexicon import (
    find_header_mentions,
    resolve_header,
)
from office_claw_sidecar.services.excel_live_agent import (
    _ROW_WRITE_FORMAT_VOCAB,
    COLUMN_LETTER_PATTERN,
    RANGE_REF_PATTERN,
    clarify_question_from_plan,
    extract_create_table_slot_hints,
    extract_font_params,
    looks_like_existing_table_convert,
    normalize_common_typos,
    parse_cell_arithmetic_write,
    parse_command_plan_with_llm,
    parse_command_rule_based,
    parse_cross_sheet_cell_ref,
    parse_excel_live_command,
    parse_explicit_row_write,
    parse_rangeless_row_write,
    parse_text_equals_condition,
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
from office_claw_sidecar.services.excel_live_service import (
    AmbiguousWorkbookError,
    ExcelConnectionError,
    ExcelDependencyError,
    ExcelLiveError,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
    get_excel_live_service,
    invalidate_excel_engine_cache,
)
from office_claw_sidecar.services.excel_live_table_presets import (
    find_variant,
    get_table_preset,
)
from office_claw_sidecar.services.excel_macro_coverage import parse_rect, rect_to_ref
from office_claw_sidecar.services.excel_macro_planner import (
    MacroStepPlan,
    decompose_macro_request,
    looks_like_macro_request,
)
from office_claw_sidecar.services.excel_param_binder import (
    _retarget_sheet_by_headers,
    bind_plan_steps,
    explicit_sheet_mention_variants,
    extend_sheet_name_leftward,
    resolve_sheet_from_message,
    sheet_entry,
    sheet_mention_matches_known,
)
from office_claw_sidecar.services.excel_plan_sanity import check_plan_sanity, worst_severity
from office_claw_sidecar.services.excel_planner_escalation import (
    EscalationResult,
    plan_with_escalation,
    record_escalation,
)
from office_claw_sidecar.services.excel_planner_prompt import (
    render_conversation_history,
)
from office_claw_sidecar.services.excel_readonly_bridge import (
    can_bridge,
    release_workbook,
    restore_workbook,
)
from office_claw_sidecar.services.excel_readonly_bridge import (
    looks_like_com_write_refusal as _looks_like_com_write_refusal,
)
from office_claw_sidecar.services.excel_result_verifier import verify_effect
from office_claw_sidecar.services.excel_selection_context import (
    decide_selection_source,
    mentions_selection,
    resolve_context_range,
)
from office_claw_sidecar.services.excel_step_repair import RepairContext, repair_step
from office_claw_sidecar.services.excel_table_region import expand_to_table_region
from office_claw_sidecar.services.excel_workbook_digest import (
    build_workbook_digest,
    invalidate_digest_cache,
    invalidate_workbook_digest,
    render_workbook_digest,
)
from office_claw_sidecar.services.excel_write_scope import assess as assess_write_scope
from office_claw_sidecar.services.excel_write_scope import assess_sort_integrity
from office_claw_sidecar.services.korean_number import (
    parse_condition as parse_korean_condition,
)
from office_claw_sidecar.services.llm_service import (
    LLMService,
    get_llm_service,
    get_macro_model_name,
    get_planner_model_name,
)
from office_claw_sidecar.services.number_format_lexicon import format_code
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
    # 프론트 쪽 사정 — 사용자가 실제로 친 원문(마크업 포함), 복합문 조각 번호, 붙여넣기
    # 범위, 라우팅 근거, 재시도 번호. 사이드카 판단에는 쓰지 않고 **로그에만** 남긴다.
    client: dict[str, Any] | None = Field(None, description="프론트 문맥(로그 전용)")


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
    # 해석 카드("이렇게 이해했어요")였는지 — 거절 로그에서 규칙 실행 취소와 모델 오해를 가른다.
    interpretation: bool = False


@dataclass
class PendingCreateTableSlots:
    session_id: str
    workbook_id: str | None
    sheet_name: str | None
    # 사용자가 문장에서 직접 지목한 시트. `sheet_name`은 매 턴 활성 시트로 덮이므로
    # 여기에 따로 붙들어 둔다. 되묻기 다음 턴("3행 3열")에는 시트 언급이 없어서,
    # 이게 없으면 "정산 시트에 표 만들어줘"의 정산이 사라지고 활성 시트를 덮어썼다.
    explicit_sheet_name: str | None = None
    rows: int | None = None
    cols: int | None = None
    headers: list[str] | None = None
    values_2d: list[list[Any]] | None = None
    start_cell: str | None = None
    template_key: str | None = None
    template_follow_up_question: str | None = None
    # 템플릿 질문은 **한 번만** 한다. 2026-08-17 실측: "일별"이라는 답을 해석하는
    # 코드가 없어 같은 질문("일별/월별 중 어떤 형식으로?")을 되풀이했다.
    template_question_asked: bool = False
    # 사용자가 문장으로 직접 지정한 헤더인가. 2026-08-18 GUI 실측: "날짜, 이름,
    # 출석 여부, 비고"라고 지정했는데 다음 턴의 "일별로"가 프리셋 헤더(출근 시간…)
    # 로 덮었다. 사용자 지정 헤더는 프리셋 선택지가 이기지 못한다.
    headers_from_user: bool = False
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
    # 직전에 물은 질문. 같은 질문을 또 하게 되면 답을 해석 못 한 것이므로
    # 슬롯을 버린다 — 제자리 도는 대화가 최악이다(2026-08-18 GUI 실측).
    last_question: str = ""


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
#
# 2026-08-16: 90 -> 180. 상한을 45단계로 올리고 열이 13개인 통합문서로 재 보니
# 3회 중 1회만 성공했다 — 1회는 90초 타임아웃, 1회는 출력이 잘려 JSON 파싱 실패였다.
# 45단계짜리 한국어 문장 목록은 20단계의 배가 넘는 토큰을 뱉는다.
# 폴백이 더 나쁘다는 점이 중요하다 — 매크로를 포기하면 플래너가 "차트 종류를
# 선택해 주세요" 같은 엉뚱한 되묻기를 낸다. 기다리는 편이 낫다.
_MACRO_DECOMPOSE_TIMEOUT_SECONDS = _env_float("EXCEL_LIVE_MACRO_TIMEOUT_SECONDS", 180.0, 5.0)
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
# 프론트 210s·Rust 200s보다 짧게 — 이보다 오래 걸린 턴은 사용자가 이미 실패를 봤다(로그 표시용).
_CLIENT_BUDGET_SECONDS = 195.0
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
    "excel_live.set_font": re.compile(r"(굵게|볼드|bold|글꼴|폰트|글자색)", re.IGNORECASE),
    "excel_live.convert_to_excel_table": re.compile(
        r"(엑셀\s*표|테이블로|표로\s*변환|listobject)", re.IGNORECASE
    ),
    "excel_live.apply_formula_cf": re.compile(r"(조건부\s*서식|수식\s*조건부)", re.IGNORECASE),
    "excel_live.apply_data_bar": re.compile(r"(데이터\s*막대|data\s*bar)", re.IGNORECASE),
    "excel_live.apply_color_scale": re.compile(r"(색조|컬러\s*스케일|color\s*scale)", re.IGNORECASE),
    "excel_live.forecast_linear": re.compile(r"(예측|추세|forecast|전망)", re.IGNORECASE),
    "excel_live.compare_ranges": re.compile(r"(비교|차이|diff|대조)", re.IGNORECASE),
    # 통합은 **시트·파일을** 하나로 합치는 것이다. 맨 동사만 근거로 삼으면
    # "결석 몇 번인지 다 **합쳐서** A2에"까지 시트 통합으로 읽혀 "통합할 시트명을
    # 알려주세요"가 나간다(2026-08-20 파괴 게이트 실측). 대상 명사를 함께 요구한다.
    "excel_live.consolidate_sheets": re.compile(
        r"(?:시트|탭|파일|워크북|통합문서|sheet)\s*(?:들|를|을|이|가)?\s*[^\n]{0,10}?(?:통합|합쳐|합치|병합|모아)"
        r"|(?:통합|합쳐|합치|병합|모아)\s*[^\n]{0,10}?(?:시트|탭|파일|워크북|통합문서|sheet)"
        r"|여러\s*(?:시트|탭|파일)",
        re.IGNORECASE,
    ),
    "excel_live.set_data_validation": re.compile(
        r"(드롭다운|유효성|목록|제한|validation|선택되도록)", re.IGNORECASE
    ),
    "excel_live.verify_formula_result": re.compile(r"(검증|확인|검사|verify|점검)", re.IGNORECASE),
    "excel_live.clear_range": re.compile(r"(지워|삭제|비워|clear|초기화)", re.IGNORECASE),
    "excel_live.rename_sheet": re.compile(
        r"(시트|탭|sheet).{0,24}(이름|바꿔|변경|rename)", re.IGNORECASE
    ),
    "excel_live.delete_sheet": re.compile(
        r"(시트|탭|sheet).{0,12}(삭제|제거|없애)", re.IGNORECASE
    ),
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


def _looks_like_format_code(code: str) -> bool:
    """표시 형식 코드처럼 생겼는가 — "소수점 둘째 자리" 같은 한국어 문장을 걸러낸다.

    2026-08-17 배터리 실측: 플래너가 format_code에 말 그대로 "소수점 둘째 자리"를
    넣었고, 그게 그대로 셀 서식이 됐다. 형식 코드에 한글이 들어갈 일은 없다.
    """
    text = str(code or "").strip()
    if not text or re.search(r"[가-힣]", text):
        return False
    # "comma"는 'mm'이 들어 있어 코드처럼 보였고, 셀 서식이 말 그대로 comma가
    # 됐다(2026-08-18 사람 말투 배터리 실측). 영어 낱말 별칭은 코드가 아니다.
    if text.lower() in {"comma", "thousand", "thousands", "number", "percent", "currency"}:
        return False
    return bool(re.search(r"[0#@%.,]|yy|mm|dd|hh|ss", text, re.IGNORECASE)) or text.lower() in {
        "general",
        "text",
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
    # sort_rows의 기준 열 슬롯은 "column"이다 — "key_column"으로 적혀 있어 되묻기 게이트가 한 번도 안 열렸고
    # "정렬 좀"이 모델이 지어낸 '이름' 열로 실행됐다(2026-08-19 ex9·15·19·21 실측, 바인더 쪽 같은 오기도 함께 고침).
    ("excel_live.sort_rows", "column"),
    ("excel_live.sort_rows", "key_column"),
    ("excel_live.dedupe_rows", "key_columns"),
    ("excel_live.create_chart", "chart_type"),
    ("excel_live.filter_rows", "column"),
    # 무엇을 쓸지 못 정한 채 쓰면 셀에 설명문이 들어간다.
    # 2026-08-17 실측: "가장 큰 매출 값 넣어줘"가 셀에 '가장 큰 매출'을 남겼다.
    # 바인더가 unresolved로 표시해도 이 목록에 없으면 그대로 실행됐다.
    ("excel_live.write_range", "values_2d"),
    # 바꿀 말이 비면 찾은 글자를 **지운다.** 2026-08-17 실측: "아니 부산으로 바꿔줘"가
    # find='부산' replace='' 로 와서 시트에 원래 있던 '부산'을 지우고 성공으로 보고했다.
    ("excel_live.find_replace", "replace_text"),
    # 뒤집힌 범위(=AVERAGE(A2:A1)) 같은 오염 수식. 2026-08-17 배터리 실측:
    # 이게 A1:A8에 적용돼 날짜 열이 통째로 덮였다.
    ("excel_live.set_formula", "formula_a1"),
}
# 2026-08-17 실측: "도넛 차트 만들어줘"에 "차트 종류를 선택해 주세요"로 되물었다.
# `_CHART_KIND_WORDS`(아래)는 도넛을 알아보는데 **이 패턴에만 빠져 있었다.**
# 이건 "종류를 말했는가"를 판정하는 곳이라, 여기 없으면 종류를 말해도 안 말한 것이
# 된다. 두 목록이 어긋나면 이런 식으로 조용히 되묻기가 된다 — 같이 고쳐야 한다.
_CHART_TYPE_MENTION = re.compile(
    r"(선\s*그래프|꺾은|라인|line|막대|bar|원형|파이|pie|원\s*그래프|원\s*차트|영역|area|분산|scatter"
    r"|도넛|도너츠|donut|doughnut|링\s*차트|바\s*차트|바\s*그래프"
    # 종류를 함의하는 낱말도 종류 언급이다 — _CHART_KIND_WORDS와 같이 간다.
    # "추이 그래프"가 종류 질문으로 새던 문제(2026-08-18 배터리 실측).
    r"|추이|트렌드|비중|비율|구성비)",
    re.IGNORECASE,
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
    # 2026-08-17 실측: 무일치 keep 필터가 시트를 통째로 비웠는데, 이 목록에 없어
    # 검증 실패 후에도 복구가 안 됐다. 행을 지우는 액션은 전부 스냅샷을 뜬다.
    "excel_live.filter_rows",
    # sort_range의 쌍둥이인데 빠져 있었다(2026-08-24 감사 A2). 값 순서를 통째로
    # 섞는 액션이 복구 그물 없이 돌았다 — filter_rows(08-17)와 같은 "목록 누락" 부류.
    "excel_live.sort_rows",
    # 값을 치환하는 액션인데 빠져 있었다(같은 감사에서 육안 발견). 잘못된 치환은
    # 사용 범위 전체에 번진다.
    "excel_live.find_replace",
}
# 값 스냅샷으로 **복원이 안 되는** 파괴 액션 — 편입하면 가짜 안전감만 생긴다.
# 구조 핀(test_battery_regressions)이 이 분류를 강제한다: CONFIRM 액션은
# 스냅샷 목록·이 면제 목록·비파괴 중 하나로 반드시 분류돼야 한다.
_ROLLBACK_EXEMPT_ACTIONS: dict[str, str] = {
    "excel_live.delete_charts": "차트는 셀 값이 아니다 — 값 스냅샷으로 복원 불가",
    "excel_live.delete_sheet": "시트 삭제는 값 복원으로 안 된다 — 승인 게이트가 방어선",
    "excel_live.merge_cells": "실행기 가드가 값 손실 병합 자체를 거부한다(2026-08-20)",
    "excel_live.consolidate_sheets": "결과 시트에 쓰므로 원본 파괴가 아니다",
    "excel_live.drop_column": "열 삭제는 레이아웃 이동 — 값 스냅샷 복원이 어긋난다",
    "excel_live.add_column": "열 삽입도 레이아웃 이동 — 위와 같다",
    "excel_live.run_vba_macro": "매크로의 부작용 범위를 알 수 없다",
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


def _initialize_excel_thread() -> None:
    """COM을 이 스레드에 붙인다. Windows 밖에서는 할 일이 없다."""
    try:
        import pythoncom
    except ImportError:
        # macOS/Linux에는 COM 자체가 없다. xlwings가 AppleScript로 간다.
        return
    pythoncom.CoInitialize()


# Excel 호출을 전담하는 스레드 하나. `max_workers=1`이 두 가지를 동시에 준다.
#
# 1. **직렬화** — 예전에 락이 하던 일이다. 스레드가 하나뿐이라 저절로 된다.
# 2. **COM 아파트먼트 고정** — 이쪽이 더 중요하다. COM 객체는 만들어진 스레드에
#    묶인다. 예전에는 async 핸들러가 이벤트 루프 스레드에서, sync 핸들러가
#    FastAPI 스레드풀의 아무 스레드에서 같은 Excel 객체를 건드렸다.
#
# 스레드를 미리 띄우지 않는다. Excel을 한 번도 안 쓰는 실행(테스트 대부분)에서
# 쓸데없이 COM을 초기화할 이유가 없다.
_EXCEL_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="excel-com",
    initializer=_initialize_excel_thread,
)


def _queue_timeout_error(task_name: str) -> ExcelLiveError:
    return ExcelLiveError(
        f"Excel 작업 큐 대기 시간이 초과되었습니다({int(_EXCEL_QUEUE_TIMEOUT_SECONDS)}초): {task_name}"
    )


def _in_excel_thread() -> bool:
    return threading.current_thread().name.startswith("excel-com")


def _submit_to_excel_thread(fn):
    """전담 스레드로 넘기되, 지금 턴의 추적 컨텍스트를 함께 들려 보낸다.

    `decision_trace`는 "지금 어느 턴인가"를 `ContextVar`로 들고 다닌다. 새 스레드는
    빈 컨텍스트로 시작하므로, 그냥 넘기면 실행부 안에서 부르는
    `trace_route("execute:error")`·`trace_route("verify:failed")`가 조용히 버려진다.
    하필 실패를 진단할 때 가장 필요한 두 줄이라, 없으면 "검증이 통과했다"와
    "검증기가 안 돌았다"를 로그로 구분할 수 없다.

    제출 시점의 컨텍스트를 복사하므로, 턴 밖에서 부른 작업은 어느 턴에도 붙지 않는다.
    """
    return _EXCEL_EXECUTOR.submit(contextvars.copy_context().run, fn)


def _run_in_excel_queue(task_name: str, fn):
    """Excel 전담 스레드에서 `fn`을 돌리고 (결과, 큐 대기 ms)를 돌려준다.

    호출한 스레드는 끝날 때까지 블록된다. sync 라우트 핸들러용이다 — FastAPI가
    그것들을 이미 스레드풀에서 돌리므로 이벤트 루프는 막지 않는다. `async` 안에서는
    반드시 `_run_in_excel_queue_async`를 써야 한다.
    """
    if _in_excel_thread():
        # 전담 스레드 안에서 다시 부르면 자기 자신을 기다리다 멈춘다. 이미 그
        # 스레드에 있으니 그대로 실행하면 된다.
        return fn(), 0

    queued_at = time.time()
    future = _submit_to_excel_thread(fn)
    try:
        # 큐 대기와 실행 시간을 합쳐 기다린다. 예전에는 대기에만 상한이 있었지만,
        # 여기서 실행까지 무제한으로 두면 매달린 COM 호출을 끊을 방법이 없다.
        result = future.result(timeout=_EXCEL_QUEUE_TIMEOUT_SECONDS)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise _queue_timeout_error(task_name) from exc
    return result, int((time.time() - queued_at) * 1000)


async def _run_in_excel_queue_async(task_name: str, fn):
    """`_run_in_excel_queue`의 async 판. 기다리는 동안 이벤트 루프를 놓아준다.

    이걸 안 쓰고 동기판을 `async def` 안에서 부르면 COM이 도는 내내 루프가 통째로
    붙잡힌다. 그러면 `/health` 폴링이 답을 못 받아 UI가 사이드카를 죽은 것으로 본다.
    """
    queued_at = time.time()
    future = _submit_to_excel_thread(fn)
    try:
        result = await asyncio.wait_for(
            asyncio.wrap_future(future), timeout=_EXCEL_QUEUE_TIMEOUT_SECONDS
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        future.cancel()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise _queue_timeout_error(task_name) from exc
    return result, int((time.time() - queued_at) * 1000)


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
        # 지목한 통합문서가 목록에 없다고 **다른 통합문서의 시트로 떨어지면 안 된다.**
        #
        # 2026-08-17 실측: 워크스페이스 밖 경로로 지목했더니 목록에 없어서 rows[0]로
        # 폴백했고, 전혀 다른 통합문서의 활성 시트("추이")를 돌려줬다. 두 통합문서에
        # 같은 이름 시트가 있으면 조용히 엉뚱한 시트에 쓰게 된다.
        # 지목이 있으면 그 통합문서에게 직접 묻는다.
        try:
            info = service.list_sheets(workbook_id)
            active = str(info.get("active_sheet") or "").strip()
            if active:
                return active
            sheets = info.get("sheets") or []
            if sheets:
                return str(sheets[0])
        except Exception:
            pass
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


#: 이 액션들은 **없는 시트 이름을 받는 게 정상**이다 — 그 이름을 만들거나 붙이는 일이므로.
#
# **`rename_sheet`는 여기 들어오면 안 된다.** 그 액션의 `sheet_name`은 새 이름이 아니라
# **바꿀 대상**이라 이미 있어야 한다. 면제했더니 "수도권은 서울권으로 이름 바꿔놔"(값 치환
# 문장)가 가드를 지나 **활성 시트 이름을 바꿔 버렸다** — 그 시트를 가리키던 수식이 전부
# 깨진다(2026-08-20 624 게이트 159번에서 시트가 사라져 실행이 통째로 죽으며 드러났다).
# `consolidate_sheets`도 `sheet_name`이 원본이므로 같은 이유로 뺀다.
_SHEET_CREATING_ACTIONS = frozenset({"excel_live.create_sheet"})


def _edit_target_problem(
    workbook_id: str | None, sheet_name: str | None, *, message: str = ""
) -> str:
    """편집 대상이 실제로 존재하는지 확인하고, 아니면 되물을 문장을 만든다.

    플래너는 원문에 나온 낱말을 시트 이름으로 그대로 옮겨 적는다("학과운영비 작업" →
    sheet_name="학과운영비"). 그 시트가 없어도 승인 카드는 떠 버리고, 사용자가 승인한
    뒤에야 404로 죽는다. 승인을 요청하기 전에 여기서 걸러야 한다.

    `message`는 **계획에 시트 슬롯이 없을 때만** 넘어온다. 그때 sheet_name은 사용자의
    지시가 아니라 UI의 활성 시트일 뿐이므로, 원문이 "<이름> 시트"로 지목한 시트가
    통합문서에 없으면 그쪽을 먼저 문제 삼는다. 이 지목을 버리고 활성 시트에 쓰면
    사용자가 말한 적 없는 시트를 덮어쓴다 — 2026-08-16 실측에서 "Dashboard 시트 B4에
    합계 수식"이 Sales_Data!B4의 주문일자를 지우고도 `[VERIFY] 통과`로 보고됐다.
    """
    if not str(sheet_name or "").strip() and not str(message or "").strip():
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

    target_sheet = str(sheet_name or "").strip()
    if message:
        existing = {str(name).strip().casefold() for name in sheets}
        # 한 지목의 후보 중 **하나라도 실재하면** 그 지목은 해결된 것이다.
        # 평평한 목록으로 보면 조사를 뗀 변형("추")이 없는 시트로 걸려,
        # 멀쩡한 "추이"를 두고 되묻게 된다(2026-08-17 실측).
        for group in explicit_sheet_mention_variants(message):
            if any(name.strip().casefold() in existing for name in group):
                continue
            # "재고 관리 시트" / "간트 관리 시트"처럼 띄어 쓴 다낱말 이름은 한 낱말 패턴이 '관리'만 잡는다 —
            # 실재 시트 이름이 그 자리에서 끝나면 해결된 지목이다(2026-08-19 ex12·ex13 실측).
            if sheet_mention_matches_known(message, group[0], sheets):
                continue
            target_sheet = group[0]
            break
    if not target_sheet:
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
    elif action_name == "excel_live.sort_rows":
        # sort_rows는 target_range 파라미터가 **없고 늘 사용 범위 전체**를 정렬한다
        # (excel_live_file_service.sort_rows). else 분기로 흘리면 활성 선택 영역을
        # 스냅샷해, 실제로 섞이는 범위와 어긋난 복원이 된다.
        try:
            range_ref = str(service.get_used_range_ref(resolved_wb, resolved_sheet) or "")
        except Exception:
            return None
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


def _not_excel_response(message: str, why: str) -> ExcelLiveActionResponse:
    """이 문장은 엑셀 작업이 아니다 — 프론트가 /agent/chat으로 내려보내야 한다.

    HTTPException(400)으로 알리면 안 된다. Rust의 read_response가 4xx를 Err로 바꿔
    프론트 catch로 떨어뜨리므로(src-tauri/src/ipc.rs), 프론트가 "실패"와 "엑셀 일이
    아님"을 구분할 수 없다. 200 응답 본문으로 돌려줘야 폴백을 만들 수 있다.
    """
    return ExcelLiveActionResponse(
        ok=True,
        action="excel_live.not_excel_request",
        reason="엑셀 작업으로 볼 수 없는 요청입니다.",
        result={"route_to_chat": True, "why": why, "original_message": message},
    )


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
    """색 이름 → 헥사. 사전은 `color_lexicon` **한 곳**뿐이다.

    예전엔 이 함수와 `_QUICK_COLOR_PATTERN`이 각자 목록을 들고 있어, 패턴은 `흰`을
    잡는데 함수는 몰라 폴백(노란색)으로 떨어졌다 — **"흰 글씨"가 노란 글씨가 됐다**
    (2026-08-24 실측). 이제 정규식도 같은 사전에서 만든다.

    폴백은 노란색을 유지한다 — 이 함수는 패턴이 이미 색이라고 판정한 낱말만 받으므로
    여기 닿는 건 사전 누락뿐이고, 호출부들이 빈 문자열을 색 없음으로 읽어 왔다.
    """
    return color_hex(word, default="#FFFF00")


def _background_fill_hex(lowered: str, font_color: str | None) -> str:
    """한 문장에서 **배경색**만 골라낸다. 글자색과 헷갈리면 안 된다.

    "배경색 #1E6B4F로 칠하고 글자 흰색 굵게"에는 색이 둘이다. 글꼴 추출기가 이미
    글자색(#FFFFFF)을 집어 갔으므로, 남는 색이 배경색이다.

    배경을 가리키는 말이 없으면 빈 문자열 — "글씨 흰색 크게"가 배경 칠하기로
    새면 안 된다(2026-08-17 실측에서 이 오탐을 경계해 붙인 조건이다).
    """
    colors = _quick_extract_colors(lowered)
    if not colors:
        return ""
    # "남색 바탕에 흰 글씨", "남색에 흰글씨" — '배경'이라는 낱말이 없어도 색이
    # 둘이고 하나가 글자색이면 나머지는 배경이다(2026-08-18 지저분판 실측:
    # '바탕'이 어휘 밖이라 머리글 채움이 통째로 빠졌다).
    background_worded = bool(re.search(r"(배경|바탕|채우기|칠하|칠해|음영|하이라이트)", lowered))
    two_colors_with_font = bool(font_color) and len({c.upper() for c in colors}) >= 2
    if not background_worded and not two_colors_with_font:
        return ""
    # '배경'이라는 말과 붙어 있는 색이 가장 확실한 배경색이다 — "남색 배경에
    # 흰 글씨", "배경색 흰색으로 칠하고 글자 흰색" 둘 다 이 규칙 하나로 풀린다.
    for token in _QUICK_COLOR_PATTERN.finditer(lowered):
        window = lowered[max(0, token.start() - 8) : token.end() + 8]
        if re.search(r"배경|바탕|채우|음영", window):
            return _quick_color_hex(token.group(1))
    normalized_font = str(font_color or "").upper()
    for color in colors:
        if color.upper() != normalized_font:
            return color
    # 남은 색이 글자색 하나뿐이고 배경 곁에 색이 없으면, 어휘 밖 색(예전의
    # "남색")을 못 읽은 경우일 가능성이 크다. 글자색으로 배경을 덮으면 흰 바탕에
    # 흰 글씨가 된다(2026-08-18 실측) — 칠하지 않는 쪽이 안전하다.
    if len(colors) == 1 and normalized_font:
        return ""
    return colors[0]


# `#1F4E79` 같은 코드도 받는다. 대시보드 배색은 이름으로 부를 수 없는 색이 대부분이라,
# 코드를 못 읽으면 전부 기본값(노랑)으로 칠해진다(2026-08-16 실측: 남색 제목 바가 노랗게 나왔다).
#: 색 낱말 정규식. **사전에서 만든다** — 목록을 두 곳에 두면 갈라진다.
_QUICK_COLOR_PATTERN = COLOR_TOKEN_PATTERN


def _quick_extract_colors(text: str) -> list[str]:
    source = str(text or "")
    matches = []
    for m in _QUICK_COLOR_PATTERN.finditer(source):
        tail = source[m.end() : m.end() + 6]
        # "빨간색 **말고** 노란색" — 제외 지시가 붙은 색은 후보가 아니다
        # (2026-08-18 사냥: 첫 색을 집어 빨강으로 칠했다).
        if re.match(r"\s*(?:은|는|을|를)?\s*(?:말고|빼고|제외|아니)", tail):
            continue
        matches.append(m.group(1))
    out: list[str] = []
    for raw in matches:
        color = _quick_color_hex(raw)
        if not out or out[-1] != color:
            out.append(color)
    return out


#: 조건부 강조가 "어느 열인지 모른다"는 뜻으로 남기는 범위들.
_UNSCOPED_HIGHLIGHT_TARGETS = frozenset({"", "A:Z", "__ACTIVE_SELECTION__", "__USED_RANGE__"})


def _header_in_message(
    header: str, compact_message: str, taken: frozenset[str] | set[str] = frozenset()
) -> bool:
    """머리글이 문장에 나왔는가 — **한 글자 오타까지** 같은 것으로 본다.

    "출고**겅**수"는 "출고건수"다(2026-08-20 게이트9). 세 글자 미만은 보지 않는다.

    `taken`: 문장에 **정확히** 나온 다른 머리글들. 오차 창이 그중 하나와 정확히 일치하면
    그 언급은 이미 임자가 있다 — '매출액'을 매입액의 오타로 읽으면 안 된다
    (2026-08-20 자체 검토 실측: "매출액만 콤마"가 매입액까지 서식을 걸었다).
    """
    needle = _norm_header(str(header or ""))
    if not needle:
        return False
    if needle in compact_message:
        return True
    size = len(needle)
    if size < 3 or len(compact_message) < size:
        return False
    for start in range(len(compact_message) - size + 1):
        window = compact_message[start : start + size]
        if window in taken:
            continue
        if sum(1 for a, b in zip(needle, window) if a != b) <= 1:
            return True
    return False


def _digest_active_entry(digest: dict[str, Any]) -> dict[str, Any]:
    active = str((digest or {}).get("active_sheet") or "")
    for sheet in (digest or {}).get("sheets") or []:
        if str(sheet.get("name")) == active:
            return sheet
    sheets = (digest or {}).get("sheets") or [{}]
    return sheets[0] if sheets else {}


def _header_column_from_message(message: str, columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """문장이 부른 머리글이 **유일하게** 짚이면 그 열을 돌려준다."""
    compact = _norm_header(str(message or ""))
    if not compact:
        return None
    exact_norms = {
        _norm_header(str(c.get("header") or ""))
        for c in columns
        if _norm_header(str(c.get("header") or "")) and _norm_header(str(c.get("header") or "")) in compact
    }
    hits = [
        column for column in columns if _header_in_message(column.get("header"), compact, exact_norms)
    ]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    # 여러 머리글이 걸린다. 사람이 "그 열"이라고 콕 집은 쪽이 있으면 그것이다 —
    # "대기 중인 **운송장**이 몇 개인지 …, **상태 열에서** 대기인 셀만" 에서
    # 길이로 고르면 운송장이 이겨 엉뚱한 열이 칠해진다(2026-08-20 실측).
    for column in hits:
        head = re.escape(str(column.get("header") or "").strip())
        if head and re.search(rf"{head}\s*(?:열|칸|컬럼|column)", str(message or ""), re.IGNORECASE):
            return column
    # 그다음은 **문장에서 나중에 불린** 머리글 — 한국어는 뒤쪽이 본론이다.
    positions = []
    for column in hits:
        head = str(column.get("header") or "").strip()
        idx = str(message or "").rfind(head)
        positions.append((idx, column))
    positions.sort(key=lambda pair: pair[0])
    if positions and positions[-1][0] >= 0:
        return positions[-1][1]
    return None


def _scope_highlight_to_header_column(
    plan: list[dict[str, Any]] | None, message: str, digest: dict[str, Any]
) -> str:
    """조건부 강조의 범위를 머리글 열로 좁힌다. 좁혔으면 그 범위를, 아니면 빈 문자열."""
    if not plan or len(plan) != 1 or not isinstance(plan[0], dict):
        return ""
    step = plan[0]
    if str(step.get("action") or "") != "excel_live.highlight_by_condition":
        return ""
    params = dict(step.get("params") or {})
    target = str(params.get("target_range") or "").strip().upper()
    if target not in _UNSCOPED_HIGHLIGHT_TARGETS:
        # 상징 범위가 이미 표 전체(`A1:F3`)로 풀려 있는 경우도 "열을 모른다"는 뜻이다
        # (2026-08-20 게이트6: 이 판정을 안 해서 열 좁히기가 한 번도 발동하지 않았다).
        # 다만 원문이 범위를 직접 적었으면 사람 뜻이므로 건드리지 않는다.
        rect = re.fullmatch(r"([A-Z]{1,3})\d{1,7}:([A-Z]{1,3})\d{1,7}", target)
        if rect is None or rect.group(1) == rect.group(2):
            return ""
        if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}", str(message or "")):
            return ""
    entry = _digest_active_entry(digest)
    # 1행 머리글 → 표 블록 머리글 순으로 본다. 대시보드는 한 시트에 표를 쌓으므로
    # 1행만 보면 아래쪽 표를 통째로 놓친다(2026-08-20 ex23: "지연일수 5 넘는 셀" 0칸).
    span = _locate_header_span(message, entry)
    if span is None:
        return ""
    letter, first_row, last_row = span
    scoped = f"{letter}{first_row}:{letter}{last_row}"
    params["target_range"] = scoped
    step["params"] = params
    return scoped


#: 값 후보에서 떼어낼 조사·군말.
_VALUE_TOKEN_TAIL = re.compile(r"(?:만|은|는|이|가|을|를|인|짜리|건|것|거)+$")


def _digest_value_column(value: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """그 값이 든 열이 **하나뿐**이면 그 열을 돌려준다. 여럿이면 추측이므로 None."""
    target = _norm_header(value)
    if not target:
        return None
    columns = [c for c in (entry.get("columns") or []) if isinstance(c, dict)]
    rows = entry.get("sample_rows") or []
    hits: list[int] = []
    for idx in range(len(columns)):
        for row in rows:
            if idx < len(row) and _norm_header(str(row[idx])) == target:
                hits.append(idx)
                break
    return columns[hits[0]] if len(hits) == 1 else None


def _spans_multiple_columns(target: str) -> bool:
    """`A1:D5`처럼 열이 둘 이상이거나 상징 범위면 참."""
    text = str(target or "").strip().upper()
    if text in _UNSCOPED_HIGHLIGHT_TARGETS:
        return True
    rect = re.fullmatch(r"([A-Z]{1,3})\d{1,7}:([A-Z]{1,3})\d{1,7}", text)
    return bool(rect and rect.group(1) != rect.group(2))


#: "비어 있는 칸", "빈 칸", "공란" — 빈 칸 자체가 조건인 문형.
_BLANK_CONDITION = re.compile(r"(비어\s*있|비었|빈\s*(?:칸|셀|값|행)|공란|미입력|안\s*(?:적힌|채워진|들어간))")
#: 반대: "값이 있는", "채워진"
_FILLED_CONDITION = re.compile(r"(값이\s*있|채워진|입력된|들어\s*있)")


def _blank_condition_highlight(
    message: str, digest: dict[str, Any]
) -> list[dict[str, Any]]:
    """"입고예정일이 비어 있는 행만 노란색으로 칠해줘" — 빈 칸 조건 강조.

    2026-08-20 ex23 실측: 이 문형에 규칙이 없어 모델이 엉뚱한 한 칸(N1)을 칠했다.
    머리글로 열을 못 짚으면 물러난다 — 어느 열이 비었는지 추측하지 않는다.
    """
    text = str(message or "")
    if not _BLANK_CONDITION.search(text) or _FILLED_CONDITION.search(text):
        return []
    if not re.search(r"(강조|칠해|칠하|표시|색|하이라이트|highlight|빨갛|노랗|파랗)", text):
        return []
    colors = _quick_extract_colors(text.lower())
    entry = _digest_active_entry(digest)
    span = _locate_header_span(text, entry)
    if span is None:
        return []
    letter, first_row, last_row = span
    return [
        {
            "action": "excel_live.highlight_by_condition",
            "params": {
                "target_range": f"{letter}{first_row}:{letter}{last_row}",
                "operator": "isblank",
                "threshold": 0,
                "fill_color": colors[0] if colors else "#FFFF00",
            },
            "reason": "빠른 규칙 기반 빈 칸 강조",
        }
    ]


def _locate_header_span(message: str, entry: dict[str, Any]) -> tuple[str, int, int] | None:
    """문장이 부른 머리글의 (열문자, 첫 데이터 행, 마지막 행). 못 짚으면 None.

    ① 시트 1행 머리글 ② 표 블록별 머리글 순으로 본다. 대시보드는 한 시트에 표를 여럿
    쌓으므로 ①만 보면 아래쪽 표의 머리글을 통째로 놓친다(2026-08-20 ex23 실측:
    "지연일수 5 넘는 셀만 빨간색" → **0칸 칠해짐**. '지연일수'는 A25 표의 머리글이었다).
    여러 블록에 같은 머리글이 있으면 **추측하지 않고 물러난다.**
    """
    columns = [c for c in (entry.get("columns") or []) if isinstance(c, dict)]
    column = _header_column_from_message(message, columns)
    if column is not None:
        letter = str(column.get("letter") or "").strip().upper()
        used = str(entry.get("used_range") or "")
        last_row = int(m.group(1)) if (m := re.search(r"(\d+)$", used)) else 0
        if letter and last_row >= 2:
            return letter, 2, last_row
    hits: list[tuple[str, int, int]] = []
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_columns = [c for c in (block.get("columns") or []) if isinstance(c, dict)]
        found = _header_column_from_message(message, block_columns)
        if found is None:
            continue
        letter = str(found.get("letter") or "").strip().upper()
        first = int(block.get("first_data_row") or 0)
        last = int(block.get("last_row") or 0)
        if letter and 0 < first <= last:
            hits.append((letter, first, last))
    return hits[0] if len(hits) == 1 else None


def _scope_data_bar_to_header_column(
    plan: list[dict[str, Any]] | None, message: str, digest: dict[str, Any]
) -> str:
    """데이터 막대 대상을 문장이 부른 머리글의 열로 좁힌다. 좁혔으면 그 범위를."""
    if not plan or len(plan) != 1 or not isinstance(plan[0], dict):
        return ""
    step = plan[0]
    if str(step.get("action") or "") != "excel_live.apply_data_bar":
        return ""
    params = dict(step.get("params") or {})
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}", str(message or "")):
        return ""
    entry = _digest_active_entry(digest)
    # 1행 머리글 → 표 블록 머리글 순으로 본다. 대시보드는 한 시트에 표를 쌓으므로
    # 1행만 보면 아래쪽 표를 통째로 놓친다(2026-08-20 ex23: "지연일수 5 넘는 셀" 0칸).
    span = _locate_header_span(message, entry)
    if span is None:
        return ""
    letter, first_row, last_row = span
    scoped = f"{letter}{first_row}:{letter}{last_row}"
    params["target_range"] = scoped
    step["params"] = params
    return scoped


def _scope_number_format_to_headers(
    plan: list[dict[str, Any]] | None, message: str, digest: dict[str, Any]
) -> str:
    """표시 형식 대상을 문장이 부른 머리글의 열들로 좁힌다. 좁혔으면 요약 문자열을 돌려준다.

    "주문건수 출고건수는 천 단위 쉼표" → B·C 두 열에만. 표 전체에 걸면 글자 열까지 바뀐다.
    """
    if not plan or len(plan) != 1 or not isinstance(plan[0], dict):
        return ""
    step = plan[0]
    if str(step.get("action") or "") != "excel_live.set_number_format":
        return ""
    params = dict(step.get("params") or {})
    if not _spans_multiple_columns(str(params.get("target_range") or "")):
        return ""
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}", str(message or "")):
        return ""
    entry = _digest_active_entry(digest)
    columns = [c for c in (entry.get("columns") or []) if isinstance(c, dict)]
    compact = _norm_header(str(message or ""))
    exact_norms = {
        _norm_header(str(c.get("header") or ""))
        for c in columns
        if _norm_header(str(c.get("header") or "")) and _norm_header(str(c.get("header") or "")) in compact
    }
    named = [c for c in columns if _header_in_message(c.get("header"), compact, exact_norms)]
    if not named or len(named) == len(columns):
        return ""
    used = str(entry.get("used_range") or "")
    last_row = int(m.group(1)) if (m := re.search(r"(\d+)$", used)) else 0
    if last_row < 2:
        return ""
    letters = [str(c.get("letter") or "").strip().upper() for c in named]
    letters = [x for x in letters if x]
    if not letters:
        return ""
    ranges = [f"{x}2:{x}{last_row}" for x in letters]
    base = dict(params)
    plan[:] = [
        {
            "action": "excel_live.set_number_format",
            "params": {**base, "target_range": rng},
            "reason": step.get("reason") or "빠른 규칙 기반 표시 형식",
        }
        for rng in ranges
    ]
    return ",".join(ranges)


#: "표 전체", "시트 다" 처럼 **넓게** 지우라는 말. 이때는 열로 좁히지 않는다.
_CLEAR_WHOLE_WORDS = re.compile(
    r"(표\s*전체|시트\s*전체|전체\s*(?:표|시트|내용|데이터)|시트\s*(?:를|을)?\s*(?:다|싹|통째)"
    r"|싹\s*(?:다\s*)?(?:지워|비워|밀어)|다\s*지워|모두\s*지워|전부\s*지워"
    # '초기화'는 그 자체로 전체가 아니다 — "결석 기록**만** 초기화"는 한 열이다.
    # 전체를 가리키는 말이 앞에 붙었을 때만 전체 리셋으로 본다(2026-08-20 실측).
    r"|(?:표|시트|전체|전부|다)\s*(?:를|을)?\s*(?:초기화|리셋|reset))"
)
#: 머리글과 보호 낱말 사이에 이게 있으면 보호가 아니라 삭제 지시다.
_CLEAR_VERB_BREAK = re.compile(r"(비우|비워|지우|지워|삭제|없애|클리어|초기화|리셋|밀어|날리|clear)")


def _clear_protected_headers(text: str, columns: list[dict[str, Any]]) -> set[str]:
    """지키라고 불린 머리글들 — "점수는 건드리지 말고"·"결석 빼고"의 점수·결석.

    머리글 뒤 24자 안에 보호 낱말이 있고, 그 사이에 삭제 동사가 없으면 보호다.
    삭제 동사가 끼면("결석 열 **비우고** 나머지는 그대로") 보호 낱말은 다른 대상 얘기다.
    """
    out: set[str] = set()
    for column in columns:
        header = str(column.get("header") or "").strip()
        if not header:
            continue
        for m in re.finditer(re.escape(header), str(text or "")):
            tail = str(text)[m.end() : m.end() + 24]
            pm = _CLEAR_PROTECT_CLAUSE.search(tail)
            if pm and not _CLEAR_VERB_BREAK.search(tail[: pm.start()]):
                out.add(header)
                break
    return out


#: 리셋 계열 계획의 단계들. `초기화`는 테두리·채움까지 걷어내는 여러 단계를 만든다.
_CLEAR_RESET_ACTIONS = frozenset(
    {"excel_live.clear_range", "excel_live.fill_range", "excel_live.apply_border"}
)

#: "~는 건드리지 말고", "~는 그대로" — **지키라고** 부른 열이라 대상이 아니다.
_CLEAR_PROTECT_CLAUSE = re.compile(r"(건드리지|손대지|지우지|그대로|유지|놔두|놔둬|냅두|남기|남겨|빼고|제외)")

#: "결석 **열만**", "결석 값**만**" — 한 열로 한정한다는 표시.
_CLEAR_ONLY_WORDS = re.compile(r"(만|열|칸|컬럼|column|값들|수치|데이터)")


def _scope_clear_to_header_column(
    plan: list[dict[str, Any]] | None, message: str, digest: dict[str, Any]
) -> list[dict[str, Any]]:
    """`결석 열만 비워줘` → 결석 열의 데이터 구간만 비우는 계획.

    계획이 없거나 **표 전체를 비우는 계획**일 때만 만든다.
    머리글을 유일하게 못 짚으면 빈 목록 — 추측해서 지우지 않는다.
    """
    text = str(message or "")
    if not _is_clear_reset_request(text.lower()):
        return []
    if not _CLEAR_ONLY_WORDS.search(text):
        return []
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}", text):
        return []
    if plan:
        if not all(
            isinstance(step, dict) and str(step.get("action") or "") in _CLEAR_RESET_ACTIONS
            for step in plan
        ):
            return []
        if not any(
            _spans_multiple_columns(str((step.get("params") or {}).get("target_range") or ""))
            for step in plan
        ):
            return []
    entry = _digest_active_entry(digest)
    columns = [c for c in (entry.get("columns") or []) if isinstance(c, dict)]
    # "이름이랑 **점수는 건드리지 말고**" — 지키라고 부른 열은 대상이 아니다.
    # 이 절을 안 빼면 마지막에 불린 '점수'가 대상이 된다(2026-08-20 실측).
    # 보호 판정은 절 단위가 아니라 **머리글 단위**다. 절 단위로 거르면
    # "결석 열 값 전부 삭제해줘 (다른 열은 유지)"처럼 대상과 보호가 한 절에 있는
    # 정상 문장까지 물러난다(2026-08-20 자체 검토에서 확인).
    # 규칙: 머리글 뒤에, **삭제 동사를 거치지 않고** 보호 낱말이 오면 그 머리글은 보호다.
    #   "결석 값들 **빼고**"                 → 사이에 동사 없음 → 보호 (지우면 안 된다)
    #   "결석 열 **비우고** 나머지는 그대로"  → '비우고'가 사이에 있음 → 대상
    protected = _clear_protected_headers(text, columns)
    candidates = [c for c in columns if str(c.get("header") or "").strip() not in protected]
    if not candidates:
        return []
    column = _header_column_from_message(text, candidates)
    if column is None:
        return []
    header = str(column.get("header") or "").strip()
    # "결석 칸 내용**만** 싹 지워줘" — '싹'은 그 열 안에서 남김없이라는 뜻이다.
    # **머리글 + 만**이 있으면 좁히는 쪽이 이긴다(2026-08-20 파괴 게이트 2차).
    scoped_by_name = bool(
        header
        and re.search(
            rf"{re.escape(header)}\s*(?:열|칸|값|값들|기록|데이터|수치|내용|의)*\s*(?:내용|값)?\s*만",
            text,
        )
    )
    if not scoped_by_name and _CLEAR_WHOLE_WORDS.search(text):
        return []
    letter = str(column.get("letter") or "").strip().upper()
    used = str(entry.get("used_range") or "")
    last_row = int(m.group(1)) if (m := re.search(r"(\d+)$", used)) else 0
    if not letter or last_row < 2:
        return []
    return [
        {
            "action": "excel_live.clear_range",
            "params": {"target_range": f"{letter}2:{letter}{last_row}"},
            "reason": "빠른 규칙 기반 열 비우기(머리글로 열 확인)",
        }
    ]


def _value_equals_highlight(message: str, digest: dict[str, Any]) -> list[dict[str, Any]]:
    """"상태 대기 분홍 강조!" / "대기만 분홍" — 값과 색만 있는 강조 문장.

    값이 통합문서 안에 실제로 있고 그 열이 하나뿐일 때만 계획을 만든다.
    """
    text = str(message or "").strip()
    if not text or _quick_parse_condition(text) is not None:
        return []
    if not re.search(r"(강조|칠해|칠하|표시|색|하이라이트|highlight|빨갛|노랗|파랗|분홍|핑크)", text):
        return []
    colors = _quick_extract_colors(text.lower())
    if not colors:
        return []
    entry = _digest_active_entry(digest)
    if not (entry.get("columns") or []):
        return []
    headers = {_norm_header(str(c.get("header") or "")) for c in entry.get("columns") or []}
    for raw in re.findall(r"[가-힣A-Za-z0-9_]+", text):
        token = _VALUE_TOKEN_TAIL.sub("", raw).strip()
        if len(token) < 2 or _norm_header(token) in headers:
            continue
        column = _digest_value_column(token, entry)
        if column is None:
            continue
        letter = str(column.get("letter") or "").strip().upper()
        used = str(entry.get("used_range") or "")
        last_row = int(m.group(1)) if (m := re.search(r"(\d+)$", used)) else 0
        if not letter or last_row < 2:
            continue
        return [
            {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": f"{letter}2:{letter}{last_row}",
                    "operator": "==",
                    "threshold": 0,
                    "value": token,
                    "fill_color": colors[0],
                },
                "reason": "빠른 규칙 기반 값 동등 강조(통합문서에서 열 확인)",
            }
        ]
    return []


#: 붙여넣기 직후의 한 마디 집계 — "합계!", "평균 좀".
#: "합계!" 한 낱말만 온 꼴. 어휘와 변환표는 `aggregate_lexicon` **한 곳**에서 온다 —
#: 예전엔 정규식과 표가 따로였고 `개수`를 COUNT로 옮겼다(바인더는 COUNTA. 다른 답이다).
_BARE_AGGREGATE = re.compile(
    rf"^\s*(?:ㅇㅇ\s*)?({AGG_WORD_PATTERN.pattern})\s*(?:좀|만|도|은|는|을|를)?\s*[!.~…]*\s*$"
)
_BARE_AGGREGATE_FUNC = AGG_FUNC


def _bare_aggregate_after_paste(message: str, context_range: str | None) -> tuple[str, str] | None:
    """"합계!" — 방금 붙여넣은 표가 있으면 그 아래 한 줄이라는 뜻이다.

    맥락이 없으면 어디에 넣을지 모르므로 물러난다(추측하지 않는다).
    """
    if not str(context_range or "").strip():
        return None
    m = _BARE_AGGREGATE.match(str(message or ""))
    if not m:
        return None
    label = m.group(1)
    func = _BARE_AGGREGATE_FUNC.get(label)
    return (func, label) if func else None


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
# 값 없이 쓰기 동사만 온 문장 — 붙여넣기 뒤 값을 빠뜨린 실수("여기에 입력해줘").
_BARE_WRITE_REQUEST = re.compile(
    r"^(?:(?:[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?)\s*)?"
    # "이거 넣어줘", "복사한 거 여기에 붙여줘", "방금 거 입력" — 대명사는 값이 아니다(2026-08-19 ex9 v2 실측).
    r"(?:(?:방금|지금|아까)\s*)?(?:복사한|복붙한|붙여넣은|긁어온|선택한)?\s*"
    r"(?:이거|이걸|이것|요거|그거|그걸|그것|저거|얘네|이\s*값|이\s*내용|이\s*표|복사한\s*거|복사본|클립보드|거|것)?\s*(?:을|를|도)?\s*"
    r"(?:여기(?:에|에다|다가|다)?|요기에?|이\s*(?:곳|쪽|자리|칸|셀|범위|영역)에?)?\s*(?:값\s*(?:을|를)?\s*)?"
    r"(?:입력|기록|넣어|채워|써|적어|붙여\s*넣어|붙여|붙여넣기)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐)?\s*[~.!?…]*$"
)
_AGGREGATE_REQUEST_PATTERN = re.compile(
    r"(교차표|피벗|pivot)"
    r"|별\s*(?:로|은|는)?[^\n]{0,20}?(합계|집계|평균|순위|총액|총합|건수|얼마|요약|실적)",
    re.IGNORECASE,
)


def _message_states_condition(text: str) -> bool:
    """"매출이 100만도 안 되는 건"처럼 대상을 좁히는 조건이 붙었는지."""
    message = str(text or "")
    return (
        _quick_parse_condition(message) is not None
        or _looks_like_column_comparison(message)
        or parse_text_equals_condition(message) is not None
    )


def _underfit_reason(quick_first_action: str, text: str) -> str:
    """`_quick_plan_underfits_message`가 참일 때 **어느 조항**이었는지 — 로그 전용."""
    message = str(text or "")
    if quick_first_action in _CONDITION_SENSITIVE_QUICK_ACTIONS and _message_states_condition(message):
        return "condition"
    if quick_first_action in _FORMULA_SENSITIVE_QUICK_ACTIONS and _FORMULA_MENTION_PATTERN.search(message):
        return "formula_mention"
    if quick_first_action in _PREPARATION_ONLY_QUICK_ACTIONS and _message_asks_for_more_work(message):
        return "preparation_only"
    if quick_first_action in _RANK_LIMIT_SENSITIVE_QUICK_ACTIONS and rank_limit.detect(message):
        return "rank_limit"
    if quick_first_action in _DATA_STATE_SENSITIVE_QUICK_ACTIONS and _DATA_STATE_PATTERN.search(message):
        return "data_state"
    if quick_first_action in _AGGREGATE_SENSITIVE_QUICK_ACTIONS and _AGGREGATE_REQUEST_PATTERN.search(message):
        return "aggregate_request"
    return "unknown"


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
    if quick_first_action in _RANK_LIMIT_SENSITIVE_QUICK_ACTIONS and rank_limit.detect(message):
        return True
    if quick_first_action in _DATA_STATE_SENSITIVE_QUICK_ACTIONS and _DATA_STATE_PATTERN.search(
        message
    ):
        return True
    return quick_first_action in _AGGREGATE_SENSITIVE_QUICK_ACTIONS and bool(
        _AGGREGATE_REQUEST_PATTERN.search(message)
    )


# 대상이 **값의 상태**로 정의된 문장. 규칙은 동사 하나만 보고 범위를 통째로 잡으므로
# 그 조건이 통째로 사라진다. "지역이 비어 있는 행은 삭제해줘"가 clear_range로 가면
# 빈 행 3개가 아니라 멀쩡한 46행이 지워지고, 지운 셀이 있으니 성공이 보고된다
# (2026-08-11 armA-current 실측: 3/3회).
#
# 어느 행이 해당하는지는 다이제스트(머리글 + 예시 3행)로도 알 수 없다. 규칙도 못 하고
# 다이제스트도 못 하니 남는 길은 데이터를 실제로 보는 것뿐이다.
_DATA_STATE_PATTERN = re.compile(
    r"빈\s*칸|빈칸|비어\s*있|공란|누락"
    r"|이상치|이상\s*값|비정상|튀는\s*값"
    r"|문자열로|텍스트로|숫자가\s*아닌|형식이\s*다른|깨진",
    re.IGNORECASE,
)
_DATA_STATE_SENSITIVE_QUICK_ACTIONS = frozenset(
    {
        "excel_live.fill_range",
        "excel_live.clear_range",
        "excel_live.apply_border",
        "excel_live.write_range",
        "excel_live.sort_range",
    }
)


# 조건과 같은 이유로 규칙에 맡기면 안 되는 문장들. "상위 3개를 노랗게"가 fill_range로
# 가면 "상위 3개"가 통째로 사라지고 열 전체가 칠해진다 — 실행기는 칠한 셀이 있으니
# 성공을 보고하고, 사용자만 전부 노래진 시트를 본다.
_RANK_LIMIT_SENSITIVE_QUICK_ACTIONS = frozenset(
    {
        "excel_live.fill_range",
        "excel_live.clear_range",
        "excel_live.apply_border",
        "excel_live.write_range",
    }
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
    # "격자 표시해줘"가 read_range로 샜다(2026-08-17 실측). 표에 선을 긋는 말이다.
    if any(token in lowered for token in ["테두리", "경계선", "border", "보더", "격자", "모눈"]):
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
            # "없애"가 빠져 있었다 — "여기 표를 없애줘"가 규칙에 안 걸려
            # 플래너로 갔고, 성공 보고만 하고 아무것도 안 바뀌었다(2026-08-17 실측).
            r"(지우|지워|지울|삭제|없애|없앤|없애줘|비우|비워|초기화|리셋|reset|clear|wipe|erase|밀어|싹)",
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
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)


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
    if _chart_kind_from_message(lowered) and re.search(
        r"(그려|그러|뽑아|만들|생성|보여|시각화|차트|그래프)", lowered
    ) and not re.search(r"(지워|삭제|없애|제거|치워)", lowered):
        # "지연건수는 막대로 그러줘" — 종류 낱말+그리기 동사는 차트다. 값 낱말
        # ('건수')이 formula/countif로 먼저 잡히면 종류를 말했는데도 되묻는다
        # (2026-08-18 ex5 대화형 각본 정찰).
        return "chart"
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
    # "가운데 정렬"은 맞춤이지 줄 세우기가 아니다. 예외가 아래 둘째 가지에만 붙어 있어
    # 낱말 "정렬"이 먼저 걸렸고, "달력 전체 가운데 정렬해줘"가 기준 열을 되물었다
    # (2026-08-20 ex24 실측). 예외는 **가지 전체**를 덮어야 한다.
    if _ALIGN_REQUEST.search(lowered):
        return "format"
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
    ) or (_matches_any_pattern(
        lowered,
        [
            r"(큰|높은|많은|작은|낮은|적은)\s*(값|순|순서)",
            r"(정렬|배치|재배치|줄세우).{0,8}(해|해줘|하|해봐)",
        ],
    ) and not re.search(r"(가운데|중앙|왼쪽|오른쪽|양쪽)\s*(?:로|으로)?\s*정렬", lowered)):
        # "가운데 정렬"은 맞춤(alignment)이지 데이터 정렬이 아니다 — 정렬로
        # 오인해 기준 열을 물은 뒤 행 순서를 섞었다(2026-08-18 사냥).
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
    chart_like = _contains_any_keyword(
        lowered,
        compact,
        ["차트", "그래프", "시각화", "도식화", "비율로 보고", "한눈에", "추이", "발표용"],
    ) or _matches_any_pattern(lowered, [r"(차트|그래프|시각화|도식화).{0,8}(만들|생성|그려|표시)"])
    # "월별 매출 그래프"의 '월별'만으로 피벗 슬롯에 넣으면 차트 요청이 표 집계 질문이 된다.
    # 피벗/집계표를 말했거나, 묶는 말만 있고 그래프가 없을 때만 피벗이다.
    if _contains_any_keyword(lowered, compact, ["피벗", "집계표"]):
        return "pivot"
    if (
        _contains_any_keyword(
            lowered,
            compact,
            ["월별", "부서별", "지역별", "담당자별", "카테고리별", "고객별"],
        )
        and not chart_like
    ):
        return "pivot"
    if chart_like:
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
    # "a4"는 낱말로만 — "A46:F51"의 a4가 인쇄로 분류돼 합계 줄 요청이 "인쇄 기준을 알려주세요"로 샜다
    # (2026-08-19 ex12 실측).
    if _contains_any_keyword(lowered, compact, ["인쇄", "pdf", "출력", "제출"]) or re.search(
        r"(?<![a-z0-9])a4(?![a-z0-9])", lowered
    ):
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
        "excel_live.sort_rows": "sort",
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


# 되묻기 답이 아니라 새 명령으로 보는 규칙 액션. 슬롯 intent 매핑이 비어 있어도
# "노랗게 칠해줘"가 앞 턴 차트 슬롯을 이어서 실행하면 안 된다.
_STANDALONE_RULE_ACTIONS = frozenset(
    {
        "excel_live.fill_range",
        "excel_live.highlight_by_condition",
        "excel_live.write_range",
        "excel_live.read_range",
        "excel_live.apply_border",
        "excel_live.clear_range",
        "excel_live.list_sheets",
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.select_sheet",
        "excel_live.create_sheet",
        "excel_live.rename_sheet",
        "excel_live.delete_sheet",
        "excel_live.save_workbook",
        "excel_live.apply_data_bar",
        "excel_live.apply_color_scale",
        "excel_live.set_font",
        "excel_live.convert_to_excel_table",
        "excel_live.apply_formula_cf",
    }
)
_SOFT_OPERATION_INTENTS = frozenset({"", "general", "safety"})


def _incoming_command_intent(
    *,
    operation_intent: str,
    table_hints: dict[str, Any],
    rule_step: dict[str, Any] | None,
    quick_plan: Any,
    standalone_read: bool,
) -> str:
    """이번 문장이 앞 턴 되묻기의 답이 아니라 새 명령이면 그 의도를 돌려준다."""
    intent = str(operation_intent or "").strip()
    if intent not in _SOFT_OPERATION_INTENTS:
        return intent
    if table_hints.get("table_intent"):
        return "table"
    action = ""
    if isinstance(rule_step, dict):
        action = str(rule_step.get("action") or "").strip()
    if not action and isinstance(quick_plan, list) and quick_plan:
        first = quick_plan[0]
        if isinstance(first, dict):
            action = str(first.get("action") or "").strip()
        else:
            action = str(getattr(first, "action", "") or "").strip()
    mapped = _action_to_operation_intent(action)
    if mapped:
        return mapped
    if action in _STANDALONE_RULE_ACTIONS:
        return f"rule:{action}"
    if standalone_read:
        return "read"
    return ""


def _drop_superseded_pending_slots(
    *,
    session_key: str,
    pending_slot: PendingCreateTableSlots | None,
    pending_operation: PendingExcelOperationSlots | None,
    incoming: str,
) -> tuple[PendingCreateTableSlots | None, PendingExcelOperationSlots | None]:
    """새 명령이 대기 슬롯과 다른 일이면 슬롯을 버린다.

    차트 종류를 묻던 세션에 "노랗게 칠해줘"가 오면 답이 아니라 다른 작업이다.
    표 크기를 묻던 세션에 "피벗 만들어줘"가 와도 같다. 같은 의도이거나 답이
    짧아 의도를 못 정하면 슬롯을 유지한다.
    """
    if pending_operation is not None:
        pending_label = str(pending_operation.intent or "").strip()
    elif pending_slot is not None:
        pending_label = "table"
    else:
        return pending_slot, pending_operation
    if not incoming or incoming == pending_label:
        return pending_slot, pending_operation
    # "아무거나 알아서 해줘"는 새 명령이 아니라 되묻기에 대한 (모호한) 답이다.
    # general로 분류됐다고 표 슬롯을 버리면, 답을 받고도 대화가 원점으로 돌아간다
    # (2026-08-17 실측 — 템플릿 질문 다음 턴이 "어떤 작업을 원하시는지…"로 샜다).
    if pending_label == "table" and incoming == "general":
        return pending_slot, pending_operation
    # "보기 좋게 만들어줘" 뒤에 "매출 열 기준 내림차순"은 같은 대화의 구체화다.
    # 일반 슬롯을 버리면 업그레이드가 안 되고 LLM을 다시 부른다.
    # 다만 "노랗게 칠해줘"처럼 규칙이 확정한 다른 작업은 가로채지 않는다.
    if pending_label in _SOFT_OPERATION_INTENTS and not str(incoming).startswith("rule:"):
        return pending_slot, pending_operation
    if pending_slot is not None:
        _pending_create_table_slots.pop(session_key, None)
        pending_slot = None
    if pending_operation is not None:
        _pending_operation_slots.pop(session_key, None)
        pending_operation = None
    return pending_slot, pending_operation


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


#: `=` 다음에 올 수 있는 것 — 함수·셀 주소·숫자·괄호·따옴표·시트참조.
#: 한글 낱말이 오면 수식이 아니라 "같다"는 말이다(2026-08-20 게이트6: `상태=대기`가
#: `=대기` 수식이 되어 표 전체를 덮었다).
_FORMULA_HEAD = re.compile(
    r"^=\s*(?:"
    r"[A-Za-z_][A-Za-z0-9_.]*\s*\("
    r"|\$?[A-Za-z]{1,3}\$?\d{1,7}(?![A-Za-z0-9])"
    r"|[-+]?\d"
    r"|[(\"']"
    r"|[^\s=!<>]{1,31}!\$?[A-Za-z]{1,3}\$?\d"
    r")"
)


def _looks_like_a_formula(candidate: str) -> bool:
    return _FORMULA_HEAD.match(str(candidate or "").strip()) is not None


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
                # =COUNTIF(...)/COUNTA(...) 처럼 닫힌 뒤 이항 연산이 이어지면 계속 읽는다.
                # 첫 )에서 끊으면 배송완료율 같은 비율 수식이 앞쪽 함수만 남는다.
                rest = raw[idx + 1 :]
                if re.match(r"\s*[&+\-*/^]", rest):
                    continue
                end = idx + 1
                break
        elif depth == 0 and ch.isspace() and idx > start:
            end = idx
            break
    formula = raw[start:end].strip()
    return formula if len(formula) > 1 else None


def _extract_excel_table_name(text: str) -> str:
    """'SalesTable 이름으로'처럼 문장에 적힌 ListObject 이름을 뽑는다."""
    match = re.search(r"([A-Za-z][A-Za-z0-9_]{2,})\s*이름", str(text or ""))
    if not match:
        return ""
    name = str(match.group(1) or "").strip()
    if name.lower() in {"excel", "table", "listobject"}:
        return ""
    return name


def _anchor_cell_from_target(target: str, fallback_col: str) -> str:
    """수식 조건부서식의 기준 셀. 범위 시작이 K23이면 K2가 아니라 $K23이어야 한다."""
    raw = str(target or "").strip()
    if "!" in raw:
        raw = raw.split("!", 1)[1]
    raw = raw.replace("$", "").upper()
    match = re.match(r"([A-Z]{1,3})(\d+)", raw)
    if match:
        return f"${match.group(1)}{match.group(2)}"
    col = re.sub(r"[^A-Z]", "", str(fallback_col or "A").upper()) or "A"
    return f"${col}2"


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


# "이 시트에", "새 시트에"의 앞말은 이름이 아니라 지시어다. 이름으로 받아들이면
# 없는 시트를 찾아 되묻게 되고, 사용자는 눈앞에 열어 둔 시트를 두고 질문을 받는다.
_SHEET_DEMONSTRATIVES = frozenset(
    {
        "이", "그", "저", "새", "새로운", "현재", "지금", "해당", "여기", "이번",
        "다음", "이전", "위", "아래", "같은", "빈", "다른",
        "this", "that", "new", "current", "another", "next", "previous",
    }
)


def _named_sheet_in_text(text: str) -> str | None:
    """문장이 시트를 **이름으로** 지목했으면 그 이름을 준다. 지시어뿐이면 None.

    여러 개면 마지막을 쓴다 — 앞쪽은 대개 원본이라 그걸 고르면 원본을 덮어쓴다
    (`_extract_output_sheet_from_text`와 같은 이유).
    """
    src = str(text or "")
    for match in reversed(list(_SHEET_MENTION_PATTERN.finditer(src))):
        name = str(match.group(1)).strip().strip("\"'")
        if name and name.lower() not in _SHEET_DEMONSTRATIVES:
            # "재고 관리 시트" — 이름은 여러 낱말일 수 있다(2026-08-19 ex12 실측: '관리' 시트를 찾았다).
            return extend_sheet_name_leftward(src, match.start(1), name)
    return None


_HEADER_INTENT_PATTERN = re.compile(r"헤더|머리글|컬럼\s*명|열\s*이름|header", re.IGNORECASE)
_TABLE_CREATE_INTENT_PATTERN = re.compile(r"(?:표|테이블|table)\s*\S{0,4}\s*(?:만들|생성|작성|create)", re.IGNORECASE)


_FREEZE_WORD = re.compile(r"(틀\s*고정|고정|freeze|프리즈)", re.IGNORECASE)
_FREEZE_ROW_HINT = re.compile(
    r"(첫\s*줄|첫줄|첫\s*번째\s*행|첫\s*행|1\s*행|맨\s*윗\s*줄|윗줄|위\s*줄|상단|머리글|헤더|제목\s*줄|타이틀\s*줄|"
    r"스크롤|내려도|내려가도|따라오게|안\s*사라지게|계속\s*보이게|항상\s*보이게)"
)
_FREEZE_COL_HINT = re.compile(r"(첫\s*열|첫열|A\s*열|왼쪽\s*열|첫\s*번째\s*열)")
_FREEZE_OFF = re.compile(r"(해제|풀어|풀고|없애|취소|끄|지워)")


def _quick_freeze_step(text: str) -> dict[str, Any] | None:
    """"첫 줄 고정해줘", "머리글 줄 고정, 내려도 보이게", "틀 고정 해제" — 틀 고정은 결정적 규칙이다.

    2026-08-19 블라인드 게이트: 규칙이 없어 전부 모델로 갔고 5/24가 빈 파라미터로 실행돼 아무 일도 없었다.
    """
    if not _FREEZE_WORD.search(text):
        return None
    # "고정"이 다른 뜻인 문장("합계 줄은 고정", "값 고정") — 줄/열/스크롤 맥락이 없으면 물러난다.
    if not (_FREEZE_ROW_HINT.search(text) or _FREEZE_COL_HINT.search(text) or re.search(r"틀\s*고정|freeze", text, re.IGNORECASE)):
        return None
    if _FREEZE_OFF.search(text):
        return {"action": "excel_live.freeze_panes", "params": {"freeze_at": "해제"}, "reason": "빠른 규칙 기반 틀 고정 해제"}
    row = bool(_FREEZE_ROW_HINT.search(text)) or not _FREEZE_COL_HINT.search(text)
    col = bool(_FREEZE_COL_HINT.search(text))
    freeze_at = "B2" if (row and col) else ("B1" if col else "A2")
    return {"action": "excel_live.freeze_panes", "params": {"freeze_at": freeze_at}, "reason": "빠른 규칙 기반 틀 고정"}


def _quick_header_write_step(text: str, preferred_cell: str) -> dict[str, Any] | None:
    """머리글 목록만 준 문장을 표 첫 행 쓰기로 바꾼다.

    "헤더에는 '날짜', '금액', ... 이렇게 만들어줘"는 이미 만들어 둔 표의 첫 줄을 채우라는 뜻인데,
    LLM에 맡기면 이름마다 add_column을 부르거나 엉뚱한 시트를 지어내서 열을 덧붙인다.
    목록이 명시된 문장은 추론할 게 없으므로 규칙으로 확정한다.
    """
    if not _HEADER_INTENT_PATTERN.search(text) or _TABLE_CREATE_INTENT_PATTERN.search(text):
        return None
    # "머리글은, 글씨는 흰색 굵게" / "머리글 줄 고정해줘, 내려도 계속 보이게" — 서식·고정 문장의 쉼표 조각을
    # 머리글 목록으로 읽어 **표 첫 줄을 덮어썼다**(2026-08-19 블라인드 게이트: 머리글 서식 6건·틀 고정 5건).
    # 목록 쓰기는 "머리글(은|을|로|:) 이름, 이름, …" 꼴에서만 — 서식·동작 어휘가 섞이면 물러난다.
    if re.search(
        r"(굵게|진하게|색|배경|바탕|글씨|글자|고정|보이게|스크롤|테두리|병합|정렬|콤마|서식|"
        r"해줘|해주|해라|해봐|부탁|주세요|줄래|줘요|사라지|내려도|너비|높이|폰트|크기)",
        text,
    ):
        return None
    if not re.search(r"(?:헤더|머리글|컬럼\s*명|열\s*이름|header)\s*(?:은|는|을|를|로|으로|에는|:|\s)", text):
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


# 표시 형식을 말로 부르는 방식들. 규칙으로 확정하지 않으면 플래너가 문구를 **값으로**
# 써 버린다 — 2026-08-17 실측: "천 단위 콤마 넣어줘"가 D5의 97000을 문자열
# '천 단위 콤마'로 덮어썼다. 서식 요청이 데이터를 부수는 건 가장 나쁜 실패다.
_NUMBER_FORMAT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 소수점 규칙이 천 단위보다 **앞**이어야 한다 — "소수점 세 자리"가 뒤 규칙의
    # "세 자리"에 먼저 걸리면 #,##0(정수 표시)이 돼 소수가 사라진다.
    (re.compile(r"소수(?:점)?\s*(?:네|4)\s*자리"), "0.0000"),
    (re.compile(r"소수(?:점)?\s*(?:세|3)\s*자리"), "0.000"),
    (re.compile(r"소수(?:점)?\s*(?:두|2)\s*자리|둘째\s*자리"), "0.00"),
    (re.compile(r"소수(?:점)?\s*(?:한|1)\s*자리|첫째\s*자리"), "0.0"),
    (re.compile(r"(퍼센트|백분율|%\s*(로|표시|형식))", re.IGNORECASE), format_code("퍼센트")),
    (re.compile(r"(통화|원화|₩|금액\s*기호)", re.IGNORECASE), format_code("통화")),
    (re.compile(r"(천\s*단위|1,?000\s*단위|세\s*자리|쉼표|콤마|comma)", re.IGNORECASE), format_code("천단위")),
    (re.compile(r"(날짜\s*형식|yyyy)", re.IGNORECASE), format_code("날짜")),
)
_EXPLICIT_FORMAT_CODE = re.compile(r"([#0][#0,\.]*(?:%|)|yyyy[-/][mM]{1,2}[-/]dd)")


def _quick_number_format_step(text: str, target: str) -> dict[str, Any] | None:
    """표시 형식 요청을 규칙으로 확정한다. 값은 건드리지 않는다."""
    lowered = str(text or "").lower()
    # "소수점 두 자리로 보여줘"가 read_range로 샜다(2026-08-17 실측) — '보여줘'가
    # 읽기로 해석된 탓이다. 자릿수·서식 어휘가 있으면 표시 형식 요청으로 본다.
    if not re.search(
        r"(형식|서식|포맷|format|콤마|쉼표|퍼센트|백분율|천\s*단위|자리|소수)", lowered
    ):
        return None
    # 문장에 코드가 그대로 있으면 그걸 쓴다("#,##0", "0.00%").
    explicit = ""
    for match in _EXPLICIT_FORMAT_CODE.finditer(str(text or "")):
        token = match.group(1)
        if len(token) < 3:
            continue
        # 맨숫자("1,000", "1000")는 사람이 적은 **수량**이지 서식 코드가 아니다.
        # 그대로 쓰면 "1,000 단위 comma format"이 format_code='000'이 되어
        # 콤마 대신 세 자리 0 채움이 걸린다(2026-08-19 블라인드 게이트 실측).
        if not any(ch in token for ch in "#%."):
            continue
        explicit = token
        break
    code = explicit
    if not code:
        for pattern, fmt in _NUMBER_FORMAT_HINTS:
            if pattern.search(lowered):
                code = fmt
                break
    if not code:
        return None
    return {
        "action": "excel_live.set_number_format",
        "params": {"target_range": target, "format_code": code},
        "reason": "빠른 규칙 기반 표시 형식",
    }


#: 사람이 부르는 **맞춤**. 한국어 "정렬"은 줄 세우기도 뜻하므로 방향 낱말이 있어야 맞춤이다.
_ALIGN_REQUEST = re.compile(r"(가운데|중앙|왼쪽|오른쪽|양쪽|좌|우)\s*(?:로|으로)?\s*(?:정렬|맞춤)")
#: "표 전체"·"달력 전부"처럼 **통째로**를 뜻하는 말.
_WHOLE_WORDED = re.compile(r"(전체|전부|모두|모든|통째)")


#: "제목 줄 병합" 계열. 범위를 직접 적지 않고 **줄을 이름으로** 부른 병합이다.
_TITLE_MERGE_WORDED = re.compile(
    r"(제목|타이틀|title|맨\s*윗|맨\s*위|첫\s*줄|첫\s*행|위쪽|1\s*행)"
)
_MERGE_VERB_WORDED = re.compile(r"(병합|머지|merge|하나로\s*합|한\s*칸으로|합쳐)")


def _title_row_merge_plan(message: str, digest: dict[str, Any]) -> list[dict[str, Any]]:
    """"제목 줄 병합해줘" — **값이 하나뿐인 맨 윗줄**을 병합한다.

    그게 제목 줄의 정의다. 머리글 줄(값이 여러 개)을 병합하면 그 값들이 사라진다
    (2026-08-20 파괴 게이트: 12문형 중 9개가 머리글 줄을 먹었다).
    그런 줄이 없으면 **빈 계획을 돌려주고 물러난다** — 짐작해서 지우지 않는다.
    """
    text = str(message or "")
    if not _MERGE_VERB_WORDED.search(text) or not _TITLE_MERGE_WORDED.search(text):
        return []
    # 범위를 직접 적었으면 그건 사람 뜻이다. **콜론만 보면 안 된다** —
    # 사람은 "A1**부터** G1**까지**"라고 더 자주 쓴다. 콜론만 보다가
    # "제목 줄은 A1부터 G1까지 병합해줘"를 가로채 `A1:A1`(한 칸)로 만들었다
    # (2026-08-22 42각본 전수: ex14·ex14_v2·ex20의 유일한 실패 3건이 이것이었다).
    if re.search(
        r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*"
        r"(?::|~|-|부터|에서|~에서)\s*(?:[A-Za-z]{1,3}\d{1,7})",
        text,
    ):
        return []
    entry = _digest_active_entry(digest)
    used = str(entry.get("used_range") or "")
    span = re.match(r"^([A-Z]+)\d+:([A-Z]+)\d+$", used.upper())
    if not span:
        return []
    # 다이제스트는 1행을 `columns`(머리글)로, 2행부터를 `sample_rows`로 담는다.
    columns = [c for c in (entry.get("columns") or []) if isinstance(c, dict)]
    rows = [[str(c.get("header") or "") for c in columns], *(entry.get("sample_rows") or [])]
    if not columns:
        return []
    first_col, last_col = span.group(1), span.group(2)
    for offset, row in enumerate(rows[:3], start=1):
        cells = [str(v).strip() for v in (row or []) if str(v or "").strip()]
        if len(cells) == 1:
            return [
                {
                    "action": "excel_live.merge_cells",
                    "params": {"target_range": f"{first_col}{offset}:{last_col}{offset}"},
                    "reason": "빠른 규칙 기반 제목 줄 병합(값이 하나뿐인 줄)",
                }
            ]
    return []


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

    freeze_step = _quick_freeze_step(text)
    if freeze_step is not None:
        return [freeze_step]

    fmt_step = _quick_number_format_step(
        text, normalized_ctx or explicit_range or "__ACTIVE_SELECTION__"
    )
    if fmt_step is not None:
        return [fmt_step]

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

    create_step = _quick_create_sheet_step(text)
    if create_step is not None:
        return [create_step]

    rename_step = _quick_rename_sheet_step(text)
    if rename_step is not None:
        return [rename_step]

    autofit_step = _quick_autofit_step(text, explicit_range or normalized_ctx)
    if autofit_step is not None:
        return [autofit_step]

    delete_sheet_match = re.search(
        r"([A-Za-z0-9_가-힣]+)\s*(?:시트|sheet|탭)\s*(?:을|를)?\s*(?:삭제|제거|없애)",
        text,
        re.IGNORECASE,
    )
    if delete_sheet_match:
        sheet_name = str(delete_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return [
                {
                    "action": "excel_live.delete_sheet",
                    "params": {"sheet_name": sheet_name},
                    "reason": "빠른 규칙 기반 시트 삭제",
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
    #
    # "A8:A13에 지역 목록 입력 (서울, 경기, …)"은 **값을 쓰라는 말**이지 드롭다운을 걸라는
    # 말이 아니다. '목록' 하나로 여기 걸리면 셀은 빈 채 유효성 검사만 붙고, 뒤 단계의
    # SUMIF가 빈 기준을 보게 된다(2026-08-16 실측: 매크로가 11단계에서 멈췄다).
    # 쓰기 동사가 있으면 드롭다운이 아니라고 본다 — 제한하라는 말이 함께 있을 때만 예외.
    writes_values = re.search(r"(입력|기입|써|쓰|넣|적어|채워|write|set)", lowered)
    restricts = re.search(r"(드롭다운|dropdown|제한|유효성|validation|선택되도록|선택만)", lowered)
    if (
        any(token in lowered for token in ["드롭다운", "dropdown", "목록", "선택되도록", "선택만"])
        and (explicit_range or normalized_ctx)
        and not (writes_values and not restricts)
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
    if formula_a1 and not _looks_like_a_formula(formula_a1):
        # "상태=대기"의 '='는 같다는 말이다 — 수식으로 쓰면 표가 덮인다.
        formula_a1 = None
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
        token in lowered for token in ["읽어", "보여", "확인", "조회", "알려", "read", "show", "display"]
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
                # "제거"는 흰색 칠이 아니라 무채움이다 — 흰색은 기본 격자선을 가려
                # 시트가 종이처럼 하얘 보인다(2026-08-17 GUI 실측).
                "params": {"target_range": target, "fill_color": "none"},
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
        # "**표 전체** 테두리"는 직전 결과 범위가 아니라 **표 전체**를 뜻한다.
        # `_is_whole_sheet_style_request`는 컨텍스트가 있으면 거짓을 주므로, 대화 중에는
        # 그 말이 통째로 무시됐다 — 직전 턴이 머리글 서식이면 테두리가 머리글에만 갔다
        # (2026-08-20 ex1 실측: 표가 A1:F8인데 A1:F1에만 둘러졌다).
        # 원문이 범위를 직접 적었으면 그건 사람 뜻이므로 건드리지 않는다.
        whole_table_worded = bool(re.search(r"(표|테이블|table)?\s*(전체|전부|모두|모든|통째)", lowered))
        # 컨텍스트가 있을 때만 덮는다. 컨텍스트가 없으면 예전 경로(`__USED_RANGE__`)가
        # 이미 맞다 — 계획 문자열을 바꿀 이유가 없다.
        if whole_table_worded and not explicit_range and normalized_ctx:
            target = "__TABLE_REGION__"
        else:
            target = normalized_ctx or explicit_range or (
                "__USED_RANGE__" if whole_sheet_border else "__ACTIVE_SELECTION__"
            )
        border_thin = any(token in lowered for token in ["얇", "thin"])
        # "회식"은 회색의 흔한 오타다 — 실측 문장("가장 기본 회식 얇은 색")에서 나왔다.
        border_light = any(token in lowered for token in ["옅", "연한", "회색", "회식", "그레이", "gray", "grey"])
        border_reset = any(
            token in lowered
            for token in ["기본값", "기본 상태", "기본", "원래 상태", "원래", "초기 상태", "초기", "없애", "제거", "지워", "reset"]
        )
        border_remove = any(token in lowered for token in ["없애", "제거", "지워", "remove"])
        line_style = "none" if border_remove else "continuous"
        weight = "thin" if (border_reset or border_thin) else "medium"
        # "기본 경계선"의 기본은 Excel '모든 테두리' 버튼과 같은 **검정 얇은 실선**이다.
        # 2026-08-17 GUI 실측: 기본값 복구가 #D9D9D9(아주 연한 회색)를 칠했는데,
        # 흰 배경 위에서 사실상 안 보여 사용자가 "그건 못하나 보네"라고 했다 —
        # COM으로 확인하니 테두리는 실제로 박혀 있었다. 연한 회색은 사용자가
        # 명시적으로 옅은 색을 말했을 때만 쓴다.
        color = "#D9D9D9" if border_light else "#000000"
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

    if looks_like_existing_table_convert(text):
        target = normalized_ctx or explicit_range or "__USED_RANGE__"
        table_params = {"target_range": target, "has_header": True}
        table_name = _extract_excel_table_name(text)
        if table_name:
            table_params["table_name"] = table_name
        return [
            {
                "action": "excel_live.convert_to_excel_table",
                "params": table_params,
                "reason": "빠른 규칙 기반 Excel 표 변환",
            }
        ]

    # 크기·색을 버리면 "제목 글씨 흰색으로 크게"가 굵게로만 끝난다. 같은 추출기를 쓴다.
    # "글씨 흰색"처럼 굵게·글꼴이라는 말이 없는 문장이 배경색 규칙으로 새지 않게 한다.
    font_params = extract_font_params(text)
    # "가운데 정렬"은 글꼴 분기가 처리한다 — 대상 범위 고르기(머리글·표 전체·컨텍스트)를
    # 그대로 쓰려는 것이다. 액션은 set_font 하나이므로 파라미터만 얹으면 된다.
    _align_word = _ALIGN_REQUEST.search(lowered)
    if _align_word:
        font_params = dict(font_params)
        font_params["align"] = _align_word.group(1)
    if (
        # '굵은 글씨'·'진하게'도 굵게다 — 어미 하나 때문에 분기에 못 들어가면
        # 배경만 칠해지고 굵게가 통째로 빠진다(2026-08-20 게이트3 header_navy).
        re.search(r"(굵게|굵은|굵직|진하게|진한|두껍게|볼드|bold|글꼴|폰트)", lowered)
        or font_params.get("color")
        or font_params.get("size")
        or font_params.get("align")
    ) and not re.search(r"(테두리|경계선|border|괘선)", lowered):
        # "첫줄/제목줄"도 1행이다 — 어휘 밖이라 활성 셀에 칠해졌다(2026-08-18
        # 지저분판 실측: 첫줄 남색이 F7 한 칸 배경이 됐다).
        header_font = bool(
            re.search(r"(머리글|헤더|header|첫\s*줄|첫\s*행|1\s*행|제목\s*줄|맨\s*윗\s*줄|타이틀)", lowered)
        )
        if header_font and not explicit_range:
            # "머리글 행은 빨간 배경" — 머리글을 콕 집었으면 직전 쓰기 범위(A1:E6)가
            # 아니라 **그 표의 첫 행**이다. 컨텍스트를 우선하면 표 전체가 칠해진다
            # (2026-08-18 사람 말투 3라운드 실측: 30셀 전부 빨강 → 상태 배지 오염).
            ctx_rows = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", str(normalized_ctx or "").upper())
            target = (
                f"{ctx_rows.group(1)}{ctx_rows.group(2)}:{ctx_rows.group(3)}{ctx_rows.group(2)}"
                if ctx_rows
                else "1:1"
            )
        elif font_params.get("align") and not explicit_range and _WHOLE_WORDED.search(lowered):
            # "달력 **전체** 가운데 정렬" — 직전 결과 범위가 아니라 표 전체다.
            target = "__TABLE_REGION__"
        else:
            target = normalized_ctx or explicit_range or "__ACTIVE_SELECTION__"
        font_params = font_params or {"bold": True}
        steps: list[dict[str, Any]] = []
        # "배경색 …로 칠하고 글자 굵게" — 한 문장에 둘 다 있으면 둘 다 한다.
        #
        # 2026-08-17 실측: 이 분기가 먼저 return해서 뒤의 배경색 분기에 닿지 못했다.
        # "배경색 #1E6B4F로 칠하고 글자 흰색 굵게"가 글꼴만 바뀌고 배경은 그대로였는데
        # **성공으로 보고됐다.** 같은 문장을 둘로 쪼개면 되던 것이라 더 헷갈렸다.
        fill_hex = _background_fill_hex(lowered, font_params.get("color"))
        if fill_hex:
            steps.append({
                "action": "excel_live.fill_range",
                "params": {"target_range": target, "fill_color": fill_hex},
                "reason": "빠른 규칙 기반 배경색 적용",
            })
        steps.append({
            "action": "excel_live.set_font",
            "params": {"target_range": target, **font_params},
            "reason": "빠른 규칙 기반 글꼴 변경",
        })
        return steps

    text_equals = parse_text_equals_condition(text)
    formula_cf = bool(re.search(r"조건부\s*서식|수식\s*조건부", lowered))
    if formula_cf and text_equals:
        col = str(col_match.group(1)).upper() if col_match else ""
        target = normalized_ctx or explicit_range or (f"{col}:{col}" if col else "__USED_RANGE__")
        colors = _quick_extract_colors(lowered)
        return [
            {
                "action": "excel_live.apply_formula_cf",
                "params": {
                    "target_range": target,
                    "formula": f'{_anchor_cell_from_target(target, col)}="{text_equals}"',
                    "fill_color": colors[0] if colors else "#FFC7CE",
                },
                "reason": "빠른 규칙 기반 수식 조건부 서식",
            }
        ]

    # 병합 — "A1부터 L1까지 병합해줘" / "A1:L1 병합" / "제목 줄은 A1부터 H1까지
    # 합쳐줘". 규칙이 없어 플래너 의존이었다(2026-08-18 사람 말투 각본 정찰:
    # 7건이 LLM 경로). "합계"의 '합'과 헷갈리지 않게 병합/합쳐/합치기만 받는다.
    if re.search(r"(병합|합쳐|합치|merge)", lowered) and not re.search(r"(해제|풀어|unmerge|취소)", lowered):
        span = re.search(
            r"\b([A-Za-z]{1,3}\d{1,7})\s*(?:부터|에서|~|-)\s*([A-Za-z]{1,3}\d{1,7})\s*(?:까지)?",
            text,
        )
        merge_target = (
            f"{span.group(1).upper()}:{span.group(2).upper()}" if span else (explicit_range or normalized_ctx or "")
        )
        if merge_target and ":" in merge_target:
            return [
                {
                    "action": "excel_live.merge_cells",
                    "params": {"target_range": merge_target},
                    "reason": "빠른 규칙 기반 셀 병합",
                }
            ]

    if re.search(r"데이터\s*막대|data\s*bar", lowered):
        # "**연비효율** 열에도 데이터 막대" — 문장이 부른 머리글이 대상이다.
        # 컨텍스트만 보면 직전 열에 다시 걸린다(2026-08-20 ex1 실측: 규칙이 B2:B6 하나뿐,
        # C열에는 안 걸렸다). 열은 다이제스트가 준비된 뒤 `_scope_data_bar_to_header_column`이
        # 확정하므로, 여기서는 **머리글을 불렀다는 표시**만 남긴다.
        target = explicit_range or normalized_ctx or "__ACTIVE_SELECTION__"
        return [
            {
                "action": "excel_live.apply_data_bar",
                "params": {"target_range": target},
                "reason": "빠른 규칙 기반 데이터 막대",
            }
        ]

    if re.search(r"색조|컬러\s*스케일|color\s*scale", lowered):
        target = normalized_ctx or explicit_range or "__ACTIVE_SELECTION__"
        return [
            {
                "action": "excel_live.apply_color_scale",
                "params": {"target_range": target},
                "reason": "빠른 규칙 기반 색조 조건부 서식",
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
            # 형용사 어간형 — "빨갛게", "노랗게". 색 추출기는 이미 아는데 **분기 입구**에만
            # 없어서 문장 전체가 규칙 밖으로 샜다(2026-08-20: "클레임 10 넘는 셀 빨갛게").
            "빨갛",
            "노랗",
            "파랗",
            "하얗",
            "까맣",
            "누렇",
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
        if text_equals:
            return [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {
                        "target_range": target,
                        "operator": "==",
                        "threshold": 0,
                        "value": text_equals,
                        "fill_color": primary,
                    },
                    "reason": "빠른 규칙 기반 값 동등 강조",
                }
            ]
        # 조건도 값 동등도 못 만들었다. 여기서 통짜로 칠하면 사용자가 "~만"이라고 한
        # 문장이 **표 전체**를 덮는다(2026-08-20 게이트3: "상태 대기 분홍 강조!" →
        # 대기가 아닌 줄까지 분홍). 지목이 없으면 물러난다 — 해석 카드/되묻기가 받는다.
        if not normalized_ctx and not explicit_range and not whole_sheet_color:
            return None
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
        # "차트 다 지워, 데이터는 그대로" — 지목이 차트이고 값을 지키라고 했으면 답은 하나다.
        # 아래 한정사 가드('는 그대로')가 먼저 걸리면 **아무것도 안 하고** 물러난다 —
        # 데이터는 지켜지지만 사용자가 콕 집은 차트 삭제도 빠진다(2026-08-20 게이트4).
        if re.search(r"(차트|그래프|chart)", lowered) and re.search(
            r"(데이터|값|내용|표|숫자|기존)\s*(?:는|은|만|도)?\s*(?:[^\s,]{1,8}\s*){0,2}"
            r"(?:놔두|놔둬|냅두|두고|두시|남기|남겨|유지|그대로|빼고|건드리지|손대지|지우지)",
            lowered,
        ):
            return [
                {
                    "action": "excel_live.delete_charts",
                    "params": {},
                    "reason": "빠른 규칙 기반 차트 삭제(데이터 보존 지시)",
                }
            ]
        # 한정사가 있으면 통째 삭제 금지 — "서식만 지워줘(값은 그대로)",
        # "중복된 행은 지워줘", "합계 행만", "필터 초기화"가 범위 전체 clear로
        # 실행돼 데이터가 조용히 사라졌다(2026-08-18 5렌즈 사냥, 조용한 파괴
        # 4건). 여기서 물러나면 해석 카드/되묻기가 받는다 — 안 하고 묻는 쪽이
        # 항상 싸다.
        if re.search(r"[^\s,]+\s*시트\s*(?:을|를)?\s*(?:지워|삭제|없애|제거|치워)", lowered) and not re.search(
            r"시트\s*(?:의|에\s*있|내용|값|안)", lowered
        ):
            # "임시 시트 지워줘"가 선택 영역 내용 삭제로 실행됐다(2026-08-18
            # 사냥). 시트를 지목한 삭제는 여기(내용 비우기) 소관이 아니다.
            return None
        if re.search(
            # "값만/내용만"은 정상 좁은 삭제(값만 지움)가 이미 있으므로 막지
            # 않는다 — 여기서 거르는 건 통째 삭제로 오실행되던 위험 한정사다.
            r"(서식만|스타일만|필터|중복(된|되는)?\s*(행|줄|값)"
            r"|합계\s*행|마지막\s*행|은\s*그대로|는\s*그대로|빼고|말고)",
            lowered,
        ):
            return None
        chart_mentioned = bool(re.search(r"(차트|그래프|chart)", lowered))
        # "데이터는 놔두고 / 값은 그대로 / 표는 두고" — 값을 지키라는 한정사. 차트만 지우라는 뜻이다
        # (2026-08-19 블라인드 게이트: "지워줘 차트 다, 데이터는 놔두고"가 데이터까지 비웠다).
        # 조사와 동사 사이에 한두 낱말이 낀다("데이터는 **그대로** 두시고요").
        # 예전에는 조사 바로 뒤만 봐서 보호가 꺼졌고, 뒤따르는 clear_range가 표를 지웠다
        # (2026-08-20 게이트3: `차트 전부 지워 주세요, 데이터는 그데로 두시고요.` → B2=None).
        keep_data = bool(
            re.search(
                r"(데이터|값|내용|표|숫자|기존)\s*(?:는|은|만|도)?\s*(?:[^\s,]{1,8}\s*){0,2}"
                r"(?:놔두|놔둬|냅두|두고|두시|남기|남겨|유지|그대로|빼고|건드리지|손대지|지우지)",
                lowered,
            )
        )
        # "차트 전부/다 없애"의 전부·다는 차트의 수량이지 셀 범위가 아니다 — 차트가 언급된 문장에서는
        # 전체/전부/다를 범위 어휘로 세지 않는다(같은 날 실측: "시트에 있는 차트 전부 없애"가 표까지 지웠다).
        scope_words = r"(셀|내용|값|초기화|리셋|서식|데이터|표|뭐든)" if chart_mentioned else r"(셀|내용|값|전체|전부|초기화|리셋|서식|데이터|표|뭐든)"
        cell_scope_mentioned = bool(re.search(scope_words, lowered)) and not keep_data
        if chart_mentioned and (not cell_scope_mentioned or keep_data):
            # "차트 다 지워줘" — 차트만 지목했으면 값은 건드리지 않는다.
            # 삭제 액션 부재로 차트 **생성** 슬롯("차트 종류를 선택해 주세요")에
            # 새던 문형이다(2026-08-18 GUI 실측).
            return [
                {
                    "action": "excel_live.delete_charts",
                    "params": {},
                    "reason": "빠른 규칙 기반 차트 삭제",
                }
            ]
        if not whole_sheet_reset and not explicit_range and _clear_request_targets_a_subset(lowered):
            # 지울 대상이 따로 지목된 문장. 규칙으로 밀면 시트가 통째로 비워진다.
            return None
        target = normalized_ctx or explicit_range or ("__USED_RANGE__" if whole_sheet_reset else "__ACTIVE_SELECTION__")
        steps: list[dict[str, Any]] = []
        if chart_mentioned:
            # "차트 같은 거 다 지워주고 셀 초기화 전체 해줘" — 차트는 clear_range로
            # 안 지워진다. 리셋 계획 맨 앞에 차트 삭제를 넣는다.
            steps.append(
                {
                    "action": "excel_live.delete_charts",
                    "params": {},
                    "reason": "빠른 규칙 기반 차트 삭제",
                }
            )
        # "표를 없애줘"는 값만 지우라는 뜻이 아니다 — 테두리·배경까지 걷어내야
        # 사용자 눈에 표가 사라진다. clear_range는 서식을 남기므로, 값만 지우면
        # 빈 칸에 테두리만 남아 "아무것도 안 됐다"로 보인다(2026-08-17 실측:
        # 사용자가 "완료되었습니다"를 받고도 화면은 그대로였다).
        #
        # "초기화"도 같은 부류다(같은 날 두 번째 실측). "여기 부분 초기화시켜줄 수
        # 있어?"가 값 비우기로만 분류돼, 서식만 있고 값이 없는 범위에서 **아무것도
        # 안 바뀐 채** "완료"가 나갔다. 초기화·원래대로·리셋은 의미상 전체 리셋이다
        # — 값·비우기 어휘("비워줘", "내용 지워줘")만 서식을 남긴다.
        if re.search(
            r"(표|테이블|table|서식|포맷|스타일|꾸민|테두리|경계선"
            r"|초기화|리셋|reset|원래대로|원상복구|(원래|처음|초기|기본)\s*상태"
            r"|깨끗하게|깔끔하게|말끔하게|새\s*것처럼|새것처럼|싹\s*(다\s*)?(지워|밀어|비워))",
            lowered,
        ):
            steps.append({
                "action": "excel_live.apply_border",
                "params": {
                    "target_range": target,
                    "line_style": "none",
                    "weight": "thin",
                    "color": "#D9D9D9",
                },
                "reason": "빠른 규칙 기반 테두리 제거",
            })
            steps.append({
                "action": "excel_live.fill_range",
                # "제거"는 흰색 칠이 아니라 무채움이다 — 흰색은 기본 격자선을 가려
                # 시트가 종이처럼 하얘 보인다(2026-08-17 GUI 실측).
                "params": {"target_range": target, "fill_color": "none"},
                "reason": "빠른 규칙 기반 배경색 제거",
            })
        steps.append({
            "action": "excel_live.clear_range",
            "params": {"target_range": target},
            "reason": "빠른 규칙 기반 내용 비우기",
        })
        return steps

    # 부정 판정은 **공용 게이트 한 곳**만 쓴다. 예전엔 저장 규칙이 자체 정규식을 들고 있어
    # "저장 안 해도 돼요" · "저장 말고" · "저장 금지"를 놓치고 그대로 저장했다(2026-08-19 게이트 실측).
    # 규칙마다 부정을 따로 적으면 이런 구멍이 반드시 생긴다.
    if any(token in lowered for token in ["저장", "save"]) and not _negated_command(text):
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

    replace_step = _quick_find_replace_step(text, context_range)
    if replace_step is not None:
        return [replace_step]
    return None


# "처리중을 진행중으로 바꿔줘", "ML Ops를 MLOps로 찾아 바꿔", "'김선생' → '김선생님'" — 찾아 바꾸기는 결정적이다.
# 2026-08-19 ex16 실측: 규칙이 없어 전부 모델로 갔고 플래너가 JSON을 못 내 되물었다.
_FIND_REPLACE_VERB = (
    r"(?:전부|모두|다|싹|일괄|한\s*번에)?\s*(?:찾아서?\s*|find\s*&?\s*replace\s*로?\s*)?"
    r"(?:바꿔|바꾸|바꺼|변경|교체|치환|치환햐|replace|고쳐|고치|이름\s*바꿔)"
)
# 찾을/바꿀 말은 **한 덩어리**다. 공백을 허용하면 "전부 서울권" · "건 다 서울권"을 통째로 문다
# (2026-08-20 게이트2에서 이 규칙 때문에 조용한 오실행이 5→6으로 **늘었다**).
# 넓게 잡는 것보다 확실할 때만 잡는 편이 낫다 — 잘못된 치환은 데이터를 망친다.
# **비탐욕**이어야 한다 — 탐욕적이면 "서울권으로"에서 조사까지 먹어 '서울권으'가 된다(2026-08-20 실측).
# **비탐욕**이어야 한다 — 탐욕적이면 "서울권으로"에서 조사까지 먹어 '서울권으'가 된다(2026-08-20 실측).
# 두 낱말까지만 허용한다("ML Ops"·"대기 중"). 더 열면 "전부 서울권"을 통째로 문다.
_FR_ATOM = r"[^\s,;→\"'“”‘’]{1,24}?"
_FR_WORD = _FR_ATOM + r"(?:\s" + _FR_ATOM + r")??"
_FR_Q = r"[\"'“”‘’]?"
# 값에 붙는 군더더기 — 잡은 뒤 떼어낸다.
_FR_STRIP_LEAD = re.compile(r"^(?:전부|모두|다|싹|일괄|여기|표|이|그|저)\s+")
_FR_STRIP_TAIL = re.compile(
    r"(?:\s*(?:이?라는|이?라고|들어간|적힌|된|하는))?"
    r"(?:\s*(?:글자|텍스트|문자열|단어|말|이름|명칭|값|건|거|것|셀|칸|부분|애들))?"
    r"(?:\s*(?:을|를|은|는|이|가|도|만))?\s*$"
)
_FR_SCOPE = (
    r"(?:(?:[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?|[A-Za-z]\s*열|[^\s,;]+\s*열|[^\s,;]+\s*시트"
    r"|여기\s*표|이\s*표|표\s*전체|시트\s*전체|표에\s*있는|전체|표|여기)\s*(?:에서|의|에|안에서|은|는)?\s+)?"
)

_FIND_REPLACE_PATTERNS = (
    # "수도권 → 서울권" · "수도권→서울권 변경!"
    re.compile(
        r"^\s*" + _FR_SCOPE + _FR_Q + r"(?P<find>" + _FR_WORD + r")" + _FR_Q
        + r"\s*(?:→|->|=>|⇒)\s*" + _FR_Q + r"(?P<repl>" + _FR_WORD + r")" + _FR_Q
        # 비탐욕이라 경계를 안 주면 '서' 한 글자에서 멈춘다(2026-08-20 실측).
        + r"(?:\s*(?:으로|로))?(?=[\s,.!?~…]|$)",
        re.IGNORECASE,
    ),
    # "수도권을 서울권으로 바꿔" · "수도권이라고 된 거 전부 서울권으로 고쳐" · "수도권은 서울권으로"
    re.compile(
        r"^\s*" + _FR_SCOPE + _FR_Q + r"(?P<find>" + _FR_WORD + r")" + _FR_Q
        + r"\s*(?:이?라는|이?라고|을|를|은|는)\s*"
        r"(?:(?:된|적힌|쓰인|써진|입력된|표시된|들어간|돼\s*있는|되어\s*있는)\s*)?"
        r"(?:(?:글자|텍스트|문자열|단어|말|값|건|거|것|셀|칸|부분|애들)\s*(?:을|를|은|는|도)?\s*)?"
        r"(?:(?:전부|모두|다|싹|일괄)\s*)?"
        + _FR_Q + r"(?P<repl>" + _FR_WORD + r")" + _FR_Q + r"\s*(?:으로|로)",
        re.IGNORECASE,
    ),
    # 바꿀 말이 앞: "서울권으로 바꿔줘 수도권 들어간 건 다"
    re.compile(
        r"^\s*" + _FR_Q + r"(?P<repl>" + _FR_WORD + r")" + _FR_Q + r"\s*(?:으로|로)\s*" + _FIND_REPLACE_VERB
        # 동사 뒤에 **공백을 강제**하고 높임 꼬리를 건너뛴다 — 안 그러면 '줘'·'주세요'가 찾을 말이 된다
        # (2026-08-20 실측).
        + r"(?:\s*(?:줘요|줘|주세요|주라|주시|해줘|놔|둬))*[^\n]{0,4}?\s+"
        + _FR_Q + r"(?P<find>" + _FR_WORD + r")" + _FR_Q
        + r"(?=[\s,.!?~…]|$|이?라|들어|적힌|된)",
        re.IGNORECASE,
    ),
)
# 이런 낱말이 찾을/바꿀 값이면 텍스트 치환이 아니라 서식·구조 명령이거나 수량어다.
_FIND_REPLACE_NOT_TEXT = re.compile(
    r"^(?:열|행|시트|탭|차트|그래프|글꼴|폰트|색|배경|서식|형식|크기|너비|높이|이름|제목|굵게|기울임|테두리|병합|정렬|"
    r"수식|함수|숫자|날짜|퍼센트|콤마|소수|자리|순서|위치|방향|모양|스타일|타입|종류|단위|통화|원화|달러|"
    r"여기|표|전부|모두|다|싹)$"
)


# 치환이라는 **증거** — 동사나 화살표가 있어야 한다.
_FIND_REPLACE_EVIDENCE = re.compile(
    r"(?:바꿔|바꾸|바꺼|변경|교체|치환|치환햐|replace|고쳐|고치|→|->|=>|⇒|찾아\s*바꾸)"
)
# 조사 모양이 비슷하지만 치환이 아닌 문장.
_FIND_REPLACE_OTHER_INTENT = re.compile(
    r"(?:순으로|순서대로|오름차순|내림차순|정렬|높은\s*순|낮은\s*순|많은\s*순|적은\s*순"
    r"|시트\s*이름|탭\s*이름|시트명|이름\s*(?:을|를)?\s*(?:바꿔|변경)"
    r"|배경|글꼴|폰트|테두리|병합|차트|그래프|서식|형식|퍼센트|콤마|소수)"
)


def _fr_clean(value: str) -> str:
    """잡은 말에서 수량·분류어·조사를 떼어낸다. 남는 게 없으면 빈 문자열."""
    out = _FR_STRIP_LEAD.sub("", str(value or "").strip())
    out = _FR_STRIP_TAIL.sub("", out).strip().strip("\"'“”‘’")
    return out


def _quick_find_replace_step(text: str, context_range: str | None) -> dict[str, Any] | None:
    """"수도권을 서울권으로 바꿔줘" — 찾아 바꾸기는 결정적이다.

    **넓게 잡는 것보다 확실할 때만 잡는 것이 낫다.** 잘못된 치환은 데이터를 망치고 되돌리기 어렵다
    (2026-08-20 게이트2: 어제 넣은 느슨한 규칙이 'find=여기 표, replace=수도권을 서울권' 같은 값을 만들어
    조용한 오실행을 5→6건으로 늘렸다). 확신이 없으면 물러나 모델·되묻기에 맡긴다.
    """
    src = str(text or "").strip()
    if not src or "=" in src:
        return None
    # **치환 문장이라는 증거가 없으면 잡지 않는다.** 조사만 보고 잡았더니
    # "매출 높은 순으로 보여줘"(정렬)가 find='매출 높' → replace='순' 치환이 됐다
    # (2026-08-20, 기존 라우터 테스트가 잡아냄). 잘못된 치환은 데이터를 망친다.
    if not _FIND_REPLACE_EVIDENCE.search(src):
        return None
    # 정렬·서식·차트 문장은 조사 모양이 비슷해도 치환이 아니다.
    if _FIND_REPLACE_OTHER_INTENT.search(src):
        return None
    for pattern in _FIND_REPLACE_PATTERNS:
        m = pattern.search(src)
        if not m:
            continue
        find_text = _fr_clean(m.group("find"))
        raw_repl = str(m.group("repl") or "").strip().strip("\"'“”‘’")
        # "빈칸으로"의 '칸'은 분류어가 아니라 낱말의 일부다 — 자르기 **전에** 판정한다.
        blank_target = re.fullmatch(r"(?:빈\s*칸|공백|빈\s*값|빈\s*문자열|없음|삭제)", raw_repl) is not None
        replace_text = "" if blank_target else _fr_clean(raw_repl)
        if not find_text or find_text == replace_text:
            continue
        if _FIND_REPLACE_NOT_TEXT.match(find_text) or _FIND_REPLACE_NOT_TEXT.match(replace_text):
            continue
        if re.fullmatch(r"[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?", find_text, re.IGNORECASE):
            continue
        if not blank_target and not replace_text:
            continue
        explicit = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{1,7}:[A-Za-z]{1,3}\d{1,7})(?![A-Za-z0-9])", src)
        col = re.search(r"(?<![A-Za-z0-9])([A-Za-z])\s*열", src)
        if explicit:
            target = explicit.group(1).upper()
        elif col:
            target = f"{col.group(1).upper()}:{col.group(1).upper()}"
        elif context_range and ":" in str(context_range):
            target = str(context_range).upper()
        else:
            target = "__USED_RANGE__"
        return {
            "action": "excel_live.find_replace",
            "params": {
                "target_range": target,
                "find_text": find_text,
                "replace_text": replace_text,
                "whole_cell": False,
            },
            "reason": "빠른 규칙 기반 찾아 바꾸기",
        }
    return None


# "요약 시트 하나 만들어줘" — 시트 생성은 결정적이다.
# 2026-08-20 블라인드 게이트 `new_sheet`: 24문장 중 8이 되묻기·3이 오실행이었고,
# 그중 하나는 `'요약' 이름으로 시트 하나` 에서 **'요약 이름으로'라는 시트를 만들었다.**
_CS_LABEL = r"(?:시트|sheet|탭|tab|워크시트|싯트|씨트)"
_CS_MAKE = r"(?:만들|맏드|만드|생성|추가|파\s*줘|파\s*주|파줄|create|add|새로\s*파)"
# 이름이 될 수 없는 말 — 지시어·수량·라벨.
_CS_NOT_NAME = frozenset(
    {
        "새", "새로", "새로운", "빈", "임시", "다른", "이", "그", "저", "요", "하나", "한", "두", "세",
        "시트", "sheet", "탭", "tab", "워크시트", "이름", "명", "이름으로", "여기", "여기다", "거기",
        "ㅇㅇ", "ㅇㅋ", "아", "음", "어", "네", "또", "그리고", "새로이", "더", "추가로", "좀", "먼저",
    }
)
_CS_QUOTES = "\"'“”‘’"

_CREATE_SHEET_PATTERNS = (
    # "'요약' 이름으로 시트 하나 새로 만들어" — 이름이 '이름으로' **앞**에 온다.
    # 이걸 놓쳐 **'요약 이름으로'라는 시트가 만들어졌다**(2026-08-20 게이트).
    re.compile(
        r"[\"'“‘]?(?P<name>[^\s,\"'”’]+?)[\"'”’]?\s*(?:이름|명)\s*(?:으로|로)"
        r"[^\n]{0,14}" + _CS_LABEL + r"[^\n]{0,14}" + _CS_MAKE,
        re.IGNORECASE,
    ),
    # "요약이라는 이름의 새 시트를 만들어" · "요약이라고 부를 탭 추가" · "이름은 요약으로 해서 시트 하나"
    re.compile(
        r"(?:(?P<name>[^\s,]+?)\s*(?:이?라는|이?란|이?라고)\s*(?:이름(?:의|으로|은)?|부를|부르는)?"
        r"|이름(?:은|을|이)?\s*(?P<name2>[^\s,]+?)(?:으로|로)?)"
        r"[^\n]{0,14}" + _CS_LABEL + r"[^\n]{0,12}" + _CS_MAKE,
        re.IGNORECASE,
    ),
    # "새 시트 하나 추가해서 요약이라고 불러줘" — 이름이 동사 뒤
    re.compile(
        _CS_LABEL + r"[^\n]{0,12}" + _CS_MAKE + r"[^\n]{0,10}?"
        r"(?:이름(?:은|을|이)?\s*)?(?P<name>[^\s,]+?)\s*(?:이?라고|으로|로)\s*(?:불러|부르|해)",
        re.IGNORECASE,
    ),
    # "새 탭 추가, 이름 요약으로" · "시트 만들어 이름은 요약"
    re.compile(
        _CS_LABEL + r"[^\n]{0,12}" + _CS_MAKE + r"[^\n]{0,8}[,·]?\s*이름\s*(?:은|을|이)?\s*"
        r"(?P<name>[^\s,]+?)\s*(?:으로|로)?(?=[\s,.!?~…]|$)",
        re.IGNORECASE,
    ),
    # "요약 시트 하나 만들어줘" · "요약 탭 추가" · "요약 시트 새로 하나 부탁"
    re.compile(
        r"(?P<name>[^\s,]+?)\s*" + _CS_LABEL + r"\s*(?:을|를|도|좀|하나|한\s*개|새로|새로이|더|추가로|새|먼저)*"
        r"[^\n]{0,10}?" + _CS_MAKE,
        re.IGNORECASE,
    ),
    # 동사 없이: "요약 시트" · "요약 탭 하나" · "요약 시트 새로 하나 부탁드려요"
    re.compile(
        r"^\s*(?P<name>[^\s,]+?)\s*" + _CS_LABEL
        + r"(?:\s*(?:하나|한\s*개|새로|새로이|더|좀|또))*"
        r"(?:\s*(?:부탁\s*드려요|부탁해요|부탁해|부탁드립니다|부탁|주세요|줘))?\s*[~.!?…]*$",
        re.IGNORECASE,
    ),
)


def _quick_create_sheet_step(text: str) -> dict[str, Any] | None:
    """"요약 시트 하나 만들어줘" — 시트 생성. 이름을 못 고르면 물러난다(추측 금지)."""
    src = str(text or "").strip()
    if not src:
        return None
    # 이름 변경·삭제와 섞이지 않게.
    if re.search(r"(바꿔|바꾸|변경|고쳐|rename|삭제|지워|없애)", src):
        return None
    # "A1에 시트 이름 써줘"는 쓰기다 — 단, 셀 지목이 **시트 낱말보다 앞**일 때만.
    # 뒤에 있으면 "Summary 시트 만들어서 A1에 …"처럼 시트 생성이 먼저인 복합문이다.
    cell_write = re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*(?:셀|칸)?\s*에", src)
    label_at = re.search(_CS_LABEL, src, re.IGNORECASE)
    if (
        cell_write
        and label_at
        and cell_write.start() < label_at.start()
        and re.search(r"(입력|기록|써|적어|넣어|채워)", src)
    ):
        return None
    for pattern in _CREATE_SHEET_PATTERNS:
        m = pattern.search(src)
        if not m:
            continue
        groups = m.groupdict()
        name = ""
        for key in ("name", "name2"):
            raw = str(groups.get(key) or "").strip().strip(_CS_QUOTES)
            # 앞뒤 따옴표를 뗀 뒤에도 조사가 남을 수 있다("'요약' 이름으로" → 요약).
            raw = re.sub(r"(?:이?라는|이?라고|이?란)$", "", raw).strip()
            if raw and raw.lower() not in _CS_NOT_NAME:
                name = raw
                break
        if not name:
            continue
        # "재고 관리 시트도 하나" — 이름은 여러 낱말일 수 있다(2026-08-19 실측: '관리' 시트가 생겼다).
        span = m.span(m.lastindex or 1)
        try:
            idx = src.index(name)
        except ValueError:
            idx = span[0]
        name = extend_sheet_name_leftward(src, idx, name)
        return {
            "action": "excel_live.create_sheet",
            "params": {"sheet_name": name, "make_active": True},
            "reason": "빠른 규칙 기반 시트 생성",
        }
    return None


# "이 시트" · "현재 시트" · "여기 탭" — 이름이 아니라 활성 시트를 가리킨다.
# 이걸 이름으로 잡으면 없는 시트를 고치려다 실패한다(2026-08-19 게이트 ERROR 5건의 원인).
_RENAME_DEICTIC = frozenset(
    {
        "이", "그", "저", "요", "현재", "지금", "여기", "해당", "이번", "새", "시트", "탭", "워크시트", "sheet", "tab",
        # "지역성과라고 된 시트" 의 '된'을 이름으로 잡으면 없는 시트를 고치려 든다(2026-08-19 게이트).
        "된", "라고", "이라고", "하는", "있는", "그런", "저런", "이런",
        # 라벨 낱말이 옛 이름 자리로 새는 것도 막는다("탭 이름을 …" → old='이름").
        "이름", "명", "이름을", "워크",
    }
)
# 새 이름 뒤에 오는 조사. 이름 정규식이 이걸 삼키면 '지역별실적으'라는 시트가 생긴다.
_RENAME_TAIL = r"(?:으로|로|이라고|라고|으루|루)"
# 조사와 동사 사이에 "이름"이 한 번 더 끼는 꼴: "…지역별실적으로 이름 바꿔 부탁드려요"
_RENAME_MID = r"(?:\s*(?:이름|명)\s*(?:을|를|은|는)?)?"
_RENAME_VERB = r"(?:바꿔|바꾸|바꺼|바꺼줘|변경|고쳐|고치|rename|다시\s*지어|지어|바꿔놔|바꿔둬|해\s*줘|해주|부탁)"
_RENAME_LABEL = r"(?:시트|sheet|탭|tab|워크시트|시트명|탭명)"

_RENAME_PATTERNS = (
    # "지역성과 시트 이름을 지역별실적으로 바꿔" / "이 시트 이름 지역별실적으로 바꿔"
    re.compile(
        r"(?:(?P<old>[A-Za-z0-9_가-힣]+)\s*)?" + _RENAME_LABEL + r"\s*(?:탭\s*)?(?:이름|명)?\s*(?:을|를|은|는)?\s*"
        r"(?P<new>[A-Za-z0-9_가-힣]+?)\s*" + _RENAME_TAIL + _RENAME_MID + r"\s*" + _RENAME_VERB,
        re.IGNORECASE,
    ),
    # "시트 이름을 지역성과에서 지역별실적으로" — 옛 이름이 '에서'로 붙는 꼴
    re.compile(
        _RENAME_LABEL + r"\s*(?:이름|명)?\s*(?:을|를)?\s*(?P<old>[A-Za-z0-9_가-힣]+?)\s*(?:에서|를|을)\s*"
        r"(?P<new>[A-Za-z0-9_가-힣]+?)\s*" + _RENAME_TAIL,
        re.IGNORECASE,
    ),
    # 동사 없이 끝나는 꼴: "탭 이름 지역별실적으로" · "시트명 지역별실적으로"
    re.compile(
        r"(?:(?P<old>[A-Za-z0-9_가-힣]+)\s*)?" + _RENAME_LABEL + r"\s*(?:탭\s*)?(?:이름|명)?\s*(?:을|를|은|는)?\s*"
        r"(?P<new>[A-Za-z0-9_가-힣]+?)\s*" + _RENAME_TAIL + r"\s*[~.!?…]*$",
        re.IGNORECASE,
    ),
    # 조사도 없는 꼴: "시트 이름 지역별실적!" — 이름 자리에 하나만 남는다
    re.compile(
        r"^(?:(?P<old>[A-Za-z0-9_가-힣]+)\s*)?" + _RENAME_LABEL + r"\s*(?:탭\s*)?(?:이름|명)\s*(?:을|를|은|는)?\s*"
        r"(?P<new>[A-Za-z0-9_가-힣]+)\s*[~.!?…]*$",
        re.IGNORECASE,
    ),
    # "지역성과라고 된 시트 이름을 지역별실적으로" — 옛 이름이 '라고 된'으로 붙는 꼴
    re.compile(
        r"(?P<old>[A-Za-z0-9_가-힣]+?)\s*(?:이?라고)\s*(?:된|되어\s*있는|불리는)\s*" + _RENAME_LABEL
        + r"\s*(?:이름|명)?\s*(?:을|를|은|는)?\s*(?P<new>[A-Za-z0-9_가-힣]+?)\s*" + _RENAME_TAIL,
        re.IGNORECASE,
    ),
    # "지역성과 시트는 지역별실적이라고 이름 바꿔놔" — 새 이름이 '(이)라고'로 붙고 '이름'이 뒤에 온다
    re.compile(
        r"(?:(?P<old>[A-Za-z0-9_가-힣]+)\s*)?" + _RENAME_LABEL + r"\s*(?:은|는|을|를)?\s*"
        r"(?P<new>[A-Za-z0-9_가-힣]+?)\s*(?:이?라고)\s*(?:이름|명)\s*" + _RENAME_VERB,
        re.IGNORECASE,
    ),
    # 새 이름이 앞: "지역별실적으로 바꿔줘 지역성과 시트 이름"
    re.compile(
        r"^(?P<new>[A-Za-z0-9_가-힣]+?)\s*" + _RENAME_TAIL + _RENAME_MID + r"\s*" + _RENAME_VERB
        + r"[^\n]{0,12}?(?:(?P<old>[A-Za-z0-9_가-힣]+)\s*)?" + _RENAME_LABEL + r"\s*(?:이름|명)",
        re.IGNORECASE,
    ),
)


# "열 너비 자동 맞춤" — 게이트에서 규칙 0건이라 24문장 전부 모델(해석 카드)로 갔다(2026-08-19).
# 결정적으로 풀리는 동작인데 모델을 부르면 느리고 흔들린다.
_AUTOFIT_WIDTH = re.compile(
    r"(?:열|칸|칼럼|컬럼|column)\s*(?:너비|폭|width|길이|간격)"
    r"|(?:너비|폭)\s*(?:자동|auto)"
    r"|(?:열|칼럼|컬럼)\s*(?:을|를)?\s*(?:넓혀|늘려|줄여)"
)
_AUTOFIT_HOW = re.compile(
    r"자동|auto\s*fit|autofit|맞춤|맞춰|맞추|마춰|마추|맏게|맞게|조정|조졍|안\s*잘리게|"
    r"글자\s*길이|내용에\s*(?:맞|딱)|딱\s*맞|잘리|잘려|넓혀|알아서|###"
)
# 폭을 **숫자로** 지정한 요청은 자동 맞춤이 아니다("열 너비 15로").
_AUTOFIT_EXPLICIT_WIDTH = re.compile(r"(?:너비|폭|width)\s*(?:를|을)?\s*\d+")


def _quick_autofit_step(text: str, preferred_range: str) -> dict[str, Any] | None:
    """"열 너비 내용에 맞게 자동으로 맞춰" — 열 너비 자동 맞춤은 결정적이다."""
    src = str(text or "")
    if not _AUTOFIT_WIDTH.search(src) or not _AUTOFIT_HOW.search(src):
        return None
    if _AUTOFIT_EXPLICIT_WIDTH.search(src):
        return None
    # "행 높이"는 다른 동작이다.
    if re.search(r"(?:행|줄)\s*(?:높이|height)", src) and not re.search(r"(?:열|칼럼|컬럼|column)", src):
        return None
    params: dict[str, Any] = {}
    target = str(preferred_range or "").strip()
    if target and ":" in target:
        params["target_range"] = target.upper()
    return {
        "action": "excel_live.autofit_columns",
        "params": params,
        "reason": "빠른 규칙 기반 열 너비 자동 맞춤",
    }



def _quick_rename_sheet_step(text: str) -> dict[str, Any] | None:
    """"지역성과 시트 이름을 지역별실적으로 바꿔" — 시트 이름 변경은 결정적이다.

    2026-08-19 블라인드 게이트에서 이 과제는 규칙 0건 · 오류 5 · 오실행 3으로 가장 나빴다.
    기존 규칙은 새 이름이 조사를 먹고('지역별실적으'), 지시어를 시트 이름으로 잡았다('이').
    """
    src = str(text or "").strip()
    if not src or (not re.search(r"(?:이름|명)", src) and not re.search(r"rename", src, re.IGNORECASE)):
        return None
    # "시트 만들어/삭제" 같은 다른 시트 동작과 섞이지 않게.
    if re.search(r"(만들|생성|추가|삭제|지워|없애|복사)", src):
        return None
    # "A1에 시트 이름 써줘"는 **쓰기**다 — 셀을 지목하고 쓰기 동사가 있으면 이름 변경이 아니다.
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*(?:셀|칸)?\s*에", src) and re.search(
        r"(입력|기록|써|적어|넣어|채워)", src
    ):
        return None
    # 앞 패턴이 지시어("된")를 옛 이름으로 물면 뒤의 정확한 패턴("X라고 된 …")을 못 쓴다.
    # **옛 이름을 제대로 집은 후보를 우선**하고, 없을 때만 활성 시트 대상으로 떨어진다.
    fallback: dict[str, Any] | None = None
    for pattern in _RENAME_PATTERNS:
        m = pattern.search(src)
        if not m:
            continue
        new_name = str(m.group("new") or "").strip().strip("\"'")
        old_name = str(m.groupdict().get("old") or "").strip().strip("\"'")
        if not new_name or new_name.lower() in _RENAME_DEICTIC:
            continue
        # 지시어("이 시트")면 대상은 활성 시트다 — 이름을 넘기지 않는다.
        if old_name.lower() in _RENAME_DEICTIC:
            old_name = ""
        if old_name and old_name == new_name:
            continue
        params: dict[str, Any] = {"new_name": new_name}
        if old_name:
            params["sheet_name"] = old_name
        step = {
            "action": "excel_live.rename_sheet",
            "params": params,
            "reason": "빠른 규칙 기반 시트 이름 변경",
        }
        if old_name:
            return step
        if fallback is None:
            fallback = step
    return fallback



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


def _intent_first_enabled() -> bool:
    """통역 AI를 규칙표보다 앞에 세우는 실험 스위치().

    로드맵 2단계의 가설을 **재기 위한** 것이다. 기본은 꺼짐 — 제품 동작은 안 바뀐다.
    환경변수를 매번 읽는다(캐시하지 않는다): 한 프로세스 안에서 켜고 끄며
    A/B를 돌릴 수 있어야 하고, 이 판정은 턴당 한 번이라 비용이 문제가 되지 않는다.
    """
    return str(os.environ.get("OFFICECLAW_INTENT_FIRST", "")).strip().lower() in {"1", "true", "yes", "on"}


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
    if any(token in lowered for token in ["인쇄", "pdf", "출력", "제출"]) or re.search(r"(?<![a-z0-9])a4(?![a-z0-9])", lowered):
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

    if hints["intent"] == "formula":
        hints["params"].update(_extract_formula_common_params(text))

    if hints["intent"] == "sort":
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

    if hints["intent"] == "filter":
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

    if hints["intent"] == "pivot":
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

    if hints["intent"] == "chart":
        # 종류 어휘는 한 곳(_CHART_KIND_WORDS)만 본다 — 슬롯 파서가 도넛/영역/분산·
        # "선그래프"(띄어쓰기 없음)를 몰라 종류를 말했는데도 되묻기가 반복됐다
        # (2026-08-18 ex5 대화형 각본 실측: "GMV로 도넛 차트"에 종류 질문 → 다음
        # 턴까지 슬롯이 삼킴).
        kind_from_words = _chart_kind_from_message(lowered)
        if kind_from_words:
            hints["params"]["chart_type"] = kind_from_words
        elif any(token in lowered for token in ["변화"]):
            hints["params"]["chart_type"] = "line"
        elif "비교" in lowered:
            hints["params"]["chart_type"] = "bar"
        if "발표" in lowered:
            hints["params"]["title"] = "발표용 핵심 차트"
        if "매출" in lowered:
            hints["params"]["title"] = "매출 차트"
        quoted_title = _extract_quoted_chart_title(text)
        if quoted_title:
            hints["params"]["title"] = quoted_title

    if hints["intent"] == "validate":
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

    if hints["intent"] == "protect":
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

    if hints["intent"] == "consolidate":
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

    if hints["intent"] == "automation":
        if "power query" in lowered or "새로고침" in lowered:
            hints["params"]["mode"] = "refresh"
        else:
            hints["params"]["mode"] = "vba"
        macro_match = re.search(r"매크로\s*([A-Za-z_][A-Za-z0-9_\.]*)", text)
        if macro_match is None:
            macro_match = re.search(r"([A-Za-z_][A-Za-z0-9_\.]*)\s*매크로", text)
        if macro_match:
            hints["params"]["macro_name"] = str(macro_match.group(1))

    if hints["intent"] == "compare":
        sheet_matches = re.findall(r"([^\s,]+)\s*시트", text)
        if len(sheet_matches) >= 2:
            hints["params"]["left_sheet"] = sheet_matches[0]
            hints["params"]["right_sheet"] = sheet_matches[1]
        if "주문번호" in lowered:
            hints["params"]["compare_key"] = "주문번호"
        if "금액" in lowered:
            hints["params"]["compare_fields"] = ["금액"]

    if hints["intent"] == "forecast":
        horizon_match = re.search(r"(\d{1,2})\s*(개월|달|월|주)", lowered)
        if horizon_match:
            hints["params"]["horizon"] = int(horizon_match.group(1))

    if hints["intent"] == "print":
        if "a4" in lowered:
            hints["params"]["paper"] = "A4"
        if "가로" in lowered:
            hints["params"]["orientation"] = "landscape"
        elif "세로" in lowered:
            hints["params"]["orientation"] = "portrait"
        if "pdf" in lowered:
            hints["params"]["output_format"] = "pdf"

    if hints["intent"] == "safety":
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

    if hints["intent"] == "debug":
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

    if hints["intent"] == "performance":
        if "피벗" in lowered:
            hints["params"]["suspect"] = "pivot_refresh"
        elif "수식" in lowered:
            hints["params"]["suspect"] = "formula_recalc"
        elif "조건부서식" in lowered:
            hints["params"]["suspect"] = "conditional_formatting"

    if hints["intent"] == "explain":
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
    if digest:
        retargeted, _prefix = _retarget_sheet_by_headers(text, entry, digest, "")
        if retargeted is not None:
            entry = retargeted
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
    source_sheet = str(entry.get("name") or "").strip() or None
    return {
        "action": "excel_live.pivot_table",
        "params": {
            "source_range": "__ACTIVE_SELECTION__",
            "row_field": row_field,
            "value_field": value_field,
            "agg": agg,
            "source_sheet": source_sheet,
            "output_sheet": _extract_output_sheet_from_text(text) or "피벗1",
        },
        "reason": "원문이 요청한 피벗 단계 보완",
    }


# 대상을 좁히지 못한 채 범위 전체에 서식을 먹이는 액션들. "상위 3개"가 붙은 문장에서
# 이 액션이 계획에 남아 있으면 한정어가 실행에 반영될 곳이 없다는 뜻이다.
_RANK_LIMIT_FORMAT_ACTIONS = frozenset(
    {"excel_live.fill_range", "excel_live.highlight_by_condition"}
)


def _rank_limited_format_plan(plan, req, *, digest):
    """"상위 N 강조" 계획을 기준값이 박힌 한 단계로 바꾼다. 못 바꾸면 None.

    계획에 서식 단계가 없으면(정렬만 요청한 경우 등) 손대지 않는다. 기준 열이
    모호하거나 값이 N개보다 적으면 역시 None을 돌려주고, 그 경우 원래 계획이
    그대로 간다 — 여기서 열 하나를 찍는 것은 또 다른 조용한 오답이다.
    """
    if not any(step.action in _RANK_LIMIT_FORMAT_ACTIONS for step in plan):
        return None

    def _read_column(range_ref: str):
        service = get_excel_live_service()
        resolved_wb = _resolve_workbook_id(service, req.workbook_id)
        resolved_sheet = _resolve_sheet_name(service, resolved_wb, req.sheet_name)
        # 실무 파일의 금액 열은 대부분 수식이다. 그대로 읽으면 "=I2*J2" 문자열이 와서
        # 숫자가 하나도 없는 열로 보이고, 상위 N을 정할 수 없다고 판단해 버린다.
        reader = getattr(service, "read_computed_range", None) or service.read_range
        return (reader(resolved_wb, resolved_sheet, range_ref) or {}).get("values") or []

    try:
        return rank_limit.resolve_step(
            req.message,
            digest,
            sheet_name=req.sheet_name,
            read_column=_read_column,
            fill_color=_rank_limit_fill_color(plan),
        )
    except ExcelLiveError:
        return None


def _rank_limit_fill_color(plan) -> str:
    """원래 계획이 고른 색을 그대로 이어받는다. 사용자가 말한 색이 여기 들어 있다."""
    for step in plan:
        if step.action in _RANK_LIMIT_FORMAT_ACTIONS:
            color = str(step.params.get("fill_color") or step.params.get("color") or "").strip()
            if color:
                return color
    return "#FFFF00"


# 원문에 나온 차트 종류 → 실행기가 받는 이름. 실행기가 실제로 만들 수 있는 값만 둔다.
# 2026-08-16: 도넛이 pie로 접혀 있었다. DoughnutChart를 구현했으니 갈라 준다.
# 영역·분산도 구현했으므로 되묻지 않고 확정한다. 도넛을 먼저 봐야 한다 —
# "도넛"보다 "원형"이 앞에 오면 도넛 요청이 원형으로 떨어진다.
_CHART_KIND_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(도넛|도너츠|donut|doughnut|링\s*차트)", re.IGNORECASE), "doughnut"),
    (re.compile(r"(막대|bar|컬럼|column)", re.IGNORECASE), "bar"),
    (re.compile(r"(원형|파이|pie|원\s*그래프|원\s*차트)", re.IGNORECASE), "pie"),
    (re.compile(r"(분산|산점|scatter)", re.IGNORECASE), "scatter"),
    (re.compile(r"(영역\s*차트|면적\s*차트|area\s*chart)", re.IGNORECASE), "area"),
    (re.compile(r"(선\s*그래프|꺾은|라인|line|추이|트렌드)", re.IGNORECASE), "line"),
    # 함의어는 맨 끝 — "비율을 원형 차트로"는 명시 종류(원형)가 이겨야 한다.
    # "비중/비율"만 말했을 때의 부분-전체 그림 기본값이 도넛이다.
    (re.compile(r"(비중|비율|구성비)", re.IGNORECASE), "doughnut"),
)


def _chart_kind_from_message(message: str) -> str:
    text = str(message or "")
    for pattern, kind in _CHART_KIND_WORDS:
        if pattern.search(text):
            return kind
    return ""


def _extract_quoted_chart_title(message: str) -> str:
    """문장에 따옴표로 적힌 차트 제목을 뽑는다."""
    match = re.search(r"['\"‘’“”](.{2,80}?)['\"‘’“”]", str(message or ""))
    title = str(match.group(1) if match else "").strip()
    if not title or re.fullmatch(r"[A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?", title, re.IGNORECASE):
        return ""
    return title


def _chart_step_from_message(message: str, *, default_kind: str = ""):
    """원문이 차트를 요구했는데 계획에 차트가 없을 때 채워 넣을 단계.

    작은 모델은 "지역별 금액 막대 차트 만들어줘"를 피벗 한 단계로 계획하고,
    그 결과 시트 이름을 "차트"라고 붙인 뒤 끝낸다. 시트 이름만 차트일 뿐 도형은
    없는데 검증기는 피벗의 사후조건만 보므로 통과시키고, 사용자는 성공했다는 말을
    들은 채 있지도 않은 그래프를 찾게 된다.

    종류를 말하지 않았으면 여기서 만들지 않는다 — 선/막대/원형은 결과물의 성격이
    서로 다르고, 기본값으로 밀면 또 다른 조용한 오답이 된다. 그건 되묻기로 넘긴다.
    단 집계/피벗 뒤에 따라붙는 차트는 종류가 부수라서 default_kind를 받을 수 있다.
    """
    kind = _chart_kind_from_message(message) or str(default_kind or "").strip()
    if not kind:
        return None
    # "G2:G9로 막대 그래프" — 문장에 범위가 있으면 그게 원본이다. 활성 선택으로
    # 고정하면 표가 둘인 시트에서 엉뚱한 영역을 그린다(2026-08-18 사람 말투 각본
    # 정찰). 셀 하나짜리 참조(A4 등)는 범위가 아니므로 제외.
    explicit = RANGE_REF_PATTERN.search(str(message or ""))
    source = explicit.group(0).upper() if explicit and ":" in explicit.group(0) else "__ACTIVE_SELECTION__"
    params: dict[str, Any] = {"source_range": source, "chart_type": kind}
    title = _extract_quoted_chart_title(message)
    if title:
        params["title"] = title
    return {
        "action": "excel_live.create_chart",
        "params": params,
        "reason": "원문이 요청한 차트 단계 보완",
    }


# 피벗 **주입**은 원문이 피벗을 명시했을 때만 한다. '집계·요약'은 group_by·수식으로도
# 정당하게 풀리는 말이라, 그 낱말만 보고 피벗을 끼워 넣으면 이미 옳게 계획된 group_by
# 문형에 시키지 않은 피벗 시트가 따라붙는다(감사 B4). 시트 이름이 '요약'이기만 해도
# 걸린다(블라인드 624에 그런 문장 27건 실측). 근거 게이트(_ACTION_EVIDENCE)는 반대로
# 느슨한 게 맞다 — 플래너가 '집계해줘'를 피벗으로 푸는 것 자체는 정당한 해석이다.
_PIVOT_EXPLICIT = re.compile(r"(피벗|pivot)", re.IGNORECASE)
# 이미 계획에 든 집계 액션 — 있으면 사용자가 말한 집계는 계획에 있는 것이므로 겹쳐 넣지 않는다.
_AGGREGATE_PLAN_ACTIONS = frozenset(
    {
        "excel_live.group_by_aggregate",
        "excel_live.calculate_column_stat",
    }
)


def _should_inject_pivot_step(message: str, current_plan: list[Any]) -> bool:
    """빠진 피벗 단계를 규칙으로 채울지 — 명시된 피벗이 계획에 없을 때만."""
    actions = {str(getattr(step, "action", "")) for step in current_plan}
    if "excel_live.pivot_table" in actions or actions & _AGGREGATE_PLAN_ACTIONS:
        return False
    return bool(_PIVOT_EXPLICIT.search(str(message or "")))


def _chart_accompanies_aggregate(message: str, plan: list[Any] | None) -> bool:
    """집계가 본 작업이고 차트는 그 결과를 그리는 부수 단계인지."""
    if any(getattr(step, "action", "") == "excel_live.pivot_table" for step in plan or []):
        return True
    return bool(_ACTION_EVIDENCE["excel_live.pivot_table"].search(str(message or "")))


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
                # "건수를 셀 기준 열" — 사용자가 무슨 말인지 모르겠다고 한 문구다
                # (2026-08-18). 무엇으로 이해했는지 밝히고 빠져나갈 길도 준다.
                return (
                    "개수 세기로 이해했어요. 어느 열의 값을 셀까요? 예: 상태 열. "
                    "개수 세기가 아니라면 원하시는 작업을 다시 말씀해 주세요."
                )
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
        # "중복된 행 지워줘"는 행 전체가 같은 걸 지우라는 말이다. 실행기는 키 없이
        # 부르면 이미 전체 열 기준으로 돈다 — 여기서 기준 열을 물으면 과잉 질문이다
        # (2026-08-17 배터리 실측). 특정 열 중복("전화번호 중복")일 때만 묻는다.
        if not re.search(r"중복(된|되는)?\s*(행|줄|row)", str(slot.params.get("raw_message") or ""), re.IGNORECASE):
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
    # 시트를 이름으로 지목한 턴에서만 갱신한다. 뒤 턴에 언급이 없다고 지우면
    # 처음 지목이 사라진다 — 그게 바로 고치려는 버그다.
    named_sheet = _named_sheet_in_text(req.message)
    if named_sheet:
        slot.explicit_sheet_name = named_sheet
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
                if from_user:
                    slot.headers_from_user = True
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

    # 사용자가 끌어 둔 영역이 곧 표 크기다.
    #
    # 2026-08-16 실측: A1:D9를 드래그하고 "이 부분에 표 만들어줘"라고 했는데
    # `start_cell`을 만들 때 `.split(":")[0]`으로 왼쪽 위 칸만 남기고 크기를 버려,
    # "표 크기와 헤더를 알려주세요"로 되물었다. 끌어 놓고 다시 크기를 불러야 하면
    # 드래그가 아무 의미가 없다.
    #
    # 사용자가 문장으로 크기를 말했으면(`5*5`) 그게 이미 채워져 있으므로 건드리지 않는다.
    #
    # **가리켰을 때만** 크기로 쓴다. 지시어 없는 "표 만들어줘"까지 선택으로 크기를
    # 정하면, 우연히 남아 있던 선택이 표 크기가 된다 — 사용자가 의도한 적 없는 크기다.
    # (이 조건을 안 걸었더니 기존 테스트 5건이 되묻기 대신 곧장 표를 만들었다.)
    dragged = _normalize_range_text(getattr(req, "context_range", None) or "")
    if ":" not in dragged and mentions_selection(req.message):
        # 붙여넣기 경로는 범위가 context_range가 아니라 **문장 앞에 인라인**으로 온다
        # ("A1:D13 여기에 출석부를…"). 2026-08-17 실측: 그래서 이 크기 소비 로직을
        # 못 타고 "표 크기를 알려주세요"로 되물었다 — 2026-08-16에 고친 바로 그
        # 버그가 다른 입구로 되살아난 것이다.
        inline = re.search(
            r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}:[A-Za-z]{1,3}\d{1,7}(?![A-Za-z0-9])", str(req.message or "")
        )
        if inline:
            dragged = _normalize_range_text(inline.group(0))
    if (
        ":" in dragged
        and mentions_selection(req.message)
        and (slot.rows is None or slot.cols is None)
    ):
        rect = parse_rect(dragged)
        if rect:
            top, left, bottom, right = rect
            # 빠진 축만 채운다. 2026-08-18 GUI 실측: 헤더 4개로 열이 먼저 정해지자
            # 이 블록 전체가 건너뛰어져 A1:D9를 붙여넣고도 행이 프리셋 기본값
            # 32가 됐다("요청한 위치와 장소에 만드는지 모르겠는데").
            if slot.rows is None:
                slot.rows = max(1, min(100, bottom - top + 1))
            if slot.cols is None:
                slot.cols = max(1, min(50, right - left + 1))
            if not slot.start_cell:
                slot.start_cell = dragged.split(":")[0]

    # 템플릿 질문에 대한 답을 실제로 해석한다.
    #
    # 2026-08-17 실측: "일별/월별 중 어떤 형식으로?"라고 물어 놓고 "일별로
    # 만들어줘"라는 답을 해석하는 코드가 없었다. 긍정어("응/네")만 통과라서 같은
    # 질문을 또 했고, 되묻기 한도가 차자 프리셋 헤더도 버린 채 5×5 빈 표가 나갔다.
    if slot.template_key:
        template = get_table_preset(slot.template_key)
        variant = find_variant(template, req.message)
        if variant is not None:
            variant_headers = list(variant[1])
            if not user_header_explicit and not slot.headers_from_user:
                # 대상 범위를 지목했으면(13×4) 그 폭에 맞춰 자른다.
                # 사용자가 직접 지정한 헤더는 프리셋 선택지가 덮지 못한다(2026-08-18 실측).
                slot.headers = variant_headers[: slot.cols] if slot.cols else variant_headers
            if slot.cols is None:
                slot.cols = max(1, min(50, len(slot.headers or variant_headers)))
            if slot.rows is None and template is not None:
                slot.rows = template.default_rows
            slot.template_follow_up_question = ""
        elif current is not None and slot.template_question_asked and template is not None:
            # 질문에 선택지 밖의 답이 왔다. 다시 물으면 대화가 제자리를 돈다 —
            # 답이 뭐든 기본형으로 진행한다(구체 지정이 있으면 위에서 이미 반영됨).
            if not slot.headers:
                slot.headers = list(template.headers)[: slot.cols] if slot.cols else list(template.headers)
            if slot.cols is None:
                slot.cols = template.default_cols
            if slot.rows is None:
                slot.rows = template.default_rows
            slot.template_follow_up_question = ""

    slot.updated_at_ts = now
    return slot


# 같은 질문을 이 횟수만큼 하고도 크기를 못 받으면 기본값으로 만든다.
_MAX_TABLE_FOLLOW_UPS = 2


def _build_table_follow_up(slot: PendingCreateTableSlots, *, last_call: bool = False) -> str:
    # 템플릿 질문이 크기 질문보다 먼저다 — 답(일별/월별)이 헤더까지 정하기 때문이다.
    # 단, **한 번만**, 그리고 **헤더를 모를 때만** — 사용자가 헤더를 지정했으면
    # 형식 질문은 정할 것이 없는 빈 질문이다(2026-08-18 실측).
    if slot.template_follow_up_question and not slot.template_question_asked and not slot.headers:
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


def _safe_table_start_cell(slot, digest: dict[str, Any]) -> str:
    """표를 놓을 시작 칸. 이미 데이터가 있으면 그 아래로 내린다.

    2026-08-17 멀티턴 실측: "매출 시트에 표 만들어줘" → "5행 4열로" 두 턴 만에
    시트의 머리글과 데이터가 통째로 사라졌다. `create_table`은 빈 값을 쓰는데
    시작 칸이 A1로 기본값이라, 데이터가 A1부터 있는 시트를 그대로 덮었다.

    사용자가 칸을 지목했으면 그대로 쓴다 — 덮어쓰기를 원할 수도 있고, 그건 승인
    카드에서 보인다. 지목하지 않았을 때만 안전한 자리를 고른다.
    """
    named = _normalize_range_text(getattr(slot, "start_cell", "") or "")
    if named:
        return named

    sheet_name = str(getattr(slot, "explicit_sheet_name", "") or "").strip()
    sheets = (digest or {}).get("sheets") or []
    entry = None
    for candidate in sheets:
        if sheet_name and str(candidate.get("name") or "") == sheet_name:
            entry = candidate
            break
    if entry is None and not sheet_name:
        active = str((digest or {}).get("active_sheet") or "")
        entry = next((c for c in sheets if str(c.get("name") or "") == active), None)
    if entry is None:
        return "A1"

    used = str(entry.get("used_range") or "").replace("$", "").upper()
    match = re.search(r"([A-Z]{1,3})(\d+)$", used.rpartition(":")[2] or used)
    if not match:
        return "A1"
    last_row = int(match.group(2))
    # 한 칸짜리 사용범위(A1)는 빈 시트다. 그대로 A1에서 시작한다.
    if used in {"", "A1"} or last_row <= 1:
        return "A1"
    return f"A{last_row + 2}"  # 한 줄 띄우고 아래에


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

    def _finish(built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """사용자가 지목한 시트를 모든 단계에 박아 준다.

        안 박으면 하류가 활성 시트로 떨어뜨린다. 되묻기를 거치면 마지막 발화에는
        시트 언급이 없으므로, 그 경로에서 남의 시트를 말없이 덮어쓰는 사고가 났다.
        """
        sheet = str(slot.explicit_sheet_name or "").strip()
        if sheet:
            for step in built:
                step["params"]["sheet_name"] = sheet
        return built

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
            return _finish(steps)

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
    return _finish(steps)


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
    """액션을 실행하되, Excel이 쓰기를 거부하면 파일 편집으로 갈아타고 다시 시도한다.

    왜 예외를 잡아서 판단하는가 (2026-08-17 실측):
        이 PC의 Excel은 정품 인증이 안 돼 COM 편집이 전부 막혀 있다. 그런데
        **상태 플래그로는 그걸 알 수 없다** —

            ReadOnly: False · 시트보호: False · 통합문서보호: False
            그런데 빈 셀 F1에 값 하나 쓰기조차 실패:
              com_error(-2147352567, …, (0, None, None, None, 0, -2146827284))

        `-2146827284`(0x800A03EC)는 Excel의 일반 편집 거부다. 읽기 전용 플래그만
        보던 이전 버전은 이 경우를 통과시켜, 사용자는 매번 날 COM 덤프를 봤다.
        플래그를 더 뒤지는 대신 **실제 실패를 신호로 삼는다.**

    폴백 방법은 읽기 전용 브리지와 같다: Excel에서 통합문서를 닫아 파일 잠금을
    풀고(Excel이 붙들고 있으면 openpyxl도 PermissionError다), 파일을 직접 편집한
    뒤 다시 열어 준다.
    """
    service = get_excel_live_service()

    if action in EDIT_ACTIONS:
        flags = read_protection_flags(
            service, workbook_id=workbook_id, sheet_name=sheet_name
        )
        block = evaluate_write_block(action=action, flags=flags, is_edit_action=True)
        # 시트·구조 보호처럼 파일을 닫아도 안 풀리는 건 여기서 막고 끝낸다.
        if block.blocked and not can_bridge(flags):
            raise ExcelEditBlockedError(block.reason, code=block.code)

    try:
        return _dispatch_action(
            action=action, params=params, workbook_id=workbook_id, sheet_name=sheet_name
        )
    except Exception as exc:
        if action not in EDIT_ACTIONS or not _looks_like_com_write_refusal(exc):
            raise
        bridge = release_workbook(service, workbook_id=workbook_id)
        if not bridge.released:
            raise
        invalidate_excel_engine_cache()
        invalidate_workbook_digest()
        try:
            # 경로를 반드시 넘긴다 — 통합문서를 닫으면 file 엔진이 대상을 못 고른다.
            return _dispatch_action(
                action=action,
                params=params,
                workbook_id=bridge.path,
                sheet_name=sheet_name,
            )
        finally:
            invalidate_excel_engine_cache()
            restore_workbook(service, bridge.path)
            invalidate_workbook_digest()


def _dispatch_action(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> dict[str, Any]:
    """액션 하나를 실제 서비스 호출로 옮긴다. 재시도·폴백은 _execute_action이 맡는다."""
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

    if action == "excel_live.rename_sheet":
        resolved_wb = _resolve_workbook_id(service, workbook_id or str(params.get("workbook_id", "")).strip() or None)
        source_sheet = str(params.get("sheet_name") or params.get("old_name") or "").strip()
        new_name = str(params.get("new_name") or params.get("name") or "").strip()
        if not source_sheet:
            raise WorksheetNotFoundError("rename_sheet에는 sheet_name이 필요합니다.")
        if not new_name:
            raise WorksheetNotFoundError("rename_sheet에는 new_name이 필요합니다.")
        return service.rename_sheet(
            workbook_id=resolved_wb,
            sheet_name=source_sheet,
            new_name=new_name,
        )

    if action == "excel_live.delete_sheet":
        resolved_wb = _resolve_workbook_id(service, workbook_id or str(params.get("workbook_id", "")).strip() or None)
        target_sheet = str(params.get("sheet_name") or params.get("name") or "").strip()
        if not target_sheet:
            raise WorksheetNotFoundError("delete_sheet에는 sheet_name이 필요합니다.")
        return service.delete_sheet(
            workbook_id=resolved_wb,
            sheet_name=target_sheet,
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
        fill_color = str(params.get("fill_color", "#FFFF00"))
        compare_column = str(params.get("compare_column") or "").strip().upper() or None
        value = params.get("value")
        try:
            threshold = float(params.get("threshold", 0) or 0)
        except (TypeError, ValueError):
            if value is None:
                value = params.get("threshold")
            threshold = 0.0
        extra: dict[str, Any] = {}
        if compare_column:
            extra["compare_column"] = compare_column
        if value is not None and str(value).strip() != "":
            extra["value"] = value
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

    if action == "excel_live.set_font":
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
        size_raw = params.get("size")
        size = None if size_raw in {None, ""} else float(size_raw)
        return service.set_font(
            resolved_wb,
            resolved_sheet,
            target_range,
            bold=None if params.get("bold") is None else bool(params.get("bold")),
            name=str(params.get("name") or "").strip() or None,
            size=size,
            color=str(params.get("color") or "").strip() or None,
            align=str(params.get("align") or "").strip() or None,
        )

    if action == "excel_live.convert_to_excel_table":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        target_range = _resolve_runtime_range_ref(
            service,
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            raw_range=str(params.get("target_range") or "__USED_RANGE__"),
            for_cell=False,
        )
        return service.convert_to_excel_table(
            resolved_wb,
            resolved_sheet,
            target_range,
            table_name=str(params.get("table_name") or ""),
            has_header=bool(params.get("has_header", True)),
        )

    if action == "excel_live.apply_formula_cf":
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
        return service.apply_formula_cf(
            resolved_wb,
            resolved_sheet,
            target_range,
            formula=str(params.get("formula") or ""),
            fill_color=str(params.get("fill_color") or "#FFC7CE"),
            font_color=str(params.get("font_color") or "#9C0006") or None,
        )

    if action == "excel_live.sort_range":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
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
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
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
            mode=str(params.get("mode", "hide")),
        )

    if action == "excel_live.dedupe_rows":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
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

    if action == "excel_live.delete_charts":
        resolved_wb = _resolve_workbook_id(service, workbook_id)
        resolved_sheet = _resolve_sheet_name(
            service, resolved_wb, str(params.get("sheet_name") or "").strip() or sheet_name
        )
        return service.delete_charts(resolved_wb, resolved_sheet)

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
        include = key in _XLWINGS_TRACE_PARAM_KEYS or key.endswith(("_range", "_sheet", "_cell"))
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
    method = action_name.removeprefix("excel_live.")
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

    if action == "excel_live.highlight_by_condition":
        # 조건에 맞는 셀이 0개인 것은 **정상 결과**다. "50 이상인 셀만 노란색"에서
        # 50 이상이 하나도 없으면 아무것도 안 칠하는 게 맞다. 그런데 이걸 실패로
        # 보면 abort_on_failure로 계획이 끊기고 롤백까지 돌며, 모델은 재계획에서
        # 조건을 느슨하게 만들어 결국 전 행을 칠한다
        # (2026-08-11 `0811-182610-armA-off` 이상치강조: `verify:failed×2 →
        #  replan:1` 뒤에 49행 전부 도색, 3/3회).
        #
        # 가르는 기준은 "칠했는가"가 아니라 "대 봤는가"다.
        scanned = int(result.get("scanned_cells", 0) or 0)
        if scanned >= 1:
            return True
        # 옛 실행기는 scanned_cells를 안 준다. 그때는 예전 기준으로 판정한다.
        if "scanned_cells" not in result:
            return int(result.get("changed_cells", 0) or 0) >= 1
        return False, "empty_target_range:조건을 검사할 셀이 없습니다"

    if action in {"excel_live.fill_range", "excel_live.apply_border"}:
        # 이 둘은 조건이 없다. 범위 전체를 칠하므로 changed_cells는 범위 크기와
        # 같고, 0이면 범위를 잘못 잡은 것이다.
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

    if action == "excel_live.set_font":
        return int(result.get("changed_cells", 0) or 0) >= 1

    if action == "excel_live.convert_to_excel_table":
        return bool(result.get("created"))

    if action == "excel_live.apply_formula_cf":
        return bool(result.get("applied"))

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

    if action == "excel_live.rename_sheet":
        return bool(result.get("renamed"))

    if action == "excel_live.delete_sheet":
        return bool(result.get("deleted"))

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


def _repair_for_request(req: Any) -> Callable[[PlanStep, Exception | str], PlanStep | None]:
    """이 요청의 통합문서 사실을 붙들어 둔 보정기.

    같은 파라미터로 다시 던지는 재시도는 한 번도 통한 적이 없다
    (`logs/diagnostics/*.jsonl` 410단계 중 재시도 3회 · 성공 0회). 그래서 고칠 게
    없으면 `excel_step_repair.repair_step`이 None을 돌려주고 실행기는 재시도를 접는다.
    """

    def _repair(step: PlanStep, failure: Exception | str) -> PlanStep | None:
        try:
            ctx = _build_repair_context(req)
        except Exception:
            return None
        repaired = repair_step(step, failure, ctx)
        if repaired is not None:
            trace_route(
                "repair:applied",
                why=f"{step.params} → {repaired.params}",
                action=step.action,
            )
        return repaired

    return _repair


def _build_repair_context(req: Any) -> RepairContext:
    service = get_excel_live_service()
    sheet_names: tuple[str, ...] = ()
    active_sheet: str | None = None
    try:
        listed = service.list_sheets(getattr(req, "workbook_id", None))
        sheet_names = tuple(str(name) for name in (listed.get("sheets") or []))
        active_sheet = listed.get("active_sheet") or None
    except Exception:
        pass
    return RepairContext(
        sheet_names=sheet_names,
        active_sheet=active_sheet,
        context_range=_normalize_range_text(getattr(req, "context_range", None)) or None,
        active_cell=_recent_range_by_workbook.get(_context_key(getattr(req, "workbook_id", None))),
    )


def _step_failure_line(step: Any) -> str:
    """실패한 단계 하나를 한국어 한 줄로. "작업에 실패했습니다"로 끝내지 않는다."""
    action = str(getattr(step, "action", "") or "")
    label = _ACTION_SUMMARY.get(action, action or "작업")
    params = getattr(step, "params", None) or {}
    where = ""
    for key in ("target_range", "range_ref", "source_range", "start_cell"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            where = f" {value.strip()}에서"
            break
    cause = str(getattr(step, "error", "") or getattr(step, "verify_detail", "") or "").strip()
    if not cause:
        return f"{label.rstrip('.')}{where} 실패했습니다."
    # `code:설명` 형태면 사람이 읽는 뒤쪽을 쓴다.
    readable = cause.split(":", 1)[1].strip() if ":" in cause and not cause.startswith("http") else cause
    return f"{label.rstrip('.')}{where} 실패했습니다 — {readable}"


def _verification_failure_reason(detail: str) -> str:
    code = str(detail or "").split(":", 1)[0].split(";", 1)[0].strip()
    message = _VERIFY_FAILURE_MESSAGES.get(code)
    if message:
        return message
    return "작업 실행이 안정성 검증을 통과하지 못했습니다. 복구 정보로 원상 복원이 가능합니다."


def _action_summary(action: str) -> str:
    return _ACTION_SUMMARY.get(action, "엑셀 변경 작업을 실행합니다.")


# 승인 카드 제목용 짧은 한국어 이름. 원시 액션 문자열("excel_live.create_table")이
# 제목으로 뜨던 것을 사람 말로 바꾼다(로드맵 2-3 프리뷰-승인).
_ACTION_DISPLAY = {
    "excel_live.delete_charts": "차트 삭제",
    "excel_live.write_range": "값 입력",
    "excel_live.create_table": "표 생성",
    "excel_live.highlight_by_condition": "조건부 강조",
    "excel_live.fill_range": "배경색 변경",
    "excel_live.apply_border": "테두리 적용",
    "excel_live.set_formula": "수식 적용",
    "excel_live.clear_range": "내용 비우기",
    "excel_live.sort_range": "정렬",
    "excel_live.filter_rows": "행 필터",
    "excel_live.dedupe_rows": "중복 제거",
    "excel_live.find_replace": "찾아 바꾸기",
    "excel_live.set_font": "글자 서식",
    "excel_live.set_number_format": "표시 형식",
    "excel_live.merge_cells": "셀 병합",
    "excel_live.delete_sheet": "시트 삭제",
}

# 데이터가 사라지거나 재배치되는 액션 — 카드에서 경고 표시.
_DESTRUCTIVE_ACTIONS = frozenset(
    {
        "excel_live.clear_range",
        "excel_live.filter_rows",
        "excel_live.dedupe_rows",
        "excel_live.find_replace",
        "excel_live.delete_sheet",
        "excel_live.drop_column",
        "excel_live.merge_cells",
    }
)

_SYMBOLIC_TARGET_LABELS = {
    "__ACTIVE_SELECTION__": "현재 선택 영역",
    "__USED_RANGE__": "데이터가 있는 전체 범위",
    "__ACTIVE_CELL__": "현재 셀",
}


def _step_target(params: dict[str, Any] | None) -> str:
    """승인 카드에 보여줄 대상 — 바인더가 확정한 실제 범위.

    "선택 범위에 경계선을 적용합니다"만 보고 승인하면 사용자는 **어디에** 적용되는지
    모른 채 승인한다(2026-08-17 실측: A1:M201 2,613셀에 적용된다는 것을 실행 후에야
    알았다). MS·Google 모두 실행 전 영향 범위 표시를 신뢰 수단으로 출시했다(로드맵 2-3).
    """
    p = params or {}
    raw = str(p.get("target_range") or p.get("range_ref") or p.get("start_cell") or "").strip()
    if not raw:
        sheet = str(p.get("sheet_name") or "").strip()
        return f"{sheet} 시트" if sheet else ""
    label = _SYMBOLIC_TARGET_LABELS.get(raw.upper(), raw.upper().replace("$", ""))
    sheet = str(p.get("sheet_name") or "").strip()
    return f"{sheet} 시트 {label}" if sheet else label


def _step_preview_line(index: int, action: str, params: dict[str, Any] | None) -> str:
    mark = "⚠ " if action in _DESTRUCTIVE_ACTIONS else ""
    target = _step_target(params)
    tail = f" — {target}" if target else ""
    return f"{index}. {mark}{_action_summary(action)}{tail}"


def _result_count_phrase(action: str, result: dict[str, Any] | None) -> str:
    """실행 결과의 규모를 사람 말로 — "몇 셀이 어떻게 됐는지"까지가 보고다."""
    r = result or {}
    emptied = r.get("emptied_values")
    if action == "excel_live.clear_range" and emptied is not None:
        return f" · 값 {int(emptied or 0)}개 삭제"
    if r.get("written_cells") is not None:
        return f" · 값 {int(r['written_cells'] or 0)}개 기록"
    if r.get("replaced_cells") is not None:
        return f" · {int(r['replaced_cells'] or 0)}개 셀 치환"
    if r.get("removed_rows") is not None:
        removed = int(r["removed_rows"] or 0)
        return f" · {removed}행 제거" if removed else " · 제거된 행 없음"
    if r.get("formula_applied_cells") is not None:
        return f" · 수식 {int(r['formula_applied_cells'] or 0)}칸"
    if r.get("changed_cells") is not None:
        return f" · {int(r['changed_cells'] or 0)}개 셀"
    if r.get("rows") is not None and r.get("cols") is not None:
        return f" · {r['rows']}×{r['cols']}"
    return ""


def _build_execution_report(steps: list[Any]) -> str:
    """실행된 매 단계를 "액션 — 실제 대상 · 규모"로 보고한다.

    2026-08-18 사용자 요구: "실행할 때마다 어떤 방식으로 수정을 진행하는지
    나오게, 화면 정확성 최대치로." 지금까지는 대표 액션 한 줄("완료되었습니다")만
    보여서, 3단계가 돌아도 무엇이 어디에 일어났는지 화면으로는 알 수 없었다.
    검증은 통과가 기본이므로 예외(검증 안 됨·재시도)만 표시해 소음을 줄인다.
    """
    visible = [s for s in steps if s.action not in _ANCILLARY_REPORT_ACTIONS]
    if not visible:
        return ""
    lines: list[str] = []
    for idx, s in enumerate(visible, start=1):
        label = _ACTION_DISPLAY.get(s.action, _action_summary(s.action))
        result = s.result or {}
        target = str(result.get("address") or "").replace("$", "") or _step_target(s.params)
        tail = _result_count_phrase(s.action, result)
        marks = ""
        if getattr(s, "retried", False):
            marks += " (재시도 후 성공)"
        if not getattr(s, "verified", True):
            marks += " (검증 안 됨)"
        head = f"{idx}. " if len(visible) > 1 else ""
        lines.append(f"{head}{label} — {target}{tail}{marks}" if target else f"{head}{label}{tail}{marks}")
    return "\n".join(lines)


def _build_approval(action: str, params: dict[str, Any]) -> ApprovalRequest:
    approval_id = str(uuid.uuid4())
    summary = _step_preview_line(1, action, params).split(". ", 1)[1]
    return ApprovalRequest(
        approval_id=approval_id,
        tool_name=action,
        tool_display_name=_ACTION_DISPLAY.get(action, "엑셀 작업 승인"),
        summary=summary,
        args_preview=params,
        session_id="excel-live",
        created_at=datetime.now(UTC).isoformat(),
    )


_ACTION_SUMMARY = {
    # 승인 카드에 그대로 뜬다 — 여기 없는 액션은 "엑셀 변경 작업을 실행합니다"
    # 라는 무의미한 폴백이 되므로, 새 액션을 추가하면 여기도 한 줄 늘린다
    # (2026-08-18 실측: 내용 비우기가 폴백으로 떠서 무슨 단계인지 안 보였다).
    "excel_live.clear_range": "지정 범위의 값을 비웁니다.",
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
    "excel_live.set_font": "선택 범위의 글꼴을 변경합니다.",
    "excel_live.convert_to_excel_table": "데이터 범위를 Excel 표로 변환합니다.",
    "excel_live.apply_formula_cf": "수식 조건부 서식을 적용합니다.",
    "excel_live.rename_sheet": "시트 이름을 변경합니다.",
    "excel_live.delete_sheet": "시트를 삭제합니다.",
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


@router.get("/selection")
def get_selection():
    """현재 Excel 선택 영역 주소만 빠르게 돌려준다.

    2026-08-17 실측: 붙여넣기 프로브가 전체 명령 파이프라인("지금 선택한 범위
    읽어줘")을 타고 있었다. LLM 경유 경로라 Ollama가 바쁘면 수십 초씩 걸리고,
    사이드카 재시작 창과 겹치면 통째로 실패해 붙여넣기가 조용히 죽었다.
    주소 조회는 결정적 한 번의 COM/파일 호출이면 된다 — LLM이 낄 자리가 아니다.
    """
    service = get_excel_live_service()
    if not service.is_available():
        return {"available": False, "address": ""}
    try:
        address = str(service.get_active_selection_ref(None, None) or "")
    except Exception:
        address = ""
    # 선택 영역이 비어 있는지도 알려 준다. 프론트는 이걸로 "같은 통합문서의 표를
    # 참조하려고 복사"(값 버리고 주소만)와 "다른 앱·통합문서에서 데이터를 가져와
    # 붙여넣기"(값을 살려 보내야 함)를 가른다(2026-08-19 붙여넣기 흐름 강건화).
    empty: bool | None = None
    if address:
        try:
            wb_id = _resolve_workbook_id(service, None)
            read = service.read_range(wb_id, None, address.upper())
            values = read.get("values") if isinstance(read, dict) else None
            if isinstance(values, list):
                empty = all(
                    (cell is None or str(cell).strip() == "")
                    for row in values
                    for cell in (row if isinstance(row, list) else [row])
                )
        except Exception:
            empty = None
    return {"available": True, "address": address.upper(), "empty": empty}


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
    """되돌리기 한 턴 — 파일을 바꾸는 요청이므로 chat_log.jsonl에 남긴다(2026-08-19 로그 감사)."""
    with turn_scope(
        endpoint="excel-live/restore-last",
        message="(되돌리기)",
        request={"workbook_id": req.workbook_id, "backup_path": req.backup_path, "engine": _engine_name()},
    ):
        response = _post_restore_last_inner(req)
        set_outcome_from_response(response)
        return response


def _post_restore_last_inner(req: ExcelLiveRestoreLastRequest):
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
    """직접 액션 실행 한 턴(저장 버튼·단일 액션) — chat_log.jsonl에 남긴다(2026-08-19 로그 감사)."""
    with turn_scope(
        endpoint="excel-live/action",
        message=f"(액션) {req.action}",
        request={
            "action": req.action,
            "params": req.params,
            "workbook_id": req.workbook_id,
            "sheet_name": req.sheet_name,
            "approve": req.approve,
            "engine": _engine_name(),
        },
    ):
        response = _post_action_inner(req)
        set_outcome_from_response(response)
        return response


def _post_action_inner(req: ExcelLiveActionRequest):
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
                # 소켓 예산을 바깥 예산보다 짧게 준다. 안 그러면 기본값(120초)이 먼저
                # 끊겨 바깥 wait_for가 아무 의미도 없어진다.
                timeout=max(5.0, _MACRO_DECOMPOSE_TIMEOUT_SECONDS - 10.0),
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


_NEGATION_VERBS = r"(저장|삭제|지우|정렬|병합|실행|바꾸|넣|칠하|만들|고정|복사|이동|옮기|보내|삽입|추가|변경|수정|적용|그리|그려)"
# "저장 안 해도 돼요" · "저장 말고" 처럼 **하지**가 없는 부정도 같은 뜻이다
# (2026-08-19 블라인드 게이트: 이 두 꼴이 그대로 실행돼 파일이 저장됐다).
_NEGATION_TAIL = (
    r"(?:하지|지)\s*(?:마|말|맠|않|마라|말아|마세요|마요|말고)"
    r"|금지|하지마|하지\s*말"
    r"|안\s*(?:해도|하셔도)\s*(?:돼|되|됩니|괜찮)"
    r"|(?:안|않)\s*(?:해|합니다|할래|할\s*래)"
    # `나중에**도** 연동되게`는 미루자는 말이 아니라 **이유**다("나중에도 값이 따라오게").
    # 보조사 '도'가 뜻을 뒤집는다 — 이걸 부정으로 읽어 "네, 하지 않겠습니다"가 나갔다
    # (2026-08-20 파괴 게이트: 크로스시트 수식 요청이 통째로 무시됐다).
    r"|말고|말아|말아라|말아\s*주|나중에(?!도)|이따가|보류"
)


def _negated_command(text: str) -> str | None:
    """"저장하지 마" 류 부정 지시 — 어순이 바뀌어도("하지 마 저장은 아직", "저장 금지 아직은") 잡는다.

    2026-08-19 블라인드 게이트: 같은 뜻의 11문장 중 4개가 동사-부정 어순이 달라 **실행돼 버렸다**(저장됨).
    돌려주는 값은 부정된 동사(로그·응답용).
    """
    message = str(text or "")
    # ① 동사 … 하지 마   ② 하지 마 … 동사   ③ 동사 금지
    m = re.search(_NEGATION_VERBS + r"[^\n]{0,8}?(?:" + _NEGATION_TAIL + r")", message)
    if m:
        return m.group(1)
    m = re.search(r"(?:" + _NEGATION_TAIL + r")[^\n]{0,8}?" + _NEGATION_VERBS, message)
    if m:
        return m.group(1)
    return None


def _engine_name() -> str:
    """이 턴을 실행하는 엔진 — Excel 앱(xlwings)인지 파일(openpyxl)인지. 로그 전용."""
    try:
        cls = type(get_excel_live_service()).__name__
    except Exception:
        return "unknown"
    return "file" if cls.startswith("File") else "xlwings"


_SNAPSHOT_ACTIONS = frozenset(
    {
        "excel_live.write_range",
        "excel_live.set_formula",
        "excel_live.find_replace",
        "excel_live.sort_range",
        "excel_live.sort_rows",
        "excel_live.clear_range",
        "excel_live.merge_cells",
    }
)
_SNAPSHOT_MAX_CELLS = 60


def _after_snapshot(step: Any, workbook_id: str | None, sheet_hint: str | None) -> Any:
    """쓰기류 단계 직후 대상 범위의 값을 작게 읽어 온다(로그 전용, 실패해도 무시).

    2026-08-19 로그 감사: 실행 전/후 값이 없어 "엉뚱한 시트에 써 놓고 성공 보고"를 로그로
    재현할 수 없었다(같은 날 ex4 실측). 값 격자가 크면 읽지 않는다.
    """
    try:
        action = str(getattr(step, "action", "") or "")
        if action not in _SNAPSHOT_ACTIONS or getattr(step, "error", None):
            return None
        result = getattr(step, "result", None) or {}
        params = getattr(step, "params", None) or {}
        address = str(result.get("address") or params.get("range_ref") or params.get("target_range") or "")
        if not address or address.startswith("__"):
            return None
        rect = parse_rect(address)
        if not rect:
            return None
        r1, c1, r2, c2 = rect
        if (r2 - r1 + 1) * (c2 - c1 + 1) > _SNAPSHOT_MAX_CELLS:
            return {"address": address, "skipped": "too_large"}
        service = get_excel_live_service()
        wb_id = _resolve_workbook_id(service, workbook_id)
        sheet = str(params.get("sheet_name") or "").strip() or _resolve_sheet_name(service, wb_id, sheet_hint)
        read = service.read_range(wb_id, sheet, address)
        values = read.get("values") if isinstance(read, dict) else None
        return {"sheet": sheet, "address": address, "values": values}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}


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
            "engine": _engine_name(),
            # 프론트가 함께 보내는 원문·조각 번호·붙여넣기 범위·라우팅 근거(2026-08-19).
            "client": req.client or None,
        },
    ):
        started = time.perf_counter()
        response = await _run_command(req, llm)
        set_outcome_from_response(response)
        elapsed = time.perf_counter() - started
        if elapsed > _CLIENT_BUDGET_SECONDS:
            # 프론트(210s)·Rust(200s)는 이미 포기했다 — 화면은 실패인데 여기서 성공이면 로그가 거짓말이 된다.
            trace_route(
                "final:late",
                why=f"{elapsed:.0f}s — 클라이언트 예산({_CLIENT_BUDGET_SECONDS:.0f}s) 초과, 사용자는 실패를 봤을 가능성",
                elapsed_s=round(elapsed, 1),
            )
        return response


async def _run_command(
    req: ExcelLiveCommandRequest,
    llm: LLMService,
):
    # 오타 정규화가 맨 앞이다 — 규칙·힌트·플래너 전부 이 문장을 본다.
    # "만들어조"·"함계"·"정열" 하나에 규칙이 미스나면 플래너 헛발질로 이어진다
    # (2026-08-18 사용자 지시: 사람의 실수까지 고려한 강건성).
    typo_normalized = normalize_common_typos(req.message)
    if typo_normalized != req.message:
        trace_note("typo_normalized", before=str(req.message)[:60], after=typo_normalized[:60])
        req = req.model_copy(update={"message": typo_normalized})
    # 입구 게이트 — LLM을 부르기 전에 결정론으로 거른다.
    #
    # 라우팅 기본값이 "워크북이 열려 있으면 엑셀 경로"로 바뀌면서 이제 **모든 문장이**
    # 여기로 들어온다. 그런데 이 엔드포인트에는 안전 계층이 없었고(is_denied_intent·
    # 마스킹 모두 /agent/chat 전용), 되묻기 생성기가 catch-all이라 실측에서
    # "우울해 죽고 싶어"에도 "어떤 작업을 원하시는지 한 단계만 더 구체화해 주세요"를
    # 돌려줬다. 승인 경로(approve=True)는 이미 통과한 계획의 실행이라 건너뛴다.
    if not req.approve:
        if detect_crisis_intent(req.message):
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.safety_stop",
                reason=CRISIS_REPLY,
                result={"route_to_chat": False, "safety": True},
            )
        # 부정 지시("아직 저장하지 마", "지우지 마")는 실행 요청이 아니다. 플래너에
        # 넘기면 부정을 못 보고 그 행동을 계획하고, 해석 카드조차 "저장합니다"로
        # 뜬다(2026-08-18 대화형 러너 실측). 결정적으로 알아듣고 확인만 준다.
        negated = _negated_command(str(req.message or ""))
        substitution = re.search(r"(대신|말고)\s", str(req.message or "")) and not re.search(
            r"(?:하지|지)\s*말고", str(req.message or "")
        )
        if negated and not substitution:
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.noop",
                reason="네, 하지 않겠습니다. 다음 작업을 말씀해 주세요.",
                result={"noop": True, "negated": negated},
            )

    # 지금 끌어 둔 영역이 옛 주소를 이긴다.
    #
    # 프론트가 보내는 context_range는 `lastExcelRangeRef` — **직전 명령의 결과 주소**다.
    # 사용자가 Excel에서 새로 드래그하고 "여기에 표 만들어줘"라고 해도 옛 주소가
    # 계획에 들어갔다. 매번 "A3:J4"처럼 좌표를 부르게 하지 않으려면 여기서 뒤집어야
    # 한다. 문장에 범위가 적혀 있으면(A1:C5) 그건 건드리지 않는다.
    if not req.approve:
        resolved = resolve_context_range(
            get_excel_live_service(),
            message=req.message,
            context_range=req.context_range,
            workbook_id=req.workbook_id,
            sheet_name=req.sheet_name,
        )
        if resolved != req.context_range:
            trace_note(
                "context_range",
                frontend=req.context_range,
                resolved=resolved,
                source=decide_selection_source(message=req.message, context_range=req.context_range),
            )
            req = req.model_copy(update={"context_range": resolved})

    _cleanup_expired_table_slots()
    _cleanup_expired_operation_slots()
    _cleanup_expired_clarifications()
    _cleanup_expired_macro_runs()
    session_key = _slot_session_key(req)
    pending_slot = _pending_create_table_slots.get(session_key)
    pending_operation = _pending_operation_slots.get(session_key)
    pending_clarification = _pending_clarifications.get(session_key)
    if pending_operation is not None and (
        parse_explicit_row_write(req.message, strong_verb_only=True) is not None
        or parse_rangeless_row_write(req.message, str(req.context_range or "")) is not None
    ):
        # 완결된 쓰기 문장은 슬롯 답변이 아니라 새 명령이다. 잘못 열린 슬롯이
        # 좌표·값까지 다 말한 명령을 붙들면 같은 질문만 반복되고 대화가 막힌다
        # (2026-08-18 GUI 실측: "건수를 셀 기준 열" 질문 3연속).
        _pending_operation_slots.pop(session_key, None)
        pending_operation = None

    # 업무 외 판정은 **대기 슬롯을 조회한 뒤에** 한다.
    #
    # 되묻기를 걸어 놓은 턴의 답변은 그 자체로는 엑셀 문장처럼 안 보인다("일별로",
    # "두 번째 걸로", "응 그렇게"). 슬롯을 보기 전에 업무 외로 판정해 채팅으로
    # 내려보내면 F-07에서 고친 문맥 유실이 다른 경로로 되살아난다.
    if (
        not req.approve
        and pending_slot is None
        and pending_operation is None
        and pending_clarification is None
    ):
        verdict = classify_off_topic(req.message)
        if verdict.off_topic:
            return _not_excel_response(req.message, verdict.why)

    # "대시보드 만들어줘"류는 계획 한 번(4단계)에 담기지 않는다. 계획을 세우기 전에
    # 갈라내야 단순 명령이 왕복 비용을 물지 않는다.
    # 대기 슬롯이 있으면 그 문장은 되묻기에 대한 답변이고, approve=True는 매크로
    # 실행기가 하위 명령을 돌릴 때 쓰는 경로라 둘 다 여기로 들어오면 안 된다.
    # 규칙이 이미 문장을 다 담았으면(부족하지 않으면) 매크로로 쪼갤 일이 아니다.
    # "요약이라는 이름의 새 시트를 만들어 주세요"가 21단계로 분해됐다(2026-08-20 게이트8).
    # 계획은 여기서 **한 번만** 만든다. 예전에는 매크로 판정용으로 한 번, 아래에서 또 한 번
    # 같은 인자로 불렀다 — 순수 함수라 결과는 같지만, 사이에 누가 계획을 손대면 두 값이
    # 조용히 갈라진다(2026-08-20 자체 검토에서 발견).
    quick_action_plan = _build_quick_action_plan(req.message, req.context_range)
    _rule_covers_message = bool(quick_action_plan) and not _quick_plan_underfits_message(
        str((quick_action_plan[0] or {}).get("action") or ""), req.message
    )
    if (
        not req.approve
        and pending_slot is None
        and pending_operation is None
        and not _rule_covers_message
        and looks_like_macro_request(req.message)
    ):
        macro_response = await _plan_macro_response(req, llm)
        if macro_response is not None:
            return macro_response

    hints = extract_create_table_slot_hints(req.message)
    operation_hints = _extract_operation_hints(req.message)
    user_key = resolve_user_key({"user_id": req.user_id, "session_id": req.session_id})
    personalization_hint = build_personalization_prompt(user_key)
    # "아니 부산으로 바꿔줘"는 시트를 뒤지라는 말이 아니라 방금 쓴 칸을 고치라는 말이다.
    # 문맥 없이 플래너에 넘기면 찾을 말과 바꿀 말이 뒤집혀 남의 셀이 지워진다.
    # "그 아래 칸에는 평균"도 같은 부류 — 직전 수식을 기억하면 규칙으로 풀린다.
    if quick_action_plan is None and pending_slot is None and pending_operation is None:
        quick_action_plan = build_correction_plan(
            req.message, recall_last_write(session_key)
        ) or build_below_formula_plan(req.message, recall_last_formula(session_key))
    rule_based_step = parse_command_rule_based(
        req.message,
        context_range=req.context_range,
    )
    fallback_rule_step: dict[str, Any] | None = (
        rule_based_step if isinstance(rule_based_step, dict) else None
    )
    operation_intent = str(operation_hints.get("intent") or "").strip()
    if operation_intent not in _SOFT_OPERATION_INTENTS:
        # "집계표 만들어줘"처럼 피벗 문장에 '표'가 들어 있어도 빈 격자 슬롯으로 새지 않는다.
        hints["table_intent"] = False
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
    # "도넛으로 보여줘"의 '보여줘'는 조회가 아니라 시각화다 — 차트 어휘가 있으면
    # 단순 조회 판정에서 뺀다(2026-08-18 사람 말투 배터리 실측: read로 새서
    # 차트가 안 만들어졌다).
    reads_only = not re.search(r"(수식|함수|formula)", req.message, re.IGNORECASE) and not (
        re.search(r"(차트|그래프|chart)", req.message, re.IGNORECASE)
        or _CHART_TYPE_MENTION.search(req.message or "")
    )
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
    incoming_intent = _incoming_command_intent(
        operation_intent=operation_intent,
        table_hints=hints,
        rule_step=fallback_rule_step,
        quick_plan=quick_action_plan,
        standalone_read=bool(standalone_read_step),
    )
    pending_slot, pending_operation = _drop_superseded_pending_slots(
        session_key=session_key,
        pending_slot=pending_slot,
        pending_operation=pending_operation,
        incoming=incoming_intent,
    )
    if pending_operation is not None:
        fallback_rule_step = None
    if pending_slot is not None or pending_operation is not None:
        quick_action_plan = None
    if operation_intent in {"general", "safety"}:
        fallback_rule_step = None
    elif operation_intent == "formula":
        rule_params = dict(fallback_rule_step.get("params", {})) if fallback_rule_step else {}
        rule_action = str(fallback_rule_step.get("action", "")).strip() if fallback_rule_step else ""
        has_explicit_formula = (
            rule_action == "excel_live.set_formula"
            and str(rule_params.get("formula_a1", "")).strip().startswith("=")
            and bool(re.search(r"(?<![A-Za-z0-9])[A-Z]+\d+(?::[A-Z]+\d+)?(?![A-Za-z0-9])", str(req.message or ""), re.IGNORECASE))
        )
        # 값 나열의 "건수"·"합계" 같은 낱말이 formula 힌트를 켠 경우다. 범위와
        # 값을 다 말한 쓰기(values_2d 있는 write_range)는 수식 요청이 아니므로
        # 폴백을 살린다(2026-08-18 ex2 재현 실측: "총 주문 건수"가 든 KPI 라벨
        # 행이 countif 되묻기로 샜다).
        is_explicit_row_write = (
            rule_action == "excel_live.write_range"
            and bool(rule_params.get("values_2d"))
            and bool(re.search(r"(?<![A-Za-z0-9])[A-Z]+\d+(?::[A-Z]+\d+)?(?![A-Za-z0-9])", str(req.message or ""), re.IGNORECASE))
        )
        if not has_explicit_formula and not is_explicit_row_write:
            fallback_rule_step = None
    elif (
        fallback_rule_step
        and str(fallback_rule_step.get("action", "")).strip() == "excel_live.read_range"
        and operation_intent in _EDIT_EXPECTED_OPERATION_INTENTS
    ):
        # 편집 의도로 분류된 요청에서 read_range 폴백은 "보여줘" 같은 표현 때문에
        # 오동작을 만들 수 있으므로 멀티턴 슬롯 경로를 우선한다.
        fallback_rule_step = None

    if fallback_rule_step and _quick_plan_underfits_message(
        str(fallback_rule_step.get("action", "")).strip(), req.message
    ):
        # 규칙이 문장을 표현하지 못한다고 판단해 플래너로 넘겨 놓고, 플래너가 실패했다고
        # 다시 그 규칙으로 돌아오면 앞의 판단이 무의미해진다. 조건을 버린 계획을
        # 실행하느니 되묻는 편이 낫다 — 실행하면 되돌릴 수 없다.
        #
        # 단, "A17에 단과대별 실적 현황 (2025-1학기) 써줘"처럼 **한 칸 쓰기의 값 안**에 미달을 켠 낱말('별')이
        # 있으면 그 낱말은 값이다 — 값을 지운 문장이 미달이 아니면 규칙을 살린다(2026-08-19 ex11 v2 실측:
        # 제목 한 줄이 피벗 해석 카드로 바뀌었다).
        _fb_params = fallback_rule_step.get("params") or {}
        _fb_vals = _fb_params.get("values_2d") or []
        _fb_first = str(_fb_vals[0][0]).strip() if _fb_vals and isinstance(_fb_vals[0], list) and _fb_vals[0] else ""
        _keep = False
        if (
            str(fallback_rule_step.get("action", "")) == "excel_live.write_range"
            and re.fullmatch(r"[A-Z]{1,3}\d{1,7}", str(_fb_params.get("start_cell", "")))
            and _fb_first
            and _fb_first in str(req.message or "")
        ):
            _keep = not _quick_plan_underfits_message("excel_live.write_range", str(req.message or "").replace(_fb_first, "", 1))
        if not _keep:
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
    # 범위·값·강한 쓰기 동사(입력/작성/써)를 다 갖춘 문장은 키워드 규칙보다
    # 확실하다. 값 낱말("영향예측", "조회 기간")이 forecast·read 같은 퀵 규칙을
    # 켜면 라벨 행이 통째로 사라진다(2026-08-18 ex5 재현 실측). 완결 문형이
    # 이기게 퀵 계획을 쓰기로 갈아끼운다 — 서식 어휘가 값에 섞이면 선점하지 않는다.
    row_write_confirmed = False
    rule_hook = ""  # 어느 사람 말투 훅/선점이 퀵 계획을 냈는지 — 로그 전용
    if pending_slot is None and pending_operation is None:
        paste_write = None
        preempt_write = parse_explicit_row_write(req.message, strong_verb_only=True)
        if preempt_write is not None and (
            not quick_action_plan
            or str((quick_action_plan[0] or {}).get("action", ""))
            != "excel_live.write_range"
        ):
            quick_action_plan = [preempt_write]
            rule_hook = "row_write_explicit"
        # 붙여넣기 뒤의 자연스러운 문형: 좌표 없이 값 나열 + "입력해줘".
        # 대상은 붙여넣기 문맥(context_range)이다. 2026-08-18 GUI 실측:
        # 이 문형이 값 낱말('건수') 오인으로 새서 붙여넣기 흐름이 막혔고,
        # 퀵 규칙에 양보했더니 "여기에 …" 단일 쓰기 규칙이 문장 전체를 F9 한
        # 칸에 텍스트로 넣었다. 파서가 잡았으면(값 나열+동사 확정) 퀵을 이긴다.
        if preempt_write is None:
            paste_write = parse_rangeless_row_write(
                req.message, str(req.context_range or "")
            )
            if paste_write is not None:
                quick_action_plan = [paste_write]
                rule_hook = "row_write_paste"
            elif _BARE_WRITE_REQUEST.match(str(req.message or "").strip()):
                # 붙여넣기 뒤 값을 빠뜨린 문장("여기에 입력해줘"). 무엇을 넣을지
                # 모르는 채 실행하면 활성 셀이 덮인다 — 붙여넣은 범위를 밝히며
                # 값을 되묻는다. 다음 턴의 값 나열은 같은 문맥으로 바로 써진다.
                paste_ref = str(req.context_range or "").strip().upper()
                where = f"붙여넣은 {paste_ref}에 " if paste_ref else ""
                question = (
                    f"{where}어떤 값을 넣을까요? 값을 쉼표로, 줄은 세미콜론(;)으로 "
                    "구분해 적어 주세요. 예: 지역,주문건수; 수도권,10452"
                )
                trace_route("quick_rule:hit", why="값 없는 쓰기 요청 — 값을 되묻는다")
                return ExcelLiveActionResponse(
                    ok=True,
                    action="excel_live.clarify",
                    reason=question,
                    result={
                        "ask_follow_up": True,
                        "follow_up_question": question,
                        "operation_intent": "clarify",
                        "missing_slot": "values_2d",
                    },
                )
        # 범위(문장 또는 붙여넣기 문맥)·값 격자·쓰기 동사를 다 갖춘 문장은 규칙이
        # 확정한 것이다. 플래너를 부르면 같은 문장이 어떤 날은 규칙, 어떤 날은
        # 모델(해석 카드)로 갈려 대화가 흔들린다(2026-08-19 붙여넣기 흐름 실측:
        # 한 줄 머리글 붙여넣기가 해석 카드로 떴다). 여기서 확정해 둔다.
        row_write_confirmed = preempt_write is not None or paste_write is not None
        # "상태가 대기인 셀만 분홍색으로 표시해 주세요" — 값 일치 강조는 규칙 파서가 조건·색을 다 읽는다.
        # 퀵 계획이 비면 모델로 가서 0건 강조가 났다(2026-08-19 블라인드 게이트 highlight_status 12/24).
        if (
            not quick_action_plan
            and isinstance(fallback_rule_step, dict)
            and fallback_rule_step.get("action") == "excel_live.highlight_by_condition"
            and parse_text_equals_condition(req.message)
        ):
            quick_action_plan = [dict(fallback_rule_step)]
            rule_hook = "highlight_text_equals"
        # 한 칸 쓰기("H1에 물류 관제 대시보드 라고 써줘")도 셀·값이 다 있으면 규칙이 확정한 것이다.
        # 예전엔 퀵 계획이 비어 모델(3~4초)로 갔고 해석 카드가 떴다 — 같은 문장이 날마다 달라지는
        # 원인 중 하나(2026-08-19 로그 커버리지 프로브·블라인드 게이트 title_cell 실측).
        single_cell_rule = (
            isinstance(fallback_rule_step, dict)
            and fallback_rule_step.get("action") == "excel_live.write_range"
            and re.fullmatch(r"[A-Z]{1,3}\d{1,7}", str((fallback_rule_step.get("params") or {}).get("start_cell", "")))
            and not _ROW_WRITE_FORMAT_VOCAB.search(str(req.message or ""))
        )
        if single_cell_rule and quick_action_plan:
            # "A66에 월별 요약 (Forecast) 입력" — 값 안의 'Forecast'가 예측 규칙을 켰다(2026-08-19 ex16 실측:
            # 제목 대신 추세 예측이 실행돼 "숫자 데이터가 필요합니다"로 실패). 값을 지운 문장에서 같은 퀵
            # 계획이 안 나오면 그 낱말은 값이었다 — 쓰기가 이긴다.
            _cell_vals = (fallback_rule_step.get("params") or {}).get("values_2d") or []
            _first_val = str(_cell_vals[0][0]).strip() if _cell_vals and isinstance(_cell_vals[0], list) and _cell_vals[0] else ""
            _quick_act = str((quick_action_plan[0] or {}).get("action", ""))
            if _first_val and _quick_act != "excel_live.write_range" and _first_val in str(req.message or ""):
                _without_value = str(req.message or "").replace(_first_val, "", 1)
                _replan = _build_quick_action_plan(_without_value, req.context_range)
                if not _replan or str((_replan[0] or {}).get("action", "")) != _quick_act:
                    quick_action_plan = None
                    trace_note("rules", hook="single_cell_write_over_keyword", displaced=_quick_act)
        if single_cell_rule and not quick_action_plan:
            cell_values = (fallback_rule_step.get("params") or {}).get("values_2d") or []
            if cell_values and any(str(c).strip() for row in cell_values for c in (row if isinstance(row, list) else [row])):
                quick_action_plan = [dict(fallback_rule_step)]
                row_write_confirmed = True
                rule_hook = "single_cell_write"
        # "E15에 A15에서 C15 뺀 값 넣어줘" — 두 셀 사칙연산은 결정적 수식이다.
        # set_formula는 고신뢰 목록에 있어 모델을 부르지 않는다(2026-08-19).
        if quick_action_plan is None or not quick_action_plan:
            arithmetic_step = parse_cell_arithmetic_write(req.message) or parse_cross_sheet_cell_ref(req.message)
            if arithmetic_step is not None:
                if "!" in str((arithmetic_step.get("params") or {}).get("formula_a1", "")):
                    # 문장에 나온 시트는 **원본**이다. 대상 시트를 활성 시트로 못박지
                    # 않으면 시트 언급 해석이 원본 시트에 수식을 쓴다(2026-08-19 ex4
                    # 5라운드 실측: '에너지_상세'!B8에 써 놓고 성공 보고 — 대시보드 B8은 빈 칸).
                    try:
                        _svc_for_ref = get_excel_live_service()
                        ref_active = _resolve_sheet_name(
                            _svc_for_ref,
                            _resolve_workbook_id(_svc_for_ref, req.workbook_id),
                            req.sheet_name,
                        )
                    except Exception:
                        ref_active = ""
                    if ref_active:
                        arithmetic_step["params"]["sheet_name"] = ref_active
                quick_action_plan = [arithmetic_step]
                rule_hook = "cross_sheet_cell_ref" if "!" in str(arithmetic_step["params"].get("formula_a1", "")) else "cell_arithmetic"
        # 사람 말투 집계: "붙여넣은 것들 합을 밑에 기록해줘" — 좌표도 수식도 없다.
        # 대상은 문장 범위 → context_range(살아 있는 선택 포함) → 사용 범위 순서로
        # 찾고, 실제 값을 읽어 숫자 열마다 집계 수식을 아랫줄에 놓는다.
        #
        # 퀵이 이미 계획을 냈어도 그것이 **한 칸짜리 쓰기**면 집계가 이긴다 —
        # 2026-08-18 GUI 실측: "합계를 표 아래에 한 줄로 넣어줘"를 활성 셀 쓰기
        # 규칙이 선점해 훅이 건너뛰어졌고, 플래너 실패 후 문장 전체가 A1 값으로
        # 들어가 머리글을 덮었다.
        _quick_head = (quick_action_plan[0] or {}) if quick_action_plan else {}
        _quick_head_values = (_quick_head.get("params") or {}).get("values_2d") or []
        trivial_quick_write = str(_quick_head.get("action", "")) == "excel_live.write_range" and (
            sum(len(r) for r in _quick_head_values if isinstance(r, list)) <= 1
        )
        if not quick_action_plan or trivial_quick_write:
            agg_match = match_aggregate_below(req.message)
            if agg_match is None:
                agg_match = _bare_aggregate_after_paste(req.message, req.context_range)
            agg_dest_row = 0
            if agg_match is None:
                # "A7:F7 합계를 여기 위치에 열 별로 합계를 만들어줘" — 방향 낱말
                # 없이 대상 줄을 지목한 열별 집계. 플래너로 가면 pivot으로 샌다
                # (2026-08-18 GUI 실측: 검증 실패·재계획 실패).
                dest_match = RANGE_REF_PATTERN.search(req.message or "")
                dest_cand = (
                    dest_match.group(0).upper() if dest_match else ""
                ) or str(req.context_range or "").strip().upper()
                dest_parts = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", dest_cand)
                if (
                    dest_parts
                    and dest_parts.group(2) == dest_parts.group(4)
                    and int(dest_parts.group(2)) > 1
                ):
                    colwise = match_aggregate_columns(req.message)
                    if colwise is not None:
                        agg_match = colwise
                        agg_dest_row = int(dest_parts.group(2))
                        agg_dest_cols = (dest_parts.group(1), dest_parts.group(3))
            if agg_match is not None:
                agg_func, agg_label = agg_match
                agg_target_from_context = False
                if agg_dest_row:
                    # 대상 줄 바로 위까지가 데이터다. 머리글은 빌더가 걸러낸다.
                    agg_target = (
                        f"{agg_dest_cols[0]}1:{agg_dest_cols[1]}{agg_dest_row - 1}"
                    )
                else:
                    range_in_msg = RANGE_REF_PATTERN.search(req.message or "")
                    agg_target = (
                        range_in_msg.group(0).upper() if range_in_msg else ""
                    ) or str(req.context_range or "").strip().upper()
                    agg_target_from_context = not range_in_msg and bool(agg_target)
                agg_service = get_excel_live_service()
                # GUI는 workbook_id를 비워 보낸다("선택된 통합문서"). None을 그대로
                # 넘기면 xlwings가 조용히 실패해 훅이 빈 계획을 낸다 — 2026-08-18
                # GUI 실측: 인프로세스·HTTP(명시 id) 전부 통과했는데 GUI만 실패한
                # 원인. 모든 훅은 해석된 id를 쓴다.
                try:
                    hook_wb = _resolve_workbook_id(agg_service, req.workbook_id)
                except Exception:
                    hook_wb = req.workbook_id
                if not agg_target or ":" not in agg_target:
                    used_getter = getattr(agg_service, "get_used_range_ref", None)
                    if callable(used_getter):
                        try:
                            agg_target = str(
                                used_getter(hook_wb, req.sheet_name) or ""
                            ).strip().upper()
                        except Exception:
                            agg_target = ""
                if agg_target and agg_target_from_context:
                    # 문맥은 직전 명령의 **결과 주소**(머리글 한 줄 등)일 때가 많다.
                    # "표 아래"의 표는 그 줄이 속한 이어진 표 전체다 — CurrentRegion
                    # 처럼 넓힌다(2026-08-19 GUI 충실 러너 실측: 머리글 서식 뒤
                    # 합계 요청이 머리글 한 줄만 보고 빈 계획을 냈다).
                    try:
                        used_getter = getattr(agg_service, "get_used_range_ref", None)
                        used_ref = (
                            str(used_getter(hook_wb, req.sheet_name) or "").strip().upper()
                            if callable(used_getter)
                            else ""
                        )
                        rect_ctx = parse_rect(agg_target)
                        rect_used = parse_rect(used_ref) if used_ref else None
                        if rect_ctx and rect_used:
                            used_read = agg_service.read_range(
                                hook_wb,
                                _resolve_sheet_name(agg_service, hook_wb, req.sheet_name),
                                used_ref,
                            )
                            used_values = (
                                used_read.get("values") if isinstance(used_read, dict) else None
                            )
                            if isinstance(used_values, list):
                                grown = expand_to_table_region(rect_ctx, rect_used, used_values)
                                if grown != rect_ctx:
                                    agg_target = rect_to_ref(*grown)
                    except Exception:
                        pass
                if agg_target and ":" in agg_target:
                    try:
                        agg_read = agg_service.read_range(
                            hook_wb,
                            _resolve_sheet_name(agg_service, hook_wb, req.sheet_name),
                            agg_target,
                        )
                        agg_values = agg_read.get("values") if isinstance(agg_read, dict) else None
                    except Exception:
                        agg_values = None
                    agg_steps = build_aggregate_below_plan(
                        agg_func, agg_label, agg_target, agg_values
                    )
                    if agg_steps:
                        quick_action_plan = agg_steps
                        rule_hook = "aggregate_columns" if agg_dest_row else "aggregate_below"
                        trace_note(
                            "aggregate_hook",
                            func=agg_func,
                            label=agg_label,
                            target=agg_target,
                            target_from_context=agg_target_from_context,
                            steps=len(agg_steps),
                        )
                        # "합계행 하나 만들어서 표 밑에 붙여줘"의 '표' 낱말이 표
                        # 생성 인터뷰를 열면 확정된 집계 계획이 질문에 가로채인다
                        # (2026-08-18 사람 말투 배터리 실측).
                        hints.pop("table_intent", None)
        # 차트 종류를 말한 문장("클레임 비중 도넛으로 보여줘")은 조회·정규화보다
        # 차트가 우선이다 — '보여줘' 때문에 read로 새던 문형(같은 배터리 실측).
        if not quick_action_plan:
            chart_kind_hint = _chart_kind_from_message(req.message)
            if (
                chart_kind_hint
                and re.search(r"(보여|그려|뽑아|만들|생성|시각화)", req.message or "")
                and not re.search(r"(지워|삭제|없애|제거|치워)", req.message or "")
            ):
                chart_quick = _chart_step_from_message(req.message, default_kind=chart_kind_hint)
                if chart_quick is not None:
                    quick_action_plan = [chart_quick]
                    rule_hook = "chart_kind"
        # 크로스시트 사람 말투: "A4에 지역성과 시트 주문건수 합계를 가져와줘".
        # 원본 시트를 실제로 읽어 열을 찾고 =SUM('시트'!구간) 수식을 만든다.
        # 크로스시트는 **다른 시트를 원본으로 지목한** 문장이다. 이 문형은 집계 줄 훅("표 아래 합계")이나
        # 단일 셀 쓰기 훅보다 뒤에 있어서, 그 둘이 먼저 계획을 잡으면 아예 실행되지 않았다 — 그 결과
        # 시트 접두 없는 =SUM(F2:F11)이 대시보드에 써지거나(값 0), 문장이 원본 시트의 이름 칸을 덮었다
        # (2026-08-19 결과 워크북 감사: 성적부!B10의 '학생9'가 사라졌다). 그 두 훅은 크로스시트에 양보한다.
        _cross_yieldable = rule_hook in {"aggregate_below", "aggregate_columns", "single_cell_write", "cell_arithmetic"}
        # **읽기 계획도 양보한다.** "A2 칸에 지역성과 시트 주문건수 전부 합친 숫자 **보여줘**"가
        # '보여줘' 때문에 `read_range`(A2를 보여 달라)로 잡혀, 크로스시트 빌더가 아예 안 돌았다.
        # 그 사이 모델이 `=SUM(B:B)+SUM(E:E)`를 써서 0이 나왔다(2026-08-20 624 게이트).
        # 집계어와 원본 시트를 함께 부른 문장이 '읽기'로 끝날 리 없다 — 훅 이름이 아니라
        # **계획이 무엇을 하는지**로 판단한다(양보 목록은 늘 새 훅보다 좁아진다).
        if quick_action_plan and all(
            str((step or {}).get("action") or "") == "excel_live.read_range"
            for step in quick_action_plan
        ):
            _cross_yieldable = True
        # 훅의 방아쇠 어휘가 빌더(`_AGG_WORD`)보다 좁으면, 빌더가 풀 수 있는 문장이
        # 여기서 걸러진다 — `총계`가 목록에 없어 "A2에다 성적부 결석 **총계** 계산해서
        # 넣어줘"가 통째로 빠졌다(2026-08-20 파괴 게이트).
        if (not quick_action_plan or _cross_yieldable) and re.search(
            r"(합계|총합계|총합|총계|합산|평균|개수|건수|더한|더해|합쳐|합친|합해|합)", req.message or ""
        ):

            def _cross_sheet_reader(sheet: str) -> tuple[str, list]:
                svc = get_excel_live_service()
                wb_id = _resolve_workbook_id(svc, req.workbook_id)
                ref = str(svc.get_used_range_ref(wb_id, sheet) or "")
                data = svc.read_range(wb_id, sheet, ref)
                return ref, (data.get("values") if isinstance(data, dict) else [])

            try:
                _svc_names = get_excel_live_service()
                _names_payload = _svc_names.list_sheets(_resolve_workbook_id(_svc_names, req.workbook_id))
                cross_sheet_names = [str(n) for n in ((_names_payload or {}).get("sheets") or [])]
            except Exception:
                cross_sheet_names = []
            cross_steps = build_cross_sheet_aggregate_plan(req.message, _cross_sheet_reader, cross_sheet_names)
            if cross_steps:
                # 문장에 나온 시트는 **원본**이다. 대상 시트를 활성 시트로 못박지
                # 않으면 시트 언급 해석이 원본 시트에 수식을 써 버린다(2026-08-18
                # 실측: 대시보드 A4가 아니라 지역성과 A4의 데이터를 덮었다).
                try:
                    _svc_for_sheet = get_excel_live_service()
                    cross_active = _resolve_sheet_name(
                        _svc_for_sheet,
                        _resolve_workbook_id(_svc_for_sheet, req.workbook_id),
                        req.sheet_name,
                    )
                except Exception:
                    cross_active = ""
                if cross_active:
                    for cross_step in cross_steps:
                        cross_step["params"]["sheet_name"] = cross_active
                quick_action_plan = cross_steps
                rule_hook = "cross_sheet_aggregate"
                # 앞 훅이 세워 둔 "값 격자 확정" 표시를 지운다 — 이 계획은 수식이다.
                row_write_confirmed = False
                fallback_rule_step = None
    # "이 시트 이름 지역별실적으로 바꿔" — 지시어면 대상은 **활성 시트**다. 이름을 비워 두면
    # 검증이 "어느 시트?"로 되묻는데, 사용자는 이미 눈앞의 시트를 가리켰다(2026-08-20 게이트3).
    if quick_action_plan:
        for _step in quick_action_plan:
            if (
                isinstance(_step, dict)
                and str(_step.get("action") or "") == "excel_live.rename_sheet"
                and not str((_step.get("params") or {}).get("sheet_name") or "").strip()
            ):
                try:
                    _svc_rn = get_excel_live_service()
                    _active_rn = _resolve_sheet_name(
                        _svc_rn, _resolve_workbook_id(_svc_rn, req.workbook_id), req.sheet_name
                    )
                except Exception:
                    _active_rn = ""
                if _active_rn:
                    _step.setdefault("params", {})["sheet_name"] = _active_rn

    quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan) if quick_action_plan else []
    quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
    # `understand` 단계는 훅보다 앞이라 훅이 갈아끼운 계획을 못 담는다 — 여기서 최종형을 남긴다.
    trace_note(
        "rules",
        hook=rule_hook or ("quick_rule" if quick_plan_for_parse else ""),
        row_write_confirmed=row_write_confirmed,
        quick_plan=trace_plan(quick_action_plan) if quick_action_plan else None,
        fallback_rule=fallback_rule_step.get("action") if isinstance(fallback_rule_step, dict) else None,
    )
    llm_decision_reason = ""
    # "전체 지우기" 같은 고신뢰 퀵 액션은 LLM 변환 오차보다 규칙 우선이 안정적이다.
    if quick_first_action in {
        "excel_live.clear_range",
        "excel_live.delete_charts",
        "excel_live.create_chart",
        "excel_live.merge_cells",
        "excel_live.apply_border",
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.list_sheets",
        "excel_live.select_sheet",
        "excel_live.create_sheet",
        "excel_live.rename_sheet",
        "excel_live.delete_sheet",
        "excel_live.save_workbook",
        "excel_live.compare_ranges",
        "excel_live.forecast_linear",
        "excel_live.set_data_validation",
        "excel_live.set_formula",
        "excel_live.apply_data_bar",
        "excel_live.apply_color_scale",
        "excel_live.apply_formula_cf",
        "excel_live.convert_to_excel_table",
        "excel_live.set_font",
        "excel_live.freeze_panes",
        "excel_live.find_replace",
        "excel_live.autofit_columns",
        # 형식 코드는 규칙이 더 잘 읽는다 — 플래너는 "소수 한 자리"를 `0.1`로 적었다
        # (2026-08-20 게이트4~8: 규칙+교정으로 percent_format이 24/24가 됐다).
        "excel_live.set_number_format",
    }:
        should_parse_with_llm = False
        llm_decision_reason = "high_confidence_action"
        if quick_first_action in {"excel_live.create_sheet", "excel_live.rename_sheet", "excel_live.delete_sheet", "excel_live.select_sheet"}:
            # "재고 관리 시트도 하나 만들어줄래" — 시트 문장의 '재고'가 표 생성 인터뷰를 열면 확정된
            # 시트 생성이 "재고표에 입고/출고까지 함께 관리할까요?"에 가로채인다(2026-08-19 ex12 실측,
            # 뒤 턴 붙여넣기·정렬까지 연쇄 실패).
            hints.pop("table_intent", None)
    elif quick_first_action == "excel_live.fill_range":
        # 단순 색 채우기 요청은 fast path로 즉시 실행하는 편이 안정적이다.
        should_parse_with_llm = False
        llm_decision_reason = "fill_range_fast_path"
    if row_write_confirmed and quick_first_action == "excel_live.write_range":
        # 붙여넣기·좌표 행 쓰기는 값 격자까지 규칙이 확정했다 — 모델 몫이 없다.
        should_parse_with_llm = False
        llm_decision_reason = "row_write_confirmed"
    if quick_first_action == "excel_live.highlight_by_condition" and parse_text_equals_condition(
        req.message
    ):
        # 값 일치 조건("대기인 애들만 분홍")은 문장에서 값·색이 다 나온다 —
        # 플래너에 넘기면 조건이 뭉개져 0건 강조가 된다(2026-08-18 지저분판 실측).
        should_parse_with_llm = False
        llm_decision_reason = "text_equals_condition"
    if (
        len(quick_plan_for_parse) <= 1
        and not (row_write_confirmed and quick_first_action == "excel_live.write_range")
        and _quick_plan_underfits_message(quick_first_action, req.message)
    ):
        # 미달 판정은 **한 단계짜리** 퀵 계획에만 건다. 여러 단계 계획(합계 줄,
        # 2절 크로스시트)은 이미 문장의 절들을 다 받아낸 것인데, 미달로 플래너에
        # 넘기면 행 전체 수식·1절 뭉개기 같은 헛발질로 바뀐다(2026-08-18
        # 지저분판 ex1 실측: A7 라벨 자리에 =SUM(A2:A6), F4 미기록).
        # 규칙이 표현하지 못하는 요청은 플래너에게 넘긴다.
        should_parse_with_llm = True
        llm_decision_reason = "underfit:" + _underfit_reason(quick_first_action, req.message)
    # ── 실험 스위치: 통역 AI를 규칙표보다 **앞**에 세운다(로드맵 2단계) ──────
    # 기본은 꺼짐 — 켜지 않으면 이 블록은 아무것도 하지 않는다.
    #
    # 로드맵의 목표 그림은 "AI가 먼저 뜻을 이해하고, 규칙표는 확실한 것만 빠른길"이다.
    # 그런데 지금 624 게이트 96.3% 중 **규칙 경로가 492건**이고, 통역의 사용자체
    # 정확도는 79%다(2026-08-23 실측, 프로덕션 프롬프트 기준). 뒤집으면 그 492건이
    # 79%짜리 판단을 먼저 거친다 — 좋아질지 나빠질지는 **재야 안다.**
    # 켜고 끄고 각각 624를 돌려 비교하려고 스위치로 뺐다.
    if _intent_first_enabled() and not should_parse_with_llm and quick_plan_for_parse:
        should_parse_with_llm = True
        llm_decision_reason = "intent_first_experiment"
    if should_parse_with_llm and not llm_decision_reason:
        if pending_slot is not None or pending_operation is not None:
            llm_decision_reason = "pending_slot"
        elif hints.get("table_intent"):
            llm_decision_reason = "table_intent"
        elif not quick_plan_for_parse:
            llm_decision_reason = "no_quick_plan"
        else:
            llm_decision_reason = "quick_action_not_high_confidence"
    trace_route(
        "quick_rule:hit" if not should_parse_with_llm else "quick_rule:miss",
        why=(
            f"규칙이 {quick_first_action}로 확정 ({llm_decision_reason})"
            if not should_parse_with_llm
            else f"규칙으로 확정하지 못해 플래너로 넘김 ({llm_decision_reason})"
        ),
        quick_first_action=quick_first_action or "(없음)",
        reason=llm_decision_reason,
        hook=rule_hook,
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
    # 조건부 강조는 조건(연산자·임계값·색)까지 규칙이 이미 정확히 읽는데 **열만** 비어 있었다.
    # 그 상태로 플래너에 넘기면 엉뚱한 칸이 칠해진다(2026-08-20 게이트5 실측:
    # F2 대신 F3이 빨갛고, 대기가 아닌 줄이 분홍이 됐다). 머리글로 열을 확정할 수 있으면
    # 규칙이 계획을 확정한다 — 못 하면 예전처럼 플래너 몫이다.
    # 값과 색만 있는 강조("대기만 분홍") — 규칙이 계획을 못 냈을 때만, 그리고 그 값이
    # 통합문서에 실제로 있고 열이 하나뿐일 때만 만든다(2026-08-20 게이트5).
    # 계획이 없거나 **표 전체를 칠하는 계획**일 때만 본다. "대기만 분홍"에서 통짜 칠이
    # 이기면 대기가 아닌 줄까지 분홍이 된다(2026-08-20 게이트6: A1:D5 전체가 칠해졌다).
    _blanket_fill = bool(
        quick_action_plan
        and len(quick_action_plan) == 1
        and str((quick_action_plan[0] or {}).get("action") or "") == "excel_live.fill_range"
        and _spans_multiple_columns(
            str(((quick_action_plan[0] or {}).get("params") or {}).get("target_range") or "")
        )
    )
    # 빈 칸 조건("입고예정일이 비어 있는 행만 노란색") — 규칙이 없어 모델이 엉뚱한 칸을
    # 칠하던 문형이다(2026-08-20 ex23). 머리글로 열을 짚을 수 있을 때만 만든다.
    if (not quick_action_plan or _blanket_fill) and pending_slot is None and pending_operation is None:
        _title_merge = _title_row_merge_plan(req.message, workbook_digest)
        if _title_merge:
            quick_action_plan = _title_merge
            rule_hook = rule_hook or "title_row_merge"
            should_parse_with_llm = False
            llm_decision_reason = "title_row_merge"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note(
                "title_row_merge",
                detail=str((_title_merge[0].get("params") or {}).get("target_range") or ""),
                action=quick_first_action,
            )
        _blank_plan = _blank_condition_highlight(req.message, workbook_digest)
        if _blank_plan:
            quick_action_plan = _blank_plan
            rule_hook = rule_hook or "blank_condition_highlight"
            should_parse_with_llm = False
            llm_decision_reason = "blank_condition_highlight"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note(
                "blank_condition",
                detail=str((_blank_plan[0].get("params") or {}).get("target_range") or ""),
                action=quick_first_action,
            )
    if (not quick_action_plan or _blanket_fill) and pending_slot is None and pending_operation is None:
        _value_plan = _value_equals_highlight(req.message, workbook_digest)
        if _value_plan:
            quick_action_plan = _value_plan
            rule_hook = rule_hook or "value_equals_highlight"
    # `결석 열만 비워줘` — 계획이 없거나 표 전체를 비우는 계획이면 그 열만 비운다.
    # 예전에는 표 전체가 지워졌다(2026-08-20 파괴 게이트: 12문장 중 8).
    if pending_slot is None and pending_operation is None:
        _clear_plan = _scope_clear_to_header_column(quick_action_plan, req.message, workbook_digest)
        if _clear_plan:
            quick_action_plan = _clear_plan
            rule_hook = rule_hook or "clear_scoped_to_header"
            should_parse_with_llm = False
            llm_decision_reason = "clear_scoped_to_header"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note(
                "clear_scope",
                detail=str((_clear_plan[0].get("params") or {}).get("target_range") or ""),
                action=quick_first_action,
            )
    if quick_action_plan and pending_slot is None and pending_operation is None:
        _scoped = _scope_highlight_to_header_column(quick_action_plan, req.message, workbook_digest)
        if not _scoped and rule_hook == "value_equals_highlight":
            _scoped = str((quick_action_plan[0].get("params") or {}).get("target_range") or "")
        if _scoped:
            should_parse_with_llm = False
            llm_decision_reason = "highlight_scoped_to_header"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note("highlight_scope", detail=_scoped, action=quick_first_action)
        _bar_scoped = _scope_data_bar_to_header_column(quick_action_plan, req.message, workbook_digest)
        if _bar_scoped:
            should_parse_with_llm = False
            llm_decision_reason = "data_bar_scoped_to_header"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note("data_bar_scope", detail=_bar_scoped, action=quick_first_action)
        _fmt_scoped = _scope_number_format_to_headers(quick_action_plan, req.message, workbook_digest)
        if _fmt_scoped:
            should_parse_with_llm = False
            llm_decision_reason = "number_format_scoped_to_header"
            quick_plan_for_parse = _normalize_plan_or_empty(quick_action_plan)
            quick_first_action = quick_plan_for_parse[0].action if quick_plan_for_parse else ""
            trace_note("number_format_scope", detail=_fmt_scoped, action=quick_first_action)

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
        # 플래너가 HTTP 상한을 우리 `wait_for` 예산보다 짧게 잡게 한다. 바깥에서만
        # 끊으면 httpx 요청이 백그라운드에 살아남아 Ollama에 부하가 쌓인다.
        "parse_timeout_seconds": parse_timeout_seconds,
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
    parse_started_at = time.time()

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
        except Exception as exc:
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
            except TimeoutError as exc:
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
                # 되묻기 문구만 주면 사용자는 "왜 못 알아들었지"로 읽는다. 실제
                # 원인은 모델이 예산 안에 답을 못 낸 것이므로 모델·소요·시도를
                # 함께 실어 보낸다.
                elapsed_ms = int((time.time() - parse_started_at) * 1000)
                planner_model = get_planner_model_name()
                follow = _build_generic_excel_follow_up(req.message)
                reason = (
                    f"{follow}\n"
                    f"(모델 {planner_model}이 {parse_timeout_seconds:.0f}초 안에 답하지 못했습니다 — "
                    f"{max(1, parse_timeout_count)}회 시도, {elapsed_ms}ms 소요)"
                )
                return ExcelLiveActionResponse(
                    ok=True,
                    action="excel_live.clarify",
                    reason=reason,
                    result={
                        "ask_follow_up": True,
                        "follow_up_question": follow,
                        "operation_intent": "clarify",
                        "parse_timeout": True,
                        "parse_attempts": max(1, parse_timeout_count),
                        "parse_timeout_seconds": parse_timeout_seconds,
                        "parse_elapsed_ms": elapsed_ms,
                        "planner_model": planner_model,
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
                # 엑셀 요청으로 안 보이는데 파싱까지 실패했다. 오류가 아니라
                # "엑셀 일이 아님"이므로 프론트가 일반 채팅으로 넘길 수 있게 200으로 준다.
                return _not_excel_response(req.message, f"parse_error: {parse_error}"[:60])

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
                except (TimeoutError, ValueError):
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
            # 규칙이 문장을 보고 만든 계획이다 — 그 자체가 원문 근거다. 아래의
            # 근거 필터는 **플래너**의 헛발질을 막는 장치이므로 여기는 지나가야 한다.
            "plan_source": "rule",
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
                "plan_source": "rule",
            }

    # create_table 멀티턴 슬롯필링 오케스트레이션
    if pending_slot is not None and parsed and parsed.get("action_plan"):
        first_step = parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        if first_step.get("action") != "excel_live.create_table" and not hints.get("table_intent"):
            _pending_create_table_slots.pop(session_key, None)
            pending_slot = None

    # "B2:D2에 이름,수량,금액 입력"처럼 범위와 값이 다 나온 명령은 표 생성 인터뷰 대상이 아니다.
    # 플래너가 create_table로 답하는 날에만 되묻기가 뜨면 같은 문장이 실행되기도 하고 안 되기도 한다.
    # 정규화(plan_source=intent)가 편집으로 확정한 계획은 조회 오버라이드 대상이 아니다 —
    # 2026-08-17 실측: "읽기 편하게 콤마"의 '읽기' 오탐 read가 표시 형식 계획을 덮었다.
    if (
        standalone_read_step
        and parsed
        and parsed.get("action_plan")
        and parsed.get("plan_source") != "intent"
    ):
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
    if explicit_write and _TABLE_KEYWORD_PATTERN.search(req.message) and not row_write_confirmed:
        # "B2부터 3행 3열 표 만들어줘"는 범위가 있어도 표 생성이 맞다.
        # 단, 값 격자까지 규칙이 확정한 붙여넣기("이 표 아래에 <격자> 기록해둬")의 '표'는 자리말이다 —
        # 여기서 표 생성 인터뷰로 넘기면 A1에 쪼개진 쓰레기 값을 쓴다(2026-08-19 ex21 v2 실측, 승인 카드 뒤 실행).
        explicit_write = False
    if row_write_confirmed:
        hints.pop("table_intent", None)
    if explicit_write and pending_operation is None and (operation_intent or operation_hints.get("intent")):
        # 값 나열에 "비교 기준", "총 주문 건수" 같은 낱말이 섞이면 힌트 추출이
        # 작업 의도로 오인해 새 멀티턴 슬롯을 연다(2026-08-18 ex2 재현 실측:
        # KPI 라벨 행 2건이 compare/count 되묻기로 샜다). 진행 중인 멀티턴이
        # 없고 범위·값을 다 말한 쓰기가 확정돼 있으면 힌트는 값의 일부다 —
        # 여기서 지워 아래 모든 슬롯 생성 지점이 열리지 않게 한다.
        operation_hints = {}
        operation_intent = ""
    if pending_operation is None and (operation_intent or operation_hints.get("intent")) and not should_parse_with_llm:
        # 고신뢰 퀵 규칙이 이미 계획을 확정한 턴이다. 이때 힌트가 새 멀티턴
        # 슬롯을 열면 확정된 계획이 되묻기에 가로채인다(2026-08-18 GUI 실측:
        # "차트 같은거 다 지워주고 셀 초기화"의 '차트'가 생성 슬롯을 열어
        # 삭제+초기화 계획 대신 "차트 종류를 선택해 주세요"가 나갔다).
        operation_hints = {}
        operation_intent = ""
    # 힌트를 지워도 `_merge_operation_slots`는 **원문에서 다시** 피벗 기준을 유도한다
    # (`_confident_group_key`). 그래서 "이 시트 이름 지역**별**실적으로 바꿔"의 새 시트 **이름** 안의
    # '별'이 피벗으로 잡혀 확정된 rename 계획을 갈아치웠다(2026-08-20 게이트3: rename 규칙 0건의 진짜 원인).
    # 고신뢰 규칙이 이미 계획을 확정했으면 키워드 슬롯이 그것을 대체하지 못하게 한다.
    rule_confirmed_plan = bool(quick_action_plan) and not should_parse_with_llm and pending_operation is None

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
        # "플래너가 뒤집지 못하게"가 목적이다. 계획이 이미 규칙에서 왔다면 뒤집을
        # 플래너가 없고, 여기서 쓰기 단계만 남기면 규칙이 낸 나머지 단계가 사라진다
        # — 근거 필터가 "표 없애줘" 3단계를 1단계로 자른 것과 같은 부류다.
        # 정규화(intent) 계획은 **같은-액션 값 교체에 한해** 이 블록을 지난다:
        # 2026-08-18 실측, 스키마 강제 후 모델이 option에 태스크 이름을 되뇌어
        # 'write_value'가 셀에 쓰였는데 intent 면제 때문에 규칙의 정답(120)이
        # 못 이겼다. 문장에서 값을 뽑는 일은 결정적 규칙이 모델보다 정확하다.
        #
        # 규칙(rule) 계획 면제의 예외 하나: 단일 read_range. 값 나열의 "조회
        # 기간" 같은 낱말이 읽기 퀵 규칙을 켜면 완결된 쓰기가 조회로 오실행된다
        # (2026-08-18 ex2 재현 실측). 한 단계짜리 읽기는 잘려 나갈 다른 단계가
        # 없으니 덮어도 "표 없애줘" 3단계 절단 같은 사고가 나지 않는다.
        and (
            parsed.get("plan_source") != "rule"
            or [
                str(s.get("action", ""))
                for s in parsed["action_plan"]
                if isinstance(s, dict)
            ]
            == ["excel_live.read_range"]
        )
    ):
        planner_actions = [
            str(s.get("action", "")) for s in parsed["action_plan"] if isinstance(s, dict)
        ]
        if planner_actions == ["excel_live.write_range"]:
            # 액션이 같아도 **값은 규칙 것**을 쓴다. 2026-08-17 배터리 실측:
            # "A12에 합계 라고 입력해줘"에서 규칙은 '합계'를 뽑았는데 플래너의
            # '합계 라고'가 액션이 같다는 이유로 살아남아 그대로 셀에 들어갔다.
            # 문장에서 값을 뽑는 일은 결정적 규칙이 플래너보다 정확하다.
            planner_params = (
                parsed["action_plan"][0].get("params") or {}
                if isinstance(parsed["action_plan"][0], dict)
                else {}
            )
            if planner_params.get("values_2d") != preferred_write[0].params.get("values_2d"):
                parsed = {
                    "action_plan": [step.__dict__ for step in preferred_write],
                    "action": preferred_write[0].action,
                    "params": preferred_write[0].params,
                    "reason": "범위와 값을 지목한 쓰기",
                    "intent": "edit",
                    "plan_source": "rule",
                }
        else:
            # "K2:K181에 데이터 막대 넣어줘"는 범위+넣어줘라서 쓰기 폴백이 생긴다.
            # 이미 데이터 막대·수식·표를 골랐으면 그 계획을 쓰기로 덮지 않는다.
            #
            # 정규화(intent) 계획도 같은 목록으로 거른다. 예전에는 "쓰기 아닌
            # 분류는 의도"라고 전부 존중했는데, 2026-08-18 ex5 재현 실측에서
            # "A7:F7에 순위,SKU,… 입력"의 값 낱말(순위)을 pivot_table로
            # 오분류해 라벨 행이 통째로 사라졌다. 범위·값·입력 동사를 다 갖춘
            # 문장에서는 결정적 규칙이 모델 분류보다 정확하다 — 목록에 있는
            # 서식·차트류만 예외다. 재배치류(정렬·필터·중복 제거)는 "정렬해서
            # 입력해줘" 같은 문형이 실재해 목록에 남긴다.
            parsed_action = planner_actions[0] if planner_actions else ""
            if parsed_action not in {
                "excel_live.apply_data_bar",
                "excel_live.apply_color_scale",
                "excel_live.apply_formula_cf",
                "excel_live.set_formula",
                "excel_live.convert_to_excel_table",
                "excel_live.set_font",
                "excel_live.set_data_validation",
                "excel_live.highlight_by_condition",
                "excel_live.fill_range",
                "excel_live.apply_border",
                "excel_live.create_chart",
                "excel_live.sort_range",
                "excel_live.sort_rows",
                "excel_live.filter_rows",
                "excel_live.dedupe_rows",
            }:
                parsed = {
                    "action_plan": [step.__dict__ for step in preferred_write],
                    "action": preferred_write[0].action,
                    "params": preferred_write[0].params,
                    "reason": "범위와 값을 지목한 쓰기",
                    "intent": "edit",
                    # 값 격자는 결정적 규칙이 뽑았다 — 모델 해석이 아니므로 해석
                    # 카드를 띄우지 않는다(2026-08-19 붙여넣기 흐름 실측).
                    "plan_source": "rule",
                }

    # 플래너 파라미터가 명백히 오염됐고 빠른 규칙이 대안을 갖고 있으면 규칙을 쓴다.
    #
    # 2026-08-17 배터리 실측 — 규칙이 정답을 내놨는데 오염된 계획이 그걸 덮었다:
    #   "금액에 천 단위 콤마 넣어줘"
    #     quick_plan : set_number_format(A1:D9, "#,##0")            ← 정답
    #     plan_final : write_range("금액에 천 단위 콤마")            ← 지시문이 셀에
    #   "D2:D9 소수점 둘째 자리까지 보이게 해줘"
    #     quick_plan : set_number_format(D2:D9, "0.00")             ← 정답
    #     plan_final : set_number_format(format_code="소수점 둘째 자리") ← 말 그대로
    if (
        parsed
        and parsed.get("action_plan")
        and parsed.get("plan_source") not in {"rule", "intent"}
        and quick_action_plan
    ):
        tainted_first = (
            parsed["action_plan"][0] if isinstance(parsed["action_plan"][0], dict) else {}
        )
        t_action = str(tainted_first.get("action", ""))
        t_params = tainted_first.get("params") or {}
        quick_first_step = quick_action_plan[0] if isinstance(quick_action_plan[0], dict) else {}
        q_action = str(quick_first_step.get("action", ""))
        contaminated = False
        # ① 지시문을 값으로 쓰는 계획. 규칙이 같은 문장을 편집 액션으로 읽었다면
        #    그 문장은 데이터가 아니라 명령이다.
        if t_action == "excel_live.write_range" and q_action != "excel_live.write_range":
            joined = " ".join(
                str(c) for row in (t_params.get("values_2d") or []) for c in (row or [])
            ).strip()
            if len(joined) >= 4 and joined in str(req.message or ""):
                contaminated = True
        # ② 표시 형식이 형식 코드가 아니라 한국어 문장인 계획.
        if t_action == "excel_live.set_number_format" and not _looks_like_format_code(
            str(t_params.get("format_code", ""))
        ):
            contaminated = True
        # ③ 둘 다 표시 형식인데 코드가 다르다 — 코드는 문장에서 규칙이 결정적으로 읽었고
        #    ("첫째 자리"→0.0), 대상 열은 모델이 머리글을 보고 골랐다. 각자 잘하는 것을
        #    합친다: 대상은 모델, 코드는 규칙(2026-08-19 블라인드 게이트 교정 실측:
        #    "정시배송률은 소수 첫째 자리까지"가 모델 코드 '0.1'로 실행됐다).
        q_code = str((quick_first_step.get("params") or {}).get("format_code", "") or "")
        t_code = str(t_params.get("format_code", "") or "")
        if (
            not contaminated
            and t_action == "excel_live.set_number_format"
            and q_action == "excel_live.set_number_format"
            and q_code
            and t_code != q_code
        ):
            for plan_step in parsed.get("action_plan") or []:
                if isinstance(plan_step, dict) and plan_step.get("action") == "excel_live.set_number_format":
                    (plan_step.setdefault("params", {}))["format_code"] = q_code
            if isinstance(parsed.get("params"), dict) and parsed.get("action") == "excel_live.set_number_format":
                parsed["params"]["format_code"] = q_code
            trace_note("format_code_from_rule", detail=f"{t_code!r} → {q_code!r}")
        if contaminated:
            rule_plan = _normalize_plan_or_empty(quick_action_plan)
            if rule_plan:
                parsed = {
                    "action_plan": [s.__dict__ for s in rule_plan],
                    "action": rule_plan[0].action,
                    "params": rule_plan[0].params,
                    "reason": "플래너 파라미터 오염 — 규칙 계획으로 대체",
                    "intent": "edit",
                    "plan_source": "rule",
                }
            elif t_action == "excel_live.set_number_format" and re.search(
                r"콤마|천\s*단위|comma", str(req.message or ""), re.IGNORECASE
            ):
                # 규칙 대안이 없어도 원문이 콤마를 말했으면 코드로 교정한다 —
                # 'comma'가 그대로 셀 서식이 됐다(2026-08-18 배터리 실측).
                for plan_step in parsed.get("action_plan") or []:
                    if (
                        isinstance(plan_step, dict)
                        and plan_step.get("action") == "excel_live.set_number_format"
                    ):
                        (plan_step.setdefault("params", {}))["format_code"] = "#,##0"

    # 플래너가 고른 액션이 사용자의 말에 근거가 없으면, 근거 있는 규칙 후보로 되돌린다.
    # 같은 문장이 실행될 때마다 색칠·표생성·조건부서식으로 튀는 문제를 여기서 끊는다.
    #
    # 규칙이 만든 계획(plan_source=rule)은 대상이 아니다. 2026-08-17 실측:
    # "이 부분은 원래대로 초기화해줄 수 있어? 표 없애줘"에 규칙이 일부러
    # [테두리 제거, 배경 제거, 내용 비우기] 3단계를 냈는데, 문장에 '테두리'라는
    # 낱말이 없다고 이 필터가 2단계를 잘라 **내용만 비우고 테두리가 남았다.**
    # 규칙은 문장을 보고 발화된 것이라 낱말 대조를 다시 할 이유가 없다.
    if parsed and parsed.get("action_plan") and parsed.get("plan_source") not in {"rule", "intent"}:
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
        # 크기를 알아도(붙여넣은 A1:D13 → 13×4) 템플릿의 형식(일별/월별)이 미정이면
        # 그 질문 **한 번**은 한다 — 답이 헤더를 정한다. 이미 물었다면 merge가
        # 기본형으로 채웠으므로 여기 다시 오지 않는다.
        need_template_answer = (
            bool(slot.template_key)
            and bool(slot.template_follow_up_question)
            and not slot.template_question_asked
            and not slot.headers
        )
        need_follow_up = slot.rows is None or slot.cols is None or need_template_answer
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
            if follow_up_question == (slot.template_follow_up_question or ""):
                # 이 질문은 한 번만 한다 — 다음 턴의 답은 무조건 진행으로 이어진다.
                slot.template_question_asked = True
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
        # 시작 칸을 못 정했으면 기존 데이터 아래로 내린다 — A1 기본값이 시트를 덮었다.
        slot.start_cell = _safe_table_start_cell(slot, workbook_digest)
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

        op_slot = (
            None
            if rule_confirmed_plan
            else _merge_operation_slots(
                pending_operation,
                session_key=session_key,
                req=req,
                hints=operation_hints,
                parsed=parsed,
                digest=workbook_digest,
            )
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
            # 같은 질문을 두 번 하게 되면 답을 해석 못 한 것이다. 슬롯을 버리고
            # 이 턴을 새 명령으로 처리한다(2026-08-18 GUI 실측: 같은 질문 3연속에
            # 대화가 막혔다).
            if follow_up and follow_up == op_slot.last_question:
                _pending_operation_slots.pop(session_key, None)
                op_slot = None
                follow_up = ""
            # 새 멀티턴 시작이거나 기존 멀티턴 이어서 파라미터가 부족하면 질문한다.
            if follow_up and op_slot is not None:
                op_slot.last_question = follow_up
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

            op_plan_raw = _operation_action_plan(op_slot) if op_slot is not None else None
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
        # 여기는 _looks_like_excel_request가 False인 갈래다 — 계획을 못 만든 게 아니라
        # 애초에 엑셀 요청이 아니다. 400은 프론트에서 분기 불가능한 신호다.
        return _not_excel_response(req.message, "no_action_plan")

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
    candidate_log: list[dict[str, Any]] = []
    for cand_idx, candidate in enumerate([action_plan, *_recovery_plans()]):
        bound_candidate, candidate_notes = _bind_steps(candidate)
        try:
            current_plan = _validate_steps(bound_candidate)
            bind_notes = candidate_notes
            validation_error = None
            candidate_log.append({"candidate": cand_idx, "ok": True})
            break
        except Exception as exc:
            validation_error = exc
            candidate_log.append({"candidate": cand_idx, "ok": False, "error": str(exc)[:200]})
    # 바인더가 무엇을 채우고 무엇을 못 채웠는지, 검증이 어느 후보를 통과시켰는지 —
    # 예전엔 응답(param_bindings)에만 있고 로그엔 없었다(2026-08-19 로그 감사).
    trace_note(
        "binder",
        plan_source=str((parsed or {}).get("plan_source") or ""),
        notes=[
            {k: v for k, v in note.items() if k in ("action", "slot", "status", "reason", "changes")}
            for note in (bind_notes or [])
        ],
        candidates=candidate_log,
        validation_error=str(validation_error) if validation_error else "",
    )

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
    # '집계·요약'만으로는 주입하지 않는다 — _should_inject_pivot_step의 주석 참조(감사 B4).
    if pending_operation is None and _should_inject_pivot_step(req.message, current_plan):
        pivot_raw = _pivot_step_from_message(req.message, workbook_digest, sheet_name=req.sheet_name)
        if pivot_raw:
            extra = _bind_and_validate(_normalize_plan_or_empty([pivot_raw]))
            if extra:
                current_plan = current_plan + extra

    # "금액 높은 상위 3개를 노랗게" — 계획이 서식 단계 하나로 끝나면 "상위 3개"가
    # 아무 데도 남지 않고 열 전체가 칠해진다. 몇 개인지는 원문이 말했고 그 경계값이
    # 얼마인지는 파일만 안다. 모델에게 묻지 않고 여기서 실제 값으로 환산한다.
    if pending_operation is None and rank_limit.detect(req.message):
        narrowed = _rank_limited_format_plan(current_plan, req, digest=workbook_digest)
        if narrowed is not None:
            replacement = _bind_and_validate(_normalize_plan_or_empty([narrowed]))
            if replacement:
                current_plan = replacement + [
                    step for step in current_plan if step.action not in _RANK_LIMIT_FORMAT_ACTIONS
                ]

    # "막대 차트 만들어줘"인데 계획에 차트가 없다 — 피벗만 만들고 결과 시트 이름을
    # "차트"로 붙인 채 성공을 보고하는 일이 잦다. 원문이 분명히 그래프를 요구했으면
    # 종류가 확정될 때만 규칙으로 채우고, 아니면 아래에서 되묻는다.
    # 집계+차트는 종류가 부수다. 종류를 묻느라 피벗까지 미루면 본 작업이 안 된다.
    chart_requested = bool(_ACTION_EVIDENCE["excel_live.create_chart"].search(req.message))
    chart_planned = any(step.action == "excel_live.create_chart" for step in current_plan)
    chart_with_aggregate = _chart_accompanies_aggregate(req.message, current_plan)
    if pending_operation is None and chart_requested and not chart_planned:
        chart_raw = _chart_step_from_message(
            req.message,
            default_kind="bar" if chart_with_aggregate else "",
        )
        if chart_raw:
            extra = _bind_and_validate(_normalize_plan_or_empty([chart_raw]))
            if extra:
                current_plan = current_plan + extra
                chart_planned = True

    # 차트 종류는 결과물의 성격 자체를 바꾼다. 원문에 없으면 기본값(선)으로 밀지 않고 물어본다.
    # 단 "필터 → 피벗 → 차트"처럼 차트가 파이프라인의 마지막 단계일 뿐이라면
    # 여기서 멈춰 세우면 앞 단계 작업까지 통째로 미뤄지므로 그냥 진행한다.
    chart_only = {step.action for step in current_plan if step.action in _ACTION_EVIDENCE} == {
        "excel_live.create_chart"
    }
    # 그래프를 요구했는데 종류를 못 정해 계획에 넣지도 못했다면, 있지도 않은 차트를
    # 만들었다고 답하는 대신 종류를 묻는다. 집계 문장의 부수 차트는 묻지 않는다.
    #
    # 차트 **삭제** 계획이 있으면 문장의 '차트'는 지울 대상이지 만들 대상이 아니다
    # (2026-08-18 GUI 실측: "차트 같은거 다 지워주고 셀 초기화"에 종류를 물었다).
    chart_deletion_planned = any(
        step.action == "excel_live.delete_charts" for step in current_plan
    )
    if (
        pending_operation is None
        and chart_requested
        and not chart_planned
        and not chart_with_aggregate
        and not chart_deletion_planned
    ):
        unresolved_pairs = unresolved_pairs | {("excel_live.create_chart", "chart_type")}
    if (
        pending_operation is None
        and chart_only
        and not _CHART_TYPE_MENTION.search(req.message)
        and not chart_with_aggregate
        and not chart_deletion_planned
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
        # 되묻기 문구를 못 만들었다고 그대로 실행하면 안 된다.
        #
        # 2026-08-17 실측: write_range에는 대응하는 operation intent가 없어
        # `follow_up`이 비었고, 게이트를 통과해 셀에 '가장 큰 매출'이라는 **설명문**이
        # 써졌다. 무엇을 쓸지 모르는 채 쓰느니 묻는 편이 낫다.
        if ("excel_live.write_range", "values_2d") in unresolved_pairs:
            echoed = any(
                note.get("reason") == "echoed_request" for note in bind_notes
            )
            question = (
                "어떤 값을 넣을지 정하지 못했습니다. 계산 결과를 원하시면 어떤 열을 "
                "기준으로 할지 알려 주세요 (예: 매출 열의 최댓값)."
                if echoed
                else "어떤 값을 넣을까요?"
            )
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=question,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": question,
                    "unresolved_slots": sorted(f"{a}.{s}" for a, s in unresolved_pairs),
                },
            )

    trace_note("plan_final", plan_source=str((parsed or {}).get("plan_source") or ""), approve=req.approve, steps=trace_plan(current_plan))

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

    # 주의: 예전에는 여기서 `if ctx.approved: return None`으로 **모든 검사를**
    # 건너뛰었다. 그런데 /macro/step은 하위 명령 전체를 approve=True로 보내므로
    # (매크로 승인 한 번 = 전체 승인), 하위 명령의 **틀린 계획**이 블라스트 반경도
    # 계획 위생도 없이 실행됐다(2026-08-24 감사 B2). 재계획도 같은 구멍이었다 —
    # 승인 후 검증 실패로 다시 짠 계획은 사람이 본 적이 없는데 approved로 통과했다.
    # 승인이 면제하는 것은 **승인 카드 재요청뿐**이다. 검사는 항상 수행한다.

    # 블라스트 반경 — 사람이 가리키지 않은 자리의 **값**을 덮는가.
    # 계획이 옳은지는 판단하지 않는다. "지목한 자리"와 "건드릴 자리"만 비교하므로
    # 말투·어순·파서 커버리지와 무관하게 같은 보호가 남는다(2026-08-19 결과 워크북 감사:
    # 크로스시트 집계가 원본 시트에 써져 학생 이름을 지웠고, 어느 층도 못 막았다).
    scope_verdict = _assess_blast_radius(ctx, plan)

    # 계획 위생 — 워크북이 아니라 **계획과 원문의 관계**를 본다. 사후조건은 "계획이 말한 대로
    # 됐는가"만 보므로 계획 자체가 틀린 경우(지시문을 값으로 쓰기, 원본 시트에 쓰기)를
    # 원리적으로 못 잡는다(2026-08-19 결과 워크북 감사에서 확인).
    sanity_issues = _assess_plan_sanity(ctx, plan)
    if worst_severity(sanity_issues) == "block":
        question = _sanity_question(sanity_issues)
        trace_note(
            "plan_sanity",
            code=sanity_issues[0].code,
            detail=sanity_issues[0].detail[:120],
            action=sanity_issues[0].action,
        )
        previous = _pending_clarifications.get(ctx.session_key)
        _pending_clarifications[ctx.session_key] = PendingClarification(
            session_id=ctx.session_key,
            original_message=previous.original_message if previous else req.message,
            question=question,
            ask_count=(previous.ask_count + 1) if previous else 1,
            created_at_ts=time.time(),
        )
        return ExcelLiveActionResponse(
            ok=True,
            action="excel_live.clarify",
            reason=question,
            result={
                "ask_follow_up": True,
                "follow_up_question": question,
                "operation_intent": "clarify",
                "blocked_action": sanity_issues[0].action,
                "sanity_code": sanity_issues[0].code,
            },
        )

    if ctx.approved:
        if scope_verdict.is_risky:
            # 승인은 화면에 보인 계획에 대한 것이다. 매크로 하위 명령·재계획은 사람이
            # 본 적 없는데 지목 밖의 **값**까지 덮는다면 조용히 진행할 수 없다.
            # 카드는 못 쓴다 — 매크로 단계 응답은 approval_required를 소화하지 않아
            # 실행 없이 done으로 넘어가 버린다. 되묻기로 세우면 매크로가 waiting_input
            # 으로 멈추고 사람이 답한다(2026-08-19 크로스시트 집계가 학생 이름을 덮은
            # 사고가 정확히 이 모양이었다 — 승인 한 번 뒤의 무검문 실행).
            question = (
                f"승인된 작업이 지목하지 않은 칸의 값을 덮습니다 — {scope_verdict.summary()} "
                "계속할까요, 아니면 대상 범위를 알려주시겠어요?"
            )
            trace_note(
                "blast_radius_after_approval",
                cells=len(scope_verdict.risky),
                detail=scope_verdict.summary(limit=8),
            )
            previous = _pending_clarifications.get(ctx.session_key)
            _pending_clarifications[ctx.session_key] = PendingClarification(
                session_id=ctx.session_key,
                original_message=previous.original_message if previous else req.message,
                question=question,
                ask_count=(previous.ask_count + 1) if previous else 1,
                created_at_ts=time.time(),
            )
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                reason=question,
                result={
                    "ask_follow_up": True,
                    "follow_up_question": question,
                    "operation_intent": "clarify",
                    "blocked_action": plan[0].action if plan else "",
                    "sanity_code": "blast_radius_after_approval",
                },
            )
        # 검사를 통과한 승인 계획 — 카드를 다시 묻지 않고 실행으로 보낸다.
        # (정상 승인 흐름의 계획은 카드를 만들 때 이미 같은 검사를 통과했으므로
        # 재검사는 결정적으로 같은 결과다. 여기서 새로 잡히는 것은 매크로 하위
        # 명령과 재계획, 즉 사람이 본 적 없는 계획뿐이다.)
        return None

    confirm_steps = [
        step
        for step in plan
        if (tool := get_tool(step.action)) and tool.permission == PermissionLevel.CONFIRM
    ]
    # 확신 3분기: 규칙(plan_source=rule)이 아닌 계획은 모델의 **해석**이다.
    # 해석은 SAFE로 분류된 편집이라도 실행 전에 확인을 받는다 — 커버리지
    # 구멍이 조용한 오답 대신 "이렇게 이해했어요" 질문으로 나타나게 하는
    # 구조적 장치다(2026-08-18, 3개월 반복 루프의 근본 대책).
    plan_from_model = str(ctx.parsed.get("plan_source") or "") != "rule"
    if not confirm_steps:
        if plan_from_model:
            model_edit_steps = [step for step in plan if step.action in EDIT_ACTIONS]
            if not model_edit_steps:
                return None
            confirm_steps = model_edit_steps
        elif scope_verdict.is_risky:
            # 지금은 쓰기 계열이 전부 CONFIRM이라 여기까지 오지 않는다. 나중에 SAFE로 분류된
            # 쓰기 경로가 생겨도 지목 밖의 값을 조용히 덮지 않도록 남겨 둔다.
            risky_steps = [step for step in plan if step.action in EDIT_ACTIONS]
            if not risky_steps:
                return None
            confirm_steps = risky_steps
        else:
            return None

    head = confirm_steps[0]
    # 계획이 시트를 지정했으면 그 시트만 본다. 지정하지 않았을 때만 원문을 넘겨,
    # "Dashboard 시트 ~"처럼 콕 집었는데 없는 시트를 활성 시트로 대체하지 못하게 막는다.
    plan_sheet = str(head.params.get("sheet_name") or "").strip()
    guard_message = req.message
    if head.action == "excel_live.write_range":
        # "대시보드 시트 A1에 AI 기반 … 분석 시트 입력" — 셀 좌표 뒤는 값이다. 값 속 "분석 시트"를 지목으로
        # 읽어 "'분석' 시트를 찾을 수 없습니다"라고 되물었다(2026-08-19 ex13·ex16 실측).
        _cell = str(head.params.get("start_cell") or "")
        _pos = re.search(rf"(?<![A-Za-z0-9]){re.escape(_cell)}(?![A-Za-z0-9])", str(req.message or ""), re.IGNORECASE) if re.fullmatch(r"[A-Za-z]{1,3}\d{1,7}", _cell) else None
        if _pos:
            guard_message = str(req.message or "")[: _pos.end()]
    # 시트를 **만드는·새로 이름 붙이는** 액션은 그 이름이 아직 없는 게 정상이다.
    # 가드를 그대로 걸면 "급여계산 시트 하나 만들어줄래?"가
    # "'급여계산' 시트를 찾을 수 없습니다"로 되묻힌다 — 만들어 달라는 그 시트를 두고
    # 어디에 만들지 되묻는 꼴이다(2026-08-20 ex24 실측: 1·2번째 턴이 이렇게 죽고
    # 그 시트를 쓰는 뒤 턴들이 연쇄로 무너져 44/49가 됐다).
    target_problem = (
        ""
        if head.action in _SHEET_CREATING_ACTIONS
        else _edit_target_problem(
            req.workbook_id,
            plan_sheet or req.sheet_name,
            message="" if plan_sheet else guard_message,
        )
    )
    if target_problem:
        trace_note("target_missing", action=head.action, detail=target_problem)
        # 되묻고 끝내면 다음 턴이 맨바닥에서 다시 계획한다. 사용자의 답변("Sales_Data
        # 시트 H1에 넣어줘")만으로는 **무엇을 넣을지**를 알 수 없어, 실측에서
        # values_2d=[[""]]가 되어 빈 칸을 쓰고도 성공으로 보고됐다.
        # 원 요청과 질문을 남겨 두면 다음 턴 프롬프트(conversation_history_text)에
        # 함께 들어가 계획이 완성된다 — 플래너가 스스로 되물을 때와 같은 취급.
        previous = _pending_clarifications.get(ctx.session_key)
        _pending_clarifications[ctx.session_key] = PendingClarification(
            session_id=ctx.session_key,
            original_message=previous.original_message if previous else req.message,
            question=target_problem,
            ask_count=(previous.ask_count + 1) if previous else 1,
            created_at_ts=time.time(),
        )
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
        # 계획 전체가 된 이상, 다이얼로그도 계획 전체 + **바인더가 확정한 대상
        # 범위**를 보여줘야 한다(로드맵 2-3 — 어디에 적용되는지 모른 채 승인 금지).
        steps_text = "\n".join(
            _step_preview_line(idx, step.action, step.params)
            for idx, step in enumerate(plan, start=1)
        )
        pending.summary = f"다음 {len(plan)}단계를 실행합니다.\n{steps_text}"
        if any(step.action in _DESTRUCTIVE_ACTIONS for step in plan):
            pending.summary += "\n\n⚠ 표시 단계는 데이터가 지워지거나 재배치됩니다. 실행 후 '되돌리기'로 복구할 수 있습니다."
    if scope_verdict.is_risky:
        # "엑셀 셀 값을 수정합니다 — B10"만 보고는 성적부!B10의 학생 이름이 사라지는 걸 알 수 없다.
        # 무엇을 덮는지 적고 **해석 카드로 올린다** — 규칙이 낸 계획이라도 지목 밖을 건드리면
        # 그것은 확신이 아니라 해석이다(2026-08-19 결과 워크북 감사).
        pending.interpretation = True
        pending.summary = f"{pending.summary}\n\n⚠ {scope_verdict.summary()}"
        trace_note(
            "blast_radius",
            cells=len(scope_verdict.risky),
            detail=scope_verdict.summary(limit=8),
            action=head.action,
        )
    if sanity_issues:
        pending.interpretation = True
        pending.summary = f"{pending.summary}\n\n⚠ {_sanity_question(sanity_issues)}"
    if plan_from_model or scope_verdict.is_risky or sanity_issues:
        pending.interpretation = True
        pending.summary = (
            "이렇게 이해했어요:\n"
            + pending.summary
            + "\n\n해석이 다르면 취소하고 원하시는 작업을 다시 말씀해 주세요."
        )
    _pending_approvals[pending.approval_id] = PendingExcelApproval(
        action=head.action,
        params=head.params,
        workbook_id=req.workbook_id,
        sheet_name=req.sheet_name,
        created_at=pending.created_at,
        resume=replace(ctx, plan=list(plan), approved=True),
        interpretation=bool(getattr(pending, "interpretation", False)),
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


def _sanity_question(issues: list) -> str:
    """위생 문제를 사람에게 물을 한 문장으로."""
    head = issues[0]
    if head.code == "value_is_a_directive":
        return f"{head.detail} 그 문장을 글자 그대로 넣을까요, 아니면 계산 결과를 넣을까요?"
    if head.code == "writes_to_the_source_sheet":
        return f"{head.detail} 어느 시트에 쓸까요?"
    if head.code == "formula_refers_to_itself":
        return f"{head.detail} 어느 범위를 계산할까요?"
    return f"{head.detail} 이대로 진행할까요?"


def _assess_plan_sanity(ctx: PlanExecution, plan: list[PlanStep]) -> list:
    """계획이 원문과 앞뒤가 맞는지. 판정에 실패하면 통과시킨다(멀쩡한 작업을 막지 않는다)."""
    try:
        service = get_excel_live_service()
        workbook_id = _resolve_workbook_id(service, ctx.req.workbook_id)
        active_sheet = _resolve_sheet_name(service, workbook_id, ctx.req.sheet_name)
        return check_plan_sanity(
            [{"action": step.action, "params": dict(step.params or {})} for step in plan],
            message=str(ctx.req.message or ""),
            active_sheet=str(active_sheet or ""),
        )
    except Exception:
        return []


def _assess_blast_radius(ctx: PlanExecution, plan: list[PlanStep]):
    """계획이 지목 밖의 값을 덮는지 본다. 못 보면 통과시킨다 — 판정 실패로 멀쩡한 작업을 막지 않는다."""
    req = ctx.req
    try:
        service = get_excel_live_service()
        workbook_id = _resolve_workbook_id(service, req.workbook_id)
        active_sheet = _resolve_sheet_name(service, workbook_id, req.sheet_name)

        def _resolve_placeholder(sheet: str, token: str) -> str:
            # 실행기(_resolve_range)와 같은 값을 봐야 한다. 안 풀면 자리표시자를 쓰는
            # clear_range 대부분을 놓친다(2026-08-19 적대적 검증).
            if token == "__USED_RANGE__":
                return str(service.get_used_range_ref(workbook_id, sheet or active_sheet) or "")
            return str(service.get_active_selection_ref(workbook_id, sheet or active_sheet) or "")

        def _read_rect(sheet: str, ref: str):
            data = service.read_range(workbook_id, sheet, ref)
            return data.get("values") if isinstance(data, dict) else None

        plan_dicts = [{"action": step.action, "params": dict(step.params)} for step in plan]
        verdict = assess_write_scope(
            steps=plan_dicts,
            message=req.message,
            context_range=req.context_range,
            active_sheet=active_sheet,
            read_rect=_read_rect,
            resolve_placeholder=_resolve_placeholder,
        )
        # 정렬은 값을 "덮지" 않지만 행을 어긋나게 한다 — 되돌릴 수 없어 같은 카드에 함께 알린다.
        verdict.warnings.extend(
            assess_sort_integrity(
                plan_dicts,
                active_sheet=active_sheet,
                read_rect=_read_rect,
                used_ref_of=lambda sheet: service.get_used_range_ref(workbook_id, sheet or active_sheet),
                resolve_placeholder=_resolve_placeholder,
            )
        )
        return verdict
    except Exception as exc:
        trace_note("blast_radius", checked=False, detail=f"판정 생략: {type(exc).__name__}")

        class _Skip:
            is_risky = False
            risky: list = []

            @staticmethod
            def summary(limit: int = 4) -> str:
                return ""

        return _Skip()


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
    observed_count = 0
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
                    elif current_snapshot is not None:
                        # 스냅샷이 **있는데** 복원이 실패했다 — 조용히 삼키면 사용자는
                        # "검증 실패 + 원상 복구"로 읽는다. 실제로는 파일이 반쯤 바뀐
                        # 채다. 실패를 표면화하고 수동 복구 길을 알린다(감사 A1).
                        detail = (
                            f"{detail};auto_rollback_FAILED(백업이 있으면 '/복구' 또는 "
                            f"복구 백업 파일로 되돌리세요)"
                            if detail
                            else "auto_rollback_FAILED"
                        )
                    trace_route(
                        "verify:failed",
                        why=str(detail or "사후조건 불일치"),
                        action=action,
                        rolled_back=bool(restored),
                    )
                return bool(is_ok), str(detail or "")

            # 재계획 루프 안에서 정의되므로 이번 회차의 계획을 기본 인자로 묶는다.
            # 루프 변수를 그대로 닫으면 호출 시점의 값을 읽게 되고, 나중에 호출 위치가
            # 한 줄만 밀려도 다음 회차 계획을 실행하는 버그가 된다.
            def _run_execute_once(plan_for_this_round: list[PlanStep] | None = current_plan):
                nonlocal recovery_backup_info
                if recovery_backup_info is None and _plan_needs_recovery_backup(plan_for_this_round):
                    recovery_backup_info = _create_recovery_backup_if_possible(
                        workbook_id=req.workbook_id,
                        label="command",
                    )
                # 관측 루프에서는 관측 단계에서 계획을 끊는다. 끊지 않으면 뒤 단계가
                # 읽기 전에 정해진 인자로 실행돼, 재계획할 기회가 오기 전에 파일이 바뀐다.
                planned = excel_observation.truncate_at_observation(plan_for_this_round)
                return execute_plan(
                    steps=_chain_chart_to_pivot(
                        _drop_trailing_verification(_drop_table_step_when_aggregating(planned)),
                        session_key=session_key,
                    ),
                    execute_action=_guarded_execute,
                    verify_step=_guarded_verify,
                    max_attempts=2,
                    abort_on_failure=True,
                    reraise=(AmbiguousWorkbookError,),
                    repair=_repair_for_request(req),
                )

            execution, queue_wait_ms = await _run_in_excel_queue_async(
                "command-plan", _run_execute_once
            )
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

        # 관측만 하고 끝난 턴이면 그 값을 들고 다시 계획한다. 실패 재계획과 트리거가
        # 정반대라(성공한 관측 vs 깨진 편집) 따로 둔다.
        if excel_observation.should_replan_after_observation(execution, observed=observed_count):
            observed_count += 1
            observation_text = excel_observation.render_observation(execution)
            trace_route(
                f"observe:{observed_count}",
                why=f"{getattr(execution.last, 'action', '')} 결과를 보고 다시 계획",
                observed_chars=len(observation_text),
            )
            observe_context = dict(base_context)
            observe_context["personalization_hint"] = personalization_hint
            observe_context["observation_text"] = observation_text
            observe_digest = build_workbook_digest(
                get_excel_live_service(),
                workbook_id=req.workbook_id,
                active_sheet_hint=req.sheet_name,
            )
            observe_context["workbook_digest"] = observe_digest
            observe_context["workbook_digest_text"] = render_workbook_digest(observe_digest)
            try:
                observed_plan = await parse_command_plan_with_llm(
                    req.message,
                    llm,
                    context=observe_context,
                    forbid_list_action=True,
                    require_edit_action=True,
                )
                observed_steps, _ = _bind_plan_for_request(
                    _normalize_plan_or_empty(observed_plan.get("action_plan")),
                    digest=observe_digest,
                    req=req,
                )
                current_plan = _validate_plan_for_request(observed_steps, req)
                parsed["reason"] = observed_plan.get("reason", "") or parsed.get("reason", "")
                continue
            except Exception as exc:
                trace_route("observe:replan_failed", why=f"{type(exc).__name__}: {exc}")
                break

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
        engine=_engine_name(),
        steps=[
            {
                "index": s.index,
                "action": s.action,
                # 실제로 실행된 파라미터(보정됐으면 보정본)와 원본 — "무엇을 어디에"가
                # 로그에 있어야 잘못 간 대상을 plan_final과 대조하지 않고도 본다.
                "params": s.params,
                "original_params": s.original_params,
                "ok": not s.error,
                "verified": s.verified,
                "retried": s.retried,
                "error": s.error or "",
                "verify_detail": s.verify_detail or "",
                # 결과는 값 격자만 빼고 전부 — 예전의 5키 화이트리스트는 changed_cells·
                # replaced_cells·no_change 같은 규모 키를 버려 "변화 없음" 판정 근거가 없었다.
                "result": {
                    key: value
                    for key, value in (s.result or {}).items()
                    if isinstance(s.result, dict) and key not in ("values", "rows_data", "xlwings_ops", "data", "preview")
                },
                # 실행 직후 대상 범위의 값(≤60칸) — 조용한 오배치·빈 쓰기를 로그만으로 잡는다.
                "after": _after_snapshot(s, req.workbook_id, req.sheet_name),
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
    # 한 칸에 값을 써 넣었다면 기억해 둔다. 다음 턴의 "아니 부산으로 바꿔줘"가
    # 시트 전체를 뒤지는 대신 이 칸을 고치게 하려면 이게 있어야 한다.
    if primary.action == "excel_live.write_range":
        record_last_write(
            session_key,
            sheet_name=str((primary.params or {}).get("sheet_name") or ""),
            address=address,
            values=(primary.params or {}).get("values_2d"),
        )
    # 한 칸짜리 수식도 기억한다 — "그 아래 칸에는 평균"의 문맥이다.
    if primary.action == "excel_live.set_formula":
        record_last_formula(
            session_key,
            sheet_name=str((primary.params or {}).get("sheet_name") or ""),
            cell=str((primary.params or {}).get("range_ref") or ""),
            formula=str((primary.params or {}).get("formula_a1") or ""),
        )
    # 방금 파일을 건드렸다. 다이제스트 캐시(TTL 20초)를 버리지 않으면 다음 단계가
    # 앞 단계의 결과를 못 본다 — 매크로는 2~3초 간격으로 돈다.
    invalidate_workbook_digest(req.workbook_id)
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
    # 매 실행의 단계별 보고 — 프런트가 이 문자열을 그대로 그린다(2026-08-18).
    execution_report = _build_execution_report(execution.steps)
    if execution_report:
        last_result["execution_report"] = execution_report
    if rollback_events:
        last_result["execution_report"] = (
            f"{last_result.get('execution_report', '')}\n⚠ 검증 실패로 {len(rollback_events)}건을 자동 되돌렸습니다."
        ).strip()
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
        # 어떤 액션이 / 어떤 범위에서 / 왜 실패했는지가 사용자에게 도달해야 한다.
        # 등급별 정형 문구만 주면 "작업에 실패했습니다"와 다를 게 없다.
        failure_lines = [_step_failure_line(s) for s in execution.steps if s.error or not s.verified]
        reason = _verification_failure_reason(failure_detail)
        if failure_lines:
            reason = f"{failure_lines[0]} ({reason})"
        return ExcelLiveActionResponse(
            ok=False,
            action=last.action,
            reason=reason,
            result={
                "failed_steps": failure_lines,
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

                (retry_set, retry_verify), retry_queue_wait_ms = await _run_in_excel_queue_async(
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

    reason = parsed.get("reason", "") or primary.reason
    no_match = _no_match_note(primary.action, last_result)
    if no_match:
        # 성공인데 파일이 안 바뀐 경우다. 말해 주지 않으면 사용자는 명령이 씹혔다고
        # 생각하고 같은 문장을 다시 친다.
        last_result["no_matching_cells"] = True
        reason = f"{reason} — {no_match}" if reason else no_match

    return ExcelLiveActionResponse(
        ok=True,
        action=primary.action,
        result=last_result,
        reason=reason,
    )


def _no_match_note(action: str, result: dict[str, Any]) -> str:
    """실행은 성공했는데 사용자 눈에 보이는 변화가 없는 경우의 안내 문구.

    말해 주지 않으면 사용자는 명령이 씹혔다고 생각하고 같은 문장을 다시 친다.
    """
    if action == "excel_live.clear_range":
        # 2026-08-17 실측: 서식(배경·테두리)만 있고 값이 없는 범위를 비우고
        # "완료"가 나갔다. 화면은 그대로였고 사용자는 "아무것도 안 됐다"고 했다.
        emptied = result.get("emptied_values")
        if emptied is None or int(emptied or 0) >= 1:
            return ""
        # 여러 단계 계획(테두리·배경 제거 포함)이면 화면이 실제로 바뀌므로 조용히 넘어간다.
        if result.get("executed_steps"):
            return ""
        return (
            "지울 값이 없는 범위였습니다. 배경색·테두리 같은 서식을 없애려면 "
            "'서식 지워줘' 또는 '초기화해줘'라고 말씀해 주세요"
        )
    if action == "excel_live.find_replace":
        # 2026-08-17 실측: 못 찾은 찾아바꾸기가 "완료"로만 나갔다.
        replaced = result.get("replaced_cells")
        if replaced is not None and int(replaced or 0) == 0:
            return "바꿀 대상을 찾지 못해 변경된 셀이 없습니다"
        return ""
    if action == "excel_live.dedupe_rows":
        removed = result.get("removed_rows")
        if (
            removed is not None
            and int(removed or 0) == 0
            and int(result.get("remaining_rows", 0) or 0) > 0
        ):
            return "중복된 행이 없어 지운 행이 없습니다"
        return ""
    if action == "excel_live.filter_rows":
        # 무일치 keep 필터는 실행기가 파일을 건드리지 않고 no_change로 돌아온다
        # (그대로 진행하면 시트가 통째로 비기 때문). 그 사실을 말로 전한다.
        if result.get("no_change") and int(result.get("matched_rows", 0) or 0) == 0:
            return "조건에 맞는 행이 없어 아무것도 바꾸지 않았습니다. 값이나 열을 확인해 주세요"
        if result.get("no_change"):
            return "모든 행이 조건에 맞아 지울 행이 없습니다"
        return ""
    if action != "excel_live.highlight_by_condition":
        return ""
    if int(result.get("scanned_cells", 0) or 0) < 1:
        return ""
    if int(result.get("changed_cells", 0) or 0) >= 1:
        return ""
    return (
        f"조건에 맞는 셀이 없어 변경된 항목이 없습니다 "
        f"({result.get('scanned_cells')}칸을 검사했습니다)"
    )


@router.post("/approval", response_model=ExcelLiveActionResponse)
async def post_approval(
    req: ApprovalResponse,
    llm: LLMService = Depends(get_llm_service),
):
    """승인 카드의 결정 한 턴 — 승인·거절·만료 전부 chat_log.jsonl에 한 줄로 남는다.

    2026-08-19 로그 감사: 거절·단일 액션 승인·만료(404)는 turn_scope 밖이라 흔적이
    audit에만 있었다. "해석 카드를 얼마나 취소하는가"가 플래너 품질의 핵심 신호인데
    로그로 셀 수 없었다. 이제 모든 분기가 같은 턴 기록 안에서 끝난다.
    """
    pending = _pending_approvals.pop(req.approval_id, None)
    resume = getattr(pending, "resume", None) if pending else None
    origin_message = (
        str(getattr(getattr(resume, "req", None), "message", "") or "")
        if resume is not None
        else (str(getattr(pending, "action", "") or "") if pending else "")
    )
    session_key = str(getattr(resume, "session_key", "") or "") if resume is not None else ""
    with trace_origin(
        user_input=origin_message,
        kind="approval",
        approved=bool(req.approved),
        approval_id=req.approval_id,
        approved_action=str(getattr(pending, "action", "") or "") if pending else "",
        interpretation=bool(getattr(pending, "interpretation", False)) if pending else False,
        rejection_reason=str(req.rejection_reason or ""),
    ), turn_scope(
        endpoint="excel-live/approval",
        message=origin_message,
        session_id=session_key,
        request={
            "approval_id": req.approval_id,
            "approved": bool(req.approved),
            "workbook_id": getattr(pending, "workbook_id", None) if pending else None,
            "sheet_name": getattr(pending, "sheet_name", None) if pending else None,
            "planned_steps": len(resume.plan) if resume is not None else (1 if pending else 0),
            "engine": _engine_name(),
        },
    ):
        if pending is None:
            trace_route("approval:missing", why="승인 대기 작업 없음 — 만료·중복 클릭·재시작")
            raise HTTPException(status_code=404, detail="승인 대기 작업을 찾을 수 없습니다.")

        if not req.approved:
            _audit.log(
                action="excel.live.approval.rejected",
                target=pending.action,
                detail=f"approval_id={req.approval_id}",
            )
            trace_route(
                "approval:rejected",
                why="사용자가 거부" + (" (해석 카드)" if pending.interpretation else ""),
                action=pending.action,
                interpretation=pending.interpretation,
            )
            response = ExcelLiveActionResponse(
                ok=True,
                action=pending.action,
                approval_required=False,
                reason="사용자가 작업을 거부했습니다.",
                result={"approved": False, "interpretation": pending.interpretation},
            )
            set_outcome_from_response(response)
            return response

        if pending.resume is not None:
            # 명령 경로가 세운 계획을 그대로 이어서 실행한다. 재계획하지 않으므로
            # 사용자가 승인한 계획과 실행되는 계획이 같다.
            _audit.log(
                action="excel.live.approval.executed",
                target=pending.action,
                detail=f"approval_id={req.approval_id} steps={len(pending.resume.plan)}",
            )
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

            result, queue_wait_ms = await _run_in_excel_queue_async("approval", _run_approval_once)
            if isinstance(result, dict):
                result["queue_wait_ms"] = queue_wait_ms
                if recovery_backup:
                    result["recovery_backup"] = recovery_backup
            _audit.log(
                action="excel.live.approval.executed",
                target=pending.action,
                detail=f"approval_id={req.approval_id}",
            )
            response = ExcelLiveActionResponse(
                ok=True,
                action=pending.action,
                result=result,
                reason="승인 후 작업이 실행되었습니다.",
            )
            set_outcome_from_response(response)
            return response
        except (WorkbookNotFoundError, WorksheetNotFoundError) as exc:
            # 승인까지 누른 사용자에게 404를 던지면 "요청한 정보를 찾을 수 없습니다"만 남는다.
            # 무엇이 없어서 못 했는지, 무엇을 알려주면 되는지 문장으로 돌려준다.
            detail = f"{exc} 작업할 파일과 시트를 알려주시면 다시 진행하겠습니다."
            _audit.log(
                action="excel.live.approval.target_missing",
                target=pending.action,
                detail=f"approval_id={req.approval_id} {exc}",
            )
            response = ExcelLiveActionResponse(
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
            set_outcome_from_response(response)
            return response
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
    """매크로 제어 한 턴(승인·건너뛰기·답변·완료) — 하위 명령은 각자 줄을 남기지만
    제어 자체는 audit에만 있었다(2026-08-19 로그 감사). 이제 이 줄이 그 제어를 적는다."""
    run = _macro_runs.get(req.macro_id)
    with turn_scope(
        endpoint="excel-live/macro/step",
        message=str(getattr(run, "message", "") or "(매크로 단계)"),
        session_id=str(getattr(run, "session_id", "") or ""),
        request={
            "macro_id": req.macro_id,
            "skip_indices": list(req.skip_indices or []),
            "answer": req.answer,
            "skip_current": bool(getattr(req, "skip_current", False)),
            "engine": _engine_name(),
        },
    ):
        response = await _post_macro_step_inner(req, llm)
        set_outcome_from_response(response)
        return response


async def _post_macro_step_inner(
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
        run.backup, _ = await _run_in_excel_queue_async(
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
        # 하위 명령의 message는 분해기가 만든 문장이다. 사람이 실제로 요구한 말과
        # 계보를 함께 남겨야, 19줄짜리 로그를 보고 "이게 어느 한마디에서 나왔나"를 되짚을 수 있다.
        with trace_origin(
            user_input=run.message,
            kind="macro_step",
            macro_id=run.macro_id,
            step_index=step.index,
            total_steps=len(run.steps),
            step_command=step.command,
            user_reply=answer or None,
        ):
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
    """매크로 중단/롤백 한 턴 — chat_log.jsonl에 남긴다(2026-08-19 로그 감사)."""
    run = _macro_runs.get(req.macro_id)
    with turn_scope(
        endpoint="excel-live/macro/abort",
        message=str(getattr(run, "message", "") or "(매크로 중단)"),
        session_id=str(getattr(run, "session_id", "") or ""),
        request={"macro_id": req.macro_id, "rollback": bool(req.rollback), "engine": _engine_name()},
    ):
        response = _post_macro_abort_inner(req)
        set_outcome_from_response(response)
        return response


def _post_macro_abort_inner(req: ExcelLiveMacroAbortRequest):
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

