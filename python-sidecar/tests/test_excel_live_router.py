"""Excel Live 라우터 통합 테스트."""

from __future__ import annotations

import asyncio

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
        self._last_snapshot = {"row_count": 5, "col_count": 5}

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
        return {
            "address": range_ref,
            "row_count": self._last_snapshot["row_count"],
            "col_count": self._last_snapshot["col_count"],
            "filled_cells": 4,
        }

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        return {"written_cells": 2, "address": f"{start_cell}:B1"}

    def create_table(self, workbook_id, sheet_name, start_cell, rows, cols, with_border):
        self._last_snapshot = {"row_count": int(rows), "col_count": int(cols)}
        return {"created": True, "address": "B2:F6", "rows": rows, "cols": cols}

    def highlight_by_condition(self, workbook_id, sheet_name, target_range, operator, threshold, fill_color):
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def fill_range(self, workbook_id, sheet_name, target_range, fill_color):
        return {"changed_cells": 12, "address": target_range}

    def clear_range(self, workbook_id, sheet_name, target_range):
        return {"cleared_cells": 12, "address": target_range}

    def apply_border(self, workbook_id, sheet_name, target_range, line_style, weight, color):
        return {"changed_cells": 4, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        return {"formula_applied_cells": 5, "address": range_ref}

    def verify_formula_result(self, workbook_id, sheet_name, range_ref):
        return {
            "address": range_ref,
            "non_empty_cells": 8,
            "numeric_cells": 8,
            "sum": 24500.0,
            "average": 3062.5,
            "sample_values": [1200, 3500],
        }

    def sort_range(self, workbook_id, sheet_name, target_range, key_column, order, has_header):
        return {
            "sorted_rows": 12,
            "address": target_range,
            "key_column_index": 1,
            "order": order,
        }

    def filter_rows(self, workbook_id, sheet_name, target_range, column, operator, value, has_header):
        return {
            "filtered_rows": 4,
            "address": target_range,
            "column_index": 1,
            "operator": operator,
            "value": value,
        }

    def dedupe_rows(self, workbook_id, sheet_name, target_range, key_columns, has_header):
        return {
            "removed_rows": 3,
            "remaining_rows": 7,
            "address": target_range,
            "key_columns": key_columns or [1],
        }

    def pivot_table(
        self,
        workbook_id,
        sheet_name,
        source_range,
        row_field,
        value_field,
        agg,
        column_field,
        output_sheet,
        output_start,
        has_header,
    ):
        return {
            "created": True,
            "address": "A1:C5",
            "rows": 5,
            "cols": 3,
            "sheet_name": output_sheet or sheet_name,
        }

    def create_chart(self, workbook_id, sheet_name, source_range, chart_type, title, output_sheet):
        return {
            "created": True,
            "chart_name": "chart_1",
            "chart_type": chart_type,
            "source_address": source_range,
            "sheet_name": output_sheet or sheet_name,
        }

    def validate_data(self, workbook_id, sheet_name, target_range, checks, has_header, date_min, date_max):
        return {
            "address": target_range,
            "issues": [{"type": "empty", "count": 2, "samples": ["B3", "C7"]}],
            "total_issues": 2,
        }

    def save_workbook(self, workbook_id):
        return {
            "saved": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
        }

    def list_workbook_backups(self, workbook_id, limit=20):
        return {
            "workbook_id": workbook_id,
            "source_path": workbook_id,
            "backup_dir": r"C:\work\officeclaw_backups",
            "backups": [
                {
                    "backup_path": r"C:\work\officeclaw_backups\sales.command.20260707_120000.xlsx",
                    "backup_name": "sales.command.20260707_120000.xlsx",
                    "size_bytes": 4096,
                    "modified_at": "2026-07-07T12:00:00",
                }
            ][: max(1, int(limit))],
        }

    def restore_workbook_from_backup(self, workbook_id, backup_path=None):
        return {
            "restored": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
            "restored_from_backup_path": backup_path
            or r"C:\work\officeclaw_backups\sales.command.20260707_120000.xlsx",
            "pre_restore_backup_path": r"C:\work\officeclaw_backups\sales.pre_restore.20260707_120100.xlsx",
            "active_sheet": "Sheet1",
        }


def test_excel_live_status(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.get("/excel-live/status", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["workbooks"]) == 1


def test_excel_live_backups_list(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.get("/excel-live/backups?limit=5", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["workbook_id"] == r"C:\work\sales.xlsx"
    assert len(body["backups"]) == 1
    assert body["backups"][0]["backup_name"].endswith(".xlsx")


def test_excel_live_restore_last(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/restore-last",
        json={
            "workbook_id": r"C:\work\sales.xlsx",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.restore_last_backup"
    assert body["result"]["restored"] is True


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


def test_command_two_color_condition_executes_fill_then_highlight(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "A열 15 이상은 빨간색, 나머지는 노란색으로 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.highlight_by_condition"
    assert body["result"]["executed_steps"] == 2
    plan_actions = [step["action"] for step in body["result"]["plan"]]
    assert plan_actions == ["excel_live.fill_range", "excel_live.highlight_by_condition"]


def test_detect_operation_intent_color_else_not_formula():
    intent = excel_live_router._detect_operation_intent("A열 15 이상은 빨간색 아니면 노란색으로 칠해줘")
    assert intent != "formula"


def test_command_rule_based_clear_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "안에 내용 전부 지우고 깨끗하게 만들어줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
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


def test_command_parse_failure_returns_clarify_for_excel_like_request(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "대시보드처럼 한눈에 정리해줘", "approve": False, "session_id": "clarify-test"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.chart"}
    assert body["result"]["ask_follow_up"] is True


def test_command_parse_timeout_returns_clarify_not_400(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _raise_timeout(_message, llm_service, context):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_timeout)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭔가 이상한 명령", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True
    assert body["result"]["parse_timeout"] is True


def test_command_parse_failure_problem_phrase_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭐가 문제인지 봐줘", "approve": False, "session_id": "clarify-test-2"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.validate_data"}


def test_command_empty_plan_excel_like_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _empty_plan(_message, llm_service, context):
        return {"action_plan": [], "reason": "불충분"}

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _empty_plan)

    resp = client.post(
        "/excel-live/command",
        json={"message": "보고용으로 정리해줘", "approve": False, "session_id": "clarify-test-3"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.general"}
    assert body["result"]["ask_follow_up"] is True


def test_command_general_followup_prefers_llm_then_fallback(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "보기 좋게 만들어줘", "session_id": "sess-general-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.general"
    assert body["result"]["ask_follow_up"] is True
    assert called["count"] == 1


def test_command_safety_followup_prefers_llm_then_fallback(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "파일이 읽기 전용이라 수정이 안 돼", "session_id": "sess-safety-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.safety"
    assert body["result"]["ask_follow_up"] is True
    assert called["count"] == 1


def test_command_validation_error_excel_like_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _invalid_plan(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.write_range",
                    "params": {},
                    "reason": "잘못된 계획",
                }
            ],
            "reason": "invalid sort plan",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _invalid_plan)

    resp = client.post(
        "/excel-live/command",
        json={"message": "엑셀 alpha123", "approve": True, "session_id": "clarify-invalid-plan"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True


def test_command_general_slot_upgrades_to_specific_intent(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "보기 좋게 만들어줘", "session_id": "sess-upgrade-1", "approve": False},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["action"] == "excel_live.general"

    second = client.post(
        "/excel-live/command",
        json={"message": "매출 열 기준으로 내림차순", "session_id": "sess-upgrade-1", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.sort_range"
    # 첫 턴에서만 LLM 해석 시도, 이후는 pending 슬롯을 이어서 처리한다.
    assert called["count"] == 1


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
        json={"message": "alpha123", "approve": True},
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
        json={"message": "beta123", "context_range": "C3:E9", "approve": True},
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


def test_command_executes_sort_range_action_plan(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.sort_range",
                    "params": {"target_range": "A1:E20", "key_column": "E", "order": "desc"},
                    "reason": "매출 내림차순 정렬",
                }
            ],
            "reason": "sort",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "매출 높은 순으로 보여줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.sort_range"
    assert body["result"]["order"] == "desc"


def test_command_executes_validate_data_action_plan(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "read",
            "action_plan": [
                {
                    "action": "excel_live.validate_data",
                    "params": {"target_range": "A1:E20", "checks": ["empty", "negative"]},
                    "reason": "데이터 오류 점검",
                }
            ],
            "reason": "validate",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "이상한 값 있는지 봐줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.validate_data"
    assert body["result"]["total_issues"] == 2


def test_command_create_chart_requires_confirm(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.create_chart",
                    "params": {"source_range": "A1:B12", "chart_type": "line", "title": "월별 매출"},
                    "reason": "차트 생성",
                }
            ],
            "reason": "chart",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "그래프로 만들어줘", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_required"] is True
    assert body["action"] == "excel_live.create_chart"


def test_command_executes_verify_formula_result(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "read",
            "action_plan": [
                {
                    "action": "excel_live.verify_formula_result",
                    "params": {"range_ref": "D2:D20"},
                    "reason": "수식 결과 검증",
                }
            ],
            "reason": "formula verify",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "D2:D20 수식 값 확인해줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["numeric_cells"] == 8


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


def test_command_replan_formula_without_equal_is_normalized(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {"action": "excel_live.fill_range", "params": {"target_range": "A:A"}, "reason": "1차 계획"}
            ],
            "reason": "first plan",
        }

    async def _replan_parse(_message, llm_service, context, forbid_list_action=False, require_edit_action=False):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.set_formula",
                    "params": {"range_ref": "D2:D6", "formula_a1": "B2*C2"},
                    "reason": "2차 계획 수식",
                }
            ],
            "reason": "replanned formula",
        }

    def _verify_step_result(**kwargs):
        return kwargs.get("action") == "excel_live.set_formula"

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)
    monkeypatch.setattr(excel_live_router, "parse_command_plan_with_llm", _replan_parse)
    monkeypatch.setattr(excel_live_router, "_verify_step_result", _verify_step_result)

    resp = client.post(
        "/excel-live/command",
        json={"message": "금액 계산해줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.set_formula"
    assert body["result"]["formula_applied_cells"] >= 1


def test_command_create_table_multiturn_slot_fill_with_session(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "표 만들어줘",
            "session_id": "sess-table-1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["ok"] is True
    assert body1["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "5*5, 금액, 장소, 날짜, 요건, 비고",
            "session_id": "sess-table-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.write_range"
    assert body2["result"]["executed_steps"] == 2


def test_command_table_vague_ignores_llm_1x1_guess_and_asks_follow_up(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_create_table_slots.clear()

    async def _fake_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.create_table",
                    "params": {"start_cell": "A1", "rows": 1, "cols": 1},
                    "reason": "llm guessed tiny table",
                }
            ],
            "slot_fill": {"rows": 1, "cols": 1},
            "partial_params": {"rows": 1, "cols": 1},
            "reason": "guess",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _fake_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-table-vague", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["ask_follow_up"] is True


def test_command_table_intent_skips_llm_parse_for_faster_follow_up(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_create_table_slots.clear()

    async def _must_not_run(_message, llm_service, context):
        raise AssertionError("LLM parse should be skipped for direct table intent")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _must_not_run)

    resp = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-table-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["ask_follow_up"] is True


def test_command_create_table_slot_state_expires_by_ttl(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "표 만들어줘",
            "session_id": "sess-table-ttl",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    slot = excel_live_router._pending_create_table_slots.get("sess-table-ttl")
    assert slot is not None
    slot.updated_at_ts -= (excel_live_router._SLOT_TTL_SECONDS + 1)

    expired = client.post(
        "/excel-live/command",
        json={
            "message": "5*5, 금액, 장소",
            "session_id": "sess-table-ttl",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert expired.status_code == 400


def test_command_template_follow_up_then_affirmative_executes(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "회의록 표 하나 만들어줘",
            "session_id": "sess-template-1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True
    assert "회의록" in body1["result"]["follow_up_question"]

    second = client.post(
        "/excel-live/command",
        json={
            "message": "응 그 정도면 돼",
            "session_id": "sess-template-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.write_range"
    assert body2["result"]["executed_steps"] == 2


def test_command_sort_multiturn_followup_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "정렬해줘", "session_id": "sess-sort-1", "approve": False},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True
    assert "열 기준" in body1["result"]["follow_up_question"]

    second = client.post(
        "/excel-live/command",
        json={
            "message": "매출 열 기준으로 높은 순",
            "session_id": "sess-sort-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.sort_range"
    assert body2["result"]["order"] == "desc"


def test_command_filter_multiturn_followup_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "완료된 것만 따로 보고 싶어", "session_id": "sess-filter-1", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["ok"] is True
    assert body1["action"] == "excel_live.filter_rows"
    assert body1["result"]["filtered_rows"] == 4


def test_command_formula_multiturn_then_execute_and_verify(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-1", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "B열이 수량이고 C열이 단가야",
            "session_id": "sess-formula-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.verify_formula_result"
    assert body2["result"]["numeric_cells"] == 8
    assert body2["result"]["executed_steps"] == 2


def test_command_formula_verify_zero_numeric_returns_follow_up(monkeypatch):
    class _ZeroFormulaFake(_FakeExcelService):
        def verify_formula_result(self, workbook_id, sheet_name, range_ref):
            return {
                "address": range_ref,
                "non_empty_cells": 4,
                "numeric_cells": 0,
                "sum": 0.0,
                "average": 0.0,
                "sample_values": ["N/A"],
            }

    fake = _ZeroFormulaFake()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-2", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "B열이 수량이고 C열이 단가야", "session_id": "sess-formula-2", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["ask_follow_up"] is True


def test_command_formula_countif_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "완료 건수 세어줘", "session_id": "sess-formula-countif", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    first_body = first.json()
    if first_body.get("result", {}).get("ask_follow_up"):
        second = client.post(
            "/excel-live/command",
            json={"message": "B열 상태에서 완료 개수", "session_id": "sess-formula-countif", "approve": True},
            headers=HEADERS,
        )
        assert second.status_code == 200
        body = second.json()
    else:
        body = first_body

    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    if "executed_steps" in body.get("result", {}):
        assert body["result"]["executed_steps"] == 2


def test_command_formula_auto_retry_once_when_numeric_zero(monkeypatch):
    class _RetryFormulaFake(_FakeExcelService):
        def __init__(self):
            super().__init__()
            self.formulas = []
            self.verify_calls = 0

        def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
            self.formulas.append(formula_a1)
            return {"formula_applied_cells": 5, "address": range_ref}

        def verify_formula_result(self, workbook_id, sheet_name, range_ref):
            self.verify_calls += 1
            if self.verify_calls == 1:
                return {
                    "address": range_ref,
                    "non_empty_cells": 4,
                    "numeric_cells": 0,
                    "sum": 0.0,
                    "average": 0.0,
                    "sample_values": ["ERR"],
                }
            return {
                "address": range_ref,
                "non_empty_cells": 4,
                "numeric_cells": 4,
                "sum": 100.0,
                "average": 25.0,
                "sample_values": [10, 20],
            }

    fake = _RetryFormulaFake()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-retry", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "B열이 수량이고 C열이 단가야", "session_id": "sess-formula-retry", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["auto_retry_applied"] is True
    assert len(fake.formulas) == 2
    assert fake.formulas[1].startswith("=IFERROR(")


def test_command_formula_vlookup_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "코드 기준으로 가격 찾아와", "session_id": "sess-formula-vlookup", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "조회값은 A열, 참조표는 F열부터 H열, 반환 2열",
            "session_id": "sess-formula-vlookup",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"


def test_command_formula_if_compare_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "점수 기준 조건식 넣어줘", "session_id": "sess-formula-if", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "C열이 70 미만이면 미달, 아니면 통과",
            "session_id": "sess-formula-if",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"

