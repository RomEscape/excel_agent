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


def _is_valid_plan(action_plan: Any) -> bool:
    if not isinstance(action_plan, list) or not action_plan:
        return False
    for step in action_plan:
        if not isinstance(step, dict):
            return False
        action = _text(step.get("action"))
        if not action.startswith("excel_live."):
            return False
    return True


def _append_note(metadata: dict[str, Any], note: str) -> None:
    notes = metadata.get("notes")
    if not isinstance(notes, list):
        notes = []
        metadata["notes"] = notes
    if note not in notes:
        notes.append(note)


def _build_context(row: dict[str, Any], *, reflection_note: str) -> dict[str, Any]:
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
        "reasoning_mode": "reflect",
        "reflection_note": reflection_note or "teacher_retry",
        "previous_first_action": _text(((row.get("target") or {}).get("action_plan") or [{}])[0].get("action")),
        "complexity_score": 5,
    }


def _needs_retry(row: dict[str, Any]) -> bool:
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    label_status = _text(target.get("label_status")).lower()
    action_plan = target.get("action_plan")
    if label_status != "teacher_labeled":
        return True
    if not _is_valid_plan(action_plan):
        return True
    teacher_error = row.get("teacher_error")
    if isinstance(teacher_error, dict) and _text(teacher_error.get("error")):
        return True
    return False


async def retry_rows(
    rows: list[dict[str, Any]],
    *,
    max_attempts: int,
    parse_timeout_seconds: float,
    max_cases: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: list[dict[str, Any]] = []
    retried = 0
    recovered = 0
    unchanged = 0
    llm = get_llm_service()

    for row in rows:
        copied = json.loads(json.dumps(row, ensure_ascii=False))
        target = copied.get("target")
        if not isinstance(target, dict):
            target = {}
            copied["target"] = target
        metadata = copied.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            copied["metadata"] = metadata

        if not _needs_retry(copied):
            unchanged += 1
            out.append(copied)
            continue
        if max_cases > 0 and retried >= max_cases:
            unchanged += 1
            out.append(copied)
            continue

        instruction = _text((copied.get("input") or {}).get("instruction"))
        if not instruction:
            _append_note(metadata, "teacher_retry_skipped:empty_instruction")
            unchanged += 1
            out.append(copied)
            continue

        retried += 1
        err_text = ""
        success = False
        for attempt in range(1, max_attempts + 1):
            reflection_note = _text((copied.get("teacher_error") or {}).get("error")) or "retry_after_invalid_plan"
            context = _build_context(copied, reflection_note=reflection_note)
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
                if not _is_valid_plan(action_plan):
                    raise ValueError("retry 결과 action_plan이 유효하지 않습니다.")

                target["action_plan"] = action_plan
                target["label_status"] = "teacher_labeled"
                copied.pop("teacher_error", None)
                copied["teacher_retry"] = {
                    "attempt": attempt,
                    "recovered_at": datetime.now(KST).isoformat(),
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                    "model": _text(load_llm_config().get("model")),
                    "provider": llm.current_provider,
                }
                quality = copied.get("quality")
                if not isinstance(quality, dict):
                    quality = {}
                    copied["quality"] = quality
                quality["verification"] = "teacher_retry_parse"
                quality["passed"] = True
                quality["confidence"] = 0.9
                _append_note(metadata, "teacher_retry_recovered")
                recovered += 1
                success = True
                break
            except Exception as exc:
                err_text = _text(exc)
                await asyncio.sleep(0.15 * attempt)
        if not success:
            copied["teacher_error"] = {
                "failed_at": datetime.now(KST).isoformat(),
                "error": err_text or "retry_failed",
            }
            _append_note(metadata, "teacher_retry_failed")
        out.append(copied)

    stats = {
        "total": len(rows),
        "retried": retried,
        "recovered": recovered,
        "unchanged": unchanged,
    }
    return out, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="teacher 라벨 실패/불완전 케이스 재라벨")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--provider", type=str, default="ollama")
    parser.add_argument("--teacher-model", type=str, default="")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--parse-timeout-seconds", type=float, default=25.0)
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
        out_rows, stats = await retry_rows(
            rows,
            max_attempts=max(1, int(args.max_attempts)),
            parse_timeout_seconds=float(args.parse_timeout_seconds),
            max_cases=max(0, int(args.max_cases)),
        )
        write_jsonl(args.output_jsonl, out_rows)
    finally:
        if next_cfg != original_cfg:
            save_llm_config(original_cfg)
            reload_llm_service()

    if args.stats:
        print(
            f"[DONE] total={stats['total']} retried={stats['retried']} "
            f"recovered={stats['recovered']} unchanged={stats['unchanged']}"
        )
        print(f"[DONE] output={args.output_jsonl}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

