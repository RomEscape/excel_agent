"""통합 로그(JSONL) 적재 서비스.

여러 경로에 흩어진 이력을 한 파일에서 확인할 수 있도록
`all_events.jsonl`에 append-only로 기록한다.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from office_claw_sidecar.config import get_unified_log_path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
KST = timezone(timedelta(hours=9), name="KST")


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def append_unified_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """
    통합 로그에 이벤트 1건을 append한다.

    Parameters
    ----------
    event_type:
        이벤트 분류 (예: audit, chat_message, command_audit, harness)
    payload:
        이벤트 상세 데이터(JSON 직렬화 가능 dict 권장)
    """
    entry = {
        "at": _now_iso(),
        "event_type": str(event_type or "unknown"),
        "payload": payload if isinstance(payload, dict) else {},
    }
    path = get_unified_log_path()
    try:
        raw = json.dumps(entry, ensure_ascii=False, default=str)
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(raw + "\n")
    except Exception as exc:
        logger.warning("통합 로그 기록 실패(무시): %s", exc)

