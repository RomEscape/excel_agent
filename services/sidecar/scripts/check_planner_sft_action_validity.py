"""
플래너 SFT 데이터가 프롬프트의 '허용 action' 목록 밖 액션을 가르치고 있는지 검사한다.

프롬프트에 없는 액션을 정답으로 학습하면 모델은 실행할 수 없는 계획을 뱉게 되고,
비슷한 이름의 유효 액션(set_formula 등)으로 확률이 새는 부작용도 생긴다.

허용 목록은 build_planner_prompt가 실제로 출력하는 문자열에서 뽑는다 —
목록을 여기에 다시 적으면 프롬프트가 바뀔 때 조용히 어긋난다.

사용:
    python scripts/check_planner_sft_action_validity.py \
        --jsonl ../../datasets/distill/planner_sft_v2_train.jsonl \
        --output ../../logs/planner_sft_action_validity.md
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 SFT 액션 유효성 검사")
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def allowed_actions() -> set[str]:
    prompt = build_planner_prompt("샘플", context={}, planner_model="probe")
    return set(re.findall(r"excel_live\.[a-z_]+", prompt))


def action_seq(answer: str) -> list[str]:
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    steps = parsed.get("action_plan")
    if not isinstance(steps, list):
        return []
    return [str(s.get("action", "")) for s in steps if isinstance(s, dict)]


def main() -> None:
    args = parse_args()
    allowed = allowed_actions()
    rows = [
        json.loads(line)
        for line in args.jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    used: collections.Counter[str] = collections.Counter()
    invalid_rows = 0
    for row in rows:
        seq = action_seq(row["messages"][-1]["content"])
        for action in seq:
            used[action] += 1
        if any(a not in allowed for a in seq):
            invalid_rows += 1

    invalid = {a: c for a, c in used.items() if a not in allowed}
    valid_total = sum(c for a, c in used.items() if a in allowed)
    invalid_total = sum(invalid.values())

    lines: list[str] = []
    lines.append(f"# 플래너 SFT 액션 유효성 — {args.jsonl.name}")
    lines.append("")
    lines.append(f"- 레코드: {len(rows)}")
    lines.append(f"- 허용 액션 종류: {len(allowed)}")
    lines.append(f"- 등장 액션 종류: {len(used)}")
    lines.append(f"- 유효 액션 등장: {valid_total}회")
    lines.append(f"- **허용 목록 밖 액션 등장: {invalid_total}회** (레코드 {invalid_rows}건)")
    lines.append("")
    lines.append("## 허용 목록에 없는 액션")
    lines.append("")
    if invalid:
        lines.append("| 건수 | action | 비슷한 유효 액션 |")
        lines.append("|---|---|---|")
        for action, count in sorted(invalid.items(), key=lambda kv: -kv[1]):
            tail = action.split(".", 1)[-1]
            near = [a for a in sorted(allowed) if tail in a or a.split(".", 1)[-1] in tail]
            lines.append(f"| {count} | `{action}` | {', '.join(f'`{a}`' for a in near) or '—'} |")
    else:
        lines.append("없음.")

    lines.append("")
    lines.append("## 사용되지 않은 허용 액션")
    lines.append("")
    unused = sorted(a for a in allowed if a not in used)
    lines.append(", ".join(f"`{a}`" for a in unused) if unused else "없음.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    # Windows 콘솔이 cp949라 비ASCII를 쓰면 UnicodeEncodeError로 죽는다.
    print(f"[DONE] {args.output} invalid={invalid_total} kinds={len(invalid)}")


if __name__ == "__main__":
    main()
