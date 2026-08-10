"""검증 공백 측정 — 검증기가 틀린 결과를 성공으로 통과시키는 비율.

LLM을 쓰지 않으므로 GPU가 학습에 묶여 있어도 돌릴 수 있다.

틀린 결과에는 성격이 다른 두 부류가 있고, 이 둘을 섞으면 수치가 무의미해진다.

1. 인자 오류 (planner 책임)
   사용자는 C3를 원했는데 계획이 D3를 지목했다. 실행기는 D3에 정확히 썼으므로
   사후조건 검증은 통과시키는 게 맞다. 검증기는 사용자 의도를 모른다.
   → 이 수치는 검증 강화로 내려가지 않는다. 계획 단계에서 잡아야 한다.

2. 실행 거짓말 (executor/verifier 책임)
   인자는 옳은데 워크북이 그대로다. 보호된 시트, 병합 셀, 삼켜진 쓰기.
   written_cells는 정상으로 올라온다.
   → 워크북을 다시 읽는 검증만이 잡을 수 있다. 이 수치가 이번 수정의 대상이다.

각 부류마다 "예전 판정(몇 칸을 건드렸나)"과 "지금 판정"을 나란히 찍는다.

    python scripts/run_verifier_gap.py --output-json ../logs/verifier_gap.json

`run_verifier_suite.py`와 역할이 다르다. 이쪽은 **여러 액션에 걸친 넓이** —
정렬·필터·차트까지 10종 케이스에서 두 부류를 가른다. 저쪽은 **write/clear
사후조건의 깊이** — 값 하나가 틀린 경우, 일부만 쓰인 경우처럼 상태 변이를
종류별로 잰다. 검증기를 손대면 둘 다 돌려야 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.excel_e2e.bench_cases import all_cases
from tests.excel_e2e.bench_core import run_plan_with_verification


# 인자는 그대로 두고 실행기만 일을 안 하게 만든다. 성공 보고는 그대로 올린다.
def _swallow_mutations(service: Any) -> None:
    def _fake_write(workbook_id, sheet_name, start_cell, values_2d, **_kw):
        rows = len(values_2d or [])
        cols = max((len(r) for r in values_2d or []), default=0)
        return {"written_cells": rows * cols, "address": str(start_cell)}

    def _fake_clear(workbook_id, sheet_name, target_range, **_kw):
        return {"cleared_cells": 1, "address": str(target_range)}

    service.write_range = _fake_write
    service.clear_range = _fake_clear


_SABOTAGE_TARGETS = {"write", "clear", "formula"}


def _rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in all_cases():
        runs = [("argument_error", case.mutant, None)]
        if case.category in _SABOTAGE_TARGETS:
            runs.append(("execution_lie", case.oracle, _swallow_mutations))

        for kind, plan, sabotage in runs:
            outcome = run_plan_with_verification(case, plan, sabotage=sabotage)
            rows.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "kind": kind,
                    "legacy_passed": outcome.legacy_passed,
                    "verifier_passed": outcome.verifier_passed,
                    "file_correct": outcome.file_correct,
                    "verify_detail": outcome.verify_detail,
                    "error": outcome.error,
                }
            )
    return rows


def _section(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    print(f"\n  {title}")
    print("  " + "─" * 70)
    print(f"  {'케이스':<26} {'예전':<6} {'지금':<6} 판정 근거")
    caught = 0
    for row in rows:
        was = "통과" if row["legacy_passed"] else "차단"
        now = "통과" if row["verifier_passed"] else "차단"
        if not row["verifier_passed"]:
            caught += 1
        print(f"  {row['case_id']:<26} {was:<6} {now:<6} {row['verify_detail'][:44]}")
    total = len(rows)
    print(f"  → 검증이 잡아낸 비율 {caught}/{total}")
    return {"total": total, "caught": caught, "cases": [r["case_id"] for r in rows]}


def main() -> int:
    parser = argparse.ArgumentParser(description="검증 공백 측정")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    rows = _rows()
    arg_rows = [r for r in rows if r["kind"] == "argument_error"]
    lie_rows = [r for r in rows if r["kind"] == "execution_lie"]

    lie = _section("실행 거짓말 — 인자는 맞는데 워크북이 안 바뀐 경우", lie_rows)
    arg = _section("인자 오류 — 계획이 엉뚱한 대상을 지목한 경우", arg_rows)

    oracle_rows = [
        {
            "case_id": case.case_id,
            **{
                k: v
                for k, v in vars(run_plan_with_verification(case, case.oracle)).items()
                if k != "case_id"
            },
        }
        for case in all_cases()
    ]
    false_fail = [r for r in oracle_rows if not r["verifier_passed"]]
    print("\n  정답 계획을 잘못 막았는가(false fail)")
    print("  " + "─" * 70)
    print(f"  {len(false_fail)}/{len(oracle_rows)}" + (f" — {[r['case_id'] for r in false_fail]}" if false_fail else " (없음)"))

    report = {
        "execution_lie": lie,
        "argument_error": arg,
        "false_fail_count": len(false_fail),
        "false_fail_cases": [r["case_id"] for r in false_fail],
        "rows": rows,
    }
    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
