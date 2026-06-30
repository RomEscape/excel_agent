"""Excel Live 실행 관측 기반 Critic."""

from __future__ import annotations

from typing import Any

from office_claw_sidecar.services.excel_live_executor import ExecutionResult


def should_replan_after_execution(
    execution: ExecutionResult,
    *,
    intent: str,
    replan_count: int,
    max_replans: int = 1,
) -> bool:
    if replan_count >= max_replans:
        return False
    if intent != "edit":
        return False
    last = execution.last
    if last is None:
        return False
    return bool(last.error) or not bool(last.verified)


def build_replan_context(
    *,
    base_context: dict[str, Any],
    execution: ExecutionResult,
) -> dict[str, Any]:
    ctx = dict(base_context or {})
    last = execution.last
    if last is None:
        return ctx
    result = dict(last.result or {})
    last_address = str(result.get("address", "")).strip().upper()
    if last_address:
        ctx["context_range"] = last_address
    ctx["failed_action"] = last.action
    ctx["failed_reason"] = last.reason
    if last.error:
        ctx["failed_error"] = last.error
    elif last.verify_detail:
        ctx["failed_error"] = f"verify_failed:{last.verify_detail}"
    else:
        ctx["failed_error"] = "unknown_failure"
    return ctx

