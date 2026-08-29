"""exec.approval 플로우 관련 Pydantic 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    """CONFIRM 권한 스킬 실행 전 사용자 확인 요청 데이터."""

    approval_id: str
    tool_name: str
    tool_display_name: str   # 한국어 표시명 (예: "Excel 셀 값 수정")
    summary: str             # 한국어 요약 (예: "kim@company.com에게 이메일을 전송합니다")
    args_preview: dict       # 사용자가 검토할 핵심 인자 (민감 데이터 마스킹 적용)
    session_id: str
    created_at: str          # ISO 8601 UTC 타임스탬프


class ApprovalResponse(BaseModel):
    """사용자의 승인/거부 결정."""

    approval_id: str
    approved: bool = Field(..., description="True=승인, False=거부")
    # 거부 시 사용자 입력 사유 — 승인 시에는 무시됨 (N-1 Sprint 4)
    rejection_reason: str | None = None
