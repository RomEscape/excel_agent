"""Excel Live 계획 실행기.

Planner가 만든 action_plan을 표준 형태로 실행/검증/재시도한다.
OpenClaw 상위 오케스트레이터가 붙더라도 동일 인터페이스를 재사용할 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
    # 재시도 전에 파라미터가 보정됐다면 `params`는 **실제로 실행된** 쪽이다.
    # 원본은 여기 남겨야 "무엇을 고쳐서 됐는지"를 로그에서 볼 수 있다.
    original_params: dict[str, Any] | None = None


@dataclass
class ExecutionResult:
    steps: list[StepExecutionResult]

    @property
    def ok(self) -> bool:
        """모든 단계가 예외 없이 끝나고 검증까지 통과했는가."""
        if not self.steps:
            return False
        return all(s.error is None and s.verified for s in self.steps)

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
    reraise: tuple[type[BaseException], ...] = (),
    repair: Callable[[PlanStep, Exception | str], PlanStep | None] | None = None,
) -> ExecutionResult:
    """계획을 순서대로 실행한다.

    reraise에 넣은 예외는 결과로 감싸지 않고 그대로 올려보낸다. "대상 통합문서를
    못 정했다" 같은 정보 부족은 재시도한다고 달라지지 않고, 단계 실패로 접어 두면
    호출자가 되묻기로 바꿀 기회를 잃는다.

    repair를 주면 재시도 **전에** 파라미터를 고칠 기회를 준다. 잘못된 범위나 없는
    시트명 같은 결정적 실패는 같은 파라미터로 다시 던져 봐야 똑같이 실패하고
    지연만 두 배가 된다. repair가 None을 돌려주면 못 고친다는 뜻이므로 남은
    시도를 버리고 즉시 실패로 접는다.

    repair를 안 주면 예전처럼 같은 파라미터로 재시도한다 — COM의 일시적 실패는
    그대로 다시 시도하는 게 맞기 때문에 이 경로를 없애지는 않는다.
    """
    results: list[StepExecutionResult] = []
    for idx, step in enumerate(steps, start=1):
        last_result: dict[str, Any] | None = None
        verified = False
        verify_detail: str | None = None
        error_text: str | None = None
        attempts = max(1, int(max_attempts))
        used_attempts = 0
        current = step
        for _ in range(attempts):
            used_attempts += 1
            failure: Exception | str
            try:
                out = execute_action(current.action, current.params)
            except reraise:
                raise
            except Exception as exc:
                error_text = str(exc)
                failure = exc
            else:
                error_text = None
                last_result = out
                checked = verify_step(current.action, current.params, out)
                if isinstance(checked, tuple):
                    is_ok, detail = checked
                else:
                    is_ok, detail = bool(checked), ""
                if is_ok:
                    verified = True
                    verify_detail = None
                    break
                verify_detail = detail or "verify_failed"
                failure = verify_detail

            if used_attempts >= attempts:
                break
            if repair is not None:
                repaired = repair(current, failure)
                if repaired is None:
                    break
                current = repaired
        if last_result is None:
            last_result = {}
        step_result = StepExecutionResult(
            index=idx,
            action=current.action,
            params=current.params,
            reason=current.reason,
            result=last_result,
            retried=used_attempts > 1,
            verified=verified,
            error=error_text,
            verify_detail=verify_detail,
            original_params=dict(step.params) if current is not step else None,
        )
        results.append(step_result)
        if on_step_complete is not None:
            on_step_complete(step_result)
        if abort_on_failure and (step_result.error or not step_result.verified):
            break
    return ExecutionResult(steps=results)
