"""Excel Live 라우터 통합 테스트 — tool-calling 경로."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router
from office_claw_sidecar.services import excel_actions
from office_claw_sidecar.services.llm_service import LLMToolsNotSupportedError


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

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        return {"written_cells": 2, "address": f"{start_cell}:B1"}

    def highlight_by_condition(self, workbook_id, sheet_name, target_range, operator, threshold, fill_color):
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def apply_border(self, workbook_id, sheet_name, target_range, line_style, weight, color):
        return {"changed_cells": 4, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        return {"formula_applied_cells": 5, "address": range_ref}

    def calculate_column_stat(self, workbook_id, sheet_name, column, stat):
        return {
            "column": "B",
            "header": column,
            "stat": stat,
            "value": 400.0,
            "numeric_count": 3,
            "address": "B1:B4",
        }

    def save_workbook(self, workbook_id):
        return {
            "saved": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
        }


def _patch_service(monkeypatch) -> _FakeExcelService:
    fake = _FakeExcelService()
    # /status는 라우터 심볼, 액션 실행은 excel_actions 심볼을 사용한다
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    monkeypatch.setattr(excel_actions, "get_excel_live_service", lambda: fake)
    return fake


def test_excel_live_status(monkeypatch):
    _patch_service(monkeypatch)

    resp = client.get("/excel-live/status", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["workbooks"]) == 1


def test_action_confirm_required_then_approval_execute(monkeypatch):
    _patch_service(monkeypatch)

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
    _patch_service(monkeypatch)

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


def test_action_save_workbook_without_id_uses_selected(monkeypatch):
    _patch_service(monkeypatch)

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
    _patch_service(monkeypatch)

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


def test_action_calculate_column_stat_safe_executes_directly(monkeypatch):
    _patch_service(monkeypatch)

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.calculate_column_stat",
            "params": {"column": "매출", "stat": "sum"},
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["approval_required"] is False
    assert body["result"]["value"] == 400.0


# ── /command — tool-calling 경로 (run_excel_tool_turn 모킹) ──────────────────


def test_command_confirm_tool_returns_approval_then_executes(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        return {
            "type": "approval",
            "action": "excel_live.highlight_by_condition",
            "params": {"target_range": "A:A", "operator": ">=", "threshold": 50},
            "sheet_name": None,
            "reason": "조건부 강조 작업",
            "executed": [],
        }

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={"message": "A열 50 이상 노란색으로 칠해줘", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.highlight_by_condition"
    assert body["approval_required"] is True
    assert body["reason"] == "조건부 강조 작업"

    approval_id = body["pending_approval"]["approval_id"]
    done = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": True},
        headers=HEADERS,
    )
    assert done.status_code == 200
    assert done.json()["result"]["changed_cells"] == 3


def test_command_safe_tool_executed_returns_result_and_answer(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        return {
            "type": "chat",
            "assistant_text": "매출 열의 합계는 400입니다.",
            "executed": [
                {
                    "action": "excel_live.calculate_column_stat",
                    "params": {"column": "매출", "stat": "sum"},
                    "result": {"value": 400.0, "column": "B"},
                }
            ],
        }

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={"message": "매출 열 다 더해줘"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.calculate_column_stat"
    assert body["result"]["value"] == 400.0
    assert body["assistant_text"] == "매출 열의 합계는 400입니다."
    assert len(body["executed_actions"]) == 1


def test_command_plain_chat_returns_assistant_text_only(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        return {"type": "chat", "assistant_text": "안녕하세요! 무엇을 도와드릴까요?", "executed": []}

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={"message": "안녕"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "chat"
    assert body["result"] is None
    assert body["assistant_text"].startswith("안녕하세요")


def test_command_history_is_forwarded_to_agent(monkeypatch):
    _patch_service(monkeypatch)
    captured: dict = {}

    async def _fake_turn(**kwargs):
        captured.update(kwargs)
        return {"type": "chat", "assistant_text": "이어서 답변합니다.", "executed": []}

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "그럼 평균은?",
            "history": [
                {"role": "user", "content": "매출 열 다 더해줘"},
                {"role": "assistant", "content": "합계는 400입니다."},
            ],
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert captured["history"] == [
        {"role": "user", "content": "매출 열 다 더해줘"},
        {"role": "assistant", "content": "합계는 400입니다."},
    ]


def test_command_llm_unreachable_returns_503(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={"message": "매출 열 다 더해줘"},
        headers=HEADERS,
    )
    assert resp.status_code == 503
    assert "Ollama" in resp.json()["detail"]


def test_command_tools_not_supported_returns_400(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        raise LLMToolsNotSupportedError("'claude' provider는 tools(function calling)를 지원하지 않습니다.")

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    resp = client.post(
        "/excel-live/command",
        json={"message": "매출 열 다 더해줘"},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert "tools" in resp.json()["detail"]


# ── B안: 승인 후 에이전트 루프 재개 (라우터 레벨) ────────────────────────────


def test_command_confirm_with_resume_continues_loop_on_approval(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        return {
            "type": "approval",
            "action": "excel_live.highlight_by_condition",
            "params": {"target_range": "A:A", "operator": ">=", "threshold": 50},
            "sheet_name": None,
            "reason": "조건부 강조 작업",
            "executed": [],
            "resume": {"messages": [{"role": "user", "content": "x"}], "tool_call_id": "c1"},
        }

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    first = client.post(
        "/excel-live/command",
        json={"message": "A열 50 이상 강조해줘", "approve": False},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["approval_required"] is True
    approval_id = body["pending_approval"]["approval_id"]

    captured = {}

    async def _fake_resume(**kwargs):
        captured.update(kwargs)
        return {
            "type": "chat",
            "assistant_text": "50 이상 셀을 강조했습니다.",
            "executed": [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {},
                    "result": {"changed_cells": 3},
                }
            ],
        }

    monkeypatch.setattr(excel_live_router, "resume_excel_tool_turn", _fake_resume)

    done = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": True},
        headers=HEADERS,
    )
    assert done.status_code == 200
    body = done.json()
    # 승인 후 루프가 재개되어 LLM 자연어 답변 + 실행 결과가 함께 온다
    assert body["assistant_text"] == "50 이상 셀을 강조했습니다."
    assert body["result"]["changed_cells"] == 3
    assert len(body["executed_actions"]) == 1
    # resume에 원래 pending 컨텍스트가 전달됐는지
    assert captured["action"] == "excel_live.highlight_by_condition"
    assert captured["resume"]["tool_call_id"] == "c1"


def test_command_confirm_reject_does_not_resume(monkeypatch):
    _patch_service(monkeypatch)

    async def _fake_turn(**_kwargs):
        return {
            "type": "approval",
            "action": "excel_live.filter_rows",
            "params": {"column": "매출", "operator": ">=", "value": 5000000},
            "sheet_name": None,
            "reason": "행 필터링",
            "executed": [],
            "resume": {"messages": [], "tool_call_id": "c9"},
        }

    monkeypatch.setattr(excel_live_router, "run_excel_tool_turn", _fake_turn)

    def _resume_should_not_be_called(**_kwargs):
        raise AssertionError("거부 시 resume_excel_tool_turn이 호출되면 안 된다")

    monkeypatch.setattr(excel_live_router, "resume_excel_tool_turn", _resume_should_not_be_called)

    first = client.post(
        "/excel-live/command",
        json={"message": "매출 500만 이상만 남겨줘"},
        headers=HEADERS,
    )
    approval_id = first.json()["pending_approval"]["approval_id"]

    done = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": False, "rejection_reason": "취소"},
        headers=HEADERS,
    )
    assert done.status_code == 200
    body = done.json()
    assert body["result"]["approved"] is False
    assert "거부" in body["reason"]
