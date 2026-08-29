"""Pydantic models for credential operations."""

from pydantic import BaseModel


class CredentialStore(BaseModel):
    key: str
    value: str


class CredentialResponse(BaseModel):
    success: bool
    message: str
