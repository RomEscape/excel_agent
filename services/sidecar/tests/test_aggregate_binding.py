"""말로 시킨 집계를 수식으로 만든다.

2026-08-17: "가장 큰 매출 값 넣어줘"가 셀에 '가장 큰 매출'이라는 텍스트를 남겼다.
안전망을 달아 되묻게는 했지만, 되묻는 것보다 만들어 주는 게 낫다.

빠른 규칙에서는 못 한다 — 다이제스트를 못 봐서 "매출"이 몇 번 열인지 모른다.
그래서 바인더에서 한다. 바인더는 머리글과 사용 범위를 안다.

조건이 붙은 집계("서울 지역만")는 **일부러 만들지 않는다.** 기준 열과 값이 더
필요한데 잘못 짚으면 엉뚱한 숫자가 조용히 들어간다 — 그럴 땐 되묻는 편이 낫다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import PlanStep
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
    build_aggregate_formula,
)

ENTRY = {
    "name": "주문",
    "used_range": "A1:D6",
    "columns": [
        {"letter": "A", "header": "지역"},
        {"letter": "B", "header": "매출"},
        {"letter": "C", "header": "수량"},
    ],
}
DIGEST = {"active_sheet": "주문", "sheets": [ENTRY]}


def _formula(message: str) -> str:
    return build_aggregate_formula(message, entry=ENTRY, digest=DIGEST)


class TestBuildingTheFormula:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("F7에 가장 큰 매출 값 넣어줘", "=MAX(B2:B6)"),
            ("F7에 매출 최댓값 넣어줘", "=MAX(B2:B6)"),
            ("F7에 가장 작은 매출 값 넣어줘", "=MIN(B2:B6)"),
            ("F6에 매출 평균 구하는 수식 넣어줘", "=AVERAGE(B2:B6)"),
            ("F2에 매출을 다 더한 값 넣어줘", "=SUM(B2:B6)"),
            ("F2에 매출 합계 넣어줘", "=SUM(B2:B6)"),
            ("F3에 수량 개수 세는 수식 넣어줘", "=COUNTA(C2:C6)"),
        ],
    )
    def test_it_resolves_the_column_from_the_header(self, message, expected):
        assert _formula(message) == expected

    def test_the_range_follows_the_used_range(self):
        # 사용 범위가 A1:D6이므로 데이터는 2~6행이다.
        assert _formula("매출 합계 넣어줘").endswith("B2:B6)")


class TestItRefusesWhenUnsure:
    def test_a_conditional_aggregate_is_left_alone(self):
        # "서울 지역만"은 기준 열과 값이 더 필요하다. 추측하면 엉뚱한 숫자가 들어간다.
        assert _formula("F2에 서울 지역 매출만 더한 값 넣어줘") == ""

    @pytest.mark.parametrize(
        "message",
        ["F2에 지역별 매출 더한 값", "F2에 매출 100 이상만 더한 값", "F2에 매출이 목표 넘는 것만 더해"],
    )
    def test_other_conditional_phrasings_are_left_alone(self, message):
        assert _formula(message) == ""

    def test_it_refuses_when_no_column_is_named(self):
        # "가장 큰 값" — 어느 열인지 모른다.
        assert _formula("F9에 가장 큰 값 넣어줘") == ""

    def test_it_refuses_when_two_columns_are_named(self):
        # 열을 하나로 못 좁히면 추측하지 않는다.
        assert _formula("F9에 매출과 수량 중 가장 큰 값") == ""

    def test_it_refuses_without_an_aggregate_word(self):
        assert _formula("F9에 매출 넣어줘") == ""

    def test_no_entry_means_no_formula(self):
        assert build_aggregate_formula("가장 큰 매출 값", entry=None, digest=DIGEST) == ""


class TestTheStepBecomesAFormula:
    """바인더가 write_range를 set_formula로 바꿔야 실제로 값이 계산된다."""

    def _bind(self, message: str, cell: str, value: str):
        step = PlanStep(
            action="excel_live.write_range",
            params={"start_cell": cell, "values_2d": [[value]]},
            reason="",
        )
        return bind_plan_steps([step], digest=DIGEST, message=message, sheet_name=None)

    def test_an_echoed_aggregate_becomes_a_formula(self):
        bound, notes = self._bind("주문 시트 F7에 가장 큰 매출 값 넣어줘", "F7", "가장 큰 매출")
        assert bound[0].action == "excel_live.set_formula"
        assert bound[0].params == {"range_ref": "F7", "formula_a1": "=MAX(B2:B6)"}
        # 만들어 줬으므로 되물을 이유가 없다.
        assert [n for n in notes if n.get("status") == "unresolved"] == []

    def test_a_conditional_aggregate_still_asks(self):
        bound, notes = self._bind(
            "주문 시트 F2에 서울 지역 매출만 더한 값 넣어줘", "F2", "서울 지역 매출만 더한"
        )
        assert bound[0].action == "excel_live.write_range"
        assert [n for n in notes if n.get("status") == "unresolved"]

    def test_a_plain_header_write_is_untouched(self):
        bound, notes = self._bind("주문 시트 A1에 총매출 입력", "A1", "총매출")
        assert bound[0].action == "excel_live.write_range"
        assert bound[0].params["values_2d"] == [["총매출"]]
        assert [n for n in notes if n.get("status") == "unresolved"] == []

    def test_a_range_start_cell_collapses_to_one_cell(self):
        # 집계 결과는 한 칸이다.
        bound, _ = self._bind("주문 시트 F7:G9에 가장 큰 매출 값 넣어줘", "F7:G9", "가장 큰 매출")
        assert bound[0].params["range_ref"] == "F7"
