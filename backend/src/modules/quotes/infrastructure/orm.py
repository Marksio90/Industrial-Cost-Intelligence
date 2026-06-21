from __future__ import annotations
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ....database import Base


class QuoteORM(Base):
    __tablename__ = "quotes"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rfq_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    supplier_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    quote_number: Mapped[str] = mapped_column(String(64), nullable=False)
    validity_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RECEIVED")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    delivery_terms: Mapped[str] = mapped_column(String(16), nullable=False, default="DAP")
    payment_terms: Mapped[str] = mapped_column(String(8), nullable=False, default="NET30")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rejection_reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    line_items: Mapped[list["QuoteLineItemORM"]] = relationship("QuoteLineItemORM", back_populates="quote", cascade="all, delete-orphan")


class QuoteLineItemORM(Base):
    __tablename__ = "quote_line_items"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    quote_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ici.quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    material_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_price_eur: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_price_eur: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    quote: Mapped["QuoteORM"] = relationship("QuoteORM", back_populates="line_items")
