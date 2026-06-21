from __future__ import annotations
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ....database import Base


class ProcessORM(Base):
    __tablename__ = "processes"
    __table_args__ = (
        Index("ix_processes_tenant_code", "tenant_id", "code", unique=True),
        {"schema": "ici"},
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    process_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    setup_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_type: Mapped[str] = mapped_column(String(64), nullable=False)
    machine_rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    machine_location: Mapped[str] = mapped_column(String(4), nullable=False, default="DE")
    skill_level: Mapped[str] = mapped_column(String(16), nullable=False)
    labor_rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    labor_location: Mapped[str] = mapped_column(String(4), nullable=False, default="DE")
    scrap_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.02"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
