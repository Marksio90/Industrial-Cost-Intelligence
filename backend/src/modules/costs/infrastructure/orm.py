from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ....database import Base


class CostBreakdownORM(Base):
    __tablename__ = "cost_breakdowns"
    __table_args__ = (
        Index("ix_cb_tenant_ref", "tenant_id", "reference_number"),
        Index("ix_cb_tenant_bom",  "tenant_id", "bom_item_id"),
        {"schema": "ici"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bom_item_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reference_number: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    location: Mapped[str] = mapped_column(String(4), nullable=False, default="DE")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    components: Mapped[list["CostComponentORM"]] = relationship(
        "CostComponentORM", back_populates="breakdown", cascade="all, delete-orphan"
    )


class CostComponentORM(Base):
    __tablename__ = "cost_components"
    __table_args__ = ({"schema": "ici"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    breakdown_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ici.cost_breakdowns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    share_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal(0))
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    breakdown: Mapped["CostBreakdownORM"] = relationship(
        "CostBreakdownORM", back_populates="components"
    )
