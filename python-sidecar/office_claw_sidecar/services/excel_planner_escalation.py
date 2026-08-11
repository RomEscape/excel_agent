"""플래너 에스컬레이션 사다리.

지금까지의 실패 구조는 이랬다.

    로컬 7B가 계획을 못 만듦 → 사용자에게 되묻기 or HTTP 400

즉 **모델 한 대의 정확도가 곧 제품의 정확도**였다. 7B가 못 하는 작업은 영원히
안 되고, 고치려면 사람이 규칙을 하나씩 추가하는 수밖에 없었다. 이 모듈은 그
고리를 끊는다.

    0단계  규칙            — LLM 없이 결정적 처리 (라우터가 이미 함)
    1단계  로컬 플래너      — 파인튜닝된 A.X 7B
    2단계  자가 수정        — 검증기가 낸 오류 문구를 붙여 로컬에 1회 재시도
    3단계  강한 모델        — Claude 등으로 승격
    4단계  되묻기          — 여기까지 와야 사용자에게 묻는다

중요한 설계 두 가지:

- **검증 실패도 에스컬레이션 사유다.** JSON 파싱만 보면 "그럴듯하지만 실행 못 하는
  계획"이 그대로 통과한다. 실행 직전 검증을 통과해야 성공으로 친다.
- **에스컬레이션은 곧 학습 데이터다.** 로컬이 틀리고 강한 모델이 맞힌 순간이
  가장 값진 증류 샘플이라, 전부 실패 큐에 적재한다. 사람이 사례를 손으로
  만들지 않아도 다음 라운드 학습셋이 쌓인다.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from office_claw_sidecar.config import get_logs_dir

# 계획 후보를 실행 직전 형태로 검증하는 콜백.
# (ok, 오류 문구)를 돌려준다 — 오류 문구는 자가 수정 프롬프트에 그대로 들어간다.
PlanValidator = Callable[[list[dict[str, Any]]], tuple[bool, str]]

# (message, context) -> 파서 결과 dict. 라우터/에이전트가 주입한다.
PlanParser = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

TIER_LOCAL = "local"
TIER_REPAIR = "local_repair"
TIER_STRONG = "strong"
TIER_FAILED = "failed"

_FAILURE_LOG_NAME = "planner_escalations.jsonl"


def _strong_model_name() -> str:
    return str(os.environ.get("OFFICECLAW_STRONG_MODEL", "")).strip()


def strong_tier_enabled() -> bool:
    """강한 모델 단계를 쓸 수 있는 상태인지.

    키가 없으면 조용히 건너뛴다 — 로컬만으로도 동작해야 하는 제품이라
    키 부재를 오류로 만들면 오프라인 사용자가 전부 막힌다.
    """
    if os.environ.get("OFFICECLAW_DISABLE_STRONG_PLANNER", "").strip() == "1":
        return False
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True
    try:
        from office_claw_sidecar.services.keyring_service import KeyringService

        return bool(KeyringService().retrieve("claude_api_key"))
    except Exception:
        return False


@dataclass
class PlannerAttempt:
    """한 단계의 시도 기록. 그대로 응답의 decision trace에 실린다."""

    tier: str
    model: str
    ok: bool
    error: str = ""
    latency_ms: int = 0


@dataclass
class EscalationResult:
    parsed: dict[str, Any] | None
    attempts: list[PlannerAttempt] = field(default_factory=list)
    final_tier: str = TIER_FAILED
    last_error: str = ""
    # 어느 단계도 검증을 통과하지 못했을 때 마지막으로 받은 계획.
    # 라우터에는 슬롯필링·복구 계획 같은 하류 구제 경로가 있어서, 여기서 None만
    # 돌려주면 예전보다 오히려 더 자주 실패한다. 사다리는 "더 시도하는" 장치이지
    # 기존 구제를 빼앗는 장치가 아니다.
    best_effort: dict[str, Any] | None = None

    @property
    def escalated(self) -> bool:
        return self.final_tier not in {TIER_LOCAL, TIER_FAILED}

    def trace(self) -> list[dict[str, Any]]:
        return [asdict(attempt) for attempt in self.attempts]


def _steps_of(parsed: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(parsed, dict):
        return []
    plan = parsed.get("action_plan")
    return [s for s in plan if isinstance(s, dict)] if isinstance(plan, list) else []


def _is_clarify(parsed: dict[str, Any] | None) -> bool:
    steps = _steps_of(parsed)
    return bool(steps) and str(steps[0].get("action")) == "excel_live.clarify"


def _repair_note(error: str, previous: list[dict[str, Any]]) -> str:
    """자가 수정용 지시문. 무엇이 왜 반려됐는지 구체적으로 준다."""
    actions = ", ".join(str(s.get("action", "")) for s in previous) or "(없음)"
    return (
        f"직전 계획({actions})은 실행 검증에서 반려됐다. 사유: {error}. "
        "같은 실수를 반복하지 말고, 통합문서 상태에 실제로 있는 시트·열 이름과 "
        "필수 파라미터를 모두 채워 다시 계획하라."
    )


async def _attempt(
    parse: PlanParser,
    message: str,
    context: dict[str, Any],
    validate: PlanValidator,
    *,
    tier: str,
    model: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, PlannerAttempt]:
    started = time.monotonic()

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        parsed = await parse(message, context)
    except Exception as exc:
        return None, None, PlannerAttempt(
            tier=tier, model=model, ok=False, error=str(exc), latency_ms=_elapsed()
        )

    # 되묻기는 "검증 통과한 계획"은 아니지만 정상 응답이다. 라우터가 그대로 내보낸다.
    if _is_clarify(parsed):
        return parsed, parsed, PlannerAttempt(tier=tier, model=model, ok=True, latency_ms=_elapsed())

    steps = _steps_of(parsed)
    if not steps:
        return None, None, PlannerAttempt(
            tier=tier, model=model, ok=False, error="계획이 비어 있음", latency_ms=_elapsed()
        )

    ok, error = validate(steps)
    if not ok:
        return None, parsed, PlannerAttempt(
            tier=tier, model=model, ok=False, error=error, latency_ms=_elapsed()
        )
    return parsed, parsed, PlannerAttempt(tier=tier, model=model, ok=True, latency_ms=_elapsed())


async def plan_with_escalation(
    message: str,
    *,
    parse: PlanParser,
    validate: PlanValidator,
    context: dict[str, Any],
    local_model: str = "",
    allow_strong: bool | None = None,
    allow_repair: bool = True,
) -> EscalationResult:
    """계획이 실행 검증을 통과할 때까지 단계를 올린다.

    `allow_repair=False`면 로컬 1회로 끝낸다. 호출자에게 이미 결정적 폴백이 있는
    경우에 쓴다 — 어차피 폴백으로 답할 요청에 LLM을 한 번 더 태우면 지연만 늘어난다.
    """
    result = EscalationResult(parsed=None)

    parsed, best, attempt = await _attempt(
        parse, message, dict(context), validate, tier=TIER_LOCAL, model=local_model or "local"
    )
    result.attempts.append(attempt)
    result.best_effort = best or result.best_effort
    if parsed is not None:
        result.parsed = parsed
        result.final_tier = TIER_LOCAL
        return result
    result.last_error = attempt.error

    if not allow_repair:
        result.final_tier = TIER_FAILED
        return result

    # 2단계: 같은 모델에 오류를 알려주고 한 번 더. 형식 실수·파라미터 누락은
    # 대부분 여기서 잡히고, 강한 모델 호출(비용·지연)을 아낀다.
    repair_context = dict(context)
    repair_context["reflection_note"] = _repair_note(attempt.error, [])
    repair_context["forbid_clarify"] = True
    parsed, best, attempt = await _attempt(
        parse, message, repair_context, validate, tier=TIER_REPAIR, model=local_model or "local"
    )
    result.attempts.append(attempt)
    result.best_effort = best or result.best_effort
    if parsed is not None:
        result.parsed = parsed
        result.final_tier = TIER_REPAIR
        return result
    result.last_error = attempt.error

    use_strong = strong_tier_enabled() if allow_strong is None else bool(allow_strong)
    if use_strong:
        strong_context = dict(context)
        strong_context["planner_provider"] = "strong"
        strong_context["planner_model"] = _strong_model_name()
        strong_context["reflection_note"] = _repair_note(attempt.error, [])
        parsed, best, attempt = await _attempt(
            parse,
            message,
            strong_context,
            validate,
            tier=TIER_STRONG,
            model=_strong_model_name() or "strong",
        )
        result.attempts.append(attempt)
        result.best_effort = best or result.best_effort
        if parsed is not None:
            result.parsed = parsed
            result.final_tier = TIER_STRONG
            return result
        result.last_error = attempt.error

    result.final_tier = TIER_FAILED
    return result


def record_escalation(
    *,
    message: str,
    result: EscalationResult,
    workbook_digest: dict[str, Any] | None = None,
    log_dir: Path | None = None,
) -> Path | None:
    """에스컬레이션·최종 실패를 큐에 적재한다.

    로컬이 틀리고 상위 단계가 맞힌 사례가 다음 학습 라운드의 정답이 된다.
    이걸 쌓아야 "사람이 실패를 하나씩 발견해서 규칙을 추가하는" 고리에서 벗어난다.
    """
    if result.final_tier == TIER_LOCAL:
        return None

    payload = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "instruction": message,
        "final_tier": result.final_tier,
        "last_error": result.last_error,
        "attempts": result.trace(),
        # 정답 계획이 있는 경우에만 학습 후보다. 실패(final_tier=failed)는
        # 되묻기·규칙 보강 대상으로 따로 본다.
        "output_json": result.parsed if result.parsed else None,
        "digest": workbook_digest or None,
    }

    target_dir = log_dir or get_logs_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / _FAILURE_LOG_NAME
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path
    except OSError:
        # 로그를 못 남긴다고 사용자 요청을 실패시키지는 않는다.
        return None
