"""저장소 `logs/` 의 옛 배치를 새 자리로 옮긴다 — `chat_log.jsonl` 하나만 남기기 위해.

2026-09-10 사용자 지시: 런타임 기록은 `logs/chat_log.jsonl` 한 파일에만 남기고, 측정·게이트·
진단 스크립트의 산출물은 저장소 밖(`get_reports_dir()`)으로 보낸다. 이 스크립트는 그 이전에
쌓인 디스크 배치를 새 규칙대로 옮긴다. 코드가 새 자리에 쓰기 시작한 뒤 **사람이 한 번** 돌린다.

    & $PY scripts/migrate_logs_dir.py            # dry-run: 무엇을 어디로 옮길지 출력만 (기본)
    & $PY scripts/migrate_logs_dir.py --apply    # 실제 이동

대응표(원본 → 대상):
    logs/nightly · measurements · e2e · diagnostics · skill_ab · archive-*  → <reports>/<같은 이름>/
    logs/all_events.jsonl · planner_escalations.jsonl                     → <reports>/legacy/
    logs/chat_log.<스탬프>.jsonl (64MB 회전 조각)                           → <chat_log_archive>/
    그 밖의 파일(turns.txt, *.json, *.md …)·그 밖의 폴더                    → <reports>/legacy/
    빈 logs/officeclaw_backups                                              → 제거
    logs/chat_log.jsonl · logs/README.md                                    → 그대로 둔다

  reports          = %LOCALAPPDATA%/office_claw/reports          (환경변수 OFFICE_CLAW_REPORTS_DIR)
  chat_log_archive = %LOCALAPPDATA%/office_claw/chat_log_archive (환경변수 OFFICE_CLAW_CHAT_LOG_ARCHIVE_DIR)
  경로는 사이드카 `office_claw_sidecar.config` 한 곳이 소유한다 — 여기 따로 적지 않는다.

규칙:
- 폴더는 **항목 단위로 합친다.** 새 코드가 이미 `<reports>/nightly/` 에 쓰기 시작했어도 옛
  `logs/nightly/*` 가 그 옆으로 들어간다. 같은 이름이 이미 있으면 덮지 않고 건너뛰며 보고한다
  (건너뛴 원본은 `logs/` 에 그대로 남는다).
- `logs/nightly/.running.lock` 을 산 프로세스가 쥐고 있으면(게이트·배터리가 도는 중) `--apply` 를
  거부한다 — 돌고 있는 실행의 산출물 폴더를 옮기면 그 실행이 깨진다. 죽은 프로세스의 자물쇠는 지운다.

종료코드: 0 완료(건너뛴 것이 있어도) · 2 실행 불가(긴 실행이 도는 중 · logs/ 없음)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "sidecar"))
from _run_lock import _live_holder
from office_claw_sidecar.config import get_chat_log_archive_dir, get_reports_dir

#: 그대로 두는 것 — chat_log.jsonl 은 유일한 런타임 기록, README.md 는 문서가 다룬다.
KEEP = {"chat_log.jsonl", "README.md"}
#: 이름 그대로 <reports>/ 아래로 가는 폴더. `archive-*` 도 같다.
REPORT_DIRS = {"nightly", "measurements", "e2e", "diagnostics", "skill_ab"}
LOCK_NAME = ".running.lock"

MOVE = "이동"
SKIP = "건너뜀(이미 있음)"
RMDIR = "빈 폴더 제거"
UNLOCK = "죽은 자물쇠 제거"


@dataclass
class Step:
    kind: str
    src: Path
    dst: Path | None = None


def _merge_dir(src_dir: Path, dst_dir: Path, steps: list[Step]) -> None:
    """폴더를 항목 단위로 합친다 — 대상에 같은 이름이 있으면 그 항목만 건너뛴다."""
    for child in sorted(src_dir.iterdir()):
        if child.name == LOCK_NAME:
            continue  # 자물쇠는 plan() 이 따로 다룬다
        target = dst_dir / child.name
        steps.append(Step(SKIP if target.exists() else MOVE, child, target))


def plan(logs: Path, reports: Path, archive: Path) -> list[Step]:
    steps: list[Step] = []
    for entry in sorted(logs.iterdir()):
        name = entry.name
        if name in KEEP:
            continue
        if entry.is_dir():
            if name == "officeclaw_backups" and not any(entry.iterdir()):
                steps.append(Step(RMDIR, entry))
            elif name in REPORT_DIRS or name.startswith("archive-"):
                _merge_dir(entry, reports / name, steps)
            else:
                _merge_dir(entry, reports / "legacy" / name, steps)
            continue
        if name.startswith("chat_log.") and name.endswith(".jsonl"):
            target = archive / name
        else:
            target = reports / "legacy" / name
        steps.append(Step(SKIP if target.exists() else MOVE, entry, target))
    lock = logs / "nightly" / LOCK_NAME
    if lock.exists():
        steps.append(Step(UNLOCK, lock))
    return steps


def apply(steps: list[Step], logs: Path) -> None:
    for s in steps:
        if s.kind == MOVE:
            assert s.dst is not None
            s.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(s.src), str(s.dst))  # 다른 폴더(저장소 밖)로의 이동
        elif s.kind == RMDIR:
            s.src.rmdir()
        elif s.kind == UNLOCK:
            s.src.unlink(missing_ok=True)
    # 항목을 다 내보내 비워진 원본 폴더는 치운다. 건너뛴 항목이 남은 폴더는 그대로 둔다.
    for entry in sorted(logs.iterdir()):
        if entry.is_dir() and entry.name not in KEEP and not any(entry.iterdir()):
            entry.rmdir()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="실제로 옮긴다 (기본은 dry-run)")
    ap.add_argument(
        "--logs-dir", type=Path, default=ROOT / "logs",
        help="옮길 원본 logs 폴더 (기본: 저장소의 logs/). 시험용.",
    )
    args = ap.parse_args()

    logs: Path = args.logs_dir.resolve()
    if not logs.is_dir():
        print(f"logs 폴더가 없습니다: {logs}", file=sys.stderr)
        return 2
    reports = get_reports_dir()
    archive = get_chat_log_archive_dir()

    mode = "APPLY" if args.apply else "DRY-RUN(출력만)"
    print(f"[{mode}] 원본 {logs}")
    print(f"        reports          → {reports}")
    print(f"        chat_log_archive → {archive}")

    holder = _live_holder(logs / "nightly" / LOCK_NAME)
    if holder:
        print(f"\n긴 실행이 도는 중입니다 — 자물쇠를 쥔 쪽: `{holder}`. 끝난 뒤에 돌리세요.")
        if args.apply:
            return 2
        print("(dry-run 이라 계획만 보여 준다. 이 상태에서 --apply 는 거부된다.)")

    steps = plan(logs, reports, archive)
    print()
    for s in steps:
        if s.dst is None:
            print(f"[{s.kind}] {_rel(s.src)}")
        else:
            print(f"[{s.kind}] {_rel(s.src)} → {s.dst}")

    moved = [s for s in steps if s.kind == MOVE]
    skipped = [s for s in steps if s.kind == SKIP]
    total_bytes = sum(_size(s.src) for s in moved)
    print()
    print(
        f"이동 {len(moved)}건 ({total_bytes / 1_048_576:.1f} MB) · 건너뜀 {len(skipped)}건 · "
        f"그 밖 {len(steps) - len(moved) - len(skipped)}건"
    )
    if skipped:
        print("건너뛴 항목은 대상에 같은 이름이 이미 있는 것이다 — 원본을 logs/ 에 그대로 둔다:")
        for s in skipped:
            print(f"  - {_rel(s.src)}")

    if not args.apply:
        print("\n(dry-run) 실제로 옮기려면 --apply 를 붙인다.")
        return 0
    apply(steps, logs)
    remaining = sorted(p.name for p in logs.iterdir())
    print(f"\n옮겼습니다. logs/ 에 남은 것: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
