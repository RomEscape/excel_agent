"""
Sprint 4 사이드카 통합 테스트 — N-1 reason path 연결 검증.

검증 항목:
  N-1: /agent/approval 엔드포인트가 rejection_reason을 수신하고 command_log에 영속화
    4-1. 거부 + rejection_reason → reason_recorded=True, DB 저장 확인 (/agent/approval)
    4-2. 승인 + rejection_reason → reason_recorded=False (승인 시 무시)
    4-3. 거부 rejection_reason 없음 → reason_recorded=False (하위 호환)
    4-4. ApprovalResponse 모델에 rejection_reason 필드 존재 확인
    4-5. /security/approval/{id}/respond (기존 path) 회귀 없음 확인
"""

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.command_audit import get_command_audit_logger
from office_claw_sidecar.models.approval import ApprovalResponse

client = TestClient(app)
HEADERS = {"Authorization": "Bearer dev-token"}


# ── N-1: /agent/approval rejection_reason 영속화 ─────────────────────────────

class TestAgentApprovalReasonPath:
    """agent/approval 엔드포인트의 rejection_reason 영속화 검증 (N-1)."""

    def _register_pending_approval(self, tool_name: str = "gog.gmail.send", session_id: str = "session-n1-test") -> str:
        """
        agent.py의 _pending_approvals 딕셔너리에 직접 ApprovalRequest를 삽입한다.
        실제 OpenClaw 연결 없이 /agent/approval을 테스트하기 위한 헬퍼.
        """
        from office_claw_sidecar.routers.agent import _pending_approvals
        from office_claw_sidecar.models.approval import ApprovalRequest
        from datetime import datetime, timezone
        import uuid

        approval_id = str(uuid.uuid4())
        _pending_approvals[approval_id] = ApprovalRequest(
            approval_id=approval_id,
            tool_name=tool_name,
            tool_display_name="Gmail 이메일 전송",
            summary="boss@company.com에게 이메일을 전송합니다",
            args_preview={"to": "boss@company.com", "subject": "테스트"},
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return approval_id

    def _seed_command_log(self, tool_name: str, session_id: str) -> int:
        """command_log에 CONFIRM 항목을 삽입하고 audit_id를 반환한다."""
        cmd_audit = get_command_audit_logger()
        return cmd_audit.log(
            grade="CONFIRM",
            command="send_mail(to='boss@company.com')",
            reason="민감 작업 확인 필요",
            tool_name=tool_name,
            session_id=session_id,
        )

    def test_rejection_with_reason_records_in_db(self):
        """거부 + rejection_reason → reason_recorded=True, command_log에 저장."""
        tool_name = "gog.gmail.send"
        session_id = "session-n1-reject-with-reason"

        # command_log 시딩
        audit_id = self._seed_command_log(tool_name, session_id)
        approval_id = self._register_pending_approval(tool_name, session_id)

        resp = client.post(
            "/agent/approval",
            json={
                "approval_id": approval_id,
                "approved": False,
                "rejection_reason": "보안 정책 위반 — N-1 테스트",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, f"예상 200, 실제: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["ok"] is True
        assert data["approved"] is False
        assert data["reason_recorded"] is True, f"reason_recorded가 True여야 함: {data}"

        # DB 직접 확인
        cmd_audit = get_command_audit_logger()
        entry = cmd_audit.get_by_id(audit_id)
        assert entry is not None
        assert entry["rejection_reason"] == "보안 정책 위반 — N-1 테스트", (
            f"DB rejection_reason 불일치: {entry['rejection_reason']}"
        )

    def test_approval_ignores_rejection_reason(self):
        """승인 시 rejection_reason을 전달해도 저장하지 않는다."""
        tool_name = "gog.sheets.write"
        session_id = "session-n1-approve-ignore-reason"

        audit_id = self._seed_command_log(tool_name, session_id)
        approval_id = self._register_pending_approval(tool_name, session_id)

        resp = client.post(
            "/agent/approval",
            json={
                "approval_id": approval_id,
                "approved": True,
                "rejection_reason": "무시돼야 하는 사유",
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["approved"] is True
        assert data["reason_recorded"] is False, "승인 시 reason_recorded는 False여야 함"

        # DB: rejection_reason은 NULL이어야 함
        cmd_audit = get_command_audit_logger()
        entry = cmd_audit.get_by_id(audit_id)
        assert entry is not None
        assert entry["rejection_reason"] is None, (
            f"승인 시 rejection_reason은 NULL이어야 함: {entry['rejection_reason']}"
        )

    def test_rejection_without_reason_backward_compat(self):
        """rejection_reason 없는 거부 → 하위 호환, reason_recorded=False."""
        tool_name = "gog.gmail.send"
        session_id = "session-n1-reject-no-reason"

        self._seed_command_log(tool_name, session_id)
        approval_id = self._register_pending_approval(tool_name, session_id)

        resp = client.post(
            "/agent/approval",
            json={"approval_id": approval_id, "approved": False},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["approved"] is False
        assert data["reason_recorded"] is False

    def test_approval_response_model_has_rejection_reason_field(self):
        """ApprovalResponse 모델에 rejection_reason 필드가 있어야 한다 (N-1 시그니처 검증)."""
        # pydantic 모델 필드 확인
        fields = ApprovalResponse.model_fields
        assert "rejection_reason" in fields, (
            "ApprovalResponse에 rejection_reason 필드가 없음 — ipc.rs body와 불일치"
        )
        # 기본값 None 확인
        resp = ApprovalResponse(approval_id="test-id", approved=False)
        assert resp.rejection_reason is None

    def test_reason_recorded_always_in_response(self):
        """응답 구조에 reason_recorded 필드가 항상 포함된다."""
        approval_id = self._register_pending_approval()

        resp = client.post(
            "/agent/approval",
            json={"approval_id": approval_id, "approved": True},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert "reason_recorded" in resp.json(), "reason_recorded 필드가 응답에 없음"


# ── N-1 회귀: 기존 /security/approval/{id}/respond path 동작 보존 ─────────────

class TestSecurityApprovalRegressionN1:
    """/security/approval/{id}/respond가 Sprint 3 변경 후에도 정상 동작한다."""

    def _create_security_pending(self, audit_id: int) -> str:
        """보안 큐에 승인 대기 항목을 생성하고 approval_id를 반환한다."""
        resp = client.post(
            "/security/approval",
            params={"command": "rm -rf /tmp/test", "reason": "테스트 회귀", "audit_id": audit_id},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        return resp.json()["approval_id"]

    def test_security_approval_respond_still_works(self):
        """기존 /security/approval/{id}/respond 엔드포인트 회귀 없음."""
        cmd_audit = get_command_audit_logger()
        audit_id = cmd_audit.log(
            grade="CONFIRM",
            command="rm -rf /tmp/test",
            reason="회귀 테스트",
            tool_name="gog.shell.exec",
        )
        approval_id = self._create_security_pending(audit_id)

        resp = client.post(
            f"/security/approval/{approval_id}/respond",
            json={"approved": False, "rejection_reason": "Sprint 4 회귀 확인"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["reason_recorded"] is True

        # DB 확인
        entry = cmd_audit.get_by_id(audit_id)
        assert entry is not None
        assert entry["rejection_reason"] == "Sprint 4 회귀 확인"
