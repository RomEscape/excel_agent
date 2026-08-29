"""상징 범위(`__USED_RANGE__` 등)가 A1 한 칸으로 떨어지지 않는지.

2026-08-20 실측: `_range_bounds`가 파싱 실패를 'A1'로 대체해서, 범위를 받는 **모든**
연산이 조용히 한 칸짜리 no-op이 됐다. `highlight_by_condition`이
`scanned_cells=1 / matched_cells=0`으로 0건을 칠하고 **성공을 보고**했다.
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "지연경고.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "지연경고"
    ws.append(["운송장", "구간", "지연시간", "상태"])
    for row in (
        ["T1", "서울", 2, "대기"],
        ["T2", "부산", 1, "완료"],
        ["T3", "대구", 3, "대기"],
        ["T4", "광주", 0, "완료"],
    ):
        ws.append(row)
    wb.save(path)
    service = FileExcelLiveService()
    service.select_workbook(str(path))
    service.select_sheet(None, "지연경고")
    return service


@pytest.mark.parametrize("symbolic", ["__USED_RANGE__", "__ACTIVE_SELECTION__", "__TABLE_REGION__"])
def test_symbolic_ranges_cover_the_used_area(book, symbolic: str) -> None:
    result = book.highlight_by_condition(None, "지연경고", symbolic, "==", 0, "#FFC0CB", None, "대기")
    assert result["scanned_cells"] == 20, result
    assert result["matched_cells"] == 2, result


def test_an_explicit_range_is_unchanged(book) -> None:
    result = book.highlight_by_condition(None, "지연경고", "D1:D5", "==", 0, "#FFC0CB", None, "대기")
    assert (result["scanned_cells"], result["matched_cells"]) == (5, 2)


def test_a_row_range_still_fills_from_the_used_columns(book) -> None:
    """"1:1" 같은 행 전체 범위는 예전 경로 그대로여야 한다."""
    result = book.fill_range(None, "지연경고", "1:1", "#002060")
    assert result["changed_cells"] == 4, result
    assert result["address"] == "A1:D1", result
