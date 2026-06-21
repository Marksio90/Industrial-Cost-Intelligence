"""Tests for rate limiter, compliance, and anti-spam."""
from __future__ import annotations

import asyncio

import pytest

from ..config import AgentSettings
from ..safety.anti_spam import AntiSpamChecker, content_fingerprint
from ..safety.compliance import ComplianceChecker
from ..safety.rate_limiter import RateLimiter, SlidingWindowCounter, TokenBucket


@pytest.fixture
def settings(monkeypatch) -> AgentSettings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-0000")
    monkeypatch.setenv("RFQ_ANTHROPIC_API_KEY", "sk-test-0000")
    monkeypatch.setenv("RFQ_MAX_EMAILS_PER_HOUR", "5")
    monkeypatch.setenv("RFQ_MAX_EMAILS_PER_SUPPLIER_PER_DAY", "2")
    monkeypatch.setenv("RFQ_MIN_EMAIL_INTERVAL_S", "1")
    return AgentSettings()  # type: ignore[call-arg]


# ── Token Bucket ───────────────────────────────────────────────────────────

class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_allows_within_capacity(self):
        bucket = TokenBucket(capacity=3, refill_rate=1.0)
        assert await bucket.consume(1)
        assert await bucket.consume(1)
        assert await bucket.consume(1)

    @pytest.mark.asyncio
    async def test_blocks_when_empty(self):
        bucket = TokenBucket(capacity=1, refill_rate=0.01)
        assert await bucket.consume(1)
        assert not await bucket.consume(1)

    @pytest.mark.asyncio
    async def test_refills_over_time(self):
        bucket = TokenBucket(capacity=1, refill_rate=100.0)  # fast refill
        assert await bucket.consume(1)
        await asyncio.sleep(0.05)
        assert await bucket.consume(1)


# ── Sliding Window ─────────────────────────────────────────────────────────

class TestSlidingWindowCounter:
    def test_allows_within_limit(self):
        sw = SlidingWindowCounter(window_s=60, max_count=3)
        assert sw.allow()
        assert sw.allow()
        assert sw.allow()

    def test_blocks_at_limit(self):
        sw = SlidingWindowCounter(window_s=60, max_count=2)
        assert sw.allow()
        assert sw.allow()
        assert not sw.allow()


# ── Rate Limiter ───────────────────────────────────────────────────────────

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_global_check_passes_initially(self, settings):
        rl = RateLimiter(settings, redis=None)
        ok, msg = await rl.check_global()
        assert ok

    @pytest.mark.asyncio
    async def test_global_check_fails_at_cap(self, settings):
        rl = RateLimiter(settings, redis=None)
        for _ in range(5):
            rl._global_window.allow()
        ok, msg = await rl.check_global()
        assert not ok
        assert "cap" in msg.lower()

    @pytest.mark.asyncio
    async def test_domain_check_no_redis(self, settings):
        rl = RateLimiter(settings, redis=None)
        ok, _ = await rl.check_domain_daily("supplier@acme.de")
        assert ok  # no Redis → fail open


# ── Compliance ────────────────────────────────────────────────────────────

class TestComplianceChecker:
    @pytest.fixture
    def checker(self, settings) -> ComplianceChecker:
        return ComplianceChecker(settings)

    def test_valid_email_allowed(self, checker):
        result = checker.check_recipient("procurement@supplier.de")
        assert result.allowed

    def test_example_com_blocked(self, checker):
        result = checker.check_recipient("test@example.com")
        assert not result.allowed
        assert any("blocked" in v.lower() for v in result.violations)

    def test_malformed_email_blocked(self, checker):
        result = checker.check_recipient("not-an-email")
        assert not result.allowed

    def test_body_missing_unsubscribe(self, checker):
        result = checker.check_email_body("Dear Supplier, please quote this.")
        assert result.warnings  # missing GDPR tokens

    def test_unsubscribe_intent_detected(self, checker):
        assert checker.detect_unsubscribe_intent("Please unsubscribe me from your emails.")
        assert checker.detect_unsubscribe_intent("I want to opt out of future contact.")
        assert not checker.detect_unsubscribe_intent("Thank you for the RFQ, price is €10.")

    def test_url_http_allowed(self, checker):
        result = checker.check_url("https://supplier.de/contact")
        assert result.allowed

    def test_url_ftp_blocked(self, checker):
        result = checker.check_url("ftp://supplier.de/file")
        assert not result.allowed


# ── Anti-Spam ─────────────────────────────────────────────────────────────

class TestAntiSpamChecker:
    @pytest.fixture
    def checker(self) -> AntiSpamChecker:
        return AntiSpamChecker()

    def test_professional_email_passes(self, checker):
        result = checker.check(
            subject="RFQ-ICI-202506-ABCD: Aluminium Brackets — Request for Quotation",
            body=(
                "Dear Procurement Manager,\n\n"
                "We would like to request a quotation for aluminium CNC machined brackets "
                "as per the attached specifications. Kindly provide your best price, lead time, "
                "and payment terms by 2025-07-01.\n\n"
                "Best regards,\nICI Procurement Team\n\n"
                "To unsubscribe: https://example.com/unsubscribe — GDPR compliant"
            ),
        )
        assert result.passed
        assert result.score < 2.0

    def test_spam_triggers_detected(self, checker):
        result = checker.check(
            subject="ACT NOW! LIMITED TIME OFFER!!!",
            body="Click here to make money fast! Guaranteed lowest price! No risk!",
        )
        assert not result.passed
        assert result.score >= 3.0
        assert result.flags

    def test_caps_heavy_fails(self, checker):
        result = checker.check(
            subject="RFQ",
            body="PLEASE SEND US YOUR BEST PRICE IMMEDIATELY WE NEED THIS URGENTLY TODAY",
        )
        assert not result.passed

    def test_fingerprint_deterministic(self):
        fp1 = content_fingerprint("Subject", "Body text here.")
        fp2 = content_fingerprint("Subject", "Body text here.")
        assert fp1 == fp2

    def test_fingerprint_different_content(self):
        fp1 = content_fingerprint("Subject A", "Body A.")
        fp2 = content_fingerprint("Subject B", "Body B.")
        assert fp1 != fp2
