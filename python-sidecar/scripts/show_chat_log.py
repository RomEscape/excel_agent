"""chat_log.jsonl을 사람이 읽는 형태로 출력한다.

사용 예:
    uv run python scripts/show_chat_log.py                 # 최근 10턴
    uv run python scripts/show_chat_log.py -n 30           # 최근 30턴
    uv run python scripts/show_chat_log.py --failed        # 실패/되묻기만
    uv run python scripts/show_chat_log.py --grep 표        # 메시지에 '표'가 든 턴
    uv run python scripts/show_chat_log.py --raw           # 원본 JSON 그대로
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.config import get_chat_log_path

STAGE_LABEL = {
    "understand": "해석",
    "planner": "플래너",
    "macro_plan": "매크로 분해",
    "macro_skipped": "매크로 미적용",
    "macro_decompose_failed": "매크로 분해 실패",
    "table_slot": "표 슬롯",
    "plan_final": "최종 계획",
    "executed": "실행",
}


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _fmt_steps(steps: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(steps, list):
        return out
    for step in steps:
        if not isinstance(step, dict):
            out.append(f"      - {step}")
            continue
        head = step.get("action") or step.get("command") or ""
        bits = []
        if "ok" in step:
            bits.append("성공" if step.get("ok") else "실패")
        if step.get("error"):
            bits.append(f"오류={step['error']}")
        if step.get("params"):
            bits.append(json.dumps(step["params"], ensure_ascii=False))
        if step.get("result"):
            bits.append(json.dumps(step["result"], ensure_ascii=False))
        suffix = f"  {' | '.join(bits)}" if bits else ""
        out.append(f"      - {head}{suffix}")
    return out


def _print_turn(turn: dict[str, Any]) -> None:
    outcome = turn.get("outcome") or {}
    mark = "OK" if outcome.get("ok") else ("ASK" if outcome.get("ask_follow_up") else "FAIL")
    print(f"\n=== [{mark}] {turn.get('at', '')}  ({turn.get('elapsed_ms', 0)}ms)")
    print(f"  사용자: {turn.get('message', '')}")
    for stage in turn.get("stages", []):
        name = STAGE_LABEL.get(stage.get("stage", ""), stage.get("stage", ""))
        fields = {
            k: v for k, v in stage.items() if k not in {"stage", "at_ms", "steps", "action_plan", "quick_plan"}
        }
        detail = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in fields.items() if v not in (None, "", [], {}))
        print(f"  · {name}{': ' + detail if detail else ''}")
        for key in ("action_plan", "quick_plan", "steps"):
            if stage.get(key):
                print("\n".join(_fmt_steps(stage[key])))
    print(
        f"  응답: action={outcome.get('action', '')} ok={outcome.get('ok')} "
        f"되묻기={outcome.get('ask_follow_up')} 승인대기={outcome.get('approval_required')}"
    )
    if outcome.get("reason"):
        print(f"  이유: {outcome['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="대화 판단 추적 로그 뷰어")
    parser.add_argument("-n", "--limit", type=int, default=10, help="최근 N턴 (기본 10)")
    parser.add_argument("--grep", default="", help="사용자 메시지 부분 일치 필터")
    parser.add_argument("--failed", action="store_true", help="실패하거나 되묻기로 끝난 턴만")
    parser.add_argument("--raw", action="store_true", help="원본 JSON 그대로 출력")
    parser.add_argument("--path", default="", help="로그 파일 경로 직접 지정")
    parser.add_argument("--out", default="", help="결과를 UTF-8 파일로 저장 (콘솔 인코딩 회피)")
    args = parser.parse_args()

    if args.out:
        sys.stdout = Path(args.out).open("w", encoding="utf-8")

    path = Path(args.path) if args.path else get_chat_log_path()
    rows = _load(path)
    if args.grep:
        rows = [r for r in rows if args.grep in str(r.get("message", ""))]
    if args.failed:
        rows = [
            r
            for r in rows
            if not (r.get("outcome") or {}).get("ok") or (r.get("outcome") or {}).get("ask_follow_up")
        ]
    rows = rows[-max(1, args.limit) :]

    print(f"# {path} — {len(rows)}턴 표시")
    for turn in rows:
        if args.raw:
            print(json.dumps(turn, ensure_ascii=False, indent=2))
        else:
            _print_turn(turn)


if __name__ == "__main__":
    main()
