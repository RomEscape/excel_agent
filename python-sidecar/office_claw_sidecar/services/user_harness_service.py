"""사용자 대화/처리 이력 누적 서비스.

목표:
- 사용자 요청/응답 이벤트를 JSONL로 누적
- 누적 통계를 profile.json으로 유지
- 사용자 맞춤 실행 요약 README.md 자동 갱신
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.services.unified_log_service import append_unified_event


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return "local_user"


def _harness_root() -> Path:
    root = get_data_dir() / "user_harness"
    root.mkdir(parents=True, exist_ok=True)
    return root


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


def _render_profile_readme(profile: dict[str, Any]) -> str:
    totals = profile.get("totals", {})
    action_counts = profile.get("action_counts", {})
    recent = profile.get("recent_messages", [])

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
    error_text = ""
    if status_code >= 400:
        error_text = _shorten(res.get("detail") or res, 240)

    event = {
        "at": _now_iso(),
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
    }

    user_path = _user_dir(user_key)
    events_path = user_path / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    append_unified_event("harness", event)
    _update_profile(user_key, event)

