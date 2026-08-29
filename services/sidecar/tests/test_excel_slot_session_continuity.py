"""되묻기 슬롯이 다음 턴까지 살아남는가 — 세션 키 계약.

2026-08-16 실측(logs/chat_log.jsonl 마지막 두 턴):

    '내가 드래그한 영역에 출석부를 위한 표를 만들어줘'
        session_id='excel-live::stateless::a8abc818…'  → stage 'table_slot' 등록, 되묻기
    '일별로 만들어줄래?'
        session_id='excel-live::stateless::c844b85b…'  → pending_table_slot=false,
                                                          conversation_history=''

앱이 session_id를 안 보내서 사이드카가 **매 턴 새 stateless 키**를 발급했다. 슬롯과
대화 이력이 그 키에 매달려 있어 통째로 유실됐고, "일별로"가 맨바닥에서 재해석돼
정렬(sort_range) 명령이 됐다. 고친 곳은 앱(WorkspacePage의 excelSessionKey)이지만,
계약 자체는 여기서 못 박아 둔다 — 키가 다시 턴마다 달라지면 이 테스트가 먼저 깨진다.
"""

from __future__ import annotations

from office_claw_sidecar.routers.excel_live import (
    ExcelLiveCommandRequest,
    _slot_session_key,
)


def _req(message: str, session_id: str | None) -> ExcelLiveCommandRequest:
    return ExcelLiveCommandRequest(message=message, session_id=session_id)


class TestSlotSessionKey:
    def test_the_same_session_id_yields_the_same_key(self):
        """같은 세션이면 두 턴이 같은 슬롯 칸을 본다 — 되묻기가 이어지는 조건."""
        first = _slot_session_key(_req("출석부를 위한 표를 만들어줘", "excel-live::ui::abc"))
        second = _slot_session_key(_req("일별로 만들어줄래?", "excel-live::ui::abc"))
        assert first == second == "excel-live::ui::abc"

    def test_a_missing_session_id_isolates_every_turn(self):
        """id가 없으면 턴마다 다른 키다 — 이게 실측에서 슬롯을 날린 경로다."""
        first = _slot_session_key(_req("출석부를 위한 표를 만들어줘", None))
        second = _slot_session_key(_req("일별로 만들어줄래?", None))
        assert first != second
        assert first.startswith("excel-live::stateless::")

    def test_blank_and_whitespace_ids_are_treated_as_missing(self):
        # 앱이 빈 문자열을 보내면 "세션이 있다"고 착각해선 안 된다.
        for blank in ("", "   ", "\t"):
            assert _slot_session_key(_req("표 만들어줘", blank)).startswith(
                "excel-live::stateless::"
            )
