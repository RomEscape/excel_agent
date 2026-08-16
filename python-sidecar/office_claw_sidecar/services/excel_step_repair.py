"""실패한 단계의 파라미터를 재시도 전에 고친다 (Task 8-2).

## 왜 필요한가

실행기는 실패한 단계를 **같은 파라미터로** 다시 던졌다. 잘못된 범위나 없는
시트명 같은 결정적 실패는 두 번 똑같이 실패하고 지연만 두 배가 된다.

실측이 이걸 뒷받침한다 — `logs/diagnostics/*.jsonl` 전체 **410단계 중 재시도는
3회뿐이었고 그중 성공은 0회**다(게다가 그 3회는 Task 7에서 고친 검증기 오탐이
부른 것이었다). 그래서 이 모듈이 못 고치는 실패는 재시도하지 않는다. 나중에
일시적 실패 유형이 실제로 관측되면 그때 그 유형을 여기 추가한다 — 한 번도
통한 적 없는 맹목적 재시도를 남겨 두는 것보다 낫다.

## 무엇을 고치나

1. 범위 문자열 정규화 — `a1:c10` → `A1:C10`, 전각 콜론, 내부 공백, `$`
2. 없는 시트명 → 활성 시트
3. 안 풀린 `__ACTIVE_SELECTION__` → 컨텍스트 범위 → 활성 셀

고칠 게 없으면 `None`을 돌려준다. 실행기는 이걸 "재시도해도 소용없다"로 읽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from office_claw_sidecar.services.excel_live_executor import PlanStep

ACTIVE_SELECTION = "__ACTIVE_SELECTION__"

# 범위를 담는 파라미터. 값이 A1 표기이거나 `__ACTIVE_SELECTION__`인 것들이다.
RANGE_PARAM_KEYS = (
    "target_range",
    "range_ref",
    "source_range",
    "left_range",
    "right_range",
    "start_cell",
    "output_start",
)
SHEET_PARAM_KEYS = ("sheet_name", "output_sheet", "left_sheet", "right_sheet")

# 표 전체를 대상으로 하는 액션. 이것들의 `__ACTIVE_SELECTION__`은 라우터가 시트의
# 사용 영역으로 정확히 편다(excel_live.py의 _resolve_symbolic_range).
#
# 그런데 여기서 먼저 센티널을 지워 버리면 그 정식 해석이 재시도에서 영영 안 돈다.
# 대체값으로 쓰이는 ctx.active_cell은 **직전 턴이 건드린 범위**라, 차트를 G1:G4로
# 만든 다음 "지역별 매출 집계해줘"를 하면 피벗이 G열(Product) 4칸을 집계하려다
# "'Product' 열은 숫자가 아니라 sum 집계를 할 수 없습니다"로 죽는다(2026-08-16 실측).
# 좁은 범위를 물려받아 조용히 틀린 답을 내느니, 센티널을 남겨 두는 편이 낫다.
TABLE_SCOPED_ACTIONS = frozenset(
    {
        "excel_live.pivot_table",
        "excel_live.create_chart",
        "excel_live.sort_range",
        "excel_live.sort_rows",
        "excel_live.filter_rows",
        "excel_live.dedupe_rows",
        "excel_live.group_by_aggregate",
        "excel_live.find_duplicates",
        "excel_live.convert_to_excel_table",
        "excel_live.create_table",
        "excel_live.validate_data",
        "excel_live.forecast_linear",
    }
)

_A1_PATTERN = re.compile(r"^\$?[A-Z]{1,3}\$?\d{1,7}(:\$?[A-Z]{1,3}\$?\d{1,7})?$")
_COLUMN_SPAN_PATTERN = re.compile(r"^\$?[A-Z]{1,3}(:\$?[A-Z]{1,3})?$")


@dataclass(frozen=True)
class RepairContext:
    """보정에 필요한 통합문서 사실들. COM 없이 단위 테스트할 수 있게 값으로 받는다."""

    sheet_names: tuple[str, ...] = ()
    active_sheet: str | None = None
    context_range: str | None = None
    active_cell: str | None = None

    def resolve_sheet(self, name: str) -> str | None:
        """대소문자·앞뒤 공백만 다른 시트를 찾아 준다. 못 찾으면 None."""
        target = str(name or "").strip().casefold()
        if not target:
            return None
        for actual in self.sheet_names:
            if str(actual).strip().casefold() == target:
                return actual
        return None


def normalize_range_text(value: str) -> str:
    """` a1 ： c10 ` → `A1:C10`. A1 표기가 아니면 원문을 그대로 돌려준다."""
    text = str(value or "")
    # 한글 IME가 남기는 전각 콜론은 xlwings가 범위로 못 읽는다.
    text = text.replace("：", ":").replace("∶", ":")
    text = re.sub(r"\s+", "", text).replace("$", "").upper()
    if not text:
        return ""
    if _A1_PATTERN.match(text) or _COLUMN_SPAN_PATTERN.match(text):
        return text
    return str(value or "").strip()


def repair_step(
    step: PlanStep,
    failure: Exception | str,
    ctx: RepairContext,
) -> PlanStep | None:
    """재시도용으로 고친 단계. 고칠 게 없으면 None.

    `failure`는 아직 안 본다 — 지금 고치는 셋(범위 표기·시트명·활성 선택)은 실패
    메시지와 무관하게 파라미터 모양만으로 판정된다. 실패 유형별 보정이 생기면
    여기서 갈라 쓴다.
    """
    del failure
    params = dict(step.params or {})
    changed = False

    for key in RANGE_PARAM_KEYS:
        raw = params.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if raw.strip() == ACTIVE_SELECTION:
            if step.action in TABLE_SCOPED_ACTIONS:
                # 라우터의 사용 영역 해석에 맡긴다. 직전 턴의 범위로 바꾸지 않는다.
                continue
            resolved = _resolve_active_selection(ctx)
            if resolved and resolved != raw:
                params[key] = resolved
                changed = True
            continue
        fixed = normalize_range_text(raw)
        if fixed and fixed != raw:
            params[key] = fixed
            changed = True

    for key in SHEET_PARAM_KEYS:
        raw = params.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if not ctx.sheet_names:
            continue
        actual = ctx.resolve_sheet(raw)
        if actual is not None:
            # 대소문자만 달랐던 경우. 실제 이름으로 맞춰 준다.
            if actual != raw:
                params[key] = actual
                changed = True
            continue
        # 아예 없는 시트다. 새로 만드는 출력 시트일 수 있으므로 대상 시트만 바꾼다.
        if key == "sheet_name" and ctx.active_sheet:
            params[key] = ctx.active_sheet
            changed = True

    if not changed:
        return None
    return PlanStep(action=step.action, params=params, reason=step.reason)


def _resolve_active_selection(ctx: RepairContext) -> str:
    for candidate in (ctx.context_range, ctx.active_cell):
        text = normalize_range_text(candidate or "")
        if text and text != ACTIVE_SELECTION:
            return text
    return ""
