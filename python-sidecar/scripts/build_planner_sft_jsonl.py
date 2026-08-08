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

from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt

# intent 판정용 — 프롬프트 규칙 6/7의 편집 액션 목록과 같은 집합.
EDIT_ACTIONS = frozenset(
    {
        "write_range", "create_table", "highlight_by_condition", "fill_range",
        "apply_border", "set_formula", "sort_range", "sort_rows", "filter_rows",
        "dedupe_rows", "pivot_table", "create_chart", "drop_column", "rename_column",
        "add_column", "set_data_validation", "protect_sheet", "save_workbook",
        "find_replace", "merge_cells", "unmerge_cells", "freeze_panes",
        "autofit_columns", "define_named_range", "set_print_area", "add_cell_comment",
        "apply_color_scale", "apply_data_bar", "set_number_format", "clear_range",
        "chart", "sort",
    }
)

NAVIGATE_ACTIONS = frozenset(
    {"list_workbooks", "select_workbook", "list_sheets", "select_sheet", "create_sheet"}
)


def _short_name(action: str) -> str:
    return action.split(".", 1)[-1] if "." in action else action


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


def build_target(row: dict[str, Any]) -> dict[str, Any] | None:
    """프로덕션 파서가 읽는 스키마 그대로의 정답 JSON을 만든다."""
    # ax7b_planner_sft.v1은 이미 프로덕션 스키마의 output_json을 들고 있다.
    output_json = row.get("output_json")
    if isinstance(output_json, dict) and isinstance(output_json.get("action_plan"), list):
        return output_json

    target = row.get("target") or {}
    action_plan = target.get("action_plan")
    if not isinstance(action_plan, list) or not action_plan:
        return None

    steps: list[dict[str, Any]] = []
    for raw in action_plan[:4]:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action", "")).strip()
        if not action:
            continue
        steps.append(
            {
                "action": action,
                "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
                "reason": str(raw.get("reason", "")).strip(),
            }
        )
    if not steps:
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
    stats = {"total": 0, "skipped_no_plan": 0, "skipped_quality": 0, "converted": 0}

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
    parser.add_argument("--input", required=True, help="excel_distill.v1 JSONL 경로")
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

    rows = read_jsonl(Path(args.input))
    converted, stats = convert(
        rows, planner_model=args.planner_model, only_passed=args.only_passed
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for item in converted:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"입력 레코드      : {stats['total']}")
    print(f"품질 미달 제외   : {stats['skipped_quality']}")
    print(f"계획 없음 제외   : {stats['skipped_no_plan']}")
    print(f"변환 완료        : {stats['converted']}")
    if converted:
        sample = converted[0]["messages"]
        print(f"프롬프트 길이(첫 건): {len(sample[0]['content'])}자")
        print(f"정답 길이(첫 건)    : {len(sample[1]['content'])}자")
    print(f"출력             : {out_path}")


if __name__ == "__main__":
    main()
