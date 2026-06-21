from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4
from ..domain.models import Quote, QuoteLineItem, QuoteStatus
from ..infrastructure.repository import AbstractQuoteRepository


@dataclass
class LineItemInput:
    material_id: UUID; description: str; quantity: Decimal; unit_of_measure: str
    unit_price_eur: Decimal; lead_time_days: int; notes: str = ""


@dataclass
class CreateQuoteCommand:
    rfq_id: UUID; supplier_id: UUID; quote_number: str; validity_date: date
    line_items: list[LineItemInput]; tenant_id: str
    currency: str = "EUR"; delivery_terms: str = "DAP"
    payment_terms: str = "NET30"; notes: str = ""


class QuoteService:
    def __init__(self, repo: AbstractQuoteRepository) -> None: self._repo = repo

    async def create(self, cmd: CreateQuoteCommand) -> Quote:
        items = [QuoteLineItem(uuid4(), li.material_id, li.description, li.quantity, li.unit_of_measure, li.unit_price_eur, (li.unit_price_eur * li.quantity).quantize(Decimal("0.0001")), li.lead_time_days, li.notes) for li in cmd.line_items]
        q = Quote.create(rfq_id=cmd.rfq_id, supplier_id=cmd.supplier_id, quote_number=cmd.quote_number, validity_date=cmd.validity_date, line_items=items, tenant_id=cmd.tenant_id, currency=cmd.currency, delivery_terms=cmd.delivery_terms, payment_terms=cmd.payment_terms, notes=cmd.notes)
        return await self._repo.save(q)

    async def get(self, qid: UUID, tenant_id: str) -> Quote: return await self._repo.get_by_id(qid, tenant_id)
    async def list(self, tenant_id: str, rfq_id: UUID | None = None, supplier_id: UUID | None = None, status: QuoteStatus | None = None, offset: int = 0, limit: int = 20) -> tuple[list[Quote], int]:
        return await self._repo.list(tenant_id, rfq_id, supplier_id, status, offset, limit)
    async def accept(self, qid: UUID, tenant_id: str) -> Quote:
        q = await self._repo.get_by_id(qid, tenant_id); q.accept(); return await self._repo.save(q)
    async def reject(self, qid: UUID, tenant_id: str, reason: str) -> Quote:
        q = await self._repo.get_by_id(qid, tenant_id); q.reject(reason); return await self._repo.save(q)
