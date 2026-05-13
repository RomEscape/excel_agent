"""Telegram bot control endpoints — Phase 1 (Private-Claw)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar.services.telegram_service import TelegramService

router = APIRouter()
telegram_svc = TelegramService()


class TelegramSetupRequest(BaseModel):
    """봇 토큰 설정 요청 모델."""
    token: str
    chat_id: str | None = None


@router.post("/setup")
async def telegram_setup(req: TelegramSetupRequest):
    """
    봇 토큰을 설정하고 연결 테스트를 수행한다.

    Request body:
        token: Telegram Bot API 토큰 (필수)
        chat_id: 허용할 chat_id (선택)

    Returns:
        {"ok": true, "bot_name": str} 또는 HTTP 400
    """
    result = await telegram_svc.setup(req.token, req.chat_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "설정 실패"))
    return result


@router.get("/status")
async def telegram_status():
    """Telegram 봇 실행 상태와 bot_username을 반환한다."""
    from office_claw_sidecar.services.keyring_service import KeyringService
    _keyring = KeyringService()
    # setup 시 저장된 bot_username 조회 (없으면 null)
    bot_username = _keyring.retrieve("telegram_bot_username")
    return {
        "running": telegram_svc.is_running(),
        "bot_username": bot_username or None,
    }


@router.post("/start")
async def telegram_start():
    """Start the Telegram bot."""
    try:
        result = await telegram_svc.start()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def telegram_stop():
    """Stop the Telegram bot."""
    try:
        result = await telegram_svc.stop()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
