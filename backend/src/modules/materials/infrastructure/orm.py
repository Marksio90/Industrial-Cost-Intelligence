from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime, ForeignKey, Index, Numeric, String, Text, Boolean,
    Integer, func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ....database import Base


class MaterialORM(Base):
    __tablename__ = "materials"
    __table_args__ = (
        Index("ix_materials_tenant_number", "tenant_id", "material_number", unique=True),
        Index("ix_materials_tenant_class",  "tenant_id", "material_class"),
        Index("ix_materials_tenant_status", "tenant_id", "status"),
        {"schema": "ici"},
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_number: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    material_class: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(8), nullable=False)
    base_price_eur: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    alloy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    density_g_cm3: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    min_order_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("1"))
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
