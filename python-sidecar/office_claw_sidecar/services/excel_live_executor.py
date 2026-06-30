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
    verify_step: Callable[[str, dict[str, Any], dict[str, Any]], bool],
    max_attempts: int = 2,
) -> ExecutionResult:
    results: list[StepExecutionResult] = []
    for idx, step in enumerate(steps, start=1):
        last_result: dict[str, Any] | None = None
        verified = False
        attempts = max(1, int(max_attempts))
        for _ in range(attempts):
            out = execute_action(step.action, step.params)
            last_result = out
            if verify_step(step.action, step.params, out):
                verified = True
                break
        if last_result is None:
            last_result = {}
        results.append(
            StepExecutionResult(
                index=idx,
                action=step.action,
                params=step.params,
                reason=step.reason,
                result=last_result,
                retried=attempts > 1 and not verified,
                verified=verified,
            )
        )
    return ExecutionResult(steps=results)
