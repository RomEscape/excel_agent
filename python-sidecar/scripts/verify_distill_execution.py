from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from office_claw_sidecar.services.excel_live_executor import execute_plan, normalize_plan_steps
from office_claw_sidecar.services.excel_live_plan_validator import ValidationContext, validate_plan

# 검증 스크립트는 파일 기반 안정성을 위해 pandas 엔진으로 강제한다.
os.environ.setdefault("EXCEL_LIVE_ENGINE", "file")
from office_claw_sidecar.routers.excel_live import _execute_action, _verify_step_result  # noqa: E402

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


def _build_temp_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "매출"
    ws.append(["월", "카테고리", "수량", "단가", "금액"])
    ws.append(["1월", "A", 10, 100, 1000])
    ws.append(["1월", "B", 8, 110, 880])
    ws.append(["2월", "A", 7, 120, 840])
    ws.append(["2월", "B", 11, 90, 990])
    ws.append(["3월", "A", 9, 130, 1170])
    ws.append(["3월", "B", 9, 130, 1170])
    ws2 = wb.create_sheet("비교")
    ws2.append(["월", "카테고리", "수량", "단가", "금액"])
    ws2.append(["1월", "A", 10, 100, 1000])
    ws2.append(["1월", "B", 8, 111, 888])
    ws2.append(["2월", "A", 7, 120, 840])
    ws2.append(["2월", "B", 11, 90, 990])
    ws2.append(["3월", "A", 9, 130, 1170])
    ws2.append(["3월", "B", 9, 130, 1171])
    wb.save(path)
    wb.close()


def _verify_row_execution(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    message = _text(input_obj.get("instruction")) or "엑셀 작업을 수행해줘"
    raw_plan = target.get("action_plan") if isinstance(target.get("action_plan"), list) else []
    if not raw_plan:
        return False, {"error": "empty_action_plan"}

    with tempfile.TemporaryDirectory(prefix="officeclaw_verify_") as td:
        workbook_path = Path(td) / "verify.xlsx"
        _build_temp_workbook(workbook_path)
        workbook_id = str(workbook_path.resolve())
        sheet_name = "매출"

        normalized_steps = normalize_plan_steps(raw_plan)
        if not normalized_steps:
            return False, {"error": "normalize_plan_failed"}

        try:
            validated_steps = validate_plan(
                normalized_steps,
                context=ValidationContext(
                    message=message,
                    workbook_id=workbook_id,
                    sheet_name=sheet_name,
                    context_range=_text((input_obj.get("context_hints") or {}).get("target_range")) or "A1:E8",
                    recent_range="A1:E8",
                ),
            )
        except Exception as exc:
            return False, {"error": f"validate_plan_failed:{exc}"}

        execution = execute_plan(
            steps=validated_steps,
            max_attempts=2,
            abort_on_failure=True,
            execute_action=lambda action, params: _execute_action(
                action=action,
                params=params,
                workbook_id=workbook_id,
                sheet_name=sheet_name,
            ),
            verify_step=lambda action, params, result: _verify_step_result(
                action=action,
                params=params,
                result=result,
                workbook_id=workbook_id,
                sheet_name=sheet_name,
            ),
        )
        last = execution.last
        if last is None:
            return False, {"error": "execution_empty"}
        report = {
            "steps": [
                {
                    "index": step.index,
                    "action": step.action,
                    "verified": bool(step.verified),
                    "retried": bool(step.retried),
                    "error": _text(step.error),
                    "verify_detail": _text(step.verify_detail),
                }
                for step in execution.steps
            ],
            "last_action": last.action,
            "last_verified": bool(last.verified),
            "last_error": _text(last.error),
        }
        ok = bool(last.verified and not last.error)
        return ok, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="teacher_labeled action_plan 실행 검증")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--stats", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = iter_jsonl(args.input_jsonl)
    out: list[dict[str, Any]] = []

    total = 0
    verified_count = 0
    failed = 0
    skipped = 0

    for row in rows:
        copied = json.loads(json.dumps(row, ensure_ascii=False))
        total += 1
        target = copied.get("target")
        if not isinstance(target, dict):
            target = {}
            copied["target"] = target
        current_status = _text(target.get("label_status")).lower()
        if current_status != "teacher_labeled":
            skipped += 1
            out.append(copied)
            continue
        if args.max_cases > 0 and (verified_count + failed) >= int(args.max_cases):
            skipped += 1
            out.append(copied)
            continue

        ok, report = _verify_row_execution(copied)
        quality = copied.get("quality")
        if not isinstance(quality, dict):
            quality = {}
            copied["quality"] = quality
        quality["verification"] = "execution_replay"
        quality["passed"] = bool(ok)
        quality["confidence"] = 0.96 if ok else 0.4

        metadata = copied.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            copied["metadata"] = metadata
        notes = metadata.get("notes")
        if not isinstance(notes, list):
            notes = []
            metadata["notes"] = notes
        notes.append("verified_by_execute_plan" if ok else "verification_failed")

        copied["verification_report"] = {
            "at": datetime.now(KST).isoformat(),
            "ok": bool(ok),
            **report,
        }

        if ok:
            target["label_status"] = "verified"
            verified_count += 1
        else:
            failed += 1
        out.append(copied)

    write_jsonl(args.output_jsonl, out)
    if args.stats:
        print(
            f"[DONE] total={total} verified={verified_count} failed={failed} skipped={skipped} "
            f"output={args.output_jsonl}"
        )


if __name__ == "__main__":
    main()

