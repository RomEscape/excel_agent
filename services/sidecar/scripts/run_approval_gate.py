"""승인 게이트 손실을 측정해 JSON으로 남긴다.

승인 경로(`/excel-live/command` → `/excel-live/approval`)와 대조 경로
(`approve: true` 단일 호출)에 같은 계획을 태워 결과를 나란히 놓는다.

수정 전후를 같은 파일 이름으로 비교할 수 있게 단계별로 따로 저장한다.

    uv run python scripts/run_approval_gate.py                      # 요약만
    uv run python scripts/run_approval_gate.py --save baseline      # logs/approval_gate_baseline.json
    uv run python scripts/run_approval_gate.py --save after-plan-approval
    uv run python scripts/run_approval_gate.py --diff baseline after-plan-approval

PowerShell에서 한글이 깨지면:

    $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGS_DIR = ROOT.parent / "logs"


def _report_path(label: str) -> Path:
    return LOGS_DIR / f"approval_gate_{label}.json"


def _run() -> dict[str, Any]:
    import pytest
    from fastapi.testclient import TestClient

    from office_claw_sidecar.main import app
    from tests.excel_e2e import approval_gate

    client = TestClient(app)
    patcher = pytest.MonkeyPatch()
    try:
        outcomes = approval_gate.run_all(client, patcher)
    finally:
        patcher.undo()
    return approval_gate.to_report(outcomes)


def _print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("\n승인 게이트 손실 측정")
    print("=" * 78)
    print(
        f"  케이스 {summary['cases']}건 (다단계 {summary['multi_step_cases']}건) · "
        f"계획 {summary['planned_steps']}단계 중 {summary['lost_steps']}단계 소실"
    )
    print(f"  계획 이행률       {summary['completion_rate'] * 100:.1f}%")
    print(
        f"  파일 정합         대조 {summary['direct_file_correct']}/{summary['cases']} · "
        f"승인 {summary['gated_file_correct']}/{summary['cases']}"
    )
    print(f"  두 경로 결과 상이  {summary['diverged']}건")
    print(f"  서식 소실         {summary['formatting_lost']}건")
    print(f"  검증만 조용히 소실 {summary['silent_verification_loss']}건")
    print(
        f"  롤백 소실         {summary['rollback_lost']}/{summary['rollback_measured']}건 "
        "(실행기가 거짓말했을 때 되돌리지 못함)"
    )

    print("\n  케이스별")
    print("  " + "-" * 76)
    for case in report["cases"]:
        mark = "손실" if case["lost_steps"] else "동일"
        print(
            f"  [{mark}] {case['case_id']:<24} "
            f"{case['planned_steps']}단계 중 {case['lost_steps']}단계 소실 "
            f"({case['loss_kind']})"
        )
        print(f"         {case['description']}")
        print(f"         대조 {case['direct']['cells']}")
        print(f"         승인 {case['gated']['cells']}")
        if case["rollback"]["measured"]:
            print(
                f"         거짓말 실행 후 값 — 대조 {case['rollback']['direct']!r} / "
                f"승인 {case['rollback']['gated']!r}"
            )
    print()


def _print_diff(before: dict[str, Any], after: dict[str, Any], labels: tuple[str, str]) -> None:
    keys = [
        ("completion_rate", "계획 이행률", True),
        ("lost_steps", "소실 단계", False),
        ("gated_file_correct", "승인 경로 파일 정합", False),
        ("diverged", "두 경로 결과 상이", False),
        ("formatting_lost", "서식 소실", False),
        ("silent_verification_loss", "검증 소실", False),
        ("rollback_lost", "롤백 소실", False),
    ]
    print(f"\n{labels[0]} → {labels[1]}")
    print("=" * 78)
    for key, label, is_rate in keys:
        old = before["summary"].get(key, 0)
        new = after["summary"].get(key, 0)
        arrow = "→"
        if is_rate:
            print(f"  {label:<22} {old * 100:6.1f}% {arrow} {new * 100:6.1f}%")
        else:
            print(f"  {label:<22} {old:6} {arrow} {new:6}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="승인 게이트 손실 측정")
    parser.add_argument("--save", metavar="LABEL", help="logs/approval_gate_<LABEL>.json 으로 저장")
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="저장된 두 리포트를 비교한다 (측정을 다시 돌리지 않음)",
    )
    args = parser.parse_args()

    if args.diff:
        before_path, after_path = (_report_path(label) for label in args.diff)
        for path in (before_path, after_path):
            if not path.exists():
                print(f"리포트가 없습니다: {path}")
                return 1
        _print_diff(
            json.loads(before_path.read_text(encoding="utf-8")),
            json.loads(after_path.read_text(encoding="utf-8")),
            tuple(args.diff),
        )
        return 0

    report = _run()
    _print_report(report)

    if args.save:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = _report_path(args.save)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
