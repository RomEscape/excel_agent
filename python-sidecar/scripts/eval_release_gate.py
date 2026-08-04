from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9), name="KST")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON 객체가 아닙니다: {path}")
    return parsed


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _load_threshold_overrides(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"thresholds 파일이 없습니다: {path}")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("thresholds JSON은 객체 형태여야 합니다.")
    return parsed


def _clarify_ratio(cases: list[dict[str, Any]]) -> float:
    if not cases:
        return 0.0
    clarify = 0
    for case in cases:
        predicted = str(case.get("predicted_first_action", "")).strip().lower()
        if predicted in {"excel_live.clarify", "clarify"}:
            clarify += 1
    return clarify / len(cases)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AX7B planner 승격 게이트 평가")
    parser.add_argument("--shadow-report", type=Path, required=True)
    parser.add_argument("--hard-smoke-report", type=Path, default=None)
    parser.add_argument("--complex-report", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--thresholds-json", type=Path, default=None)
    parser.add_argument("--min-parse-gain-pp", type=float, default=10.0)
    parser.add_argument("--min-hard-e2e-rate", type=float, default=0.95)
    parser.add_argument("--max-p95-latency-ratio", type=float, default=1.25)
    parser.add_argument("--min-complex-pass-rate", type=float, default=0.95)
    parser.add_argument("--max-complex-critical-failures", type=int, default=0)
    parser.add_argument("--min-complex-scenarios", type=int, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides = _load_threshold_overrides(args.thresholds_json)
    if "min_parse_gain_pp" in overrides:
        args.min_parse_gain_pp = float(overrides["min_parse_gain_pp"])
    if "min_hard_e2e_rate" in overrides:
        args.min_hard_e2e_rate = float(overrides["min_hard_e2e_rate"])
    if "max_p95_latency_ratio" in overrides:
        args.max_p95_latency_ratio = float(overrides["max_p95_latency_ratio"])
    if "min_complex_pass_rate" in overrides:
        args.min_complex_pass_rate = float(overrides["min_complex_pass_rate"])
    if "max_complex_critical_failures" in overrides:
        args.max_complex_critical_failures = int(overrides["max_complex_critical_failures"])
    if "min_complex_scenarios" in overrides:
        args.min_complex_scenarios = int(overrides["min_complex_scenarios"])

    shadow = _read_json(args.shadow_report)
    baseline = shadow.get("baseline") if isinstance(shadow.get("baseline"), dict) else {}
    candidate = shadow.get("candidate") if isinstance(shadow.get("candidate"), dict) else {}

    baseline_first = _safe_float(baseline.get("first_action_match_rate"))
    candidate_first = _safe_float(candidate.get("first_action_match_rate"))
    parse_gain_pp = (candidate_first - baseline_first) * 100.0

    baseline_p95 = _safe_int((baseline.get("latency_ms") or {}).get("p95"))
    candidate_p95 = _safe_int((candidate.get("latency_ms") or {}).get("p95"))
    latency_ratio = (candidate_p95 / baseline_p95) if baseline_p95 > 0 else 0.0

    baseline_cases = baseline.get("cases") if isinstance(baseline.get("cases"), list) else []
    candidate_cases = candidate.get("cases") if isinstance(candidate.get("cases"), list) else []
    baseline_clarify = _clarify_ratio([row for row in baseline_cases if isinstance(row, dict)])
    candidate_clarify = _clarify_ratio([row for row in candidate_cases if isinstance(row, dict)])

    hard_e2e_rate = None
    hard_smoke_ok = True
    if args.hard_smoke_report is not None:
        hard = _read_json(args.hard_smoke_report)
        e2e = hard.get("korean_command_e2e_hard_tasks")
        if not isinstance(e2e, dict):
            hard_smoke_ok = False
        else:
            hard_e2e_rate = _safe_float(e2e.get("accuracy"))
            hard_smoke_ok = hard_e2e_rate >= float(args.min_hard_e2e_rate)

    complex_pass_rate = None
    complex_total = 0
    complex_passed = 0
    complex_critical_failures = 0
    complex_ok = True
    if args.complex_report is not None:
        complex_report = _read_json(args.complex_report)
        complex_total = _safe_int(
            complex_report.get("total_scenarios", complex_report.get("total", 0))
        )
        complex_passed = _safe_int(
            complex_report.get("passed_scenarios", complex_report.get("passed", 0))
        )
        complex_critical_failures = _safe_int(complex_report.get("critical_failures", 0))
        complex_pass_rate = _safe_float(
            complex_report.get(
                "pass_rate",
                (complex_passed / complex_total) if complex_total > 0 else 0.0,
            )
        )
        complex_ok = bool(
            complex_total >= int(args.min_complex_scenarios)
            and complex_pass_rate >= float(args.min_complex_pass_rate)
            and complex_critical_failures <= int(args.max_complex_critical_failures)
        )

    checks = {
        "parse_gain": {
            "required_pp": float(args.min_parse_gain_pp),
            "actual_pp": round(parse_gain_pp, 4),
            "passed": parse_gain_pp >= float(args.min_parse_gain_pp),
        },
        "hard_e2e": {
            "required_rate": float(args.min_hard_e2e_rate),
            "actual_rate": hard_e2e_rate,
            "passed": hard_smoke_ok,
        },
        "latency": {
            "required_max_ratio": float(args.max_p95_latency_ratio),
            "actual_ratio": round(latency_ratio, 4),
            "baseline_p95_ms": baseline_p95,
            "candidate_p95_ms": candidate_p95,
            "passed": (latency_ratio <= float(args.max_p95_latency_ratio)) if baseline_p95 > 0 else True,
        },
        "clarify_ratio": {
            "baseline": round(baseline_clarify, 4),
            "candidate": round(candidate_clarify, 4),
            "passed": candidate_clarify <= baseline_clarify,
        },
    }
    if args.complex_report is not None:
        checks["complex_scenarios"] = {
            "required_min_pass_rate": float(args.min_complex_pass_rate),
            "required_max_critical_failures": int(args.max_complex_critical_failures),
            "required_min_total": int(args.min_complex_scenarios),
            "actual_pass_rate": round(float(complex_pass_rate or 0.0), 4),
            "actual_total": complex_total,
            "actual_passed": complex_passed,
            "actual_critical_failures": complex_critical_failures,
            "passed": complex_ok,
        }
    passed = all(bool(item.get("passed")) for item in checks.values())
    payload = {
        "at": datetime.now(KST).isoformat(),
        "passed": passed,
        "checks": checks,
        "input": {
            "shadow_report": str(args.shadow_report),
            "hard_smoke_report": str(args.hard_smoke_report) if args.hard_smoke_report else None,
            "complex_report": str(args.complex_report) if args.complex_report else None,
            "thresholds_json": str(args.thresholds_json) if args.thresholds_json else None,
        },
        "summary": {
            "baseline_model": baseline.get("model", ""),
            "candidate_model": candidate.get("model", ""),
            "parse_gain_pp": round(parse_gain_pp, 4),
            "p95_latency_ratio": round(latency_ratio, 4),
            "complex_pass_rate": round(float(complex_pass_rate or 0.0), 4),
            "complex_critical_failures": complex_critical_failures,
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] release gate report: {args.output_json}")


if __name__ == "__main__":
    main()

