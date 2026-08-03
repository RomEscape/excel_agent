"""릴레이 라우팅/페어링 통합 테스트 (Starlette TestClient WebSocket).

검증:
  1) /health
  2) 페어링 start → complete (일회성 code, 잘못된 code는 404)
  3) 모바일→데스크톱 / 데스크톱→모바일 프레임이 원본 그대로 브리지되는가
  4) relay가 payload를 건드리지 않는가(content-blind: 받은 텍스트 == 보낸 텍스트)
  5) TTL 만료·rate-limit이 HTTP 레벨에서 동작하는가

TTL·rate-limit 단위 동작은 test_pairing.py / test_rate_limit.py가 따로 고정한다.
여기서는 앱에 제대로 결합됐는지(429/404 응답, Retry-After 헤더)만 본다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from oc_protocol import ChatUserMsg, Direction, Envelope, TokenDelta
from oc_relay.app import create_app
from oc_relay.pairing import PairingRegistry
from oc_relay.rate_limit import RateLimiter
from oc_shared import encode_envelope

from .conftest import FakeClock


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
    # 데스크톱이 카운트다운/재발급을 붙일 수 있도록 TTL을 함께 알려준다
    assert start["expires_in"] == 120

    # 잘못된 코드 → 404
    assert client.post("/pair/complete", json={"code": "deadbeef"}).status_code == 404

    # 정상 완료 → 같은 pairing_id
    completed = client.post("/pair/complete", json={"code": code}).json()
    assert completed["pairing_id"] == pairing_id

    # 일회성: 같은 코드 재사용은 실패
    assert client.post("/pair/complete", json={"code": code}).status_code == 404


def test_expired_code_rejected(clock: FakeClock) -> None:
    """QR을 띄워둔 채 방치하면 창이 닫혀야 한다 — TTL이 앱에 결합됐는지 확인."""
    client = TestClient(
        create_app(pairing=PairingRegistry(ttl_seconds=120, clock=clock))
    )
    start = client.post("/pair/start").json()

    clock.advance(121)
    resp = client.post("/pair/complete", json={"code": start["code"]})
    assert resp.status_code == 404


def test_brute_force_is_rate_limited(clock: FakeClock) -> None:
    """무차별 대입 방어 — 예산을 넘기면 코드 검사 전에 429로 끊긴다."""
    client = TestClient(
        create_app(limiter=RateLimiter(max_attempts=3, window_seconds=60, clock=clock))
    )

    for _ in range(3):
        assert client.post("/pair/complete", json={"code": "00000000"}).status_code == 404

    blocked = client.post("/pair/complete", json={"code": "00000000"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"

    clock.advance(60)  # 창이 지나면 다시 열린다
    assert client.post("/pair/complete", json={"code": "00000000"}).status_code == 404


def test_successful_pairing_resets_rate_limit_budget(clock: FakeClock) -> None:
    """정상 사용자가 오타 몇 번 냈다고 다음 페어링까지 손해보면 안 된다."""
    client = TestClient(
        create_app(limiter=RateLimiter(max_attempts=3, window_seconds=60, clock=clock))
    )
    start = client.post("/pair/start").json()

    client.post("/pair/complete", json={"code": "deadbeef"})  # 실패 1회
    assert (
        client.post("/pair/complete", json={"code": start["code"]}).status_code == 200
    )

    # 성공으로 카운터가 비워져 예산이 다시 3회
    for _ in range(3):
        assert client.post("/pair/complete", json={"code": "00000000"}).status_code == 404
    assert client.post("/pair/complete", json={"code": "00000000"}).status_code == 429


def test_forged_forwarded_header_does_not_bypass_limit(clock: FakeClock) -> None:
    """XFF를 신뢰하지 않는 기본 설정에서는 헤더를 바꿔도 같은 키로 묶여야 한다."""
    client = TestClient(
        create_app(
            limiter=RateLimiter(max_attempts=2, window_seconds=60, clock=clock),
            trust_proxy=False,
        )
    )

    for i in range(2):
        client.post(
            "/pair/complete",
            json={"code": "00000000"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
    blocked = client.post(
        "/pair/complete",
        json={"code": "00000000"},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert blocked.status_code == 429


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
