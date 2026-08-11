"""
chat_history.py — Sprint 5 채팅 세션 영속화.

채팅 메시지를 ~/PrivateClaw/audit.db 에 저장한다.
기존 command_log 테이블과 같은 DB를 공유하되, 별도 테이블(chat_messages)을 사용.

테이블 스키마:
  chat_messages
    id             INTEGER PRIMARY KEY AUTOINCREMENT
    session_id     TEXT NOT NULL
    role           TEXT NOT NULL   -- user|agent|system
    text           TEXT
    tool_calls     TEXT            -- JSON 문자열
    masked_count   INTEGER DEFAULT 0
    masked_types   TEXT            -- JSON array 문자열
    error_text     TEXT
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from office_claw_sidecar.services.unified_log_service import append_unified_event

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / "PrivateClaw" / "audit.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT,
    tool_calls   TEXT,
    masked_count INTEGER DEFAULT 0,
    masked_types TEXT,
    error_text   TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_session
ON chat_messages (session_id, created_at);
"""

_ALLOWED_ROLES = {"user", "agent", "system"}


def _get_conn() -> sqlite3.Connection:
    """DB 연결 및 테이블 초기화 (없을 경우 자동 생성)."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_IDX)
    conn.commit()
    return conn


def save_message(
    session_id: str,
    role: str,
    text: str | None = None,
    tool_calls: object | None = None,
    masked_count: int = 0,
    masked_types: list | None = None,
    error_text: str | None = None,
) -> int:
    """메시지를 DB에 저장하고 생성된 id를 반환한다."""
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"유효하지 않은 role: {role}. 허용값: {_ALLOWED_ROLES}")

    tool_calls_str = json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
    masked_types_str = json.dumps(masked_types, ensure_ascii=False) if masked_types is not None else None

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO chat_messages
                (session_id, role, text, tool_calls, masked_count, masked_types, error_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, text, tool_calls_str, masked_count, masked_types_str, error_text),
        )
        conn.commit()
        row_id = int(cur.lastrowid or 0)
        append_unified_event(
            "chat_message",
            {
                "id": row_id,
                "session_id": session_id,
                "role": role,
                "text": text or "",
                "tool_calls": tool_calls if isinstance(tool_calls, list) else None,
                "masked_count": int(masked_count or 0),
                "masked_types": masked_types if isinstance(masked_types, list) else [],
                "error_text": error_text or "",
            },
        )
        return row_id
    finally:
        conn.close()


def list_sessions(limit: int = 20) -> list[dict]:
    """세션 목록을 최근 활동순으로 반환한다.

    각 항목: {session_id, last_message_at, message_count, preview}
    preview는 마지막 user 메시지 앞 60자.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                session_id,
                MAX(created_at)  AS last_message_at,
                COUNT(*)         AS message_count
            FROM chat_messages
            GROUP BY session_id
            ORDER BY last_message_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        result = []
        for row in rows:
            # 마지막 user 메시지 미리보기
            preview_row = conn.execute(
                """
                SELECT text FROM chat_messages
                WHERE session_id = ? AND role = 'user' AND text IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (row["session_id"],),
            ).fetchone()
            preview = ""
            if preview_row and preview_row["text"]:
                preview = preview_row["text"][:60]

            result.append({
                "session_id": row["session_id"],
                "last_message_at": row["last_message_at"],
                "message_count": row["message_count"],
                "preview": preview,
            })
        return result
    finally:
        conn.close()


def get_messages(session_id: str) -> list[dict]:
    """세션의 전체 메시지를 시간순으로 반환한다."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, role, text, tool_calls, masked_count, masked_types, error_text, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()

        result = []
        for row in rows:
            tool_calls = None
            if row["tool_calls"]:
                try:
                    tool_calls = json.loads(row["tool_calls"])
                except json.JSONDecodeError:
                    tool_calls = row["tool_calls"]

            masked_types = None
            if row["masked_types"]:
                try:
                    masked_types = json.loads(row["masked_types"])
                except json.JSONDecodeError:
                    masked_types = []

            result.append({
                "id": row["id"],
                "role": row["role"],
                "text": row["text"],
                "tool_calls": tool_calls,
                "masked_count": row["masked_count"],
                "masked_types": masked_types,
                "error_text": row["error_text"],
                "created_at": row["created_at"],
            })
        return result
    finally:
        conn.close()


def delete_session(session_id: str) -> int:
    """세션의 모든 메시지를 삭제하고 삭제된 행 수를 반환한다."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM chat_messages WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
