"""Excel Live 실행기(plan/executor) 단위 테스트."""

from office_claw_sidecar.services.excel_live_executor import (
    ExecutionResult,
    PlanStep,
    execute_plan,
    normalize_plan_steps,
)


def test_normalize_plan_steps_filters_invalid_entries():
    steps = normalize_plan_steps(
        [
            {"action": "excel_live.read_range", "params": {"range_ref": "A1"}, "reason": "읽기"},
            {"action": "", "params": {}},
            "invalid",
        ]
    )
    assert len(steps) == 1
    assert steps[0].action == "excel_live.read_range"


def test_execute_plan_runs_steps_in_order():
    called = []

    def _execute(action, params):
        called.append((action, params))
        return {"address": params.get("range_ref", "A1"), "row_count": 1, "col_count": 1}

    def _verify(action, params, result):
        return bool(result.get("address"))

    out: ExecutionResult = execute_plan(
        steps=[
            PlanStep(action="excel_live.read_range", params={"range_ref": "A1"}, reason="first"),
            PlanStep(action="excel_live.read_range", params={"range_ref": "B2"}, reason="second"),
        ],
        execute_action=_execute,
        verify_step=_verify,
        max_attempts=2,
    )
    assert out.ok is True
    assert len(out.steps) == 2
    assert called[0][1]["range_ref"] == "A1"
    assert called[1][1]["range_ref"] == "B2"


def test_execute_plan_retries_and_aborts_on_verify_failure():
    calls = {"count": 0}

    def _execute(action, params):
        calls["count"] += 1
        return {"address": "A1", "row_count": 1, "col_count": 1}

    def _verify(action, params, result):
        return False, "changed_cells=0"

    out = execute_plan(
        steps=[
            PlanStep(action="excel_live.fill_range", params={"target_range": "A:A"}, reason="first"),
            PlanStep(action="excel_live.read_range", params={"range_ref": "A1"}, reason="second"),
        ],
        execute_action=_execute,
        verify_step=_verify,
        max_attempts=2,
        abort_on_failure=True,
    )
    # 첫 step만 2회 재시도 후 중단
    assert calls["count"] == 2
    assert len(out.steps) == 1
    assert out.steps[0].verified is False
    assert out.steps[0].retried is True
    assert out.steps[0].verify_detail == "changed_cells=0"


def test_execute_plan_captures_action_exception():
    def _execute(action, params):
        raise RuntimeError("COM error")

    def _verify(action, params, result):
        return True

    out = execute_plan(
        steps=[PlanStep(action="excel_live.write_range", params={"start_cell": "A1", "values_2d": [[1]]})],
        execute_action=_execute,
        verify_step=_verify,
        max_attempts=1,
        abort_on_failure=True,
    )
    assert len(out.steps) == 1
    assert out.steps[0].error is not None
    assert out.steps[0].verified is False
