"""대화 한 턴의 판단 과정을 통째로 남기는 추적 로그.

"무슨 질문이 들어왔고, 무엇으로 알아들었고, 어떤 계획을 세웠고, 실제로 무엇을 실행했는가"를
파일 하나로 확인할 수 있어야 한다. `logs/chat_log.jsonl`에 턴당 JSON 한 줄을 쌓는다.

턴 단위로 묶기 때문에 요청·판단·실행·응답이 흩어지지 않는다. 라우터가 어디서 반환하든
`turn_scope()`를 빠져나가는 순간 한 줄이 완성된다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from office_claw_sidecar.config import get_chat_log_path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
KST = timezone(timedelta(hours=9), name="KST")

# 셀 값 수천 개가 통째로 들어오면 로그가 읽을 수 없게 된다.
_MAX_TEXT = 400
_MAX_LIST = 12


@dataclass
class DecisionTurn:
    """한 번의 사용자 발화가 응답으로 끝나기까지의 기록."""

    turn_id: str
    endpoint: str
    message: str
    session_id: str
    started_at: str
    started_ts: float
    request: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)


_CURRENT: ContextVar[DecisionTurn | None] = ContextVar("decision_turn", default=None)


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def compact(value: Any, *, depth: int = 0) -> Any:
    """로그로 읽을 수 있는 크기까지 값을 줄인다."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + f"...(+{len(value) - _MAX_TEXT}자)"
    if depth >= 4:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 40:
                out["..."] = f"(+{len(value) - 40}개 키)"
                break
            out[str(key)] = compact(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [compact(v, depth=depth + 1) for v in list(value)[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            items.append(f"...(+{len(value) - _MAX_LIST}개)")
        return items
    return compact(str(value), depth=depth + 1)


def current_turn() -> DecisionTurn | None:
    return _CURRENT.get()


def note(stage: str, **fields: Any) -> None:
    """진행 중인 턴에 판단 근거 한 조각을 붙인다. 턴 밖에서 부르면 조용히 무시한다."""
    turn = _CURRENT.get()
    if turn is None:
        return
    entry: dict[str, Any] = {
        "stage": str(stage),
        "at_ms": round((time.perf_counter() - turn.started_ts) * 1000, 1),
    }
    for key, value in fields.items():
        entry[key] = compact(value)
    turn.stages.append(entry)


def plan_summary(steps: Any) -> list[dict[str, Any]]:
    """계획을 '액션 + 핵심 파라미터'로 줄여 로그에 남기기 좋게 만든다."""
    out: list[dict[str, Any]] = []
    if not isinstance(steps, (list, tuple)):
        return out
    for raw in list(steps)[:20]:
        if isinstance(raw, dict):
            action = str(raw.get("action", ""))
            params = raw.get("params", {})
            reason = str(raw.get("reason", ""))
        else:
            action = str(getattr(raw, "action", ""))
            params = getattr(raw, "params", {})
            reason = str(getattr(raw, "reason", ""))
        row: dict[str, Any] = {"action": action}
        if isinstance(params, dict) and params:
            row["params"] = compact(params)
        if reason:
            row["reason"] = compact(reason)
        out.append(row)
    return out


def _write(entry: dict[str, Any]) -> None:
    try:
        raw = json.dumps(entry, ensure_ascii=False, default=str)
        path = get_chat_log_path()
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(raw + "\n")
    except Exception as exc:
        logger.warning("대화 추적 로그 기록 실패(무시): %s", exc)


@contextmanager
def turn_scope(
    *,
    endpoint: str,
    message: str,
    session_id: str = "",
    request: dict[str, Any] | None = None,
):
    """턴 하나를 열고, 빠져나갈 때 chat_log.jsonl에 한 줄로 확정한다."""
    turn = DecisionTurn(
        turn_id=uuid.uuid4().hex[:12],
        endpoint=str(endpoint),
        message=str(message or ""),
        session_id=str(session_id or ""),
        started_at=_now_iso(),
        started_ts=time.perf_counter(),
        request=compact(request or {}),
    )
    token = _CURRENT.set(turn)
    try:
        yield turn
    except Exception as exc:
        turn.outcome = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": compact(str(exc)),
        }
        raise
    finally:
        _CURRENT.reset(token)
        _write(
            {
                "turn_id": turn.turn_id,
                "at": turn.started_at,
                "endpoint": turn.endpoint,
                "session_id": turn.session_id,
                "message": turn.message,
                "request": turn.request,
                "elapsed_ms": round((time.perf_counter() - turn.started_ts) * 1000, 1),
                "stages": turn.stages,
                "outcome": turn.outcome,
            }
        )


def set_outcome_from_response(response: Any) -> None:
    """응답 객체에서 사용자에게 실제로 나간 내용을 뽑아 턴 결론으로 남긴다."""
    turn = _CURRENT.get()
    if turn is None:
        return
    result = getattr(response, "result", None)
    result = result if isinstance(result, dict) else {}
    turn.outcome = compact(
        {
            "ok": getattr(response, "ok", None),
            "action": getattr(response, "action", ""),
            "reason": getattr(response, "reason", ""),
            "ask_follow_up": bool(result.get("ask_follow_up")),
            "follow_up_question": result.get("follow_up_question", ""),
            "approval_required": bool(getattr(response, "approval_required", False)),
            "executed_steps": result.get("executed_steps"),
            "failure_detail": result.get("failure_detail", ""),
        }
    )
