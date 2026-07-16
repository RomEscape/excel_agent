"""RelaySession 프레임 디스패치 단위 테스트 (소켓/Ollama 없이).

에이전트 진입점(run_/resume_excel_tool_turn)을 monkeypatch로 대체하고, 주입한 send_raw로
나가는 프레임을 수집해 검증한다. async는 asyncio.run으로 구동(pytest-asyncio 불필요).
"""

import asyncio
import json

from oc_protocol import (
    AgentState,
    ApprovalResponse,
    ChatUserMsg,
    Direction,
    Envelope,
    Ping,
)
from oc_shared import decode_envelope, encode_envelope

import office_claw_sidecar.services.relay_client as rc


class Collector:
    """send_raw 대역 — 나가는 raw 프레임을 모은다."""

    def __init__(self) -> None:
        self.raw: list[str] = []

    async def __call__(self, raw: str) -> None:
        self.raw.append(raw)


def _frames(col: Collector) -> list:
    return [decode_envelope(r).payload for r in col.raw]


def _types(col: Collector) -> list[str]:
    return [type(f).__name__ for f in _frames(col)]


def _incoming(pairing_id: str, frame, seq: int = 1) -> str:
    return encode_envelope(
        Envelope(
            pairing_id=pairing_id,
            direction=Direction.to_desktop,
            seq=seq,
            payload=frame,
        )
    )


def test_chat_streams_and_status(monkeypatch):
    async def fake_run(**kwargs):
        return {
            "type": "chat",
            "assistant_text": f"echo:{kwargs['message']}",
            "executed": [],
        }

    monkeypatch.setattr(rc, "run_excel_tool_turn", fake_run)

    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())
    asyncio.run(
        sess.handle_incoming(_incoming("p1", ChatUserMsg(client_msg_id="c1", text="hi")))
    )

    frames = _frames(col)
    assert _types(col) == ["AgentStatus", "TokenDelta", "StreamEnd", "AgentStatus"]
    assert frames[0].state == AgentState.thinking
    assert frames[1].text == "echo:hi" and frames[1].stream_id == "c1"
    assert frames[2].reason == "complete"
    assert frames[3].state == AgentState.idle


def test_approval_round_trip(monkeypatch):
    async def fake_run(**kwargs):
        return {
            "type": "approval",
            "action": "delete_sheet",
            "params": {"name": "Sheet1"},
            "sheet_name": None,
            "reason": "시트 삭제는 확인이 필요합니다",
            "executed": [],
            "resume": {"messages": [{"role": "system", "content": "x"}], "tool_call_id": "t1"},
        }

    async def fake_resume(**kwargs):
        return {"type": "chat", "assistant_text": "삭제 완료", "executed": []}

    monkeypatch.setattr(rc, "run_excel_tool_turn", fake_run)
    monkeypatch.setattr(rc, "resume_excel_tool_turn", fake_resume)

    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())

    async def scenario():
        await sess.handle_incoming(
            _incoming("p1", ChatUserMsg(client_msg_id="c1", text="시트 지워"))
        )
        # 첫 턴: thinking → ApprovalRequest → idle
        assert _types(col) == ["AgentStatus", "ApprovalRequest", "AgentStatus"]
        req = _frames(col)[1]
        assert req.command == "delete_sheet"
        rid = req.request_id
        col.raw.clear()
        await sess.handle_incoming(
            _incoming("p1", ApprovalResponse(request_id=rid, approved=True), seq=2)
        )

    asyncio.run(scenario())

    frames = _frames(col)
    assert _types(col) == ["AgentStatus", "TokenDelta", "StreamEnd", "AgentStatus"]
    assert frames[0].state == AgentState.remote_controlling
    assert frames[1].text == "삭제 완료"
    assert frames[3].state == AgentState.idle


def test_rejected_approval(monkeypatch):
    async def fake_run(**kwargs):
        return {
            "type": "approval",
            "action": "delete_sheet",
            "params": {},
            "sheet_name": None,
            "reason": "확인 필요",
            "executed": [],
            "resume": {"messages": [], "tool_call_id": "t"},
        }

    monkeypatch.setattr(rc, "run_excel_tool_turn", fake_run)

    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())

    async def scenario():
        await sess.handle_incoming(
            _incoming("p1", ChatUserMsg(client_msg_id="c1", text="지워"))
        )
        rid = _frames(col)[1].request_id
        col.raw.clear()
        await sess.handle_incoming(
            _incoming("p1", ApprovalResponse(request_id=rid, approved=False), seq=2)
        )

    asyncio.run(scenario())

    frames = _frames(col)
    assert _types(col) == ["StreamEnd", "AgentStatus"]
    assert frames[0].reason == "aborted"
    assert frames[1].state == AgentState.idle


def test_ping_pong():
    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())
    asyncio.run(sess.handle_incoming(_incoming("p1", Ping(nonce="abc"))))
    frames = _frames(col)
    assert _types(col) == ["Pong"] and frames[0].nonce == "abc"


def test_presence_control_ignored():
    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())
    asyncio.run(
        sess.handle_incoming(json.dumps({"control": "peer_status", "state": "online"}))
    )
    assert col.raw == []  # presence는 응답 프레임을 만들지 않는다


def test_chat_streams_incrementally(monkeypatch):
    async def fake_run(**kwargs):
        # 에이전트가 토큰을 조각조각 흘려보낸다
        on_token = kwargs["on_token"]
        await on_token("안녕")
        await on_token("하세요")
        return {"type": "chat", "assistant_text": "안녕하세요", "executed": []}

    monkeypatch.setattr(rc, "run_excel_tool_turn", fake_run)

    col = Collector()
    sess = rc.RelaySession("p1", col, llm_service=object())
    asyncio.run(
        sess.handle_incoming(_incoming("p1", ChatUserMsg(client_msg_id="c1", text="hi")))
    )

    frames = _frames(col)
    # thinking → TokenDelta("안녕",0) → TokenDelta("하세요",1) → StreamEnd → idle
    assert _types(col) == [
        "AgentStatus",
        "TokenDelta",
        "TokenDelta",
        "StreamEnd",
        "AgentStatus",
    ]
    assert frames[1].text == "안녕" and frames[1].index == 0
    assert frames[2].text == "하세요" and frames[2].index == 1
    assert frames[3].reason == "complete"
    assert frames[4].state == AgentState.idle
    # 스트리밍했으니 완성본 재전송(폴백) 없음 — TokenDelta는 정확히 2개
    assert sum(1 for t in _types(col) if t == "TokenDelta") == 2
