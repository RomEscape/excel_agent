"""
플래너 SFT 데이터의 액션 분포와 사용자 문장을 뽑아 본다.

50개 명령 회귀(리터럴 값 입력을 set_formula로 오해)의 원인을 찾으려면
write_range/set_formula 예제가 각각 어떤 문장으로 학습됐는지 봐야 한다.

Windows 콘솔이 cp949라 한글이 깨지므로 결과는 UTF-8 파일로 쓴다.

사용:
    python scripts/analyze_planner_sft_actions.py \
        --jsonl ../../datasets/distill/planner_sft_v2_train.jsonl \
        --output ../../logs/planner_sft_action_analysis.md
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

# build_planner_prompt가 사용자 메시지를 붙이는 꼬리 부분을 되짚는다.
USER_LINE_PATTERNS = [
    re.compile(r"사용자 메시지\s*[:：]\s*(.+?)(?:\n\s*\n|\Z)", re.DOTALL),
    re.compile(r"사용자 요청\s*[:：]\s*(.+?)(?:\n\s*\n|\Z)", re.DOTALL),
    re.compile(r"요청\s*[:：]\s*(.+?)(?:\n\s*\n|\Z)", re.DOTALL),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 SFT 액션 분포 분석")
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=25)
    return parser.parse_args()


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


def answer_params(answer: str) -> dict:
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    steps = parsed.get("action_plan") or []
    if steps and isinstance(steps[0], dict):
        return steps[0].get("params") or {}
    return {}


def extract_user_message(prompt: str) -> str:
    for pattern in USER_LINE_PATTERNS:
        match = pattern.search(prompt)
        if match:
            return " ".join(match.group(1).split())
    # 패턴을 못 찾으면 마지막 비어 있지 않은 줄을 쓴다.
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def main() -> None:
    args = parse_args()
    rows = [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]

    first_counter: collections.Counter[str] = collections.Counter()
    by_action: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)

    for row in rows:
        messages = row["messages"]
        seq = action_seq(messages[-1]["content"])
        if not seq:
            continue
        first_counter[seq[0]] += 1
        by_action[seq[0]].append(
            (extract_user_message(messages[0]["content"]), answer_params(messages[-1]["content"]))
        )

    lines: list[str] = []
    lines.append(f"# 플래너 SFT 액션 분석 — {args.jsonl.name}")
    lines.append("")
    lines.append(f"총 레코드: {len(rows)}")
    lines.append("")
    lines.append("## first action 분포")
    lines.append("")
    lines.append("| 건수 | action |")
    lines.append("|---|---|")
    for action, count in first_counter.most_common():
        lines.append(f"| {count} | `{action}` |")

    for action in ("excel_live.write_range", "excel_live.set_formula"):
        lines.append("")
        lines.append(f"## `{action}` 학습 문장 (중복 제거, 최대 {args.samples}건)")
        lines.append("")
        seen: set[str] = set()
        shown = 0
        for message, params in by_action.get(action, []):
            if message in seen:
                continue
            seen.add(message)
            shown += 1
            value_keys = {k: v for k, v in params.items() if k in ("values", "formula_a1", "target_range")}
            lines.append(f"- `{message[:110]}`")
            lines.append(f"  - params: `{json.dumps(value_keys, ensure_ascii=False)[:150]}`")
            if shown >= args.samples:
                break
        lines.append("")
        lines.append(f"(고유 문장 {len(seen)}건 / 전체 {len(by_action.get(action, []))}건)")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DONE] {args.output}")


if __name__ == "__main__":
    main()
