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
