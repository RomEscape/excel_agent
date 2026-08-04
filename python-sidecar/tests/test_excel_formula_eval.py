"""수식 계산기 테스트.

실제 데모 워크북에 들어 있는 수식 모양을 그대로 가져와 검증한다.
"""

from datetime import date, datetime

import pytest

from office_claw_sidecar.services.excel_formula_eval import (
    FormulaError,
    WorkbookEvaluator,
    excel_serial,
)


def _evaluator(cells: dict[str, dict[tuple[int, int], object]], **kwargs) -> WorkbookEvaluator:
    def raw(sheet: str, row: int, col: int):
        return cells.get(sheet, {}).get((row, col))

    return WorkbookEvaluator(raw, default_sheet=next(iter(cells)), **kwargs)


class TestArithmetic:
    """데모 워크북 703개 수식 중 418개가 함수 없는 사칙연산이다."""

    def test_sales_formula(self):
        # =I2*J2*(1-K2) — 수량 x 단가 x (1 - 할인율)
        cells = {"S": {(2, 9): 5, (2, 10): 1_680_000, (2, 11): 0.1, (2, 12): "=I2*J2*(1-K2)"}}
        assert _evaluator(cells).value("S", 2, 12) == pytest.approx(7_560_000)

    def test_gross_profit_references_another_formula(self):
        # =L2-(I2*M2) — L2 자체가 수식이라 연쇄 계산이 필요하다
        cells = {
            "S": {
                (2, 9): 5,
                (2, 10): 1_000_000,
                (2, 11): 0,
                (2, 12): "=I2*J2*(1-K2)",
                (2, 13): 700_000,
                (2, 14): "=L2-(I2*M2)",
            }
        }
        assert _evaluator(cells).value("S", 2, 14) == pytest.approx(1_500_000)

    def test_operator_precedence_and_parens(self):
        cells = {"S": {(1, 1): "=2+3*4", (1, 2): "=(2+3)*4", (1, 3): "=2^3^2", (1, 4): "=-3+10"}}
        ev = _evaluator(cells)
        assert ev.value("S", 1, 1) == 14
        assert ev.value("S", 1, 2) == 20
        assert ev.value("S", 1, 3) == 512  # 오른쪽 결합
        assert ev.value("S", 1, 4) == 7

    def test_percent_literal(self):
        cells = {"S": {(1, 1): "=200*15%"}}
        assert _evaluator(cells).value("S", 1, 1) == pytest.approx(30)


class TestConditionals:
    def test_iferror_swallows_division_by_zero(self):
        # =IFERROR(N2/L2,0) — 이익률. 매출이 0이면 0을 준다.
        cells = {"S": {(2, 12): 0, (2, 14): 100, (2, 15): "=IFERROR(N2/L2,0)"}}
        assert _evaluator(cells).value("S", 2, 15) == 0

    def test_iferror_passes_through_good_value(self):
        cells = {"S": {(2, 12): 200, (2, 14): 50, (2, 15): "=IFERROR(N2/L2,0)"}}
        assert _evaluator(cells).value("S", 2, 15) == pytest.approx(0.25)

    def test_nested_if_reorder_status(self):
        # =IF(E2<=F2,"발주필요",IF(E2<=F2*1.5,"주의","정상"))
        formula = '=IF(E2<=F2,"발주필요",IF(E2<=F2*1.5,"주의","정상"))'
        for stock, expected in [(5, "발주필요"), (12, "주의"), (100, "정상")]:
            cells = {"I": {(2, 5): stock, (2, 6): 10, (2, 8): formula}}
            assert _evaluator(cells).value("I", 2, 8) == expected

    def test_if_with_and(self):
        # =IF(AND(J2<=7,I2<0.8),"주의","정상")
        formula = '=IF(AND(J2<=7,I2<0.8),"주의","정상")'
        cells = {"P": {(2, 9): 0.5, (2, 10): 3, (2, 11): formula}}
        assert _evaluator(cells).value("P", 2, 11) == "주의"
        cells = {"P": {(2, 9): 0.9, (2, 10): 3, (2, 11): formula}}
        assert _evaluator(cells).value("P", 2, 11) == "정상"

    def test_max_floor_at_zero(self):
        # =MAX(0,F2*2-E2) — 권장 발주수량
        cells = {"I": {(2, 5): 100, (2, 6): 10, (2, 9): "=MAX(0,F2*2-E2)"}}
        assert _evaluator(cells).value("I", 2, 9) == 0
        cells = {"I": {(2, 5): 5, (2, 6): 10, (2, 9): "=MAX(0,F2*2-E2)"}}
        assert _evaluator(cells).value("I", 2, 9) == 15


class TestAggregates:
    def test_sum_over_range(self):
        cells = {"S": {(r, 12): r * 100 for r in range(2, 6)}}
        cells["S"][(1, 1)] = "=SUM(L2:L5)"
        assert _evaluator(cells).value("S", 1, 1) == 1400

    def test_sum_across_sheets(self):
        cells = {
            "Dashboard": {(6, 1): "=SUM(Sales_Data!L2:L4)"},
            "Sales_Data": {(2, 12): 10, (3, 12): 20, (4, 12): 30},
        }
        assert _evaluator(cells).value("Dashboard", 6, 1) == 60

    def test_sum_of_formula_cells(self):
        """집계 대상이 다시 수식인 경우 — 대시보드가 실제로 이 모양이다."""
        cells = {
            "Sales_Data": {
                (2, 9): 2, (2, 10): 100, (2, 11): 0, (2, 12): "=I2*J2*(1-K2)",
                (3, 9): 3, (3, 10): 100, (3, 11): 0, (3, 12): "=I3*J3*(1-K3)",
            },
            "Dashboard": {(6, 1): "=SUM(Sales_Data!L2:L3)"},
        }
        assert _evaluator(cells).value("Dashboard", 6, 1) == 500

    def test_sumif_with_text_criteria(self):
        # =SUMIF(Sales_Data!$D$2:$D$4,A23,Sales_Data!$L$2:$L$4)
        cells = {
            "Dashboard": {(23, 1): "경기", (23, 2): "=SUMIF(Sales_Data!$D$2:$D$4,A23,Sales_Data!$L$2:$L$4)"},
            "Sales_Data": {
                (2, 4): "경기", (2, 12): 100,
                (3, 4): "서울", (3, 12): 200,
                (4, 4): "경기", (4, 12): 300,
            },
        }
        assert _evaluator(cells).value("Dashboard", 23, 2) == 400

    def test_countif_with_comparison_criteria(self):
        cells = {
            "S": {(1, 1): '=COUNTIF(B1:B4,">=100")', (1, 2): 50, (2, 2): 100, (3, 2): 150, (4, 2): 20}
        }
        assert _evaluator(cells).value("S", 1, 1) == 2

    def test_countifs_with_date_bounds(self):
        # ">="&DATE(...) 패턴. 날짜는 결합될 때 일련번호가 된다.
        cells = {
            "S": {
                (1, 1): '=COUNTIFS(B1:B3,">="&DATE(2026,1,1),B1:B3,"<"&DATE(2026,2,1))',
                (1, 2): datetime(2025, 12, 31),
                (2, 2): datetime(2026, 1, 15),
                (3, 2): datetime(2026, 2, 5),
            }
        }
        assert _evaluator(cells).value("S", 1, 1) == 1

    def test_ratio_of_two_counts(self):
        # =COUNTIF(P2:P4,"배송완료")/COUNTA(A2:A4)
        cells = {
            "S": {
                (1, 1): '=COUNTIF(P2:P4,"배송완료")/COUNTA(A2:A4)',
                (2, 16): "배송완료", (3, 16): "취소", (4, 16): "배송완료",
                (2, 1): "a", (3, 1): "b", (4, 1): "c",
            }
        }
        assert _evaluator(cells).value("S", 1, 1) == pytest.approx(2 / 3)


class TestWholeColumnRefs:
    """`=SUM(L:L)` 같은 열 전체 참조. 실무 파일에 흔하다."""

    def _bounded(self, cells):
        def raw(sheet, row, col):
            return cells.get(sheet, {}).get((row, col))

        return WorkbookEvaluator(
            raw,
            default_sheet=next(iter(cells)),
            sheet_bounds=lambda _sheet: (4, 12),
        )

    def test_sum_whole_column(self):
        cells = {"S": {(1, 1): "=SUM(L:L)", (2, 12): 10, (3, 12): 20, (4, 12): 30}}
        assert self._bounded(cells).value("S", 1, 1) == 60

    def test_sum_whole_column_absolute_and_cross_sheet(self):
        cells = {
            "Dashboard": {(1, 1): "=SUM(Sales_Data!$L:$L)"},
            "Sales_Data": {(2, 12): 5, (3, 12): 7},
        }
        assert self._bounded(cells).value("Dashboard", 1, 1) == 12

    def test_whole_column_without_bounds_is_reported(self):
        cells = {"S": {(1, 1): "=SUM(L:L)"}}

        def raw(sheet, row, col):
            return cells.get(sheet, {}).get((row, col))

        ev = WorkbookEvaluator(raw, default_sheet="S")
        with pytest.raises(FormulaError):
            ev.value("S", 1, 1)


class TestDates:
    def test_days_remaining_uses_today(self):
        # =F2-TODAY()
        cells = {"P": {(2, 6): datetime(2026, 1, 20), (2, 10): "=F2-TODAY()"}}
        ev = _evaluator(cells, today=date(2026, 1, 10))
        assert ev.value("P", 2, 10) == 10

    def test_date_and_edate(self):
        cells = {"S": {(1, 1): "=EDATE(DATE(2026,1,31),1)"}}
        assert _evaluator(cells).value("S", 1, 1) == excel_serial(datetime(2026, 2, 28))

    def test_month_from_text_parts(self):
        # =DATE(VALUE(LEFT(A12,4)),VALUE(RIGHT(A12,2)),1)
        cells = {"S": {(12, 1): "2026-03", (1, 1): "=DATE(VALUE(LEFT(A12,4)),VALUE(RIGHT(A12,2)),1)"}}
        assert _evaluator(cells).value("S", 1, 1) == excel_serial(datetime(2026, 3, 1))


class TestFailureModes:
    """계산 못 한 걸 0으로 둔갑시키지 않는지가 핵심이다."""

    def test_unsupported_function_raises(self):
        cells = {"S": {(1, 1): "=XLOOKUP(D1,E1:E9,F1:F9)"}}
        with pytest.raises(FormulaError) as exc:
            _evaluator(cells).value("S", 1, 1)
        assert exc.value.code == "#NAME?"

    def test_self_reference_is_circular(self):
        cells = {"S": {(1, 1): "=A1+1"}}
        with pytest.raises(FormulaError) as exc:
            _evaluator(cells).value("S", 1, 1)
        assert exc.value.code == "#REF!"

    def test_division_by_zero_raises(self):
        cells = {"S": {(1, 1): "=10/0"}}
        with pytest.raises(FormulaError) as exc:
            _evaluator(cells).value("S", 1, 1)
        assert exc.value.code == "#DIV/0!"

    def test_circular_reference_raises(self):
        cells = {"S": {(1, 1): "=B1+1", (1, 2): "=A1+1"}}
        with pytest.raises(FormulaError) as exc:
            _evaluator(cells).value("S", 1, 1)
        assert exc.value.code == "#REF!"

    def test_text_in_arithmetic_raises(self):
        cells = {"S": {(1, 1): "=A2*2", (2, 1): "안녕"}}
        with pytest.raises(FormulaError):
            _evaluator(cells).value("S", 1, 1)

    def test_cached_value_wins_over_recompute(self):
        """Excel이 남긴 캐시가 있으면 다시 계산하지 않는다."""
        cells = {"S": {(1, 1): "=1+1"}}

        def raw(sheet, row, col):
            return cells[sheet].get((row, col))

        def cached(sheet, row, col):
            return 999 if (row, col) == (1, 1) else None

        ev = WorkbookEvaluator(raw, cached_lookup=cached, default_sheet="S")
        assert ev.value("S", 1, 1) == 999

    def test_blank_cells_count_as_zero(self):
        cells = {"S": {(1, 1): "=A5+10"}}
        assert _evaluator(cells).value("S", 1, 1) == 10
