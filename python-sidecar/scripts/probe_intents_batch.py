"""문장 여러 개의 1차 판정을 한 번에 찍는다. 파일에서 읽어 파일로 쓴다.

콘솔 인코딩 문제를 피하려고 입출력을 모두 UTF-8 파일로 다룬다.

    python scripts/probe_intents_batch.py <messages.txt> [out.txt]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.routers import excel_live as router  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    messages = [
        line.strip()
        for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    lines: list[str] = []
    for message in messages:
        quick = router._build_quick_action_plan(message, None)
        first = str(quick[0]["action"]) if quick else "-"
        lines.append(f"{message}")
        lines.append(f"    intent={router._detect_operation_intent(message)} quick={first}")
        hints = router._extract_operation_hints(message)
        lines.append(f"    hints={json.dumps(hints, ensure_ascii=False)[:400]}")
    text = "\n".join(lines)
    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(text, encoding="utf-8")
        print(f"wrote {sys.argv[2]}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
