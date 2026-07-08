"""
Sprint 4 사이드카 통합 테스트 — N-1 reason path 연결 검증.

검증 항목:
  4-4. ApprovalResponse 모델에 rejection_reason 필드 존재 확인
  4-5. /security/approval/{id}/respond (기존 path) 회귀 없음 확인

참고: OpenClaw agent 라우터(/agent/approval) 기반 테스트는 tool-calling
전환으로 agent 라우터가 제거되면서 함께 삭제되었다. Excel Live 승인 흐름은
tests/test_excel_live_router.py가 검증한다.
"""

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.command_audit import get_command_audit_logger
from office_claw_sidecar.models.approval import ApprovalResponse

client = TestClient(app)
HEADERS = {"Authorization": "Bearer dev-token"}


def test_approval_response_model_has_rejection_reason_field():
    """ApprovalResponse 모델에 rejection_reason 필드가 있어야 한다 (N-1 시그니처 검증)."""
    fields = ApprovalResponse.model_fields
    assert "rejection_reason" in fields, (
        "ApprovalResponse에 rejection_reason 필드가 없음 — ipc.rs body와 불일치"
    )
    resp = ApprovalResponse(approval_id="test-id", approved=False)
    assert resp.rejection_reason is None


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
