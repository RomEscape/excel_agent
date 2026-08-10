"""에스컬레이션 큐 → 다음 라운드 학습 데이터.

지금까지는 실패를 사람이 발견해야 했다. 사용자가 "이거 안 되는데"라고 말하면
개발자가 로그를 뒤져 원인을 찾고 규칙이나 사례를 손으로 추가했다. 액션이 50종이고
한국어 표현은 무한하니 이 방식으로는 끝이 없다.

`excel_planner_escalation`이 남기는 큐에는 그 노동이 이미 담겨 있다.

- `final_tier=local_repair|strong` → 로컬은 틀렸는데 위 단계가 **맞힌** 사례.
  질문과 정답이 모두 있으니 그대로 학습 정답이 된다. 가장 값진 증류 샘플이다.
- `final_tier=failed` → 아무도 못 푼 사례. 정답이 없으니 학습에 넣으면 안 되고,
  사람이 봐야 할 목록으로 따로 뽑는다.

사용:
    python scripts/build_sft_from_escalations.py \
        --input ../logs/planner_escalations.jsonl \
        --output ../datasets/distill/excel_escalation_harvest_v1.jsonl \
        --unsolved-output ../logs/planner_unsolved.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_plan_validator import (
    PLANNER_ONLY_ACTIONS,
    SUPPORTED_ACTIONS,
)

SOLVED_TIERS = {"local_repair", "strong"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _plan_is_teachable(plan: Any) -> bool:
    """실행 가능한 액션만으로 이뤄진 계획인지.

    되묻기(clarify)로 끝난 턴은 정답 계획이 아니다. 이걸 학습에 넣으면
    "어려우면 되물어라"를 강화하게 되는데, 우리가 원하는 건 그 반대다.
    """
    if not isinstance(plan, list) or not plan:
        return False
    for step in plan:
        if not isinstance(step, dict):
            return False
        action = str(step.get("action", ""))
        if action in PLANNER_ONLY_ACTIONS or action not in SUPPORTED_ACTIONS:
            return False
    return True


def harvest(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter]:
    solved: list[dict[str, Any]] = []
    unsolved: list[dict[str, Any]] = []
    stats: Counter = Counter()
    seen: set[str] = set()

    for index, row in enumerate(rows):
        stats["total"] += 1
        tier = str(row.get("final_tier", ""))
        stats[f"tier:{tier}"] += 1
        instruction = str(row.get("instruction", "")).strip()
        if not instruction:
            stats["skipped_no_instruction"] += 1
            continue

        if tier not in SOLVED_TIERS:
            unsolved.append(row)
            continue

        plan = (row.get("output_json") or {}).get("action_plan")
        if not _plan_is_teachable(plan):
            stats["skipped_not_teachable"] += 1
            continue

        # 같은 문장이 반복 실패하면 큐에 여러 번 쌓인다. 그대로 넣으면
        # 그 문장만 과대표집된다.
        key = f"{instruction}|{json.dumps(plan, ensure_ascii=False, sort_keys=True)}"
        if key in seen:
            stats["skipped_duplicate"] += 1
            continue
        seen.add(key)

        solved.append(
            {
                "record_id": f"escalation:{tier}:{index}",
                "instruction": instruction,
                "output_json": row["output_json"],
                # 다이제스트가 함께 있으면 빌더가 통합문서를 다시 섞지 않는다 —
                # 정답이 그 통합문서를 보고 만들어졌으니 그래야 아귀가 맞는다.
                "digest": row.get("digest") or None,
            }
        )
        stats["harvested"] += 1

    return solved, unsolved, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="../logs/planner_escalations.jsonl")
    parser.add_argument("--output", required=True, help="학습에 넣을 수확 JSONL")
    parser.add_argument("--unsolved-output", default="", help="사람이 봐야 할 미해결 목록")
    args = parser.parse_args()

    rows = _read_jsonl(Path(args.input))
    solved, unsolved, stats = harvest(rows)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for item in solved:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    if args.unsolved_output:
        unsolved_path = Path(args.unsolved_output)
        unsolved_path.parent.mkdir(parents=True, exist_ok=True)
        with unsolved_path.open("w", encoding="utf-8") as fh:
            for item in unsolved:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"큐 레코드        : {stats['total']}")
    for tier in sorted(k for k in stats if k.startswith("tier:")):
        print(f"  {tier:<20}: {stats[tier]}")
    print(f"중복 제외        : {stats['skipped_duplicate']}")
    print(f"학습 불가 제외   : {stats['skipped_not_teachable']}")
    print(f"수확(학습 후보)  : {stats['harvested']} -> {out_path}")
    print(f"미해결(사람 확인): {len(unsolved)}")


if __name__ == "__main__":
    main()
