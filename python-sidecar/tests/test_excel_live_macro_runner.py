"""매크로 분기·실행기 라우터 테스트.

순차 진행·중단·재개·롤백만 본다. 하위 명령이 실제로 어떤 액션이 되는지는 기존
/command 경로의 몫이라 여기서는 그 호출을 가로채 결정론적으로 만든다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router
from office_claw_sidecar.routers.excel_live import ExcelLiveActionResponse
from office_claw_sidecar.services.excel_macro_planner import MacroStepPlan

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)

WORKBOOK = r"C:\work\sales.xlsx"


class _BackupCapableExcelService:
    """매크로 백업·복구만 흉내 내는 최소 서비스."""

    def __init__(self):
        self.backup_calls: list[str] = []
        self.restore_calls: list[str] = []

    def is_available(self):
        return True

    def list_workbooks(self):
        return [
            {
                "workbook_id": WORKBOOK,
                "name": "sales.xlsx",
                "full_path": WORKBOOK,
                "active_sheet": "Sales_Data",
            }
        ]

    def get_selected_workbook_id(self):
        return WORKBOOK

    def list_sheets(self, workbook_id):
        return {"sheets": ["Sales_Data"], "count": 1, "active_sheet": "Sales_Data"}

    def get_used_range_ref(self, workbook_id, sheet_name):
        return "A1:C3"

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [["지역", "매출"]], "address": range_ref, "row_count": 1, "col_count": 2}

    def create_workbook_backup(self, workbook_id, label=""):
        self.backup_calls.append(label)
        return {"backup_created": True, "backup_path": rf"C:\backups\{label}.xlsx"}

    def restore_workbook_from_backup(self, workbook_id, backup_path=None):
        self.restore_calls.append(str(backup_path))
        return {"restored": True, "restored_from_backup_path": backup_path}


@pytest.fixture
def excel_service(monkeypatch):
    service = _BackupCapableExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: service)
    return service


@pytest.fixture(autouse=True)
def clear_macro_runs():
    excel_live_router._macro_runs.clear()
    yield
    excel_live_router._macro_runs.clear()


def _fake_decomposer(commands: list[str]):
    async def _decompose(message, llm_service, **kwargs):
        return [
            MacroStepPlan(index=i, command=command, destructive=False)
            for i, command in enumerate(commands, start=1)
        ]

    return _decompose


def _plan_macro(monkeypatch, commands: list[str], message: str = "대시보드 만들어줘"):
    monkeypatch.setattr(excel_live_router, "decompose_macro_request", _fake_decomposer(commands))
    resp = client.post(
        "/excel-live/command",
        json={"message": message, "workbook_id": WORKBOOK, "session_id": "macro-test"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "excel_live.macro_plan"
    return body["result"]


def _stub_command(monkeypatch, handler):
    """하위 명령 실행을 가로챈다. 인자로 받은 함수가 메시지를 보고 응답을 만든다."""
    calls: list[str] = []

    async def _post_command(req, llm):
        calls.append(req.message)
        assert req.approve is True, "매크로 하위 명령은 승인된 상태로 실행돼야 한다"
        return handler(req.message)

    monkeypatch.setattr(excel_live_router, "post_command", _post_command)
    return calls


def _ok(message):
    return ExcelLiveActionResponse(
        ok=True,
        action="excel_live.write_range",
        result={"address": "A1"},
        reason=f"{message} 완료",
    )


def test_macro_sized_request_returns_a_plan_for_approval(monkeypatch, excel_service):
    result = _plan_macro(monkeypatch, ["1번 명령", "2번 명령", "3번 명령"])

    assert result["ask_macro_approval"] is True
    assert result["status"] == "planned"
    assert result["total"] == 3
    assert [step["command"] for step in result["steps"]] == ["1번 명령", "2번 명령", "3번 명령"]
    # 승인 전에는 아무것도 손대지 않는다.
    assert excel_service.backup_calls == []


@pytest.mark.parametrize(
    "message",
    ["A1에 매출 입력", "B2:B10 합계 구해줘", "저장해줘"],
)
def test_simple_command_never_reaches_the_decomposer(monkeypatch, excel_service, message):
    """오탐이 최대 위험이다 — 단순 명령이 매크로로 새면 멀쩡한 경로가 망가진다.

    분해기 예외는 호출부가 삼키므로(실패 시 기존 경로로 떨어지는 설계) 예외로 잡으면
    안 되고, 호출 자체를 세야 한다.
    """
    seen: list[str] = []

    async def _record(msg, llm_service, **kwargs):
        seen.append(msg)
        return []

    monkeypatch.setattr(excel_live_router, "decompose_macro_request", _record)

    client.post(
        "/excel-live/command",
        json={"message": message, "workbook_id": WORKBOOK},
        headers=HEADERS,
    )

    assert seen == []


def test_single_step_decomposition_falls_back_to_the_normal_path(monkeypatch, excel_service):
    """한 단계짜리는 매크로가 아니다 — 승인 카드만 늘어난다."""
    monkeypatch.setattr(excel_live_router, "decompose_macro_request", _fake_decomposer(["하나뿐"]))

    resp = client.post(
        "/excel-live/command",
        json={"message": "대시보드 만들어줘", "workbook_id": WORKBOOK},
        headers=HEADERS,
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert resp.json()["action"] != "excel_live.macro_plan"


def test_steps_run_in_order_until_done(monkeypatch, excel_service):
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령", "3번 명령"])
    calls = _stub_command(monkeypatch, _ok)

    statuses = []
    for _ in range(3):
        resp = client.post(
            "/excel-live/macro/step",
            json={"macro_id": plan["macro_id"]},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        statuses.append(resp.json()["result"]["status"])

    assert calls == ["1번 명령", "2번 명령", "3번 명령"]
    assert statuses[-1] == "done"
    assert excel_service.backup_calls == ["macro"], "매크로 시작 백업은 한 번만 떠야 한다"


def test_unchecked_steps_are_skipped(monkeypatch, excel_service):
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령", "3번 명령"])
    calls = _stub_command(monkeypatch, _ok)

    resp = client.post(
        "/excel-live/macro/step",
        json={"macro_id": plan["macro_id"], "skip_indices": [2]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    client.post("/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS)

    assert calls == ["1번 명령", "3번 명령"]


def test_follow_up_halts_and_resumes_with_the_answer(monkeypatch, excel_service):
    """되묻기가 나오면 나머지 계획을 버리지 않고 그 자리에서 기다린다."""
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령"])

    def _handler(message):
        if message == "1번 명령":
            return ExcelLiveActionResponse(
                ok=True,
                action="excel_live.clarify",
                result={"ask_follow_up": True, "follow_up_question": "어느 열인가요?"},
                reason="어느 열인가요?",
            )
        return _ok(message)

    calls = _stub_command(monkeypatch, _handler)

    first = client.post(
        "/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS
    ).json()["result"]
    assert first["status"] == "waiting_input"
    assert first["follow_up_question"] == "어느 열인가요?"

    # 답이 없으면 진행하지 않는다.
    again = client.post(
        "/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS
    ).json()["result"]
    assert again["status"] == "waiting_input"
    assert calls == ["1번 명령"]

    resumed = client.post(
        "/excel-live/macro/step",
        json={"macro_id": plan["macro_id"], "answer": "B열이요"},
        headers=HEADERS,
    ).json()["result"]

    assert calls == ["1번 명령", "B열이요"]
    assert resumed["status"] == "running"


def test_failure_halts_and_keeps_finished_work(monkeypatch, excel_service):
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령", "3번 명령"])

    def _handler(message):
        if message == "2번 명령":
            return ExcelLiveActionResponse(
                ok=False, action="excel_live.write_range", result={}, reason="범위를 찾지 못했습니다"
            )
        return _ok(message)

    _stub_command(monkeypatch, _handler)

    client.post("/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS)
    body = client.post(
        "/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS
    ).json()

    assert body["ok"] is False
    result = body["result"]
    assert result["status"] == "halted"
    assert result["steps"][0]["status"] == "done", "먼저 끝난 작업은 보존한다"
    assert result["steps"][1]["status"] == "failed"
    assert result["steps"][2]["status"] == "pending"


def test_skip_current_continues_past_the_failed_step(monkeypatch, excel_service):
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령"])

    def _handler(message):
        if message == "1번 명령":
            return ExcelLiveActionResponse(
                ok=False, action="excel_live.write_range", result={}, reason="실패"
            )
        return _ok(message)

    calls = _stub_command(monkeypatch, _handler)

    client.post("/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS)
    resumed = client.post(
        "/excel-live/macro/step",
        json={"macro_id": plan["macro_id"], "skip_current": True},
        headers=HEADERS,
    ).json()["result"]

    assert calls == ["1번 명령", "2번 명령"]
    assert resumed["status"] == "done"


def test_abort_with_rollback_restores_the_macro_backup(monkeypatch, excel_service):
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령"])
    _stub_command(monkeypatch, _ok)
    client.post("/excel-live/macro/step", json={"macro_id": plan["macro_id"]}, headers=HEADERS)

    resp = client.post(
        "/excel-live/macro/abort",
        json={"macro_id": plan["macro_id"], "rollback": True},
        headers=HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["rolled_back"] is True
    assert excel_service.restore_calls == [r"C:\backups\macro.xlsx"]
    # 중단하면 실행 정보를 남기지 않는다.
    assert plan["macro_id"] not in excel_live_router._macro_runs


def test_abort_without_backup_refuses_rollback(monkeypatch, excel_service):
    """승인 전에는 백업이 없다 — 되돌릴 게 없다고 분명히 말해야 한다."""
    plan = _plan_macro(monkeypatch, ["1번 명령", "2번 명령"])

    resp = client.post(
        "/excel-live/macro/abort",
        json={"macro_id": plan["macro_id"], "rollback": True},
        headers=HEADERS,
    )

    assert resp.status_code == 400


def test_step_on_unknown_macro_is_not_found():
    resp = client.post(
        "/excel-live/macro/step", json={"macro_id": "does-not-exist"}, headers=HEADERS
    )
    assert resp.status_code == 404
