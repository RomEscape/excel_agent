"""FastAPI 릴레이 앱 — content-blind WebSocket 브리지.

핵심: relay는 Envelope의 payload를 절대 파싱하지 않는다. `parse_routing`으로 라우팅 헤더만
읽고, 수신한 '원본 텍스트 프레임'을 그대로 상대편 소켓으로 전달한다. presence(온/오프라인)만
relay 자체 control 메시지로 통지한다(E2E payload와 분리되어 content-blind를 깨지 않는다).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from oc_shared import parse_routing

from .pairing import PairingRegistry
from .routing import Role, SessionRegistry

# 정책상 잘못된 접속 종료 코드 (RFC 6455 사용자 정의 영역)
WS_UNAUTHORIZED = 4401


class PairStartResp(BaseModel):
    pairing_id: str
    code: str


class PairCompleteReq(BaseModel):
    code: str


class PairCompleteResp(BaseModel):
    pairing_id: str


async def _send_control(ws: WebSocket, state: str) -> None:
    """relay 자체 presence control (Envelope 프레임과 구분되는 `control` 키)."""
    await ws.send_json({"control": "peer_status", "state": state})


def create_app() -> FastAPI:
    app = FastAPI(title="kimdaeri relay", version="0.1.0")
    sessions = SessionRegistry()
    pairing = PairingRegistry()
    app.state.sessions = sessions
    app.state.pairing = pairing

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/pair/start", response_model=PairStartResp)
    async def pair_start() -> PairStartResp:
        pairing_id, code = pairing.start()
        return PairStartResp(pairing_id=pairing_id, code=code)

    @app.post("/pair/complete", response_model=PairCompleteResp)
    async def pair_complete(req: PairCompleteReq) -> PairCompleteResp:
        pairing_id = pairing.complete(req.code)
        if pairing_id is None:
            raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 페어링 코드")
        return PairCompleteResp(pairing_id=pairing_id)

    async def relay_ws(ws: WebSocket, role: Role) -> None:
        await ws.accept()
        pairing_id = ws.query_params.get("pairing_id")
        # 접속 자격: 바인딩된 pairing_id만 허용 (MVP: 접속 인증. E2E는 별도 계층)
        if not pairing_id or not pairing.is_bound(pairing_id):
            await ws.close(code=WS_UNAUTHORIZED)
            return

        sess = sessions.connect(pairing_id, role, ws)
        peer = sess.peer(role)
        if peer is not None:
            # 상대가 이미 접속 중 → 양쪽에 online 통지
            await _send_control(ws, "online")
            await _send_control(peer, "online")

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    header = parse_routing(raw)  # content-blind: 라우팅 헤더만
                except Exception:
                    continue  # 파싱 불가 프레임은 무시(payload는 애초에 열지 않음)
                if header.pairing_id != pairing_id:
                    continue  # 세션 위조 방지
                target = sessions.peer(pairing_id, role)
                if target is not None:
                    await target.send_text(raw)  # 원본 그대로 전달
        except WebSocketDisconnect:
            pass
        finally:
            remaining = sessions.disconnect(pairing_id, role)
            if remaining is not None:
                try:
                    await _send_control(remaining, "offline")
                except Exception:
                    pass  # 상대도 이미 끊긴 경우

    @app.websocket("/ws/desktop")
    async def ws_desktop(ws: WebSocket) -> None:
        await relay_ws(ws, "desktop")

    @app.websocket("/ws/mobile")
    async def ws_mobile(ws: WebSocket) -> None:
        await relay_ws(ws, "mobile")

    return app


app = create_app()
