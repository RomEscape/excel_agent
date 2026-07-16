"""Application configuration and platform-specific paths."""

import logging
import platform
import shutil
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


def get_relay_config_path() -> Path:
    """중계 서버(relay) 연동 설정 경로 — 비밀 아닌 값(relay_url, pairing_id 등).

    페어링 시크릿 같은 비밀은 여기 두지 않는다(keyring 사용).
    """
    return get_data_dir() / "relay_config.json"


def get_app_home_dir() -> Path:
    """사용자 가시 홈 디렉토리 (~/officeclaw) — Workspace와 audit.db의 부모.

    OS 표준 데이터 디렉토리(get_data_dir)와 달리, 사용자가 파일 탐색기에서
    바로 접근할 수 있도록 홈 최상위에 둔다. Workspace 경로·DB 경로의 단일 출처.
    """
    return Path.home() / "officeclaw"


def get_workspace_root() -> Path:
    """샌드박스 워크스페이스 루트 (~/officeclaw/Workspace).

    sandbox.WORKSPACE_ROOT의 단일 출처. 메신저 봇·에이전트의 모든 파일 접근이
    이 디렉토리 내부로 제한된다.
    """
    return get_app_home_dir() / "Workspace"


def get_app_db_path() -> Path:
    """명령 감사 + 채팅 세션 공용 SQLite DB 경로 (~/officeclaw/audit.db).

    command_audit / chat_history / backup 세 모듈이 같은 파일을 공유한다(테이블만 다름).
    """
    return get_app_home_dir() / "audit.db"


def migrate_legacy_paths() -> None:
    """레거시 홈 디렉토리 ~/PrivateClaw → ~/officeclaw 1회 이전 (멱등).

    브랜드 변경(PrivateClaw → officeclaw)에 따라 기존 사용자의 워크스페이스 파일과
    audit.db를 새 위치로 옮긴다. 신규 사용자(레거시 없음)나 이미 이전된 경우 no-op.

    반드시 DB 연결·워크스페이스 생성이 일어나기 전(앱 startup 최상단)에 호출해야
    한다 — 그 전에 새 경로에 빈 파일이 생기면 이전이 건너뛰어진다.
    """
    old_root = Path.home() / "PrivateClaw"
    new_root = get_app_home_dir()
    if not old_root.exists():
        return  # 신규 사용자 또는 이미 이전됨

    if not new_root.exists():
        # 일반 케이스: 디렉토리 통째로 rename
        shutil.move(str(old_root), str(new_root))
        logger.info("레거시 경로 이전: %s → %s", old_root, new_root)
        return

    # 엣지 케이스: 새 디렉토리가 이미 있으면 충돌하지 않는 항목만 개별 이동
    for item in old_root.iterdir():
        target = new_root / item.name
        if target.exists():
            logger.warning("레거시 이전 건너뜀(대상 이미 존재): %s", target)
            continue
        shutil.move(str(item), str(target))
        logger.info("레거시 항목 이전: %s → %s", item, target)
    try:
        old_root.rmdir()  # 비었으면 제거, 남은 항목 있으면 보존
    except OSError:
        pass


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
