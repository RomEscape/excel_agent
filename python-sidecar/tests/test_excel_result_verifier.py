from office_claw_sidecar.services.excel_result_verifier import verify_effect


class _Service:
    """정렬 결과를 파일에서 다시 읽는 상황을 흉내 내는 테스트 더블."""

    def __init__(self, rows, sheets=("매출",), sheet_data=None):
        self.rows = rows
        self.sheets = list(sheets)
        self.sheet_data = sheet_data or {}

    def read_range(self, workbook_id, sheet_name, range_ref):
        if sheet_name in self.sheet_data:
            return {"values": self.sheet_data[sheet_name], "address": range_ref}
        return {"values": self.rows, "address": range_ref}

    def list_sheets(self, workbook_id=None):
        return {"sheets": self.sheets, "count": len(self.sheets), "active_sheet": self.sheets[0]}

    def get_used_range_ref(self, workbook_id, sheet_name):
        return "A1:B3"


SORTED_ASC = [["월", "금액"], ["1월", 840], ["2월", 1000], ["3월", 1440]]
UNSORTED = [["월", "금액"], ["1월", 1000], ["2월", 840], ["3월", 1440]]


def _sort(rows, order="asc", key_column="금액"):
    return verify_effect(
        action="excel_live.sort_range",
        params={"range_ref": "A1:B4", "key_column": key_column, "order": order, "has_header": True},
        result={"address": "A1:B4", "sorted_rows": 3},
        service=_Service(rows),
        workbook_id="wb",
        sheet_name="매출",
    )


def test_sort_passes_when_key_column_is_monotonic():
    assert _sort(SORTED_ASC) == (True, "")


def test_sort_fails_when_rows_were_not_reordered():
    ok, detail = _sort(UNSORTED)
    assert ok is False
    assert "sort_not_applied" in detail


def test_sort_respects_descending_order():
    ok, _detail = _sort(list(reversed(SORTED_ASC[1:])) and [SORTED_ASC[0], *reversed(SORTED_ASC[1:])], "desc")
    assert ok is True


def test_sort_key_can_be_column_letter():
    assert _sort(SORTED_ASC, key_column="B") == (True, "")


# 기준 열이 수식이면 파일 엔진은 계산값이 아니라 `=G2*H2` 같은 **수식 문자열**을 읽는다.
# 그걸 문자열로 비교하면 어떤 정렬도 "안 됐다"가 되고, 제대로 끝난 정렬까지 롤백된다.
# 2026-08-16 실측: "매출 높은 순으로 정렬해줘"가 sort_not_applied로 되돌려졌다.
FORMULA_KEY = [["월", "금액"], ["1월", "=C2*D2"], ["2월", "=C3*D3"], ["3월", "=C4*D4"]]


def test_a_formula_key_column_is_not_declared_unsorted():
    assert _sort(FORMULA_KEY, "desc") == (True, "")


def test_a_formula_key_column_passes_in_ascending_order_too():
    assert _sort(FORMULA_KEY, "asc") == (True, "")


def test_plain_values_are_still_checked():
    # 수식 예외가 진짜 미정렬까지 통과시키면 안 된다.
    ok, detail = _sort(UNSORTED)
    assert ok is False
    assert "sort_not_applied" in detail


def test_filter_reporting_zero_matches_is_a_failure():
    ok, detail = verify_effect(
        action="excel_live.filter_rows",
        params={},
        result={"filtered_rows": 0},
        service=_Service([]),
        workbook_id="wb",
        sheet_name="매출",
    )
    assert ok is False
    assert "filter_no_match" in detail


def test_highlight_without_changed_cells_is_a_failure():
    ok, detail = verify_effect(
        action="excel_live.highlight_by_condition",
        params={},
        result={"changed_cells": 0},
        service=_Service([]),
        workbook_id="wb",
        sheet_name="매출",
    )
    assert ok is False
    assert "no_cells_changed" in detail


def test_pivot_requires_output_sheet_to_exist():
    ok, detail = verify_effect(
        action="excel_live.pivot_table",
        params={"output_sheet": "피벗1"},
        result={"created": True},
        service=_Service([], sheets=("매출",)),
        workbook_id="wb",
        sheet_name="매출",
    )
    assert ok is False
    assert "output_sheet_missing" in detail


def test_pivot_passes_when_output_sheet_has_data():
    service = _Service([], sheets=("매출", "피벗1"), sheet_data={"피벗1": [["월", "합계"], ["1월", 1000]]})
    assert verify_effect(
        action="excel_live.pivot_table",
        params={"output_sheet": "피벗1"},
        result={"created": True},
        service=service,
        workbook_id="wb",
        sheet_name="매출",
    ) == (True, "")


def test_verifier_passes_when_workbook_cannot_be_read():
    class _Broken(_Service):
        def read_range(self, workbook_id, sheet_name, range_ref):
            raise RuntimeError("boom")

    assert _sort_with(_Broken(SORTED_ASC)) == (True, "")


def _sort_with(service):
    return verify_effect(
        action="excel_live.sort_range",
        params={"range_ref": "A1:B4", "key_column": "금액", "order": "asc"},
        result={"address": "A1:B4"},
        service=service,
        workbook_id="wb",
        sheet_name="매출",
    )


# ── write_range 사후조건 ─────────────────────────────────────────────────
# written_cells는 "몇 칸을 건드렸다"까지만 말해 준다. 보호 시트나 병합 셀처럼
# 쓰기가 삼켜지는 경우에도 그 숫자는 그대로 올라오므로, 실제로 그 값이
# 들어갔는지는 파일에서 다시 읽어야 안다.


def _write(expected_2d, actual_2d, address="C1:C1"):
    return verify_effect(
        action="excel_live.write_range",
        params={"start_cell": address.split(":")[0], "values_2d": expected_2d},
        result={"address": address, "written_cells": 1},
        service=_Service(actual_2d),
        workbook_id="wb",
        sheet_name="매출",
    )


def test_write_passes_when_written_value_is_in_the_cell():
    assert _write([[120]], [[120]]) == (True, "")


def test_write_fails_when_cell_holds_a_different_value():
    """실행은 성공을 보고했지만 파일 값이 다르면 실패로 판정해야 한다."""
    ok, detail = _write([[120]], [[999]])
    assert ok is False
    assert "write_value_mismatch" in detail


def test_write_fails_when_cell_stayed_empty():
    ok, detail = _write([["비고"]], [[None]])
    assert ok is False
    assert "write_value_mismatch" in detail


def test_write_tolerates_numeric_type_change():
    """3.0을 쓰면 3으로 돌아온다. 표현 차이로 멀쩡한 작업을 되돌리면 안 된다."""
    assert _write([[3.0]], [[3]]) == (True, "")
    assert _write([["1200"]], [[1200]]) == (True, "")


def test_write_tolerates_blank_representations():
    assert _write([[""]], [[None]]) == (True, "")


def test_write_skips_formula_cells():
    """수식은 읽을 때 수식 문자열과 계산값 중 무엇이 오는지 엔진에 달렸다."""
    assert _write([["=SUM(A1:A9)"]], [[12918500]]) == (True, "")


def test_write_reports_the_offending_cell():
    ok, detail = _write([[1, 2, 999]], [[1, 2, 3]], address="A1:C1")
    assert ok is False
    assert "C1" in detail


def test_write_passes_when_workbook_cannot_be_read():
    class _Broken(_Service):
        def read_range(self, workbook_id, sheet_name, range_ref):
            raise RuntimeError("boom")

    assert verify_effect(
        action="excel_live.write_range",
        params={"start_cell": "C1", "values_2d": [[120]]},
        result={"address": "C1:C1", "written_cells": 1},
        service=_Broken([[999]]),
        workbook_id="wb",
        sheet_name="매출",
    ) == (True, "")


# ── clear_range 사후조건 ─────────────────────────────────────────────────


def _clear(remaining_2d):
    return verify_effect(
        action="excel_live.clear_range",
        params={"target_range": "C2:C9"},
        result={"address": "C2:C9", "cleared_cells": 8},
        service=_Service(remaining_2d),
        workbook_id="wb",
        sheet_name="매출",
    )


def test_clear_passes_when_range_is_empty():
    assert _clear([[None], [None], [""]]) == (True, "")


def test_clear_fails_when_values_remain():
    ok, detail = _clear([[None], ["김민수"], [None]])
    assert ok is False
    assert "clear_not_applied" in detail
    assert "김민수" in detail


class _CellService:
    """범위 문자열 → 값을 그대로 돌려주는 테스트 더블.

    `read_computed_range`는 계산값을, `read_range`는 기준 셀 조회를 맡는다.
    """

    def __init__(self, computed, cells, unresolved=()):
        self.computed = computed          # 대상 범위의 계산 결과 2차원
        self.cells = dict(cells)          # {"A6": "서울", ...} — 없으면 빈 칸
        self.unresolved = list(unresolved)

    def read_computed_range(self, workbook_id, sheet_name, range_ref):
        return {"values": self.computed, "address": range_ref, "unresolved_formulas": self.unresolved}

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [[self.cells.get(range_ref)]], "address": range_ref}

    def list_sheets(self, workbook_id=None):
        return {"sheets": ["Dashboard"], "count": 1, "active_sheet": "Dashboard"}


def _formula(service, formula, address="B6:B11"):
    return verify_effect(
        action="excel_live.set_formula",
        params={"range_ref": address, "formula_a1": formula},
        result={"formula_applied_cells": 6, "address": address},
        service=service,
        workbook_id="wb",
        sheet_name="Dashboard",
    )


ZEROS = [[0], [0], [0], [0], [0], [0]]
SUMIF = "=SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$J$2:$J$61)"


class TestConditionalAggregateResults:
    """기준 셀이 비어 SUMIF가 전부 0이 된 경우를 잡는다.

    2026-08-16 실측: 매크로가 "A6:A11에 지역 이름 입력"을 빠뜨린 채 B6:C11에 SUMIF를
    넣었고, 12칸이 전부 0인데 "19단계를 마쳤습니다"로 보고됐다.
    """

    def test_empty_criteria_with_all_zero_results_is_a_failure(self):
        ok, detail = _formula(_CellService(ZEROS, {}), SUMIF)
        assert ok is False
        assert "criteria_range_empty" in detail

    def test_the_relative_reference_is_expanded_down_the_target_range(self):
        # A6만 채워도 A7:A11이 비면 잡아야 한다 — 실측에서 A6에는 평균주문금액 '값'이 있었다.
        ok, detail = _formula(_CellService(ZEROS, {"A6": 10458741.67}), SUMIF)
        assert ok is False
        assert "A7" in detail

    def test_a_fully_populated_criteria_column_passes_even_when_every_result_is_zero(self):
        # 매출이 정말 0인 지역들 — 이걸 실패로 보면 정상 작업을 되돌린다.
        regions = ["서울", "경기", "충청", "영남", "호남", "강원"]
        cells = {f"A{r}": name for r, name in zip(range(6, 12), regions)}
        assert _formula(_CellService(ZEROS, cells), SUMIF) == (True, "")

    def test_a_nonzero_result_passes(self):
        nonzero = [[8480000], [11320000], [0], [0], [0], [0]]
        assert _formula(_CellService(nonzero, {}), SUMIF) == (True, "")

    def test_a_plain_sum_is_not_checked(self):
        # 조건 집계가 아니면 기준 셀 개념이 없다. 예전 동작 그대로 통과.
        assert _formula(_CellService(ZEROS, {}), "=SUM(Sales_Data!J2:J61)", "B3:B3") == (True, "")

    def test_unresolved_formulas_are_not_judged(self):
        # 계산값을 못 구했으면 판정하지 않는다 — 못 본 것을 실패로 단정하지 않는다.
        svc = _CellService(ZEROS, {}, unresolved=[(0, 0)])
        assert _formula(svc, SUMIF) == (True, "")

    def test_absolute_rows_are_not_expanded(self):
        # $A$6로 고정했으면 A6 한 칸만 기준이다. A6이 차 있으면 통과해야 한다.
        formula = "=SUMIF(Sales_Data!$C$2:$C$61,$A$6,Sales_Data!$J$2:$J$61)"
        assert _formula(_CellService(ZEROS, {"A6": "서울"}), formula) == (True, "")

    def test_a_service_without_computed_reads_is_skipped(self):
        class _Old:
            def read_range(self, *a, **k):
                return {"values": [[None]]}

        assert _formula(_Old(), SUMIF) == (True, "")

    def test_formula_not_applied_still_fails_first(self):
        out = verify_effect(
            action="excel_live.set_formula",
            params={"range_ref": "B6:B11", "formula_a1": SUMIF},
            result={"formula_applied_cells": 0},
            service=_CellService(ZEROS, {}),
            workbook_id="wb",
            sheet_name="Dashboard",
        )
        assert out[0] is False
        assert "formula_not_applied" in out[1]
