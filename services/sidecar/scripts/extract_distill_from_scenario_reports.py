"""통과한 시나리오 리포트에서 (문장 -> 실제 실행된 action_plan) 쌍을 뽑아 학습 레코드로 만든다.

`verify_excel_complex_scenarios.py`가 남긴 리포트(JSON)에는 각 턴에서 실제로 실행된
`xlwings_ops`(엔진에 넘어간 action + params)가 그대로 들어 있다. 이 스크립트는 오라클을
통과한 시나리오("passed": true)의 턴만 골라, 그 턴의 실제 실행 계획을
`excel_distill.v1` 스키마의 `label_status=verified` 레코드로 변환한다.

라벨을 다시 만드는 게 아니라 "이미 통과가 확인된 실행 결과"를 학습 형식으로 옮기는
작업이라 재검증 없이 verified로 표시한다 — 리포트 자체가 검증 기록이다.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9), name="KST")


def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _turn_action_plan(turn: dict[str, Any]) -> list[dict[str, Any]]:
    result = turn.get("result") if isinstance(turn.get("result"), dict) else {}
    ops = result.get("xlwings_ops") if isinstance(result, dict) else None
    plan: list[dict[str, Any]] = []
    if isinstance(ops, list) and ops:
        for op in ops:
            if not isinstance(op, dict):
                continue
            action = str(op.get("action") or "").strip()
            if not action:
                continue
            params = op.get("params") if isinstance(op.get("params"), dict) else {}
            plan.append({"action": action, "params": dict(params)})
        if plan:
            return plan
    # 읽기/안내성 액션(clarify, read_range 등)은 엔진 호출 없이 끝나서 xlwings_ops가 비어 있다.
    # 이 경우도 "이 문장에는 이 액션이 맞다"는 신호라 액션 이름만이라도 남긴다.
    action = str(turn.get("action") or "").strip()
    if action:
        plan.append({"action": action, "params": {}})
    return plan


def extract_records(report: dict[str, Any], dataset_tag: str, source_file: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    now = datetime.now(KST).isoformat()
    for scenario in report.get("results", []):
        if not scenario.get("passed") or scenario.get("critical_failure"):
            continue
        scenario_id = str(scenario.get("id") or "")
        sheet_name = str(scenario.get("sheet_name") or "")
        for turn_idx, turn in enumerate(scenario.get("turns", [])):
            message = str(turn.get("message") or "").strip()
            if not message:
                continue
            plan = _turn_action_plan(turn)
            if not plan:
                continue
            record_id = f"{dataset_tag}:{scenario_id}:t{turn_idx}:{uuid.uuid4().hex[:10]}"
            records.append(
                {
                    "schema_version": "excel_distill.v1",
                    "record_id": record_id,
                    "source": {
                        "dataset": dataset_tag,
                        "split": "train",
                        "sample_id": f"{scenario_id}:t{turn_idx}",
                        "license": "internal",
                        "provenance": {"source_file": source_file},
                    },
                    "input": {
                        "instruction": message,
                        "locale": "ko",
                        "workbook_refs": [],
                        "context_hints": {
                            "sheet_name": sheet_name,
                            "reason": "",
                            "status_code": int(turn.get("status_code") or 0),
                        },
                    },
                    "target": {
                        "task_type": "spreadsheet_edit",
                        "label_status": "verified",
                        "action_plan": plan,
                        "expected_output": {},
                    },
                    "quality": {
                        "verification": "scenario_report_replay",
                        "passed": True,
                        "confidence": 0.95,
                    },
                    "metadata": {
                        "created_at": now,
                        "generator": "python-sidecar/scripts/extract_distill_from_scenario_reports.py",
                        "notes": [f"scenario_id:{scenario_id}", f"turn_index:{turn_idx}"],
                    },
                }
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="통과 시나리오 리포트에서 학습 레코드 추출")
    parser.add_argument("--report", action="append", required=True, help="scenario report json (여러 번 지정 가능)")
    parser.add_argument("--dataset-tag", action="append", required=True, help="--report와 같은 순서로 지정")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    args = parser.parse_args()

    if len(args.report) != len(args.dataset_tag):
        raise SystemExit("--report 개수와 --dataset-tag 개수가 같아야 합니다.")

    all_records: list[dict[str, Any]] = []
    for report_path_str, tag in zip(args.report, args.dataset_tag, strict=True):
        report_path = Path(report_path_str)
        report = _load_report(report_path)
        records = extract_records(report, tag, str(report_path))
        print(f"[{tag}] scenarios_total={report.get('total_scenarios')} "
              f"scenarios_passed={report.get('passed_scenarios')} records={len(records)}")
        all_records.extend(records)

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[DONE] total_records={len(all_records)} output={args.output_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
