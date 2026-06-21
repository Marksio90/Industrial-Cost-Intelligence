from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class QuoteStatus(StrEnum):
    RECEIVED  = "RECEIVED"
    ANALYSED  = "ANALYSED"
    ACCEPTED  = "ACCEPTED"
    REJECTED  = "REJECTED"
    EXPIRED   = "EXPIRED"


@dataclass(frozen=True)
class QuoteLineItem:
    item_id: UUID
    material_id: UUID
    description: str
    quantity: Decimal
    unit_of_measure: str
    unit_price_eur: Decimal
    total_price_eur: Decimal
    lead_time_days: int
    notes: str = ""


class Quote:
    def __init__(
        self,
        quote_id: UUID,
        rfq_id: UUID,
        supplier_id: UUID,
        quote_number: str,
        validity_date: date,
        line_items: list[QuoteLineItem],
        status: QuoteStatus,
        tenant_id: str,
        currency: str = "EUR",
        delivery_terms: str = "DAP",
        payment_terms: str = "NET30",
        notes: str = "",
        rejection_reason: str = "",
    ) -> None:
        self._id = quote_id
        self._rfq_id = rfq_id
        self._supplier_id = supplier_id
        self._quote_number = quote_number
        self._validity_date = validity_date
        self._line_items = list(line_items)
        self._status = status
        self._tenant_id = tenant_id
        self._currency = currency
        self._delivery_terms = delivery_terms
        self._payment_terms = payment_terms
        self._notes = notes
        self._rejection_reason = rejection_reason

    @classmethod
    def create(
        cls,
        rfq_id: UUID,
        supplier_id: UUID,
        quote_number: str,
        validity_date: date,
        line_items: list[QuoteLineItem],
        tenant_id: str,
        currency: str = "EUR",
        delivery_terms: str = "DAP",
        payment_terms: str = "NET30",
        notes: str = "",
    ) -> "Quote":
        return cls(
            quote_id=uuid4(), rfq_id=rfq_id, supplier_id=supplier_id,
            quote_number=quote_number, validity_date=validity_date,
            line_items=line_items, status=QuoteStatus.RECEIVED,
            tenant_id=tenant_id, currency=currency, delivery_terms=delivery_terms,
            payment_terms=payment_terms, notes=notes,
        )

    @property
    def id(self) -> UUID: return self._id
    @property
    def rfq_id(self) -> UUID: return self._rfq_id
    @property
    def supplier_id(self) -> UUID: return self._supplier_id
    @property
    def quote_number(self) -> str: return self._quote_number
    @property
    def validity_date(self) -> date: return self._validity_date
    @property
    def line_items(self) -> list[QuoteLineItem]: return list(self._line_items)
    @property
    def status(self) -> QuoteStatus: return self._status
    @property
    def tenant_id(self) -> str: return self._tenant_id
    @property
    def currency(self) -> str: return self._currency
    @property
    def delivery_terms(self) -> str: return self._delivery_terms
    @property
    def payment_terms(self) -> str: return self._payment_terms
    @property
    def notes(self) -> str: return self._notes
    @property
    def rejection_reason(self) -> str: return self._rejection_reason

    @property
    def total_eur(self) -> Decimal:
        return sum((li.total_price_eur for li in self._line_items), Decimal(0))

    @property
    def is_expired(self) -> bool:
        from datetime import date as d
        return self._validity_date < d.today()

    def accept(self) -> None:
        if self._status != QuoteStatus.RECEIVED and self._status != QuoteStatus.ANALYSED:
            raise ValueError("Quote cannot be accepted in current status")
        if self.is_expired:
            raise ValueError("Cannot accept expired quote")
        self._status = QuoteStatus.ACCEPTED

    def reject(self, reason: str) -> None:
        if self._status == QuoteStatus.ACCEPTED:
            raise ValueError("Cannot reject accepted quote")
        self._status = QuoteStatus.REJECTED
        self._rejection_reason = reason

    def mark_analysed(self) -> None:
        self._status = QuoteStatus.ANALYSED
