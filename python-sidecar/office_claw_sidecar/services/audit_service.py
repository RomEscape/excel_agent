"""Append-only local audit logging service.

All data access events are recorded to a JSONL file for transparency.
Storage locations:
  - Windows: %LOCALAPPDATA%/office_claw/audit.jsonl
  - macOS:   ~/Library/Application Support/office_claw/audit.jsonl
  - Linux:   ~/.local/share/office_claw/audit.jsonl
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from office_claw_sidecar.config import get_audit_log_path

logger = logging.getLogger(__name__)


class AuditService:
    """Append-only audit log for all data access events."""

    def __init__(self) -> None:
        self._log_path = get_audit_log_path()

    def log(self, action: str, target: str, detail: str = "") -> None:
        """Append an audit entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "target": target,
            "detail": detail,
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)

    def get_logs(self, limit: int = 100) -> list[dict]:
        """Read the most recent audit log entries."""
        if not self._log_path.exists():
            return []

        entries = []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.error("Failed to read audit log: %s", e)
            return []

        # Return most recent entries first
        return list(reversed(entries[-limit:]))

    def get_all_logs(self) -> list[dict]:
        """감사 로그 전체를 시간 순(오래된 것부터)으로 반환한다."""
        if not self._log_path.exists():
            return []
        entries: list[dict] = []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.error("Failed to read audit log: %s", e)
        return entries

    def get_masking_stats(self) -> dict:
        """
        마스킹 통계를 집계한다.

        Returns:
            {
                "today": {"주민등록번호": 3, "이메일 주소": 7, ...},
                "week": {...},
                "total": {...},
            }
        """
        all_logs = self.get_all_logs()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)

        def _parse_ts(ts: str) -> datetime | None:
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return None

        today_counts: dict[str, int] = defaultdict(int)
        week_counts: dict[str, int] = defaultdict(int)
        total_counts: dict[str, int] = defaultdict(int)

        for entry in all_logs:
            if entry.get("action") != "masking.applied":
                continue
            detail = entry.get("detail", "")
            # detail 형식: "N건 마스킹: 유형1, 유형2"
            # 유형 부분만 추출하여 카운트
            if "마스킹:" in detail:
                try:
                    count_part, types_part = detail.split("마스킹:", 1)
                    int(count_part.strip().replace("건", ""))
                    types = [t.strip() for t in types_part.split(",")]
                except (ValueError, IndexError):
                    continue
            else:
                continue

            ts = _parse_ts(entry.get("timestamp", ""))
            for t in types:
                total_counts[t] += 1
                if ts:
                    if ts >= week_start:
                        week_counts[t] += 1
                    if ts >= today_start:
                        today_counts[t] += 1

        return {
            "today": dict(today_counts),
            "week": dict(week_counts),
            "total": dict(total_counts),
        }

    def get_blocked_log(self, limit: int = 50) -> list[dict]:
        """
        보안 차단/승인 거부 이벤트 목록을 최신순으로 반환한다.

        대상 액션:
          - agent.chat.denied (DENIED 키워드 차단)
          - approval.rejected (사용자가 거부)
          - approval.auto_rejected (DENIED 권한 자동 거부)
        """
        _BLOCK_ACTIONS = frozenset({
            "agent.chat.denied",
            "approval.rejected",
            "approval.auto_rejected",
        })
        all_logs = self.get_all_logs()
        blocked = [e for e in all_logs if e.get("action") in _BLOCK_ACTIONS]
        return list(reversed(blocked[-limit:]))

    def get_last_blocked_at(self) -> str | None:
        """
        가장 최근 차단 이벤트의 ISO timestamp를 반환한다. 없으면 None.

        차단 액션: agent.chat.denied, approval.rejected, approval.auto_rejected
        """
        _BLOCK_ACTIONS = frozenset({
            "agent.chat.denied",
            "approval.rejected",
            "approval.auto_rejected",
        })
        all_logs = self.get_all_logs()
        # 시간 역순으로 스캔 — 가장 최근 차단 이벤트를 찾음
        for entry in reversed(all_logs):
            if entry.get("action") in _BLOCK_ACTIONS:
                return entry.get("timestamp") or None
        return None

    def get_last_approval_at(self) -> str | None:
        """
        가장 최근 승인 처리(승인 또는 거부 응답 완료) 이벤트의 ISO timestamp를 반환한다.
        없으면 None.

        승인 처리 액션: ui_approval.승인, ui_approval.거부, approval.approved, approval.rejected
        """
        _APPROVAL_ACTIONS = frozenset({
            "ui_approval.승인",
            "ui_approval.거부",
            "approval.approved",
            "approval.rejected",
            "approval.auto_rejected",
        })
        all_logs = self.get_all_logs()
        for entry in reversed(all_logs):
            if entry.get("action") in _APPROVAL_ACTIONS:
                return entry.get("timestamp") or None
        return None
