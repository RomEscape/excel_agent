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
_MAX_LONG_TEXT = 4000
_MAX_LIST = 12


class Long(str):
    """400자에서 자르면 뜻이 사라지는 문자열.

    LLM 원본 응답이나 통합문서 다이제스트가 여기 해당한다. 계획 JSON이 중간에
    끊기면 "모델이 뭘 뱉었나"를 확인할 수 없어 로그를 남긴 의미가 없다.
    """


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
    routes: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)


_CURRENT: ContextVar[DecisionTurn | None] = ContextVar("decision_turn", default=None)
# 턴을 만든 주체. 사람이 앱에서 친 명령인지, 어느 테스트가 돌린 것인지 구분한다.
_SOURCE: ContextVar[dict[str, Any] | None] = ContextVar("decision_source", default=None)


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def compact(value: Any, *, depth: int = 0) -> Any:
    """로그로 읽을 수 있는 크기까지 값을 줄인다."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = _MAX_LONG_TEXT if isinstance(value, Long) else _MAX_TEXT
        return str(value) if len(value) <= limit else value[:limit] + f"...(+{len(value) - limit}자)"
    # plan_summary를 거친 값이 note()에서 한 번 더 줄어든다. 한도가 얕으면
    # 그 두 번을 지나는 동안 params의 values_2d가 "<list>"로 뭉개진다.
    if depth >= 7:
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


def current_source() -> dict[str, Any]:
    """지금 열려 있는 출처 태그. 턴 밖에서 부르면 빈 dict."""
    return dict(_SOURCE.get() or {})


@contextmanager
def source(**fields: Any):
    """이 블록 안에서 만들어지는 턴에 출처를 붙인다.

    테스트와 벤치마크가 만든 턴이 사람이 친 명령과 같은 파일에 섞이면, 나중에
    "이 실패는 누가 낸 것인가"를 되짚을 수 없다. 태그를 붙여 두면 한 파일에
    쌓아 놓고도 `--source`로 갈라 볼 수 있다.
    """
    merged = {**(_SOURCE.get() or {}), **{k: v for k, v in fields.items() if v is not None}}
    token = _SOURCE.set(merged)
    try:
        yield merged
    finally:
        _SOURCE.reset(token)


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


def route(name: str, why: str = "", **fields: Any) -> None:
    """이 턴이 지나간 갈림길을 순서대로 남긴다.

    `stages`가 "무엇을 알아냈나"라면 `routes`는 "어느 길로 갔나"다. 같은 명령이
    어떤 날은 규칙으로, 어떤 날은 플래너로 처리되는데 이 흔적이 없으면 재현이
    안 된다. 한 줄로 이어 붙이면 그 턴의 경로가 그대로 보인다.

        quick_rule:miss → planner:llm → validate:ok → execute → verify:failed → replan
    """
    turn = _CURRENT.get()
    if turn is None:
        return
    entry: dict[str, Any] = {
        "at": str(name),
        "at_ms": round((time.perf_counter() - turn.started_ts) * 1000, 1),
    }
    if why:
        entry["why"] = compact(why)
    for key, value in fields.items():
        entry[key] = compact(value)
    turn.routes.append(entry)


def route_path(turn: dict[str, Any] | DecisionTurn) -> str:
    """`routes`를 한 줄 경로 문자열로 잇는다."""
    routes = turn.get("routes", []) if isinstance(turn, dict) else turn.routes
    return " → ".join(str(r.get("at", "")) for r in routes if isinstance(r, dict))


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
                "source": compact(_SOURCE.get() or {}),
                "message": turn.message,
                "request": turn.request,
                "elapsed_ms": round((time.perf_counter() - turn.started_ts) * 1000, 1),
                "routes": turn.routes,
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
    ok = getattr(response, "ok", None)
    asked = bool(result.get("ask_follow_up"))
    approval = bool(getattr(response, "approval_required", False))
    turn.outcome = compact(
        {
            "ok": ok,
            "action": getattr(response, "action", ""),
            "reason": getattr(response, "reason", ""),
            "ask_follow_up": asked,
            "follow_up_question": result.get("follow_up_question", ""),
            "approval_required": approval,
            "executed_steps": result.get("executed_steps"),
            "failure_detail": result.get("failure_detail", ""),
            "auto_rollbacks": result.get("auto_rollbacks") or [],
        }
    )
    # 경로가 결론 없이 끝나면 로그만 봐서는 이 턴이 어떻게 됐는지 알 수 없다.
    if approval:
        verdict = "final:approval_required"
    elif asked:
        verdict = "final:asked_back"
    elif ok:
        verdict = "final:ok"
    else:
        verdict = "final:failed"
    route(verdict, why=str(result.get("failure_detail", "") or getattr(response, "reason", "")))
