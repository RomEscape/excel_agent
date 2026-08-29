"""검증 리포트 전체를 한 화면 요약으로 편다.

실패한 시나리오마다 원문·실행 액션·오류·단언 결과를 함께 보여준다.
실패를 하나씩 다른 명령으로 캐묻지 않으려고 만든 도구다.

사용: python scripts/show_scenario_report.py <report.json> [out.txt] [--all]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _lines_for(result: dict[str, Any]) -> list[str]:
    flag = "PASS" if result.get("passed") else "FAIL"
    lines = [f"[{flag}] {result.get('id')} ({result.get('category')}/{result.get('difficulty')})"]
    for index, turn in enumerate(result.get("turns") or []):
        action = turn.get("action") or "(없음)"
        follow_up = " ask_follow_up" if turn.get("ask_follow_up") else ""
        lines.append(f"    T{index} {turn.get('message')}")
        lines.append(f"       -> {action} ok={turn.get('ok')}{follow_up}")
        if turn.get("error"):
            lines.append(f"       !! {turn['error']}")
        result_obj = turn.get("result")
        if isinstance(result_obj, dict):
            ops = result_obj.get("xlwings_ops")
            if isinstance(ops, list):
                for op in ops:
                    if isinstance(op, dict):
                        params = json.dumps(op.get("params") or {}, ensure_ascii=False)
                        lines.append(
                            f"       op {op.get('method')} sheet={op.get('sheet_name')} "
                            f"range={op.get('target_range')} {params[:300]}"
                        )
            if result_obj.get("ask_follow_up"):
                lines.append(f"       ask: {str(result_obj.get('follow_up_question') or '')[:200]}")
    errors = result.get("errors") or {}
    for group in ("conversation", "execution"):
        for message in errors.get(group) or []:
            lines.append(f"    ERR[{group}] {message}")
    for row in result.get("assertion_results") or []:
        mark = "ok " if row.get("ok") else "NG "
        lines.append(f"    {mark}{row.get('detail')}")
    return lines


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_all = "--all" in sys.argv
    if not args:
        print(__doc__)
        return 2
    report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    results = report.get("results") or []
    lines = [
        f"pack={report.get('scenario_pack')}",
        f"passed={report.get('passed_scenarios')}/{report.get('total_scenarios')} "
        f"critical_failures={report.get('critical_failures')} "
        f"avg_turn_ms={report.get('average_turn_latency_ms')}",
        "",
    ]
    for result in results:
        if not show_all and result.get("passed"):
            continue
        lines.extend(_lines_for(result))
        lines.append("")

    text = "\n".join(lines)
    if len(args) >= 2:
        Path(args[1]).write_text(text, encoding="utf-8")
        print(f"wrote {args[1]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
