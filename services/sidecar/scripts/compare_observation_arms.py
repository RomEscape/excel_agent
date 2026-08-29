"""관측 모드 세 팔을 케이스 단위로 나란히 놓는다.

    uv run python scripts/compare_observation_arms.py off=<실행id> read_first=<실행id> loop=<실행id>

`run_command_diagnostics.py --set observation`을 모드별로 돌린 뒤 그 실행 id를 준다.
같은 케이스가 팔마다 어떤 경로를 타고 어떤 액션으로 끝났으며 결과 파일이 맞았는지를
한 표에 놓는다. 이게 있어야 "금지 규칙을 푼 효과"와 "읽은 값을 돌려준 효과"를 가른다.

결과는 `logs/observation_arms.md`에 쓴다 — 콘솔은 한글이 깨지는 환경이 있다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIAG_DIR = ROOT.parent / "logs" / "diagnostics"
OUT_PATH = ROOT.parent / "logs" / "observation_arms.md"


def _load(run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = json.loads((DIAG_DIR / f"{run_id}.report.json").read_text(encoding="utf-8"))
    turns = [
        json.loads(line)
        for line in (DIAG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return report, turns


def _routes_of(turn: dict[str, Any]) -> str:
    from office_claw_sidecar.services.decision_trace import route_path

    return route_path(turn)


def _final_action(turn: dict[str, Any]) -> str:
    outcome = turn.get("outcome") or {}
    return str(outcome.get("action") or "")


def _per_case(turns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for turn in turns:
        case = str((turn.get("source") or {}).get("case", "?"))
        entry = out.setdefault(case, {"routes": {}, "actions": {}, "ms": []})
        entry["routes"][_routes_of(turn)] = entry["routes"].get(_routes_of(turn), 0) + 1
        action = _final_action(turn)
        entry["actions"][action] = entry["actions"].get(action, 0) + 1
        entry["ms"].append(float(turn.get("elapsed_ms") or 0))
    return out


def _wrong_counts(report: dict[str, Any]) -> dict[str, tuple[int, int]]:
    by_case = ((report.get("effects") or {}).get("by_case") or {})
    return {case: (entry.get("wrong", 0), entry.get("checked", 0)) for case, entry in by_case.items()}


def main() -> int:
    arms: dict[str, str] = {}
    for raw in sys.argv[1:]:
        if "=" not in raw:
            raise SystemExit(f"mode=run_id 형식이어야 합니다: {raw}")
        mode, run_id = raw.split("=", 1)
        arms[mode] = run_id
    if not arms:
        raise SystemExit(__doc__)

    loaded = {mode: _load(run_id) for mode, run_id in arms.items()}
    cases: list[str] = []
    for _, turns in loaded.values():
        for case in _per_case(turns):
            if case not in cases:
                cases.append(case)

    lines: list[str] = ["# 관측 모드 세 팔 비교", ""]
    for mode, run_id in arms.items():
        report, _ = loaded[mode]
        effects = report.get("effects") or {}
        checked = effects.get("checked_cases", 0)
        wrong = effects.get("wrong_cases", 0)
        lines.append(
            f"- `{mode}` — 실행 `{run_id}` · 오라클 {checked}개 중 어긋남 {wrong}개 "
            f"→ **요청 이행 {checked - wrong}/{checked}**"
        )
    lines.append("")

    header = "| 케이스 | " + " | ".join(arms) + " |"
    lines.append(header)
    lines.append("|---" * (len(arms) + 1) + "|")
    for case in cases:
        cells = []
        for mode in arms:
            report, turns = loaded[mode]
            entry = _per_case(turns).get(case)
            if entry is None:
                cells.append("—")
                continue
            wrong, checked = _wrong_counts(report).get(case, (0, 0))
            mark = "맞음" if checked and not wrong else f"어긋남 {wrong}/{checked}"
            action = max(entry["actions"], key=lambda k: entry["actions"][k]) if entry["actions"] else "-"
            median = sorted(entry["ms"])[len(entry["ms"]) // 2] if entry["ms"] else 0
            cells.append(f"{mark}<br>`{action.replace('excel_live.', '')}`<br>{median:.0f}ms")
        lines.append(f"| {case} | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## 경로")
    for case in cases:
        lines.append(f"\n### {case}")
        for mode in arms:
            _, turns = loaded[mode]
            entry = _per_case(turns).get(case)
            if entry is None:
                continue
            for route, count in sorted(entry["routes"].items(), key=lambda kv: -kv[1]):
                lines.append(f"- `{mode}` {count}회 — {route or '(경로 없음)'}")

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
