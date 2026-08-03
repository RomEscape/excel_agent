"""FastAPI 릴레이 앱 — content-blind WebSocket 브리지.

핵심: relay는 Envelope의 payload를 절대 파싱하지 않는다. `parse_routing`으로 라우팅 헤더만
읽고, 수신한 '원본 텍스트 프레임'을 그대로 상대편 소켓으로 전달한다. presence(온/오프라인)만
relay 자체 control 메시지로 통지한다(E2E payload와 분리되어 content-blind를 깨지 않는다).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from oc_shared import parse_routing

from .pairing import PairingRegistry
from .rate_limit import RateLimiter
from .routing import Role, SessionRegistry

# 정책상 잘못된 접속 종료 코드 (RFC 6455 사용자 정의 영역)
WS_UNAUTHORIZED = 4401


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def client_key(request: Request, *, trust_proxy: bool) -> str:
    """rate-limit 키 — 클라이언트 IP.

    기본은 소켓 peer 주소다. `X-Forwarded-For`는 클라이언트가 마음대로 위조할 수 있어
    무조건 신뢰하면 rate-limit이 통째로 무력화된다(헤더만 바꿔가며 무한 시도 가능).
    relay를 리버스 프록시 뒤에 두고 **그 프록시가 들어오는 XFF를 덮어쓰도록** 설정한
    경우에만 `RELAY_TRUST_PROXY=1`로 켠다.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        origin = forwarded.split(",")[0].strip()
        if origin:
            return origin
    return request.client.host if request.client else "unknown"


class PairStartResp(BaseModel):
    pairing_id: str
    code: str
    # code가 유효한 초. 데스크톱이 QR에 카운트다운을 붙이고 만료 시 재발급하도록
    # 서버가 값을 알려준다 — TTL을 클라이언트가 하드코딩해 추측하면 어긋난다.
    expires_in: int


class PairCompleteReq(BaseModel):
    code: str


class PairCompleteResp(BaseModel):
    pairing_id: str


async def _send_control(ws: WebSocket, state: str) -> None:
    """relay 자체 presence control (Envelope 프레임과 구분되는 `control` 키)."""
    await ws.send_json({"control": "peer_status", "state": state})


def create_app(
    *,
    pairing: PairingRegistry | None = None,
    limiter: RateLimiter | None = None,
    trust_proxy: bool | None = None,
) -> FastAPI:
    """앱 생성. 인자는 테스트에서 가짜 clock을 주입하기 위한 구멍이다(운영은 기본값)."""
    app = FastAPI(title="kimdaeri relay", version="0.1.0")
    sessions = SessionRegistry()
    pairing = pairing or PairingRegistry()
    limiter = limiter or RateLimiter()
    trust_proxy = _env_flag("RELAY_TRUST_PROXY") if trust_proxy is None else trust_proxy
    app.state.sessions = sessions
    app.state.pairing = pairing
    app.state.limiter = limiter

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/pair/start", response_model=PairStartResp)
    async def pair_start(request: Request) -> PairStartResp:
        # /pair/start도 인증이 없다 — 제한이 없으면 무한 호출로 _pending을 불려
        # 메모리를 고갈시킬 수 있다. 정상 사용은 클릭당 1회라 넉넉한 예산이다.
        key = f"start:{client_key(request, trust_proxy=trust_proxy)}"
        if not limiter.allow(key):
            raise HTTPException(
                status_code=429,
                detail="페어링 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.",
                headers={"Retry-After": str(limiter.retry_after(key))},
            )
        pairing_id, code = pairing.start()
        return PairStartResp(
            pairing_id=pairing_id, code=code, expires_in=int(pairing.ttl_seconds)
        )

    @app.post("/pair/complete", response_model=PairCompleteResp)
    async def pair_complete(req: PairCompleteReq, request: Request) -> PairCompleteResp:
        # 무차별 대입 방어. code를 검사하기 **전에** 막아야 시도 자체가 비용을 갖는다.
        key = f"complete:{client_key(request, trust_proxy=trust_proxy)}"
        if not limiter.allow(key):
            raise HTTPException(
                status_code=429,
                detail="페어링 시도가 너무 잦습니다. 잠시 후 다시 시도하세요.",
                headers={"Retry-After": str(limiter.retry_after(key))},
            )
        pairing_id = pairing.complete(req.code)
        if pairing_id is None:
            raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 페어링 코드")
        limiter.reset(key)  # 정상 페어링 성공 → 카운터를 비운다
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
