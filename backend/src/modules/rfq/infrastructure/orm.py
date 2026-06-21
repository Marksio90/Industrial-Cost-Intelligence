from __future__ import annotations
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ....database import Base


class RFQORM(Base):
    __tablename__ = "rfqs"
    __table_args__ = (
        Index("ix_rfqs_tenant_number", "tenant_id", "rfq_number", unique=True),
        {"schema": "ici"},
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rfq_number: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    winning_supplier_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    line_items: Mapped[list["RFQLineItemORM"]] = relationship("RFQLineItemORM", back_populates="rfq", cascade="all, delete-orphan")
    suppliers: Mapped[list["RFQSupplierORM"]] = relationship("RFQSupplierORM", back_populates="rfq", cascade="all, delete-orphan")


class RFQLineItemORM(Base):
    __tablename__ = "rfq_line_items"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    rfq_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ici.rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    material_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(8), nullable=False)
    target_price_eur: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    notes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    rfq: Mapped["RFQORM"] = relationship("RFQORM", back_populates="line_items")


class RFQSupplierORM(Base):
    __tablename__ = "rfq_suppliers"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    rfq_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ici.rfqs.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    rfq: Mapped["RFQORM"] = relationship("RFQORM", back_populates="suppliers")
