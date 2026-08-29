"""실패 이유가 재계획 프롬프트까지 전달되는지 확인한다.

critic이 failed_action/failed_error를 계산해도 `normalize_planner_context`가
그 키를 버리고 있었다. 그래서 재계획 모델은 "방금 왜 실패했는지 모르는 상태에서
다시 계획해라"를 받았고, 같은 인자를 그대로 다시 내놓았다.
"""

from __future__ import annotations

from office_claw_sidecar.services.excel_live_executor import (
    ExecutionResult,
    StepExecutionResult,
)
from office_claw_sidecar.services.excel_live_plan_critic import build_replan_context
from office_claw_sidecar.services.excel_planner_prompt import (
    build_planner_prompt,
    normalize_planner_context,
)


def _failed_step(**overrides):
    base = {
        "index": 1,
        "action": "excel_live.sort_range",
        "params": {"range_ref": "A1:F100", "key_column": "매출", "order": "desc"},
        "reason": "매출 기준 정렬",
        "result": {"address": "A1:F100"},
        "error": "KeyError: '매출' 열을 찾을 수 없습니다",
        "retried": False,
        "verified": False,
        "verify_detail": None,
    }
    base.update(overrides)
    return StepExecutionResult(**base)


def _execution(step):
    return ExecutionResult(steps=[step])


def test_replan_context_carries_action_error_and_args():
    ctx = build_replan_context(base_context={}, execution=_execution(_failed_step()))

    assert ctx["failed_action"] == "excel_live.sort_range"
    assert ctx["failed_error"] == "KeyError: '매출' 열을 찾을 수 없습니다"
    assert ctx["failed_args"]["key_column"] == "매출"


def test_verification_failure_is_reported_as_the_error():
    step = _failed_step(error=None, verify_detail="write_value_mismatch:C3")
    ctx = build_replan_context(base_context={}, execution=_execution(step))

    assert ctx["failed_error"] == "verify_failed:write_value_mismatch:C3"


def test_normalize_keeps_the_failure_fields():
    """정규화가 이 키들을 버리면 프롬프트까지 갈 방법이 없다."""
    ctx = normalize_planner_context(
        {
            "failed_action": "excel_live.write_range",
            "failed_error": "boom",
            "failed_args": {"start_cell": "C3"},
        }
    )

    assert ctx["failed_action"] == "excel_live.write_range"
    assert ctx["failed_error"] == "boom"
    assert "C3" in ctx["failed_args"]


def test_prompt_contains_the_exact_error_text():
    execution = _execution(_failed_step())
    ctx = build_replan_context(base_context={}, execution=execution)

    prompt = build_planner_prompt("매출 높은 순으로 정렬해줘", context=ctx)

    assert "KeyError: '매출' 열을 찾을 수 없습니다" in prompt
    assert "excel_live.sort_range" in prompt
    assert "key_column" in prompt


def test_prompt_has_no_failure_block_on_a_first_attempt():
    """첫 시도 프롬프트는 그대로여야 한다. 학습 데이터와 형식이 어긋나면 안 된다."""
    prompt = build_planner_prompt("C3에 120 입력해줘", context={})

    assert "직전 실행 실패" not in prompt
