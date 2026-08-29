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
    excel_live,
    harness,
    health,
    llm,
    maintenance,
    permissions,
    relay,
    security,
    settings,
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

    # 모바일 릴레이: 페어링돼 있으면 relay로 아웃바운드 WS 클라이언트 자동 기동
    await _auto_start_relay_client(app)

    nightly_task = asyncio.create_task(_nightly_replay_loop())

    try:
        yield
    finally:
        nightly_task.cancel()
        with suppress(asyncio.CancelledError):
            await nightly_task

    # ── shutdown ──
    from office_claw_sidecar.services.relay_client import stop_relay_client

    await stop_relay_client(app)


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




async def _auto_start_relay_client(app: FastAPI) -> None:
    """앱 시작 시 relay 페어링 설정이 있으면 아웃바운드 WS 클라이언트를 자동 시작한다.

    설정이 없거나 실패하면 조용히 무시 — 사용자가 온보딩에서 페어링 가능.
    """
    try:
        from office_claw_sidecar.services.relay_client import start_relay_client

        if await start_relay_client(app):
            logger.info("relay 클라이언트 자동 시작 완료")
        else:
            logger.info("relay 미설정 — 자동 시작 건너뜀")
    except Exception as exc:
        logger.warning("relay 클라이언트 자동 시작 실패 (무시됨): %s", exc)


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

# ── Phase 1: Private-Claw 워크스페이스 라우터 ──────────────────────────────────
app.include_router(workspace.router, prefix="/workspace", dependencies=[Depends(verify_auth)])
app.include_router(settings.router, prefix="/settings", dependencies=[Depends(verify_auth)])
app.include_router(maintenance.router, prefix="/maintenance", dependencies=[Depends(verify_auth)])

# ── Phase 4: OpenClaw 에이전트 라우터 (신규) ──────────────────────────────────
app.include_router(agent.router, prefix="/agent", dependencies=[Depends(verify_auth)])
app.include_router(security.router, prefix="/security", dependencies=[Depends(verify_auth)])
app.include_router(harness.router, prefix="/harness", dependencies=[Depends(verify_auth)])

# ── Phase 3: 권한 설정 라우터 ───────────────────────────────────────────────────
app.include_router(permissions.router, prefix="/permissions", dependencies=[Depends(verify_auth)])

# ── Sprint 5: 채팅 세션 영속화 + 백업/복원 ─────────────────────────────────────
app.include_router(chat.router, prefix="/chat", dependencies=[Depends(verify_auth)])
app.include_router(backup.router, prefix="/backup", dependencies=[Depends(verify_auth)])
app.include_router(excel_live.router, prefix="/excel-live", dependencies=[Depends(verify_auth)])
# 프론트 사건(라우팅·붙여넣기 프로브·화면 오류·타임아웃)을 같은 chat_log에 — 2026-08-19 로그 감사.
app.include_router(trace.router, dependencies=[Depends(verify_auth)])

# ── 모바일 릴레이(중계 서버) 연동 ──────────────────────────────────────────────
app.include_router(relay.router, prefix="/relay", dependencies=[Depends(verify_auth)])


def _smoke_test() -> int:
    """번들 산출물이 이 OS에서 실제로 설 수 있는지 확인한다 (CI 릴리스 게이트).

    왜 필요한가: 사이드카는 PyInstaller로 얼려서 배포하는데, **번들에서만
    깨지는 결함**이 있다. 실제로 `__main__.py`의 상대 import 때문에 번들이
    기동조차 못 한 적이 있고(78213c6), 개발 중에는 `python -m`으로 돌아
    패키지 컨텍스트가 있으므로 재현되지 않았다.

    플랫폼 백엔드도 여기서 걸린다. xlwings는 Windows에서 pywin32(pythoncom·
    win32com), macOS에서 appscript를 타는데 **둘 다 우리 코드가 직접 import
    하지 않는다** — PyInstaller의 정적 분석이 놓치면 번들에 안 들어가고,
    사용자가 엑셀 명령을 내리는 순간에야 드러난다.

    포트는 열지 않는다. CI 러너에서 uvicorn을 띄우면 종료 시점을 잡아야 하고,
    확인하려는 것은 "구성이 성립하는가"이지 "서빙이 되는가"가 아니다.
    """
    import platform
    import sys

    print(f"[smoke] {platform.system()} {platform.machine()} / Python {sys.version.split()[0]}")
    print(f"[smoke] frozen={getattr(sys, 'frozen', False)}")

    failures: list[str] = []

    # 1. FastAPI 앱 구성 — 라우터·서비스 import 사슬 전체가 여기서 걸린다.
    try:
        print(f"[smoke] app routes: {len(app.routes)}")
    except Exception as exc:  # pragma: no cover - 번들 전용 경로
        failures.append(f"app 구성 실패: {exc}")

    # 2. xlwings + 플랫폼 백엔드. Excel이 없어도 import 자체는 성공해야 한다.
    try:
        import xlwings  # noqa: F401

        print(f"[smoke] xlwings {getattr(xlwings, '__version__', '?')}")
    except Exception as exc:  # pragma: no cover - 번들 전용 경로
        failures.append(f"xlwings import 실패: {exc}")

    backend_name = "appscript" if sys.platform == "darwin" else "pythoncom"
    try:
        __import__(backend_name)
        print(f"[smoke] {backend_name} OK")
    except Exception as exc:  # pragma: no cover - 번들 전용 경로
        failures.append(f"{backend_name} import 실패: {exc}")

    # 3. keyring 백엔드 — entry point로 해석되므로 번들에서 잘 빠진다.
    #    Null/Fail 백엔드로 떨어지면 자격증명이 조용히 평문이 되거나 실패한다.
    try:
        import keyring

        kr = type(keyring.get_keyring()).__name__
        print(f"[smoke] keyring backend: {kr}")
        if any(bad in kr for bad in ("Null", "Fail")):
            failures.append(f"keyring 백엔드가 사용 불가: {kr}")
    except Exception as exc:  # pragma: no cover - 번들 전용 경로
        failures.append(f"keyring 확인 실패: {exc}")

    if failures:
        for f in failures:
            print(f"[smoke] FAIL: {f}")
        return 1
    print("[smoke] OK")
    return 0


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
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        default=False,
        help="번들 산출물 자기진단 — 포트를 열지 않고 구성만 확인하고 종료한다.",
    )
    args = parser.parse_args()

    if args.smoke_test:
        raise SystemExit(_smoke_test())

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
