"""릴레이 라우팅/페어링 통합 테스트 (Starlette TestClient WebSocket).

검증:
  1) /health
  2) 페어링 start → complete (일회성 code, 잘못된 code는 404)
  3) 모바일→데스크톱 / 데스크톱→모바일 프레임이 원본 그대로 브리지되는가
  4) relay가 payload를 건드리지 않는가(content-blind: 받은 텍스트 == 보낸 텍스트)
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from oc_protocol import ChatUserMsg, Direction, Envelope, TokenDelta
from oc_relay.app import create_app
from oc_shared import encode_envelope


def _recv_frame(ws) -> dict:
    """presence control 메시지를 건너뛰고 Envelope 프레임만 반환."""
    while True:
        msg = ws.receive_json()
        if "payload" in msg:  # Envelope
            return msg
        # {"control": ...} presence 메시지는 스킵


def test_health_and_pairing() -> None:
    client = TestClient(create_app())
    assert client.get("/health").json()["status"] == "ok"

    start = client.post("/pair/start").json()
    pairing_id, code = start["pairing_id"], start["code"]
    assert pairing_id and code

    # 잘못된 코드 → 404
    assert client.post("/pair/complete", json={"code": "deadbeef"}).status_code == 404

    # 정상 완료 → 같은 pairing_id
    completed = client.post("/pair/complete", json={"code": code}).json()
    assert completed["pairing_id"] == pairing_id

    # 일회성: 같은 코드 재사용은 실패
    assert client.post("/pair/complete", json={"code": code}).status_code == 404


def test_unbound_pairing_rejected() -> None:
    client = TestClient(create_app())
    # 페어링하지 않은 임의 pairing_id로 접속 시도 → relay가 닫는다
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/mobile?pairing_id=nope") as ws:
            ws.receive_text()  # 닫힘


def test_bidirectional_relay_content_blind() -> None:
    client = TestClient(create_app())
    start = client.post("/pair/start").json()
    pairing_id = start["pairing_id"]
    client.post("/pair/complete", json={"code": start["code"]})

    with (
        client.websocket_connect(f"/ws/desktop?pairing_id={pairing_id}") as dws,
        client.websocket_connect(f"/ws/mobile?pairing_id={pairing_id}") as mws,
    ):
        # 모바일 → 데스크톱
        up = Envelope(
            pairing_id=pairing_id,
            direction=Direction.to_desktop,
            seq=1,
            payload=ChatUserMsg(client_msg_id="m1", text="엑셀 정리해줘"),
        )
        up_raw = encode_envelope(up)
        mws.send_text(up_raw)
        got_up = _recv_frame(dws)
        assert got_up["payload"]["type"] == "chat_user_msg"
        assert got_up["payload"]["text"] == "엑셀 정리해줘"

        # 데스크톱 → 모바일 (토큰 스트리밍 조각)
        down = Envelope(
            pairing_id=pairing_id,
            direction=Direction.to_mobile,
            seq=1,
            payload=TokenDelta(stream_id="s1", index=0, text="네, "),
        )
        down_raw = encode_envelope(down)
        dws.send_text(down_raw)
        got_down_raw = None
        while got_down_raw is None:
            m = mws.receive_json()
            if "payload" in m:
                got_down_raw = m
        assert got_down_raw["payload"]["type"] == "token_delta"
        assert got_down_raw["payload"]["text"] == "네, "
