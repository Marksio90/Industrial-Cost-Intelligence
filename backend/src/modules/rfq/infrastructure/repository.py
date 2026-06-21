from __future__ import annotations
from abc import ABC, abstractmethod
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ..domain.exceptions import RFQNotFound
from ..domain.models import RFQ, RFQLineItem, RFQStatus
from .orm import RFQORM, RFQLineItemORM, RFQSupplierORM


class AbstractRFQRepository(ABC):
    @abstractmethod
    async def get_by_id(self, rfq_id: UUID, tenant_id: str) -> RFQ: ...
    @abstractmethod
    async def save(self, rfq: RFQ) -> RFQ: ...
    @abstractmethod
    async def list(self, tenant_id: str, status: RFQStatus | None, offset: int, limit: int) -> tuple[list[RFQ], int]: ...


class SqlAlchemyRFQRepository(AbstractRFQRepository):
    def __init__(self, session: AsyncSession) -> None: self._s = session

    def _q(self):
        return select(RFQORM).options(selectinload(RFQORM.line_items), selectinload(RFQORM.suppliers))

    async def get_by_id(self, rfq_id: UUID, tenant_id: str) -> RFQ:
        row = (await self._s.execute(self._q().where(RFQORM.id == rfq_id, RFQORM.tenant_id == tenant_id, RFQORM.is_deleted.is_(False)))).scalar_one_or_none()
        if not row: raise RFQNotFound(rfq_id)
        return self._map(row)

    async def save(self, rfq: RFQ) -> RFQ:
        row = (await self._s.execute(self._q().where(RFQORM.id == rfq.id))).scalar_one_or_none()
        if row is None:
            row = RFQORM(
                id=rfq.id, tenant_id=rfq.tenant_id, rfq_number=rfq.rfq_number,
                title=rfq.title, status=rfq.status.value, due_date=rfq.due_date,
                buyer_id=rfq.buyer_id, notes=rfq.notes,
                winning_supplier_id=rfq.winning_supplier_id,
            )
            self._s.add(row)
            await self._s.flush()
            for li in rfq.line_items:
                row.line_items.append(RFQLineItemORM(
                    id=li.line_id, rfq_id=rfq.id, material_id=li.material_id,
                    description=li.description, quantity=li.quantity,
                    unit_of_measure=li.unit_of_measure, target_price_eur=li.target_price_eur, notes=li.notes,
                ))
            for sid in rfq.supplier_ids:
                row.suppliers.append(RFQSupplierORM(rfq_id=rfq.id, supplier_id=sid))
        else:
            row.status = rfq.status.value
            row.winning_supplier_id = rfq.winning_supplier_id
        await self._s.flush()
        return rfq

    async def list(self, tenant_id: str, status: RFQStatus | None, offset: int, limit: int) -> tuple[list[RFQ], int]:
        base = self._q().where(RFQORM.tenant_id == tenant_id, RFQORM.is_deleted.is_(False))
        if status: base = base.where(RFQORM.status == status.value)
        total = (await self._s.execute(select(func.count()).select_from(select(RFQORM).where(RFQORM.tenant_id == tenant_id).subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(RFQORM.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        return [self._map(r) for r in rows], total

    @staticmethod
    def _map(r: RFQORM) -> RFQ:
        return RFQ(
            rfq_id=r.id, rfq_number=r.rfq_number, title=r.title,
            line_items=[RFQLineItem(li.id, li.material_id, li.description, li.quantity, li.unit_of_measure, li.target_price_eur, li.notes) for li in r.line_items],
            supplier_ids=[s.supplier_id for s in r.suppliers],
            due_date=r.due_date, status=RFQStatus(r.status), tenant_id=r.tenant_id,
            buyer_id=r.buyer_id, notes=r.notes, winning_supplier_id=r.winning_supplier_id,
        )
