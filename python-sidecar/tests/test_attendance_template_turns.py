"""출석부 대화가 스크린샷처럼 무너지지 않는가 — 그 대화 그대로 재현한다.

2026-08-17 GUI 실측 (`logs/chat_log.jsonl` 18:38):

    사용자: "A1:D13 여기에 출석부를 본격적으로 만들기 시작하자"
    봇   : "근태표는 일별/월별 중 어떤 형식으로 만들까요?"   ← 출석부라니까 근태표?
    사용자: "일별로 만들어줘"
    봇   : "근태표는 일별/월별 중 어떤 형식으로 만들까요?"   ← 같은 질문을 또
    사용자: "일별"
    봇   : "표 크기를 확정하지 못해 5행 5열, A1 기준으로…"   ← 프리셋도 범위도 다 버림

원인 셋: ① 질문의 선택지(일별/월별)를 해석하는 코드가 아예 없었다(긍정어만 통과).
② 붙여넣기 범위가 context_range가 아니라 문장 인라인이라 크기 소비 로직을 안 탔다.
③ 질문 문구가 사용자의 낱말(출석부)이 아니라 프리셋 이름(근태표)이었다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router
from office_claw_sidecar.services.excel_live_table_presets import (
    find_variant,
    match_table_preset,
    preset_follow_up,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_router import _FakeExcelService

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


class _RecordingService(_FakeExcelService):
    def __init__(self):
        super().__init__()
        self.tables: list[dict] = []
        self.writes: list[dict] = []

    def create_table(self, workbook_id, sheet_name, start_cell, rows, cols, with_border):
        self.tables.append({"start_cell": start_cell, "rows": int(rows), "cols": int(cols)})
        return super().create_table(workbook_id, sheet_name, start_cell, rows, cols, with_border)

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d, **kwargs):
        self.writes.append({"start_cell": start_cell, "values_2d": values_2d})
        return super().write_range(workbook_id, sheet_name, start_cell, values_2d, **kwargs)


@pytest.fixture()
def service(monkeypatch):
    fake = _RecordingService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._pending_create_table_slots.clear()

    async def _no_llm(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _no_llm)
    return fake


def _turn(message: str, session: str = "sess-attendance") -> dict:
    """앱과 같은 경로: 승인 카드가 뜨면 approval_id로 `/approval`을 부른다.

    같은 명령을 approve=True로 재전송하는 방식은 여기서 못 쓴다 — 슬롯은 1차
    요청에서 이미 소비됐고, 재전송은 처음부터 다시 도는 별개 실행이 된다.
    """
    payload = {"message": message, "session_id": session, "approve": False}
    body = client.post("/excel-live/command", json=payload, headers=HEADERS).json()
    approval_id = (body.get("pending_approval") or {}).get("approval_id")
    if body.get("approval_required") and approval_id:
        body = client.post(
            "/excel-live/approval",
            json={"approval_id": approval_id, "approved": True},
            headers=HEADERS,
        ).json()
    return body


class TestTheScreenshotConversation:
    FIRST = "A1:D13 여기에 출석부를 본격적으로 만들기 시작하자"

    def test_the_question_uses_the_users_word(self, service):
        body = _turn(self.FIRST)
        assert body["result"].get("ask_follow_up") is True
        q = str(body["result"].get("follow_up_question"))
        assert "출석부" in q, f"사용자는 출석부라고 했다: {q}"
        assert "근태표" not in q

    def test_the_answer_is_understood_and_the_table_lands_on_the_pasted_range(self, service):
        _turn(self.FIRST)
        body = _turn("일별로 만들어줘")
        # 같은 질문을 또 하면 안 된다.
        assert not body["result"].get("ask_follow_up"), (
            f"답을 해석 못 하고 또 묻는다: {body.get('reason')}"
        )
        assert service.tables, "표가 생성되지 않았다"
        table = service.tables[-1]
        # 붙여넣은 A1:D13 = 13행 × 4열이 곧 표 크기다. 5×5 기본값이 아니라.
        assert (table["start_cell"], table["rows"], table["cols"]) == ("A1", 13, 4), table
        # 일별 형식의 헤더가 실제로 들어갔는가 (폭 4에 맞춰 앞에서부터).
        header_rows = [w["values_2d"][0] for w in service.writes if w.get("values_2d")]
        assert ["날짜", "이름", "출근 시간", "퇴근 시간"] in header_rows, header_rows

    def test_an_off_menu_answer_still_moves_forward(self, service):
        """선택지 밖의 답("아무거나 알아서")에 같은 질문을 또 하면 대화가 제자리를 돈다."""
        _turn(self.FIRST, session="sess-offmenu")
        body = _turn("아무거나 알아서 해줘", session="sess-offmenu")
        assert not body["result"].get("ask_follow_up"), body.get("reason")
        assert service.tables, "기본형으로라도 만들어야 한다"
        assert service.tables[-1]["rows"] == 13  # 범위는 그대로 존중

    def test_a_variant_in_the_first_message_skips_the_question(self, service):
        body = _turn("A1:D13 여기에 일별 출석부 만들어줘", session="sess-oneshot")
        assert not body["result"].get("ask_follow_up"), body.get("reason")
        assert service.tables and service.tables[-1]["rows"] == 13


class TestPresetPlumbing:
    def test_the_variant_is_found_inside_a_sentence(self):
        preset = match_table_preset("출석부 만들어줘")
        assert preset is not None
        name, headers = find_variant(preset, "일별로 만들어줘")
        assert name == "일별"
        assert headers[0] == "날짜"

    def test_no_variant_returns_none(self):
        preset = match_table_preset("출석부 만들어줘")
        assert find_variant(preset, "아무거나") is None
        assert find_variant(None, "일별") is None

    @pytest.mark.parametrize(
        ("message", "expected_name"),
        [("출석부 만들어줘", "출석부"), ("근태 관리표 만들어줘", "근태표")],
    )
    def test_the_question_mirrors_the_users_word(self, message, expected_name):
        preset = match_table_preset(message)
        q = preset_follow_up(preset, message)
        assert q.startswith(expected_name), q
        assert "{name}" not in q
