from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domain.exceptions import CostBreakdownNotFound
from ..domain.models import CostBreakdown, CostCategory, CostComponent, CostStatus
from .orm import CostBreakdownORM, CostComponentORM


class AbstractCostRepository(ABC):
    @abstractmethod
    async def get_by_id(self, breakdown_id: UUID, tenant_id: str) -> CostBreakdown: ...
    @abstractmethod
    async def save(self, cb: CostBreakdown) -> CostBreakdown: ...
    @abstractmethod
    async def list(
        self, tenant_id: str, bom_item_id: UUID | None,
        status: CostStatus | None, offset: int, limit: int
    ) -> tuple[list[CostBreakdown], int]: ...


class SqlAlchemyCostRepository(AbstractCostRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, breakdown_id: UUID, tenant_id: str) -> CostBreakdown:
        stmt = (
            select(CostBreakdownORM)
            .options(selectinload(CostBreakdownORM.components))
            .where(
                CostBreakdownORM.id == breakdown_id,
                CostBreakdownORM.tenant_id == tenant_id,
                CostBreakdownORM.is_deleted.is_(False),
            )
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if not row:
            raise CostBreakdownNotFound(breakdown_id)
        return self._map(row)

    async def save(self, cb: CostBreakdown) -> CostBreakdown:
        stmt = (
            select(CostBreakdownORM)
            .options(selectinload(CostBreakdownORM.components))
            .where(CostBreakdownORM.id == cb.id)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = CostBreakdownORM(
                id=cb.id, tenant_id=cb.tenant_id,
                bom_item_id=cb.bom_item_id,
                reference_number=cb.reference_number,
                quantity=cb.quantity, status=cb.status.value,
                location=cb.location, currency=cb.currency,
                notes=cb.notes, version=cb.version,
            )
            self._s.add(row)
        else:
            row.status = cb.status.value
            row.notes = cb.notes
            row.version = cb.version
            # Delete old components
            for c in row.components:
                await self._s.delete(c)
            await self._s.flush()

        for comp in cb.components:
            row.components.append(
                CostComponentORM(
                    breakdown_id=cb.id,
                    category=comp.category.value,
                    amount_eur=comp.amount_eur,
                    share_pct=comp.share_pct,
                    description=comp.description,
                )
            )
        await self._s.flush()
        return cb

    async def list(
        self, tenant_id: str, bom_item_id: UUID | None,
        status: CostStatus | None, offset: int, limit: int
    ) -> tuple[list[CostBreakdown], int]:
        base = (
            select(CostBreakdownORM)
            .options(selectinload(CostBreakdownORM.components))
            .where(
                CostBreakdownORM.tenant_id == tenant_id,
                CostBreakdownORM.is_deleted.is_(False),
            )
        )
        if bom_item_id:
            base = base.where(CostBreakdownORM.bom_item_id == bom_item_id)
        if status:
            base = base.where(CostBreakdownORM.status == status.value)
        total = (await self._s.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()
        rows = (await self._s.execute(
            base.order_by(CostBreakdownORM.created_at.desc()).offset(offset).limit(limit)
        )).scalars().all()
        return [self._map(r) for r in rows], total

    @staticmethod
    def _map(row: CostBreakdownORM) -> CostBreakdown:
        components = [
            CostComponent(
                category=CostCategory(c.category),
                amount_eur=c.amount_eur,
                share_pct=c.share_pct,
                description=c.description,
            )
            for c in row.components
        ]
        return CostBreakdown(
            breakdown_id=row.id,
            bom_item_id=row.bom_item_id,
            reference_number=row.reference_number,
            quantity=row.quantity,
            components=components,
            status=CostStatus(row.status),
            tenant_id=row.tenant_id,
            location=row.location,
            currency=row.currency,
            notes=row.notes,
            version=row.version,
        )
