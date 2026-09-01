"""xlwings 엔진(ExcelLiveService)이 파일 엔진에만 있던 작업들을 같은 계약으로 갖는지.

2026-08-19 GUI 실측: Excel을 띄운 채 "콤마 찍어주라"가 `'ExcelLiveService' object has no
attribute 'set_number_format'`로 실패했다. 배터리는 파일 엔진으로만 돌아 이 구멍을 못 봤다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_service import _build_service, _FakeRange

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService
from office_claw_sidecar.services.excel_live_service import ExcelLiveError, ExcelLiveService


def test_every_public_method_of_the_file_engine_exists_on_the_xlwings_engine():
    file_only = {
        name
        for name in dir(FileExcelLiveService)
        if not name.startswith("_") and callable(getattr(FileExcelLiveService, name, None))
    } - {name for name in dir(ExcelLiveService) if callable(getattr(ExcelLiveService, name, None))}
    assert file_only == set(), f"xlwings 엔진에 없는 공개 메서드: {sorted(file_only)}"


# ---- 가짜 xlwings에 필요한 최소 속성만 덧붙인다 ----

def _install_fake_features(monkeypatch):
    formats: dict[tuple[int, int, int, int], str] = {}
    merged: list[str] = []
    autofit_calls: list[str] = []

    def _get_nf(self):
        return formats.get((self._sr, self._sc, self._rows, self._cols))

    def _set_nf(self, code):
        formats[(self._sr, self._sc, self._rows, self._cols)] = code

    monkeypatch.setattr(_FakeRange, "number_format", property(_get_nf, _set_nf), raising=False)
    monkeypatch.setattr(_FakeRange, "merge", lambda self: merged.append(self.address), raising=False)
    monkeypatch.setattr(_FakeRange, "unmerge", lambda self: merged.remove(self.address) if self.address in merged else None, raising=False)
    monkeypatch.setattr(_FakeRange, "shape", property(lambda self: (self._rows, self._cols)), raising=False)

    class _Cols:
        def __init__(self, rng):
            self.count = rng._cols
            self._rng = rng

        def autofit(self):
            autofit_calls.append(self._rng.address)

    monkeypatch.setattr(_FakeRange, "columns", property(lambda self: _Cols(self)), raising=False)
    return formats, merged, autofit_calls


def test_set_number_format_applies_to_the_range(monkeypatch):
    formats, _merged, _auto = _install_fake_features(monkeypatch)
    service, wb1, _wb2 = _build_service()
    out = service.set_number_format(wb1.fullname, "Sheet1", "A1:B2", "#,##0")
    assert out["formatted_cells"] == 4 and out["format_code"] == "#,##0" and out["address"] == "A1:B2"
    assert formats[(1, 1, 2, 2)] == "#,##0"


def test_merge_and_unmerge_cells(monkeypatch):
    _formats, merged, _auto = _install_fake_features(monkeypatch)
    service, wb1, _wb2 = _build_service()
    # 앱 DisplayAlerts 흉내
    class _Api:
        DisplayAlerts = True
    wb1.app = type("App", (), {"api": _Api()})()
    # 값이 둘 이상인 병합은 **지우는 일**이라 거절한다 — 파일 엔진과 같은 계약
    # (2026-09-01 위치 감사: 이 가드가 라이브에 없어 값이 파괴됐다).
    with pytest.raises(ExcelLiveError):
        service.merge_cells(wb1.fullname, "Sheet1", "A1:B1")
    assert merged == []
    # 좌상단 외에 값이 없으면 병합한다.
    sheet1 = wb1.sheets[0]
    sheet1._values.pop((1, 2), None)
    assert service.merge_cells(wb1.fullname, "Sheet1", "A1:B1") == {"merged": True, "address": "A1:B1"}
    assert merged == ["A1:B1"]
    out = service.unmerge_cells(wb1.fullname, "Sheet1", "A1:B1")
    assert out["address"] == "A1:B1" and merged == []


def test_autofit_columns_counts_columns(monkeypatch):
    _formats, _merged, auto = _install_fake_features(monkeypatch)
    service, wb1, _wb2 = _build_service()
    out = service.autofit_columns(wb1.fullname, "Sheet1", "A1:B2")
    assert out == {"adjusted_columns": 2, "address": "A1:B2"} and auto == ["A1:B2"]


def test_freeze_panes_sets_split_and_freeze(monkeypatch):
    service, wb1, _wb2 = _build_service()

    class _Window:
        FreezePanes = False
        SplitRow = 0
        SplitColumn = 0

    win = _Window()
    wb1.app = type("App", (), {"api": type("Api", (), {"ActiveWindow": win})()})()
    out = service.freeze_panes(wb1.fullname, "Sheet1", "A2")
    assert out == {"frozen": True, "freeze_at": "A2"}
    assert (win.SplitRow, win.SplitColumn, win.FreezePanes) == (1, 0, True)
    assert service.freeze_panes(wb1.fullname, "Sheet1", "해제") == {"frozen": False, "freeze_at": None}
    assert win.FreezePanes is False


def test_find_replace_writes_only_changed_cells(monkeypatch):
    service, wb1, _wb2 = _build_service()
    sheet = wb1.sheets[0]
    sheet.seed("A1:B2", [["서울 본사", 10], ["부산", "서울"]])
    out = service.find_replace(wb1.fullname, "Sheet1", "A1:B2", "서울", "인천")
    assert out["replaced_cells"] == 2
    assert sheet.range("A1").value == "인천 본사" and sheet.range("B2").value == "인천"
    assert sheet.range("B1").value == 10  # 숫자 칸은 건드리지 않는다


def test_table_ops_by_header_name(monkeypatch):
    service, wb1, _wb2 = _build_service()
    sheet = wb1.sheets[0]
    sheet.seed("A1:C4", [["지역", "매출", "건수"], ["서울", 100, 3], ["부산", 250, 5], ["서울", 50, 1]])
    stat = service.calculate_column_stat(wb1.fullname, "Sheet1", "매출", "sum")
    assert stat["value"] == 400.0 and stat["header"] == "매출" and stat["column"] == "B"
    grouped = service.group_by_aggregate(wb1.fullname, "Sheet1", "지역", "sum", "매출")
    assert [(g["key"], g["value"]) for g in grouped["groups"]] == [("부산", 250.0), ("서울", 150.0)]
    renamed = service.rename_column(wb1.fullname, "Sheet1", "건수", "주문건수")
    assert renamed == {"old_name": "건수", "new_name": "주문건수", "column": "C"}
    assert sheet.range("C1").value == "주문건수"
    with pytest.raises(ExcelLiveError):
        service.calculate_column_stat(wb1.fullname, "Sheet1", "없는열", "sum")


def test_sort_range_pins_the_total_row_at_the_bottom():
    service, wb1, _wb2 = _build_service()
    sheet = wb1.sheets[0]
    sheet.seed("A1:B4", [["지역", "주문"], ["서울", 10], ["부산", 30], ["합계", 40]])
    out = service.sort_range(wb1.fullname, "Sheet1", "A1:B4", key_column="주문", order="desc")
    assert out["sorted_rows"] == 2 and out["pinned_tail_rows"] == 1
    assert [sheet.range(f"A{r}").value for r in range(1, 5)] == ["지역", "부산", "서울", "합계"]
    assert sheet.range("B4").value == 40
