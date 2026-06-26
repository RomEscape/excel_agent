"""Application configuration and platform-specific paths."""

import logging
import platform
import time
from pathlib import Path

APP_NAME = "office_claw"
SERVICE_NAMESPACE = "office_claw"

logger = logging.getLogger(__name__)

# 임시 파일 정리 대상 서브디렉토리 — 시작 시 정리(main)와 수동 정리(maintenance)가
# 같은 목록을 공유한다. 단일 출처.
TEMP_SUBDIRS: tuple[str, ...] = ("excel_uploads", "document_exports")


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


def get_credentials_registry_path() -> Path:
    return get_data_dir() / "credentials_registry.json"


def get_whitelist_path() -> Path:
    """스킬 화이트리스트 영속 저장 경로."""
    return get_data_dir() / "skill_whitelist.json"


def get_app_db_path() -> Path:
    """명령 감사 + 채팅 세션 공용 SQLite DB 경로 (~/PrivateClaw/audit.db).

    command_audit / chat_history / backup 세 모듈이 같은 파일을 공유한다(테이블만 다름).
    """
    return Path.home() / "PrivateClaw" / "audit.db"


def cleanup_temp(
    subdirs: tuple[str, ...] = TEMP_SUBDIRS,
    max_age: float | None = None,
) -> tuple[int, int]:
    """임시 서브디렉토리의 파일을 삭제한다.

    Parameters
    ----------
    subdirs:
        정리 대상 서브디렉토리 목록 (기본: TEMP_SUBDIRS).
    max_age:
        None이면 모든 파일 삭제(수동 정리). 초 단위 값이 주어지면 해당 시간보다
        오래된 파일만 삭제(시작 시 정리).

    Returns
    -------
    tuple[int, int]
        (삭제한 파일 수, 확보한 바이트 수)
    """
    cutoff = time.time() - max_age if max_age is not None else None
    deleted_count = 0
    freed_bytes = 0
    for subdir in subdirs:
        temp_dir = get_data_dir() / subdir
        if not temp_dir.exists():
            continue
        for candidate in list(temp_dir.iterdir()):
            if not candidate.is_file():
                continue
            try:
                stat = candidate.stat()
                if cutoff is not None and stat.st_mtime >= cutoff:
                    continue
                file_size = stat.st_size
                candidate.unlink()
                deleted_count += 1
                freed_bytes += file_size
                logger.info("임시 파일 정리: %s (%d bytes)", candidate, file_size)
            except OSError as exc:
                logger.warning("임시 파일 삭제 실패 (%s): %s", candidate, exc)
    return deleted_count, freed_bytes
