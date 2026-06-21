from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from ..domain.models import CostCategory, CostStatus


class ComponentRequest(BaseModel):
    category: CostCategory
    amount_eur: Decimal = Field(..., ge=0)
    description: str = Field(default="", max_length=512)


class CreateBreakdownRequest(BaseModel):
    bom_item_id: UUID
    reference_number: str = Field(..., min_length=1, max_length=128)
    quantity: int = Field(..., ge=1)
    components: list[ComponentRequest] = Field(..., min_length=1)
    location: str = Field(default="DE", max_length=4)
    currency: str = Field(default="EUR", max_length=3)
    notes: str = Field(default="", max_length=2000)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


class ReviseRequest(BaseModel):
    components: list[ComponentRequest] = Field(..., min_length=1)
    notes: str = Field(default="", max_length=2000)


class ComponentResponse(BaseModel):
    category: CostCategory
    amount_eur: Decimal
    share_pct: Decimal
    description: str


class CostBreakdownResponse(BaseModel):
    id: UUID
    bom_item_id: UUID
    reference_number: str
    quantity: int
    status: CostStatus
    location: str
    currency: str
    total_cost_eur: Decimal
    unit_cost_eur: Decimal
    notes: str
    version: int
    tenant_id: str
    components: list[ComponentResponse]

    @classmethod
    def from_domain(cls, cb) -> "CostBreakdownResponse":
        return cls(
            id=cb.id, bom_item_id=cb.bom_item_id,
            reference_number=cb.reference_number, quantity=cb.quantity,
            status=cb.status, location=cb.location, currency=cb.currency,
            total_cost_eur=cb.total_cost_eur, unit_cost_eur=cb.unit_cost_eur,
            notes=cb.notes, version=cb.version, tenant_id=cb.tenant_id,
            components=[
                ComponentResponse(
                    category=c.category, amount_eur=c.amount_eur,
                    share_pct=c.share_pct, description=c.description,
                )
                for c in cb.components
            ],
        )


class CostListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[CostBreakdownResponse]
