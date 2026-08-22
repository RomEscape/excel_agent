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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "logs/nightly/.running.lock"
#: 이보다 오래된 자물쇠는 죽은 프로세스가 남긴 것으로 본다.
STALE_SECONDS = 10 * 3600


class RunLock:
    """자물쇠를 잡거나, 못 잡으면 누가 쥐고 있는지 알려 준다."""

    def __init__(self, owner: str, path: Path | None = None) -> None:
        self.owner = owner
        self.path = path or LOCK_PATH
        self.acquired = False
        self.held_by = ""

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < STALE_SECONDS:
                self.held_by = self.path.read_text(encoding="utf-8", errors="replace").strip()
                return self
            # 오래된 자물쇠는 죽은 프로세스가 남긴 것이다. 영원히 막히는 게 더 나쁘다.
        self.path.write_text(f"{self.owner} pid={os.getpid()}", encoding="utf-8")
        self.acquired = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
