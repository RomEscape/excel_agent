"""Excel Live 라우터 통합 테스트."""

from __future__ import annotations

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router


HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


class _FakeExcelService:
    def __init__(self):
        self._selected = r"C:\work\sales.xlsx"
        self._workbooks = [
            {
                "workbook_id": r"C:\work\sales.xlsx",
                "name": "sales.xlsx",
                "full_path": r"C:\work\sales.xlsx",
                "active_sheet": "Sheet1",
            }
        ]

    def is_available(self):
        return True

    def list_workbooks(self):
        return self._workbooks

    def select_workbook(self, workbook_id):
        self._selected = workbook_id
        return {"selected": True, "workbook_id": workbook_id}

    def get_selected_workbook_id(self):
        return self._selected

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [[1, 2]], "address": range_ref, "row_count": 1, "col_count": 2}

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        return {"written_cells": 2, "address": f"{start_cell}:B1"}

    def highlight_by_condition(self, workbook_id, sheet_name, target_range, operator, threshold, fill_color):
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        return {"formula_applied_cells": 5, "address": range_ref}


def test_excel_live_status(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.get("/excel-live/status", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["workbooks"]) == 1


def test_action_confirm_required_then_approval_execute(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    first = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.highlight_by_condition",
            "params": {
                "target_range": "A:A",
                "operator": ">=",
                "threshold": 50,
                "fill_color": "#FFFF00",
            },
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["approval_required"] is True
    approval_id = body["pending_approval"]["approval_id"]

    second = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    done = second.json()
    assert done["ok"] is True
    assert done["result"]["changed_cells"] == 3


def test_action_without_workbook_id_uses_first_open_workbook(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.write_range",
            "params": {"start_cell": "B2", "values_2d": [["H1", "H2", "H3"]]},
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["written_cells"] == 2


def test_command_rule_based_highlight(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "A열 데이터 중 50 이상인 셀을 노란색으로 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.highlight_by_condition"
    assert body["approval_required"] is True

