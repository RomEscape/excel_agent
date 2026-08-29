from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from office_claw_sidecar.services.user_harness_service import (
    record_user_feedback_event,
    record_user_harness_event,
)

KST = timezone(timedelta(hours=9), name="KST")


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON 객체가 아닙니다: {path}")
    return parsed


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _expected_actions(scenario: dict[str, Any]) -> list[str]:
    oracle = scenario.get("oracle") if isinstance(scenario.get("oracle"), dict) else {}
    conversation = oracle.get("conversation") if isinstance(oracle.get("conversation"), dict) else {}
    turn_expectations = (
        conversation.get("turn_expectations")
        if isinstance(conversation.get("turn_expectations"), list)
        else []
    )
    rows: list[str] = []
    for exp in turn_expectations:
        if not isinstance(exp, dict):
            continue
        action = _text(exp.get("action")).lower()
        if action.startswith("excel_live."):
            rows.append(action)
        action_in = exp.get("action_in")
        if isinstance(action_in, list):
            for item in action_in:
                normalized = _text(item).lower()
                if normalized.startswith("excel_live."):
                    rows.append(normalized)
    dedup: list[str] = []
    for row in rows:
        if row and row not in dedup:
            dedup.append(row)
    return dedup


def _primary_cluster(errors: dict[str, Any]) -> str:
    conversation = errors.get("conversation") if isinstance(errors.get("conversation"), list) else []
    execution = errors.get("execution") if isinstance(errors.get("execution"), list) else []
    result = errors.get("result") if isinstance(errors.get("result"), list) else []

    joined_execution = " | ".join(_text(x).lower() for x in execution)
    joined_conversation = " | ".join(_text(x).lower() for x in conversation)
    joined_result = " | ".join(_text(x).lower() for x in result)

    if "must_include_actions" in joined_execution:
        return "missing_required_action"
    if "forbid_actions" in joined_execution:
        return "forbidden_action_executed"
    if "action_in" in joined_conversation or "action expected" in joined_conversation:
        return "conversation_action_mismatch"
    if "status_code" in joined_conversation:
        return "conversation_status_mismatch"
    if "ask_follow_up" in joined_conversation:
        return "conversation_followup_mismatch"
    if "assertion[" in joined_result or "sheet_not_found" in joined_result:
        return "result_assertion_failed"
    if joined_result:
        return "result_validation_failed"
    return "other"


def _compose_error_reason(errors: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("conversation", "execution", "result"):
        rows = errors.get(key) if isinstance(errors.get(key), list) else []
        if not rows:
            continue
        first = _text(rows[0])
        if first:
            chunks.append(f"{key}:{first}")
    return " | ".join(chunks)[:600]


def _build_instruction(turns: list[dict[str, Any]], fallback_title: str) -> str:
    msgs: list[str] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        msg = _text(turn.get("message"))
        if msg:
            msgs.append(msg)
    if not msgs:
        return fallback_title or "복잡 엑셀 작업을 수행해줘"
    if len(msgs) == 1:
        return msgs[0]
    return " / ".join(msgs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="복잡 시나리오 실패를 하네스/teacher 큐로 재주입")
    parser.add_argument("--complex-report", type=Path, required=True)
    parser.add_argument("--scenario-pack", type=Path, required=True)
    parser.add_argument(
        "--cluster-report-output",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "logs" / "excel_complex_failure_clusters.json",
    )
    parser.add_argument(
        "--teacher-queue-output",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "datasets"
        / "distill"
        / "excel_distill_v1_complex_failed_queue.jsonl",
    )
    parser.add_argument("--inject-harness", action="store_true", default=False)
    parser.add_argument("--harness-user-id", type=str, default="complex_eval_bot")
    parser.add_argument("--max-failures", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = _load_json(args.complex_report)
    pack = _load_json(args.scenario_pack)

    scenario_rows = pack.get("scenarios") if isinstance(pack.get("scenarios"), list) else []
    scenario_map: dict[str, dict[str, Any]] = {}
    for row in scenario_rows:
        if not isinstance(row, dict):
            continue
        scenario_id = _text(row.get("id"))
        if scenario_id:
            scenario_map[scenario_id] = row

    result_rows = report.get("results") if isinstance(report.get("results"), list) else []
    failed_rows = [row for row in result_rows if isinstance(row, dict) and not bool(row.get("passed", False))]
    if int(args.max_failures or 0) > 0:
        failed_rows = failed_rows[: int(args.max_failures)]

    cluster_counts: dict[str, int] = {}
    cluster_scenarios: dict[str, list[str]] = {}
    teacher_rows: list[dict[str, Any]] = []

    injected_feedback = 0
    injected_failure_events = 0

    for row in failed_rows:
        scenario_id = _text(row.get("id"))
        scenario = scenario_map.get(scenario_id, {})
        title = _text(row.get("title") or scenario.get("title") or scenario_id)
        category = _text(row.get("category") or scenario.get("category"))
        difficulty = _text(row.get("difficulty") or scenario.get("difficulty"))
        errors = row.get("errors") if isinstance(row.get("errors"), dict) else {}
        cluster = _primary_cluster(errors)
        cluster_counts[cluster] = int(cluster_counts.get(cluster, 0)) + 1
        cluster_scenarios.setdefault(cluster, []).append(scenario_id)

        turns = row.get("turns") if isinstance(row.get("turns"), list) else []
        instruction = _build_instruction(turns, title)
        expected_actions = _expected_actions(scenario)
        expected_action = expected_actions[0] if expected_actions else ""
        observed_actions = row.get("observed_actions") if isinstance(row.get("observed_actions"), list) else []
        observed_action = _text(observed_actions[0]).lower() if observed_actions else ""
        reason = _compose_error_reason(errors)

        teacher_row = {
            "schema_version": "excel_distill.v1",
            "record_id": f"complex_failed:{scenario_id}:{uuid.uuid4().hex[:10]}",
            "source": {
                "dataset": "excel_complex_failures_v1",
                "split": "train",
                "sample_id": scenario_id,
                "license": "internal",
                "provenance": {
                    "complex_report": str(args.complex_report),
                    "scenario_pack": str(args.scenario_pack),
                    "cluster": cluster,
                },
            },
            "input": {
                "instruction": instruction,
                "locale": "ko",
                "language_views": {
                    "normalized": instruction,
                    "no_space": instruction.replace(" ", ""),
                    "compact": instruction.replace(" ", ""),
                    "contains_hangul": True,
                    "ko_core": instruction,
                    "ko_no_space": instruction.replace(" ", ""),
                },
                "training_hints": {
                    "preferred_locale": "ko",
                    "preferred_locale_match": True,
                    "notes": [f"cluster:{cluster}", f"expected_action:{expected_action}"],
                },
                "workbook_refs": [
                    {"role": "input", "path": "C:\\work\\complex_scenario.xlsx"}
                ],
                "context_hints": {
                    "sheet_name": _text(scenario.get("sheet_name") or "매출"),
                    "category": category,
                    "difficulty": difficulty,
                    "status_code": 500,
                    "failure_cluster": cluster,
                },
            },
            "target": {
                "task_type": "spreadsheet_edit",
                "label_status": "needs_teacher_plan",
                "action_plan": [],
                "expected_output": {
                    "expected_actions": expected_actions,
                    "expected_behavior": title,
                },
            },
            "quality": {
                "verification": "complex_failed_observed",
                "passed": False,
                "confidence": 0.35,
            },
            "metadata": {
                "created_at": _now_iso(),
                "generator": "python-sidecar/scripts/reinject_complex_failures.py",
                "notes": [
                    f"cluster:{cluster}",
                    f"observed_action:{observed_action}",
                    f"error:{reason}",
                ],
            },
        }
        teacher_rows.append(teacher_row)

        if args.inject_harness:
            payload = {"user_id": args.harness_user_id, "session_id": f"complex-{scenario_id}"}
            record_user_feedback_event(
                user_payload=payload,
                rating="bad",
                reason=f"[{cluster}] {reason}",
                route="/excel-live/command",
                message=instruction,
                expected_action=expected_action,
                expected_behavior=title,
            )
            injected_feedback += 1

            record_user_harness_event(
                route="/excel-live/command",
                method="POST",
                request_payload={
                    "user_id": args.harness_user_id,
                    "session_id": f"complex-{scenario_id}",
                    "message": instruction,
                    "workbook_id": "C:\\work\\complex_scenario.xlsx",
                    "sheet_name": _text(scenario.get("sheet_name") or "매출"),
                    "approve": True,
                },
                response_payload={
                    "ok": False,
                    "action": observed_action,
                    "reason": reason,
                    "detail": reason,
                    "result": {
                        "ask_follow_up": False,
                    },
                },
                status_code=500,
                elapsed_ms=0,
            )
            injected_failure_events += 1

    cluster_rows = [
        {
            "cluster": cluster,
            "count": count,
            "scenario_ids": sorted(cluster_scenarios.get(cluster, [])),
        }
        for cluster, count in sorted(cluster_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    cluster_report = {
        "at": _now_iso(),
        "input": {
            "complex_report": str(args.complex_report),
            "scenario_pack": str(args.scenario_pack),
        },
        "summary": {
            "failed_total": len(failed_rows),
            "clusters": len(cluster_rows),
            "teacher_queue_size": len(teacher_rows),
            "harness_feedback_injected": injected_feedback,
            "harness_failure_events_injected": injected_failure_events,
        },
        "clusters": cluster_rows,
    }
    _write_json(args.cluster_report_output, cluster_report)
    _write_jsonl(args.teacher_queue_output, teacher_rows)

    print(
        f"[DONE] failed={len(failed_rows)} clusters={len(cluster_rows)} "
        f"teacher_queue={args.teacher_queue_output}"
    )
    print(f"[DONE] cluster_report={args.cluster_report_output}")
    if args.inject_harness:
        print(
            f"[DONE] harness_injected feedback={injected_feedback} "
            f"failure_events={injected_failure_events} user={args.harness_user_id}"
        )


if __name__ == "__main__":
    main()
