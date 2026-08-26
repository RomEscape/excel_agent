"""
Sprint 4 사이드카 통합 테스트 — N-1 reason path 연결 검증.

검증 항목:
  4-4. ApprovalResponse 모델에 rejection_reason 필드 존재 확인

참고: OpenClaw agent 라우터(/agent/approval) 기반 테스트는 tool-calling
전환으로 agent 라우터가 제거되면서 함께 삭제됐고, `/security/approval/{id}/respond`
회귀 테스트는 메신저 봇 제거로 그 엔드포인트가 사라지면서 함께 걷어냈다.
Excel Live 승인 흐름은 tests/test_excel_live_router.py가 검증한다.
"""

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
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
