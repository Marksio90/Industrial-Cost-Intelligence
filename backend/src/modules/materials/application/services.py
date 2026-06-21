from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import structlog

from ..domain.models import Material, MaterialClass, MaterialStatus, UnitOfMeasure
from ..infrastructure.repository import AbstractMaterialRepository

logger = structlog.get_logger(__name__)


@dataclass
class CreateMaterialCommand:
    material_number: str
    name: str
    material_class: MaterialClass
    unit_of_measure: UnitOfMeasure
    base_price_eur: Decimal
    description: str = ""
    alloy: str | None = None
    density_g_cm3: Decimal | None = None
    min_order_qty: Decimal = Decimal("1")
    lead_time_days: int = 0
    tenant_id: str = "default"
    created_by: str | None = None


@dataclass
class UpdateMaterialCommand:
    material_id: UUID
    tenant_id: str
    name: str | None = None
    description: str | None = None
    lead_time_days: int | None = None
    min_order_qty: Decimal | None = None
    updated_by: str | None = None


@dataclass
class UpdatePriceCommand:
    material_id: UUID
    tenant_id: str
    new_price_eur: Decimal
    updated_by: str | None = None


@dataclass
class ListMaterialsQuery:
    tenant_id: str
    material_class: MaterialClass | None = None
    status: MaterialStatus | None = None
    search: str | None = None
    offset: int = 0
    limit: int = 20


@dataclass
class MaterialResult:
    total: int
    items: list[Material]


class MaterialService:
    def __init__(self, repository: AbstractMaterialRepository) -> None:
        self._repo = repository

    async def create(self, cmd: CreateMaterialCommand) -> Material:
        logger.info(
            "creating_material",
            number=cmd.material_number,
            tenant_id=cmd.tenant_id,
        )
        material = Material.create(
            material_number=cmd.material_number,
            name=cmd.name,
            material_class=cmd.material_class,
            unit_of_measure=cmd.unit_of_measure,
            base_price_eur=cmd.base_price_eur,
            description=cmd.description,
            alloy=cmd.alloy,
            density_g_cm3=cmd.density_g_cm3,
            min_order_qty=cmd.min_order_qty,
            lead_time_days=cmd.lead_time_days,
            tenant_id=cmd.tenant_id,
        )
        saved = await self._repo.save(material)
        events = saved.pop_events()
        logger.info("material_created", material_id=str(saved.id), events=len(events))
        return saved

    async def get_by_id(self, material_id: UUID, tenant_id: str) -> Material:
        return await self._repo.get_by_id(material_id, tenant_id)

    async def list(self, query: ListMaterialsQuery) -> MaterialResult:
        items, total = await self._repo.list(
            tenant_id=query.tenant_id,
            material_class=query.material_class,
            status=query.status,
            search=query.search,
            offset=query.offset,
            limit=query.limit,
        )
        return MaterialResult(total=total, items=items)

    async def update(self, cmd: UpdateMaterialCommand) -> Material:
        material = await self._repo.get_by_id(cmd.material_id, cmd.tenant_id)
        material.update_details(
            name=cmd.name,
            description=cmd.description,
            lead_time_days=cmd.lead_time_days,
            min_order_qty=cmd.min_order_qty,
        )
        return await self._repo.save(material)

    async def update_price(self, cmd: UpdatePriceCommand) -> Material:
        material = await self._repo.get_by_id(cmd.material_id, cmd.tenant_id)
        material.update_price(cmd.new_price_eur)
        saved = await self._repo.save(material)
        events = saved.pop_events()
        logger.info(
            "material_price_updated",
            material_id=str(cmd.material_id),
            new_price=str(cmd.new_price_eur),
        )
        return saved

    async def deprecate(self, material_id: UUID, tenant_id: str, reason: str) -> Material:
        material = await self._repo.get_by_id(material_id, tenant_id)
        material.deprecate(reason)
        return await self._repo.save(material)

    async def reactivate(self, material_id: UUID, tenant_id: str) -> Material:
        material = await self._repo.get_by_id(material_id, tenant_id)
        material.reactivate()
        return await self._repo.save(material)
