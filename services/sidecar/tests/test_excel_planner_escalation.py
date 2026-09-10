"""에스컬레이션 사다리 검증.

핵심은 "로컬이 실패했을 때 사용자에게 되묻는 대신 위 단계로 올라가는가"다.
"""

from __future__ import annotations

import asyncio
import json

from office_claw_sidecar.services import decision_trace
from office_claw_sidecar.services.excel_planner_escalation import (
    RECORD_PLANNER_ESCALATION,
    TIER_FAILED,
    TIER_LOCAL,
    TIER_REPAIR,
    TIER_STRONG,
    plan_with_escalation,
    record_escalation,
)

GOOD_PLAN = {
    "intent": "edit",
    "action_plan": [
        {"action": "excel_live.sort_rows", "params": {"column": "금액", "order": "desc"}, "reason": "정렬"}
    ],
}
CLARIFY_PLAN = {
    "intent": "clarify",
    "action_plan": [
        {"action": "excel_live.clarify", "params": {"question": "어느 열 기준인가요?"}, "reason": "모호"}
    ],
    "follow_up_question": "어느 열 기준인가요?",
}


def _accept_all(steps):
    return True, ""


def _reject_all(steps):
    return False, "필수 파라미터 누락"


class _Parser:
    """호출될 때마다 미리 정해둔 결과를 순서대로 돌려준다."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.contexts: list[dict] = []

    async def __call__(self, message, context):
        self.contexts.append(dict(context))
        outcome = self.outcomes.pop(0) if self.outcomes else ValueError("더 이상 결과 없음")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(**kwargs):
    return asyncio.run(plan_with_escalation(**kwargs))


def test_local_success_does_not_escalate():
    parser = _Parser([GOOD_PLAN])
    result = _run(
        message="금액 큰 순으로",
        parse=parser,
        validate=_accept_all,
        context={},
        allow_strong=False,
    )
    assert result.final_tier == TIER_LOCAL
    assert result.escalated is False
    assert len(parser.contexts) == 1


def test_validation_failure_triggers_repair_not_clarify():
    """예전 동작은 '검증 실패 → 사용자에게 되묻기'였다. 이제는 자가 수정으로 간다."""
    parser = _Parser([GOOD_PLAN, GOOD_PLAN])
    validations = [(False, "필수 파라미터 누락"), (True, "")]
    result = _run(
        message="정렬",
        parse=parser,
        validate=lambda steps: validations.pop(0),
        context={},
        allow_strong=False,
    )
    assert result.final_tier == TIER_REPAIR
    assert result.escalated is True
    # 2차 시도에는 무엇이 왜 반려됐는지가 들어가야 한다.
    assert "필수 파라미터 누락" in parser.contexts[1]["reflection_note"]
    # 자가 수정 단계에서 되묻기로 도피하면 무한 질문 루프가 된다.
    assert parser.contexts[1]["forbid_clarify"] is True


def test_escalates_to_strong_model_when_repair_fails():
    parser = _Parser([GOOD_PLAN, GOOD_PLAN, GOOD_PLAN])
    validations = [(False, "열 없음"), (False, "열 없음"), (True, "")]
    result = _run(
        message="정리해줘",
        parse=parser,
        validate=lambda steps: validations.pop(0),
        context={},
        allow_strong=True,
    )
    assert result.final_tier == TIER_STRONG
    assert parser.contexts[2]["planner_provider"] == "strong"
    assert [a.tier for a in result.attempts] == [TIER_LOCAL, TIER_REPAIR, TIER_STRONG]


def test_parser_exception_is_not_fatal():
    """로컬이 JSON을 깨뜨려도 위 단계에서 살릴 수 있어야 한다."""
    parser = _Parser([ValueError("LLM 계획 JSON 파싱 실패"), GOOD_PLAN])
    result = _run(
        message="뭔가 해줘",
        parse=parser,
        validate=_accept_all,
        context={},
        allow_strong=False,
    )
    assert result.final_tier == TIER_REPAIR
    assert result.attempts[0].ok is False
    assert "JSON" in result.attempts[0].error


def test_clarify_is_accepted_without_validation():
    """되묻기는 실행 계획이 아니라 정상 응답이다 — 검증기에 걸려서는 안 된다."""
    parser = _Parser([CLARIFY_PLAN])
    result = _run(
        message="정리해줘",
        parse=parser,
        validate=_reject_all,
        context={},
        allow_strong=False,
    )
    assert result.final_tier == TIER_LOCAL
    assert result.parsed is CLARIFY_PLAN


def test_all_tiers_failing_reports_failed():
    parser = _Parser([GOOD_PLAN, GOOD_PLAN, GOOD_PLAN])
    result = _run(
        message="불가능",
        parse=parser,
        validate=_reject_all,
        context={},
        allow_strong=True,
    )
    assert result.final_tier == TIER_FAILED
    assert result.parsed is None
    assert result.last_error == "필수 파라미터 누락"
    # 하류 슬롯필링·복구 경로가 쓸 수 있도록 마지막 계획은 남긴다.
    assert result.best_effort is GOOD_PLAN


def test_strong_tier_skipped_when_disabled():
    parser = _Parser([GOOD_PLAN, GOOD_PLAN])
    result = _run(
        message="불가능",
        parse=parser,
        validate=_reject_all,
        context={},
        allow_strong=False,
    )
    assert [a.tier for a in result.attempts] == [TIER_LOCAL, TIER_REPAIR]
    assert result.final_tier == TIER_FAILED


def test_escalation_is_recorded_as_training_candidate(tmp_path):
    parser = _Parser([GOOD_PLAN, GOOD_PLAN])
    validations = [(False, "열 없음"), (True, "")]
    result = _run(
        message="금액 정렬",
        parse=parser,
        validate=lambda steps: validations.pop(0),
        context={},
        allow_strong=False,
    )
    path = record_escalation(
        message="금액 정렬", result=result, workbook_digest={"active_sheet": "매출"}, log_dir=tmp_path
    )
    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["instruction"] == "금액 정렬"
    assert payload["final_tier"] == TIER_REPAIR
    assert payload["output_json"]["action_plan"][0]["action"] == "excel_live.sort_rows"
    assert payload["digest"]["active_sheet"] == "매출"


def test_local_success_is_not_recorded(tmp_path):
    """정상 처리까지 큐에 쌓으면 실패 신호가 묻힌다."""
    parser = _Parser([GOOD_PLAN])
    result = _run(
        message="정렬",
        parse=parser,
        validate=_accept_all,
        context={},
        allow_strong=False,
    )
    assert record_escalation(message="정렬", result=result, log_dir=tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_without_log_dir_the_escalation_is_a_chat_log_line(tmp_path, monkeypatch):
    """런타임 기본 경로 — `logs/` 에 `planner_escalations.jsonl` 을 만들지 않는다(2026-09-10).

    승격 기록은 `chat_log.jsonl` 에 `record: planner_escalation` 줄로 들어가고, 턴을
    읽는 `iter_turns()` 는 그 줄을 건너뛴다.
    """
    logs_dir = tmp_path / "logs"
    chat_log = logs_dir / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: chat_log)

    parser = _Parser([GOOD_PLAN, GOOD_PLAN])
    validations = [(False, "열 없음"), (True, "")]
    result = _run(
        message="금액 정렬",
        parse=parser,
        validate=lambda steps: validations.pop(0),
        context={},
        allow_strong=False,
    )
    returned = record_escalation(message="금액 정렬", result=result, workbook_digest={"active_sheet": "매출"})

    assert returned == chat_log
    assert [p.name for p in logs_dir.iterdir()] == ["chat_log.jsonl"]
    assert not list(tmp_path.rglob("planner_escalations.jsonl"))

    line = json.loads(chat_log.read_text(encoding="utf-8").strip())
    assert line["record"] == RECORD_PLANNER_ESCALATION
    assert line["at"].endswith("+09:00")
    assert "turn_id" not in line
    assert line["instruction"] == "금액 정렬"
    assert line["final_tier"] == TIER_REPAIR
    assert line["output_json"]["action_plan"][0]["action"] == "excel_live.sort_rows"
    assert line["digest"]["active_sheet"] == "매출"
    assert "recorded_at" in line, "기존 payload 키는 그대로 남는다"

    assert list(decision_trace.iter_turns(chat_log)) == []
    escalations = list(decision_trace.iter_records(chat_log, record=RECORD_PLANNER_ESCALATION))
    assert [e["instruction"] for e in escalations] == ["금액 정렬"]
