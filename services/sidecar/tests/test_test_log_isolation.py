"""conftest 가 테스트 기록을 저장소 `logs/` 와 사용자의 실제 보관 폴더 밖으로 돌리는지.

저장소 `logs/` 에는 `chat_log.jsonl` 하나만 둔다(2026-09-10, docs/logs.md). 테스트가 만든
턴·회전 조각·스크립트 산출물이 거기나 `%LOCALAPPDATA%/office_claw/{chat_log_archive,reports}`
로 새면 사람이 읽을 기록이 묻히고 규칙도 깨진다. 2026-09-11 검증에서 두 군데가 실제로 새고
있었다 — 옛 `logs/test-runs`, 그리고 64MB 회전 조각이 실제 보관 폴더로 가는 것.
"""

from __future__ import annotations

import os
from pathlib import Path

from office_claw_sidecar.config import (
    get_chat_log_archive_dir,
    get_chat_log_path,
    get_data_dir,
    get_logs_dir,
    get_reports_dir,
)

_REPO_LOGS = Path(__file__).resolve().parents[3] / "logs"


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def test_conftest_sets_all_three_dir_overrides():
    # setdefault 한 줄이 빠지면 여기서 잡힌다(기본 환경 기준).
    for name in ("OFFICE_CLAW_LOGS_DIR", "OFFICE_CLAW_CHAT_LOG_ARCHIVE_DIR", "OFFICE_CLAW_REPORTS_DIR"):
        assert os.environ.get(name, "").strip(), f"{name} 이 비어 있다 — conftest 가 정해야 한다"


def test_test_records_stay_out_of_repo_logs():
    for path in (get_logs_dir(), get_chat_log_path().parent, get_chat_log_archive_dir(), get_reports_dir()):
        assert not _is_under(path, _REPO_LOGS), f"테스트 기록이 저장소 logs/ 로 샌다: {path}"


def test_rotation_pieces_and_reports_avoid_real_user_folders():
    data_dir = get_data_dir().resolve()
    assert get_chat_log_archive_dir().resolve() != data_dir / "chat_log_archive"
    assert get_reports_dir().resolve() != data_dir / "reports"
