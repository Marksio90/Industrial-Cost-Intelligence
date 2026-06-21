from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)


class EventPublisher:
    """Publishes domain events to Redis Pub/Sub and stores in a stream."""

    def __init__(self, redis: Redis, channel_prefix: str = "rfq_agent") -> None:
        self._redis = redis
        self._prefix = channel_prefix

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str = "default",
        correlation_id: str | None = None,
    ) -> None:
        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "tenant_id": tenant_id,
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        channel = f"{self._prefix}:{tenant_id}:{event_type}"
        stream_key = f"{self._prefix}:stream:{tenant_id}"
        raw = json.dumps(event)

        try:
            pipe = self._redis.pipeline()
            await pipe.publish(channel, raw)
            await pipe.xadd(stream_key, {"data": raw}, maxlen=10_000, approximate=True)
            await pipe.execute()
            log.debug("event_published", type=event_type, tenant=tenant_id)
        except Exception as exc:
            log.warning("event_publish_failed", type=event_type, error=str(exc))


# ── Well-known event types ─────────────────────────────────────────────────

class RFQEvents:
    AGENT_STARTED        = "rfq.agent.started"
    AGENT_COMPLETED      = "rfq.agent.completed"
    AGENT_FAILED         = "rfq.agent.failed"
    AGENT_STEP           = "rfq.agent.step"

    SUPPLIER_DISCOVERED  = "rfq.supplier.discovered"
    SUPPLIER_BLACKLISTED = "rfq.supplier.blacklisted"

    JOB_CREATED          = "rfq.job.created"
    JOB_SENT             = "rfq.job.sent"
    JOB_COMPLETED        = "rfq.job.completed"

    EMAIL_SENT           = "rfq.email.sent"
    EMAIL_FAILED         = "rfq.email.failed"
    EMAIL_RECEIVED       = "rfq.email.received"

    QUOTE_PARSED         = "rfq.quote.parsed"
    QUOTE_NORMALIZED     = "rfq.quote.normalized"

    RATE_LIMIT_HIT       = "rfq.safety.rate_limit"
    COMPLIANCE_VIOLATION = "rfq.safety.compliance_violation"
