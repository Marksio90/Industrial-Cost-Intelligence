from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.models import (
    Material, MaterialClass, MaterialNumber,
    MaterialStatus, PricePerUnit, UnitOfMeasure, Density,
)
from ..domain.exceptions import MaterialNotFound, MaterialNumberAlreadyExists
from .orm import MaterialORM


# ── Repository Interface (Port) ───────────────────────────────────────────────

class AbstractMaterialRepository(ABC):
    @abstractmethod
    async def get_by_id(self, material_id: UUID, tenant_id: str) -> Material:
        ...

    @abstractmethod
    async def get_by_number(self, number: str, tenant_id: str) -> Material | None:
        ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        material_class: MaterialClass | None = None,
        status: MaterialStatus | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Material], int]:
        ...

    @abstractmethod
    async def save(self, material: Material) -> Material:
        ...

    @abstractmethod
    async def exists_by_number(self, number: str, tenant_id: str) -> bool:
        ...


# ── SQLAlchemy Implementation (Adapter) ──────────────────────────────────────

class SqlAlchemyMaterialRepository(AbstractMaterialRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, material_id: UUID, tenant_id: str) -> Material:
        row = await self._session.get(MaterialORM, material_id)
        if row is None or row.tenant_id != tenant_id or row.is_deleted:
            raise MaterialNotFound(material_id)
        return self._to_domain(row)

    async def get_by_number(self, number: str, tenant_id: str) -> Material | None:
        stmt = select(MaterialORM).where(
            MaterialORM.material_number == number,
            MaterialORM.tenant_id == tenant_id,
            MaterialORM.is_deleted.is_(False),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def exists_by_number(self, number: str, tenant_id: str) -> bool:
        stmt = select(func.count()).select_from(MaterialORM).where(
            MaterialORM.material_number == number,
            MaterialORM.tenant_id == tenant_id,
            MaterialORM.is_deleted.is_(False),
        )
        count = (await self._session.execute(stmt)).scalar_one()
        return count > 0

    async def list(
        self,
        tenant_id: str,
        material_class: MaterialClass | None = None,
        status: MaterialStatus | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Material], int]:
        base = select(MaterialORM).where(
            MaterialORM.tenant_id == tenant_id,
            MaterialORM.is_deleted.is_(False),
        )
        if material_class:
            base = base.where(MaterialORM.material_class == material_class.value)
        if status:
            base = base.where(MaterialORM.status == status.value)
        if search:
            pattern = f"%{search}%"
            from sqlalchemy import or_
            base = base.where(
                or_(
                    MaterialORM.name.ilike(pattern),
                    MaterialORM.material_number.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        rows = (
            await self._session.execute(
                base.order_by(MaterialORM.material_number).offset(offset).limit(limit)
            )
        ).scalars().all()

        return [self._to_domain(r) for r in rows], total

    async def save(self, material: Material) -> Material:
        stmt = select(MaterialORM).where(MaterialORM.id == material.id)
        existing = (await self._session.execute(stmt)).scalar_one_or_none()

        if existing is None:
            # Check uniqueness before insert
            if await self.exists_by_number(str(material.material_number), material.tenant_id):
                raise MaterialNumberAlreadyExists(str(material.material_number))
            row = self._to_orm(material)
            self._session.add(row)
        else:
            self._update_orm(existing, material)

        await self._session.flush()
        return material

    # ── Mapping ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_domain(row: MaterialORM) -> Material:
        return Material(
            material_id=row.id,
            material_number=MaterialNumber(row.material_number),
            name=row.name,
            material_class=MaterialClass(row.material_class),
            unit_of_measure=UnitOfMeasure(row.unit_of_measure),
            base_price=PricePerUnit(row.base_price_eur),
            status=MaterialStatus(row.status),
            description=row.description,
            alloy=row.alloy,
            density=Density(row.density_g_cm3) if row.density_g_cm3 else None,
            min_order_qty=row.min_order_qty,
            lead_time_days=row.lead_time_days,
            tenant_id=row.tenant_id,
        )

    @staticmethod
    def _to_orm(material: Material) -> MaterialORM:
        return MaterialORM(
            id=material.id,
            tenant_id=material.tenant_id,
            material_number=str(material.material_number),
            name=material.name,
            material_class=material.material_class.value,
            unit_of_measure=material.unit_of_measure.value,
            base_price_eur=material.base_price.amount,
            status=material.status.value,
            description=material.description,
            alloy=material.alloy,
            density_g_cm3=material.density.value if material.density else None,
            min_order_qty=material.min_order_qty,
            lead_time_days=material.lead_time_days,
        )

    @staticmethod
    def _update_orm(row: MaterialORM, material: Material) -> None:
        row.name = material.name
        row.base_price_eur = material.base_price.amount
        row.status = material.status.value
        row.description = material.description
        row.alloy = material.alloy
        row.density_g_cm3 = material.density.value if material.density else None
        row.min_order_qty = material.min_order_qty
        row.lead_time_days = material.lead_time_days
