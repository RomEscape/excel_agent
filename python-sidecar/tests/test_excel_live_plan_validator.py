"""Excel Live 계획 validator 단위 테스트."""

from office_claw_sidecar.services.excel_live_executor import PlanStep
from office_claw_sidecar.services.excel_live_plan_validator import (
    ValidationContext,
    validate_plan,
)


def test_validate_create_table_clamps_rows_cols():
    out = validate_plan(
        [PlanStep(action="excel_live.create_table", params={"rows": 999, "cols": 0}, reason="")],
        context=ValidationContext(message="표 생성"),
    )
    assert out[0].params["rows"] == 100
    assert out[0].params["cols"] == 1


def test_validate_recover_table_intent_from_invalid_write_range():
    out = validate_plan(
        [PlanStep(action="excel_live.write_range", params={"start_cell": "__ACTIVE_CELL__"}, reason="")],
        context=ValidationContext(message="5*5 표 만들어줘"),
    )
    assert out[0].action == "excel_live.create_table"
    assert out[0].params["rows"] == 5
    assert out[0].params["cols"] == 5


def test_validate_uses_context_range_for_ambiguous_border():
    out = validate_plan(
        [PlanStep(action="excel_live.apply_border", params={"target_range": "__ACTIVE_SELECTION__"}, reason="")],
        context=ValidationContext(message="여기에 테두리 적용", context_range="C3:E9"),
    )
    assert out[0].params["target_range"] == "C3:E9"


def test_validate_highlight_alias_params_are_normalized():
    out = validate_plan(
        [
            PlanStep(
                action="excel_live.highlight_by_condition",
                params={"range_ref": "A:A", "condition": ">=", "value": 10, "color": "yellow"},
                reason="",
            )
        ],
        context=ValidationContext(message="A열 10 이상 노란색"),
    )
    assert out[0].params["target_range"] == "A:A"
    assert out[0].params["operator"] == ">="
    assert out[0].params["threshold"] == 10.0
    assert out[0].params["fill_color"] == "yellow"


def test_validate_formula_requires_equals_prefix():
    try:
        validate_plan(
            [PlanStep(action="excel_live.set_formula", params={"range_ref": "A1", "formula_a1": "SUM(A1:A10)"})],
            context=ValidationContext(message="수식 넣어줘"),
        )
        assert False, "ValueError expected"
    except ValueError:
        pass

