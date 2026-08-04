"""규칙 경로가 문장을 통째로 대표하지 못할 때 플래너로 넘기는지.

규칙은 동사 하나만 보고 액션을 정한다. 그래서
- "마진율로 바꿔줘"의 '마진율'이 수식 키워드에 걸려 열 이름 변경이 계산 대화가 되고,
- "Summary 시트 만들어서 B1에 합계 수식 넣어줘"가 빈 시트 하나만 만들고 끝난다.
둘 다 실행은 성공으로 보고되므로 로그만 봐서는 잘못을 알 수 없다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import (
    _build_quick_action_plan,
    _detect_operation_intent,
    _is_likely_edit_request,
    _quick_plan_underfits_message,
)


@pytest.mark.parametrize(
    "message",
    [
        "Profit_Margin 열 이름을 마진율로 바꿔줘",
        "이익률 열 머리글을 마진으로 변경해줘",
        "맨 뒤에 원가총액 열 하나 추가해줘",
        "새 열 만들어줘",
        "Discount 열은 이제 안 쓰니까 지워줘",
    ],
)
def test_column_structure_edits_do_not_become_slot_dialogs(message):
    assert _detect_operation_intent(message) == ""


def test_column_structure_edits_are_still_treated_as_edits():
    message = "Profit_Margin 열 이름을 마진율로 바꿔줘"
    assert _is_likely_edit_request(message, {"intent": ""}) is True


def test_named_formula_request_is_still_a_formula():
    assert _detect_operation_intent("이익률 열에 매출이익 나누기 매출 수식을 넣어줘") == "formula"


def test_sheet_creation_with_more_work_goes_to_the_planner():
    message = "Summary 시트 만들어서 A1에 총매출이라고 쓰고 B1에 매출 합계 수식 넣어줘"
    plan = _build_quick_action_plan(message, None)
    assert plan and plan[0]["action"] == "excel_live.create_sheet"
    assert _quick_plan_underfits_message(plan[0]["action"], message) is True


def test_plain_sheet_creation_keeps_the_quick_path():
    message = "요약 시트 만들어줘"
    plan = _build_quick_action_plan(message, None)
    assert plan and plan[0]["action"] == "excel_live.create_sheet"
    assert _quick_plan_underfits_message(plan[0]["action"], message) is False
