"""문장 하나에 대해 라우터의 1차 판정(의도·힌트·빠른 규칙)만 찍어 본다.

LLM 없이 규칙 계층만 보고 싶을 때 쓴다. 실행은 하지 않는다.

    uv run python scripts/probe_intent.py "이익률 열에 매출이익 나누기 매출 수식을 넣어줘"
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.routers import excel_live as router  # noqa: E402
from office_claw_sidecar.services.excel_formula_builder import parse_named_formula  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: probe_intent.py <문장>")
        return 2
    message = sys.argv[1]
    headers = sys.argv[2].split(",") if len(sys.argv) > 2 else []

    print("의도:", router._detect_operation_intent(message))
    print("힌트:", json.dumps(router._extract_operation_hints(message), ensure_ascii=False))
    print("빠른규칙:", json.dumps(router._build_quick_action_plan(message, None), ensure_ascii=False))
    print("열비교문장:", router._looks_like_column_comparison(message))
    print("이름수식문장:", router._looks_like_named_formula(message))
    if headers:
        parsed = parse_named_formula(message, headers)
        print("이름수식파싱:", json.dumps(parsed.as_dict() if parsed else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
