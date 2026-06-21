"""
Section 11 — Market Monitor Domain Events

All significant market state changes emit typed events to:
  1. Redis Stream ici:market_monitor:{tenant_id}   (real-time)
  2. Redis Stream ici:market_events                 (global firehose)
  3. Redis Pub/Sub ici:mkt:events:{tenant_id}       (WebSocket fanout)
  4. PostgreSQL market_events table                  (audit + replay)

Event types:
  market.price.updated         — routine price tick (batched, not individual ticks)
  market.shock.detected        — MarketShock fired
  market.shock.contagion       — secondary shock propagation expected
  market.alert.fired           — alert rule triggered
  market.alert.acknowledged    — alert acknowledged by user
  market.alert.resolved        — alert resolved
  market.forecast.generated    — new price forecast available
  market.fx.rate_updated       — significant FX rate move (>1% in 1 day)
  market.inflation.ppi_updated — new PPI data point received
  market.data_source.degraded  — data source circuit breaker opened
  market.data_source.recovered — data source circuit breaker reset
  market.correlation.breakdown — cross-commodity correlation shift
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────────────────────

class MarketEventType(str, Enum):
    PRICE_UPDATED         = "market.price.updated"
    SHOCK_DETECTED        = "market.shock.detected"
    SHOCK_CONTAGION       = "market.shock.contagion"
    ALERT_FIRED           = "market.alert.fired"
    ALERT_ACKNOWLEDGED    = "market.alert.acknowledged"
    ALERT_RESOLVED        = "market.alert.resolved"
    FORECAST_GENERATED    = "market.forecast.generated"
    FX_RATE_UPDATED       = "market.fx.rate_updated"
    INFLATION_PPI_UPDATED = "market.inflation.ppi_updated"
    SOURCE_DEGRADED       = "market.data_source.degraded"
    SOURCE_RECOVERED      = "market.data_source.recovered"
    CORRELATION_BREAKDOWN = "market.correlation.breakdown"


# ─────────────────────────────────────────────────────────────────────────────
# Base event
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketEvent:
    event_type:  MarketEventType
    tenant_id:   str
    payload:     dict[str, Any] = field(default_factory=dict)
    event_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version:     int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":    self.event_id,
            "event_type":  self.event_type.value,
            "tenant_id":   self.tenant_id,
            "occurred_at": self.occurred_at,
            "version":     self.version,
            "payload":     self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ─────────────────────────────────────────────────────────────────────────────
# Typed constructors
# ─────────────────────────────────────────────────────────────────────────────

def price_updated(
    tenant_id:   str,
    commodity:   str,
    price:       float,
    currency:    str,
    chg_1d_pct:  float | None,
    source:      str,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.PRICE_UPDATED,
        tenant_id  = tenant_id,
        payload    = {
            "commodity":  commodity,
            "price":      price,
            "currency":   currency,
            "chg_1d_pct": chg_1d_pct,
            "source":     source,
        },
    )


def shock_detected(
    tenant_id:   str,
    shock_id:    str,
    commodity:   str | None,
    shock_type:  str,
    magnitude:   float,
    direction:   int,
    description: str,
    confidence:  float,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.SHOCK_DETECTED,
        tenant_id  = tenant_id,
        payload    = {
            "shock_id":    shock_id,
            "commodity":   commodity,
            "shock_type":  shock_type,
            "magnitude":   magnitude,
            "direction":   direction,
            "description": description,
            "confidence":  confidence,
        },
    )


def shock_contagion(
    tenant_id:       str,
    primary_shock_id: str,
    affected_commodity: str,
    lag_days:        int,
    expected_at:     str,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.SHOCK_CONTAGION,
        tenant_id  = tenant_id,
        payload    = {
            "primary_shock_id":     primary_shock_id,
            "affected_commodity":   affected_commodity,
            "lag_days":             lag_days,
            "expected_at":          expected_at,
        },
    )


def alert_fired(
    tenant_id:  str,
    alert_id:   str,
    rule_id:    str,
    commodity:  str | None,
    severity:   str,
    title:      str,
    body:       str,
    channels:   list[str],
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.ALERT_FIRED,
        tenant_id  = tenant_id,
        payload    = {
            "alert_id":  alert_id,
            "rule_id":   rule_id,
            "commodity": commodity,
            "severity":  severity,
            "title":     title,
            "body":      body,
            "channels":  channels,
        },
    )


def alert_acknowledged(
    tenant_id:     str,
    alert_id:      str,
    acknowledged_by: str,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.ALERT_ACKNOWLEDGED,
        tenant_id  = tenant_id,
        payload    = {"alert_id": alert_id, "acknowledged_by": acknowledged_by},
    )


def forecast_generated(
    tenant_id:    str,
    commodity:    str,
    model:        str,
    horizon_days: int,
    forecast_1m:  float | None,
    forecast_3m:  float | None,
    rmse:         float | None,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.FORECAST_GENERATED,
        tenant_id  = tenant_id,
        payload    = {
            "commodity":    commodity,
            "model":        model,
            "horizon_days": horizon_days,
            "forecast_1m":  forecast_1m,
            "forecast_3m":  forecast_3m,
            "rmse":         rmse,
        },
    )


def fx_rate_updated(
    tenant_id:    str,
    base:         str,
    quote:        str,
    rate:         float,
    chg_1d_pct:   float,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.FX_RATE_UPDATED,
        tenant_id  = tenant_id,
        payload    = {
            "base":       base,
            "quote":      quote,
            "rate":       rate,
            "chg_1d_pct": chg_1d_pct,
        },
    )


def source_degraded(
    tenant_id:   str,
    source_name: str,
    reason:      str,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.SOURCE_DEGRADED,
        tenant_id  = tenant_id,
        payload    = {"source_name": source_name, "reason": reason},
    )


def source_recovered(
    tenant_id:   str,
    source_name: str,
) -> MarketEvent:
    return MarketEvent(
        event_type = MarketEventType.SOURCE_RECOVERED,
        tenant_id  = tenant_id,
        payload    = {"source_name": source_name},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Event Publisher
# ─────────────────────────────────────────────────────────────────────────────

class MarketEventPublisher:
    """
    Publishes market events to Redis streams + pub/sub.
    Falls back to structured log if Redis unavailable.
    """

    STREAM_TENANT  = "ici:market_monitor:{tenant_id}"
    STREAM_GLOBAL  = "ici:market_events"
    PUBSUB_TENANT  = "ici:mkt:events:{tenant_id}"

    def __init__(self, redis: Any = None, db_writer: Any = None) -> None:
        self._redis     = redis
        self._db_writer = db_writer

    async def publish(self, event: MarketEvent) -> None:
        payload = event.to_dict()
        flat    = {
            k: str(v) if not isinstance(v, str) else v
            for k, v in payload.items()
            if not isinstance(v, dict)
        }
        flat["payload"] = event.to_json()

        logger.info(
            "market_event",
            event_type = event.event_type.value,
            tenant_id  = event.tenant_id,
            **{k: v for k, v in event.payload.items() if isinstance(v, (str, int, float, bool))},
        )

        if self._redis:
            try:
                stream  = self.STREAM_TENANT.format(tenant_id=event.tenant_id)
                channel = self.PUBSUB_TENANT.format(tenant_id=event.tenant_id)
                pipe = self._redis.pipeline()
                pipe.xadd(stream, flat, maxlen=50_000, approximate=True)
                pipe.xadd(self.STREAM_GLOBAL, flat, maxlen=1_000_000, approximate=True)
                pipe.publish(channel, event.to_json())
                await pipe.execute()
            except Exception as exc:
                logger.warning("market_event_redis_failed", error=str(exc))

        if self._db_writer:
            try:
                await self._db_writer(event)
            except Exception as exc:
                logger.warning("market_event_db_failed", error=str(exc))
