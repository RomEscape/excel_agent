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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _action_seq(action_plan: Any) -> list[str]:
    if not isinstance(action_plan, list):
        return []
    out: list[str] = []
    for step in action_plan:
        if not isinstance(step, dict):
            continue
        action = _text(step.get("action"))
        if action:
            out.append(action)
    return out


CLARIFY_ACTION = "excel_live.clarify"


def _rate(hit: int, total: int) -> float:
    return round(hit / total, 4) if total else 0.0


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    idx = round((len(sorted_values) - 1) * p)
    idx = max(0, min(len(sorted_values) - 1, idx))
    return int(sorted_values[idx])


async def _eval_model(
    *,
    model_name: str,
    rows: list[dict[str, Any]],
    parse_timeout_seconds: float,
) -> dict[str, Any]:
    llm = get_llm_service()
    cases: list[dict[str, Any]] = []
    parse_ok = 0
    first_action_match = 0
    exact_action_seq_match = 0
    latencies: list[int] = []
    # 분류별 집계. 전체 정답률 하나로는 무엇이 나빠졌는지 알 수 없다.
    by_category: dict[str, dict[str, int]] = {}
    # 되묻기는 맞히는 것만큼 "안 물어야 할 때 안 묻는 것"이 중요하다.
    clarify_expected = 0
    clarify_hit = 0
    nonclarify_expected = 0
    over_clarify = 0

    for row in rows:
        input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        expected_seq = _action_seq(target.get("action_plan"))
        if not expected_seq:
            continue
        message = _text(input_obj.get("instruction"))
        if not message:
            continue

        hints = input_obj.get("context_hints") if isinstance(input_obj.get("context_hints"), dict) else {}
        context = {
            "workbook_id": None,
            "sheet_name": _text(hints.get("sheet_name")) or None,
            "context_range": _text(hints.get("target_range")) or None,
            "reasoning_mode": "deep",
            "complexity_score": 4,
            "planner_model": model_name,
            # 이 평가는 **플래너끼리의** 비교다. 의도 정규화(2026-08-18 통합)가
            # 가로채면 두 팔이 같은 정규화 결과를 공유해 델타가 희석된다 —
            # 팔이 아니라 하네스가 조건을 바꾸는 부류(CLAUDE.md §3.6)라 우회한다.
            "skip_intent_normalizer": True,
            # 프로덕션은 실제 파일에서 읽은 통합문서 상태를 프롬프트에 넣는다.
            # 평가에서 이걸 빼면 학습·추론 조건이 어긋나 측정값이 실제보다 낮게 나온다.
            "workbook_digest_text": _text(input_obj.get("workbook_digest_text")),
        }
        category = _text(row.get("category")) or "uncategorized"
        t0 = time.perf_counter()
        case = {
            "record_id": _text(row.get("record_id")),
            "category": category,
            "message": message,
            "expected_first_action": expected_seq[0],
            "expected_action_seq": expected_seq,
            "predicted_first_action": "",
            "predicted_action_seq": [],
            "ok_parse": False,
            "match_first_action": False,
            "match_exact_action_seq": False,
            "elapsed_ms": 0,
            "error": "",
        }
        try:
            parsed = await asyncio.wait_for(
                parse_excel_live_command(
                    message,
                    llm_service=llm,
                    context=context,
                ),
                timeout=parse_timeout_seconds,
            )
            seq = _action_seq(parsed.get("action_plan"))
            elapsed = int((time.perf_counter() - t0) * 1000)
            case["predicted_action_seq"] = seq
            case["predicted_first_action"] = seq[0] if seq else ""
            case["ok_parse"] = bool(seq)
            case["elapsed_ms"] = elapsed
            latencies.append(elapsed)

            if seq:
                parse_ok += 1
            if seq and seq[0] == expected_seq[0]:
                case["match_first_action"] = True
                first_action_match += 1
            if seq == expected_seq:
                case["match_exact_action_seq"] = True
                exact_action_seq_match += 1
        except Exception as exc:
            case["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            case["error"] = _text(exc)
            latencies.append(case["elapsed_ms"])

        predicted_first = _text(case["predicted_first_action"])
        bucket = by_category.setdefault(
            category, {"total": 0, "first_action_match": 0, "exact_action_seq_match": 0}
        )
        bucket["total"] += 1
        if case["match_first_action"]:
            bucket["first_action_match"] += 1
        if case["match_exact_action_seq"]:
            bucket["exact_action_seq_match"] += 1

        if expected_seq[0] == CLARIFY_ACTION:
            clarify_expected += 1
            if predicted_first == CLARIFY_ACTION:
                clarify_hit += 1
        else:
            nonclarify_expected += 1
            if predicted_first == CLARIFY_ACTION:
                over_clarify += 1
                case["over_clarify"] = True

        cases.append(case)

    total = len(cases)
    report = {
        "model": model_name,
        "total": total,
        "parse_ok": parse_ok,
        "parse_ok_rate": round((parse_ok / total), 4) if total else 0.0,
        "first_action_match": first_action_match,
        "first_action_match_rate": round((first_action_match / total), 4) if total else 0.0,
        "exact_action_seq_match": exact_action_seq_match,
        "exact_action_seq_match_rate": round((exact_action_seq_match / total), 4) if total else 0.0,
        "clarify": {
            "expected": clarify_expected,
            "hit": clarify_hit,
            # 물어야 할 때 물었는가
            "recall": _rate(clarify_hit, clarify_expected),
            "nonclarify_expected": nonclarify_expected,
            "over_clarify": over_clarify,
            # 안 물어도 되는데 물어버린 비율 — 낮을수록 좋다
            "over_clarify_rate": _rate(over_clarify, nonclarify_expected),
        },
        "by_category": {
            name: {
                **counts,
                "first_action_match_rate": _rate(counts["first_action_match"], counts["total"]),
                "exact_action_seq_match_rate": _rate(
                    counts["exact_action_seq_match"], counts["total"]
                ),
            }
            for name, counts in sorted(by_category.items())
        },
        "latency_ms": {
            "avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0,
        },
        "cases": cases,
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="baseline vs candidate planner 그림자 평가")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--provider", type=str, default="ollama")
    parser.add_argument("--baseline-model", type=str, required=True)
    parser.add_argument("--candidate-model", type=str, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--parse-timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    rows = iter_jsonl(args.input_jsonl)
    if args.offset and args.offset > 0:
        rows = rows[int(args.offset) :]
    if args.limit and args.limit > 0:
        rows = rows[: int(args.limit)]

    original_cfg = load_llm_config()
    next_cfg = dict(original_cfg)
    next_cfg["provider"] = _text(args.provider) or next_cfg.get("provider", "ollama")
    # `model`은 건드리지 않는다. 예전엔 baseline으로 덮었는데, 평가가 중간에 죽으면
    # 복원이 안 돼 **앱의 일반 모델이 플래너로 바뀐 채 남았다**(2026-08-17 실측 —
    # 매크로 분해·정규화·채팅이 전부 플래너로 돌던 사고의 범인). 모델은 어차피
    # _eval_model이 호출마다 명시하므로 공용 설정을 오염시킬 이유가 없다.

    try:
        if next_cfg != original_cfg:
            save_llm_config(next_cfg)
            reload_llm_service()

        baseline_report = await _eval_model(
            model_name=_text(args.baseline_model),
            rows=rows,
            parse_timeout_seconds=float(args.parse_timeout_seconds),
        )
        candidate_report = await _eval_model(
            model_name=_text(args.candidate_model),
            rows=rows,
            parse_timeout_seconds=float(args.parse_timeout_seconds),
        )
    finally:
        if next_cfg != original_cfg:
            save_llm_config(original_cfg)
            reload_llm_service()

    summary = {
        "at": datetime.now(KST).isoformat(),
        "provider": _text(args.provider),
        "baseline_model": _text(args.baseline_model),
        "candidate_model": _text(args.candidate_model),
        "delta": {
            "first_action_match_rate": round(
                candidate_report["first_action_match_rate"] - baseline_report["first_action_match_rate"],
                4,
            ),
            "exact_action_seq_match_rate": round(
                candidate_report["exact_action_seq_match_rate"] - baseline_report["exact_action_seq_match_rate"],
                4,
            ),
            "parse_ok_rate": round(candidate_report["parse_ok_rate"] - baseline_report["parse_ok_rate"], 4),
            "p95_latency_ms": int(candidate_report["latency_ms"]["p95"] - baseline_report["latency_ms"]["p95"]),
            "clarify_recall": round(
                candidate_report["clarify"]["recall"] - baseline_report["clarify"]["recall"], 4
            ),
            "over_clarify_rate": round(
                candidate_report["clarify"]["over_clarify_rate"]
                - baseline_report["clarify"]["over_clarify_rate"],
                4,
            ),
        },
        "delta_by_category": {
            name: round(
                candidate_report["by_category"].get(name, {}).get("first_action_match_rate", 0.0)
                - counts.get("first_action_match_rate", 0.0),
                4,
            )
            for name, counts in baseline_report["by_category"].items()
        },
    }
    payload = {
        "summary": summary,
        "baseline": baseline_report,
        "candidate": candidate_report,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table(baseline_report, candidate_report)
    print(f"[DONE] shadow eval report: {args.output_json}")


def _print_table(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    def row(label: str, b: Any, c: Any) -> str:
        return f"{label:<24} {b!s:>12} {c!s:>12}"

    print()
    print(row("지표", baseline["model"], candidate["model"]))
    print("-" * 50)
    print(row("건수", baseline["total"], candidate["total"]))
    print(row("첫 액션 일치", baseline["first_action_match_rate"], candidate["first_action_match_rate"]))
    print(
        row(
            "순서 완전 일치",
            baseline["exact_action_seq_match_rate"],
            candidate["exact_action_seq_match_rate"],
        )
    )
    print(row("파싱 성공", baseline["parse_ok_rate"], candidate["parse_ok_rate"]))
    print(row("되묻기 재현율", baseline["clarify"]["recall"], candidate["clarify"]["recall"]))
    print(
        row(
            "과잉 되묻기",
            baseline["clarify"]["over_clarify_rate"],
            candidate["clarify"]["over_clarify_rate"],
        )
    )
    print(row("p95 지연(ms)", baseline["latency_ms"]["p95"], candidate["latency_ms"]["p95"]))
    print("-" * 50)
    for name in sorted(baseline["by_category"]):
        print(
            row(
                f"  [{name}] 첫 액션",
                baseline["by_category"][name]["first_action_match_rate"],
                candidate["by_category"].get(name, {}).get("first_action_match_rate", "-"),
            )
        )
    print()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

