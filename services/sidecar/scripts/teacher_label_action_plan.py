from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from office_claw_sidecar.services.excel_live_agent import parse_excel_live_command
from office_claw_sidecar.services.llm_service import (
    get_llm_service,
    load_llm_config,
    reload_llm_service,
    save_llm_config,
)

KST = timezone(timedelta(hours=9), name="KST")
SUPPORTED_LABEL_SOURCE = {"needs_teacher_plan", "log_observed"}


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


def _is_valid_action_plan(action_plan: Any) -> bool:
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


def _append_note(metadata: dict[str, Any], note: str) -> None:
    notes = metadata.get("notes")
    if not isinstance(notes, list):
        notes = []
        metadata["notes"] = notes
    if note not in notes:
        notes.append(note)


def _build_context(row: dict[str, Any], *, reasoning_mode: str) -> dict[str, Any]:
    input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
    hints = input_obj.get("context_hints") if isinstance(input_obj.get("context_hints"), dict) else {}
    workbook_refs = input_obj.get("workbook_refs") if isinstance(input_obj.get("workbook_refs"), list) else []

    workbook_id = ""
    for ref in workbook_refs:
        if not isinstance(ref, dict):
            continue
        if _text(ref.get("role")).lower() == "input":
            workbook_id = _text(ref.get("path"))
            if workbook_id:
                break
    if not workbook_id and workbook_refs:
        first = workbook_refs[0] if isinstance(workbook_refs[0], dict) else {}
        workbook_id = _text(first.get("path"))

    return {
        "workbook_id": workbook_id or None,
        "sheet_name": _text(hints.get("sheet_name")) or None,
        "context_range": _text(hints.get("target_range")) or None,
        "reasoning_mode": reasoning_mode,
        "complexity_score": 4 if _text(input_obj.get("locale")).lower() in {"ko", "mixed"} else 2,
    }


async def label_rows(
    rows: list[dict[str, Any]],
    *,
    max_cases: int,
    parse_timeout_seconds: float,
    reasoning_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = 0
    labeled = 0
    failed = 0
    skipped = 0

    llm = get_llm_service()
    for row in rows:
        copied = json.loads(json.dumps(row, ensure_ascii=False))
        total += 1
        target = copied.get("target")
        if not isinstance(target, dict):
            target = {}
            copied["target"] = target
        label_status = _text(target.get("label_status")).lower()
        existing_plan = target.get("action_plan")

        if label_status not in SUPPORTED_LABEL_SOURCE and _is_valid_action_plan(existing_plan):
            skipped += 1
            out.append(copied)
            continue
        if max_cases > 0 and labeled + failed >= max_cases:
            out.append(copied)
            continue

        metadata = copied.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            copied["metadata"] = metadata

        instruction = _text((copied.get("input") or {}).get("instruction"))
        if not instruction:
            failed += 1
            _append_note(metadata, "teacher_label_failed:empty_instruction")
            out.append(copied)
            continue

        context = _build_context(copied, reasoning_mode=reasoning_mode)
        t0 = time.perf_counter()
        try:
            parsed = await asyncio.wait_for(
                parse_excel_live_command(
                    instruction,
                    llm_service=llm,
                    context=context,
                ),
                timeout=parse_timeout_seconds,
            )
            action_plan = parsed.get("action_plan", [])
            if not _is_valid_action_plan(action_plan):
                raise ValueError("유효한 action_plan이 생성되지 않았습니다.")

            target["action_plan"] = action_plan
            target["label_status"] = "teacher_labeled"

            quality = copied.get("quality")
            if not isinstance(quality, dict):
                quality = {}
                copied["quality"] = quality
            quality["verification"] = "teacher_parse"
            quality["passed"] = True
            quality["confidence"] = round(
                0.92 if len(action_plan) >= 2 else 0.87,
                4,
            )

            copied["teacher"] = {
                "provider": llm.current_provider,
                "model": load_llm_config().get("model", ""),
                "labeled_at": datetime.now(KST).isoformat(),
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "reason": _text(parsed.get("reason")),
                "intent": _text(parsed.get("intent")),
                "reasoning_mode": reasoning_mode,
            }
            _append_note(metadata, "teacher_labeled")
            labeled += 1
        except Exception as exc:
            failed += 1
            target["label_status"] = _text(target.get("label_status")) or "needs_teacher_plan"
            copied["teacher_error"] = {
                "failed_at": datetime.now(KST).isoformat(),
                "error": _text(exc),
            }
            _append_note(metadata, "teacher_label_failed")
        out.append(copied)

    stats = {
        "total": total,
        "labeled": labeled,
        "failed": failed,
        "skipped": skipped,
    }
    return out, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="distill JSONL teacher action_plan 라벨링")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--provider", type=str, default="ollama")
    parser.add_argument("--teacher-model", type=str, default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--parse-timeout-seconds", type=float, default=25.0)
    parser.add_argument("--reasoning-mode", type=str, default="deep")
    parser.add_argument("--stats", action="store_true")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    rows = iter_jsonl(args.input_jsonl)

    original_cfg = load_llm_config()
    next_cfg = dict(original_cfg)
    next_cfg["provider"] = _text(args.provider) or next_cfg.get("provider", "ollama")
    if _text(args.teacher_model):
        next_cfg["model"] = _text(args.teacher_model)

    try:
        if next_cfg != original_cfg:
            save_llm_config(next_cfg)
            reload_llm_service()
        labeled_rows, stats = await label_rows(
            rows,
            max_cases=max(0, int(args.max_cases)),
            parse_timeout_seconds=float(args.parse_timeout_seconds),
            reasoning_mode=_text(args.reasoning_mode).lower() or "deep",
        )
        write_jsonl(args.output_jsonl, labeled_rows)
    finally:
        if next_cfg != original_cfg:
            save_llm_config(original_cfg)
            reload_llm_service()

    if args.stats:
        print(
            f"[DONE] total={stats['total']} labeled={stats['labeled']} "
            f"failed={stats['failed']} skipped={stats['skipped']}"
        )
        print(f"[DONE] output={args.output_jsonl}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

