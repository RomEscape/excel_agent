"""
하네스가 LLM에 실제로 보내는 요청(시스템 프롬프트 + 툴 스키마)을 그대로 덤프한다.

SFT 데이터를 프로덕션과 동일한 형식으로 만들기 위한 기준 자료.
학습 데이터 형식이 이 덤프와 어긋나면 파인튜닝 효과가 사라진다.

사용:
    python scripts/dump_harness_prompt.py [출력경로]

출력경로를 생략하면 `get_reports_dir()/harness_prompt_dump.json`
(기본 %LOCALAPPDATA%/office_claw/reports) 에 쓴다 — 저장소 `logs/` 에는 chat_log 만 둔다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.config import get_reports_dir
from office_claw_sidecar.services.excel_tool_agent import _SYSTEM_PROMPT_TEMPLATE
from office_claw_sidecar.services.excel_tool_schemas import get_excel_tools


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else get_reports_dir() / "harness_prompt_dump.json"
    tools = get_excel_tools()

    # Excel 미실행 환경에서도 재현 가능하도록 컨텍스트는 고정 문자열을 넣는다.
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        context="현재 Excel 연결 상태: 열린 통합문서 없음"
    )

    payload = {
        "system_prompt": system_prompt,
        "tool_count": len(tools),
        "tools_json_chars": len(json.dumps(tools, ensure_ascii=False)),
        "tool_names": [t.get("function", {}).get("name") for t in tools],
        "tools": tools,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"tool_count={payload['tool_count']}")
    print(f"tools_json_chars={payload['tools_json_chars']}")
    print(f"system_prompt_chars={len(system_prompt)}")
    print(f"written={out_path}")


if __name__ == "__main__":
    main()
