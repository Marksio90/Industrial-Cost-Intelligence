from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ....database import Base


class RiskItemORM(Base):
    __tablename__ = "risk_items"
    __table_args__ = ()
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    probability: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    impact: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    detectability: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    estimated_cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cost_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    impact_area: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN", index=True)
    affected_material_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    affected_supplier_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    affected_rfq_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    mitigation_actions: Mapped[list["MitigationActionORM"]] = relationship("MitigationActionORM", back_populates="risk", cascade="all, delete-orphan")


class MitigationActionORM(Base):
    __tablename__ = "mitigation_actions"
    __table_args__ = ()
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    risk_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("risk_items.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    risk: Mapped["RiskItemORM"] = relationship("RiskItemORM", back_populates="mitigation_actions")
