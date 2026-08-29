"""액션 커버리지 생성기 검증.

여기서 막고 싶은 실패는 "학습은 시켰는데 실행 단계에서 반려되는 계획"이다.
그래서 생성물을 프로덕션 검증기에 그대로 통과시켜 본다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_action_coverage_cases import (
    build_action_coverage_records,
    covered_actions,
)
from office_claw_sidecar.services.excel_live_executor import PlanStep
from office_claw_sidecar.services.excel_live_plan_validator import (
    PLANNER_ONLY_ACTIONS,
    SUPPORTED_ACTIONS,
    ValidationContext,
    validate_plan,
)
from office_claw_sidecar.services.excel_workbook_fixtures import digest_headers

EXECUTABLE_ACTIONS = SUPPORTED_ACTIONS - PLANNER_ONLY_ACTIONS


@pytest.fixture(scope="module")
def records():
    return build_action_coverage_records(per_action=6)


def test_covers_every_executable_action():
    """실행 가능한 액션 중 하나라도 빠지면 그 기능은 영영 선택되지 않는다."""
    missing = EXECUTABLE_ACTIONS - covered_actions()
    assert not missing, f"사례가 없는 액션: {sorted(missing)}"


def test_no_planner_only_action_is_generated(records):
    for record in records:
        for step in record["output_json"]["action_plan"]:
            assert step["action"] not in PLANNER_ONLY_ACTIONS


def test_every_record_passes_production_validator(records):
    failures: list[str] = []
    for record in records:
        steps = [
            PlanStep(action=s["action"], params=s["params"], reason=s["reason"])
            for s in record["output_json"]["action_plan"]
        ]
        context = ValidationContext(message=record["instruction"])
        try:
            validated = validate_plan(steps, context=context)
        except ValueError as exc:
            failures.append(f"{record['record_id']}: {exc}")
            continue
        # 검증기가 다른 액션으로 갈아끼우면 우리가 가르치려던 액션이 아니게 된다.
        if validated[0].action != steps[0].action:
            failures.append(
                f"{record['record_id']}: {steps[0].action} → {validated[0].action}로 치환됨"
            )
    assert not failures, "검증 실패:\n" + "\n".join(failures)


def test_every_action_actually_produced_records(records):
    produced = {step["action"] for r in records for step in r["output_json"]["action_plan"]}
    missing = EXECUTABLE_ACTIONS - produced
    assert not missing, f"등록은 됐지만 레코드가 안 만들어진 액션: {sorted(missing)}"


def test_referenced_columns_exist_in_digest(records):
    """정답이 다이제스트에 없는 열을 가리키면 환각을 정답으로 가르치게 된다."""
    column_keys = ("column", "group_column", "value_column", "row_field", "value_field")
    failures: list[str] = []
    for record in records:
        headers = set(digest_headers(record["digest"]))
        for step in record["output_json"]["action_plan"]:
            for key in column_keys:
                value = step["params"].get(key)
                if isinstance(value, str) and value and value not in headers:
                    failures.append(f"{record['record_id']}: {key}={value} (열 없음)")
    assert not failures, "다이제스트에 없는 열 참조:\n" + "\n".join(failures)


def test_referenced_sheets_exist_in_digest(records):
    sheet_keys = ("sheet_name", "source_sheet", "left_sheet", "right_sheet")
    failures: list[str] = []
    for record in records:
        names = {str(s.get("name")) for s in record["digest"]["sheets"]}
        for step in record["output_json"]["action_plan"]:
            if step["action"] == "excel_live.create_sheet":
                continue
            for key in sheet_keys:
                value = step["params"].get(key)
                if isinstance(value, str) and value and value not in names:
                    failures.append(f"{record['record_id']}: {key}={value} (시트 없음)")
            for item in step["params"].get("source_sheets") or []:
                if str(item) not in names:
                    failures.append(f"{record['record_id']}: source_sheets={item} (시트 없음)")
    assert not failures, "다이제스트에 없는 시트 참조:\n" + "\n".join(failures)


def test_output_sheet_is_new(records):
    """결과 시트가 기존 시트면 '원본을 덮어써라'를 가르치게 된다."""
    failures: list[str] = []
    for record in records:
        names = {str(s.get("name")) for s in record["digest"]["sheets"]}
        for step in record["output_json"]["action_plan"]:
            out = step["params"].get("output_sheet")
            # 차트는 기존 시트 위에 얹는 것이 정상이다.
            if step["action"] == "excel_live.create_chart":
                continue
            if isinstance(out, str) and out in names:
                failures.append(f"{record['record_id']}: output_sheet={out}")
    assert not failures, "결과 시트가 기존 시트와 겹침:\n" + "\n".join(failures)


def test_instructions_vary_per_action(records):
    """같은 문장만 반복되면 모델이 문장을 통째로 외운다."""
    by_action: dict[str, set[str]] = {}
    for record in records:
        action = record["output_json"]["action_plan"][0]["action"]
        by_action.setdefault(action, set()).add(record["instruction"])
    thin = {a: len(v) for a, v in by_action.items() if len(v) < 2}
    assert not thin, f"지시문이 한 가지뿐인 액션: {thin}"
