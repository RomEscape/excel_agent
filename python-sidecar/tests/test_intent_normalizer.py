"""의도 정규화 계층 — 매퍼(결정적)와 관문 배선.

실측 근거(2026-08-17, scripts/measure_*): 같은 36문장에서 정규화 100%/96% vs
플래너 67%/58%. 플래너 실패는 파라미터 암기(=SUM(E:E))였으므로, 여기서는
**모델이 좌표를 못 만들게** 하고 좌표는 매퍼·바인더가 조립한다.
"""

from __future__ import annotations

import asyncio

import pytest

from office_claw_sidecar.services.excel_intent_normalizer import (
    intent_to_plan,
    normalize_intent,
)
from office_claw_sidecar.services.excel_live_agent import parse_excel_live_command

DIGEST = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:D9",
            "columns": [
                {"letter": "A", "header": "날짜"},
                {"letter": "B", "header": "지역"},
                {"letter": "C", "header": "담당자"},
                {"letter": "D", "header": "금액"},
            ],
        }
    ],
}


def _plan(intent, message=""):
    return intent_to_plan(intent, digest=DIGEST, message=message)


class TestMappingBuildsCoordinatesFromTheDigest:
    def test_an_aggregate_formula_uses_the_named_column(self):
        # 플래너가 여섯 표현 전부 =SUM(E:E)로 암기 출력하던 바로 그 케이스.
        out = _plan({"task": "formula", "range": "F2", "column": "금액", "option": "SUM"})
        assert out["action_plan"][0]["params"] == {"range_ref": "F2", "formula_a1": "=SUM(D2:D9)"}
        assert out["plan_source"] == "intent"

    def test_number_format_maps_words_to_codes(self):
        out = _plan({"task": "number_format", "range": None, "column": "금액", "option": "천 단위 콤마"})
        assert out["action_plan"][0]["params"] == {"target_range": "D2:D9", "format_code": "#,##0"}

    def test_fill_color_keeps_the_written_range(self):
        out = _plan({"task": "fill_color", "range": "A1:D1", "column": None, "option": "노란색"})
        assert out["action_plan"][0]["params"] == {"target_range": "A1:D1", "fill_color": "#FFFF00"}

    def test_sort_stays_symbolic(self):
        # 열은 머리글 이름으로 남긴다 — 좌표 확정은 바인더 몫이다.
        out = _plan({"task": "sort", "range": None, "column": "금액", "option": "desc"})
        assert out["action_plan"][0]["params"]["key_column"] == "금액"

    def test_reset_all_emits_the_three_step_plan(self):
        out = _plan({"task": "reset_all", "range": "A1:D9", "column": None, "option": None})
        assert [s["action"] for s in out["action_plan"]] == [
            "excel_live.apply_border", "excel_live.fill_range", "excel_live.clear_range",
        ]

    def test_write_with_an_aggregate_option_becomes_a_formula(self):
        # 실측의 유일한 분류 실패("매출 총액이 얼마인지 F2에 넣어놔줘")가 회수되는 지점.
        out = _plan({"task": "write_value", "range": "F2", "column": "금액", "option": "SUM"})
        assert out["action_plan"][0]["action"] == "excel_live.set_formula"
        assert out["action_plan"][0]["params"]["formula_a1"] == "=SUM(D2:D9)"


class TestMappingRefusesWhenUnsure:
    """어설픈 매핑은 플래너 폴백보다 나쁘다 — 확신 없으면 None."""

    @pytest.mark.parametrize(
        "intent",
        [
            {"task": "fill_color", "range": "A1:D1", "option": "형광 살구색"},  # 모르는 색
            {"task": "formula", "range": "F2:F9", "column": "금액", "option": "SUM"},  # 여러 칸
            {"task": "formula", "range": "F2", "column": "없는열", "option": "SUM"},
            {"task": "formula", "range": "F2", "column": "금액", "option": "XIRR"},  # 모르는 함수
            {"task": "sort", "column": "금액", "option": None},  # 방향 없음
            {"task": "clear_values", "range": None},  # 범위 없음
            {"task": "dedupe"},  # 슬롯 경로 소유
            {"task": "pivot", "column": "지역", "option": "SUM"},  # 슬롯 경로 소유
            {"task": "other"},
            None,
        ],
    )
    def test_unmappable_intents_return_none(self, intent):
        assert _plan(intent) is None

    def test_an_unknown_color_never_defaults_to_yellow(self):
        # 라우터의 _quick_color_hex는 모르는 색을 노란색으로 접는다 — 여기서는 안 된다.
        assert _plan({"task": "fill_color", "range": "A1:D1", "option": "살구색"}) is None

    def test_a_conditional_fill_is_never_mapped(self):
        """50커맨드 실측: "A열에서 50 이상인 셀만 노란색"이 fill_color로 분류됐다.

        조건을 잃고 매핑하면 전체가 칠해진다 — 문장에 조건어가 있으면 물러난다.
        """
        intent = {"task": "fill_color", "range": None, "option": "노란색"}
        assert _plan(intent, message="A열에서 50 이상인 셀만 노란색 배경 적용") is None
        # 조건 없는 같은 의도는 여전히 매핑된다.
        assert _plan(intent, message="A열 노란색 배경 적용") is not None

    def test_highlight_is_left_to_the_planner(self):
        assert _plan({"task": "highlight", "column": "금액", "option": "노란색"}) is None


class _FakeLLM:
    """첫 호출(정규화)에 지정한 답을 주고, 이후 호출을 기록한다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if not self.replies:
            raise AssertionError("예상보다 많은 LLM 호출")
        return self.replies.pop(0)


class TestTheParseGate:
    CONTEXT = {"workbook_digest": DIGEST}

    def test_a_mapped_intent_skips_the_planner(self):
        llm = _FakeLLM(['{"task": "formula", "range": "F2", "column": "금액", "option": "SUM"}'])
        out = asyncio.run(parse_excel_live_command("F2에 금액 다 더해줘", llm, context=self.CONTEXT))
        assert out["plan_source"] == "intent"
        assert out["action_plan"][0]["params"]["formula_a1"] == "=SUM(D2:D9)"
        assert llm.calls == 1, "플래너까지 불렀다 — 정규화가 성공하면 한 번이어야 한다"

    def test_an_unmapped_intent_falls_back_to_the_planner(self):
        llm = _FakeLLM([
            '{"task": "other", "range": null, "column": null, "option": null}',
            '{"action_plan": [{"action": "excel_live.read_range", "params": {"range_ref": "A1:D9"}}],'
            ' "action": "excel_live.read_range", "params": {"range_ref": "A1:D9"},'
            ' "reason": "플래너", "intent": "read"}',
        ])
        out = asyncio.run(parse_excel_live_command("이 표 어떻게 생겼는지 봐줘", llm, context=self.CONTEXT))
        assert out.get("plan_source") != "intent"
        assert llm.calls == 2

    def test_a_normalizer_crash_falls_back_to_the_planner(self):
        class _Crashy(_FakeLLM):
            async def chat(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("정규화 죽음")
                return await super().chat(messages, **kwargs)

        llm = _Crashy([
            # 첫 호출(정규화)은 예외로 죽으므로 응답을 소비하지 않는다.
            '{"action_plan": [{"action": "excel_live.read_range", "params": {"range_ref": "A1"}}],'
            ' "action": "excel_live.read_range", "params": {"range_ref": "A1"},'
            ' "reason": "플래너", "intent": "read"}',
        ])
        out = asyncio.run(parse_excel_live_command("A1 봐줘", llm, context=self.CONTEXT))
        assert out["action"] == "excel_live.read_range"


class TestNormalizeIntentValidation:
    def test_an_unknown_task_is_rejected(self):
        llm = _FakeLLM(['{"task": "explode_sheet"}'])
        out = asyncio.run(normalize_intent("아무거나", DIGEST, llm))
        assert out is None

    def test_an_empty_message_makes_no_call(self):
        llm = _FakeLLM([])
        out = asyncio.run(normalize_intent("", DIGEST, llm))
        assert out is None and llm.calls == 0
