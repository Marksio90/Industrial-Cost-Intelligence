# ICI Platform — Event-Driven Architecture & API Gateway

## 3. Event-Driven Architecture

### 3.1 Kafka Topology — 11 services × N topics

| Namespace | Topics | Producers | Consumers |
|-----------|--------|-----------|-----------|
| `me.*` | `me.material.created`, `me.material.updated`, `me.spec.changed` | ME | BOM, CBE, CRAE |
| `pe.*` | `pe.process.updated`, `pe.rate.changed` | PE | CBE, PFE |
| `se.*` | `se.supplier.qualified`, `se.supplier.suspended` | SE | SOP, CRAE, RFQ |
| `sop.*` | `sop.offer.parsed`, `sop.offer.rejected`, `sop.bom.mapped` | SOP | CBE, SE, RFQ |
| `cbe.*` | `cbe.breakdown.approved`, `cbe.breakdown.failed`, `cbe.rate.stale` | CBE | RFQ, CRAE, LE |
| `bom.*` | `bom.bom.created`, `bom.revision.released` | BOM | ME, CBE, CAD |
| `cad.*` | `cad.file.parsed`, `cad.features.extracted` | CAD | ME, BOM, CBE |
| `pfe.*` | `pfe.forecast.ready`, `pfe.drift.detected`, `pfe.price.anomaly` | PFE | CBE, CRAE, LE |
| `crae.*` | `crae.risk.critical`, `crae.portfolio.updated`, `crae.mitigation.overdue` | CRAE | RFQ, LE, Alerts |
| `rfq.*` | `rfq.rfq.sent`, `rfq.offer.received`, `rfq.awarded` | RFQ | SE, SOP, LE |
| `le.*` | `le.model.retrained`, `le.feedback.ingested` | LE | CBE, PFE, CRAE |

### 3.2 Full Event Flow: RFQ Lifecycle

```
User → API GW → BOM Engine ──────────────────────────────────────────────────┐
                                                                              │
                bom.bom.created ──► CAD Engine ──► cad.features.extracted    │
                                                         │                   │
                bom.bom.created ──► Material Engine      │                   │
                                        │                │                   │
                                   me.material.updated   │                   │
                                        │                │                   │
                                        ▼                ▼                   │
                                   Cost Engine (CBE) ◄───┘                   │
                                        │                                    │
                                   cbe.breakdown.approved                    │
                                        │                                    │
                         ┌─────────────┴──────────────┐                     │
                         │                            │                     │
                         ▼                            ▼                     │
                   Risk Engine (CRAE)          RFQ Agent ◄──────────────────┘
                         │                            │
                   crae.risk.critical           rfq.rfq.sent
                         │                            │
                         ▼                            ▼
                   Alert Engine              Supplier Offer Parser (SOP)
                   PD + Slack                       │
                                              sop.offer.parsed
                                                    │
                                                    ▼
                                             CBE (re-score) → LE feedback
```

### 3.3 Kafka Configuration

```yaml
# infra/k8s/helm/charts/kafka/values.yaml
kafka:
  replicaCount: 3
  config:
    default.replication.factor: "3"
    min.insync.replicas: "2"
    auto.create.topics.enable: "false"
    compression.type: "lz4"
    log.retention.hours: "168"        # 7 days
    log.retention.bytes: "107374182400"  # 100 GB per partition

schemaRegistry:
  enabled: true
  replicaCount: 2
  compatibility: "BACKWARD"

topics:
  - name: pfe.forecast.ready
    partitions: 6
    replicationFactor: 3
    config:
      retention.ms: "604800000"       # 7 days
  - name: crae.risk.critical
    partitions: 3
    replicationFactor: 3
    config:
      retention.ms: "2592000000"      # 30 days
  - name: cbe.breakdown.approved
    partitions: 12
    replicationFactor: 3
  - name: le.feedback.ingested
    partitions: 6
    replicationFactor: 3
```

### 3.4 Avro Schema Registry — Core Events

```json
// data/schemas/avro/cbe.breakdown.approved.avsc
{
  "type": "record",
  "name": "BreakdownApproved",
  "namespace": "ici.cbe.v1",
  "fields": [
    {"name": "event_id",       "type": "string"},
    {"name": "breakdown_id",   "type": "string"},
    {"name": "bom_item_id",    "type": "string"},
    {"name": "tenant_id",      "type": "string"},
    {"name": "unit_cost_eur",  "type": "double"},
    {"name": "total_cost_eur", "type": "double"},
    {"name": "confidence",     "type": {"type": "enum", "name": "Confidence",
                                        "symbols": ["HIGH","MEDIUM","LOW"]}},
    {"name": "location",       "type": "string"},
    {"name": "quantity",       "type": "int"},
    {"name": "occurred_at",    "type": "string"}
  ]
}
```

```json
// data/schemas/avro/pfe.forecast.ready.avsc
{
  "type": "record",
  "name": "ForecastReady",
  "namespace": "ici.pfe.v1",
  "fields": [
    {"name": "event_id",       "type": "string"},
    {"name": "forecast_id",    "type": "string"},
    {"name": "commodity",      "type": "string"},
    {"name": "tenant_id",      "type": "string"},
    {"name": "horizon_days",   "type": "int"},
    {"name": "mape_30d",       "type": "double"},
    {"name": "model_type",     "type": "string"},
    {"name": "generated_at",   "type": "string"}
  ]
}
```

```json
// data/schemas/avro/crae.risk.critical.avsc
{
  "type": "record",
  "name": "RiskCritical",
  "namespace": "ici.crae.v1",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "risk_factor_id",  "type": "string"},
    {"name": "tenant_id",       "type": "string"},
    {"name": "category",        "type": "string"},
    {"name": "domain",          "type": "string"},
    {"name": "score",           "type": "double"},
    {"name": "impact_eur",      "type": "double"},
    {"name": "description",     "type": "string"},
    {"name": "occurred_at",     "type": "string"}
  ]
}
```

### 3.5 Consumer Groups

```python
# libs/shared-kafka/src/shared_kafka/consumer.py
from aiokafka import AIOKafkaConsumer
import asyncio, logging

logger = logging.getLogger(__name__)


class BaseConsumer:
    GROUP_ID: str = ""         # override in subclass
    TOPICS: list[str] = []     # override in subclass
    MAX_POLL_INTERVAL_MS = 300_000

    def __init__(self, brokers: str, deserializer) -> None:
        self._brokers = brokers
        self._deserializer = deserializer
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *self.TOPICS,
            bootstrap_servers=self._brokers,
            group_id=self.GROUP_ID,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            max_poll_interval_ms=self.MAX_POLL_INTERVAL_MS,
        )
        await self._consumer.start()
        logger.info("Consumer %s started on %s", self.GROUP_ID, self.TOPICS)

    async def run_forever(self) -> None:
        async for msg in self._consumer:
            try:
                event = self._deserializer(msg.value)
                await self.handle(event)
                await self._consumer.commit()
            except Exception as exc:
                logger.error("Consumer %s failed on %s: %s", self.GROUP_ID, msg.offset, exc)
                # Send to DLQ instead of crashing
                await self._send_to_dlq(msg, exc)

    async def handle(self, event: dict) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        if self._consumer:
            await self._consumer.stop()

    async def _send_to_dlq(self, msg, exc: Exception) -> None:
        # publish to <topic>.dlq
        pass
```

### 3.6 Saga Pattern — RFQ Award Saga

```python
# services/rfq-agent/src/rfq_agent/sagas/award_saga.py
"""
Distributed saga: RFQ Award
Steps:
  1. rfq_agent:     mark RFQ as AWARDED        (compensate: reopen)
  2. supplier_engine: update supplier metrics  (compensate: revert)
  3. cost_engine:   lock breakdown price        (compensate: unlock)
  4. learning_engine: record winning offer      (compensate: delete)

On any failure → publish compensating events in reverse order.
"""
from enum import Enum
from dataclasses import dataclass, field
from uuid import UUID, uuid4
import logging

logger = logging.getLogger(__name__)


class SagaStep(str, Enum):
    AWARD_RFQ          = "AWARD_RFQ"
    UPDATE_SUPPLIER    = "UPDATE_SUPPLIER"
    LOCK_BREAKDOWN     = "LOCK_BREAKDOWN"
    RECORD_FEEDBACK    = "RECORD_FEEDBACK"


@dataclass
class AwardSagaState:
    saga_id: UUID = field(default_factory=uuid4)
    rfq_id: UUID = field(default=None)
    completed_steps: list[SagaStep] = field(default_factory=list)
    failed_step: SagaStep | None = None
    compensated: bool = False


class AwardSagaOrchestrator:
    def __init__(self, kafka_producer, rfq_repo, se_client, cbe_client, le_client) -> None:
        self._producer = kafka_producer
        self._rfq = rfq_repo
        self._se = se_client
        self._cbe = cbe_client
        self._le = le_client

    async def execute(self, rfq_id: UUID, winner_supplier_id: UUID, offer_id: UUID) -> AwardSagaState:
        state = AwardSagaState(rfq_id=rfq_id)
        steps = [
            (SagaStep.AWARD_RFQ,       self._rfq.mark_awarded,     rfq_id),
            (SagaStep.UPDATE_SUPPLIER,  self._se.update_win_rate,   winner_supplier_id),
            (SagaStep.LOCK_BREAKDOWN,   self._cbe.lock_price,       rfq_id),
            (SagaStep.RECORD_FEEDBACK,  self._le.record_offer,      offer_id),
        ]
        for step, fn, arg in steps:
            try:
                await fn(arg)
                state.completed_steps.append(step)
            except Exception as exc:
                logger.error("Saga %s failed at %s: %s", state.saga_id, step, exc)
                state.failed_step = step
                await self._compensate(state)
                break
        return state

    async def _compensate(self, state: AwardSagaState) -> None:
        compensations = {
            SagaStep.AWARD_RFQ:       self._rfq.reopen,
            SagaStep.UPDATE_SUPPLIER:  self._se.revert_win_rate,
            SagaStep.LOCK_BREAKDOWN:   self._cbe.unlock_price,
            SagaStep.RECORD_FEEDBACK:  self._le.delete_offer,
        }
        for step in reversed(state.completed_steps):
            try:
                await compensations[step](state.rfq_id)
            except Exception as exc:
                logger.critical("Compensation failed for %s: %s", step, exc)
        state.compensated = True
```

---

## 4. API Gateway

### 4.1 Kong Gateway Configuration

```yaml
# infra/k8s/helm/charts/api-gateway/kong.yaml
_format_version: "3.0"

services:
  - name: material-engine
    url: http://material-engine:8001
    routes:
      - name: me-routes
        paths: ["/me/v1"]
        strip_path: false

  - name: cost-engine
    url: http://cost-engine:8005
    routes:
      - name: cbe-routes
        paths: ["/cbe/v1"]
        strip_path: false

  - name: forecast-engine
    url: http://forecast-engine:8008
    routes:
      - name: pfe-routes
        paths: ["/pfe/v1"]
        strip_path: false

  - name: risk-engine
    url: http://risk-engine:8009
    routes:
      - name: crae-routes
        paths: ["/crae/v1"]
        strip_path: false

plugins:
  # Auth — JWT RS256 via Keycloak JWKS
  - name: jwt
    config:
      key_claim_name: kid
      claims_to_verify: [exp, nbf]
      uri_param_names: []

  # Rate limiting per tenant (Redis)
  - name: rate-limiting
    config:
      minute: 1000
      hour: 20000
      policy: redis
      redis_host: redis
      redis_port: 6379
      limit_by: header
      header_name: X-Tenant-ID

  # Request ID injection
  - name: correlation-id
    config:
      header_name: X-Request-ID
      generator: uuid#counter
      echo_downstream: true

  # Response caching (GET /forecasts, /risks)
  - name: proxy-cache
    config:
      response_code: [200]
      request_method: [GET]
      content_type: ["application/json"]
      cache_ttl: 300
      storage_ttl: 3600
      strategy: memory

  # OpenTelemetry tracing
  - name: opentelemetry
    config:
      endpoint: http://otel-collector:4317
      resource_attributes:
        service.name: api-gateway
      propagation:
        default_format: W3C

  # CORS
  - name: cors
    config:
      origins: ["https://*.ici-platform.com"]
      methods: [GET, POST, PUT, PATCH, DELETE, OPTIONS]
      headers: [Authorization, Content-Type, X-Tenant-ID, X-Request-ID]
      max_age: 3600
```

### 4.2 API Versioning Strategy

```
/v1/ — current stable (maintained ≥ 18 months after v2 release)
/v2/ — next generation (breaking changes)

Headers:
  X-API-Version: 2025-01-01   (date-based for non-breaking evolution)
  Deprecation: Sat, 01 Jan 2027 00:00:00 GMT
  Sunset: Sat, 01 Jan 2028 00:00:00 GMT
```

### 4.3 Custom Gateway Middleware (FastAPI fallback)

```python
# apps/api-gateway/src/gateway/middleware/auth.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import httpx, jwt as pyjwt
from cachetools import TTLCache

_jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)


class JWTMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwks_url: str) -> None:
        super().__init__(app)
        self._jwks_url = jwks_url

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/metrics", "/docs", "/openapi.json"):
            return await call_next(request)

        token = self._extract_token(request)
        if not token:
            raise HTTPException(401, "Missing Authorization header")

        try:
            payload = await self._decode(token)
        except Exception as exc:
            raise HTTPException(401, f"Invalid token: {exc}") from exc

        request.state.user_id  = payload["sub"]
        request.state.tenant_id = payload.get("tenant_id", "default")
        request.state.roles    = payload.get("roles", [])
        return await call_next(request)

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    async def _decode(self, token: str) -> dict:
        jwks = await self._get_jwks()
        header = pyjwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)
        if not key:
            raise ValueError("Unknown kid")
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(key)
        return pyjwt.decode(token, public_key, algorithms=["RS256"], audience="ici-platform")

    async def _get_jwks(self) -> dict:
        if "jwks" in _jwks_cache:
            return _jwks_cache["jwks"]
        async with httpx.AsyncClient() as client:
            resp = await client.get(self._jwks_url, timeout=5)
            resp.raise_for_status()
            _jwks_cache["jwks"] = resp.json()
        return _jwks_cache["jwks"]
```

### 4.4 Rate Limiting Tiers

| Tier | Plan | Requests/min | Burst | Concurrent |
|------|------|-------------|-------|------------|
| T0 | Internal services | Unlimited | — | — |
| T1 | Free / Trial | 60 | 10 | 5 |
| T2 | Professional | 600 | 100 | 20 |
| T3 | Enterprise | 3 000 | 500 | 100 |
| T4 | Enterprise+ | 10 000 | 2 000 | 500 |

### 4.5 API Gateway — Service Mesh (Istio)

```yaml
# infra/k8s/base/istio/virtual-service-cbe.yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: cost-engine-vs
  namespace: ici
spec:
  hosts: ["cost-engine"]
  http:
    - match:
        - headers:
            x-canary:
              exact: "true"
      route:
        - destination:
            host: cost-engine
            subset: canary
          weight: 100
    - route:
        - destination:
            host: cost-engine
            subset: stable
          weight: 100
  retries:
    attempts: 3
    perTryTimeout: 30s
    retryOn: "5xx,reset,connect-failure"
---
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: cost-engine-dr
  namespace: ici
spec:
  host: cost-engine
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        h2UpgradePolicy: UPGRADE
        http1MaxPendingRequests: 100
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 30s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
  subsets:
    - name: stable
      labels:
        version: stable
    - name: canary
      labels:
        version: canary
```

### 4.6 OpenAPI Aggregation

```python
# apps/api-gateway/src/gateway/openapi.py
"""Fetches OpenAPI specs from all services and merges into platform spec."""
import asyncio, httpx
from fastapi.openapi.utils import get_openapi

SERVICE_SPECS = {
    "material-engine":  "http://material-engine:8001/openapi.json",
    "cost-engine":      "http://cost-engine:8005/openapi.json",
    "forecast-engine":  "http://forecast-engine:8008/openapi.json",
    "risk-engine":      "http://risk-engine:8009/openapi.json",
    "rfq-agent":        "http://rfq-agent:8010/openapi.json",
}


async def fetch_platform_spec() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        specs = await asyncio.gather(*[
            client.get(url) for url in SERVICE_SPECS.values()
        ], return_exceptions=True)

    merged_paths: dict = {}
    merged_components: dict = {"schemas": {}}

    for (svc_name, _), spec_resp in zip(SERVICE_SPECS.items(), specs):
        if isinstance(spec_resp, Exception):
            continue
        spec = spec_resp.json()
        for path, path_item in spec.get("paths", {}).items():
            merged_paths[path] = path_item
        for name, schema in spec.get("components", {}).get("schemas", {}).items():
            merged_components["schemas"][f"{svc_name}__{name}"] = schema

    return {
        "openapi": "3.1.0",
        "info": {"title": "Industrial Cost Intelligence Platform API", "version": "1.0.0"},
        "paths": merged_paths,
        "components": merged_components,
    }
```
