from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AgentRunORM(Base):
    __tablename__ = "agent_runs"
    __table_args__ = {"schema": "ici"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list[AgentStepORM]] = relationship(back_populates="run", cascade="all, delete-orphan")
    rfq_jobs: Mapped[list[RFQJobORM]] = relationship(back_populates="run")


class AgentStepORM(Base):
    __tablename__ = "agent_steps"
    __table_args__ = {"schema": "ici"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ici.agent_runs.id", ondelete="CASCADE")
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    thought: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action_input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[AgentRunORM] = relationship(back_populates="steps")


class DiscoveredSupplierORM(Base):
    __tablename__ = "discovered_suppliers"
    __table_args__ = (
        Index("ix_disc_sup_email", "email"),
        Index("ix_disc_sup_domain", "domain"),
        {"schema": "ici"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    meta_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String(64), default="scraper")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RFQJobORM(Base):
    __tablename__ = "rfq_agent_jobs"
    __table_args__ = {"schema": "ici"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ici.agent_runs.id"), nullable=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rfq_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    emails_sent: Mapped[int] = mapped_column(Integer, default=0)
    responses_received: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentRunORM | None] = relationship(back_populates="rfq_jobs")
    emails: Mapped[list[SentEmailORM]] = relationship(back_populates="job", cascade="all, delete-orphan")
    quotes: Mapped[list[ParsedQuoteORM]] = relationship(back_populates="job")


class SentEmailORM(Base):
    __tablename__ = "agent_sent_emails"
    __table_args__ = (
        Index("ix_sent_email_supplier", "supplier_email"),
        Index("ix_sent_email_job", "job_id"),
        {"schema": "ici"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ici.rfq_agent_jobs.id", ondelete="CASCADE")
    )
    supplier_name: Mapped[str] = mapped_column(String(256))
    supplier_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998))
    body_html: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(32), default="SENT")

    job: Mapped[RFQJobORM] = relationship(back_populates="emails")


class ParsedQuoteORM(Base):
    __tablename__ = "agent_parsed_quotes"
    __table_args__ = {"schema": "ici"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ici.rfq_agent_jobs.id", ondelete="CASCADE")
    )
    supplier_email: Mapped[str] = mapped_column(String(320))
    supplier_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_email: Mapped[str] = mapped_column(Text)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    normalized: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validity_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[RFQJobORM] = relationship(back_populates="quotes")


class EmailRateLedgerORM(Base):
    """Tracks emails sent per supplier per day for rate limiting."""
    __tablename__ = "email_rate_ledger"
    __table_args__ = (
        Index("ix_rate_ledger_domain_date", "domain", "date"),
        {"schema": "ici"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    count: Mapped[int] = mapped_column(Integer, default=0)
