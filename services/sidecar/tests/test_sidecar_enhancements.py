"""
Sprint 2 사이드카 보강 통합 테스트.

검증 항목:
  1. confirm_pending — command_audit_stats 응답 필드 존재
  2. source enum 정규화 — normalize_source 함수 + log() 저장 후 get_recent() 반환값
  3. last_blocked_at / last_approval_at — security_stats 응답 구조

메신저(telegram/slack/discord) 관련 항목은 봇 기능 제거와 함께 걷어냈다.
source enum 정규화는 남는다 — 이미 쌓인 감사 로그가 그 값을 갖고 있다.
"""

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.command_audit import get_command_audit_logger, normalize_source
from office_claw_sidecar.main import app

client = TestClient(app)

HEADERS = {"Authorization": "Bearer dev-token"}


# ── 1. confirm_pending ───────────────────────────────────────────────────────

class TestConfirmPending:
    def test_audit_stats_has_confirm_pending(self):
        resp = client.get("/security/audit/stats", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "confirm_pending" in data, "confirm_pending 필드가 없음"
        assert isinstance(data["confirm_pending"], int)
        assert data["confirm_pending"] >= 0

    def test_confirm_pending_non_negative(self):
        """confirm_pending은 항상 0 이상이어야 한다."""
        cmd_audit = get_command_audit_logger()
        stats = cmd_audit.get_stats()
        assert stats.get("confirm_pending", 0) >= 0


# ── 2. source enum 정규화 ─────────────────────────────────────────────────────

class TestSourceEnumNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("telegram", "telegram"),
        ("telegram_bot", "telegram"),
        ("telegram_agent", "telegram"),
        ("slack", "slack"),
        ("slack_bot", "slack"),
        ("discord", "discord"),
        ("discord_bot", "discord"),
        ("agent", "agent"),
        ("webui", "webui"),
        ("web", "webui"),
        ("ui", "webui"),
        ("frontend", "webui"),
        ("unknown_random", "agent"),  # 알 수 없는 값 → 기본값 agent
        ("", "agent"),
    ])
    def test_normalize_source(self, raw, expected):
        assert normalize_source(raw) == expected

    def test_log_saves_normalized_source(self):
        """log() 저장 시 source가 정규화되어 DB에 저장되는지 검증."""
        cmd_audit = get_command_audit_logger()
        row_id = cmd_audit.log(
            grade="SAFE",
            command="print('test')",
            reason="테스트",
            source="telegram_bot",  # alias → "telegram" 으로 정규화
        )
        assert row_id > 0

        entry = cmd_audit.get_by_id(row_id)
        assert entry is not None
        assert entry["source"] == "telegram", (
            f"source가 정규화되지 않음: {entry['source']}"
        )

    def test_get_recent_includes_source(self):
        """get_recent() 반환값에 source 필드가 포함되는지 검증."""
        cmd_audit = get_command_audit_logger()
        cmd_audit.log(
            grade="SAFE",
            command="ls",
            reason="테스트",
            source="webui",
        )
        logs = cmd_audit.get_recent(limit=1)
        assert len(logs) >= 1
        assert "source" in logs[0]


# ── 3. last_blocked_at / last_approval_at ─────────────────────────────────────

class TestSecurityTimestamps:
    def test_security_stats_has_timestamp_fields(self):
        resp = client.get("/security/stats", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "last_blocked_at" in data, "last_blocked_at 필드 없음"
        assert "last_approval_at" in data, "last_approval_at 필드 없음"

    def test_timestamp_fields_are_null_or_iso_string(self):
        """타임스탬프는 None 또는 ISO-8601 형식 문자열이어야 한다."""
        from datetime import datetime
        resp = client.get("/security/stats", headers=HEADERS)
        data = resp.json()

        for field in ("last_blocked_at", "last_approval_at"):
            val = data[field]
            if val is not None:
                # ISO-8601 파싱 가능 여부 확인
                datetime.fromisoformat(val)
