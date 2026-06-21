"""
ICI Negotiation — Domain models, SQLAlchemy ORM, and Pydantic schemas.

Database tables:
  negotiation_sessions      — top-level session per supplier × material bundle
  negotiation_rounds        — one row per exchange (offer → counter → response)
  supplier_behavior_profiles — ML-inferred behavioral fingerprint
  negotiation_outcomes      — final settled terms + KPIs
  approval_requests         — human-in-the-loop checkpoints

Enumerations follow the session state machine defined in architecture.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Enum as SAEnum,
    ForeignKey, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


# ─────────────────────────────────────────────────────────────────────────────
# SQLAlchemy base
# ─────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class SessionState(str, Enum):
    INITIATED          = "INITIATED"
    ANALYZING          = "ANALYZING"         # research + behavior model
    STRATEGY_SET       = "STRATEGY_SET"      # strategy chosen
    ROUND_ACTIVE       = "ROUND_ACTIVE"      # offer sent, awaiting response
    AWAITING_APPROVAL  = "AWAITING_APPROVAL" # human checkpoint
    AGREEMENT          = "AGREEMENT"         # deal closed
    DEADLOCK           = "DEADLOCK"          # no agreement possible
    WITHDRAWN          = "WITHDRAWN"         # we pulled out
    ESCALATED          = "ESCALATED"         # passed to procurement team


class NegotiationStrategy(str, Enum):
    COMPETITIVE    = "COMPETITIVE"    # anchor high, concede slowly
    COLLABORATIVE  = "COLLABORATIVE"  # expand the pie, win-win
    COMPROMISING   = "COMPROMISING"   # split the difference
    ACCOMMODATING  = "ACCOMMODATING"  # preserve relationship > price
    HARDBALL       = "HARDBALL"       # extreme anchor + very slow concessions
    DEADLINE       = "DEADLINE"       # create artificial urgency


class RoundOutcome(str, Enum):
    SENT          = "SENT"
    ACCEPTED      = "ACCEPTED"
    REJECTED      = "REJECTED"
    COUNTER       = "COUNTER"
    WITHDRAWN     = "WITHDRAWN"
    TIMED_OUT     = "TIMED_OUT"
    PENDING       = "PENDING"


class SupplierTrait(str, Enum):
    ANCHORING_SUSCEPTIBLE  = "ANCHORING_SUSCEPTIBLE"
    LOSS_AVERSE            = "LOSS_AVERSE"
    RECIPROCAL             = "RECIPROCAL"
    RELATIONSHIP_DRIVEN    = "RELATIONSHIP_DRIVEN"
    PRICE_SENSITIVE        = "PRICE_SENSITIVE"
    VOLUME_SENSITIVE       = "VOLUME_SENSITIVE"
    DEADLINE_SENSITIVE     = "DEADLINE_SENSITIVE"
    STUBBORN               = "STUBBORN"
    FAST_CONCEDER          = "FAST_CONCEDER"


class ApprovalStatus(str, Enum):
    PENDING   = "PENDING"
    APPROVED  = "APPROVED"
    REJECTED  = "REJECTED"
    EXPIRED   = "EXPIRED"


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"
    STOP   = "STOP"       # auto-stop: never execute without human


# ─────────────────────────────────────────────────────────────────────────────
# ORM models
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationSessionORM(Base):
    __tablename__ = "negotiation_sessions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id       = Column(String(64), nullable=False, index=True)
    supplier_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    initiator_id    = Column(String(64), nullable=False)    # user who triggered
    rfq_id          = Column(UUID(as_uuid=True), nullable=True)

    state           = Column(SAEnum(SessionState), nullable=False, default=SessionState.INITIATED)
    strategy        = Column(SAEnum(NegotiationStrategy), nullable=True)
    risk_level      = Column(SAEnum(RiskLevel), nullable=False, default=RiskLevel.LOW)

    # Bundle of materials being negotiated
    material_ids    = Column(JSONB, nullable=False, default=list)
    total_quantity  = Column(Numeric(18, 4), nullable=False)
    currency        = Column(String(3), nullable=False, default="EUR")

    # Price targets (EUR/unit)
    target_price    = Column(Numeric(18, 6), nullable=True)   # our ideal
    walk_away_price = Column(Numeric(18, 6), nullable=True)   # BATNA floor
    anchor_price    = Column(Numeric(18, 6), nullable=True)   # first offer
    settled_price   = Column(Numeric(18, 6), nullable=True)   # final agreed

    # Deadline / urgency
    deadline        = Column(Date, nullable=True)
    urgency_score   = Column(Numeric(4, 3), nullable=False, default=0.5)

    # Counters
    round_count     = Column(Integer, nullable=False, default=0)
    max_rounds      = Column(Integer, nullable=False, default=8)

    # Context / notes
    context         = Column(JSONB, nullable=False, default=dict)
    internal_notes  = Column(Text, nullable=True)

    # Timestamps
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at       = Column(DateTime(timezone=True), nullable=True)

    rounds          = relationship("NegotiationRoundORM",    back_populates="session", order_by="NegotiationRoundORM.round_number")
    approval_requests = relationship("ApprovalRequestORM",   back_populates="session")
    outcome         = relationship("NegotiationOutcomeORM",  back_populates="session", uselist=False)


class NegotiationRoundORM(Base):
    __tablename__ = "negotiation_rounds"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id      = Column(UUID(as_uuid=True), ForeignKey("negotiation_sessions.id"), nullable=False, index=True)
    round_number    = Column(Integer, nullable=False)

    # Our offer
    our_price       = Column(Numeric(18, 6), nullable=False)
    our_terms       = Column(JSONB, nullable=False, default=dict)   # payment, delivery, warranty
    our_rationale   = Column(Text, nullable=True)
    offer_sent_at   = Column(DateTime(timezone=True), nullable=True)

    # Supplier counter
    supplier_price  = Column(Numeric(18, 6), nullable=True)
    supplier_terms  = Column(JSONB, nullable=True)
    supplier_message = Column(Text, nullable=True)
    response_received_at = Column(DateTime(timezone=True), nullable=True)

    # Analysis
    concession_amount    = Column(Numeric(18, 6), nullable=True)   # vs previous round
    concession_rate      = Column(Numeric(6, 4), nullable=True)    # %
    acceptance_probability = Column(Numeric(5, 4), nullable=True)  # model prediction
    outcome              = Column(SAEnum(RoundOutcome), nullable=False, default=RoundOutcome.PENDING)

    # Generated by AI
    ai_reasoning        = Column(Text, nullable=True)
    behavioral_signals  = Column(JSONB, nullable=False, default=list)

    session = relationship("NegotiationSessionORM", back_populates="rounds")


class SupplierBehaviorProfileORM(Base):
    __tablename__ = "supplier_behavior_profiles"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id     = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    tenant_id       = Column(String(64), nullable=False)

    # Concession behavior
    avg_concession_rate     = Column(Numeric(6, 4), nullable=True)   # % per round
    avg_rounds_to_close     = Column(Numeric(4, 1), nullable=True)
    anchor_resistance_score = Column(Numeric(5, 4), nullable=True)   # 0=susceptible,1=resistant

    # Identified traits (list of SupplierTrait values)
    traits          = Column(JSONB, nullable=False, default=list)

    # Price elasticity
    price_elasticity_coefficient = Column(Numeric(8, 4), nullable=True)
    volume_sensitivity_score     = Column(Numeric(5, 4), nullable=True)

    # Deal outcomes
    total_negotiations  = Column(Integer, nullable=False, default=0)
    agreement_rate      = Column(Numeric(5, 4), nullable=True)
    avg_discount_achieved = Column(Numeric(6, 4), nullable=True)   # % below initial ask

    # ML model scores (updated nightly)
    acceptance_model_features = Column(JSONB, nullable=True)
    last_model_update         = Column(DateTime(timezone=True), nullable=True)

    # Market position
    market_share_estimate  = Column(Numeric(5, 4), nullable=True)
    substitute_count       = Column(Integer, nullable=True)   # available alternatives

    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NegotiationOutcomeORM(Base):
    __tablename__ = "negotiation_outcomes"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id      = Column(UUID(as_uuid=True), ForeignKey("negotiation_sessions.id"), nullable=False, unique=True)
    tenant_id       = Column(String(64), nullable=False)

    # Result
    result          = Column(String(32), nullable=False)   # "agreement" | "deadlock" | "withdrawn"
    final_price     = Column(Numeric(18, 6), nullable=True)
    initial_ask     = Column(Numeric(18, 6), nullable=True)  # supplier's first price
    target_price    = Column(Numeric(18, 6), nullable=True)
    walk_away_price = Column(Numeric(18, 6), nullable=True)
    settled_terms   = Column(JSONB, nullable=True)

    # KPIs
    discount_vs_initial_ask  = Column(Numeric(6, 4), nullable=True)   # %
    discount_vs_market       = Column(Numeric(6, 4), nullable=True)
    rounds_taken             = Column(Integer, nullable=True)
    days_elapsed             = Column(Integer, nullable=True)
    total_value_saved_eur    = Column(Numeric(18, 2), nullable=True)
    strategy_used            = Column(SAEnum(NegotiationStrategy), nullable=True)

    # Learnings
    lessons_learned = Column(Text, nullable=True)
    model_feedback  = Column(JSONB, nullable=True)   # features for retraining

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("NegotiationSessionORM", back_populates="outcome")


class ApprovalRequestORM(Base):
    __tablename__ = "approval_requests"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id      = Column(UUID(as_uuid=True), ForeignKey("negotiation_sessions.id"), nullable=False, index=True)
    tenant_id       = Column(String(64), nullable=False)

    # What needs approval
    checkpoint_type = Column(String(64), nullable=False)   # "offer_send" | "strategy_change" | "walk_away" | "settlement"
    proposed_action = Column(JSONB, nullable=False)
    risk_summary    = Column(Text, nullable=True)
    ai_recommendation = Column(Text, nullable=True)

    # Thresholds that triggered review
    trigger_reason  = Column(String(256), nullable=False)
    risk_level      = Column(SAEnum(RiskLevel), nullable=False)

    # Approval
    status          = Column(SAEnum(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING)
    approver_id     = Column(String(64), nullable=True)
    approver_notes  = Column(Text, nullable=True)
    expires_at      = Column(DateTime(timezone=True), nullable=False)

    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    decided_at      = Column(DateTime(timezone=True), nullable=True)

    session = relationship("NegotiationSessionORM", back_populates="approval_requests")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class TermsBundle(BaseModel):
    """Commercial terms beyond price — attached to every offer."""
    payment_days:    int   = 30
    delivery_weeks:  int   = 4
    warranty_months: int   = 12
    incoterm:        str   = "DDP"
    min_order_qty:   int   = 100
    annual_volume:   int   = 1000
    price_validity_days: int = 30
    extras: dict[str, Any] = Field(default_factory=dict)


class NegotiationOffer(BaseModel):
    price_per_unit:    Decimal
    currency:          str = "EUR"
    terms:             TermsBundle = Field(default_factory=TermsBundle)
    message:           str = ""
    rationale:         str = ""
    valid_until_hours: int = 48


class SupplierResponse(BaseModel):
    price_per_unit:  Decimal
    currency:        str = "EUR"
    terms:           dict[str, Any] = Field(default_factory=dict)
    message:         str = ""
    is_final:        bool = False
    received_at:     datetime = Field(default_factory=datetime.utcnow)


class NegotiationSessionCreate(BaseModel):
    supplier_id:      uuid.UUID
    material_ids:     list[uuid.UUID]
    total_quantity:   Decimal
    currency:         str = "EUR"
    target_price:     Decimal | None = None
    walk_away_price:  Decimal | None = None
    deadline:         date | None = None
    rfq_id:           uuid.UUID | None = None
    context:          dict[str, Any] = Field(default_factory=dict)
    max_rounds:       int = Field(default=8, ge=2, le=20)

    @field_validator("total_quantity")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("total_quantity must be positive")
        return v


class NegotiationSessionOut(BaseModel):
    id:              uuid.UUID
    tenant_id:       str
    supplier_id:     uuid.UUID
    state:           SessionState
    strategy:        NegotiationStrategy | None
    risk_level:      RiskLevel
    round_count:     int
    max_rounds:      int
    target_price:    Decimal | None
    walk_away_price: Decimal | None
    anchor_price:    Decimal | None
    settled_price:   Decimal | None
    currency:        str
    created_at:      datetime
    updated_at:      datetime | None

    model_config = {"from_attributes": True}


class RoundOut(BaseModel):
    id:                    uuid.UUID
    session_id:            uuid.UUID
    round_number:          int
    our_price:             Decimal
    our_terms:             dict[str, Any]
    supplier_price:        Decimal | None
    outcome:               RoundOutcome
    concession_amount:     Decimal | None
    acceptance_probability: Decimal | None
    ai_reasoning:          str | None

    model_config = {"from_attributes": True}


class BehaviorProfileOut(BaseModel):
    supplier_id:              uuid.UUID
    traits:                   list[str]
    avg_concession_rate:      Decimal | None
    avg_rounds_to_close:      Decimal | None
    anchor_resistance_score:  Decimal | None
    agreement_rate:           Decimal | None
    avg_discount_achieved:    Decimal | None
    total_negotiations:       int

    model_config = {"from_attributes": True}


class ApprovalRequestOut(BaseModel):
    id:               uuid.UUID
    session_id:       uuid.UUID
    checkpoint_type:  str
    proposed_action:  dict[str, Any]
    risk_summary:     str | None
    ai_recommendation: str | None
    trigger_reason:   str
    risk_level:       RiskLevel
    status:           ApprovalStatus
    expires_at:       datetime

    model_config = {"from_attributes": True}


class ApprovalDecision(BaseModel):
    approved:  bool
    notes:     str = ""
    modified_price: Decimal | None = None   # approver can adjust the offer price
