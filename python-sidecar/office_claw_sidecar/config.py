"""Application configuration and platform-specific paths."""

import os
import platform
from pathlib import Path

APP_NAME = "office_claw"
SERVICE_NAMESPACE = "office_claw"


def get_data_dir() -> Path:
    """Get the platform-appropriate data directory."""
    system = platform.system()
    if system == "Windows":
        base = Path.home() / "AppData" / "Local"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"

    data_dir = base / APP_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_audit_log_path() -> Path:
    return get_data_dir() / "audit.jsonl"


def _detect_workspace_root() -> Path | None:
    """
    소스 트리 실행 환경에서 레포 루트를 추정한다.

    조건:
    - package.json 존재
    - python-sidecar 디렉터리 존재
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "package.json").exists() and (parent / "python-sidecar").exists():
            return parent
    return None


def get_logs_dir() -> Path:
    """
    통합 로그 디렉터리.

    - 기본(소스 트리 실행): <workspace_root>/logs
    - 기본(배포/기타): <data_dir>/logs
    - 환경변수 OFFICE_CLAW_LOGS_DIR 가 있으면 해당 경로 사용
    """
    override = str(os.getenv("OFFICE_CLAW_LOGS_DIR", "") or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        workspace_root = _detect_workspace_root()
        if workspace_root is not None:
            path = workspace_root / "logs"
        else:
            path = get_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_unified_log_path() -> Path:
    """통합 이벤트 JSONL 파일 경로."""
    return get_logs_dir() / "all_events.jsonl"


def get_credentials_registry_path() -> Path:
    return get_data_dir() / "credentials_registry.json"


def get_whitelist_path() -> Path:
    """스킬 화이트리스트 영속 저장 경로."""
    return get_data_dir() / "skill_whitelist.json"
