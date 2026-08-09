from __future__ import annotations

import pytest
from openpyxl import Workbook

from office_claw_sidecar.services import excel_live_service as service_module
from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService
from office_claw_sidecar.services.excel_live_service import AmbiguousWorkbookError


def _make_workbook(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "name"
    ws["B1"] = "amount"
    ws["A2"] = "A"
    ws["B2"] = 30
    ws["A3"] = "B"
    ws["B3"] = 10
    ws["A4"] = "C"
    ws["B4"] = 20
    wb.save(path)
    wb.close()


def test_get_excel_live_service_uses_file_engine_by_default(monkeypatch, tmp_path):
    _make_workbook(tmp_path / "sales.xlsx")
    monkeypatch.delenv("EXCEL_LIVE_ENGINE", raising=False)
    monkeypatch.setattr(service_module, "_excel_live_service", None)
    monkeypatch.setattr(service_module, "_excel_live_service_engine", None)

    service = service_module.get_excel_live_service()

    assert getattr(service, "engine", "") == "file"


def test_legacy_pandas_engine_value_still_selects_file_engine(monkeypatch, tmp_path):
    """예전 설정에 남아 있는 EXCEL_LIVE_ENGINE=pandas로도 앱이 뜨어야 한다."""
    _make_workbook(tmp_path / "sales.xlsx")
    monkeypatch.setenv("EXCEL_LIVE_ENGINE", "pandas")
    monkeypatch.setattr(service_module, "_excel_live_service", None)
    monkeypatch.setattr(service_module, "_excel_live_service_engine", None)

    service = service_module.get_excel_live_service()

    assert getattr(service, "engine", "") == "file"


def _service_scoped_to(tmp_path, monkeypatch):
    """워크스페이스와 cwd를 모두 tmp_path로 묶은 서비스.

    _candidate_roots가 cwd도 훑기 때문에, 고정하지 않으면 저장소 안의 다른
    통합문서가 후보로 섞여 테스트 결과가 실행 위치에 따라 달라진다.
    """
    monkeypatch.chdir(tmp_path)
    return FileExcelLiveService(workspace_root=tmp_path)


def test_single_workbook_is_auto_selected(tmp_path, monkeypatch):
    _make_workbook(tmp_path / "sales.xlsx")
    service = _service_scoped_to(tmp_path, monkeypatch)

    assert service._resolve_workbook_path(None).name == "sales.xlsx"


def test_multiple_workbooks_ask_instead_of_picking_the_newest(tmp_path, monkeypatch):
    """대상 미지정 + 후보 여럿이면 조용히 고르지 않고 되묻는다."""
    _make_workbook(tmp_path / "sales.xlsx")
    _make_workbook(tmp_path / "inventory.xlsx")
    service = _service_scoped_to(tmp_path, monkeypatch)

    with pytest.raises(AmbiguousWorkbookError) as excinfo:
        service._resolve_workbook_path(None)

    assert set(excinfo.value.candidates) == {"sales.xlsx", "inventory.xlsx"}


def test_backup_and_venv_workbooks_are_not_scan_candidates(tmp_path, monkeypatch):
    """백업본·가상환경 샘플이 편집 대상 후보로 잡히면 안 된다."""
    _make_workbook(tmp_path / "sales.xlsx")
    for noise in ("officeclaw_backups", ".venv/Lib/site-packages/xlwings"):
        noise_dir = tmp_path / noise
        noise_dir.mkdir(parents=True, exist_ok=True)
        _make_workbook(noise_dir / "quickstart.xlsx")
    service = _service_scoped_to(tmp_path, monkeypatch)

    assert [fp.name for fp in service._list_workspace_workbooks()] == ["sales.xlsx"]
    assert service._resolve_workbook_path(None).name == "sales.xlsx"


def test_explicit_workbook_id_wins_over_ambiguity(tmp_path, monkeypatch):
    _make_workbook(tmp_path / "sales.xlsx")
    _make_workbook(tmp_path / "inventory.xlsx")
    service = _service_scoped_to(tmp_path, monkeypatch)

    assert service._resolve_workbook_path("inventory.xlsx").name == "inventory.xlsx"


def test_selected_workbook_is_reused_without_asking(tmp_path, monkeypatch):
    _make_workbook(tmp_path / "sales.xlsx")
    _make_workbook(tmp_path / "inventory.xlsx")
    service = _service_scoped_to(tmp_path, monkeypatch)
    service.select_workbook("sales.xlsx")

    assert service._resolve_workbook_path(None).name == "sales.xlsx"


def test_file_service_basic_edit_flow(tmp_path):
    workbook_path = tmp_path / "sales.xlsx"
    _make_workbook(workbook_path)
    service = FileExcelLiveService(workspace_root=tmp_path)
    service.select_workbook("sales.xlsx")

    sorted_result = service.sort_range(
        workbook_id=None,
        sheet_name="Sheet1",
        target_range="A1:B4",
        key_column="amount",
        order="asc",
        has_header=True,
    )
    assert sorted_result["sorted_rows"] == 3

    sorted_read = service.read_range(workbook_id=None, sheet_name="Sheet1", range_ref="A1:B4")
    assert sorted_read["values"] == [["name", "amount"], ["B", 10], ["C", 20], ["A", 30]]

    service.write_range(
        workbook_id=None,
        sheet_name="Sheet1",
        start_cell="A5",
        values_2d=[["B", 10], ["B", 10]],
    )
    deduped = service.dedupe_rows(
        workbook_id=None,
        sheet_name="Sheet1",
        target_range="A1:B6",
        key_columns=[1, 2],
        has_header=True,
    )
    assert deduped["removed_rows"] >= 1

    filtered = service.filter_rows(
        workbook_id=None,
        sheet_name="Sheet1",
        target_range="A1:B6",
        column="amount",
        operator=">=",
        value=20,
        has_header=True,
    )
    assert filtered["filtered_rows"] >= 2
