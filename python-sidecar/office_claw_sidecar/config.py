"""Application configuration and platform-specific paths."""

import os
import platform
from pathlib import Path
import time

APP_NAME = "office_claw"
SERVICE_NAMESPACE = "office_claw"
TEMP_SUBDIRS = ("excel_uploads", "document_exports")


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


def get_workspace_root() -> Path:
    """
    워크스페이스 루트 디렉토리.

    - 기본: <data_dir>/Workspace
    - 환경변수 OFFICE_CLAW_WORKSPACE_DIR 지정 시 해당 경로 사용
    """
    override = str(os.getenv("OFFICE_CLAW_WORKSPACE_DIR", "") or "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = get_data_dir() / "Workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_app_db_path() -> Path:
    """감사/채팅 통합 SQLite 경로."""
    return get_data_dir() / "audit.db"


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


def get_chat_log_path() -> Path:
    """대화 턴별 판단·계획·실행 추적 JSONL 파일 경로."""
    return get_logs_dir() / "chat_log.jsonl"


def get_credentials_registry_path() -> Path:
    return get_data_dir() / "credentials_registry.json"


def get_whitelist_path() -> Path:
    """스킬 화이트리스트 영속 저장 경로."""
    return get_data_dir() / "skill_whitelist.json"


def cleanup_temp(*, max_age: int | None = None) -> tuple[int, int]:
    """
    임시 파일 정리.

    Args:
        max_age: 초 단위 보관 기간. None이면 나이와 무관하게 모두 삭제.

    Returns:
        (deleted_count, freed_bytes)
    """
    deleted_count = 0
    freed_bytes = 0
    now_ts = int(time.time())
    for subdir in TEMP_SUBDIRS:
        target = get_data_dir() / subdir
        target.mkdir(parents=True, exist_ok=True)
        for fp in target.iterdir():
            if not fp.is_file():
                continue
            try:
                stat = fp.stat()
            except OSError:
                continue
            if max_age is not None:
                age = now_ts - int(stat.st_mtime)
                if age < int(max_age):
                    continue
            try:
                fp.unlink()
                deleted_count += 1
                freed_bytes += int(stat.st_size)
            except OSError:
                continue
    return deleted_count, freed_bytes
