from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.models import MaterialClass, MaterialStatus, UnitOfMeasure


class MaterialBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=256, examples=["Steel S355"])
    material_class: MaterialClass
    unit_of_measure: UnitOfMeasure
    base_price_eur: Decimal = Field(..., ge=0, decimal_places=4)
    description: str = Field(default="", max_length=2000)
    alloy: str | None = Field(default=None, max_length=64)
    density_g_cm3: Decimal | None = Field(default=None, gt=0)
    min_order_qty: Decimal = Field(default=Decimal("1"), gt=0)
    lead_time_days: int = Field(default=0, ge=0)


class CreateMaterialRequest(MaterialBase):
    material_number: str = Field(
        ..., min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        examples=["STEEL-S355-3MM"],
    )

    @field_validator("base_price_eur")
    @classmethod
    def validate_price(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Price cannot be negative")
        return v


class UpdateMaterialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    lead_time_days: int | None = Field(default=None, ge=0)
    min_order_qty: Decimal | None = Field(default=None, gt=0)


class UpdatePriceRequest(BaseModel):
    base_price_eur: Decimal = Field(..., ge=0, decimal_places=4)


class DeprecateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


class MaterialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    material_number: str
    name: str
    material_class: MaterialClass
    unit_of_measure: UnitOfMeasure
    base_price_eur: Decimal
    status: MaterialStatus
    description: str
    alloy: str | None
    density_g_cm3: Decimal | None
    min_order_qty: Decimal
    lead_time_days: int
    tenant_id: str
    is_active: bool

    @classmethod
    def from_domain(cls, m) -> "MaterialResponse":
        return cls(
            id=m.id,
            material_number=str(m.material_number),
            name=m.name,
            material_class=m.material_class,
            unit_of_measure=m.unit_of_measure,
            base_price_eur=m.base_price.amount,
            status=m.status,
            description=m.description,
            alloy=m.alloy,
            density_g_cm3=m.density.value if m.density else None,
            min_order_qty=m.min_order_qty,
            lead_time_days=m.lead_time_days,
            tenant_id=m.tenant_id,
            is_active=m.is_active,
        )


class MaterialListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MaterialResponse]
