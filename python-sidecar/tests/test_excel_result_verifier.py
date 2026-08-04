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
