from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)


@dataclass
class ComplianceResult:
    allowed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_violation(self, msg: str) -> None:
        self.violations.append(msg)
        self.allowed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# Domains prohibited from being contacted (GDPR "right to be forgotten" list etc.)
_ALWAYS_BLOCKED: set[str] = {
    "example.com", "test.com", "localhost",
}

# Patterns that indicate a no-contact / unsubscribe request in email text
_UNSUBSCRIBE_PATTERNS = [
    re.compile(r"\bunsubscribe\b", re.I),
    re.compile(r"\bno[- ]contact\b", re.I),
    re.compile(r"\bremove me\b", re.I),
    re.compile(r"\bopt[- ]out\b", re.I),
    re.compile(r"\bdo not (contact|email|send)\b", re.I),
]

# Required legal footer elements
_REQUIRED_FOOTER_TOKENS = [
    "unsubscribe",
    "gdpr",
]


class ComplianceChecker:
    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings

    def check_recipient(self, email: str) -> ComplianceResult:
        result = ComplianceResult(allowed=True)
        domain = _domain(email)

        if domain in _ALWAYS_BLOCKED:
            result.add_violation(f"Domain {domain} is always blocked")

        if self._settings.blacklisted_domains and domain in self._settings.blacklisted_domains:
            result.add_violation(f"Domain {domain} is on the configured blacklist")

        if self._settings.allowed_domains_only:
            if domain not in self._settings.allowed_domains:
                result.add_violation(
                    f"Domain {domain} not in allowed_domains whitelist"
                )

        if not _is_valid_email(email):
            result.add_violation(f"Malformed email address: {email!r}")

        return result

    def check_email_body(self, body: str) -> ComplianceResult:
        result = ComplianceResult(allowed=True)
        body_lower = body.lower()

        for token in _REQUIRED_FOOTER_TOKENS:
            if token not in body_lower:
                result.add_warning(
                    f"Email body missing required footer token: {token!r}"
                )

        # Sanity: no raw PII patterns like SSNs
        if re.search(r"\b\d{3}-\d{2}-\d{4}\b", body):
            result.add_violation("Body contains pattern matching US SSN — possible PII leak")

        return result

    def detect_unsubscribe_intent(self, email_text: str) -> bool:
        for pattern in _UNSUBSCRIBE_PATTERNS:
            if pattern.search(email_text):
                log.info("unsubscribe_intent_detected")
                return True
        return False

    def check_url(self, url: str) -> ComplianceResult:
        result = ComplianceResult(allowed=True)
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if parsed.scheme not in ("http", "https"):
            result.add_violation(f"Non-HTTP scheme: {parsed.scheme!r}")
        if not domain:
            result.add_violation("Empty domain in URL")
        if domain in _ALWAYS_BLOCKED:
            result.add_violation(f"Blocked domain: {domain}")

        return result


def _domain(email: str) -> str:
    return email.split("@", 1)[-1].lower() if "@" in email else ""


def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))
