"""
Discord 어댑터 라우터 — Phase 3 (Private-Claw).

엔드포인트:
  POST /discord/setup   — bot_token 저장
  GET  /discord/status  — 연결 상태
  POST /discord/start   — 디스코드 봇 시작
  POST /discord/stop    — 디스코드 봇 중지
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar.services.keyring_service import KeyringService
from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["discord"])

_keyring = KeyringService()
_audit = AuditService()

DISCORD_TOKEN_KEY = "discord_bot_token"
DISCORD_GUILD_ID_KEY = "discord_guild_id"
DISCORD_ALLOWED_USERS_KEY = "discord_allowed_user_ids"
DISCORD_BOT_USERNAME_KEY = "discord_bot_username"

# 싱글턴 어댑터 인스턴스
_discord_adapter: Any | None = None


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class DiscordSetupRequest(BaseModel):
    """Discord 봇 토큰 설정 요청."""
    token: str
    allowed_guild_id: str | None = None
    allowed_user_ids: list[str] | None = None  # Zero-Trust 화이트리스트 (선택)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/setup")
async def discord_setup(req: DiscordSetupRequest) -> dict:
    """
    Discord Bot Token을 저장하고 연결 테스트를 수행한다.

    Returns::

        {"ok": True, "bot_username": str} 또는 {"ok": False, "error": str}
    """
    import httpx

    # 연결 테스트: Discord /users/@me API 호출
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {req.token}"},
            )
    except Exception as e:
        return {"ok": False, "error": f"Discord API 연결 실패: {e}"}

    if resp.status_code != 200:
        return {"ok": False, "error": f"잘못된 봇 토큰: HTTP {resp.status_code}"}

    bot_info = resp.json()
    _keyring.store(DISCORD_TOKEN_KEY, req.token)
    if req.allowed_guild_id:
        _keyring.store(DISCORD_GUILD_ID_KEY, req.allowed_guild_id)
    if req.allowed_user_ids:
        import json
        _keyring.store(DISCORD_ALLOWED_USERS_KEY, json.dumps(req.allowed_user_ids))

    # bot_username 영속 저장 — status 응답에서 딥링크 생성에 사용
    discord_username = bot_info.get("username", "")
    if discord_username:
        _keyring.store(DISCORD_BOT_USERNAME_KEY, discord_username)
    _audit.log("discord_setup", f"bot={discord_username or '?'}")

    return {
        "ok": True,
        "bot_username": discord_username or None,
        "bot_id": bot_info.get("id", ""),
    }


@router.get("/status")
async def discord_status() -> dict:
    """Discord 봇 연결 상태와 bot_username을 반환한다."""
    global _discord_adapter

    token = _keyring.retrieve(DISCORD_TOKEN_KEY)
    guild_id = _keyring.retrieve(DISCORD_GUILD_ID_KEY)
    bot_username = _keyring.retrieve(DISCORD_BOT_USERNAME_KEY)

    return {
        "configured": bool(token),
        "running": _discord_adapter is not None and _discord_adapter.is_running(),
        "has_token": bool(token),
        "allowed_guild_id": guild_id or None,
        "bot_username": bot_username or None,
    }


@router.post("/start")
async def discord_start() -> dict:
    """Discord 봇을 시작한다."""
    global _discord_adapter

    token = _keyring.retrieve(DISCORD_TOKEN_KEY)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Discord 봇 토큰이 설정되지 않았습니다. POST /discord/setup을 먼저 호출하세요.",
        )

    if _discord_adapter and _discord_adapter.is_running():
        return {"status": "already_running"}

    guild_id = _keyring.retrieve(DISCORD_GUILD_ID_KEY)

    try:
        import json
        allowed_raw = _keyring.retrieve(DISCORD_ALLOWED_USERS_KEY)
        allowed_user_ids = json.loads(allowed_raw) if allowed_raw else None

        from office_claw_sidecar.messenger.discord_adapter import DiscordAdapter
        _discord_adapter = DiscordAdapter(
            token=token,
            allowed_guild_id=guild_id or None,
            allowed_user_ids=allowed_user_ids,
        )
        result = await _discord_adapter.start()
        _audit.log("discord_start", "bot")
        return result
    except Exception as e:
        logger.error("[Discord] 봇 시작 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"Discord 봇 시작 실패: {e}")


@router.post("/stop")
async def discord_stop() -> dict:
    """Discord 봇을 중지한다."""
    global _discord_adapter

    if not _discord_adapter or not _discord_adapter.is_running():
        return {"status": "not_running"}

    result = await _discord_adapter.stop()
    _discord_adapter = None
    _audit.log("discord_stop", "bot")
    return result
