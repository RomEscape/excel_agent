"""Excel Live 계획 실행기.

Planner가 만든 action_plan을 표준 형태로 실행/검증/재시도한다.
OpenClaw 상위 오케스트레이터가 붙더라도 동일 인터페이스를 재사용할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class PlanStep:
    action: str
    params: dict[str, Any]
    reason: str = ""


@dataclass
class StepExecutionResult:
    index: int
    action: str
    params: dict[str, Any]
    reason: str
    result: dict[str, Any]
    retried: bool
    verified: bool
    error: str | None = None
    verify_detail: str | None = None


@dataclass
class ExecutionResult:
    steps: list[StepExecutionResult]

    @property
    def ok(self) -> bool:
        return bool(self.steps)

    @property
    def last(self) -> StepExecutionResult | None:
        return self.steps[-1] if self.steps else None


def normalize_plan_steps(raw_steps: Any) -> list[PlanStep]:
    if not isinstance(raw_steps, list):
        return []
    out: list[PlanStep] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "")).strip()
        params = step.get("params", {})
        if not isinstance(params, dict):
            params = {}
        reason = str(step.get("reason", "")).strip()
        if not action:
            continue
        out.append(PlanStep(action=action, params=params, reason=reason))
    return out


def execute_plan(
    *,
    steps: list[PlanStep],
    execute_action: Callable[[str, dict[str, Any]], dict[str, Any]],
    verify_step: Callable[[str, dict[str, Any], dict[str, Any]], bool | tuple[bool, str]],
    max_attempts: int = 2,
    abort_on_failure: bool = True,
    on_step_complete: Callable[[StepExecutionResult], None] | None = None,
) -> ExecutionResult:
    results: list[StepExecutionResult] = []
    for idx, step in enumerate(steps, start=1):
        last_result: dict[str, Any] | None = None
        verified = False
        verify_detail: str | None = None
        error_text: str | None = None
        attempts = max(1, int(max_attempts))
        used_attempts = 0
        for _ in range(attempts):
            used_attempts += 1
            try:
                out = execute_action(step.action, step.params)
            except Exception as exc:  # noqa: BLE001 - 실행기에서 예외를 결과로 구조화한다.
                error_text = str(exc)
                if used_attempts >= attempts:
                    break
                continue
            last_result = out
            checked = verify_step(step.action, step.params, out)
            if isinstance(checked, tuple):
                is_ok, detail = checked
            else:
                is_ok, detail = bool(checked), ""
            if is_ok:
                verified = True
                verify_detail = None
                break
            verify_detail = detail or "verify_failed"
        if last_result is None:
            last_result = {}
        step_result = StepExecutionResult(
            index=idx,
            action=step.action,
            params=step.params,
            reason=step.reason,
            result=last_result,
            retried=used_attempts > 1,
            verified=verified,
            error=error_text,
            verify_detail=verify_detail,
        )
        results.append(step_result)
        if on_step_complete is not None:
            on_step_complete(step_result)
        if abort_on_failure and (step_result.error or not step_result.verified):
            break
    return ExecutionResult(steps=results)
