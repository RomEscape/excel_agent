"""지목한 통합문서의 시트를 써야 한다 — 다른 통합문서로 새면 안 된다.

2026-08-17 실측: 워크스페이스 밖 경로로 통합문서를 지목했더니 `list_workbooks()`
목록에 없어서 `rows[0]`로 폴백했고, **전혀 다른 통합문서의 활성 시트**("추이")를
돌려줬다. 그 시트가 대상 파일에 없어 WorksheetNotFoundError로 죽었는데, 두
통합문서에 같은 이름 시트가 있었다면 **조용히 엉뚱한 시트에 썼을 것이다.**
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _resolve_sheet_name
from office_claw_sidecar.services.excel_live_service import ExcelConnectionError


class _Service:
    def __init__(self, rows, sheets_by_wb=None, raises=False):
        self._rows = rows
        self._sheets = sheets_by_wb or {}
        self._raises = raises
        self.asked: list[str] = []

    def list_workbooks(self):
        return self._rows

    def list_sheets(self, workbook_id):
        self.asked.append(workbook_id)
        if self._raises:
            raise RuntimeError("열 수 없음")
        return self._sheets[workbook_id]


ROW_A = {"workbook_id": r"C:\ws\A.xlsx", "name": "A.xlsx", "active_sheet": "시트A"}
ROW_B = {"workbook_id": r"C:\ws\B.xlsx", "name": "B.xlsx", "active_sheet": "추이"}


class TestExplicitSheetWins:
    def test_a_named_sheet_is_returned_as_is(self):
        svc = _Service([ROW_A])
        assert _resolve_sheet_name(svc, r"C:\ws\A.xlsx", "매출") == "매출"


class TestListedWorkbook:
    def test_it_uses_the_matching_workbooks_active_sheet(self):
        svc = _Service([ROW_B, ROW_A])
        assert _resolve_sheet_name(svc, r"C:\ws\A.xlsx", None) == "시트A"

    def test_matching_by_file_name_also_works(self):
        svc = _Service([ROW_B, ROW_A])
        assert _resolve_sheet_name(svc, "A.xlsx", None) == "시트A"


class TestUnlistedWorkbook:
    """목록에 없는 통합문서 — 여기가 버그였다."""

    def test_it_asks_that_workbook_instead_of_falling_back(self):
        svc = _Service(
            [ROW_B],
            sheets_by_wb={r"C:\scratch\gauge.xlsx": {"sheets": ["목표"], "active_sheet": "목표"}},
        )
        assert _resolve_sheet_name(svc, r"C:\scratch\gauge.xlsx", None) == "목표"
        assert svc.asked == [r"C:\scratch\gauge.xlsx"], "지목한 통합문서에 물어야 한다"

    def test_it_does_not_return_another_workbooks_sheet(self):
        svc = _Service(
            [ROW_B],
            sheets_by_wb={r"C:\scratch\gauge.xlsx": {"sheets": ["목표"], "active_sheet": "목표"}},
        )
        # ROW_B의 '추이'가 새어 나오면 안 된다.
        assert _resolve_sheet_name(svc, r"C:\scratch\gauge.xlsx", None) != "추이"

    def test_it_falls_back_to_the_first_sheet_when_no_active(self):
        svc = _Service(
            [ROW_B],
            sheets_by_wb={r"C:\x.xlsx": {"sheets": ["첫시트", "둘째"], "active_sheet": ""}},
        )
        assert _resolve_sheet_name(svc, r"C:\x.xlsx", None) == "첫시트"

    def test_an_unopenable_workbook_still_degrades_gracefully(self):
        # 물어볼 수 없으면 예전 폴백으로 간다 — 죽는 것보다는 낫다.
        svc = _Service([ROW_B], raises=True)
        assert _resolve_sheet_name(svc, r"C:\없는.xlsx", None) == "추이"


class TestNoWorkbooks:
    def test_it_raises_when_nothing_is_open(self):
        with pytest.raises(ExcelConnectionError):
            _resolve_sheet_name(_Service([]), None, None)
