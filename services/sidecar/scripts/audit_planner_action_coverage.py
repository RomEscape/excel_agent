"""
플래너 액션을 세 곳에서 교차 검증한다.

  1. tool_registry — 실제 실행 가능한 액션 (권한의 단일 소스)
  2. 플래너 프롬프트의 '허용 action' 목록 — 모델이 고를 수 있는 후보
  3. SFT 학습 데이터가 정답으로 가르치는 액션

셋이 어긋나면 조용히 망가진다.
  - 레지스트리에 있는데 프롬프트에 없으면: 모델이 그 기능을 영영 못 고른다.
  - 프롬프트에 없는데 데이터가 가르치면: 실행 불가 계획을 학습한다.
  - 프롬프트에 있는데 데이터에 없으면: 그 액션은 사실상 미학습이다.

사용:
    python scripts/audit_planner_action_coverage.py \
        --jsonl ../../datasets/distill/planner_sft_v2_train.jsonl \
        --output ../../logs/planner_action_coverage.md
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
from office_claw_sidecar.services.tool_registry import TOOL_REGISTRY

PREFIX = "excel_live."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 액션 3자 교차 검증")
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def registry_actions() -> set[str]:
    return {t.name for t in TOOL_REGISTRY if t.name.startswith(PREFIX)}


def prompt_actions() -> set[str]:
    prompt = build_planner_prompt("샘플", context={}, planner_model="probe")
    found: set[str] = set()
    # 허용 목록은 "- excel_live.xxx" 형태의 항목 줄로만 적힌다.
    # 설명 괄호 안에 언급된 이름(예: drop_column 설명의 clear_range)은 후보가 아니다.
    for line in prompt.splitlines():
        match = re.match(r"\s*-\s+(excel_live\.[a-z_]+)", line)
        if match:
            found.add(match.group(1))
    return found


def dataset_actions(path: Path) -> collections.Counter:
    counter: collections.Counter[str] = collections.Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        answer = row["messages"][-1]["content"]
        match = re.search(r"\{.*\}", answer, re.DOTALL)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        for step in parsed.get("action_plan") or []:
            if isinstance(step, dict) and step.get("action"):
                counter[str(step["action"])] += 1
    return counter


def main() -> None:
    args = parse_args()
    registry = registry_actions()
    prompt = prompt_actions()
    data = dataset_actions(args.jsonl)
    data_set = set(data)

    lines: list[str] = []
    lines.append("# 플래너 액션 커버리지 감사")
    lines.append("")
    lines.append(f"- 데이터셋: `{args.jsonl.name}`")
    lines.append(f"- tool_registry 실행 가능: {len(registry)}종")
    lines.append(f"- 프롬프트 허용 목록: {len(prompt)}종")
    lines.append(f"- 학습 데이터 등장: {len(data_set)}종 / {sum(data.values())}회")
    lines.append("")

    sections = [
        (
            "레지스트리에 있으나 프롬프트에 없음 (모델이 고를 수 없는 기능)",
            sorted(registry - prompt),
        ),
        (
            "프롬프트에 있으나 레지스트리에 없음 (고르면 실행 실패)",
            sorted(prompt - registry),
        ),
        (
            "데이터가 가르치지만 프롬프트에 없음 (실행 불가 계획 학습)",
            sorted(data_set - prompt),
        ),
        (
            "데이터가 가르치지만 레지스트리에 없음 (완전 무효)",
            sorted(data_set - registry),
        ),
        (
            "프롬프트+레지스트리 모두 있으나 학습 예제 0건 (미학습 기능)",
            sorted((prompt & registry) - data_set),
        ),
    ]

    for title, items in sections:
        lines.append(f"## {title} — {len(items)}종")
        lines.append("")
        if items:
            for action in items:
                count = data.get(action, 0)
                suffix = f" (학습 {count}회)" if count else ""
                lines.append(f"- `{action}`{suffix}")
        else:
            lines.append("없음.")
        lines.append("")

    lines.append("## 학습 데이터 액션 빈도")
    lines.append("")
    lines.append("| 건수 | action | 레지스트리 | 프롬프트 |")
    lines.append("|---|---|---|---|")
    for action, count in data.most_common():
        lines.append(
            f"| {count} | `{action}` | {'O' if action in registry else 'X'} "
            f"| {'O' if action in prompt else 'X'} |"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DONE] {args.output}")


if __name__ == "__main__":
    main()
