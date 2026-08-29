"""검증기 상태 변이 수트 — 단계별로 돌리고 결과를 보존한다.

    python scripts/run_verifier_suite.py            # V0·V1·V2 전부
    python scripts/run_verifier_suite.py --stage V2 # 현재 검증기만
    python scripts/run_verifier_suite.py --diff     # 단계 간 변화만

결과는 `logs/`에 단계별로 남는다. 나중에 "어느 수정이 무엇을 고쳤는지"를
되짚으려면 한 파일에 덮어쓰면 안 된다.

    logs/verifier_baseline.json            V0 — 검증 강화 이전
    logs/verifier_after_write_range.json   V1
    logs/verifier_after_clear_range.json   V2 — 현재
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.config import get_logs_dir
from tests.excel_e2e.verifier_mutants import (
    STAGES,
    all_cases,
    run_case,
    stage_label,
    summarize,
)

KST = timezone(timedelta(hours=9))

_FILENAME = {
    "V0": "verifier_baseline.json",
    "V1": "verifier_after_write_range.json",
    "V2": "verifier_after_clear_range.json",
}

_MARK = {"false_pass": "놓침", "false_fail": "과잉", "true_pass": "정상", "true_fail": "포착"}


def run_stage(stage: str) -> dict[str, object]:
    rows = [run_case(case, stage=stage) for case in all_cases()]
    return {
        "stage": stage,
        "label": stage_label(stage),
        "at": datetime.now(KST).isoformat(),
        "summary": summarize(rows),
        "cases": rows,
    }


def print_stage(report: dict) -> None:
    summary = report["summary"]
    print(f"\n  {report['stage']} — {report['label']}")
    print("  " + "─" * 76)
    for row in report["cases"]:
        mark = _MARK[row["classification"]]
        truth = "정상" if row["ground_truth_pass"] else "깨짐"
        verdict = "통과" if row["verifier_passed"] else "차단"
        print(
            f"  {mark:<4} {row['action']:<12} {row['kind']:<18} "
            f"파일={truth} 검증={verdict}  {row['description']}"
        )
    print("  " + "─" * 76)
    print(
        f"  false pass {summary['false_pass']}/{summary['broken_states']} "
        f"({summary['false_pass_rate']:.0%})    "
        f"false fail {summary['false_fail']}/{summary['intact_states']} "
        f"({summary['false_fail_rate']:.0%})"
    )
    if summary["missed_kinds"]:
        print(f"  놓친 변이: {', '.join(summary['missed_kinds'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="검증기 변이 수트")
    parser.add_argument("--stage", default="", choices=["", *STAGES], help="한 단계만")
    parser.add_argument("--diff", action="store_true", help="단계 간 변화만 표시")
    parser.add_argument("--no-save", action="store_true", help="파일로 남기지 않음")
    args = parser.parse_args()

    stages = [args.stage] if args.stage else list(STAGES)
    logs = get_logs_dir()
    logs.mkdir(parents=True, exist_ok=True)

    reports = []
    for stage in stages:
        report = run_stage(stage)
        reports.append(report)
        if not args.diff:
            print_stage(report)
        if not args.no_save:
            path = logs / _FILENAME[stage]
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            if not args.diff:
                print(f"  저장: {path}")

    if len(reports) > 1:
        print("\n  단계별 변화")
        print("  " + "─" * 76)
        print(f"  {'단계':<6} {'설명':<26} {'false pass':<14} {'false fail'}")
        for report in reports:
            s = report["summary"]
            print(
                f"  {report['stage']:<6} {report['label']:<26} "
                f"{s['false_pass']}/{s['broken_states']} ({s['false_pass_rate']:.0%})".ljust(62)
                + f"{s['false_fail']}/{s['intact_states']} ({s['false_fail_rate']:.0%})"
            )
        first, last = reports[0]["summary"], reports[-1]["summary"]
        print(
            f"\n  false pass {first['false_pass_rate']:.0%} → {last['false_pass_rate']:.0%}, "
            f"false fail {first['false_fail_rate']:.0%} → {last['false_fail_rate']:.0%}"
        )
        if last["missed_kinds"]:
            print(f"  아직 놓치는 변이: {', '.join(last['missed_kinds'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
