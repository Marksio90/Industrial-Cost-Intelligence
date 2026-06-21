"""
Section 11 — Domain Events

Knowledge Graph event publishing via Redis Streams.

Event types:
  NODE_CREATED          — new node added
  NODE_UPDATED          — node properties changed
  NODE_DELETED          — node removed
  EDGE_CREATED          — new relationship
  EDGE_DELETED          — relationship removed
  SIMILARITY_COMPUTED   — SIMILAR_TO edges batch-updated
  EMBEDDING_UPDATED     — node embedding refreshed
  EMBEDDING_RUN_DONE    — full/incremental embedding pipeline finished
  RECOMMENDATION_GEN    — recommendations computed for node
  SCHEMA_APPLIED        — DDL constraints/indexes applied
  SEARCH_PERFORMED      — search query executed (for audit/analytics)
  GRAPH_EXPORTED        — subgraph exported

Stream keys:
  ici:kg:{tenant_id}    — per-tenant stream
  ici:kg:global         — global aggregation stream
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────────────────────

class KGEventType(str, Enum):
    NODE_CREATED        = "node_created"
    NODE_UPDATED        = "node_updated"
    NODE_DELETED        = "node_deleted"
    EDGE_CREATED        = "edge_created"
    EDGE_DELETED        = "edge_deleted"
    SIMILARITY_COMPUTED = "similarity_computed"
    EMBEDDING_UPDATED   = "embedding_updated"
    EMBEDDING_RUN_DONE  = "embedding_run_done"
    RECOMMENDATION_GEN  = "recommendation_generated"
    SCHEMA_APPLIED      = "schema_applied"
    SEARCH_PERFORMED    = "search_performed"
    GRAPH_EXPORTED      = "graph_exported"


# ─────────────────────────────────────────────────────────────────────────────
# Event envelope
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KGEvent:
    event_id:   str              = field(default_factory=lambda: str(uuid4()))
    event_type: KGEventType      = KGEventType.NODE_CREATED
    tenant_id:  str              = ""
    timestamp:  float            = field(default_factory=time.time)
    payload:    dict[str, Any]   = field(default_factory=dict)
    source:     str              = "knowledge_graph"
    version:    str              = "1.0"

    def to_redis_fields(self) -> dict[str, str]:
        return {
            "event_id":   self.event_id,
            "event_type": self.event_type.value,
            "tenant_id":  self.tenant_id,
            "timestamp":  str(self.timestamp),
            "payload":    json.dumps(self.payload, default=str),
            "source":     self.source,
            "version":    self.version,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Publisher
# ─────────────────────────────────────────────────────────────────────────────

_STREAM_MAXLEN = 10_000   # per-tenant stream capped
_GLOBAL_STREAM = "ici:kg:global"


class KGEventPublisher:
    """
    Publishes Knowledge Graph domain events to Redis Streams.

    Graceful degradation: if Redis is unavailable, events are logged only.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client

    async def publish(self, event: KGEvent) -> str | None:
        """Publish event and return stream message ID, or None on failure."""
        fields = event.to_redis_fields()
        tenant_stream = f"ici:kg:{event.tenant_id}"

        if self._redis is None:
            log.debug("KGEvent (no Redis): %s %s", event.event_type.value, event.payload)
            return None

        try:
            pipe = self._redis.pipeline()
            pipe.xadd(tenant_stream, fields, maxlen=_STREAM_MAXLEN, approximate=True)
            pipe.xadd(_GLOBAL_STREAM, fields, maxlen=_STREAM_MAXLEN * 5, approximate=True)
            pipe.publish(f"ici:kg:pubsub:{event.tenant_id}", json.dumps({"event_type": event.event_type.value, "node_id": event.payload.get("node_id")}))
            results = await pipe.execute()
            return results[0]  # stream message ID
        except Exception as exc:
            log.warning("KGEventPublisher failed: %s", exc)
            return None

    # ── Convenience factory methods ───────────────────────────────────────────

    async def node_created(self, tenant_id: str, node_id: str, node_type: str, name: str) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.NODE_CREATED,
            tenant_id=tenant_id,
            payload={"node_id": node_id, "node_type": node_type, "name": name},
        ))

    async def node_updated(self, tenant_id: str, node_id: str, changed_props: list[str]) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.NODE_UPDATED,
            tenant_id=tenant_id,
            payload={"node_id": node_id, "changed_props": changed_props},
        ))

    async def node_deleted(self, tenant_id: str, node_id: str, node_type: str) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.NODE_DELETED,
            tenant_id=tenant_id,
            payload={"node_id": node_id, "node_type": node_type},
        ))

    async def edge_created(
        self, tenant_id: str, source_id: str, target_id: str, relation_type: str, weight: float
    ) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.EDGE_CREATED,
            tenant_id=tenant_id,
            payload={"source_id": source_id, "target_id": target_id, "relation_type": relation_type, "weight": weight},
        ))

    async def edge_deleted(self, tenant_id: str, source_id: str, target_id: str, relation_type: str) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.EDGE_DELETED,
            tenant_id=tenant_id,
            payload={"source_id": source_id, "target_id": target_id, "relation_type": relation_type},
        ))

    async def similarity_computed(self, tenant_id: str, edges_count: int, threshold: float) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.SIMILARITY_COMPUTED,
            tenant_id=tenant_id,
            payload={"edges_created": edges_count, "threshold": threshold},
        ))

    async def embedding_updated(self, tenant_id: str, node_id: str, node_type: str) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.EMBEDDING_UPDATED,
            tenant_id=tenant_id,
            payload={"node_id": node_id, "node_type": node_type},
        ))

    async def embedding_run_done(
        self, tenant_id: str, processed: int, errors: int, duration_s: float, incremental: bool
    ) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.EMBEDDING_RUN_DONE,
            tenant_id=tenant_id,
            payload={
                "processed": processed,
                "errors":    errors,
                "duration_s": duration_s,
                "incremental": incremental,
            },
        ))

    async def recommendation_generated(
        self, tenant_id: str, node_id: str, strategy: str, count: int
    ) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.RECOMMENDATION_GEN,
            tenant_id=tenant_id,
            payload={"node_id": node_id, "strategy": strategy, "count": count},
        ))

    async def schema_applied(self, tenant_id: str, applied: int, failed: int) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.SCHEMA_APPLIED,
            tenant_id=tenant_id,
            payload={"applied": applied, "failed": failed},
        ))

    async def search_performed(
        self, tenant_id: str, query: str, strategy: str, hits: int, took_ms: float
    ) -> None:
        await self.publish(KGEvent(
            event_type=KGEventType.SEARCH_PERFORMED,
            tenant_id=tenant_id,
            payload={"query": query[:200], "strategy": strategy, "hits": hits, "took_ms": took_ms},
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Event consumer skeleton (for downstream services)
# ─────────────────────────────────────────────────────────────────────────────

class KGEventConsumer:
    """
    Redis Streams consumer for KG events.
    Extend and override `handle_event()` in downstream services.

    Usage:
        consumer = KGEventConsumer(redis, tenant_id="tenant-x", group="my-service")
        await consumer.start()
    """

    def __init__(
        self,
        redis_client:  Any,
        tenant_id:     str,
        group_name:    str,
        consumer_name: str = "worker-1",
        block_ms:      int = 2_000,
    ) -> None:
        self._redis    = redis_client
        self._stream   = f"ici:kg:{tenant_id}"
        self._group    = group_name
        self._consumer = consumer_name
        self._block    = block_ms
        self._running  = False

    async def start(self) -> None:
        await self._ensure_group()
        self._running = True
        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream: ">"},
                    count=50,
                    block=self._block,
                )
                for _stream, entries in (messages or []):
                    for msg_id, fields in entries:
                        event = self._parse(fields)
                        try:
                            await self.handle_event(event)
                            await self._redis.xack(self._stream, self._group, msg_id)
                        except Exception as exc:
                            log.error("Event handling failed %s: %s", msg_id, exc)
            except Exception as exc:
                log.warning("Consumer loop error: %s", exc)

    def stop(self) -> None:
        self._running = False

    async def handle_event(self, event: KGEvent) -> None:
        """Override in subclass."""
        log.debug("KGEvent received: %s", event.event_type.value)

    async def _ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists

    @staticmethod
    def _parse(fields: dict[str, Any]) -> KGEvent:
        payload = {}
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except Exception:
            pass
        return KGEvent(
            event_id=fields.get("event_id", ""),
            event_type=KGEventType(fields.get("event_type", "node_created")),
            tenant_id=fields.get("tenant_id", ""),
            timestamp=float(fields.get("timestamp", time.time())),
            payload=payload,
            source=fields.get("source", "knowledge_graph"),
        )
