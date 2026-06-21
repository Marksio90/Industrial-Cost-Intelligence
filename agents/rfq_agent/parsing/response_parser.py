from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)


@dataclass
class ParsedQuote:
    unit_price: Decimal | None
    currency: str | None
    quantity: float | None
    lead_time_days: int | None
    validity_days: int | None
    incoterms: str | None
    payment_terms: str | None
    certifications: list[str]
    notes: str
    raw_fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


# ── Price extraction ───────────────────────────────────────────────────────

_CURRENCY_SYMBOLS = {
    "€": "EUR", "£": "GBP", "$": "USD", "¥": "JPY",
    "CHF": "CHF", "PLN": "PLN", "CZK": "CZK",
}
_CURRENCY_CODES = list(_CURRENCY_SYMBOLS.values()) + list(_CURRENCY_SYMBOLS.keys())
_CURRENCY_RE = re.compile(
    r"(?P<sym>" + "|".join(re.escape(c) for c in sorted(_CURRENCY_CODES, key=len, reverse=True)) + r")"
    r"\s*(?P<amount>[\d\s,.]+)"
    r"|"
    r"(?P<amount2>[\d\s,.]+)\s*"
    r"(?P<sym2>" + "|".join(re.escape(c) for c in sorted(_CURRENCY_CODES, key=len, reverse=True)) + r")",
    re.I,
)

# Lead time patterns: "6 weeks", "10 business days", "3-4 weeks"
_LEAD_TIME_RE = re.compile(
    r"(?:lead[- ]?time|delivery|lead)[:\s]*"
    r"(?P<lo>\d+)(?:\s*[-–]\s*(?P<hi>\d+))?\s*"
    r"(?P<unit>week|day|working day|business day|month)s?",
    re.I,
)

# Validity: "valid for 30 days", "validity: 60 days"
_VALIDITY_RE = re.compile(
    r"(?:valid(?:ity)?(?:\s+(?:for|period|until))?|offer valid)[:\s]*"
    r"(?P<days>\d+)\s*(?:days?|d\b)",
    re.I,
)

_INCOTERMS = ["EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"]
_INCOTERMS_RE = re.compile(r"\b(" + "|".join(_INCOTERMS) + r")\b", re.I)

_PAYMENT_RE = re.compile(
    r"(?:payment|terms)[:\s]*([^\n\.]{5,60})",
    re.I,
)

_CERT_KEYWORDS = [
    "ISO 9001", "ISO 14001", "IATF 16949", "AS9100", "ISO 45001",
    "ISO 13485", "NADCAP", "CE mark", "RoHS", "REACH",
]
_CERT_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _CERT_KEYWORDS) + r")\b",
    re.I,
)


class ResponseParser:
    """
    Two-stage email response parser:
    1. Regex-based extraction for structured fields (fast, no external deps)
    2. LLM-assisted extraction for ambiguous/freeform content
    """

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._llm_client = None  # lazy-loaded

    def parse(self, email_text: str) -> ParsedQuote:
        """Synchronous fast parser — regex only."""
        text = _clean_text(email_text)
        confidence = 0.0
        fields: dict[str, Any] = {}

        # Price
        price, currency = _extract_price(text)
        if price:
            fields["unit_price"] = str(price)
            fields["currency"] = currency
            confidence += 0.3

        # Lead time
        lead_days = _extract_lead_time_days(text)
        if lead_days is not None:
            fields["lead_time_days"] = lead_days
            confidence += 0.2

        # Validity
        validity_days = _extract_validity_days(text)
        if validity_days is not None:
            fields["validity_days"] = validity_days
            confidence += 0.1

        # Incoterms
        incoterms = _extract_incoterms(text)
        if incoterms:
            fields["incoterms"] = incoterms
            confidence += 0.1

        # Payment terms
        payment = _extract_payment_terms(text)
        if payment:
            fields["payment_terms"] = payment
            confidence += 0.1

        # Certifications
        certs = _extract_certifications(text)
        if certs:
            fields["certifications"] = certs
            confidence += 0.1

        confidence = min(confidence, 1.0)

        return ParsedQuote(
            unit_price=price,
            currency=currency,
            quantity=None,
            lead_time_days=lead_days,
            validity_days=validity_days,
            incoterms=incoterms,
            payment_terms=payment,
            certifications=certs,
            notes=text[:500],
            raw_fields=fields,
            confidence=confidence,
        )

    async def parse_with_llm(self, email_text: str) -> ParsedQuote:
        """
        LLM-assisted parsing for complex / non-standard responses.
        Falls back to regex if LLM fails.
        """
        import anthropic
        import json

        regex_result = self.parse(email_text)

        if self._llm_client is None:
            self._llm_client = anthropic.AsyncAnthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value()
            )

        prompt = f"""Extract quote information from this supplier email response.
Return ONLY valid JSON with these fields (use null if not found):
{{
  "unit_price": number | null,
  "currency": "EUR"|"USD"|"GBP"|null,
  "quantity": number | null,
  "lead_time_days": number | null,
  "validity_days": number | null,
  "incoterms": string | null,
  "payment_terms": string | null,
  "certifications": [string],
  "notes": string
}}

Email:
---
{email_text[:3000]}
---"""

        try:
            message = await self._llm_client.messages.create(
                model=self._settings.llm_model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
            raw = re.sub(r"\s*```$", "", raw, flags=re.M)
            data = json.loads(raw)

            price = _to_decimal(data.get("unit_price"))
            return ParsedQuote(
                unit_price=price,
                currency=data.get("currency") or self._settings.currency_default,
                quantity=data.get("quantity"),
                lead_time_days=data.get("lead_time_days"),
                validity_days=data.get("validity_days"),
                incoterms=data.get("incoterms"),
                payment_terms=data.get("payment_terms"),
                certifications=data.get("certifications") or [],
                notes=data.get("notes", ""),
                raw_fields=data,
                confidence=0.9 if price else 0.5,
            )
        except Exception as exc:
            log.warning("llm_parse_failed", error=str(exc))
            return regex_result


# ── Extraction helpers ─────────────────────────────────────────────────────

def _extract_price(text: str) -> tuple[Decimal | None, str | None]:
    for m in _CURRENCY_RE.finditer(text):
        sym = m.group("sym") or m.group("sym2") or ""
        amount_str = m.group("amount") or m.group("amount2") or ""
        amount_str = amount_str.replace(" ", "").replace(",", ".")
        # Remove thousands separators (e.g. 1.234,56 → 1234.56)
        if amount_str.count(".") > 1:
            amount_str = amount_str.replace(".", "", amount_str.count(".") - 1)
        try:
            amount = Decimal(amount_str)
            if amount > 0:
                currency = _CURRENCY_SYMBOLS.get(sym.upper(), sym.upper()) or "EUR"
                return amount, currency
        except InvalidOperation:
            continue
    return None, None


def _extract_lead_time_days(text: str) -> int | None:
    m = _LEAD_TIME_RE.search(text)
    if not m:
        return None
    lo = int(m.group("lo"))
    hi = int(m.group("hi")) if m.group("hi") else lo
    avg = (lo + hi) // 2
    unit = m.group("unit").lower()
    if "week" in unit:
        return avg * 7
    if "month" in unit:
        return avg * 30
    return avg


def _extract_validity_days(text: str) -> int | None:
    m = _VALIDITY_RE.search(text)
    return int(m.group("days")) if m else None


def _extract_incoterms(text: str) -> str | None:
    m = _INCOTERMS_RE.search(text)
    return m.group(1).upper() if m else None


def _extract_payment_terms(text: str) -> str | None:
    m = _PAYMENT_RE.search(text)
    return m.group(1).strip()[:80] if m else None


def _extract_certifications(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _CERT_RE.finditer(text)))


def _clean_text(text: str) -> str:
    # Strip quoted reply headers
    text = re.sub(r"On .+ wrote:\s*", "", text, flags=re.DOTALL)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def _to_decimal(val: Any) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return None
