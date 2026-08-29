"""Audit log viewing endpoint."""

from fastapi import APIRouter

from office_claw_sidecar.services.audit_service import AuditService

router = APIRouter()
audit_svc = AuditService()


@router.get("/logs")
async def get_audit_logs(limit: int = 100):
    """Get recent audit log entries."""
    logs = audit_svc.get_logs(limit)
    return {"logs": logs}
