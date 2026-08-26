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
    audit,
    backup,
    chat,
    credentials,
    excel_live,
    health,
    llm,
    maintenance,
    permissions,
    relay,
    security,
    settings,
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

    # 모바일 릴레이: 페어링돼 있으면 relay로 아웃바운드 WS 클라이언트 자동 기동
    await _auto_start_relay_client(app)

    yield

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

# ── Phase 1: officeclaw 워크스페이스 라우터 ──────────────────────────────────
app.include_router(workspace.router, prefix="/workspace", dependencies=[Depends(verify_auth)])
app.include_router(settings.router, prefix="/settings", dependencies=[Depends(verify_auth)])
app.include_router(maintenance.router, prefix="/maintenance", dependencies=[Depends(verify_auth)])

# ── 보안 라우터 ───────────────────────────────────────────────────────────────
app.include_router(security.router, prefix="/security", dependencies=[Depends(verify_auth)])

# ── Phase 3: 권한 설정 라우터 ───────────────────────────────────────────────────
app.include_router(permissions.router, prefix="/permissions", dependencies=[Depends(verify_auth)])

# ── Sprint 5: 채팅 세션 영속화 + 백업/복원 ─────────────────────────────────────
app.include_router(chat.router, prefix="/chat", dependencies=[Depends(verify_auth)])
app.include_router(backup.router, prefix="/backup", dependencies=[Depends(verify_auth)])
app.include_router(excel_live.router, prefix="/excel-live", dependencies=[Depends(verify_auth)])

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

    uvicorn.run(
        "office_claw_sidecar.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
