"""통합 이벤트 기록 — `chat_log.jsonl` 의 `record: event` 줄.

예전에는 감사·채팅·명령 감사·하네스 이력을 `all_events.jsonl` 에 따로 쌓았다.
2026-09-10 부터 런타임 기록은 `logs/chat_log.jsonl` 하나뿐이라(사용자 지시: "모든
작업을 다 chat_log 에만"), 이벤트는 같은 파일에 아래 한 줄로 들어간다.

    {"record": "event", "at": <KST ISO>, "event_type": "...", "payload": {...}}

호출처(`chat_history` · `command_audit` · `audit_service` · `user_harness_service`)는
그대로 `append_unified_event(event_type, payload)` 를 부른다 — 시그니처는 바뀌지 않았다.
턴 줄과 가르는 기준은 `turn_id` 유무다. `decision_trace.iter_turns()` 는 이 줄을
건너뛰고, 이벤트만 읽으려면 `decision_trace.iter_records(record="event")` 를 쓴다.
"""

from __future__ import annotations

from typing import Any

from office_claw_sidecar.services import decision_trace

RECORD_EVENT = "event"


def append_unified_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """
    이벤트 1건을 chat_log 에 `record: event` 줄로 append 한다.

    Parameters
    ----------
    event_type:
        이벤트 분류 (예: audit, chat_message, command_audit, harness)
    payload:
        이벤트 상세 데이터(JSON 직렬화 가능 dict 권장)

    기록 실패는 `decision_trace` 가 삼키고 `write_failures` 만 올린다 — 로그 때문에
    사용자 요청을 실패시키지 않는다.
    """
    decision_trace.append_record(
        RECORD_EVENT,
        {
            "event_type": str(event_type or "unknown"),
            "payload": payload if isinstance(payload, dict) else {},
        },
    )
