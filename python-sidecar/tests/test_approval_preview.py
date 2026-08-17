"""승인 카드 프리뷰 — 어디에 적용되는지 모른 채 승인하게 하지 않는다 (로드맵 2-3).

2026-08-17 실측: "선택 범위에 경계선을 적용합니다"만 보고 승인했더니 A1:M201
2,613셀에 적용됐다 — 영향 범위는 실행 후에야 알 수 있었다. MS·Google 모두 실행 전
영향 범위 표시를 신뢰 수단으로 출시했다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as router
from office_claw_sidecar.routers.excel_live import _step_preview_line, _step_target

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_router import _FakeExcelService

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


class TestStepTarget:
    def test_a_bound_range_is_shown(self):
        assert _step_target({"target_range": "A1:M201"}) == "A1:M201"

    def test_symbolic_targets_are_humanized(self):
        assert _step_target({"target_range": "__USED_RANGE__"}) == "데이터가 있는 전체 범위"
        assert _step_target({"start_cell": "__ACTIVE_CELL__"}) == "현재 셀"

    def test_the_sheet_name_is_included(self):
        assert _step_target({"sheet_name": "매출", "range_ref": "F2"}) == "매출 시트 F2"

    def test_destructive_steps_carry_a_warning(self):
        line = _step_preview_line(3, "excel_live.clear_range", {"target_range": "A1:D9"})
        assert line.startswith("3. ⚠ ")
        assert "A1:D9" in line

    def test_formatting_steps_do_not(self):
        line = _step_preview_line(1, "excel_live.apply_border", {"target_range": "A1:D9"})
        assert "⚠" not in line


class TestApprovalCardContent:
    @pytest.fixture(autouse=True)
    def _service(self, monkeypatch):
        fake = _FakeExcelService()
        monkeypatch.setattr(router, "get_excel_live_service", lambda: fake)
        router._pending_operation_slots.clear()

        async def _no_llm(*a, **k):
            raise ValueError("skip")

        monkeypatch.setattr(router, "parse_excel_live_command", _no_llm)

    def test_a_multi_step_plan_lists_every_step_with_its_target(self):
        # "표 없애줘" 3단계 — 각 단계에 바인더가 확정한 범위가 붙어야 한다.
        body = client.post(
            "/excel-live/command",
            json={
                "message": "A1:D9 표 없애줘",
                "workbook_id": r"C:\work\sales.xlsx",
                "session_id": "sess-preview",
                "approve": False,
            },
            headers=HEADERS,
        ).json()
        assert body["approval_required"] is True
        summary = body["pending_approval"]["summary"]
        assert "다음 3단계를 실행합니다" in summary
        assert summary.count("A1:D9") == 3, summary
        assert "⚠" in summary  # clear_range 단계 경고
        assert "되돌리기" in summary
        # 제목이 원시 액션 문자열이 아니라 한국어다.
        assert body["pending_approval"]["tool_display_name"] == "테두리 적용"

    def test_a_single_step_write_shows_its_cell(self):
        body = client.post(
            "/excel-live/command",
            json={
                "message": "C3에 120 입력해줘",
                "workbook_id": r"C:\work\sales.xlsx",
                "session_id": "sess-preview-2",
                "approve": False,
            },
            headers=HEADERS,
        ).json()
        assert body["approval_required"] is True
        assert "C3" in body["pending_approval"]["summary"]
        assert body["pending_approval"]["tool_display_name"] == "값 입력"
