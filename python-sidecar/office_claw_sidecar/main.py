"""FastAPI application entry point for the Office Claw sidecar."""

import argparse
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.routers import (
    agent,
    audit,
    backup,
    chat,
    credentials,
    discord,
    excel_live,
    harness,
    health,
    llm,
    maintenance,
    permissions,
    security,
    settings,
    slack,
    telegram,
    trace,
    workspace,
)
from office_claw_sidecar.services.user_harness_service import record_user_harness_event

logger = logging.getLogger(__name__)

# ── Temp file cleanup ────────────────────────────────────────────────────────

_TEMP_SUBDIRS = ("excel_uploads", "document_exports")
_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours
KST = timezone(timedelta(hours=9), name="KST")


def _cleanup_temp_files() -> None:
    """
    Delete temporary files older than 24 hours from excel_uploads/ and
    document_exports/ directories.  Called once at startup.
    """
    cutoff = time.time() - _MAX_AGE_SECONDS
    for subdir in _TEMP_SUBDIRS:
        temp_dir = get_data_dir() / subdir
        if not temp_dir.exists():
            continue
        for candidate in temp_dir.iterdir():
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
                    logger.info("임시 파일 정리: %s", candidate)
            except OSError as exc:
                logger.warning("임시 파일 삭제 실패 (%s): %s", candidate, exc)


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    import os

    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _seconds_until_next_kst(hour: int, minute: int) -> float:
    now = datetime.now(KST)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def _nightly_replay_loop() -> None:
    enabled = _env_bool("HARNESS_NIGHTLY_REPLAY_ENABLED", True)
    if not enabled:
        logger.info("하네스 야간 리플레이 비활성화 (HARNESS_NIGHTLY_REPLAY_ENABLED=0)")
        return

    replay_hour = _env_int("HARNESS_NIGHTLY_REPLAY_HOUR", 3, 0, 23)
    replay_minute = _env_int("HARNESS_NIGHTLY_REPLAY_MINUTE", 30, 0, 59)
    replay_limit = _env_int("HARNESS_NIGHTLY_REPLAY_LIMIT", 20, 1, 200)
    parse_timeout = float(_env_int("HARNESS_NIGHTLY_REPLAY_TIMEOUT_SECONDS", 10, 2, 45))
    min_gate_cases = _env_int("HARNESS_NIGHTLY_MIN_GATE_CASES", 5, 1, 200)
    min_gate_pass_pct = _env_int("HARNESS_NIGHTLY_MIN_GATE_PASS_PERCENT", 70, 0, 100)
    min_gate_pass_rate = min_gate_pass_pct / 100.0
    max_users = _env_int("HARNESS_NIGHTLY_MAX_USERS", 200, 1, 1000)

    logger.info(
        "하네스 야간 리플레이 스케줄 시작: 매일 %02d:%02d KST (limit=%d, gate=%d%%/%d건)",
        replay_hour,
        replay_minute,
        replay_limit,
        min_gate_pass_pct,
        min_gate_cases,
    )

    while True:
        wait_seconds = _seconds_until_next_kst(replay_hour, replay_minute)
        await asyncio.sleep(wait_seconds)
        try:
            result = await harness.run_nightly_replay_batch(
                route="/excel-live/command",
                limit=replay_limit,
                parse_timeout_seconds=parse_timeout,
                min_gate_cases=min_gate_cases,
                min_gate_pass_rate=min_gate_pass_rate,
                max_users=max_users,
            )
            logger.info(
                "하네스 야간 리플레이 완료: users=%s ok=%s gate_pass=%s",
                result.get("users_total", 0),
                result.get("users_ok", 0),
                result.get("users_gate_passed", 0),
            )
        except Exception as exc:
            logger.warning("하네스 야간 리플레이 실패(무시): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup/shutdown tasks around the application lifecycle."""
    # Ensure the app data directory and all required temp subdirectories exist
    # before any router tries to write to them.
    data_dir = get_data_dir()
    for subdir in _TEMP_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)

    _cleanup_temp_files()

    # Phase 5: 스킬 화이트리스트 로드 (사용자 커스텀 권한 적용)
    from office_claw_sidecar.services.tool_registry import load_whitelist
    load_whitelist()

    # Phase 3: 저장된 권한 설정을 CommandAnalyzer에 로드 (화이트리스트 복원)
    await _load_permissions_whitelist()

    # Phase 1: 저장된 봇 토큰이 있으면 텔레그램 봇 자동 시작
    await _auto_start_telegram()

    nightly_task = asyncio.create_task(_nightly_replay_loop())

    try:
        yield
    finally:
        nightly_task.cancel()
        with suppress(asyncio.CancelledError):
            await nightly_task


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
        from office_claw_sidecar.routers.telegram import telegram_svc
        from office_claw_sidecar.services.keyring_service import KeyringService

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

_HARNESS_TRACK_PATHS = {
    "/excel-live/command",
    "/excel-live/action",
    "/excel-live/approval",
    "/excel-live/backups",
    "/excel-live/restore-last",
    "/agent/chat",
}


def _parse_json_bytes(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


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


@app.middleware("http")
async def user_harness_middleware(request: Request, call_next):
    if request.url.path not in _HARNESS_TRACK_PATHS:
        return await call_next(request)

    started = time.time()
    req_body = await request.body()
    req_json = _parse_json_bytes(req_body)

    response = await call_next(request)
    response_body = b""
    async for chunk in response.body_iterator:
        response_body += chunk

    elapsed_ms = int((time.time() - started) * 1000)
    res_json = _parse_json_bytes(response_body)

    try:
        record_user_harness_event(
            route=request.url.path,
            method=request.method,
            request_payload=req_json,
            response_payload=res_json,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("사용자 하네스 로그 저장 실패(무시): %s", exc)

    return Response(
        content=response_body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=response.background,
    )


app.include_router(health.router, dependencies=[Depends(verify_auth)])
app.include_router(
    credentials.router, prefix="/credentials", dependencies=[Depends(verify_auth)]
)
app.include_router(llm.router, prefix="/llm", dependencies=[Depends(verify_auth)])
app.include_router(audit.router, prefix="/audit", dependencies=[Depends(verify_auth)])
app.include_router(telegram.router, prefix="/telegram", dependencies=[Depends(verify_auth)])

# ── Phase 1: Private-Claw 워크스페이스 라우터 ──────────────────────────────────
app.include_router(workspace.router, prefix="/workspace", dependencies=[Depends(verify_auth)])
app.include_router(settings.router, prefix="/settings", dependencies=[Depends(verify_auth)])
app.include_router(maintenance.router, prefix="/maintenance", dependencies=[Depends(verify_auth)])

# ── Phase 4: OpenClaw 에이전트 라우터 (신규) ──────────────────────────────────
app.include_router(agent.router, prefix="/agent", dependencies=[Depends(verify_auth)])
app.include_router(security.router, prefix="/security", dependencies=[Depends(verify_auth)])
app.include_router(harness.router, prefix="/harness", dependencies=[Depends(verify_auth)])

# ── Phase 3: 멀티 메신저 + 권한 설정 라우터 ─────────────────────────────────────
app.include_router(slack.router, prefix="/slack", dependencies=[Depends(verify_auth)])
app.include_router(discord.router, prefix="/discord", dependencies=[Depends(verify_auth)])
app.include_router(permissions.router, prefix="/permissions", dependencies=[Depends(verify_auth)])

# ── Sprint 5: 채팅 세션 영속화 + 백업/복원 ─────────────────────────────────────
app.include_router(chat.router, prefix="/chat", dependencies=[Depends(verify_auth)])
app.include_router(backup.router, prefix="/backup", dependencies=[Depends(verify_auth)])
app.include_router(excel_live.router, prefix="/excel-live", dependencies=[Depends(verify_auth)])
# 프론트 사건(라우팅·붙여넣기 프로브·화면 오류·타임아웃)을 같은 chat_log에 — 2026-08-19 로그 감사.
app.include_router(trace.router, dependencies=[Depends(verify_auth)])

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
