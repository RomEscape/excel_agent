"""FastAPI application entry point for the Office Claw sidecar."""

import argparse
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request, HTTPException

from office_claw_sidecar.config import (
    TEMP_SUBDIRS,
    cleanup_temp,
    get_data_dir,
    migrate_legacy_paths,
)
from office_claw_sidecar.routers import (
    agent,
    audit,
    backup,
    chat,
    credentials,
    discord,
    excel_live,
    health,
    llm,
    maintenance,
    permissions,
    security,
    settings,
    skills,
    slack,
    telegram,
    workspace,
)

logger = logging.getLogger(__name__)

# 시작 시 임시 파일 정리 기준: 24시간보다 오래된 파일만 삭제
_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup/shutdown tasks around the application lifecycle."""
    # 레거시 ~/PrivateClaw → ~/officeclaw 1회 이전. DB 연결·워크스페이스 생성보다
    # 반드시 먼저 실행해야 기존 데이터가 새 경로로 안전하게 옮겨진다.
    migrate_legacy_paths()

    # Ensure the app data directory and all required temp subdirectories exist
    # before any router tries to write to them.
    data_dir = get_data_dir()
    for subdir in TEMP_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)

    cleanup_temp(max_age=_MAX_AGE_SECONDS)

    # Phase 5: 스킬 화이트리스트 로드 (사용자 커스텀 권한 적용)
    from office_claw_sidecar.services.tool_registry import load_whitelist
    load_whitelist()

    # Phase 3: 저장된 권한 설정을 CommandAnalyzer에 로드 (화이트리스트 복원)
    await _load_permissions_whitelist()

    # Phase 1: 저장된 봇 토큰이 있으면 텔레그램 봇 자동 시작
    await _auto_start_telegram()

    yield


async def _load_permissions_whitelist() -> None:
    """
    앱 시작 시 permissions.json에 저장된 화이트리스트를 CommandAnalyzer에 로드한다.

    파일이 없거나 로드에 실패하면 조용히 무시 — 기본 화이트리스트(빈 집합)로 동작.
    """
    try:
        from office_claw_sidecar.analyzer import get_analyzer
        from office_claw_sidecar.routers.permissions import _load_permissions

        perms = _load_permissions()
        combined = (
            perms.get("shell_command_whitelist", [])
            + perms.get("python_module_whitelist", [])
        )
        if combined:
            get_analyzer().load_whitelist(combined)
            logger.info("권한 설정 화이트리스트 로드 완료: %d개 항목", len(combined))
        else:
            logger.info("저장된 화이트리스트 없음 — 기본값(빈 집합) 사용")
    except Exception as exc:
        logger.warning("화이트리스트 로드 실패 (무시됨): %s", exc)


async def _auto_start_telegram() -> None:
    """
    앱 시작 시 keyring에 봇 토큰이 저장되어 있으면 자동으로 텔레그램 봇을 시작한다.
    토큰이 없거나 봇 시작에 실패하면 조용히 무시 — 사용자가 온보딩에서 설정 가능.
    """
    try:
        from office_claw_sidecar.services.keyring_service import KeyringService
        from office_claw_sidecar.routers.telegram import telegram_svc

        ks = KeyringService()
        token = ks.retrieve("telegram_bot_token")
        if not token:
            logger.info("텔레그램 봇 토큰 없음 — 자동 시작 건너뜀")
            return

        if telegram_svc.is_running():
            logger.info("텔레그램 봇 이미 실행 중 — 자동 시작 건너뜀")
            return

        logger.info("저장된 봇 토큰 감지 — 텔레그램 봇 자동 시작 중...")
        await telegram_svc.start()
        logger.info("텔레그램 봇 자동 시작 완료")
    except Exception as exc:
        logger.warning("텔레그램 봇 자동 시작 실패 (무시됨): %s", exc)


app = FastAPI(title="Office Claw Sidecar", version="0.1.0", lifespan=lifespan)

# Auth token set at startup
_auth_token: str | None = None


def verify_auth(request: Request) -> None:
    """Verify the Bearer token matches the one passed at startup."""
    if _auth_token is None:
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth_header[7:]
    if token != _auth_token:
        raise HTTPException(status_code=403, detail="Invalid auth token")


app.include_router(health.router, dependencies=[Depends(verify_auth)])
app.include_router(
    credentials.router, prefix="/credentials", dependencies=[Depends(verify_auth)]
)
app.include_router(llm.router, prefix="/llm", dependencies=[Depends(verify_auth)])
app.include_router(audit.router, prefix="/audit", dependencies=[Depends(verify_auth)])
app.include_router(telegram.router, prefix="/telegram", dependencies=[Depends(verify_auth)])

# ── Phase 1: officeclaw 워크스페이스 라우터 ──────────────────────────────────
app.include_router(workspace.router, prefix="/workspace", dependencies=[Depends(verify_auth)])
app.include_router(settings.router, prefix="/settings", dependencies=[Depends(verify_auth)])
app.include_router(maintenance.router, prefix="/maintenance", dependencies=[Depends(verify_auth)])

# ── Phase 4: OpenClaw 에이전트 라우터 (신규) ──────────────────────────────────
app.include_router(agent.router, prefix="/agent", dependencies=[Depends(verify_auth)])
app.include_router(skills.router, prefix="/skills", dependencies=[Depends(verify_auth)])
app.include_router(security.router, prefix="/security", dependencies=[Depends(verify_auth)])

# ── Phase 3: 멀티 메신저 + 권한 설정 라우터 ─────────────────────────────────────
app.include_router(slack.router, prefix="/slack", dependencies=[Depends(verify_auth)])
app.include_router(discord.router, prefix="/discord", dependencies=[Depends(verify_auth)])
app.include_router(permissions.router, prefix="/permissions", dependencies=[Depends(verify_auth)])

# ── Sprint 5: 채팅 세션 영속화 + 백업/복원 ─────────────────────────────────────
app.include_router(chat.router, prefix="/chat", dependencies=[Depends(verify_auth)])
app.include_router(backup.router, prefix="/backup", dependencies=[Depends(verify_auth)])
app.include_router(excel_live.router, prefix="/excel-live", dependencies=[Depends(verify_auth)])


def main() -> None:
    # Windows 콘솔에서 한글 경로/로그 깨짐 방지
    import os
    import sys

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description="Office Claw Sidecar")
    parser.add_argument("--port", type=int, default=19532)
    parser.add_argument("--auth-token", type=str, default=None)
    parser.add_argument("--reload", action="store_true", default=False)
    args = parser.parse_args()

    global _auth_token
    _auth_token = args.auth_token
    # dev 모드(tauri에서 auth-token=dev-token)에서는 gateway 토큰도 dev-token으로 고정해
    # OpenClaw gateway 인증 토큰 mismatch를 방지한다.
    if args.auth_token == "dev-token" and not os.environ.get("OPENCLAW_GATEWAY_TOKEN"):
        os.environ["OPENCLAW_GATEWAY_TOKEN"] = "dev-token"

    uvicorn.run(
        "office_claw_sidecar.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
