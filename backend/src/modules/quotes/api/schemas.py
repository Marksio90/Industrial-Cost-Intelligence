from __future__ import annotations
from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from ..domain.models import QuoteStatus


class LineItemRequest(BaseModel):
    material_id: UUID; description: str = Field(..., min_length=1); quantity: Decimal = Field(..., gt=0)
    unit_of_measure: str = Field(..., max_length=8); unit_price_eur: Decimal = Field(..., ge=0)
    lead_time_days: int = Field(..., ge=0); notes: str = ""

class CreateQuoteRequest(BaseModel):
    rfq_id: UUID; supplier_id: UUID
    quote_number: str = Field(..., min_length=1, max_length=64)
    validity_date: date; line_items: list[LineItemRequest] = Field(..., min_length=1)
    currency: str = "EUR"; delivery_terms: str = "DAP"; payment_terms: str = "NET30"; notes: str = ""

class RejectQuoteRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)

class LineItemResponse(BaseModel):
    item_id: UUID; material_id: UUID; description: str; quantity: Decimal
    unit_of_measure: str; unit_price_eur: Decimal; total_price_eur: Decimal
    lead_time_days: int; notes: str

class QuoteResponse(BaseModel):
    id: UUID; rfq_id: UUID; supplier_id: UUID; quote_number: str
    validity_date: date; status: QuoteStatus; currency: str
    delivery_terms: str; payment_terms: str; total_eur: Decimal
    notes: str; rejection_reason: str; tenant_id: str
    line_items: list[LineItemResponse]

    @classmethod
    def from_domain(cls, q) -> "QuoteResponse":
        return cls(
            id=q.id, rfq_id=q.rfq_id, supplier_id=q.supplier_id, quote_number=q.quote_number,
            validity_date=q.validity_date, status=q.status, currency=q.currency,
            delivery_terms=q.delivery_terms, payment_terms=q.payment_terms, total_eur=q.total_eur,
            notes=q.notes, rejection_reason=q.rejection_reason, tenant_id=q.tenant_id,
            line_items=[LineItemResponse(item_id=li.item_id, material_id=li.material_id, description=li.description, quantity=li.quantity, unit_of_measure=li.unit_of_measure, unit_price_eur=li.unit_price_eur, total_price_eur=li.total_price_eur, lead_time_days=li.lead_time_days, notes=li.notes) for li in q.line_items],
        )

class QuoteListResponse(BaseModel):
    total: int; page: int; page_size: int; items: list[QuoteResponse]
