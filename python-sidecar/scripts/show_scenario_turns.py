"""검증 리포트에서 한 시나리오의 턴별 요청·응답·오류를 펼쳐 본다.

사용: python scripts/show_scenario_turns.py <report.json> <scenario-id> [out.txt]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TURN_FIELDS = ("message", "status_code", "action", "ok", "reply", "elapsed_ms")


def _format_turn(index: int, turn: dict[str, Any]) -> list[str]:
    lines = [f"  turn[{index}]"]
    for field in TURN_FIELDS:
        if field in turn:
            lines.append(f"    {field}: {turn[field]!r}")
    for field in ("plan_actions", "error"):
        value = turn.get(field)
        if value:
            lines.append(f"    {field}: {json.dumps(value, ensure_ascii=False)[:1200]}")
    result = turn.get("result")
    if isinstance(result, dict):
        lines.append(f"    result: {json.dumps(result, ensure_ascii=False, default=str)[:3000]}")
    return lines


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    scenario_id = sys.argv[2]
    matches = [row for row in report.get("results", []) if str(row.get("id")) == scenario_id]
    if not matches:
        print(f"시나리오를 찾지 못했습니다: {scenario_id}")
        return 1
    result = matches[0]

    lines = [
        f"{result['id']} passed={result.get('passed')} severity={result.get('severity')}",
        f"observed_actions={result.get('observed_actions')}",
        f"errors={json.dumps(result.get('errors'), ensure_ascii=False)}",
        "turns:",
    ]
    for index, turn in enumerate(result.get("turns") or []):
        lines.extend(_format_turn(index, turn))
    lines.append("assertions:")
    for row in result.get("assertion_results") or []:
        flag = "ok " if row.get("ok") else "NG "
        lines.append(f"  {flag}{row.get('type')} {row.get('detail')}")

    text = "\n".join(lines)
    if len(sys.argv) >= 4:
        Path(sys.argv[3]).write_text(text, encoding="utf-8")
        print(f"wrote {sys.argv[3]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
