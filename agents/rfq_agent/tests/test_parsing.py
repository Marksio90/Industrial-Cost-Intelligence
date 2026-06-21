"""Tests for response_parser and normalizer — no external deps required."""
from __future__ import annotations

from decimal import Decimal

import pytest

from ..config import AgentSettings
from ..parsing.normalizer import NormalizedQuote, QuoteNormalizer
from ..parsing.response_parser import ParsedQuote, ResponseParser


@pytest.fixture
def settings(monkeypatch) -> AgentSettings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-0000")
    monkeypatch.setenv("RFQ_ANTHROPIC_API_KEY", "sk-test-0000")
    return AgentSettings()  # type: ignore[call-arg]


@pytest.fixture
def parser(settings) -> ResponseParser:
    return ResponseParser(settings)


# ── Price extraction ───────────────────────────────────────────────────────

class TestPriceExtraction:
    def test_eur_symbol_before_amount(self, parser):
        result = parser.parse("Unit price: €12.50 per piece, delivery 4 weeks.")
        assert result.unit_price == Decimal("12.50")
        assert result.currency == "EUR"

    def test_usd_after_amount(self, parser):
        result = parser.parse("We offer 99.99 USD per unit.")
        assert result.unit_price == Decimal("99.99")
        assert result.currency == "USD"

    def test_thousands_separator(self, parser):
        result = parser.parse("Price: €1,234.56 per kg.")
        assert result.unit_price == Decimal("1234.56")

    def test_no_price(self, parser):
        result = parser.parse("Thank you for your inquiry. We will review it shortly.")
        assert result.unit_price is None
        assert result.currency is None

    def test_gbp_symbol(self, parser):
        result = parser.parse("Cost: £45.00 each, FOB Liverpool.")
        assert result.unit_price == Decimal("45.00")
        assert result.currency == "GBP"


# ── Lead time extraction ───────────────────────────────────────────────────

class TestLeadTimeExtraction:
    def test_weeks(self, parser):
        result = parser.parse("Price €10. Lead time: 6 weeks.")
        assert result.lead_time_days == 42

    def test_range_weeks(self, parser):
        result = parser.parse("Delivery: 3-4 weeks from order confirmation.")
        assert result.lead_time_days == 24  # (3+4)//2 * 7

    def test_business_days(self, parser):
        result = parser.parse("Lead time 10 business days.")
        assert result.lead_time_days == 10

    def test_months(self, parser):
        result = parser.parse("Production lead time: 2 months.")
        assert result.lead_time_days == 60


# ── Validity extraction ────────────────────────────────────────────────────

class TestValidityExtraction:
    def test_explicit_days(self, parser):
        result = parser.parse("This offer is valid for 30 days.")
        assert result.validity_days == 30

    def test_validity_period(self, parser):
        result = parser.parse("Validity period: 60 days from quotation date.")
        assert result.validity_days == 60

    def test_no_validity(self, parser):
        result = parser.parse("Please find our offer attached.")
        assert result.validity_days is None


# ── Incoterms extraction ───────────────────────────────────────────────────

class TestIncotermsExtraction:
    def test_exw(self, parser):
        result = parser.parse("Price €5.00/unit EXW our warehouse.")
        assert result.incoterms == "EXW"

    def test_ddp(self, parser):
        result = parser.parse("We offer DDP delivery to your site.")
        assert result.incoterms == "DDP"

    def test_no_incoterms(self, parser):
        result = parser.parse("Price €5.00 per unit.")
        assert result.incoterms is None


# ── Certification extraction ───────────────────────────────────────────────

class TestCertificationExtraction:
    def test_multiple_certs(self, parser):
        result = parser.parse(
            "We are certified to ISO 9001 and IATF 16949. "
            "Our facility holds ISO 14001 certification."
        )
        assert "ISO 9001" in result.certifications
        assert "IATF 16949" in result.certifications
        assert "ISO 14001" in result.certifications

    def test_no_duplicates(self, parser):
        result = parser.parse("ISO 9001 certified company. ISO 9001 since 2010.")
        assert result.certifications.count("ISO 9001") == 1


# ── Confidence scoring ─────────────────────────────────────────────────────

class TestConfidenceScoring:
    def test_full_response_high_confidence(self, parser):
        email = (
            "Dear Procurement Team, Thank you for your RFQ. "
            "Unit price: €15.75 EXW. Lead time: 4 weeks. "
            "Valid for 30 days. Payment: Net 30. "
            "We hold ISO 9001 and IATF 16949 certifications."
        )
        result = parser.parse(email)
        assert result.confidence >= 0.7

    def test_empty_response_low_confidence(self, parser):
        result = parser.parse("We will get back to you soon.")
        assert result.confidence < 0.2


# ── Normalizer ────────────────────────────────────────────────────────────

class TestQuoteNormalizer:
    def _make_quote(
        self,
        price: str | None = "10.00",
        currency: str = "EUR",
        lead_days: int = 30,
        certs: list[str] | None = None,
    ) -> ParsedQuote:
        return ParsedQuote(
            unit_price=Decimal(price) if price else None,
            currency=currency,
            quantity=None,
            lead_time_days=lead_days,
            validity_days=30,
            incoterms="EXW",
            payment_terms="Net 30",
            certifications=certs or [],
            notes="",
            confidence=0.8,
        )

    def test_eur_passthrough(self):
        normalizer = QuoteNormalizer(reference_price_eur=Decimal("10.00"))
        nq = normalizer.normalize(self._make_quote("10.00", "EUR"), "a@b.com")
        assert nq.unit_price_eur == Decimal("10.0000")

    def test_usd_conversion(self):
        normalizer = QuoteNormalizer()
        nq = normalizer.normalize(self._make_quote("10.00", "USD"), "a@b.com")
        assert nq.unit_price_eur is not None
        assert nq.unit_price_eur < Decimal("10.00")  # USD < EUR

    def test_ranking_best_price_first(self):
        normalizer = QuoteNormalizer(reference_price_eur=Decimal("10.00"))
        q_cheap = self._make_quote("8.00", "EUR")
        q_expensive = self._make_quote("15.00", "EUR")
        nq_cheap = normalizer.normalize(q_cheap, "cheap@b.com")
        nq_expensive = normalizer.normalize(q_expensive, "expensive@b.com")
        ranked = normalizer.rank([nq_expensive, nq_cheap])
        assert ranked[0].supplier_email == "cheap@b.com"

    def test_ranking_fast_delivery_bonus(self):
        normalizer = QuoteNormalizer()
        q_fast = self._make_quote("10.00", lead_days=7)
        q_slow = self._make_quote("10.00", lead_days=90)
        nq_fast = normalizer.normalize(q_fast, "fast@b.com")
        nq_slow = normalizer.normalize(q_slow, "slow@b.com")
        assert nq_fast.total_score > nq_slow.total_score

    def test_missing_price_lower_score(self):
        normalizer = QuoteNormalizer()
        nq_no_price = normalizer.normalize(self._make_quote(None), "x@y.com")
        nq_with_price = normalizer.normalize(self._make_quote("10.00"), "z@y.com")
        assert nq_with_price.total_score >= nq_no_price.total_score

    def test_to_dict(self):
        normalizer = QuoteNormalizer()
        nq = normalizer.normalize(self._make_quote(), "a@b.com")
        d = nq.to_dict()
        assert "unit_price_eur" in d
        assert "total_score" in d
        assert isinstance(d["total_score"], float)
