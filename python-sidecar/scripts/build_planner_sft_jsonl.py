"""
증류 데이터셋(excel_distill.v1)을 **프로덕션 플래너와 동일한 프롬프트**의
SFT 학습 데이터로 변환한다.

기존 학습 데이터는 `"너는 Excel 플래너다. JSON만 출력해라." + 사용자 문장`이라는
짧은 프롬프트를 썼는데, 실제 추론은 `excel_planner_prompt.build_planner_prompt`가
만든 4천 자짜리 프롬프트를 보낸다. 모델이 학습 때 본 적 없는 형식을 받으므로
파인튜닝 효과가 사라졌다. 이 스크립트는 추론과 같은 빌더를 호출해 그 간극을 없앤다.

정답(assistant)은 프로덕션 파서가 실제로 읽는 스키마를 그대로 따른다:
    {"intent", "mutates_workbook", "action_plan", "slot_fill",
     "partial_params", "follow_up_question", "reason"}

사용:
    python scripts/build_planner_sft_jsonl.py \
        --input ../datasets/distill/excel_distill_v1_train.jsonl \
        --output ../datasets/distill/planner_sft_v2_train.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_plan_validator import (
    EDIT_ACTIONS as VALIDATOR_EDIT_ACTIONS,
)
from office_claw_sidecar.services.excel_live_plan_validator import (
    SUPPORTED_ACTIONS,
)
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt

PREFIX = "excel_live."

# intent 판정 기준은 검증기가 단일 소스다. 여기에 목록을 복사해 두면
# 실행 계층이 바뀔 때 조용히 어긋난다 — 실제로 예전 사본에는 실행조차 되지 않는
# "chart"/"sort"가 편집 액션으로 들어가 있었다.
EDIT_ACTIONS = frozenset(name.removeprefix(PREFIX) for name in VALIDATOR_EDIT_ACTIONS)

NAVIGATE_ACTIONS = frozenset(
    {"list_workbooks", "select_workbook", "list_sheets", "select_sheet", "create_sheet"}
)

# 증류 데이터에 섞여 있던 잘못된 액션 이름 → 실제 실행 가능한 액션.
# 원본 티처가 축약형으로 뱉은 것을 그대로 정답으로 굳혀 버린 흔적이다.
ACTION_ALIASES = {
    "formula": "set_formula",
    "chart": "create_chart",
    "sort": "sort_range",
}

# 플래너가 고를 수 있는 액션이 아니라, 계획이 비었을 때 **라우터가 만들어 내는**
# 응답이다. 이걸 정답으로 가르치면 모델은 실행 불가능한 계획을 내놓는다.
ROUTER_FALLBACK_ACTIONS = frozenset({"general", "clarify", "debug"})


def _short_name(action: str) -> str:
    return action.split(".", 1)[-1] if "." in action else action


def normalize_action(action: str) -> str | None:
    """액션 이름을 실행 가능한 정식 이름으로 맞춘다. 못 살리면 None."""
    short = _short_name(str(action or "").strip())
    if not short:
        return None
    short = ACTION_ALIASES.get(short, short)
    if short in ROUTER_FALLBACK_ACTIONS:
        return None
    full = PREFIX + short
    return full if full in SUPPORTED_ACTIONS else None


def derive_intent(action_plan: list[dict[str, Any]]) -> str:
    """첫 단계의 액션으로 intent를 정한다 (프롬프트 규칙 5·6과 같은 기준)."""
    if not action_plan:
        return "read"
    first = _short_name(str(action_plan[0].get("action", "")))
    if first in EDIT_ACTIONS:
        return "edit"
    if first in NAVIGATE_ACTIONS:
        return "navigate"
    return "read"


def get_instruction(row: dict[str, Any]) -> str:
    """두 입력 스키마(excel_distill.v1 / ax7b_planner_sft.v1) 모두에서 원문을 뽑는다."""
    direct = str(row.get("instruction", "")).strip()
    if direct:
        return direct
    return str((row.get("input") or {}).get("instruction", "")).strip()


def build_context(row: dict[str, Any]) -> dict[str, Any]:
    """레코드에 남아 있는 실행 컨텍스트를 플래너 컨텍스트 형태로 복원한다."""
    inp = row.get("input") or {}
    hints = inp.get("context_hints") or {}
    return {
        "sheet_name": hints.get("sheet_name", "") or "",
        "context_range": hints.get("context_range", "") or "",
        "workbook_id": hints.get("workbook_id", "") or "",
        "reasoning_mode": "fast",
        "complexity_score": 0,
    }


def _normalize_steps(action_plan: Any) -> list[dict[str, Any]] | None:
    """단계 목록을 정규화한다. 살릴 수 없는 액션이 하나라도 있으면 레코드를 버린다.

    일부만 남기면 원래 의도와 다른 계획이 정답으로 남는다.
    (예: 3단계 중 clarify 한 단계를 지우면 나머지 2단계가 전체 답인 것처럼 학습된다)
    """
    if not isinstance(action_plan, list) or not action_plan:
        return None

    steps: list[dict[str, Any]] = []
    for raw in action_plan[:4]:
        if not isinstance(raw, dict):
            continue
        action = normalize_action(raw.get("action", ""))
        if action is None:
            return None
        steps.append(
            {
                "action": action,
                "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
                "reason": str(raw.get("reason", "")).strip(),
            }
        )
    return steps or None


def build_target(row: dict[str, Any]) -> dict[str, Any] | None:
    """프로덕션 파서가 읽는 스키마 그대로의 정답 JSON을 만든다."""
    # ax7b_planner_sft.v1은 이미 프로덕션 스키마의 output_json을 들고 있다.
    output_json = row.get("output_json")
    if isinstance(output_json, dict) and isinstance(output_json.get("action_plan"), list):
        steps = _normalize_steps(output_json.get("action_plan"))
        if steps is None:
            return None
        return {**output_json, "action_plan": steps}

    target = row.get("target") or {}
    steps = _normalize_steps(target.get("action_plan"))
    if steps is None:
        return None

    intent = derive_intent(steps)
    hints = (row.get("input") or {}).get("context_hints") or {}
    return {
        "intent": intent,
        "mutates_workbook": intent == "edit",
        "action_plan": steps,
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": "",
        "reason": str(hints.get("reason", "")).strip() or "사용자 요청 실행",
    }


def convert(
    rows: list[dict[str, Any]],
    *,
    planner_model: str,
    only_passed: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    stats = {
        "total": 0,
        "skipped_no_plan": 0,
        "skipped_quality": 0,
        "skipped_invalid_action": 0,
        "converted": 0,
    }

    for row in rows:
        stats["total"] += 1

        # quality 블록이 없는 스키마(ax7b_planner_sft.v1)는 품질 필터 대상이 아니다.
        quality = row.get("quality")
        if only_passed and isinstance(quality, dict) and not quality.get("passed", False):
            stats["skipped_quality"] += 1
            continue

        instruction = get_instruction(row)
        if not instruction:
            stats["skipped_no_plan"] += 1
            continue

        target = build_target(row)
        if target is None:
            # 계획 자체가 없던 건지, 살릴 수 없는 액션 때문에 버린 건지 구분한다.
            raw_plan = (row.get("output_json") or {}).get("action_plan") or (
                row.get("target") or {}
            ).get("action_plan")
            if isinstance(raw_plan, list) and raw_plan:
                stats["skipped_invalid_action"] += 1
            else:
                stats["skipped_no_plan"] += 1
            continue

        prompt = build_planner_prompt(
            instruction,
            context=build_context(row),
            planner_model=planner_model,
        )

        out.append(
            {
                "record_id": row.get("record_id") or row.get("id", ""),
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
                ],
            }
        )
        stats["converted"] += 1

    return out, stats


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        help="입력 JSONL 경로 (여러 개 지정 시 순서대로 이어 붙인다)",
    )
    parser.add_argument("--output", required=True, help="출력 SFT JSONL 경로")
    parser.add_argument(
        "--planner-model",
        default="skt/A.X-4.0-Light:latest",
        help="프롬프트에 박히는 planner 모델 힌트 (추론 시 값과 같아야 함)",
    )
    parser.add_argument(
        "--only-passed",
        action="store_true",
        help="quality.passed가 true인 레코드만 사용",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for raw_path in args.input:
        path = Path(raw_path)
        chunk = read_jsonl(path)
        print(f"입력 {path.name}: {len(chunk)}건")
        rows.extend(chunk)

    converted, stats = convert(
        rows, planner_model=args.planner_model, only_passed=args.only_passed
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for item in converted:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"입력 레코드         : {stats['total']}")
    print(f"품질 미달 제외      : {stats['skipped_quality']}")
    print(f"계획 없음 제외      : {stats['skipped_no_plan']}")
    print(f"실행 불가 액션 제외 : {stats['skipped_invalid_action']}")
    print(f"변환 완료           : {stats['converted']}")
    if converted:
        sample = converted[0]["messages"]
        print(f"프롬프트 길이(첫 건): {len(sample[0]['content'])}자")
        print(f"정답 길이(첫 건)    : {len(sample[1]['content'])}자")
    print(f"출력             : {out_path}")


if __name__ == "__main__":
    main()
