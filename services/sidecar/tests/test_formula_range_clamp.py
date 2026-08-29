"""데이터 끝을 넘는 수식 범위를 잘라낸다.

2026-08-17 실측: 사용 범위가 A1:D4인 시트에 `=SUM(D2:D181)`이 들어갔다. 181은 이
통합문서에 없는 숫자다 — 학습셋에 그대로 있는 리터럴이다(CLAUDE.md §3.4의
`L2:L181` 81/1000과 같은 부류).

합계 값 자체는 맞게 나온다. 그래서 검증기도 통과했다. 하지만 사용자가 수식을 열어
보면 틀린 표로 보이고, 행을 추가하면 조용히 다른 결과가 된다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import PlanStep
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
    clamp_formula_to_used_range,
)

ENTRY = {
    "name": "매출",
    "used_range": "A1:D4",
    "columns": [
        {"letter": "A", "header": "날짜"},
        {"letter": "B", "header": "지역"},
        {"letter": "C", "header": "담당자"},
        {"letter": "D", "header": "금액"},
    ],
}


class TestClamping:
    @pytest.mark.parametrize(
        ("formula", "expected"),
        [
            ("=SUM(D2:D181)", "=SUM(D2:D4)"),
            ("=AVERAGE(D2:D100)", "=AVERAGE(D2:D4)"),
            ("=COUNTA(B2:B999)", "=COUNTA(B2:B4)"),
            ("=SUM(D2:D181)+SUM(C2:C181)", "=SUM(D2:D4)+SUM(C2:C4)"),
            ("=SUM($D$2:$D$181)", "=SUM($D$2:$D$4)"),
        ],
    )
    def test_it_cuts_at_the_last_data_row(self, formula, expected):
        assert clamp_formula_to_used_range(formula, ENTRY) == expected


class TestItLeavesTheRestAlone:
    @pytest.mark.parametrize(
        "formula",
        [
            "=SUM(D2:D4)",  # 이미 딱 맞다
            "=SUM(D2:D3)",  # 더 좁은 범위는 사용자가 좁힌 것일 수 있다
            "=D2*1.1",  # 범위가 아니다
            "=SUM(매출!D2:D181)",  # 다른 시트 이야기다
            "=SUM(D:D)",  # 열 전체는 행 번호가 없다
            "=TODAY()",
        ],
    )
    def test_untouched(self, formula):
        assert clamp_formula_to_used_range(formula, ENTRY) == formula

    def test_a_range_entirely_below_the_data_is_left_alone(self):
        # 통째로 데이터 밖이면 다른 문제다. 잘라 붙이면 조용히 엉뚱한 칸을 가리킨다.
        assert clamp_formula_to_used_range("=SUM(D10:D20)", ENTRY) == "=SUM(D10:D20)"

    @pytest.mark.parametrize(
        "formula",
        [
            "=VLOOKUP(A2,$F$2:$H$200,2,FALSE)",
            "=SUM(F2:F200)",
            "=SUMPRODUCT(C2:C181,F2:F181)",  # 한쪽이라도 없는 열이면 그 범위는 그대로
        ],
    )
    def test_a_range_in_columns_that_do_not_exist_is_left_alone(self, formula):
        """처음엔 행만 보고 잘랐다가 회귀를 냈다.

        F~H는 A1:D4 시트에 없는 열이다. 즉 참조표 전체가 플래너의 추측이고,
        검증기가 그걸 거부해 되묻고 있었다. 행만 보고 다듬으니 그럴듯해져서 검증을
        통과했고, 아무도 말하지 않은 F~H열로 실행됐다. 다듬는 일이 "이건 추측이다"
        라는 신호를 지워 버린 것이다.
        """
        clipped = clamp_formula_to_used_range(formula, ENTRY)
        assert "$H$4" not in clipped and "F2:F4" not in clipped

    def test_the_column_inside_the_data_is_still_clipped(self):
        # 같은 수식이라도 데이터 안의 열은 잘린다.
        assert clamp_formula_to_used_range("=SUMPRODUCT(C2:C181,D2:D181)", ENTRY) == (
            "=SUMPRODUCT(C2:C4,D2:D4)"
        )

    @pytest.mark.parametrize("entry", [None, {}, {"used_range": ""}, {"used_range": "A:D"}])
    def test_an_unknown_used_range_means_no_clamp(self, entry):
        assert clamp_formula_to_used_range("=SUM(D2:D181)", entry) == "=SUM(D2:D181)"

    def test_plain_text_is_not_a_formula(self):
        assert clamp_formula_to_used_range("SUM(D2:D181)", ENTRY) == "SUM(D2:D181)"


class TestItRunsInsideTheBinder:
    def test_the_bound_step_carries_the_clipped_formula(self):
        digest = {"active_sheet": "매출", "sheets": [ENTRY]}
        step = PlanStep(
            action="excel_live.set_formula",
            params={"range_ref": "F2", "formula_a1": "=SUM(D2:D181)"},
            reason="",
        )
        bound, notes = bind_plan_steps(
            [step], digest=digest, message="그 아래 칸에 금액 합계 수식 넣어줘", sheet_name=None
        )
        assert bound[0].params["formula_a1"] == "=SUM(D2:D4)"
        assert any("formula_a1==SUM(D2:D4)" in c for n in notes for c in n.get("changes", []))
