"""찾기 함수와 실무 함수를 평가기가 계산할 수 있는가.

2026-08-17 실측: 수식 38개를 넣어 보니 **쓰기는 38/38인데 계산은 23/38**이었다.
빠진 것 중 제일 아픈 게 찾기 함수 전부(VLOOKUP·HLOOKUP·XLOOKUP·INDEX·MATCH)였다.

수식은 파일에 들어가지만 이 평가기가 값을 못 내면:
  - 다이제스트에 값 대신 수식 문자열이 보인다 → 다음 턴 계획이 그걸 보고 판단한다
  - `verify_formula_result`의 사후 검증이 그 자리에서 눈을 감는다

실무 대시보드에 찾기 함수는 거의 항상 들어가므로, 그 순간 검증 체계가 멈춘 셈이었다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_formula_eval import (
    FormulaError,
    WorkbookEvaluator,
)

# A      B     C
# 지역   매출  수량
# 서울   120   3
# 경기   85    2
# 부산   143   5
GRID: dict[tuple[int, int], object] = {
    (1, 1): "지역", (1, 2): "매출", (1, 3): "수량",
    (2, 1): "서울", (2, 2): 120, (2, 3): 3,
    (3, 1): "경기", (3, 2): 85, (3, 3): 2,
    (4, 1): "부산", (4, 2): 143, (4, 3): 5,
}


@pytest.fixture
def ev() -> WorkbookEvaluator:
    def raw(_sheet: str, row: int, col: int):
        return GRID.get((row, col))

    return WorkbookEvaluator(raw, default_sheet="데이터")


class TestLookupFunctions:
    def test_vlookup_finds_an_exact_match(self, ev):
        assert ev.evaluate('=VLOOKUP("경기",A2:C4,2,FALSE)') == 85

    def test_vlookup_can_reach_the_third_column(self, ev):
        assert ev.evaluate('=VLOOKUP("부산",A2:C4,3,FALSE)') == 5

    def test_vlookup_raises_when_missing(self, ev):
        with pytest.raises(FormulaError):
            ev.evaluate('=VLOOKUP("제주",A2:C4,2,FALSE)')

    def test_hlookup_searches_the_first_row(self, ev):
        # 머리글 행에서 "매출"을 찾아 그 아래 값을 준다.
        assert ev.evaluate('=HLOOKUP("매출",A1:C4,2,FALSE)') == 120

    def test_match_returns_a_position(self, ev):
        assert ev.evaluate('=MATCH("부산",A2:A4,0)') == 3.0

    def test_index_reads_by_position(self, ev):
        assert ev.evaluate("=INDEX(B2:B4,2)") == 85

    def test_index_match_together(self, ev):
        # 실무에서 VLOOKUP보다 자주 쓰는 조합이다.
        assert ev.evaluate('=INDEX(B2:B4,MATCH("부산",A2:A4,0))') == 143

    def test_index_takes_row_and_column(self, ev):
        assert ev.evaluate("=INDEX(A2:C4,3,2)") == 143

    def test_index_out_of_range_raises(self, ev):
        with pytest.raises(FormulaError):
            ev.evaluate("=INDEX(B2:B4,9)")

    def test_xlookup_finds_a_value(self, ev):
        assert ev.evaluate('=XLOOKUP("경기",A2:A4,B2:B4)') == 85

    def test_xlookup_uses_the_not_found_argument(self, ev):
        assert ev.evaluate('=XLOOKUP("제주",A2:A4,B2:B4,0)') == 0

    def test_xlookup_raises_without_a_fallback(self, ev):
        with pytest.raises(FormulaError):
            ev.evaluate('=XLOOKUP("제주",A2:A4,B2:B4)')


class TestBusinessFunctions:
    def test_median(self, ev):
        assert ev.evaluate("=MEDIAN(B2:B4)") == 120

    def test_stdev_is_the_sample_form(self, ev):
        # 120, 85, 143 → 평균 116, 편차제곱합 1706, 표본분산 853, √853 = 29.21
        assert round(ev.evaluate("=STDEV(B2:B4)"), 2) == 29.21

    def test_stdev_population_form_differs(self, ev):
        # 모분산은 1706/3이라 표본분산보다 작다.
        assert round(ev.evaluate("=STDEV.P(B2:B4)"), 2) == 23.85

    def test_sumproduct(self, ev):
        # 120*3 + 85*2 + 143*5 = 1245
        assert ev.evaluate("=SUMPRODUCT(B2:B4,C2:C4)") == 1245

    def test_subtotal_maps_to_the_inner_function(self, ev):
        assert ev.evaluate("=SUBTOTAL(9,B2:B4)") == 348
        assert ev.evaluate("=SUBTOTAL(1,B2:B4)") == 116

    def test_ifs_picks_the_first_true_branch(self, ev):
        assert ev.evaluate('=IFS(B2>200,"상",B2>100,"중",TRUE,"하")') == "중"

    def test_ifs_raises_when_nothing_matches(self, ev):
        with pytest.raises(FormulaError):
            ev.evaluate('=IFS(B2>500,"상",B2>400,"중")')

    def test_textjoin_skips_blanks(self, ev):
        assert ev.evaluate('=TEXTJOIN(",",TRUE,A2:A4)') == "서울,경기,부산"

    def test_substitute(self, ev):
        assert ev.evaluate('=SUBSTITUTE(A2,"서","S")') == "S울"

    def test_rank_is_descending_by_default(self, ev):
        # 143 > 120 > 85 이므로 120은 2위다.
        assert ev.evaluate("=RANK(B2,B2:B4)") == 2.0

    def test_rank_can_be_ascending(self, ev):
        assert ev.evaluate("=RANK(B2,B2:B4,1)") == 2.0


class TestUnsupportedStillFailsLoudly:
    def test_an_unknown_function_raises(self, ev):
        # 조용히 0을 돌려주면 틀린 값이 그대로 시트에 남는다.
        with pytest.raises(FormulaError):
            ev.evaluate("=BESSELJ(B2,1)")
