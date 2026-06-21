from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ..domain.models import CostBreakdown, CostCategory, CostComponent, CostStatus
from ..infrastructure.repository import AbstractCostRepository


@dataclass
class ComponentInput:
    category: CostCategory
    amount_eur: Decimal
    description: str = ""


@dataclass
class CreateBreakdownCommand:
    bom_item_id: UUID
    reference_number: str
    quantity: int
    components: list[ComponentInput]
    tenant_id: str
    location: str = "DE"
    currency: str = "EUR"
    notes: str = ""


class CostService:
    def __init__(self, repo: AbstractCostRepository) -> None:
        self._repo = repo

    async def create(self, cmd: CreateBreakdownCommand) -> CostBreakdown:
        components = [
            CostComponent(category=c.category, amount_eur=c.amount_eur, description=c.description)
            for c in cmd.components
        ]
        cb = CostBreakdown.create(
            bom_item_id=cmd.bom_item_id,
            reference_number=cmd.reference_number,
            quantity=cmd.quantity,
            components=components,
            tenant_id=cmd.tenant_id,
            location=cmd.location,
            currency=cmd.currency,
            notes=cmd.notes,
        )
        return await self._repo.save(cb)

    async def get(self, breakdown_id: UUID, tenant_id: str) -> CostBreakdown:
        return await self._repo.get_by_id(breakdown_id, tenant_id)

    async def list(
        self, tenant_id: str, bom_item_id: UUID | None = None,
        status: CostStatus | None = None, offset: int = 0, limit: int = 20,
    ) -> tuple[list[CostBreakdown], int]:
        return await self._repo.list(tenant_id, bom_item_id, status, offset, limit)

    async def approve(self, breakdown_id: UUID, tenant_id: str, approver: str) -> CostBreakdown:
        cb = await self._repo.get_by_id(breakdown_id, tenant_id)
        cb.approve(approver)
        return await self._repo.save(cb)

    async def reject(self, breakdown_id: UUID, tenant_id: str, reason: str) -> CostBreakdown:
        cb = await self._repo.get_by_id(breakdown_id, tenant_id)
        cb.reject(reason)
        return await self._repo.save(cb)

    async def revise(
        self, breakdown_id: UUID, tenant_id: str,
        new_components: list[ComponentInput], notes: str = "",
    ) -> CostBreakdown:
        cb = await self._repo.get_by_id(breakdown_id, tenant_id)
        components = [
            CostComponent(category=c.category, amount_eur=c.amount_eur, description=c.description)
            for c in new_components
        ]
        revised = cb.revise(components, notes)
        return await self._repo.save(revised)
