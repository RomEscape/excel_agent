"""Gmail OAuth 2.0 and mail fetching service.

OAuth flow uses a local loopback address (http://localhost:<port>/oauth/callback)
so the user's browser handles authentication directly with Google — no external
server involved.

Required setup:
1. Create a project in Google Cloud Console
2. Enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Store client_id and client_secret via the credentials manager
"""

import base64
import json
import logging
import webbrowser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from urllib.parse import urlparse, parse_qs

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.keyring_service import KeyringService
from office_claw_sidecar.config import get_data_dir

logger = logging.getLogger(__name__)
audit = AuditService()
keyring_svc = KeyringService()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
KEYRING_TOKEN_KEY = "google_oauth_token"
KEYRING_CLIENT_ID_KEY = "google_client_id"
KEYRING_CLIENT_SECRET_KEY = "google_client_secret"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    auth_code: str | None = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]

        if code:
            OAuthCallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Office Claw</h2>"
                b"<p>Google account connected. You can close this tab.</p>"
                b"</body></html>"
            )
        else:
            error = query.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Error</h2><p>{error}</p></body></html>".encode()
            )

    def log_message(self, format, *args):
        pass  # Suppress default logging


class GmailService:
    """Gmail integration via OAuth 2.0 with local loopback flow."""

    def _get_client_config(self) -> tuple[str, str]:
        """Retrieve OAuth client ID and secret from keyring."""
        client_id = keyring_svc.retrieve(KEYRING_CLIENT_ID_KEY)
        client_secret = keyring_svc.retrieve(KEYRING_CLIENT_SECRET_KEY)
        if not client_id or not client_secret:
            raise ValueError(
                "Google OAuth credentials not configured. "
                "Store 'google_client_id' and 'google_client_secret' "
                "via the credentials manager."
            )
        return client_id, client_secret

    def _load_token(self) -> Credentials | None:
        """Load OAuth token from keyring."""
        token_json = keyring_svc.retrieve(KEYRING_TOKEN_KEY)
        if not token_json:
            return None
        try:
            token_data = json.loads(token_json)
            return Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            logger.warning("Failed to load token: %s", e)
            return None

    def _save_token(self, creds: Credentials) -> None:
        """Save OAuth token to keyring."""
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or SCOPES),
        }
        keyring_svc.store(KEYRING_TOKEN_KEY, json.dumps(token_data))

    def get_credentials(self) -> Credentials | None:
        """Get valid credentials, refreshing if needed."""
        creds = self._load_token()
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                self._save_token(creds)
                audit.log("gmail_token_refresh", "success")
                return creds
            except Exception as e:
                logger.warning("Token refresh failed: %s", e)
                audit.log("gmail_token_refresh", "failed", str(e))
        return None

    def start_oauth_flow(self) -> dict:
        """Start the OAuth 2.0 flow using a local loopback server.

        1. Starts a temporary HTTP server on a random port
        2. Opens the browser to Google's auth page
        3. Captures the callback code
        4. Exchanges it for tokens
        5. Stores tokens in keyring
        """
        client_id, client_secret = self._get_client_config()

        # Start local callback server
        server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}"

        OAuthCallbackHandler.auth_code = None

        # Build the authorization URL
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&"
            f"redirect_uri={redirect_uri}&"
            "response_type=code&"
            f"scope={'%20'.join(SCOPES)}&"
            "access_type=offline&"
            "prompt=consent"
        )

        # Open browser and wait for callback
        audit.log("gmail_oauth_start", redirect_uri)
        webbrowser.open(auth_url)

        # Handle one request (the callback)
        server_thread = Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        server_thread.join(timeout=120)
        server.server_close()

        if not OAuthCallbackHandler.auth_code:
            raise ValueError("OAuth flow timed out or was cancelled")

        # Exchange code for tokens
        import httpx

        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": OAuthCallbackHandler.auth_code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        creds = Credentials(
            token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        self._save_token(creds)
        audit.log("gmail_oauth_complete", "success")

        return {"status": "connected", "message": "Google account connected"}

    def is_connected(self) -> bool:
        """Check if we have valid Gmail credentials."""
        return self.get_credentials() is not None

    def fetch_recent_emails(self, max_results: int = 20) -> list[dict]:
        """Fetch recent emails from the inbox."""
        creds = self.get_credentials()
        if not creds:
            raise ValueError("Gmail not connected. Please authenticate first.")

        audit.log("gmail_fetch", f"max_results={max_results}")

        service = build("gmail", "v1", credentials=creds)
        results = (
            service.users()
            .messages()
            .list(userId="me", maxResults=max_results, labelIds=["INBOX"])
            .execute()
        )

        messages = results.get("messages", [])
        emails = []

        for msg_ref in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            snippet = msg.get("snippet", "")

            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": snippet,
                "labels": msg.get("labelIds", []),
            })

        return emails

    def get_email_body(self, message_id: str) -> str:
        """Get the full text body of an email."""
        creds = self.get_credentials()
        if not creds:
            raise ValueError("Gmail not connected.")

        audit.log("gmail_read_body", message_id)

        service = build("gmail", "v1", credentials=creds)
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        return self._extract_body(msg.get("payload", {}))

    def _extract_body(self, payload: dict) -> str:
        """Extract plain text body from Gmail message payload."""
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            text = self._extract_body(part)
            if text:
                return text

        return ""

    def disconnect(self) -> None:
        """Remove stored Gmail credentials."""
        try:
            keyring_svc.delete(KEYRING_TOKEN_KEY)
        except Exception:
            pass
        audit.log("gmail_disconnect", "token_removed")
