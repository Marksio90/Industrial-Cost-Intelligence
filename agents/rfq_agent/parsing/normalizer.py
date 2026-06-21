from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import structlog

from .response_parser import ParsedQuote

log = structlog.get_logger(__name__)

# Approximate EUR exchange rates (updated manually / via API in prod)
_FX_TO_EUR: dict[str, Decimal] = {
    "EUR": Decimal("1.0"),
    "USD": Decimal("0.92"),
    "GBP": Decimal("1.17"),
    "CHF": Decimal("1.02"),
    "PLN": Decimal("0.23"),
    "CZK": Decimal("0.041"),
    "SEK": Decimal("0.088"),
    "NOK": Decimal("0.085"),
    "JPY": Decimal("0.0061"),
}


@dataclass
class NormalizedQuote:
    supplier_email: str
    unit_price_eur: Decimal | None
    original_price: Decimal | None
    original_currency: str | None
    lead_time_days: int | None
    validity_days: int | None
    incoterms: str | None
    payment_terms: str | None
    certifications: list[str]
    total_score: float
    confidence: float
    notes: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier_email": self.supplier_email,
            "unit_price_eur": str(self.unit_price_eur) if self.unit_price_eur else None,
            "original_price": str(self.original_price) if self.original_price else None,
            "original_currency": self.original_currency,
            "lead_time_days": self.lead_time_days,
            "validity_days": self.validity_days,
            "incoterms": self.incoterms,
            "payment_terms": self.payment_terms,
            "certifications": self.certifications,
            "total_score": round(self.total_score, 4),
            "confidence": round(self.confidence, 4),
            "notes": self.notes,
        }


class QuoteNormalizer:
    """
    Converts ParsedQuote objects into a standardised, comparable NormalizedQuote.

    Scoring formula (0–1 scale, higher = better offer):
      price_score  = 0 if no price, else 1 / (1 + ln(price_eur / reference_price))
      speed_score  = 1 - lead_days / 90   (capped at [0, 1])
      cert_score   = len(certs) / 5       (capped at 1)
      total = 0.5 * price + 0.35 * speed + 0.15 * cert
    """

    def __init__(
        self,
        reference_price_eur: Decimal | None = None,
        required_certifications: list[str] | None = None,
        fx_rates: dict[str, Decimal] | None = None,
    ) -> None:
        self._ref_price = reference_price_eur
        self._required_certs = required_certifications or []
        self._fx = fx_rates or _FX_TO_EUR

    def normalize(self, quote: ParsedQuote, supplier_email: str) -> NormalizedQuote:
        price_eur = self._to_eur(quote.unit_price, quote.currency)

        # Price score
        price_score = 0.0
        if price_eur and self._ref_price and self._ref_price > 0:
            import math
            ratio = float(price_eur / self._ref_price)
            price_score = max(0.0, min(1.0, 1.0 - math.log(max(ratio, 0.001)) * 0.5))
        elif price_eur:
            price_score = 0.5  # have price but no reference

        # Speed score
        lead = quote.lead_time_days or 60
        speed_score = max(0.0, min(1.0, 1.0 - lead / 90.0))

        # Cert score
        if self._required_certs:
            matched = sum(
                1 for rc in self._required_certs
                if any(rc.lower() in c.lower() for c in quote.certifications)
            )
            cert_score = matched / len(self._required_certs)
        else:
            cert_score = min(1.0, len(quote.certifications) / 5.0)

        total = 0.5 * price_score + 0.35 * speed_score + 0.15 * cert_score

        return NormalizedQuote(
            supplier_email=supplier_email,
            unit_price_eur=price_eur,
            original_price=quote.unit_price,
            original_currency=quote.currency,
            lead_time_days=quote.lead_time_days,
            validity_days=quote.validity_days,
            incoterms=quote.incoterms,
            payment_terms=quote.payment_terms,
            certifications=quote.certifications,
            total_score=total,
            confidence=quote.confidence,
            notes=quote.notes[:300],
            metadata={
                "price_score": round(price_score, 4),
                "speed_score": round(speed_score, 4),
                "cert_score": round(cert_score, 4),
            },
        )

    def rank(self, quotes: list[NormalizedQuote]) -> list[NormalizedQuote]:
        """Return quotes sorted best-first by total_score."""
        return sorted(quotes, key=lambda q: q.total_score, reverse=True)

    def _to_eur(self, price: Decimal | None, currency: str | None) -> Decimal | None:
        if price is None:
            return None
        ccy = (currency or "EUR").upper()
        rate = self._fx.get(ccy, Decimal("1.0"))
        return (price * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
