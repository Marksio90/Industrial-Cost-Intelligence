from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, EmailStr

from ..domain.models import PaymentTerms, SupplierStatus, SupplierTier


class AddressSchema(BaseModel):
    street: str = Field(..., min_length=1, max_length=256)
    city: str = Field(..., min_length=1, max_length=128)
    postal_code: str = Field(..., min_length=1, max_length=16)
    country_code: str = Field(..., min_length=2, max_length=2, pattern="^[A-Z]{2}$")


class SupplierScoreSchema(BaseModel):
    quality_ppm: int = Field(..., ge=0, le=1_000_000)
    delivery_reliability: Decimal = Field(..., ge=0, le=1)
    price_competitiveness: Decimal = Field(..., ge=0, le=1)
    financial_score: Decimal = Field(..., ge=0, le=10)
    overall: Decimal | None = None


class CreateSupplierRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=256)
    address: AddressSchema
    tier: SupplierTier
    payment_terms: PaymentTerms
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    lead_time_days: int = Field(default=30, ge=0)
    contact_email: str = Field(default="", max_length=256)
    contact_phone: str = Field(default="", max_length=32)
    tax_id: str = Field(default="", max_length=64)


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    address: AddressSchema
    tier: SupplierTier
    status: SupplierStatus
    payment_terms: PaymentTerms
    currency: str
    lead_time_days: int
    contact_email: str
    contact_phone: str
    tax_id: str
    tenant_id: str
    is_active: bool
    score: SupplierScoreSchema | None

    @classmethod
    def from_domain(cls, s) -> "SupplierResponse":
        score = None
        if s.score:
            score = SupplierScoreSchema(
                quality_ppm=s.score.quality_ppm,
                delivery_reliability=s.score.delivery_reliability,
                price_competitiveness=s.score.price_competitiveness,
                financial_score=s.score.financial_score,
                overall=s.score.overall,
            )
        return cls(
            id=s.id, code=s.code, name=s.name,
            address=AddressSchema(
                street=s.address.street, city=s.address.city,
                postal_code=s.address.postal_code, country_code=s.address.country_code,
            ),
            tier=s.tier, status=s.status, payment_terms=s.payment_terms,
            currency=s.currency, lead_time_days=s.lead_time_days,
            contact_email=s.contact_email, contact_phone=s.contact_phone,
            tax_id=s.tax_id, tenant_id=s.tenant_id, is_active=s.is_active, score=score,
        )


class SupplierListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SupplierResponse]
