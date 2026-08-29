"""
Workspace sandbox — Phase 1 (officeclaw).

워크스페이스 경로: config.get_workspace_root()
모든 파일 접근은 이 경로 내부로 제한된다.
symlink를 따라가도 워크스페이스 외부를 가리키면 차단.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from office_claw_sidecar.config import get_workspace_root

# 경로 단일 출처는 config.get_workspace_root()
WORKSPACE_ROOT = get_workspace_root()


def _ensure_workspace() -> None:
    """워크스페이스 디렉토리가 없으면 생성."""
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def _resolve(path: str) -> Path:
    """
    요청 경로를 절대 경로로 변환한다.
    - 상대 경로는 WORKSPACE_ROOT 기준으로 해석
    - resolve()로 symlink를 끝까지 추적
    """
    _ensure_workspace()
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = WORKSPACE_ROOT / candidate
    else:
        candidate = WORKSPACE_ROOT
    # resolve(strict=False): 존재하지 않는 경로도 정규화 (쓰기 전 검증에 사용)
    return candidate.resolve()


def is_path_safe(path: str) -> bool:
    """
    요청 경로가 워크스페이스 내부인지 검증한다.

    Parameters
    ----------
    path:
        검증할 경로 (절대 또는 상대).

    Returns
    -------
    bool
        True이면 안전 (워크스페이스 내부), False이면 접근 거부.
    """
    resolved = _resolve(path)
    workspace_resolved = WORKSPACE_ROOT.resolve()
    try:
        resolved.relative_to(workspace_resolved)
        return True
    except ValueError:
        return False


def list_files(path: str = "") -> list[dict[str, Any]]:
    """
    워크스페이스 내 디렉토리의 파일/폴더 목록을 반환한다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로 또는 빈 문자열 (루트).

    Returns
    -------
    list[dict]
        각 항목: {name, path, size, modified, is_dir}

    Raises
    ------
    PermissionError
        워크스페이스 외부 경로 요청 시.
    NotADirectoryError
        경로가 디렉토리가 아닐 때.
    """
    _ensure_workspace()

    if not is_path_safe(path):
        raise PermissionError(f"접근 거부: 워크스페이스 외부 경로입니다. ({path})")

    resolved = _resolve(path)

    if not resolved.exists():
        raise FileNotFoundError(f"경로가 존재하지 않습니다: {path}")

    if not resolved.is_dir():
        raise NotADirectoryError(f"디렉토리가 아닙니다: {path}")

    entries = []
    for entry in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            stat = entry.stat()
            # 워크스페이스 루트 기준 상대 경로로 표현
            rel = entry.relative_to(WORKSPACE_ROOT.resolve())
            entries.append({
                "name": entry.name,
                "path": str(rel),
                "size": stat.st_size if entry.is_file() else 0,
                "modified": stat.st_mtime,
                "is_dir": entry.is_dir(),
            })
        except OSError:
            # 접근 불가 파일은 건너뜀
            continue

    return entries


def read_file(path: str) -> str:
    """
    워크스페이스 내 파일 내용을 읽어 반환한다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로 또는 절대 경로.

    Returns
    -------
    str
        파일 내용 (UTF-8, 디코딩 불가 문자는 대체).

    Raises
    ------
    PermissionError
        워크스페이스 외부 경로 요청 시.
    FileNotFoundError
        파일이 없을 때.
    IsADirectoryError
        경로가 디렉토리일 때.
    """
    if not is_path_safe(path):
        raise PermissionError(f"접근 거부: 워크스페이스 외부 경로입니다. ({path})")

    resolved = _resolve(path)

    if not resolved.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    if resolved.is_dir():
        raise IsADirectoryError(f"파일이 아닌 디렉토리입니다: {path}")

    return resolved.read_text(encoding="utf-8", errors="replace")


def write_file(path: str, content: str) -> bool:
    """
    워크스페이스 내 파일에 내용을 쓴다.
    중간 디렉토리가 없으면 자동 생성한다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로 또는 절대 경로.
    content:
        저장할 텍스트.

    Returns
    -------
    bool
        성공 시 True.

    Raises
    ------
    PermissionError
        워크스페이스 외부 경로 요청 시.
    """
    if not is_path_safe(path):
        raise PermissionError(f"접근 거부: 워크스페이스 외부 경로입니다. ({path})")

    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return True
