"""플래너 SFT 데이터셋 분포 점검.

학습을 돌리기 전에 "이 데이터로 배우면 무엇이 달라지는가"를 숫자로 확인한다.
v3에서 놓쳤던 것들이 정확히 이 지표들이었다 — 다이제스트 0건, 되묻기 0건,
정답 시트의 16.8%가 `Sales_Data`.

사용:
    python scripts/audit_planner_sft.py ../datasets/train/planner_sft_v4_train.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

DIGEST_MARKER = "현재 통합문서 상태"
HISTORY_MARKER = "이전 대화:"
EXISTING_SHEET_KEYS = ("sheet_name", "source_sheet", "left_sheet", "right_sheet")


def _load(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _pct(count: int, total: int) -> str:
    return f"{count:5d} ({(count / total * 100 if total else 0):5.1f}%)"


def audit(path: Path) -> dict[str, Any]:
    rows = _load(path)
    total = len(rows)
    digest = history = clarify = 0
    grounded = ungrounded = 0
    sheet_counter: collections.Counter[str] = collections.Counter()
    action_counter: collections.Counter[str] = collections.Counter()
    ungrounded_samples: list[str] = []

    for row in rows:
        messages = row.get("messages") or []
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        assistant = next((m["content"] for m in messages if m["role"] == "assistant"), "")
        if DIGEST_MARKER in user:
            digest += 1
        if HISTORY_MARKER in user:
            history += 1
        try:
            target = json.loads(assistant)
        except json.JSONDecodeError:
            continue

        plan = target.get("action_plan") or []
        if plan and str(plan[0].get("action")) == "excel_live.clarify":
            clarify += 1
        for step in plan:
            action_counter[str(step.get("action"))] += 1
            params = step.get("params") or {}
            for key in EXISTING_SHEET_KEYS:
                name = str(params.get(key) or "").strip()
                if not name:
                    continue
                sheet_counter[name] += 1
                # 정답이 가리키는 시트가 프롬프트의 통합문서 상태에 실제로 있는가.
                # 없으면 그 레코드는 "없는 시트를 쓰라"고 가르치고 있는 것이다.
                if f"- 시트 {name} " in user or f"- 시트 {name}(" in user:
                    grounded += 1
                else:
                    ungrounded += 1
                    if len(ungrounded_samples) < 5:
                        ungrounded_samples.append(f"{row.get('record_id')}: {key}={name}")

    print(f"총 레코드              : {total}")
    print(f"통합문서 상태 포함     : {_pct(digest, total)}")
    print(f"이전 대화 포함         : {_pct(history, total)}")
    print(f"되묻기 정답            : {_pct(clarify, total)}")
    print()
    sheet_total = grounded + ungrounded
    print(f"정답의 기존 시트 참조  : {sheet_total}건")
    print(f"  다이제스트에 존재    : {_pct(grounded, sheet_total)}")
    print(f"  다이제스트에 없음    : {_pct(ungrounded, sheet_total)}")
    for sample in ungrounded_samples:
        print(f"    - {sample}")
    print()
    print("정답에 등장하는 시트명 상위 10개:")
    for name, count in sheet_counter.most_common(10):
        print(f"  {name:<20} {_pct(count, sheet_total)}")
    print()
    print("정답 액션 상위 12개:")
    action_total = sum(action_counter.values())
    for name, count in action_counter.most_common(12):
        print(f"  {name:<38} {_pct(count, action_total)}")

    return {
        "total": total,
        "digest": digest,
        "clarify": clarify,
        "ungrounded_sheets": ungrounded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="점검할 SFT JSONL 경로")
    args = parser.parse_args()
    audit(Path(args.path))


if __name__ == "__main__":
    main()
