"""
command_audit.py — Phase 2 (officeclaw) 명령 감사 로그.

모든 명령 실행/차단 이력을 SQLite DB에 기록한다.
기존 AuditService(audit_service.py)의 JSONL 로그와는 별도로 동작한다.

DB 위치: ~/officeclaw/audit.db
테이블: command_log
  id               INTEGER PRIMARY KEY AUTOINCREMENT
  timestamp        TEXT    NOT NULL  (ISO-8601 UTC)
  grade            TEXT    NOT NULL  (SAFE | CONFIRM | DENIED)
  lang             TEXT              (python | shell)
  command          TEXT    NOT NULL  (코드 원문 앞 500자)
  reason           TEXT    NOT NULL  (한국어 사유)
  pattern          TEXT              (매칭된 패턴)
  approved         INTEGER           (1=승인, 0=거부, NULL=해당없음/대기중)
  user_id          TEXT              (텔레그램 chat_id 등)
  source           TEXT    DEFAULT 'agent'  (telegram|slack|discord|agent|webui)
  tool_name        TEXT              (호출된 스킬 이름, 예: gog.gmail.send)  — Sprint 3
  session_id       TEXT              (OpenClaw 세션 ID)                      — Sprint 3
  rejection_reason TEXT              (거부 시 사용자 입력 사유, 선택)          — Sprint 3
"""

from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from office_claw_sidecar.config import get_app_db_path

logger = logging.getLogger(__name__)

# DB 경로 — config.get_app_db_path()가 단일 출처 (~/officeclaw/audit.db)
_DB_PATH = get_app_db_path()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS command_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    grade            TEXT    NOT NULL,
    lang             TEXT    DEFAULT '',
    command          TEXT    NOT NULL,
    reason           TEXT    NOT NULL,
    pattern          TEXT    DEFAULT '',
    approved         INTEGER,
    user_id          TEXT    DEFAULT '',
    source           TEXT    DEFAULT 'agent',
    tool_name        TEXT,
    session_id       TEXT,
    rejection_reason TEXT
);
"""

_CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_command_log_timestamp
ON command_log (timestamp);
"""

# 허용 source enum 값.
#
# 메신저 봇 기능은 제거됐지만 telegram/slack/discord는 **남긴다** — 이미 쌓인
# 감사 로그 행이 그 값을 갖고 있고, enum을 줄이면 과거 기록의 디바이스 칸이
# 깨진다. 이 값으로 새로 기록하는 곳은 이제 없다.
_VALID_SOURCES = frozenset({"telegram", "slack", "discord", "agent", "webui"})

# 자유 문자열 → enum 정규화 매핑
_SOURCE_ALIAS: dict[str, str] = {
    "telegram_bot": "telegram",
    "telegram_agent": "telegram",
    "slack_bot": "slack",
    "slack_agent": "slack",
    "discord_bot": "discord",
    "discord_agent": "discord",
    "web": "webui",
    "ui": "webui",
    "frontend": "webui",
}


def normalize_source(raw: str) -> str:
    """
    자유 문자열 source 값을 5개 enum 중 하나로 정규화한다.

    - 이미 유효한 enum이면 그대로 반환
    - 알려진 alias이면 매핑값 반환
    - 모를 경우 "agent" 반환 (안전 기본값)
    """
    lowered = (raw or "").lower().strip()
    if lowered in _VALID_SOURCES:
        return lowered
    return _SOURCE_ALIAS.get(lowered, "agent")


def _get_conn() -> sqlite3.Connection:
    """DB 연결을 반환한다. 필요 시 테이블/컬럼을 생성한다."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_IDX)
    # 기존 DB에 없는 컬럼을 무중단으로 추가 (ALTER TABLE은 멱등하지 않으므로 예외 무시)
    _migrate_columns = [
        ("source",           "TEXT DEFAULT 'agent'"),
        ("tool_name",        "TEXT"),
        ("session_id",       "TEXT"),
        ("rejection_reason", "TEXT"),
    ]
    for col_name, col_def in _migrate_columns:
        try:
            conn.execute(f"ALTER TABLE command_log ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # 이미 존재하는 컬럼
    conn.commit()
    return conn


class CommandAuditLogger:
    """
    명령 실행/차단 이력을 SQLite에 기록하고 조회한다.

    스레드 안전성: 각 메서드 호출 시 독립적인 커넥션을 사용한다.
    """

    # ── 쓰기 ─────────────────────────────────────────────────────────────────

    def log(
        self,
        grade: str,
        command: str,
        reason: str,
        lang: str = "",
        pattern: str = "",
        approved: Optional[bool] = None,
        user_id: str = "",
        source: str = "agent",
        tool_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """
        새 감사 항목을 기록하고 삽입된 row의 id를 반환한다.

        Parameters
        ----------
        grade:      SAFE | CONFIRM | DENIED
        command:    코드 원문 (길면 앞 500자만 저장)
        reason:     한국어 사유
        lang:       python | shell | ""
        pattern:    매칭된 패턴 문자열
        approved:   True=승인, False=거부, None=해당없음/대기중
        user_id:    텔레그램 chat_id 등
        source:     telegram | slack | discord | agent | webui (자유 문자열은 정규화됨)
        tool_name:  호출된 스킬 이름 (예: gog.gmail.send) — Sprint 3
        session_id: OpenClaw 세션 ID — Sprint 3
        """
        ts = datetime.now(timezone.utc).isoformat()
        cmd_short = (command or "")[:500]
        approved_int: Optional[int] = None
        if approved is True:
            approved_int = 1
        elif approved is False:
            approved_int = 0
        # 저장 시 enum 정규화 — 기존 자유 문자열은 그대로 두고 새 항목부터만 적용
        normalized_source = normalize_source(source)

        try:
            conn = _get_conn()
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO command_log
                        (timestamp, grade, lang, command, reason, pattern, approved, user_id, source,
                         tool_name, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, grade, lang, cmd_short, reason, pattern, approved_int, user_id, normalized_source,
                     tool_name, session_id),
                )
                return cur.lastrowid or 0
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.log 실패: %s", e)
            return 0

    def update_approval(
        self,
        row_id: int,
        approved: bool,
        rejection_reason: Optional[str] = None,
    ) -> None:
        """
        기존 CONFIRM 항목의 승인/거부 결과를 업데이트한다.

        Parameters
        ----------
        row_id:           command_log.id
        approved:         True=승인, False=거부
        rejection_reason: 거부 시 사용자 입력 사유 (선택, 승인 시 무시됨) — Sprint 3
        """
        approved_int = 1 if approved else 0
        # 승인 시에는 rejection_reason을 저장하지 않는다
        reason_to_save = rejection_reason if not approved else None
        try:
            conn = _get_conn()
            with conn:
                conn.execute(
                    "UPDATE command_log SET approved = ?, rejection_reason = ? WHERE id = ?",
                    (approved_int, reason_to_save, row_id),
                )
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.update_approval 실패: %s", e)

    # ── 조회 ─────────────────────────────────────────────────────────────────

    def get_recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """최근 항목을 최신순으로 반환한다."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT id, timestamp, grade, lang, command, reason, pattern, approved, user_id, source,
                       tool_name, session_id, rejection_reason
                FROM command_log
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.get_recent 실패: %s", e)
            return []

    def get_by_id(self, row_id: int) -> Optional[dict]:
        """특정 항목을 id로 조회한다."""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT * FROM command_log WHERE id = ?",
                (row_id,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.get_by_id 실패: %s", e)
            return None

    def get_stats(self) -> dict:
        """
        등급별 집계를 반환한다.

        Returns::

            {
                "total": 42,
                "safe": 30,
                "confirm": 8,
                "denied": 4,
                "confirm_approved": 5,
                "confirm_rejected": 3,
                "confirm_pending": 2,
            }
        """
        try:
            conn = _get_conn()
            rows = conn.execute(
                """
                SELECT
                    COUNT(*)                                                   AS total,
                    SUM(CASE WHEN grade='SAFE'    THEN 1 END)                 AS safe,
                    SUM(CASE WHEN grade='CONFIRM' THEN 1 END)                 AS confirm,
                    SUM(CASE WHEN grade='DENIED'  THEN 1 END)                 AS denied,
                    SUM(CASE WHEN grade='CONFIRM' AND approved=1 THEN 1 END)  AS confirm_approved,
                    SUM(CASE WHEN grade='CONFIRM' AND approved=0 THEN 1 END)  AS confirm_rejected,
                    SUM(CASE WHEN grade='CONFIRM' AND approved IS NULL THEN 1 END) AS confirm_pending_db
                FROM command_log
                """
            ).fetchone()
            if not rows:
                return {}
            return {
                "total": rows["total"] or 0,
                "safe": rows["safe"] or 0,
                "confirm": rows["confirm"] or 0,
                "denied": rows["denied"] or 0,
                "confirm_approved": rows["confirm_approved"] or 0,
                "confirm_rejected": rows["confirm_rejected"] or 0,
                "confirm_pending": rows["confirm_pending_db"] or 0,
            }
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.get_stats 실패: %s", e)
            return {}

    def clear_all(self) -> int:
        """모든 로그를 삭제하고 삭제된 행 수를 반환한다."""
        try:
            conn = _get_conn()
            with conn:
                cur = conn.execute("DELETE FROM command_log")
                return cur.rowcount
        except sqlite3.Error as e:
            logger.error("CommandAuditLogger.clear_all 실패: %s", e)
            return 0


# ── 싱글턴 ────────────────────────────────────────────────────────────────────

_logger_instance: CommandAuditLogger | None = None


def get_command_audit_logger() -> CommandAuditLogger:
    """전역 CommandAuditLogger 인스턴스를 반환한다 (싱글턴)."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = CommandAuditLogger()
    return _logger_instance
