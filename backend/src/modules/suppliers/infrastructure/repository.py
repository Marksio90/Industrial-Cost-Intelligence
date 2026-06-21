from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.exceptions import SupplierCodeExists, SupplierNotFound
from ..domain.models import (
    Address, PaymentTerms, Supplier, SupplierScore,
    SupplierStatus, SupplierTier,
)
from .orm import SupplierORM


class AbstractSupplierRepository(ABC):
    @abstractmethod
    async def get_by_id(self, supplier_id: UUID, tenant_id: str) -> Supplier: ...
    @abstractmethod
    async def save(self, supplier: Supplier) -> Supplier: ...
    @abstractmethod
    async def list(
        self, tenant_id: str, status: SupplierStatus | None,
        search: str | None, offset: int, limit: int
    ) -> tuple[list[Supplier], int]: ...


class SqlAlchemySupplierRepository(AbstractSupplierRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, supplier_id: UUID, tenant_id: str) -> Supplier:
        row = await self._s.get(SupplierORM, supplier_id)
        if not row or row.tenant_id != tenant_id or row.is_deleted:
            raise SupplierNotFound(supplier_id)
        return self._map(row)

    async def save(self, supplier: Supplier) -> Supplier:
        row = (await self._s.execute(
            select(SupplierORM).where(SupplierORM.id == supplier.id)
        )).scalar_one_or_none()
        if row is None:
            exists = (await self._s.execute(
                select(func.count()).select_from(SupplierORM).where(
                    SupplierORM.code == supplier.code,
                    SupplierORM.tenant_id == supplier.tenant_id,
                    SupplierORM.is_deleted.is_(False),
                )
            )).scalar_one()
            if exists:
                raise SupplierCodeExists(supplier.code)
            row = SupplierORM(
                id=supplier.id, tenant_id=supplier.tenant_id, code=supplier.code,
                name=supplier.name, street=supplier.address.street,
                city=supplier.address.city, postal_code=supplier.address.postal_code,
                country_code=supplier.address.country_code, tier=supplier.tier.value,
                payment_terms=supplier.payment_terms.value, currency=supplier.currency,
                lead_time_days=supplier.lead_time_days, contact_email=supplier.contact_email,
                contact_phone=supplier.contact_phone, tax_id=supplier.tax_id,
                status=supplier.status.value,
            )
            self._s.add(row)
        else:
            row.name = supplier.name
            row.status = supplier.status.value
            row.tier = supplier.tier.value
            row.lead_time_days = supplier.lead_time_days
            row.contact_email = supplier.contact_email
            row.contact_phone = supplier.contact_phone
            if supplier.score:
                row.quality_ppm = supplier.score.quality_ppm
                row.delivery_reliability = supplier.score.delivery_reliability
                row.price_competitiveness = supplier.score.price_competitiveness
                row.financial_score = supplier.score.financial_score
        await self._s.flush()
        return supplier

    async def list(
        self, tenant_id: str, status: SupplierStatus | None,
        search: str | None, offset: int, limit: int
    ) -> tuple[list[Supplier], int]:
        base = select(SupplierORM).where(
            SupplierORM.tenant_id == tenant_id,
            SupplierORM.is_deleted.is_(False),
        )
        if status:
            base = base.where(SupplierORM.status == status.value)
        if search:
            from sqlalchemy import or_
            p = f"%{search}%"
            base = base.where(or_(SupplierORM.name.ilike(p), SupplierORM.code.ilike(p)))
        total = (await self._s.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()
        rows = (await self._s.execute(
            base.order_by(SupplierORM.code).offset(offset).limit(limit)
        )).scalars().all()
        return [self._map(r) for r in rows], total

    @staticmethod
    def _map(row: SupplierORM) -> Supplier:
        score = None
        if row.quality_ppm is not None:
            score = SupplierScore(
                quality_ppm=row.quality_ppm,
                delivery_reliability=row.delivery_reliability or Decimal(0),
                price_competitiveness=row.price_competitiveness or Decimal(0),
                financial_score=row.financial_score or Decimal(0),
            )
        return Supplier(
            supplier_id=row.id, code=row.code, name=row.name,
            address=Address(row.street, row.city, row.postal_code, row.country_code),
            tier=SupplierTier(row.tier), status=SupplierStatus(row.status),
            payment_terms=PaymentTerms(row.payment_terms), currency=row.currency,
            lead_time_days=row.lead_time_days, tenant_id=row.tenant_id,
            contact_email=row.contact_email, contact_phone=row.contact_phone,
            tax_id=row.tax_id, score=score,
        )
