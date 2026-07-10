"""중계 서버(relay) 연동 라우터 — 페어링 개시 + 연결 상태 조회.

데스크톱 UI가 이 엔드포인트로 페어링을 시작(QR용 code 수신)하고 연결 상태를 확인한다.
실제 프레임 왕복은 RelayClient(services/relay_client.py)가 백그라운드에서 담당한다.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from office_claw_sidecar.services.relay_client import (
    DEFAULT_RELAY_URL,
    load_relay_config,
    save_relay_config,
    start_relay_client,
    stop_relay_client,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class PairStartResponse(BaseModel):
    pairing_id: str
    code: str  # 모바일이 스캔할 QR에 담을 일회성 코드
    relay_url: str


class RelayStatusResponse(BaseModel):
    enabled: bool
    relay_url: str
    pairing_id: str | None
    connected: bool


@router.post("/pair", response_model=PairStartResponse)
async def start_pairing(request: Request) -> PairStartResponse:
    """relay에 페어링을 개시하고 pairing_id/code를 받아 저장한 뒤 클라이언트를 재기동한다."""
    cfg = load_relay_config()
    relay_url = str(cfg.get("relay_url", DEFAULT_RELAY_URL)).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{relay_url}/pair/start")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"relay 페어링 개시 실패: {exc}"
        ) from exc

    pairing_id = data["pairing_id"]
    cfg.update({"relay_url": relay_url, "pairing_id": pairing_id, "enabled": True})
    save_relay_config(cfg)

    # 새 pairing_id로 클라이언트를 재기동(기존 것이 있으면 정리 후 재생성)
    await stop_relay_client(request.app)
    await start_relay_client(request.app)

    return PairStartResponse(
        pairing_id=pairing_id, code=data["code"], relay_url=relay_url
    )


@router.get("/status", response_model=RelayStatusResponse)
async def get_status(request: Request) -> RelayStatusResponse:
    cfg = load_relay_config()
    client = getattr(request.app.state, "relay_client", None)
    return RelayStatusResponse(
        enabled=bool(cfg.get("enabled")),
        relay_url=str(cfg.get("relay_url", DEFAULT_RELAY_URL)),
        pairing_id=cfg.get("pairing_id"),
        connected=bool(getattr(client, "connected", False)),
    )


@router.post("/disconnect")
async def disconnect(request: Request) -> dict:
    """연동을 중지하고(enabled=False) 백그라운드 클라이언트를 정리한다."""
    cfg = load_relay_config()
    cfg["enabled"] = False
    save_relay_config(cfg)
    await stop_relay_client(request.app)
    return {"ok": True}
