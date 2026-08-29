"""사용자 하네스 학습 루프 라우터."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from office_claw_sidecar.services.excel_live_agent import parse_excel_live_command
from office_claw_sidecar.services.llm_service import LLMService, get_llm_service
from office_claw_sidecar.services.user_harness_service import (
    build_personalization_prompt,
    evaluate_quality_gate,
    get_personalization_snapshot,
    list_recent_failure_events,
    list_user_keys,
    record_replay_report,
    record_user_feedback_event,
    resolve_user_key,
    update_learning_state_with_replay,
)

router = APIRouter(tags=["harness"])

KST = timezone(timedelta(hours=9), name="KST")


class HarnessFeedbackRequest(BaseModel):
    user_id: str | None = Field(None, description="사용자 식별자(없으면 session_id 기반)")
    session_id: str | None = Field(None, description="세션 식별자")
    route: str = Field("/excel-live/command", description="피드백 대상 라우트")
    message: str = Field("", description="피드백 대상 원문")
    rating: Literal["good", "bad"] = Field(..., description="사용자 만족도")
    reason: str = Field("", description="평가 이유")
    expected_action: str = Field("", description="(bad 시) 기대한 액션")
    expected_behavior: str = Field("", description="(bad 시) 기대한 동작 설명")


class HarnessReplayRequest(BaseModel):
    user_id: str | None = Field(None, description="사용자 식별자")
    session_id: str | None = Field(None, description="세션 식별자")
    route: str = Field("/excel-live/command", description="재시험 대상 라우트")
    limit: int = Field(20, ge=1, le=200, description="재시험 최대 건수")
    parse_timeout_seconds: float = Field(10.0, ge=2.0, le=45.0, description="건당 파싱 제한 시간")
    min_gate_cases: int = Field(5, ge=1, le=200, description="품질 게이트 최소 케이스")
    min_gate_pass_rate: float = Field(0.7, ge=0.0, le=1.0, description="품질 게이트 최소 성공률")


@router.post("/feedback")
def post_feedback(req: HarnessFeedbackRequest):
    payload = {"user_id": req.user_id, "session_id": req.session_id}
    saved = record_user_feedback_event(
        user_payload=payload,
        rating=req.rating,
        reason=req.reason,
        route=req.route,
        message=req.message,
        expected_action=req.expected_action,
        expected_behavior=req.expected_behavior,
    )
    return {
        "ok": True,
        "user_key": resolve_user_key(payload),
        "saved": saved,
    }


@router.get("/personalization")
def get_personalization(
    user_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
):
    payload = {"user_id": user_id, "session_id": session_id}
    user_key = resolve_user_key(payload)
    snapshot = get_personalization_snapshot(user_key)
    snapshot["prompt_for_llm"] = build_personalization_prompt(user_key)
    return {"ok": True, **snapshot}


async def _replay_failures_internal(req: HarnessReplayRequest, llm: LLMService) -> dict[str, Any]:
    payload = {"user_id": req.user_id, "session_id": req.session_id}
    user_key = resolve_user_key(payload)
    failures = list_recent_failure_events(
        user_key,
        route=req.route,
        limit=req.limit,
    )
    personalization_hint = build_personalization_prompt(user_key)
    replay_cases: list[dict] = []
    replay_success = 0

    for row in failures:
        message = str(row.get("message", "")).strip()
        if not message:
            continue
        parse_context = {
            "workbook_id": row.get("workbook_id"),
            "sheet_name": row.get("sheet_name"),
            "reasoning_mode": "deep",
            "complexity_score": 4,
            "personalization_hint": personalization_hint,
        }
        case = {
            "message": message,
            "previous_action": str(row.get("action", "") or ""),
            "previous_status_code": int(row.get("status_code", 0) or 0),
            "previous_error": str(row.get("error", "") or ""),
            "replayed_ok": False,
            "replayed_action": "",
            "replayed_reason": "",
            "replayed_error": "",
        }
        try:
            parsed = await asyncio.wait_for(
                parse_excel_live_command(
                    message,
                    llm_service=llm,
                    context=parse_context,
                ),
                timeout=req.parse_timeout_seconds,
            )
            action_plan = parsed.get("action_plan", [])
            first_action = ""
            if isinstance(action_plan, list) and action_plan:
                first = action_plan[0] if isinstance(action_plan[0], dict) else {}
                first_action = str(first.get("action", "")).strip()
            case["replayed_ok"] = bool(first_action)
            case["replayed_action"] = first_action
            case["replayed_reason"] = str(parsed.get("reason", "") or "")
            if case["replayed_ok"]:
                replay_success += 1
        except Exception as exc:
            case["replayed_error"] = str(exc)
        replay_cases.append(case)

    replay_total = len(replay_cases)
    quality_gate = evaluate_quality_gate(
        replay_total=replay_total,
        replay_success=replay_success,
        min_cases=req.min_gate_cases,
        min_pass_rate=req.min_gate_pass_rate,
    )
    report = {
        "at": datetime.now(KST).isoformat(),
        "route": req.route,
        "replay_total": replay_total,
        "replay_success": replay_success,
        "replay_fail": max(0, replay_total - replay_success),
        "quality_gate": quality_gate,
        "cases": replay_cases[:100],
    }
    record_replay_report(user_key, report)
    learning_state = update_learning_state_with_replay(
        user_key=user_key,
        replay_report=report,
        quality_gate=quality_gate,
    )
    return {
        "ok": True,
        "user_key": user_key,
        "quality_gate": quality_gate,
        "replay_report": report,
        "active_prompt_applied": str(learning_state.get("active_prompt", "") or ""),
    }


@router.post("/replay-failures")
async def post_replay_failures(
    req: HarnessReplayRequest,
    llm: LLMService = Depends(get_llm_service),
):
    return await _replay_failures_internal(req, llm)


async def run_nightly_replay_batch(
    *,
    route: str = "/excel-live/command",
    limit: int = 20,
    parse_timeout_seconds: float = 10.0,
    min_gate_cases: int = 5,
    min_gate_pass_rate: float = 0.7,
    max_users: int = 200,
) -> dict[str, Any]:
    """야간 배치: 사용자별 실패 리플레이를 순회 실행한다."""
    llm = get_llm_service()
    users = list_user_keys(limit=max_users)
    rows: list[dict[str, Any]] = []

    for user_key in users:
        req = HarnessReplayRequest(
            user_id=user_key,
            route=route,
            limit=limit,
            parse_timeout_seconds=parse_timeout_seconds,
            min_gate_cases=min_gate_cases,
            min_gate_pass_rate=min_gate_pass_rate,
        )
        try:
            result = await _replay_failures_internal(req, llm)
            rows.append(
                {
                    "user_key": user_key,
                    "ok": True,
                    "quality_gate_passed": bool(result.get("quality_gate", {}).get("passed", False)),
                    "replay_total": int(result.get("replay_report", {}).get("replay_total", 0) or 0),
                    "replay_success": int(result.get("replay_report", {}).get("replay_success", 0) or 0),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "user_key": user_key,
                    "ok": False,
                    "error": str(exc),
                }
            )

    success_users = sum(1 for row in rows if row.get("ok"))
    passed_users = sum(1 for row in rows if row.get("quality_gate_passed"))
    return {
        "ok": True,
        "at": datetime.now(KST).isoformat(),
        "route": route,
        "users_total": len(users),
        "users_ok": success_users,
        "users_gate_passed": passed_users,
        "results": rows,
    }
