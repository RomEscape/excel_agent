"""Email importance filtering — keyword-based and AI-based."""

import json
import logging

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
audit = AuditService()

DEFAULT_RULES = {
    "high": [
        "긴급", "urgent", "asap", "deadline", "마감",
        "결재", "승인", "요청", "필수", "중요",
    ],
    "low": [
        "newsletter", "unsubscribe", "수신거부",
        "광고", "promotion", "no-reply", "noreply",
    ],
}


class FilterService:
    """Keyword-based email importance classifier."""

    def __init__(self) -> None:
        self._rules_path = get_data_dir() / "filter_rules.json"
        self._rules = self._load_rules()

    def _load_rules(self) -> dict:
        if self._rules_path.exists():
            try:
                return json.loads(self._rules_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        # Save defaults on first run
        self._save_rules(DEFAULT_RULES)
        return DEFAULT_RULES

    def _save_rules(self, rules: dict) -> None:
        self._rules_path.write_text(
            json.dumps(rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_rules(self) -> dict:
        return self._rules

    def update_rules(self, rules: dict) -> dict:
        self._rules = rules
        self._save_rules(rules)
        audit.log("filter_rules_update", "updated")
        return self._rules

    def classify(self, subject: str, snippet: str, sender: str) -> str:
        """Classify email importance: high / normal / low."""
        text = f"{subject} {snippet} {sender}".lower()

        for keyword in self._rules.get("high", []):
            if keyword.lower() in text:
                return "high"

        for keyword in self._rules.get("low", []):
            if keyword.lower() in text:
                return "low"

        return "normal"

    def classify_emails(self, emails: list[dict]) -> list[dict]:
        """Add importance field to a list of email dicts."""
        result = []
        for email in emails:
            importance = self.classify(
                email.get("subject", ""),
                email.get("snippet", ""),
                email.get("from", ""),
            )
            result.append({**email, "importance": importance})
        audit.log("filter_classify", f"count={len(emails)}")
        return result
