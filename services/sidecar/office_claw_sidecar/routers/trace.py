"""프론트(앱)에서 벌어진 일을 같은 chat_log.jsonl에 남기는 입구.

2026-08-19 로그 감사: 라우팅 결정(엑셀 vs 일반 채팅), 붙여넣기 프로브 결과(주소·빈 선택·값
살림), 사용자가 **실제로 본 오류 문구**, 타임아웃·재시도는 전부 브라우저 안에서만 일어나
사이드카 로그에 없었다. "로그는 성공, 화면은 실패"를 로그만으로 보려면 화면 쪽 사건도 같은
파일에 있어야 한다. 판단에는 쓰지 않는다 — 오직 기록.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from office_claw_sidecar.services.decision_trace import (
    Long,
    compact,
    current_turn,
    turn_scope,
)
from office_claw_sidecar.services.decision_trace import (
    route as trace_route,
)

router = APIRouter(prefix="/trace", tags=["trace"])


class ClientEvent(BaseModel):
    kind: str = Field(..., min_length=1, description="route_chat | paste_probe | ui_error | timeout | retry | approval_ui …")
    session_id: str | None = None
    message: str | None = Field(None, description="관련된 사용자 문장(있으면)")
    detail: dict[str, Any] = Field(default_factory=dict)


@router.post("/client-event")
def post_client_event(ev: ClientEvent) -> dict[str, Any]:
    with turn_scope(
        endpoint="client/event",
        message=str(ev.message or f"(client) {ev.kind}"),
        session_id=str(ev.session_id or ""),
        request={"kind": ev.kind, **{k: v for k, v in (ev.detail or {}).items() if k != "error_text"}},
    ):
        turn = current_turn()
        if turn is not None:
            turn.outcome = compact(
                {
                    "ok": ev.kind not in {"ui_error", "timeout"},
                    "action": f"client.{ev.kind}",
                    "error_text": Long(str((ev.detail or {}).get("error_text", "") or "")),
                }
            )
        trace_route(f"client:{ev.kind}", why=str((ev.detail or {}).get("why", "") or ""))
    return {"ok": True}
