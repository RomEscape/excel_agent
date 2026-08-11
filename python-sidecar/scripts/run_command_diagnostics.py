"""같은 명령을 반복해서 태우고, 턴 로그를 남긴 뒤 케이스 단위로 접어 보여 준다.

    uv run python scripts/run_command_diagnostics.py                    # 3회 반복
    uv run python scripts/run_command_diagnostics.py -n 5               # 5회 반복
    uv run python scripts/run_command_diagnostics.py --case 정렬-명확    # 일부만
    uv run python scripts/run_command_diagnostics.py --label after-fix  # 이름 붙여 보존
    uv run python scripts/run_command_diagnostics.py --analyze <실행id>  # 다시 안 돌리고 분석만
    uv run python scripts/run_command_diagnostics.py --analyze-all      # 쌓인 실행 전부

실행마다 `logs/diagnostics/<실행id>.jsonl`에 턴을 그대로 남기고, 같은 이름의
`.report.json`에 집계를 남긴다. **덮어쓰지 않으므로** 이력이 쌓인다. 나중에
`scripts/show_turns.py --log logs/diagnostics/<실행id>.jsonl`로 개별 턴을 펼쳐 볼 수 있다.

로컬 LLM을 실제로 부르므로 케이스당 수 초가 걸린다. 12케이스 × 3회면 몇 분 걸린다.

PowerShell에서 한글이 깨지면:

    $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIAG_DIR = ROOT.parent / "logs" / "diagnostics"


def _run_id(label: str) -> str:
    from office_claw_sidecar.services.decision_trace import KST

    stamp = datetime.now(KST).strftime("%m%d-%H%M%S")
    return f"{stamp}-{label}" if label else stamp


def _run(run_id: str, *, repeats: int, only: list[str]) -> tuple[Path, dict[str, Any]]:
    """배터리를 돌린다. 턴 로그는 이 실행 전용 파일로 보낸다."""
    import pytest
    from fastapi.testclient import TestClient

    from office_claw_sidecar.main import app
    from office_claw_sidecar.services import decision_trace, trace_digest
    from tests.excel_e2e import command_battery

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DIAG_DIR / f"{run_id}.jsonl"

    cases = command_battery.ALL_CASES
    if only:
        cases = [c for c in cases if c.case_id in only]
        if not cases:
            raise SystemExit(f"그런 케이스가 없습니다: {', '.join(only)}")

    client = TestClient(app)
    patcher = pytest.MonkeyPatch()
    # 이 실행이 만든 턴만 이 파일에 담는다. 공용 chat_log.jsonl과 섞이면
    # 실행 간 비교를 할 수 없다.
    patcher.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    total = len(cases) * repeats
    done = 0
    started = time.perf_counter()

    def _progress(outcome) -> None:
        nonlocal done
        done += 1
        mark = "완료" if outcome.ok else ("되물음" if outcome.ask else "실패")
        if outcome.approval_required:
            mark = "승인대기"
        print(
            f"  [{done:>3}/{total}] {outcome.case_id:<12} {mark:<6} "
            f"{outcome.action or '-':<28} {outcome.elapsed_ms:>6}ms"
            + (f"  {outcome.error}" if outcome.error else "")
        )

    print(f"\n케이스 {len(cases)}개 × {repeats}회 = {total}턴 → {log_path.name}")
    print("=" * 78)
    try:
        command_battery.run_all(
            client, patcher, repeats=repeats, suite=run_id, cases=cases, on_result=_progress
        )
    finally:
        patcher.undo()

    print(f"\n총 {round(time.perf_counter() - started, 1)}초")

    turns = list(_read(log_path))
    report = trace_digest.to_report(trace_digest.build(turns))
    report["run_id"] = run_id
    report["repeats"] = repeats
    (DIAG_DIR / f"{run_id}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return log_path, report


def _read(path: Path):
    from office_claw_sidecar.services.trace_report import read_turns

    return read_turns(path)


def _analyze(paths: list[Path]) -> None:
    """저장된 턴 로그를 접어서 보여 준다. 여러 개를 주면 합쳐서 본다."""
    from office_claw_sidecar.services import trace_digest

    turns: list[dict[str, Any]] = []
    for path in paths:
        turns.extend(_read(path))
    if not turns:
        print("  분석할 턴이 없습니다.")
        return
    digest = trace_digest.build(turns)
    print(f"\n  대상 {len(paths)}개 실행")
    print(trace_digest.render(digest))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="명령 진단 배터리")
    parser.add_argument("-n", "--repeats", type=int, default=3, help="케이스당 반복 횟수 (기본 3)")
    parser.add_argument("--case", action="append", default=[], help="이 케이스만 (여러 번 지정 가능)")
    parser.add_argument("--label", default="", help="실행 id에 붙일 이름")
    parser.add_argument("--analyze", default="", help="실행 id 하나를 다시 돌리지 않고 분석")
    parser.add_argument("--analyze-all", action="store_true", help="쌓인 실행 전부를 합쳐 분석")
    args = parser.parse_args()

    if args.analyze_all:
        _analyze(sorted(DIAG_DIR.glob("*.jsonl")))
        return 0
    if args.analyze:
        path = DIAG_DIR / f"{args.analyze}.jsonl"
        if not path.exists():
            print(f"그런 실행이 없습니다: {path}")
            return 1
        _analyze([path])
        return 0

    run_id = _run_id(args.label)
    log_path, _ = _run(run_id, repeats=args.repeats, only=args.case)
    _analyze([log_path])
    print(f"  턴 로그  {log_path}")
    print(f"  집계     {DIAG_DIR / f'{run_id}.report.json'}")
    print(f"  개별 턴  uv run python scripts/show_turns.py --log {log_path} --failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
