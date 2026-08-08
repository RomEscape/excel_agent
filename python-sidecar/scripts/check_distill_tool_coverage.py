"""
증류 데이터셋의 action_plan이 현재 tool-calling 스키마로 표현 가능한지 검사한다.

SFT 데이터를 tool_calls 형식으로 재생성하기 전에, 라벨 어휘(excel_live.*)와
런타임 툴 어휘(get_excel_tools)가 얼마나 겹치는지 먼저 알아야 한다.
겹치지 않는 액션은 학습해도 실행 경로가 없으므로 데이터를 다시 만들어야 한다.

사용:
    python scripts/check_distill_tool_coverage.py <jsonl...>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_tool_schemas import (
    action_to_tool_name,
    get_excel_tools,
)


def iter_action_plans(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        plan = (row.get("target") or {}).get("action_plan") or []
        if isinstance(plan, list):
            yield row, plan


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("사용법: check_distill_tool_coverage.py <jsonl...>")
        raise SystemExit(2)

    tool_names = {t.get("function", {}).get("name") for t in get_excel_tools()}

    mapped: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    total_rows = 0
    fully_mappable_rows = 0

    for path in paths:
        for _row, plan in iter_action_plans(path):
            total_rows += 1
            row_ok = bool(plan)
            for step in plan:
                action = step.get("action", "")
                tool = action_to_tool_name(action)
                if tool and tool in tool_names:
                    mapped[f"{action} -> {tool}"] += 1
                else:
                    unmapped[action] += 1
                    row_ok = False
            if row_ok:
                fully_mappable_rows += 1

    print(f"툴 스키마 함수 수: {len(tool_names)}")
    print(f"검사한 레코드: {total_rows}")
    pct = (fully_mappable_rows / total_rows * 100) if total_rows else 0.0
    print(f"모든 스텝이 툴로 매핑되는 레코드: {fully_mappable_rows} ({pct:.1f}%)")

    print(f"\n매핑 성공 액션 {len(mapped)}종:")
    for key, count in mapped.most_common():
        print(f"  {count:4d}  {key}")

    print(f"\n매핑 실패 액션 {len(unmapped)}종 (툴 없음 → 실행 경로 없음):")
    for action, count in unmapped.most_common():
        print(f"  {count:4d}  {action}")

    unused = sorted(tool_names - {k.split(" -> ")[1] for k in mapped})
    print(f"\n데이터셋에 한 번도 안 쓰인 툴 {len(unused)}개:")
    for name in unused:
        print(f"        {name}")


if __name__ == "__main__":
    main()
