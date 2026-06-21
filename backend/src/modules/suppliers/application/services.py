from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ..domain.models import (
    Address, PaymentTerms, Supplier, SupplierScore,
    SupplierStatus, SupplierTier,
)
from ..infrastructure.repository import AbstractSupplierRepository


@dataclass
class CreateSupplierCommand:
    code: str
    name: str
    street: str
    city: str
    postal_code: str
    country_code: str
    tier: SupplierTier
    payment_terms: PaymentTerms
    currency: str
    lead_time_days: int
    tenant_id: str
    contact_email: str = ""
    contact_phone: str = ""
    tax_id: str = ""


@dataclass
class UpdateScoreCommand:
    supplier_id: UUID
    tenant_id: str
    quality_ppm: int
    delivery_reliability: Decimal
    price_competitiveness: Decimal
    financial_score: Decimal


class SupplierService:
    def __init__(self, repo: AbstractSupplierRepository) -> None:
        self._repo = repo

    async def create(self, cmd: CreateSupplierCommand) -> Supplier:
        supplier = Supplier.create(
            code=cmd.code, name=cmd.name,
            address=Address(cmd.street, cmd.city, cmd.postal_code, cmd.country_code),
            tier=cmd.tier, payment_terms=cmd.payment_terms,
            currency=cmd.currency, lead_time_days=cmd.lead_time_days,
            tenant_id=cmd.tenant_id, contact_email=cmd.contact_email,
            contact_phone=cmd.contact_phone, tax_id=cmd.tax_id,
        )
        return await self._repo.save(supplier)

    async def get(self, supplier_id: UUID, tenant_id: str) -> Supplier:
        return await self._repo.get_by_id(supplier_id, tenant_id)

    async def list(
        self, tenant_id: str, status: SupplierStatus | None = None,
        search: str | None = None, offset: int = 0, limit: int = 20,
    ) -> tuple[list[Supplier], int]:
        return await self._repo.list(tenant_id, status, search, offset, limit)

    async def qualify(self, supplier_id: UUID, tenant_id: str) -> Supplier:
        s = await self._repo.get_by_id(supplier_id, tenant_id)
        s.qualify()
        return await self._repo.save(s)

    async def suspend(self, supplier_id: UUID, tenant_id: str, reason: str) -> Supplier:
        s = await self._repo.get_by_id(supplier_id, tenant_id)
        s.suspend(reason)
        return await self._repo.save(s)

    async def update_score(self, cmd: UpdateScoreCommand) -> Supplier:
        s = await self._repo.get_by_id(cmd.supplier_id, cmd.tenant_id)
        s.update_score(SupplierScore(
            quality_ppm=cmd.quality_ppm,
            delivery_reliability=cmd.delivery_reliability,
            price_competitiveness=cmd.price_competitiveness,
            financial_score=cmd.financial_score,
        ))
        return await self._repo.save(s)
