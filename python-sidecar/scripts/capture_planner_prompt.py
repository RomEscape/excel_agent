"""
플래너가 LLM에 실제로 보내는 프롬프트를 가짜 llm_service로 가로채 저장한다.

프롬프트 빌더를 공유 모듈로 분리하는 리팩터링이 출력물을 한 글자도 바꾸지
않았는지 확인하는 용도. 리팩터링 전후로 각각 실행해 diff한다.

사용:
    python scripts/capture_planner_prompt.py <출력경로>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_agent import parse_command_plan_with_llm

# 다양한 분기(개인화·다이제스트·reasoning_mode·플래그)를 모두 태우는 케이스
CASES = [
    {
        "name": "minimal",
        "message": "B2:B10 값을 읽어줘",
        "context": {},
        "kwargs": {},
    },
    {
        "name": "full-context",
        "message": "이 범위 전체에 노란색 칠해줘",
        "context": {
            "workbook_id": "wb-1",
            "sheet_name": "Sales",
            "context_range": "a1:c10",
            "reasoning_mode": "deep",
            "complexity_score": 7,
            "personalization_hint": "이 사용자는 테두리를 굵게 쓰는 편",
            "workbook_digest_text": "통합문서 상태: Sales(지역, 매출)\n",
        },
        "kwargs": {"forbid_list_action": True, "require_edit_action": True},
    },
    {
        "name": "reflect",
        "message": "아까 계획 다시 세워줘",
        "context": {
            "reasoning_mode": "reflect",
            "reflection_note": "의도 불일치",
            "previous_first_action": "excel_live.list_workbooks",
        },
        "kwargs": {},
    },
]


class _CapturingLLM:
    """chat() 호출 인자를 붙잡아 두고 유효한 계획 JSON을 돌려주는 스텁."""

    def __init__(self) -> None:
        self.captured: list[dict] = []

    async def chat(self, messages, model=None, temperature=None, json_only=False):
        self.captured.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "json_only": json_only,
            }
        )
        return json.dumps(
            {
                "intent": "read",
                "mutates_workbook": False,
                "action_plan": [
                    {"action": "excel_live.read_range", "params": {"target_range": "B2:B10"}, "reason": "읽기"}
                ],
                "reason": "테스트",
            },
            ensure_ascii=False,
        )


async def main_async() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/planner_prompt_capture.json")
    results = []

    for case in CASES:
        llm = _CapturingLLM()
        await parse_command_plan_with_llm(
            case["message"],
            llm,
            context=case["context"],
            **case["kwargs"],
        )
        call = llm.captured[0]
        results.append(
            {
                "name": case["name"],
                "roles": [m.get("role") for m in call["messages"]],
                "model": call["model"],
                "temperature": call["temperature"],
                "prompt": call["messages"][0]["content"],
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        print(f"{r['name']}: roles={r['roles']} chars={len(r['prompt'])}")
    print(f"written={out_path}")


if __name__ == "__main__":
    asyncio.run(main_async())
