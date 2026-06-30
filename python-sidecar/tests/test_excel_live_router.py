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

    def get_active_selection_ref(self, workbook_id, sheet_name):
        return "B2:C3"

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [[1, 2]], "address": range_ref, "row_count": 1, "col_count": 2}

    def get_range_snapshot(self, workbook_id, sheet_name, range_ref):
        return {"address": range_ref, "row_count": 5, "col_count": 5, "filled_cells": 4}

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        return {"written_cells": 2, "address": f"{start_cell}:B1"}

    def create_table(self, workbook_id, sheet_name, start_cell, rows, cols, with_border):
        return {"created": True, "address": "B2:F6", "rows": rows, "cols": cols}

    def highlight_by_condition(self, workbook_id, sheet_name, target_range, operator, threshold, fill_color):
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def fill_range(self, workbook_id, sheet_name, target_range, fill_color):
        return {"changed_cells": 12, "address": target_range}

    def apply_border(self, workbook_id, sheet_name, target_range, line_style, weight, color):
        return {"changed_cells": 4, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        return {"formula_applied_cells": 5, "address": range_ref}

    def save_workbook(self, workbook_id):
        return {
            "saved": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
        }


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
    
    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {"target_range": "A:A", "operator": ">=", "threshold": 50, "fill_color": "#FFFF00"},
                    "reason": "조건부 강조",
                }
            ],
            "reason": "highlight",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

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


def test_action_save_workbook_without_id_uses_selected(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.save_workbook",
            "params": {},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.save_workbook"
    assert body["result"]["saved"] is True


def test_action_apply_border_uses_active_selection_when_range_missing(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.apply_border",
            "params": {"target_range": "__ACTIVE_SELECTION__"},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"


def test_action_create_table_uses_active_cell_when_start_missing(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.create_table",
            "params": {"rows": 5, "cols": 5},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["rows"] == 5
    assert body["result"]["cols"] == 5


def test_command_rule_based_fill_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "표 색을 전반적으로 노랗게 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.fill_range"
    assert body["approval_required"] is True


def test_command_parse_failure_returns_400_instead_of_list_fallback(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭔가 이상한 명령", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert "해석하지 못했습니다" in resp.json().get("detail", "")


def test_command_executes_action_plan_sequentially(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {"action": "excel_live.read_range", "params": {"range_ref": "A1:B2"}, "reason": "현재 상태 확인"},
                {"action": "excel_live.read_range", "params": {"range_ref": "B2:C3"}, "reason": "후속 확인"},
            ],
            "reason": "2단계 계획 실행",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "계획형 테스트", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.read_range"
    assert body["result"]["executed_steps"] == 2


def test_command_passes_context_range_to_parser(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        assert context["context_range"] == "C3:E9"
        return {
            "action_plan": [
                {"action": "excel_live.read_range", "params": {"range_ref": "C3:E9"}, "reason": "문맥 범위 사용"}
            ],
            "reason": "문맥 기반",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "이 범위 확인", "context_range": "C3:E9", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.read_range"


def test_command_stabilizes_table_intent_when_llm_returns_invalid_write_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {"action": "excel_live.write_range", "params": {"start_cell": "__ACTIVE_CELL__"}, "reason": "표 생성"}
            ],
            "reason": "테이블 생성",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "5*5 표 만들어줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["rows"] == 5
    assert body["result"]["cols"] == 5


def test_command_applies_context_range_to_here_border(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": "__ACTIVE_SELECTION__"},
                    "reason": "문맥 범위 테두리",
                }
            ],
            "reason": "context",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "여기에 테두리 적용해줘", "context_range": "B2:D5", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"
    assert body["result"]["address"] == "B2:D5"


def test_command_replans_once_when_execution_verify_fails(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {"action": "excel_live.fill_range", "params": {"target_range": "A:A"}, "reason": "1차 계획"}
            ],
            "reason": "first plan",
        }

    replanned_called = {"count": 0}

    async def _replan_parse(_message, llm_service, context, forbid_list_action=False, require_edit_action=False):
        replanned_called["count"] += 1
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": "B2:D5", "line_style": "continuous", "weight": "medium", "color": "#000000"},
                    "reason": "2차 계획",
                }
            ],
            "reason": "replanned",
        }

    verify_count = {"count": 0}

    def _verify_step_result(**kwargs):
        verify_count["count"] += 1
        # 첫 계획(fill_range)은 계속 실패, 재계획(apply_border)만 성공
        return kwargs.get("action") == "excel_live.apply_border"

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)
    monkeypatch.setattr(excel_live_router, "parse_command_plan_with_llm", _replan_parse)
    monkeypatch.setattr(excel_live_router, "_verify_step_result", _verify_step_result)

    resp = client.post(
        "/excel-live/command",
        json={"message": "전반적으로 색칠", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"
    assert replanned_called["count"] == 1

