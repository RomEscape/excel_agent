"""Cross-platform credential storage using OS-native secure stores.

Backends:
  - Windows: Windows Credential Manager (WinVaultKeyring)
  - macOS:   macOS Keychain (via Security framework)
  - Linux:   Secret Service (GNOME Keyring / KWallet via D-Bus)

The `keyring` library auto-detects the correct backend for the current OS.
"""

import json
import logging
import platform

import keyring
import keyring.errors

from office_claw_sidecar.config import SERVICE_NAMESPACE, get_credentials_registry_path
from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
audit = AuditService()


class KeyringService:
    """Manage credentials via OS keyring with a local key registry."""

    def __init__(self) -> None:
        self._registry_path = get_credentials_registry_path()
        self._check_backend()

    def _check_backend(self) -> None:
        """Log the active keyring backend and warn if insecure."""
        backend = keyring.get_keyring()
        backend_name = type(backend).__name__
        logger.info("Keyring backend: %s", backend_name)

        insecure_keywords = ["PlaintextKeyring", "Null", "Fail"]
        if any(kw.lower() in backend_name.lower() for kw in insecure_keywords):
            logger.warning(
                "INSECURE keyring backend detected: %s. "
                "Credentials will NOT be securely stored. "
                "Install a secret service provider (e.g., gnome-keyring).",
                backend_name,
            )

    def _load_registry(self) -> list[str]:
        """Load the credential key registry (key names only, never values)."""
        if not self._registry_path.exists():
            return []
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            return data.get("keys", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save_registry(self, keys: list[str]) -> None:
        """Save the credential key registry."""
        self._registry_path.write_text(
            json.dumps({"keys": sorted(set(keys))}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def store(self, key: str, value: str) -> None:
        """Store a credential in the OS secure store.

        On Windows, this writes to Windows Credential Manager.
        The credential appears under: Control Panel > Credential Manager
        as 'office_claw/<key>'.

        Note: Windows Credential Manager has a max blob size of ~2560 bytes.
        For larger values (e.g., OAuth refresh tokens), consider splitting
        or compressing the data.
        """
        keyring.set_password(SERVICE_NAMESPACE, key, value)

        # Update key registry
        keys = self._load_registry()
        if key not in keys:
            keys.append(key)
            self._save_registry(keys)

        audit.log("credential_store", key)
        logger.info("Stored credential: %s", key)

    def retrieve(self, key: str) -> str | None:
        """Retrieve a credential from the OS secure store."""
        audit.log("credential_retrieve", key)
        return keyring.get_password(SERVICE_NAMESPACE, key)

    def delete(self, key: str) -> None:
        """Delete a credential from the OS secure store."""
        try:
            keyring.delete_password(SERVICE_NAMESPACE, key)
        except keyring.errors.PasswordDeleteError:
            logger.warning("Credential '%s' not found in keyring", key)

        # Update key registry
        keys = self._load_registry()
        if key in keys:
            keys.remove(key)
            self._save_registry(keys)

        audit.log("credential_delete", key)
        logger.info("Deleted credential: %s", key)

    def list_keys(self) -> list[str]:
        """List all stored credential key names (never values)."""
        return self._load_registry()

    @staticmethod
    def get_backend_info() -> dict:
        """Return info about the current keyring backend for diagnostics."""
        backend = keyring.get_keyring()
        return {
            "backend": type(backend).__name__,
            "platform": platform.system(),
            "secure": "Plaintext" not in type(backend).__name__,
        }
