"""
Slack 어댑터 라우터 — Phase 3 (Private-Claw).

엔드포인트:
  POST /slack/setup   — bot_token, app_token 저장
  GET  /slack/status  — 연결 상태
  POST /slack/start   — 슬랙 봇 시작
  POST /slack/stop    — 슬랙 봇 중지
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar.services.keyring_service import KeyringService
from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["slack"])

_keyring = KeyringService()
_audit = AuditService()

SLACK_BOT_TOKEN_KEY = "slack_bot_token"
SLACK_APP_TOKEN_KEY = "slack_app_token"
SLACK_ALLOWED_USERS_KEY = "slack_allowed_user_ids"
SLACK_BOT_USERNAME_KEY = "slack_bot_username"

# 싱글턴 어댑터 인스턴스
_slack_adapter: Any | None = None


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class SlackSetupRequest(BaseModel):
    """Slack 봇 토큰 설정 요청."""
    bot_token: str
    app_token: str
    allowed_user_ids: list[str] | None = None  # Zero-Trust 화이트리스트 (선택)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/setup")
async def slack_setup(req: SlackSetupRequest) -> dict:
    """
    Slack Bot Token과 App Token을 저장하고 연결 테스트를 수행한다.

    Returns::

        {"ok": True, "bot_name": str} 또는 {"ok": False, "error": str}
    """
    import httpx

    # 연결 테스트: auth.test API 호출
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {req.bot_token}"},
            )
            data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"Slack API 연결 실패: {e}"}

    if not data.get("ok"):
        return {"ok": False, "error": f"잘못된 봇 토큰: {data.get('error', '알 수 없는 오류')}"}

    _keyring.store(SLACK_BOT_TOKEN_KEY, req.bot_token)
    _keyring.store(SLACK_APP_TOKEN_KEY, req.app_token)
    if req.allowed_user_ids:
        import json
        _keyring.store(SLACK_ALLOWED_USERS_KEY, json.dumps(req.allowed_user_ids))

    # bot_username 영속 저장 — status 응답에서 딥링크 생성에 사용
    slack_user = data.get("user", "")
    if slack_user:
        _keyring.store(SLACK_BOT_USERNAME_KEY, f"@{slack_user}")
    _audit.log("slack_setup", f"team={data.get('team', '?')}")

    return {
        "ok": True,
        "bot_name": data.get("bot_id", ""),
        "bot_username": f"@{slack_user}" if slack_user else None,
        "team": data.get("team", ""),
        "user": slack_user,
    }


@router.get("/status")
async def slack_status() -> dict:
    """Slack 봇 연결 상태와 bot_username을 반환한다."""
    global _slack_adapter

    bot_token = _keyring.retrieve(SLACK_BOT_TOKEN_KEY)
    app_token = _keyring.retrieve(SLACK_APP_TOKEN_KEY)
    bot_username = _keyring.retrieve(SLACK_BOT_USERNAME_KEY)

    return {
        "configured": bool(bot_token and app_token),
        "running": _slack_adapter is not None and _slack_adapter.is_running(),
        "has_bot_token": bool(bot_token),
        "has_app_token": bool(app_token),
        "bot_username": bot_username or None,
    }


@router.post("/start")
async def slack_start() -> dict:
    """Slack 봇을 시작한다."""
    global _slack_adapter

    bot_token = _keyring.retrieve(SLACK_BOT_TOKEN_KEY)
    app_token = _keyring.retrieve(SLACK_APP_TOKEN_KEY)

    if not bot_token or not app_token:
        raise HTTPException(
            status_code=400,
            detail="Slack 봇 토큰이 설정되지 않았습니다. POST /slack/setup을 먼저 호출하세요.",
        )

    if _slack_adapter and _slack_adapter.is_running():
        return {"status": "already_running"}

    try:
        import json
        allowed_raw = _keyring.retrieve(SLACK_ALLOWED_USERS_KEY)
        allowed_user_ids = json.loads(allowed_raw) if allowed_raw else None

        from office_claw_sidecar.messenger.slack import SlackAdapter
        _slack_adapter = SlackAdapter(
            bot_token=bot_token,
            app_token=app_token,
            allowed_user_ids=allowed_user_ids,
        )
        result = await _slack_adapter.start()
        _audit.log("slack_start", "bot")
        return result
    except Exception as e:
        logger.error("[Slack] 봇 시작 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"Slack 봇 시작 실패: {e}")


@router.post("/stop")
async def slack_stop() -> dict:
    """Slack 봇을 중지한다."""
    global _slack_adapter

    if not _slack_adapter or not _slack_adapter.is_running():
        return {"status": "not_running"}

    result = await _slack_adapter.stop()
    _slack_adapter = None
    _audit.log("slack_stop", "bot")
    return result
