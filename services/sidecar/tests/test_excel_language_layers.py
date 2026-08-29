"""머리글 사전·한국어 수량·이름 수식 — 자연어를 시트 좌표로 잇는 3개 모듈 테스트.

실무 파일의 머리글은 영문인데 사용자는 한국어로 말한다. 이 경계에서 생긴 오작동
(엉뚱한 열 집계, "10만 원 미만"을 10으로 읽기, 계산식 되묻기)을 회귀로 고정한다.
"""

from office_claw_sidecar.services.excel_formula_builder import build_formula, parse_named_formula
from office_claw_sidecar.services.excel_header_lexicon import find_header_mentions, resolve_header
from office_claw_sidecar.services.korean_number import parse_amount, parse_condition

SALES_HEADERS = [
    "Order_ID",
    "Order_Date",
    "Region",
    "Qty",
    "Unit_Price",
    "Discount",
    "Sales",
    "Gross_Profit",
    "Profit_Margin",
    "Salesperson",
]


# ── 머리글 사전 ────────────────────────────────────────────────────────────


def test_resolve_header_maps_korean_concept_to_english_header():
    assert resolve_header("매출", SALES_HEADERS) == "Sales"
    assert resolve_header("매출이익", SALES_HEADERS) == "Gross_Profit"
    assert resolve_header("지역", SALES_HEADERS) == "Region"
    assert resolve_header("영업담당자", SALES_HEADERS) == "Salesperson"


def test_resolve_header_returns_none_for_unknown_term():
    assert resolve_header("배송비", SALES_HEADERS) is None


def test_longer_concept_wins_at_same_position():
    # "매출이익"이 "매출"에 먹히면 이익 열 대신 매출 열을 집계하게 된다.
    hits = find_header_mentions("매출이익 나누기 매출", SALES_HEADERS)
    assert [hit["header"] for hit in hits] == ["Gross_Profit", "Sales"]


def test_same_header_takes_earliest_mention():
    # "Region_Chart"는 시트 이름이다. 기준 열은 앞의 '지역별'이어야 한다.
    hits = find_header_mentions("지역별 매출 합계를 Region_Chart 시트에", SALES_HEADERS)
    assert hits[0]["header"] == "Region"
    assert hits[0]["start"] == 0


# ── 한국어 수량 ────────────────────────────────────────────────────────────


def test_parse_amount_expands_korean_scale():
    assert parse_amount("10만 원") == 100000.0
    assert parse_amount("1.5억") == 150000000.0
    assert parse_amount("1,200건") == 1200.0


def test_parse_condition_reads_amount_with_unit_between():
    assert parse_condition("매출이 10만 원 미만이면") == ("<", 100000.0, False)
    assert parse_condition("진행률이 80% 미만") == ("<", 80.0, True)
    assert parse_condition("재고가 20 이상") == (">=", 20.0, False)


def test_parse_condition_handles_not_equal_phrase():
    assert parse_condition("G열 3 같지 않음") == ("!=", 3.0, False)


def test_parse_condition_reads_colloquial_negation():
    # 실제 사용자는 "미만"보다 "안 되는"을 훨씬 자주 쓴다. 이걸 놓치면 조건이
    # 통째로 사라진 채 범위 전체가 칠해진다.
    assert parse_condition("매출 100만도 안 되는 건") == ("<", 1000000.0, False)
    assert parse_condition("진행률 80% 안 되는 일감") == ("<", 80.0, True)
    assert parse_condition("재고가 20개에 못 미치는 제품") == ("<", 20.0, False)
    assert parse_condition("매출 100만 넘지 않는 건") == ("<=", 1000000.0, False)


def test_parse_condition_ignores_negation_without_number():
    assert parse_condition("이거 왜 안 되는 거야") is None


def test_parse_condition_returns_none_without_comparison():
    assert parse_condition("매출을 정렬해줘") is None


# ── 이름으로 표현된 수식 ────────────────────────────────────────────────────


def test_parse_named_formula_division():
    parsed = parse_named_formula("이익률 열에 매출이익 나누기 매출 수식을 넣어줘", SALES_HEADERS)
    assert parsed is not None
    assert parsed.target == "Profit_Margin"
    assert parsed.operands == ["Gross_Profit", "Sales"]
    assert parsed.operators == ["/"]


def test_parse_named_formula_postfix_subtraction_with_scale():
    headers = ["SKU", "Current_Stock", "Reorder_Point", "Recommended_Order_Qty"]
    parsed = parse_named_formula(
        "권장 발주수량 열에 재주문점 두 배에서 현재고를 뺀 값을 계산하는 수식을 넣어줘", headers
    )
    assert parsed is not None
    assert parsed.target == "Recommended_Order_Qty"
    assert parsed.operands == ["Reorder_Point", "Current_Stock"]
    assert parsed.operators == ["-"]
    assert parsed.scales == {0: 2.0}


def test_parse_named_formula_requires_operator():
    # 연산어가 없으면 추측하지 말고 None을 돌려 되묻게 한다.
    assert parse_named_formula("이익률 열에 매출이익 매출 넣어줘", SALES_HEADERS) is None


def test_build_formula_guards_division_by_zero():
    parsed = parse_named_formula("이익률 열에 매출이익 나누기 매출 수식을 넣어줘", SALES_HEADERS)
    assert build_formula(parsed, ["N", "L"], 2) == '=IF(L2=0,"",N2/L2)'


def test_build_formula_applies_scale():
    headers = ["SKU", "Current_Stock", "Reorder_Point", "Recommended_Order_Qty"]
    parsed = parse_named_formula(
        "권장 발주수량 열에 재주문점 두 배에서 현재고를 뺀 값을 넣어줘", headers
    )
    assert build_formula(parsed, ["F", "E"], 2) == "=(F2*2)-E2"
