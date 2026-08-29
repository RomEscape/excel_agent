"""드래그한 영역이 곧 표 크기다 (①의 나머지 절반).

2026-08-16 GUI 실측: A1:D9를 끌어 놓고 "이 부분에 표 만들어줘"라고 했는데
"표 크기와 헤더를 알려주세요"로 되물었다. 로그를 보면 범위는 제대로 도착해 있었다:

    [observation] context_range='A1:D9'
    [table_slot]  rows=None cols=None start_cell='A1' need_follow_up=True

`start_cell`을 만들면서 `.split(":")[0]`으로 왼쪽 위 칸만 남기고 크기를 버린 탓이다.
끌어 놓고 다시 크기를 불러야 하면 드래그가 아무 의미가 없다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router

client = TestClient(app)
HEADERS = {"X-Auth-Token": "dev-token", "Authorization": "Bearer dev-token"}


class _FakeService:
    """표를 만들 수 있는 최소 서비스."""

    engine = "file"

    def __init__(self, selection: str = "A1:D9") -> None:
        self.created: list[dict] = []
        self._selection = selection

    def is_available(self) -> bool:
        return True

    def list_workbooks(self):
        return [{"name": "테스트.xlsx", "id": "테스트.xlsx"}]

    def get_selected_workbook_id(self):
        return "테스트.xlsx"

    def list_sheets(self, _wb):
        return {"sheets": ["Sheet1"], "active_sheet": "Sheet1"}

    def get_used_range_ref(self, _wb, _sheet):
        return "A1"

    def get_active_selection_ref(self, _wb, _sheet):
        return self._selection

    def read_range(self, _wb, _sheet, _ref):
        return {"values": []}

    read_computed_range = read_range

    def create_table(self, workbook_id, sheet_name, start_cell, rows, cols, headers=None, **_kw):
        self.created.append(
            {"start_cell": start_cell, "rows": rows, "cols": cols, "headers": headers}
        )
        return {"address": start_cell, "rows": rows, "cols": cols}


@pytest.fixture(autouse=True)
def _clean_slots():
    excel_live_router._pending_create_table_slots.clear()
    yield
    excel_live_router._pending_create_table_slots.clear()


def _post(message: str, *, context_range: str | None, session: str, approve: bool = False):
    return client.post(
        "/excel-live/command",
        json={
            "message": message,
            "session_id": session,
            "context_range": context_range,
            "approve": approve,
        },
        headers=HEADERS,
    )


def _args(resp):
    """승인 카드의 args_preview. 되묻기면 None."""
    body = resp.json()
    pending = body.get("pending_approval") or {}
    return pending.get("args_preview")


def _follow_up(resp):
    return str((resp.json().get("result") or {}).get("follow_up_question") or "")


class TestDragBecomesTableSize:
    """슬롯이 아니라 **응답**을 본다 — 슬롯은 완성되면 소비돼 사라진다."""

    def test_a_dragged_range_becomes_the_table_size(self, monkeypatch):
        monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeService())
        resp = _post("이 부분에 표 만들어줘", context_range="A1:D9", session="drag-1")
        assert resp.status_code == 200
        args = _args(resp)
        assert args is not None, "되묻지 말고 바로 만들 수 있어야 한다"
        # A1:D9 → 9행 4열
        assert (args["rows"], args["cols"]) == (9, 4)
        assert str(args["start_cell"]).upper() == "A1"

    def test_it_does_not_ask_for_a_size_it_already_knows(self, monkeypatch):
        monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeService())
        resp = _post("이 부분에 표 만들어줘", context_range="A1:D9", session="drag-2")
        assert "표 크기" not in _follow_up(resp)

    def test_a_size_in_the_sentence_wins_over_the_drag(self, monkeypatch):
        monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeService())
        resp = _post("3행 2열 표 만들어줘", context_range="A1:D9", session="drag-3")
        args = _args(resp)
        assert args is not None
        # 사용자가 말한 크기가 드래그보다 우선이다.
        assert (args["rows"], args["cols"]) == (3, 2)

    def test_a_single_cell_selection_is_not_a_size(self, monkeypatch):
        # 커서가 한 칸에 있을 뿐이면 "여기서 시작"이지 "1행 1열 표"가 아니다.
        monkeypatch.setattr(
            excel_live_router, "get_excel_live_service", lambda: _FakeService("B2")
        )
        resp = _post("여기에 표 만들어줘", context_range="B2", session="drag-4")
        assert _args(resp) is None, "한 칸을 크기로 삼으면 안 된다"
        assert "크기" in _follow_up(resp)

    def test_a_size_given_after_a_clarify_is_used(self, monkeypatch):
        """되묻기가 한 번 끼어도 다음 턴의 크기를 받아 만든다."""
        monkeypatch.setattr(
            excel_live_router, "get_excel_live_service", lambda: _FakeService("B2")
        )
        first = _post("여기에 표 만들어줘", context_range="B2", session="drag-5")
        assert _args(first) is None
        second = _post("4행 3열", context_range="B2", session="drag-5")
        args = _args(second)
        assert args is not None
        assert (args["rows"], args["cols"]) == (4, 3)

    def test_the_live_selection_beats_a_stale_context_range(self, monkeypatch):
        """프론트가 보낸 옛 주소보다 지금 끌어 둔 영역이 이긴다 (①의 앞 절반)."""
        monkeypatch.setattr(
            excel_live_router, "get_excel_live_service", lambda: _FakeService("C3:F10")
        )
        resp = _post("여기에 표 만들어줘", context_range="A1:B2", session="drag-6")
        args = _args(resp)
        assert args is not None
        # C3:F10 → 8행 4열. 옛 주소 A1:B2(2행 2열)가 아니어야 한다.
        assert (args["rows"], args["cols"]) == (8, 4)
        assert str(args["start_cell"]).upper() == "C3"
