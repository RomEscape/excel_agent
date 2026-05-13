"""Pydantic models for audit log entries."""

from pydantic import BaseModel


class AuditEntry(BaseModel):
    timestamp: str
    action: str
    target: str
    detail: str = ""
