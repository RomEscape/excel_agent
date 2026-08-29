from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SYSTEM_PROMPT = (
    "너는 OfficeClaw Excel 플래너다. "
    "사용자의 한국어 요청을 excel_live action_plan JSON으로만 반환한다."
)


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = str(line or "").strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_action_plan(action_plan: Any) -> bool:
    if not isinstance(action_plan, list) or not action_plan:
        return False
    for step in action_plan:
        if not isinstance(step, dict):
            return False
        action = _text(step.get("action"))
        params = step.get("params")
        if not action.startswith("excel_live."):
            return False
        if not isinstance(params, dict):
            return False
    return True


def _build_target_json(row: dict[str, Any]) -> dict[str, Any]:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    return {
        "intent": "edit",
        "mutates_workbook": True,
        "action_plan": target.get("action_plan", []),
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": "",
        "reason": "학습 데이터 정답 플랜",
    }


def to_sft_rows(rows: list[dict[str, Any]], *, locale_allow: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    skipped_locale = 0
    skipped_status = 0
    skipped_plan = 0

    for row in rows:
        input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        locale = _text(input_obj.get("locale")).lower()
        label_status = _text(target.get("label_status")).lower()
        action_plan = target.get("action_plan")

        if label_status != "verified":
            skipped_status += 1
            continue
        if locale_allow and locale not in locale_allow:
            skipped_locale += 1
            continue
        if not _valid_action_plan(action_plan):
            skipped_plan += 1
            continue

        instruction = _text(input_obj.get("instruction"))
        if not instruction:
            skipped_plan += 1
            continue

        output_json = _build_target_json(row)
        assistant_text = json.dumps(output_json, ensure_ascii=False)
        out.append(
            {
                "schema_version": "ax7b_planner_sft.v1",
                "id": _text(row.get("record_id")),
                "source_record_id": _text(row.get("record_id")),
                "split": _text((row.get("source") or {}).get("split")) or "train",
                "locale": locale,
                "instruction": instruction,
                "output_json": output_json,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": assistant_text},
                ],
            }
        )

    stats = {
        "built": len(out),
        "skipped_status": skipped_status,
        "skipped_locale": skipped_locale,
        "skipped_plan": skipped_plan,
    }
    return out, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="verified distill -> A.X 7B SFT 데이터 변환")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--locale-allow", type=str, default="ko,mixed")
    parser.add_argument("--stats", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = iter_jsonl(args.input_jsonl)
    locale_allow = {token.strip().lower() for token in _text(args.locale_allow).split(",") if token.strip()}

    out_rows, stats = to_sft_rows(rows, locale_allow=locale_allow)
    write_jsonl(args.output_jsonl, out_rows)

    if args.stats:
        print(
            f"[DONE] built={stats['built']} skipped_status={stats['skipped_status']} "
            f"skipped_locale={stats['skipped_locale']} skipped_plan={stats['skipped_plan']}"
        )
        print(f"[DONE] output={args.output_jsonl}")


if __name__ == "__main__":
    main()

