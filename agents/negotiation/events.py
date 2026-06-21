"""
Section 12 — Domain Events

All state transitions emit a typed domain event that is:
  1. Written to Redis Stream ici:negotiations:{session_id}   (real-time)
  2. Written to Redis Stream ici:negotiation_events          (global firehose)
  3. Persisted to PostgreSQL negotiation_events table        (audit + replay)
  4. Published to Redis Pub/Sub ici:neg:events:{tenant_id}   (WebSocket fanout)

Event schema (wire format = JSON):
  {
    "event_id":    "uuid",
    "event_type":  "negotiation.session.opened",
    "session_id":  "uuid",
    "tenant_id":   "str",
    "occurred_at": "ISO-8601",
    "version":     1,
    "payload":     { ... event-specific fields }
  }

Event types:
  negotiation.session.opened
  negotiation.session.analyzing
  negotiation.session.strategy_set
  negotiation.offer.generated
  negotiation.offer.sent
  negotiation.response.received
  negotiation.round.completed
  negotiation.approval.requested
  negotiation.approval.decided
  negotiation.session.agreement
  negotiation.session.deadlock
  negotiation.session.withdrawn
  negotiation.session.escalated
  negotiation.risk.control_fired
  negotiation.behavior.profile_updated
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationEventType(str, Enum):
    SESSION_OPENED        = "negotiation.session.opened"
    SESSION_ANALYZING     = "negotiation.session.analyzing"
    STRATEGY_SET          = "negotiation.session.strategy_set"
    OFFER_GENERATED       = "negotiation.offer.generated"
    OFFER_SENT            = "negotiation.offer.sent"
    RESPONSE_RECEIVED     = "negotiation.response.received"
    ROUND_COMPLETED       = "negotiation.round.completed"
    APPROVAL_REQUESTED    = "negotiation.approval.requested"
    APPROVAL_DECIDED      = "negotiation.approval.decided"
    SESSION_AGREEMENT     = "negotiation.session.agreement"
    SESSION_DEADLOCK      = "negotiation.session.deadlock"
    SESSION_WITHDRAWN     = "negotiation.session.withdrawn"
    SESSION_ESCALATED     = "negotiation.session.escalated"
    RISK_CONTROL_FIRED    = "negotiation.risk.control_fired"
    BEHAVIOR_UPDATED      = "negotiation.behavior.profile_updated"
    STRATEGY_ADAPTED      = "negotiation.strategy.adapted"


# ─────────────────────────────────────────────────────────────────────────────
# Base event
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NegotiationEvent:
    event_type:  NegotiationEventType
    session_id:  str
    tenant_id:   str
    payload:     dict[str, Any] = field(default_factory=dict)
    event_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version:     int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":    self.event_id,
            "event_type":  self.event_type.value,
            "session_id":  self.session_id,
            "tenant_id":   self.tenant_id,
            "occurred_at": self.occurred_at,
            "version":     self.version,
            "payload":     self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ─────────────────────────────────────────────────────────────────────────────
# Typed event constructors
# ─────────────────────────────────────────────────────────────────────────────

def session_opened(
    session_id:  str,
    tenant_id:   str,
    supplier_id: str,
    material_ids: list[str],
    quantity:    float,
    currency:    str,
    initiator_id: str,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.SESSION_OPENED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "supplier_id":   supplier_id,
            "material_ids":  material_ids,
            "quantity":      quantity,
            "currency":      currency,
            "initiator_id":  initiator_id,
        },
    )


def strategy_set(
    session_id: str,
    tenant_id:  str,
    strategy:   str,
    reasoning:  str,
    expected_rounds: int,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.STRATEGY_SET,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "strategy":       strategy,
            "reasoning":      reasoning,
            "expected_rounds": expected_rounds,
        },
    )


def offer_sent(
    session_id:   str,
    tenant_id:    str,
    round_number: int,
    price:        float,
    currency:     str,
    terms:        dict[str, Any],
    rationale:    str,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.OFFER_SENT,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "round_number": round_number,
            "price":        price,
            "currency":     currency,
            "terms":        terms,
            "rationale":    rationale,
        },
    )


def response_received(
    session_id:       str,
    tenant_id:        str,
    round_number:     int,
    supplier_price:   float,
    supplier_message: str,
    signals:          list[str],
    acceptance_prob:  float,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.RESPONSE_RECEIVED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "round_number":    round_number,
            "supplier_price":  supplier_price,
            "supplier_message": supplier_message,
            "behavioral_signals": signals,
            "acceptance_probability": acceptance_prob,
        },
    )


def session_agreement(
    session_id:    str,
    tenant_id:     str,
    settled_price: float,
    currency:      str,
    rounds_taken:  int,
    discount_pct:  float,
    value_saved:   float,
    strategy:      str,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.SESSION_AGREEMENT,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "settled_price":  settled_price,
            "currency":       currency,
            "rounds_taken":   rounds_taken,
            "discount_pct":   discount_pct,
            "value_saved_eur": value_saved,
            "strategy":       strategy,
        },
    )


def session_deadlock(
    session_id:   str,
    tenant_id:    str,
    last_our_price: float,
    last_supplier_price: float,
    rounds_taken: int,
    reason:       str,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.SESSION_DEADLOCK,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "last_our_price":       last_our_price,
            "last_supplier_price":  last_supplier_price,
            "rounds_taken":         rounds_taken,
            "reason":               reason,
        },
    )


def approval_requested(
    session_id:      str,
    tenant_id:       str,
    request_id:      str,
    checkpoint_type: str,
    risk_level:      str,
    trigger_reason:  str,
    proposed_price:  float | None,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.APPROVAL_REQUESTED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "request_id":     request_id,
            "checkpoint_type": checkpoint_type,
            "risk_level":     risk_level,
            "trigger_reason": trigger_reason,
            "proposed_price": proposed_price,
        },
    )


def approval_decided(
    session_id:   str,
    tenant_id:    str,
    request_id:   str,
    approved:     bool,
    approver_id:  str,
    modified_price: float | None,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.APPROVAL_DECIDED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "request_id":     request_id,
            "approved":       approved,
            "approver_id":    approver_id,
            "modified_price": modified_price,
        },
    )


def risk_control_fired(
    session_id:     str,
    tenant_id:      str,
    control_name:   str,
    action:         str,
    risk_level:     str,
    reason:         str,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.RISK_CONTROL_FIRED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "control_name": control_name,
            "action":       action,
            "risk_level":   risk_level,
            "reason":       reason,
        },
    )


def strategy_adapted(
    session_id:   str,
    tenant_id:    str,
    old_strategy: str,
    new_strategy: str,
    reason:       str,
    round_number: int,
) -> NegotiationEvent:
    return NegotiationEvent(
        event_type = NegotiationEventType.STRATEGY_ADAPTED,
        session_id = session_id,
        tenant_id  = tenant_id,
        payload    = {
            "old_strategy": old_strategy,
            "new_strategy": new_strategy,
            "reason":       reason,
            "round_number": round_number,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Event Publisher
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationEventPublisher:
    """
    Publishes events to Redis streams + pub/sub.
    Also calls structured logger for every event.
    """

    STREAM_SESSION   = "ici:negotiations:{session_id}"
    STREAM_GLOBAL    = "ici:negotiation_events"
    PUBSUB_TENANT    = "ici:neg:events:{tenant_id}"

    def __init__(self, redis: Any = None, db_writer: Any = None) -> None:
        self._redis     = redis
        self._db_writer = db_writer    # async callable(event) for DB persistence

    async def publish(self, event: NegotiationEvent) -> None:
        payload = event.to_dict()
        flat    = {k: str(v) if not isinstance(v, str) else v
                   for k, v in payload.items() if not isinstance(v, dict)}
        flat["payload"] = event.to_json()

        logger.info(
            "negotiation_event",
            event_type = event.event_type.value,
            session_id = event.session_id,
            tenant_id  = event.tenant_id,
            **{k: v for k, v in event.payload.items() if isinstance(v, (str, int, float, bool))},
        )

        if self._redis:
            try:
                session_stream = self.STREAM_SESSION.format(session_id=event.session_id)
                pubsub_channel = self.PUBSUB_TENANT.format(tenant_id=event.tenant_id)

                pipe = self._redis.pipeline()
                pipe.xadd(session_stream, flat, maxlen=10_000, approximate=True)
                pipe.xadd(self.STREAM_GLOBAL, flat, maxlen=500_000, approximate=True)
                pipe.publish(pubsub_channel, event.to_json())
                await pipe.execute()
            except Exception as exc:
                logger.warning("event_publish_redis_failed", error=str(exc))

        if self._db_writer:
            try:
                await self._db_writer(event)
            except Exception as exc:
                logger.warning("event_publish_db_failed", error=str(exc))
