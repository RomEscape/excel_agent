"""실행 리포트 — 매 실행마다 "무엇을 어디에 어떻게"가 화면에 나온다.

2026-08-18 사용자 요구: "실행할 때마다 어떤 방식으로 수정을 진행하는지 나오게,
화면 정확성 최대치로. 이게 제일 중요한 것 같은데."

지금까지는 대표 액션 한 줄("[1/1] 완료되었습니다")만 보여서, 3단계가 실행돼도
무엇이 어디에 일어났는지 화면으로 알 수 없었다 — 8-17의 "완료라는데 화면 그대로"
사고들이 전부 이 불투명 위에서 커졌다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as router

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_router import _FakeExcelService

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


@pytest.fixture(autouse=True)
def _service(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(router, "get_excel_live_service", lambda: fake)
    router._pending_operation_slots.clear()

    async def _no_llm(*a, **k):
        raise ValueError("skip")

    monkeypatch.setattr(router, "parse_excel_live_command", _no_llm)
    return fake


def _run(message: str, session: str) -> dict:
    payload = {
        "message": message,
        "workbook_id": r"C:\work\sales.xlsx",
        "session_id": session,
        "approve": False,
    }
    body = client.post("/excel-live/command", json=payload, headers=HEADERS).json()
    aid = (body.get("pending_approval") or {}).get("approval_id")
    if body.get("approval_required") and aid:
        body = client.post(
            "/excel-live/approval", json={"approval_id": aid, "approved": True}, headers=HEADERS
        ).json()
    return body


class TestEveryStepIsReported:
    def test_a_multi_step_plan_reports_each_step_with_target(self):
        body = _run("A1:D9 표 없애줘", "sess-report-1")
        assert body["ok"] is True
        report = body["result"].get("execution_report", "")
        lines = report.splitlines()
        assert len([ln for ln in lines if ln and ln[0].isdigit()]) == 3, report
        assert "테두리 적용" in report and "배경색 변경" in report and "내용 비우기" in report
        # 각 단계에 실제 대상이 붙는다.
        assert report.count("A1:D9") == 3, report

    def test_a_single_write_reports_cell_and_count(self):
        body = _run("C3에 120 입력해줘", "sess-report-2")
        report = body["result"].get("execution_report", "")
        assert "값 입력" in report
        assert "C3" in report
        assert "값 1개 기록" in report
        # 한 단계면 번호를 붙이지 않는다.
        assert not report.startswith("1.")

    def test_bookkeeping_steps_are_not_listed(self):
        # 저장/시트 선택 같은 마무리 단계는 소음이다 — 편집 단계만 보고한다.
        body = _run("A1:D9 표 없애줘", "sess-report-3")
        report = body["result"].get("execution_report", "")
        assert "저장" not in report and "select" not in report.lower()
