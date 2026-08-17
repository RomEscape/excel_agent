"""2026-08-17 전 카테고리 배터리(24케이스)가 찾은 구멍 8개의 회귀 테스트.

배터리는 실제 HTTP + 파일 검증으로 16/24 → 24/24가 됐다. 여기서는 각 구멍의
원인 지점을 단위로 고정한다. 파이프라인 전체는 배터리와
`test_rule_plan_survives_pipeline.py`가 지킨다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _looks_like_format_code
from office_claw_sidecar.services.excel_correction_context import (
    LastFormula,
    build_below_formula_plan,
)
from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based
from office_claw_sidecar.services.excel_macro_planner import looks_like_macro_request
from office_claw_sidecar.services.excel_param_binder import (
    formula_has_reversed_range,
    formula_refers_beyond_used_columns,
)

ENTRY = {"used_range": "A1:D9", "columns": [{"letter": "D", "header": "금액"}]}


class TestQuotativeParticleIsNotData:
    """ "A12에 합계 라고 입력해줘" → A12에 '합계 라고'가 들어갔다."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("A12에 합계 라고 입력해줘", "합계"),
            ("A12에 완료 이라고 적어줘", "완료"),
            ("A12에 합계 입력해줘", "합계"),
        ],
    )
    def test_the_particle_is_stripped(self, message, expected):
        step = parse_command_rule_based(message)
        assert step["params"]["values_2d"] == [[expected]]


class TestFormattingWordsAreNotCellValues:
    """ "금액에 천 단위 콤마 넣어줘"가 셀에 '금액에 천 단위 콤마'라는 텍스트로 들어갔다."""

    @pytest.mark.parametrize(
        "message",
        ["금액에 천 단위 콤마 넣어줘", "소수점 둘째 자리로 넣어줘", "테두리 넣어줘", "퍼센트로 넣어줘"],
    )
    def test_no_write_plan_is_made(self, message):
        step = parse_command_rule_based(message)
        assert step is None or step.get("action") != "excel_live.write_range", step

    def test_a_plain_value_still_writes(self):
        # 서식 어휘 차단이 정상 입력("777 입력해줘")까지 막으면 안 된다.
        step = parse_command_rule_based("777 입력해줘")
        assert step["action"] == "excel_live.write_range"
        assert step["params"]["values_2d"] == [[777]]


class TestFormatCodeSniffing:
    """플래너가 format_code에 말 그대로 "소수점 둘째 자리"를 넣었다."""

    @pytest.mark.parametrize("code", ["#,##0", "0.00", "0%", "yyyy-mm-dd", "General", "@ "])
    def test_real_codes_pass(self, code):
        assert _looks_like_format_code(code) is True

    @pytest.mark.parametrize("code", ["소수점 둘째 자리", "천 단위 콤마", "", "예쁘게"])
    def test_prose_fails(self, code):
        assert _looks_like_format_code(code) is False


class TestDegenerateFormulaDetection:
    """=AVERAGE(A2:A1)이 A1:A8에 적용돼 날짜 열이 통째로 덮였다."""

    def test_reversed_rows_are_caught(self):
        assert formula_has_reversed_range("=AVERAGE(A2:A1)") is True
        assert formula_has_reversed_range("=SUM(D2:D9)") is False

    def test_reversed_columns_are_caught(self):
        assert formula_has_reversed_range("=SUM(D1:A1)") is True

    def test_sheet_prefixed_ranges_are_ignored(self):
        assert formula_has_reversed_range("=SUM(매출!D9:D2)") is False


class TestOutOfDataColumnDetection:
    """=AVERAGE(E:E) — 데이터는 A~D뿐인데 근거 없는 열을 집었다."""

    def test_a_column_beyond_the_data_is_flagged(self):
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", ENTRY) is True
        assert formula_refers_beyond_used_columns("=SUM(F2:F9)", ENTRY) is True

    def test_columns_inside_the_data_are_fine(self):
        assert formula_refers_beyond_used_columns("=SUM(D:D)", ENTRY) is False
        assert formula_refers_beyond_used_columns("=SUM(D2:D9)", ENTRY) is False

    def test_unknown_used_range_is_never_flagged(self):
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", None) is False
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", {}) is False


class TestBelowCellFormulaContext:
    """ "F2에 합계" 다음의 "그 아래 칸에는 평균" — 플래너로 가면 날짜 열이 덮였다."""

    LAST = LastFormula(sheet_name="매출", cell="F2", formula="=SUM(D:D)", at_ts=0.0)

    def test_the_function_is_swapped_one_cell_below(self):
        plan = build_below_formula_plan("그 아래 칸에는 평균 넣어줘", self.LAST)
        assert plan[0]["params"]["range_ref"] == "F3"
        assert plan[0]["params"]["formula_a1"] == "=AVERAGE(D:D)"

    @pytest.mark.parametrize(
        ("message", "func"),
        [("아래 칸에 최댓값도", "MAX"), ("밑에 칸에 건수 세어줘", "COUNTA"), ("그 아래 셀에 합계", "SUM")],
    )
    def test_every_aggregate_word_works(self, message, func):
        plan = build_below_formula_plan(message, self.LAST)
        assert plan[0]["params"]["formula_a1"].startswith(f"={func}(")

    def test_no_below_mention_means_no_plan(self):
        assert build_below_formula_plan("평균 넣어줘", self.LAST) is None

    def test_no_remembered_formula_means_no_plan(self):
        assert build_below_formula_plan("그 아래 칸에 평균", None) is None

    def test_a_complex_formula_is_not_imitated(self):
        last = LastFormula(sheet_name="", cell="F2", formula="=SUM(A1:B2)+SUM(C1:C2)", at_ts=0.0)
        assert build_below_formula_plan("아래 칸에 평균", last) is None


class TestSingleGroupByIsNotAMacro:
    """ "지역별 금액 합계 집계표 만들어줘"가 16단계 매크로가 돼 산출물이 0이었다."""

    @pytest.mark.parametrize(
        "message", ["지역별 금액 합계 집계표 만들어줘", "월별 매출 요약표 만들어줘", "지역별 집계표 만들어줘"]
    )
    def test_it_stays_on_the_pivot_path(self, message):
        assert looks_like_macro_request(message) is False

    def test_a_dashboard_is_still_a_macro(self):
        assert looks_like_macro_request("매출 대시보드 만들어줘") is True
