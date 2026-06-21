from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Classic spam trigger words (lightweight heuristic — not a replacement for SpamAssassin)
_SPAM_TRIGGERS = [
    "click here", "limited time offer", "act now", "free money",
    "winner", "you've been selected", "make money fast",
    "100% free", "guaranteed", "no risk", "million dollars",
    "buy now", "order now", "discount", "earn extra cash",
    "lowest price", "best price guaranteed",
]

_SPAM_TRIGGER_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _SPAM_TRIGGERS) + r")\b",
    re.I,
)

# Link density threshold (links per 100 words)
_MAX_LINK_DENSITY = 5.0

# Caps ratio threshold (ratio of UPPERCASE to total alpha chars)
_MAX_CAPS_RATIO = 0.4


@dataclass
class SpamCheckResult:
    passed: bool = True
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    def flag(self, msg: str, penalty: float = 1.0) -> None:
        self.flags.append(msg)
        self.score += penalty
        if self.score >= 3.0:
            self.passed = False


class AntiSpamChecker:
    """Lightweight anti-spam filter for outbound RFQ emails."""

    def check(self, subject: str, body: str) -> SpamCheckResult:
        result = SpamCheckResult()
        full_text = f"{subject}\n{body}"

        # Spam trigger words
        triggers = _SPAM_TRIGGER_RE.findall(full_text)
        if triggers:
            result.flag(f"Spam trigger words: {set(triggers)}", penalty=len(triggers) * 0.5)

        # CAPS ratio
        alpha = [c for c in full_text if c.isalpha()]
        if alpha:
            caps_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
            if caps_ratio > _MAX_CAPS_RATIO:
                result.flag(f"High caps ratio: {caps_ratio:.0%}", penalty=2.0)

        # Link density
        links = re.findall(r"https?://\S+", body)
        words = body.split()
        if words:
            density = len(links) / len(words) * 100
            if density > _MAX_LINK_DENSITY:
                result.flag(f"High link density: {density:.1f} links/100 words", penalty=1.5)

        # Excessive punctuation
        punct_ratio = sum(1 for c in full_text if c in "!?") / max(len(full_text), 1)
        if punct_ratio > 0.05:
            result.flag("Excessive ! or ? punctuation", penalty=1.0)

        # Subject line length
        if len(subject) > 120:
            result.flag("Subject line too long", penalty=0.5)

        # Duplicate content fingerprint (caller passes None on first email)
        log.debug(
            "spam_check_result",
            passed=result.passed,
            score=result.score,
            flags=result.flags,
        )
        return result


def content_fingerprint(subject: str, body: str) -> str:
    """SHA-256 fingerprint of normalised email content."""
    normalised = re.sub(r"\s+", " ", f"{subject}\n{body}").strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()
