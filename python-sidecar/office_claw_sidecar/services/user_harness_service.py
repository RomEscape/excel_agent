"""사용자 대화/처리 이력 누적 서비스.

목표:
- 사용자 요청/응답 이벤트를 JSONL로 누적
- 누적 통계를 profile.json으로 유지
- 사용자 맞춤 실행 요약 README.md 자동 갱신
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.services.traffic_origin import current_origin
from office_claw_sidecar.services.unified_log_service import append_unified_event

KST = timezone(timedelta(hours=9), name="KST")


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _safe_user_key(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "local_user"
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", raw).strip("._-")
    return cleaned[:64] or "local_user"


def _extract_user_key(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("user_id") or payload.get("userId") or payload.get("profile_key") or "").strip()
    if explicit:
        return _safe_user_key(explicit)
    session_key = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
    if session_key:
        return _safe_user_key(f"session_{session_key}")
    return "local_user"


def resolve_user_key(payload: dict[str, Any] | None) -> str:
    """외부 라우터에서 사용할 사용자 키 해석 함수."""
    raw = payload if isinstance(payload, dict) else {}
    return _extract_user_key(raw)


def _harness_root() -> Path:
    root = get_data_dir() / "user_harness"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_user_keys(limit: int = 200) -> list[str]:
    """저장된 사용자 하네스 키 목록을 반환한다."""
    max_items = max(1, min(1000, int(limit or 200)))
    rows: list[str] = []
    root = _harness_root()
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            rows.append(_safe_user_key(child.name))
    except Exception:
        rows = []
    rows = sorted({key for key in rows if key})
    if "local_user" not in rows:
        rows.insert(0, "local_user")
    return rows[:max_items]


def _user_dir(user_key: str) -> Path:
    path = _harness_root() / _safe_user_key(user_key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                text = str(line or "").strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except Exception:
        return []
    if limit is not None:
        max_items = max(1, int(limit))
        return rows[-max_items:]
    return rows


def _feedback_path(user_key: str) -> Path:
    return _user_dir(user_key) / "feedback.jsonl"


def _replay_reports_path(user_key: str) -> Path:
    return _user_dir(user_key) / "replay_reports.jsonl"


def _learning_state_path(user_key: str) -> Path:
    return _user_dir(user_key) / "learning_state.json"


def _shorten(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _extract_message(payload: dict[str, Any]) -> str:
    for key in ("message", "text", "prompt"):
        if key in payload:
            return _shorten(payload.get(key), 280)
    return ""


def _extract_response_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("response") or "").strip()
    if direct:
        return _shorten(direct, 400)
    reason = str(payload.get("reason") or "").strip()
    if reason:
        return _shorten(reason, 280)
    result_obj = payload.get("result")
    if isinstance(result_obj, dict):
        follow = str(result_obj.get("follow_up_question") or "").strip()
        if follow:
            return _shorten(follow, 280)
    return ""


def _extract_xlwings_ops(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result_obj = payload.get("result")
    if not isinstance(result_obj, dict):
        return []

    raw_ops: list[dict[str, Any]] = []
    direct_ops = result_obj.get("xlwings_ops")
    if isinstance(direct_ops, list):
        raw_ops.extend([row for row in direct_ops if isinstance(row, dict)])

    plan_rows = result_obj.get("plan")
    if isinstance(plan_rows, list):
        for step in plan_rows:
            if not isinstance(step, dict):
                continue
            step_result = step.get("result")
            if not isinstance(step_result, dict):
                continue
            step_ops = step_result.get("xlwings_ops")
            if isinstance(step_ops, list):
                raw_ops.extend([row for row in step_ops if isinstance(row, dict)])

    compact_ops: list[dict[str, Any]] = []
    for op in raw_ops[:20]:
        params = op.get("params") if isinstance(op.get("params"), dict) else {}
        result = op.get("result") if isinstance(op.get("result"), dict) else {}

        compact_params: dict[str, Any] = {}
        for key, value in list(params.items())[:12]:
            compact_params[str(key)] = _shorten(value, 120)

        compact_result: dict[str, Any] = {}
        for key in (
            "address",
            "changed_cells",
            "written_cells",
            "cleared_cells",
            "matched_cells",
            "formula_applied_cells",
            "rows",
            "cols",
            "created",
            "selected",
            "queue_wait_ms",
            "numeric_cells",
            "sum",
            "average",
        ):
            if key not in result:
                continue
            compact_result[key] = result.get(key)

        compact_ops.append(
            {
                "engine": _shorten(op.get("engine", "xlwings"), 32),
                "action": _shorten(op.get("action", ""), 80),
                "method": _shorten(op.get("method", ""), 80),
                "workbook_id": _shorten(op.get("workbook_id", ""), 180),
                "sheet_name": _shorten(op.get("sheet_name", ""), 80),
                "target_range": _shorten(op.get("target_range", ""), 64),
                "params": compact_params,
                "result": compact_result,
            }
        )
    return compact_ops


def _render_profile_readme(profile: dict[str, Any]) -> str:
    totals = profile.get("totals", {})
    action_counts = profile.get("action_counts", {})
    recent = profile.get("recent_messages", [])
    feedback = profile.get("feedback", {})

    total_events = int(totals.get("events", 0))
    total_success = int(totals.get("success", 0))
    success_rate = (total_success / total_events * 100.0) if total_events else 0.0

    top_actions = sorted(
        ((str(k), int(v)) for k, v in action_counts.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:10]

    lines: list[str] = []
    lines.append("# 사용자 맞춤 실행 README")
    lines.append("")
    lines.append(f"- 사용자 키: `{profile.get('user_key', 'local_user')}`")
    lines.append(f"- 마지막 업데이트: `{profile.get('last_updated', _now_iso())}`")
    lines.append(f"- 누적 이벤트: `{total_events}`")
    lines.append(f"- 성공 이벤트: `{total_success}`")
    lines.append(f"- 성공률: `{success_rate:.1f}%`")
    lines.append(f"- 후속질문 발생: `{int(totals.get('follow_up', 0))}`")
    lines.append(f"- 승인 필요 발생: `{int(totals.get('approval_required', 0))}`")
    lines.append(f"- 타임아웃/네트워크 실패: `{int(totals.get('timeouts', 0))}`")
    lines.append(f"- 피드백(좋음): `{int(feedback.get('good', 0))}`")
    lines.append(f"- 피드백(나쁨): `{int(feedback.get('bad', 0))}`")
    lines.append("")
    lines.append("## 자주 실행된 액션")
    if top_actions:
        for action, count in top_actions:
            lines.append(f"- `{action}`: {count}회")
    else:
        lines.append("- 아직 누적된 실행 이력이 없습니다.")
    lines.append("")
    lines.append("## 최근 요청/처리 흐름")
    if recent:
        for row in reversed(recent[-20:]):
            lines.append(
                "- "
                f"[{row.get('at', '')}] "
                f"route=`{row.get('route', '')}` "
                f"status=`{row.get('status_code', 0)}` "
                f"action=`{row.get('action', '')}` "
                f"msg=\"{_shorten(row.get('message', ''), 90)}\""
            )
    else:
        lines.append("- 아직 최근 요청 이력이 없습니다.")
    lines.append("")
    lines.append("> 이 파일은 자동 생성됩니다. 수동 편집 내용은 다음 업데이트에서 덮어써질 수 있습니다.")
    lines.append("")
    return "\n".join(lines)


def _update_profile(user_key: str, event: dict[str, Any]) -> None:
    user_path = _user_dir(user_key)
    profile_path = user_path / "profile.json"
    readme_path = user_path / "README.md"

    profile = _read_json(
        profile_path,
        {
            "user_key": user_key,
            "created_at": _now_iso(),
            "last_updated": _now_iso(),
            "totals": {
                "events": 0,
                "success": 0,
                "failure": 0,
                "follow_up": 0,
                "approval_required": 0,
                "timeouts": 0,
            },
            "action_counts": {},
            "route_counts": {},
            "recent_messages": [],
            "feedback": {"good": 0, "bad": 0},
        },
    )

    totals = profile.setdefault("totals", {})
    action_counts = profile.setdefault("action_counts", {})
    route_counts = profile.setdefault("route_counts", {})
    recent_messages = profile.setdefault("recent_messages", [])

    status_code = int(event.get("status_code", 0))
    action = str(event.get("action", "")).strip() or "__none__"
    route = str(event.get("route", "")).strip() or "__unknown__"

    totals["events"] = int(totals.get("events", 0)) + 1
    if 200 <= status_code < 300:
        totals["success"] = int(totals.get("success", 0)) + 1
    else:
        totals["failure"] = int(totals.get("failure", 0)) + 1
    if bool(event.get("ask_follow_up", False)):
        totals["follow_up"] = int(totals.get("follow_up", 0)) + 1
    if bool(event.get("approval_required", False)):
        totals["approval_required"] = int(totals.get("approval_required", 0)) + 1
    if status_code == 0 or "timeout" in str(event.get("error", "")).lower():
        totals["timeouts"] = int(totals.get("timeouts", 0)) + 1

    action_counts[action] = int(action_counts.get(action, 0)) + 1
    route_counts[route] = int(route_counts.get(route, 0)) + 1

    recent_messages.append(
        {
            "at": event.get("at"),
            "route": route,
            "status_code": status_code,
            "action": action,
            "message": _shorten(event.get("message", ""), 280),
            "elapsed_ms": int(event.get("elapsed_ms", 0)),
            "ask_follow_up": bool(event.get("ask_follow_up", False)),
            "approval_required": bool(event.get("approval_required", False)),
        }
    )
    if len(recent_messages) > 200:
        del recent_messages[: len(recent_messages) - 200]

    profile["user_key"] = user_key
    profile["last_updated"] = _now_iso()

    _write_json(profile_path, profile)
    readme_path.write_text(_render_profile_readme(profile), encoding="utf-8")


def record_user_harness_event(
    *,
    route: str,
    method: str,
    request_payload: dict[str, Any] | None,
    response_payload: dict[str, Any] | None,
    status_code: int,
    elapsed_ms: int,
) -> None:
    req = request_payload if isinstance(request_payload, dict) else {}
    res = response_payload if isinstance(response_payload, dict) else {}

    user_key = _extract_user_key(req)
    result_obj = res.get("result") if isinstance(res.get("result"), dict) else {}
    action = str(res.get("action", "")).strip()
    xlwings_ops = _extract_xlwings_ops(res)
    error_text = ""
    if status_code >= 400:
        error_text = _shorten(res.get("detail") or res, 240)

    event = {
        "at": _now_iso(),
        # 나중에 학습 데이터를 수확할 때 사람이 친 명령만 골라내려면, 지금
        # 남겨 두는 수밖에 없다. 추정으로 되돌리는 건 이미 한 번 실패했다.
        "origin": current_origin(),
        "route": str(route or ""),
        "method": str(method or "POST"),
        "status_code": int(status_code),
        "elapsed_ms": int(elapsed_ms),
        "session_id": str(req.get("session_id") or req.get("sessionId") or ""),
        "message": _extract_message(req),
        "workbook_id": str(req.get("workbook_id") or ""),
        "sheet_name": str(req.get("sheet_name") or ""),
        "action": action,
        "ok": bool(res.get("ok", False)),
        "ask_follow_up": bool(result_obj.get("ask_follow_up", False)),
        "approval_required": bool(res.get("approval_required", False)),
        "reason": _shorten(res.get("reason", ""), 240),
        "response_text": _extract_response_text(res),
        "error": error_text,
        "xlwings_op_count": len(xlwings_ops),
        "xlwings_ops": xlwings_ops,
    }

    user_path = _user_dir(user_key)
    events_path = user_path / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    append_unified_event("harness", event)
    _update_profile(user_key, event)


def _update_profile_feedback(user_key: str, *, rating: str) -> None:
    user_path = _user_dir(user_key)
    profile_path = user_path / "profile.json"
    readme_path = user_path / "README.md"
    profile = _read_json(
        profile_path,
        {
            "user_key": user_key,
            "created_at": _now_iso(),
            "last_updated": _now_iso(),
            "totals": {
                "events": 0,
                "success": 0,
                "failure": 0,
                "follow_up": 0,
                "approval_required": 0,
                "timeouts": 0,
            },
            "action_counts": {},
            "route_counts": {},
            "recent_messages": [],
            "feedback": {"good": 0, "bad": 0},
        },
    )
    feedback = profile.setdefault("feedback", {"good": 0, "bad": 0})
    key = "good" if str(rating or "").strip().lower() == "good" else "bad"
    feedback[key] = int(feedback.get(key, 0)) + 1
    profile["last_updated"] = _now_iso()
    _write_json(profile_path, profile)
    readme_path.write_text(_render_profile_readme(profile), encoding="utf-8")


def record_user_feedback_event(
    *,
    user_payload: dict[str, Any] | None,
    rating: str,
    reason: str = "",
    route: str = "",
    message: str = "",
    expected_action: str = "",
    expected_behavior: str = "",
) -> dict[str, Any]:
    """
    명시적 사용자 피드백 이벤트를 저장한다.
    """
    user_key = resolve_user_key(user_payload)
    normalized_rating = str(rating or "").strip().lower()
    if normalized_rating not in {"good", "bad"}:
        normalized_rating = "bad"
    raw_payload = user_payload if isinstance(user_payload, dict) else {}
    row = {
        "at": _now_iso(),
        "user_key": user_key,
        "session_id": str(raw_payload.get("session_id") or raw_payload.get("sessionId") or ""),
        "route": str(route or ""),
        "rating": normalized_rating,
        "reason": _shorten(reason, 400),
        "message": _shorten(message, 400),
        "expected_action": _shorten(expected_action, 120),
        "expected_behavior": _shorten(expected_behavior, 300),
    }
    _append_jsonl(_feedback_path(user_key), row)
    append_unified_event("harness_feedback", row)
    _update_profile_feedback(user_key, rating=normalized_rating)
    return row


def list_recent_feedback(user_key: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return _read_jsonl(_feedback_path(user_key), limit=max(1, min(500, int(limit))))


def list_recent_failure_events(
    user_key: str,
    *,
    route: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows = _read_jsonl(_user_dir(user_key) / "events.jsonl", limit=5000)
    target_route = str(route or "").strip()
    failures: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if target_route and str(row.get("route", "")).strip() != target_route:
            continue
        status = int(row.get("status_code", 0) or 0)
        ok = bool(row.get("ok", False))
        has_error = bool(str(row.get("error", "")).strip())
        if status >= 400 or has_error or not ok:
            failures.append(row)
    return failures[-max(1, min(500, int(limit))):]


def _summarize_expected_actions(feedback_rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in feedback_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("rating", "")).strip().lower() != "bad":
            continue
        action = str(row.get("expected_action", "")).strip()
        if not action:
            continue
        counts[action] = int(counts.get(action, 0)) + 1
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return ordered[:8]


def _build_candidate_personalization_prompt(user_key: str) -> str:
    profile = _read_json(_user_dir(user_key) / "profile.json", {})
    action_counts = profile.get("action_counts", {}) if isinstance(profile, dict) else {}
    top_actions = sorted(
        ((str(k), int(v)) for k, v in action_counts.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:6]
    feedback_rows = list_recent_feedback(user_key, limit=200)
    expected_actions = _summarize_expected_actions(feedback_rows)
    phrases = []
    for row in reversed(feedback_rows):
        if str(row.get("rating", "")).strip().lower() != "bad":
            continue
        msg = str(row.get("message", "")).strip()
        if not msg:
            continue
        exp = str(row.get("expected_action", "")).strip()
        if exp:
            phrases.append(f"- 실패 표현: \"{_shorten(msg, 80)}\" -> 기대 액션 `{exp}`")
        if len(phrases) >= 4:
            break

    lines: list[str] = []
    lines.append("개인화 힌트:")
    if top_actions:
        joined = ", ".join(f"{a}({c})" for a, c in top_actions)
        lines.append(f"- 자주 실행되는 액션: {joined}")
    if expected_actions:
        joined = ", ".join(f"{a}({c})" for a, c in expected_actions)
        lines.append(f"- 최근 실패 피드백 기대 액션 우선순위: {joined}")
    if phrases:
        lines.extend(phrases)
    lines.append("- 모호하면 잘못 실행보다 후속 질문을 우선한다.")
    return "\n".join(lines)


def _read_learning_state(user_key: str) -> dict[str, Any]:
    return _read_json(
        _learning_state_path(user_key),
        {
            "updated_at": _now_iso(),
            "quality_gate": {
                "passed": False,
                "reason": "not_evaluated",
                "checked_at": "",
            },
            "active_prompt": "",
            "candidate_prompt": "",
            "last_replay": {},
        },
    )


def _write_learning_state(user_key: str, payload: dict[str, Any]) -> None:
    _write_json(_learning_state_path(user_key), payload)


def evaluate_quality_gate(
    *,
    replay_total: int,
    replay_success: int,
    min_cases: int = 5,
    min_pass_rate: float = 0.70,
) -> dict[str, Any]:
    total = max(0, int(replay_total))
    success = max(0, int(replay_success))
    pass_rate = (float(success) / float(total)) if total > 0 else 0.0
    enough_cases = total >= max(1, int(min_cases))
    passed = bool(enough_cases and pass_rate >= float(min_pass_rate))
    reason = "ok" if passed else ("insufficient_cases" if not enough_cases else "low_pass_rate")
    return {
        "passed": passed,
        "reason": reason,
        "replay_total": total,
        "replay_success": success,
        "pass_rate": round(pass_rate, 4),
        "min_cases": int(min_cases),
        "min_pass_rate": float(min_pass_rate),
    }


def update_learning_state_with_replay(
    *,
    user_key: str,
    replay_report: dict[str, Any],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    state = _read_learning_state(user_key)
    candidate = _build_candidate_personalization_prompt(user_key)
    state["updated_at"] = _now_iso()
    state["candidate_prompt"] = candidate
    state["quality_gate"] = dict(quality_gate or {})
    state["last_replay"] = dict(replay_report or {})
    if bool(quality_gate.get("passed", False)):
        state["active_prompt"] = candidate
    _write_learning_state(user_key, state)
    return state


def build_personalization_prompt(user_key: str) -> str:
    """
    LLM 프롬프트에 주입할 개인화 힌트 문자열을 반환한다.

    우선순위:
    1) quality gate 통과 후 승격된 active_prompt
    2) 없으면 profile/feedback 기반 candidate prompt
    """
    state = _read_learning_state(user_key)
    active = str(state.get("active_prompt", "")).strip()
    if active:
        return _shorten(active, 1200)
    candidate = _build_candidate_personalization_prompt(user_key)
    return _shorten(candidate, 1200)


def get_personalization_snapshot(user_key: str) -> dict[str, Any]:
    state = _read_learning_state(user_key)
    feedback_rows = list_recent_feedback(user_key, limit=50)
    return {
        "user_key": user_key,
        "quality_gate": state.get("quality_gate", {}),
        "active_prompt": str(state.get("active_prompt", "") or ""),
        "candidate_prompt": str(state.get("candidate_prompt", "") or _build_candidate_personalization_prompt(user_key)),
        "recent_feedback_count": len(feedback_rows),
        "recent_feedback": feedback_rows[-10:],
    }


def record_replay_report(user_key: str, report: dict[str, Any]) -> dict[str, Any]:
    row = dict(report or {})
    row["at"] = str(row.get("at", "") or _now_iso())
    row["user_key"] = user_key
    _append_jsonl(_replay_reports_path(user_key), row)
    append_unified_event("harness_replay", row)
    return row

