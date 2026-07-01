from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ....database import Base


class SupplierORM(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        Index("ix_suppliers_tenant_code", "tenant_id", "code", unique=True),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROSPECT")
    tier: Mapped[str] = mapped_column(String(8), nullable=False)
    payment_terms: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    contact_email: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    street: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(16), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    quality_ppm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_reliability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    price_competitiveness: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    financial_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
