"""긴 실행끼리 겹치지 않게 하는 자물쇠 하나.

게이트와 대화 배터리는 **동시에 돌면 결과가 뒤섞인다**(CLAUDE.md §2).
자물쇠 경로를 두 곳에 적으면 반드시 갈라지므로(2026-08-20에 같은 부류로 여러 번 데었다)
여기 한 곳에만 둔다.

    from _run_lock import RunLock
    with RunLock("battery") as lock:
        if not lock.acquired:
            ...  # 남이 돌고 있다
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 산출물 폴더 경로는 사이드카 config 한 곳이 소유한다(패키지 __init__ 은 __version__ 한 줄이라 가볍다).
sys.path.insert(0, str(ROOT / "services" / "sidecar"))
from office_claw_sidecar.config import get_reports_dir

#: 자물쇠는 산출물 폴더 <reports>/nightly 에 둔다 — 저장소 `logs/` 에는 `chat_log.jsonl` 하나만
#: 남기기로 했다(2026-09-10). 기본 %LOCALAPPDATA%/office_claw/reports, 환경변수 OFFICE_CLAW_REPORTS_DIR.
LOCK_PATH = get_reports_dir() / "nightly" / ".running.lock"
#: 2026-09-10 이전 코드가 자물쇠를 쥐던 자리. **읽기만 한다** — 옛 코드로 뜬 긴 실행(그날 밤
#: 03:00 게이트)이 여기를 쥐고 있는 동안 새 코드가 겹쳐 돌면 결과가 뒤섞인다.
LEGACY_LOCK_PATH = ROOT / "logs" / "nightly" / ".running.lock"
#: pid를 못 읽는 낡은 형식의 자물쇠에만 쓰는 폴백 — pid가 있으면 생존 검사가 우선한다.
STALE_SECONDS = 10 * 3600


def _pid_alive(pid: int) -> bool:
    """그 pid의 프로세스가 살아 있는가 — Windows/macOS 양쪽에서 동작한다.

    주의: Windows의 os.kill(pid, 0)은 존재 검사가 아니라 **종료 시도**다.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            STILL_ACTIVE = 259
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 살아 있지만 남의 프로세스
    except Exception:
        return False
    return True


def _live_holder(path: Path) -> str:
    """그 자리의 자물쇠를 산 프로세스가 쥐고 있으면 그 내용, 아니면 빈 문자열."""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    match = re.search(r"pid=(\d+)", text)
    if match:
        # pid가 죽었으면 즉시 스테일 — 2026-08-30 03:01 실측: 예약 게이트가
        # CONTROL_C_EXIT로 죽으며 남긴 락이 다음 예약까지 막을 뻔했다.
        return text if _pid_alive(int(match.group(1))) else ""
    age = time.time() - path.stat().st_mtime
    return text if age < STALE_SECONDS else ""


class RunLock:
    """자물쇠를 잡거나, 못 잡으면 누가 쥐고 있는지 알려 준다."""

    def __init__(self, owner: str, path: Path | None = None) -> None:
        self.owner = owner
        self.path = path or LOCK_PATH
        self.acquired = False
        self.held_by = ""

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for path in (self.path, LEGACY_LOCK_PATH):
            holder = _live_holder(path)
            if holder:
                self.held_by = holder
                return self
        # 있어도 죽은 프로세스의 자물쇠다. 영원히 막히는 게 더 나쁘다.
        self.path.write_text(f"{self.owner} pid={os.getpid()}", encoding="utf-8")
        self.acquired = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
