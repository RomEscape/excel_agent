"""한 턴의 LLM 호출을 **전부** 펼쳐 UTF-8 파일로 쓴다.

    uv run python scripts/dump_turn_llm_calls.py <로그경로> <turn_id> <출력경로>

`show_turns.py`는 턴 하나를 사람이 읽기 좋게 요약하느라 LLM 호출을 첫 번째만
보여 준다. 관측 루프나 재계획처럼 **한 턴에 모델을 여러 번 부르는** 경로를 진단할
때는 두 번째 호출이 무엇을 보고 무엇을 냈는지가 정작 알고 싶은 것이다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 호출자가 PYTHONUTF8=1 을 안 붙여도 한국어 출력이 죽지 않게 한다(2026-09-07 실측:
# cp949 콘솔에서 첫 print 가 UnicodeEncodeError 로 죽어 '로그가 잘린다'로 보였다).
from _console import force_utf8

force_utf8()

INTERESTING = ("planner_context", "llm_call", "executed")


def _find(path: Path, turn_id: str) -> dict[str, Any] | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        turn = json.loads(line)
        if str(turn.get("turn_id", "")).startswith(turn_id):
            return turn
    return None


def main() -> int:
    log_path, turn_id, out_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    turn = _find(log_path, turn_id)
    if turn is None:
        raise SystemExit(f"그런 턴이 없습니다: {turn_id}")

    lines = [f"turn={turn.get('turn_id')}  {turn.get('message')}", ""]
    from office_claw_sidecar.services.decision_trace import route_path

    lines.append(f"[ROUTE] {route_path(turn)}")
    lines.append("")
    for index, stage in enumerate(turn.get("stages", [])):
        name = str(stage.get("stage", ""))
        if name not in INTERESTING:
            continue
        lines.append(f"── #{index} {name}  (+{stage.get('at_ms')}ms)")
        for key, value in stage.items():
            if key in {"stage", "at_ms"}:
                continue
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            lines.append(f"  {key}: {rendered}")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
