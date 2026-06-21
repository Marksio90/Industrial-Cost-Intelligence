from __future__ import annotations
from abc import ABC, abstractmethod
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ..domain.exceptions import QuoteNotFound
from ..domain.models import Quote, QuoteLineItem, QuoteStatus
from .orm import QuoteORM, QuoteLineItemORM


class AbstractQuoteRepository(ABC):
    @abstractmethod
    async def get_by_id(self, qid: UUID, tenant_id: str) -> Quote: ...
    @abstractmethod
    async def save(self, q: Quote) -> Quote: ...
    @abstractmethod
    async def list(self, tenant_id: str, rfq_id: UUID | None, supplier_id: UUID | None, status: QuoteStatus | None, offset: int, limit: int) -> tuple[list[Quote], int]: ...


class SqlAlchemyQuoteRepository(AbstractQuoteRepository):
    def __init__(self, session: AsyncSession) -> None: self._s = session

    def _q(self): return select(QuoteORM).options(selectinload(QuoteORM.line_items))

    async def get_by_id(self, qid: UUID, tenant_id: str) -> Quote:
        row = (await self._s.execute(self._q().where(QuoteORM.id == qid, QuoteORM.tenant_id == tenant_id, QuoteORM.is_deleted.is_(False)))).scalar_one_or_none()
        if not row: raise QuoteNotFound(qid)
        return self._map(row)

    async def save(self, q: Quote) -> Quote:
        row = (await self._s.execute(self._q().where(QuoteORM.id == q.id))).scalar_one_or_none()
        if row is None:
            row = QuoteORM(
                id=q.id, tenant_id=q.tenant_id, rfq_id=q.rfq_id, supplier_id=q.supplier_id,
                quote_number=q.quote_number, validity_date=q.validity_date, status=q.status.value,
                currency=q.currency, delivery_terms=q.delivery_terms, payment_terms=q.payment_terms,
                notes=q.notes,
            )
            self._s.add(row)
            await self._s.flush()
            for li in q.line_items:
                row.line_items.append(QuoteLineItemORM(
                    id=li.item_id, quote_id=q.id, material_id=li.material_id, description=li.description,
                    quantity=li.quantity, unit_of_measure=li.unit_of_measure,
                    unit_price_eur=li.unit_price_eur, total_price_eur=li.total_price_eur,
                    lead_time_days=li.lead_time_days, notes=li.notes,
                ))
        else:
            row.status = q.status.value; row.rejection_reason = q.rejection_reason
        await self._s.flush()
        return q

    async def list(self, tenant_id: str, rfq_id: UUID | None, supplier_id: UUID | None, status: QuoteStatus | None, offset: int, limit: int) -> tuple[list[Quote], int]:
        base = self._q().where(QuoteORM.tenant_id == tenant_id, QuoteORM.is_deleted.is_(False))
        if rfq_id: base = base.where(QuoteORM.rfq_id == rfq_id)
        if supplier_id: base = base.where(QuoteORM.supplier_id == supplier_id)
        if status: base = base.where(QuoteORM.status == status.value)
        total = (await self._s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(QuoteORM.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        return [self._map(r) for r in rows], total

    @staticmethod
    def _map(r: QuoteORM) -> Quote:
        return Quote(
            quote_id=r.id, rfq_id=r.rfq_id, supplier_id=r.supplier_id, quote_number=r.quote_number,
            validity_date=r.validity_date, status=QuoteStatus(r.status), tenant_id=r.tenant_id,
            currency=r.currency, delivery_terms=r.delivery_terms, payment_terms=r.payment_terms,
            notes=r.notes, rejection_reason=r.rejection_reason,
            line_items=[QuoteLineItem(li.id, li.material_id, li.description, li.quantity, li.unit_of_measure, li.unit_price_eur, li.total_price_eur, li.lead_time_days, li.notes) for li in r.line_items],
        )
