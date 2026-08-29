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
    _extract_excel_table_name,
    _extract_formula_from_text,
    _extract_operation_hints,
    _extract_quoted_chart_title,
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


def test_header_list_writes_the_first_row_instead_of_adding_columns():
    """이미 만든 표에 머리글을 넣어달라는 문장. LLM에 맡기면 이름마다 열을 덧붙인다."""
    message = (
        "헤더에는 '날짜', '사용 목적', '사용처', '법인카드 사용내역서 여부', "
        "'금액', '법인카드, 조교카드 이체 여부', '비용 유형' 이렇게 목록을 만들어줄 수 있어?"
    )
    plan = _build_quick_action_plan(message, None)

    assert plan and plan[0]["action"] == "excel_live.write_range"
    assert plan[0]["params"]["start_cell"] == "__USED_RANGE__"
    assert plan[0]["params"]["values_2d"] == [
        [
            "날짜",
            "사용 목적",
            "사용처",
            "법인카드 사용내역서 여부",
            "금액",
            "법인카드, 조교카드 이체 여부",
            "비용 유형",
        ]
    ]


def test_header_list_respects_an_explicit_start_cell():
    plan = _build_quick_action_plan("B2부터 헤더는 이름, 금액, 날짜로 넣어줘", None)

    assert plan and plan[0]["action"] == "excel_live.write_range"
    assert plan[0]["params"]["start_cell"] == "B2"


def test_table_creation_with_headers_still_goes_to_the_table_slot():
    """표를 새로 만들라는 문장은 create_table 경로가 크기까지 챙겨야 한다."""
    plan = _build_quick_action_plan("금액, 장소, 날짜 헤더로 표 만들어줘", None)

    assert not plan or plan[0]["action"] != "excel_live.write_range"


def test_ratio_formula_keeps_both_functions():
    message = (
        'Dashboard 시트 G6에 =COUNTIF(Sales_Data!P2:P181,"배송완료")'
        "/COUNTA(Sales_Data!A2:A181) 수식 넣어줘"
    )
    formula = _extract_formula_from_text(message)
    assert formula == (
        '=COUNTIF(Sales_Data!P2:P181,"배송완료")/COUNTA(Sales_Data!A2:A181)'
    )
    plan = _build_quick_action_plan(message, None)
    assert plan and plan[0]["action"] == "excel_live.set_formula"
    assert plan[0]["params"]["formula_a1"] == formula


def test_named_excel_table_convert_keeps_the_name():
    plan = _build_quick_action_plan(
        "Sales_Data를 SalesTable 이름으로 엑셀 표 테이블로 만들어줘", None
    )
    assert plan and plan[0]["action"] == "excel_live.convert_to_excel_table"
    assert plan[0]["params"]["table_name"] == "SalesTable"
    assert _extract_excel_table_name("Inventory를 InventoryTable 이름으로 엑셀 표") == (
        "InventoryTable"
    )


def test_data_bar_and_color_scale_beat_write_and_fill():
    bar = _build_quick_action_plan("K2:K181에 데이터 막대 넣어줘", None)
    assert bar and bar[0]["action"] == "excel_live.apply_data_bar"
    scale = _build_quick_action_plan("O2:O181에 색조 조건부서식 적용해줘", None)
    assert scale and scale[0]["action"] == "excel_live.apply_color_scale"


def test_quoted_chart_title_beats_generic_sales_title():
    message = "Dashboard 시트 A12:D18로 '2026 월별 매출 및 매출이익' 선 그래프 만들어줘"
    assert _extract_quoted_chart_title(message) == "2026 월별 매출 및 매출이익"
    hints = _extract_operation_hints(message)
    assert hints["params"].get("title") == "2026 월별 매출 및 매출이익"
    assert hints["params"].get("chart_type") == "line"
