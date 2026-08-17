"""섀도 평가 조각 리포트 병합.

분리 실행한 평가가 두 번 소리 없이 죽어서(2026-08-17 실측, 178/308·중간 미상)
77건 조각으로 나눠 추적되는 프로세스에서 돌린다. 이 스크립트가 조각들을
전체 리포트와 같은 형식으로 합쳐 `eval_release_gate.py`에 넘길 수 있게 한다.

합치는 방법: 절대 카운트(parse_ok, match, clarify, by_category)는 합산,
비율은 재계산, 지연 백분위는 케이스별 elapsed_ms를 이어 붙여 재계산.

실행:  & $PY scripts\\merge_shadow_reports.py 출력.json 조각A.json 조각B.json [...]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9), name="KST")

_COUNT_KEYS = ("parse_ok", "first_action_match", "exact_action_seq_match")
_CLARIFY_KEYS = ("expected", "hit", "nonclarify_expected", "over_clarify")


def _rate(hit: int, total: int) -> float:
    return round(hit / total, 4) if total else 0.0


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    idx = round((len(sorted_values) - 1) * p)
    idx = max(0, min(len(sorted_values) - 1, idx))
    return int(sorted_values[idx])


def _merge_model(reports: list[dict]) -> dict:
    cases = [c for r in reports for c in r.get("cases", [])]
    total = sum(int(r.get("total", 0)) for r in reports)
    merged: dict = {"model": reports[0].get("model", ""), "total": total}
    for key in _COUNT_KEYS:
        merged[key] = sum(int(r.get(key, 0)) for r in reports)
        merged[f"{key}_rate"] = _rate(merged[key], total)

    clarify = {k: sum(int((r.get("clarify") or {}).get(k, 0)) for r in reports) for k in _CLARIFY_KEYS}
    clarify["recall"] = _rate(clarify["hit"], clarify["expected"])
    clarify["over_clarify_rate"] = _rate(clarify["over_clarify"], clarify["nonclarify_expected"])
    merged["clarify"] = clarify

    by_category: dict[str, dict] = {}
    for r in reports:
        for name, counts in (r.get("by_category") or {}).items():
            slot = by_category.setdefault(name, {"total": 0, "first_action_match": 0, "exact_action_seq_match": 0})
            for k in ("total", "first_action_match", "exact_action_seq_match"):
                slot[k] += int(counts.get(k, 0))
    for counts in by_category.values():
        counts["first_action_match_rate"] = _rate(counts["first_action_match"], counts["total"])
        counts["exact_action_seq_match_rate"] = _rate(counts["exact_action_seq_match"], counts["total"])
    merged["by_category"] = dict(sorted(by_category.items()))

    latencies = [int(c.get("elapsed_ms", 0)) for c in cases if c.get("elapsed_ms")]
    merged["latency_ms"] = {
        "avg": int(sum(latencies) / len(latencies)) if latencies else 0,
        "p50": _percentile(latencies, 0.50),
        "p95": _percentile(latencies, 0.95),
        "max": max(latencies) if latencies else 0,
    }
    merged["cases"] = cases
    return merged


def main() -> None:
    out_path, *part_paths = sys.argv[1:]
    parts = [json.loads(Path(p).read_text(encoding="utf-8")) for p in part_paths]
    baseline = _merge_model([p["baseline"] for p in parts])
    candidate = _merge_model([p["candidate"] for p in parts])
    first = parts[0]["summary"]
    summary = {
        "at": datetime.now(KST).isoformat(),
        "provider": first.get("provider"),
        "baseline_model": first.get("baseline_model"),
        "candidate_model": first.get("candidate_model"),
        "merged_from": [str(p) for p in part_paths],
        "delta": {
            "first_action_match_rate": round(
                candidate["first_action_match_rate"] - baseline["first_action_match_rate"], 4
            ),
            "exact_action_seq_match_rate": round(
                candidate["exact_action_seq_match_rate"] - baseline["exact_action_seq_match_rate"], 4
            ),
            "parse_ok_rate": round(candidate["parse_ok_rate"] - baseline["parse_ok_rate"], 4),
            "p95_latency_ms": int(candidate["latency_ms"]["p95"] - baseline["latency_ms"]["p95"]),
            "clarify_recall": round(candidate["clarify"]["recall"] - baseline["clarify"]["recall"], 4),
            "over_clarify_rate": round(
                candidate["clarify"]["over_clarify_rate"] - baseline["clarify"]["over_clarify_rate"], 4
            ),
        },
        "delta_by_category": {
            name: round(
                candidate["by_category"].get(name, {}).get("first_action_match_rate", 0.0)
                - counts.get("first_action_match_rate", 0.0),
                4,
            )
            for name, counts in baseline["by_category"].items()
        },
    }
    payload = {"summary": summary, "baseline": baseline, "candidate": candidate}
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] 병합 리포트: {out_path} (조각 {len(parts)}개, 총 {baseline['total']}건)")


if __name__ == "__main__":
    main()
