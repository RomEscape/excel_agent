"""Credential management endpoints using OS keyring."""

from fastapi import APIRouter, HTTPException

from office_claw_sidecar.models.credential import CredentialResponse, CredentialStore
from office_claw_sidecar.services.keyring_service import KeyringService

router = APIRouter()
keyring_svc = KeyringService()


@router.post("", response_model=CredentialResponse)
async def store_credential(payload: CredentialStore):
    """Store a credential in the OS secure store."""
    try:
        keyring_svc.store(payload.key, payload.value)
        return CredentialResponse(success=True, message=f"Credential '{payload.key}' stored")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{key}")
async def get_credential(key: str):
    """Retrieve a credential value."""
    value = keyring_svc.retrieve(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Credential '{key}' not found")
    return {"key": key, "value": value}


@router.delete("/{key}", response_model=CredentialResponse)
async def delete_credential(key: str):
    """Delete a credential from the OS secure store."""
    try:
        keyring_svc.delete(key)
        return CredentialResponse(success=True, message=f"Credential '{key}' deleted")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_credentials():
    """List all stored credential keys (not values)."""
    keys = keyring_svc.list_keys()
    return {"keys": keys}
