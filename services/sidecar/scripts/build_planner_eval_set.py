"""고정 평가셋을 그림자 평가용 JSONL로 굽는다.

`planner_eval_cases.py`의 손으로 쓴 케이스에 통합문서 다이제스트를 붙인다.
다이제스트는 프로덕션과 **같은 렌더러**(`render_workbook_digest`)를 통과시킨다 —
평가에서만 다른 모양으로 보여주면 측정값이 프로덕션과 어긋난다.

    python scripts/build_planner_eval_set.py \
        --output ../../datasets/eval/planner_eval_v1.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planner_eval_cases import WORKBOOKS, all_cases

from office_claw_sidecar.services.excel_live_plan_validator import SUPPORTED_ACTIONS
from office_claw_sidecar.services.excel_workbook_digest import render_workbook_digest


def build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in all_cases():
        digest = WORKBOOKS.get(case.workbook)
        if digest is None:
            raise KeyError(f"{case.case_id}: 통합문서 '{case.workbook}'가 없습니다.")
        unknown = [a for a in case.expected if a not in SUPPORTED_ACTIONS]
        if unknown:
            raise ValueError(f"{case.case_id}: 검증기가 모르는 액션 {unknown}")
        rows.append(
            {
                "record_id": case.case_id,
                "category": case.category,
                "workbook": case.workbook,
                "input": {
                    "instruction": case.instruction,
                    "workbook_digest_text": render_workbook_digest(digest),
                    "context_hints": {
                        "sheet_name": str(digest.get("active_sheet") or ""),
                        "target_range": "",
                    },
                },
                "target": {
                    "action_plan": [{"action": action} for action in case.expected]
                },
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> str:
    by_category = Counter(str(r.get("category")) for r in rows)
    first_actions = Counter(
        str(r["target"]["action_plan"][0]["action"]) for r in rows if r["target"]["action_plan"]
    )
    covered = set(first_actions)
    for row in rows:
        for step in row["target"]["action_plan"]:
            covered.add(str(step.get("action")))
    missing = sorted(set(SUPPORTED_ACTIONS) - covered)
    instructions = [str(r["input"]["instruction"]) for r in rows]

    lines = [
        f"총 {len(rows)}건",
        "분류별: " + ", ".join(f"{k}={v}" for k, v in sorted(by_category.items())),
        f"등장 액션 {len(covered)}/{len(SUPPORTED_ACTIONS)}",
        f"미커버 액션: {missing if missing else '없음'}",
        f"문장 중복: {len(instructions) - len(set(instructions))}건",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="플래너 고정 평가셋 생성")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = build_rows()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(summarize(rows))
    print(f"저장: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
