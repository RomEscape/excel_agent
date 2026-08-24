"""
Sprint 3 사이드카 변경 통합 테스트.

검증 항목:
  작업 1: command_log DB 스키마 확장 (tool_name / session_id / rejection_reason)
    1-1. log() 에 tool_name/session_id 전달 후 get_by_id() 로 반환 확인
    1-2. get_recent() 응답에 3개 필드 포함 확인
    1-3. GET /security/audit 응답에 3개 필드 포함 확인
    1-4. 기존 로그 (필드 null) 와의 backward compat 확인

  작업 3: agent_submit_approval rejection_reason 영속화
    3-1. 거부 시 rejection_reason 전달 → reason_recorded=true
    3-2. 승인 시 rejection_reason 무시 → reason_recorded=false
    3-3. rejection_reason 없는 거부 → reason_recorded=false (하위 호환)
    3-4. DB에 rejection_reason 실제 저장 확인
"""

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.command_audit import get_command_audit_logger

client = TestClient(app)
HEADERS = {"Authorization": "Bearer dev-token"}


# ── 작업 1: DB 스키마 확장 ────────────────────────────────────────────────────

class TestSchemaExtension:
    """command_log 에 tool_name / session_id / rejection_reason 컬럼 추가 검증."""

    def test_log_with_tool_name_and_session_id(self):
        """tool_name, session_id 를 log() 에 전달하면 DB에 저장된다."""
        cmd_audit = get_command_audit_logger()
        row_id = cmd_audit.log(
            grade="SAFE",
            command="print('hello')",
            reason="테스트",
            tool_name="gog.gmail.send",
            session_id="session-abc-123",
        )
        assert row_id > 0

        entry = cmd_audit.get_by_id(row_id)
        assert entry is not None
        assert entry["tool_name"] == "gog.gmail.send", f"tool_name 불일치: {entry['tool_name']}"
        assert entry["session_id"] == "session-abc-123", f"session_id 불일치: {entry['session_id']}"

    def test_log_without_new_fields_defaults_to_none(self):
        """새 필드 없이 log() 호출 시 NULL(None) 으로 저장된다 (기존 하위 호환)."""
        cmd_audit = get_command_audit_logger()
        row_id = cmd_audit.log(
            grade="SAFE",
            command="ls -la",
            reason="하위호환 테스트",
        )
        assert row_id > 0

        entry = cmd_audit.get_by_id(row_id)
        assert entry is not None
        assert entry["tool_name"] is None
        assert entry["session_id"] is None
        assert entry["rejection_reason"] is None

    def test_get_recent_includes_new_fields(self):
        """get_recent() 반환 딕셔너리에 3개 새 필드가 포함된다."""
        cmd_audit = get_command_audit_logger()
        cmd_audit.log(
            grade="CONFIRM",
            command="rm -rf /tmp/test",
            reason="삭제 확인 필요",
            tool_name="gog.shell.exec",
            session_id="session-xyz",
        )
        logs = cmd_audit.get_recent(limit=1)
        assert len(logs) >= 1
        latest = logs[0]
        assert "tool_name" in latest, "tool_name 필드 누락"
        assert "session_id" in latest, "session_id 필드 누락"
        assert "rejection_reason" in latest, "rejection_reason 필드 누락"

    def test_audit_list_endpoint_includes_new_fields(self):
        """GET /security/audit 응답의 logs 배열 항목에 3개 필드가 포함된다."""
        # 먼저 항목 하나 추가
        cmd_audit = get_command_audit_logger()
        cmd_audit.log(
            grade="SAFE",
            command="echo hello",
            reason="API 필드 노출 테스트",
            tool_name="gog.terminal.echo",
            session_id="session-api-test",
        )

        resp = client.get("/security/audit?limit=5", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert len(data["logs"]) >= 1

        entry = data["logs"][0]
        assert "tool_name" in entry, "API 응답에 tool_name 필드 없음"
        assert "session_id" in entry, "API 응답에 session_id 필드 없음"
        assert "rejection_reason" in entry, "API 응답에 rejection_reason 필드 없음"


# ── 작업 3: rejection_reason 영속화 ──────────────────────────────────────────

class TestRejectionReason:
    """CommandAuditLogger.update_approval 의 rejection_reason 영속화 검증.

    예전에는 `/security/approval/{id}/respond` 엔드포인트를 통해 검증했지만,
    그 엔드포인트는 메신저 봇 제거와 함께 사라졌다(유일한 생산자였다).
    저장 로직 자체는 그대로 살아 있으므로 직접 호출로 검증한다.
    """

    def test_update_approval_with_rejection_reason_direct(self):
        """CommandAuditLogger.update_approval() 에 rejection_reason 직접 전달."""
        cmd_audit = get_command_audit_logger()
        row_id = cmd_audit.log(
            grade="CONFIRM",
            command="delete_files('/important')",
            reason="중요 파일 삭제 확인",
        )
        assert row_id > 0

        cmd_audit.update_approval(row_id, approved=False, rejection_reason="삭제 불허")

        entry = cmd_audit.get_by_id(row_id)
        assert entry is not None
        assert entry["approved"] == 0
        assert entry["rejection_reason"] == "삭제 불허"
