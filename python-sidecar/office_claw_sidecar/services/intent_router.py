"""
Intent Router — 사용자 메시지를 분석해 실행할 Tool과 파라미터를 결정.

LLM에 구조화된 JSON 응답을 요청하고, 파싱 실패 시 "chat"으로 안전하게 폴백.
항상 tool_registry의 SAFE 도구만 반환 — 인가되지 않은 도구는 chat으로 강제 대체.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from office_claw_sidecar.services.tool_registry import (
    SAFE_TOOL_NAMES,
    get_tools_description,
    is_denied_intent,
)

logger = logging.getLogger(__name__)

_CLASSIFIER_PROMPT = """당신은 사용자 메시지를 분석하여 수행할 작업을 JSON으로 반환하는 분류기입니다.

사용 가능한 도구 목록:
{tools}

규칙:
1. 반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이)
2. tool은 반드시 도구 목록에 있는 이름 중 하나
3. 불명확하거나 위험한 요청이면 "chat" 사용
4. document.generate 선택 시 params에 반드시 포함:
   - doc_type: 보고서|기획안|회의록|계약서초안|이메일|제안서
   - content: 사용자가 제공한 핵심 내용 (없으면 메시지 원문)
   - tone: 공식적|친근한|전문적 (명시 없으면 "공식적")
   - length: 짧게|보통|길게 (명시 없으면 "보통")
5. gmail.fetch_emails 선택 시 params에 max_results (기본값 5)

응답 형식:
{{"tool": "도구이름", "params": {{}}, "reason": "선택 이유 (한국어 한 줄)"}}

사용자 메시지: {message}"""


class IntentRouter:
    """사용자 메시지 → (tool, params) 분류기."""

    async def classify(self, message: str, llm_service) -> dict[str, Any]:
        """
        메시지를 분류하고 실행할 tool과 params를 반환.

        Returns:
            {"tool": str, "params": dict, "reason": str}
        """
        # 1) 빠른 거부 키워드 체크 (LLM 호출 없이)
        if is_denied_intent(message):
            return {
                "tool": "DENIED",
                "params": {},
                "reason": "보안 정책에 의해 차단된 요청입니다.",
            }

        # 2) LLM으로 intent 분류
        tools_desc = get_tools_description()
        prompt = _CLASSIFIER_PROMPT.format(
            tools=tools_desc,
            message=message,
        )

        try:
            response = await llm_service.chat([{"role": "user", "content": prompt}])

            # LLM이 마크다운 코드블록(```json ... ```)으로 감쌀 수 있으므로 JSON 추출
            json_match = re.search(r"\{.*?\}", response, re.DOTALL)
            if not json_match:
                raise ValueError(f"No JSON found in response: {response[:200]}")

            result = json.loads(json_match.group())
            tool_name = result.get("tool", "chat")

            # 3) 반환된 tool이 실제 SAFE 도구인지 검증 (두 번째 방어선)
            if tool_name not in SAFE_TOOL_NAMES:
                logger.warning(
                    "Intent router returned unknown/unsafe tool '%s', falling back to chat",
                    tool_name,
                )
                tool_name = "chat"

            return {
                "tool": tool_name,
                "params": result.get("params", {}),
                "reason": result.get("reason", ""),
            }

        except Exception as exc:
            logger.warning("Intent classification failed: %s — falling back to chat", exc)
            return {"tool": "chat", "params": {}, "reason": "분류 실패, 일반 대화로 처리"}
