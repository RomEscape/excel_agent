"""일반 채팅 폴백의 정체성 앵커 (F-01).

2026-08-16 실측: `_fallback_chat_via_llm`이 system 메시지 없이 user 한 줄만 보내
모델이 "저는 SK텔레콤에서 개발한 AI 비서입니다"로 자기를 소개하고, 엑셀 승인을
다시 요청한 사용자에게 사업 아이디어를 제안했다.
"""

from __future__ import annotations

import asyncio

import pytest

from office_claw_sidecar.routers import agent as agent_router
from office_claw_sidecar.services.chat_persona import (
    CHAT_PERSONA_SYSTEM_PROMPT,
    build_persona_messages,
)

# split_system_messages는 Claude 경로 제거(dev 병합 2026-08-29)로 사라졌다.
# 이 테스트가 검증하는 것은 페르소나 메시지 구성이므로, 시스템 턴 분리기는
# 원본(claude_service, 삭제됨)과 같은 의미의 로컬 헬퍼로 유지한다.
def split_system_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(p for p in parts if p.strip()), turns


class TestPersonaMessages:
    def test_system_comes_first_and_user_text_is_untouched(self):
        msgs = build_persona_messages("합계 좀 내줘")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert msgs[0]["content"] == CHAT_PERSONA_SYSTEM_PROMPT
        assert msgs[1]["content"] == "합계 좀 내줘"

    @pytest.mark.parametrize(
        "needle",
        [
            "Office-Claw",       # 정체성
            "엑셀",              # 업무 범위
            "데이터",            # 셀 값은 지시가 아니다
            "109",               # 자살예방상담전화 — 안전 민감 입력
            "1577-0199",         # 정신건강상담전화
        ],
    )
    def test_the_prompt_covers_each_required_clause(self, needle):
        assert needle in CHAT_PERSONA_SYSTEM_PROMPT

    def test_it_does_not_name_the_vendor_or_base_model(self):
        # 프롬프트 자체가 기반 모델을 언급하면 모델이 따라 말할 빌미를 준다.
        lowered = CHAT_PERSONA_SYSTEM_PROMPT.lower()
        for banned in ("sk텔레콤", "sktelecom", "a.x-4.0", "ax4", "qwen", "ollama"):
            assert banned not in lowered


class TestFallbackUsesThePersona:
    def test_the_fallback_sends_a_system_message(self, monkeypatch):
        seen: list[list[dict]] = []

        class FakeLLM:
            # 기존 테스트의 가짜 LLM과 같은 시그니처를 유지한다 — 폴백이 키워드
            # 인자를 더하면 TypeError가 광범위한 except에 먹혀 조용히 빈 응답이 된다.
            async def chat(self, messages, model=None):
                seen.append(messages)
                return "엑셀 작업을 도와드리겠습니다."

        monkeypatch.setattr(agent_router, "get_llm_service", lambda: FakeLLM())
        out = asyncio.run(agent_router._fallback_chat_via_llm("다시 제안해줄래?"))

        assert out == "엑셀 작업을 도와드리겠습니다."
        assert len(seen) == 1
        assert seen[0][0]["role"] == "system"
        assert "Office-Claw" in seen[0][0]["content"]
        assert seen[0][-1] == {"role": "user", "content": "다시 제안해줄래?"}

    def test_an_llm_failure_still_returns_empty_not_an_exception(self, monkeypatch):
        class BoomLLM:
            async def chat(self, messages, model=None):
                raise RuntimeError("ollama down")

        monkeypatch.setattr(agent_router, "get_llm_service", lambda: BoomLLM())
        assert asyncio.run(agent_router._fallback_chat_via_llm("안녕")) == ""


class TestClaudeHoistsSystem:
    """Anthropic API는 system을 messages가 아니라 최상위 필드로 받는다.

    이걸 안 올리면 Claude provider에서 400이 나고, 폴백의 넓은 except에 먹혀
    "응답 없음"으로만 보인다 — 조사에서 지적된 회귀 경로다.
    """

    def test_system_is_hoisted_out_of_messages(self):
        system_text, turns = split_system_messages(build_persona_messages("합계"))
        assert all(m["role"] != "system" for m in turns)
        assert "Office-Claw" in system_text
        assert turns == [{"role": "user", "content": "합계"}]

    def test_no_system_text_when_there_is_none(self):
        system_text, turns = split_system_messages([{"role": "user", "content": "안녕"}])
        assert system_text == ""
        assert turns == [{"role": "user", "content": "안녕"}]

    def test_several_system_messages_are_joined_in_order(self):
        system_text, turns = split_system_messages(
            [
                {"role": "system", "content": "첫째"},
                {"role": "user", "content": "질문"},
                {"role": "system", "content": "둘째"},
            ]
        )
        assert system_text == "첫째\n\n둘째"
        assert turns == [{"role": "user", "content": "질문"}]

    def test_blank_system_messages_do_not_create_an_empty_field(self):
        # 빈 system을 올리면 Anthropic이 빈 문자열 system을 받는다. 아예 안 보내야 한다.
        system_text, _ = split_system_messages(
            [{"role": "system", "content": "   "}, {"role": "user", "content": "안녕"}]
        )
        assert system_text == ""
