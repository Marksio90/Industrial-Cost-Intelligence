# ICI Platform — Observability, Security & Multi-Tenant Design

## 10. Observability Stack

### 10.1 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   ICI Observability Stack                    │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐   │
│  │         Instrumentation (per service)                 │   │
│  │  FastAPI → prometheus-fastapi-instrumentator          │   │
│  │  Python  → opentelemetry-sdk (traces + logs)         │   │
│  │  Custom  → prometheus_client (domain metrics)        │   │
│  └────────────────────┬──────────────────────────────────┘   │
│                       │                                      │
│         ┌─────────────┼───────────────────┐                  │
│         │             │                   │                  │
│         ▼             ▼                   ▼                  │
│   Prometheus      OTEL Collector      structlog              │
│   (pull /metrics) (push OTLP)         (JSON stdout)         │
│         │             │                   │                  │
│         │         ┌───┴─────┐             │                  │
│         │         │         │             │                  │
│         │         ▼         ▼             ▼                  │
│         │     Jaeger     Tempo        Loki                   │
│         │     (traces)   (traces)     (logs)                 │
│         │                                                    │
│         ▼                                                    │
│     Grafana (unified dashboards: metrics + traces + logs)   │
│         │                                                    │
│         ▼                                                    │
│     Alertmanager (PD + Slack + Email)                       │
└──────────────────────────────────────────────────────────────┘
```

### 10.2 Platform-wide Prometheus Metrics

All services expose `/metrics` (standard Prometheus format). Shared baseline metrics from `shared-observability` lib:

```python
# libs/shared-observability/src/shared_observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge, Info
import platform, os

# Base labels injected automatically
BASE_LABELS = {
    "service":     os.getenv("SERVICE_NAME", "unknown"),
    "environment": os.getenv("ENVIRONMENT", "dev"),
    "version":     os.getenv("IMAGE_TAG", "local"),
}

# HTTP
http_requests_total = Counter(
    "ici_http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status_code"],
)
http_duration_seconds = Histogram(
    "ici_http_duration_seconds",
    "HTTP request duration",
    ["service", "method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Database
db_query_duration_seconds = Histogram(
    "ici_db_query_duration_seconds",
    "PostgreSQL query duration",
    ["service", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)
db_pool_size = Gauge(
    "ici_db_pool_size",
    "asyncpg pool connections",
    ["service", "state"],   # state: active | idle | waiting
)

# Kafka
kafka_events_produced_total = Counter(
    "ici_kafka_events_produced_total",
    "Kafka events produced",
    ["service", "topic"],
)
kafka_events_consumed_total = Counter(
    "ici_kafka_events_consumed_total",
    "Kafka events consumed",
    ["service", "topic", "consumer_group"],
)
kafka_outbox_lag_seconds = Gauge(
    "ici_kafka_outbox_lag_seconds",
    "Seconds between event creation and publication",
    ["service"],
)

# Cache
cache_hits_total   = Counter("ici_cache_hits_total",   "Redis cache hits",   ["service", "key_prefix"])
cache_misses_total = Counter("ici_cache_misses_total", "Redis cache misses", ["service", "key_prefix"])

# Multi-tenant
tenant_requests_total = Counter(
    "ici_tenant_requests_total",
    "Requests per tenant",
    ["service", "tenant_id"],
)
```

### 10.3 Consolidated Metric Inventory

| Service | Key Metrics | SLO |
|---------|-------------|-----|
| api-gateway | `ici_http_duration_seconds` p95 | ≤ 100ms |
| material-engine | `me_material_lookup_duration` p95 | ≤ 200ms |
| supplier-offer-parser | `sop_parse_duration_seconds` p95 | ≤ 30s |
| cost-engine | `cbe_breakdown_duration_seconds` p95 | ≤ 2s |
| forecast-engine | `pfe_forecast_duration_seconds` p95 | ≤ 120s |
| risk-engine | `crae_analysis_duration_seconds` p95 | ≤ 5s |
| learning-engine | `le_retrain_duration_seconds` | ≤ 3600s |
| All services | `ici_http_requests_total{status_code=~"5.."}` / total | < 1% |
| All services | `ici_kafka_outbox_lag_seconds` | ≤ 60s |

### 10.4 OpenTelemetry Tracing

```python
# libs/shared-observability/src/shared_observability/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
import os


def configure_tracing(service_name: str) -> trace.Tracer:
    resource = Resource(attributes={
        "service.name":        service_name,
        "service.version":     os.getenv("IMAGE_TAG", "local"),
        "deployment.environment": os.getenv("ENVIRONMENT", "dev"),
        "tenant.id":           "platform",   # overridden per-request via baggage
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_ENDPOINT", "http://otel-collector:4317"),
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument
    FastAPIInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()
    RedisInstrumentor().instrument()

    return trace.get_tracer(service_name)
```

### 10.5 Structured Logging

```python
# libs/shared-observability/src/shared_observability/logging.py
import structlog, logging, sys, os


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )


def bind_request_context(
    request_id: str, tenant_id: str, user_id: str, service: str
) -> None:
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        tenant_id=tenant_id,
        user_id=user_id,
        service=service,
    )
```

### 10.6 Grafana Dashboard Inventory

| Dashboard | Panels | Audience |
|-----------|--------|----------|
| Platform Overview | Error rate, p95 latency, active tenants, Kafka lag | Ops |
| Cost Engine (CBE) | Breakdown throughput, confidence distribution, location comparison | Engineering |
| Forecast Engine (PFE) | MAPE 30d per commodity, drift PSI, retrain schedule | Data Science |
| Risk Engine (CRAE) | Portfolio VaR, risk by domain/level, critical count, alert rate | Risk Team |
| Supplier Offer Parser | Parse throughput, rejection rate, NLP model latency | Engineering |
| Data Lake | S3 ingest rate, Glue job duration, Athena query cost | Platform |
| ML Pipeline | Training duration, model accuracy by version, active learning samples | Data Science |
| Multi-Tenant | Per-tenant request rate, error rate, quota usage | Product |
| Infrastructure | Pod count, CPU/mem by service, DB connections, Kafka consumer lag | Ops |

### 10.7 Alertmanager Rules

```yaml
# infra/k8s/helm/charts/alertmanager/rules/platform.yaml
groups:
  - name: ici-platform-slos
    interval: 1m
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(ici_http_requests_total{status_code=~"5.."}[5m])) by (service)
          / sum(rate(ici_http_requests_total[5m])) by (service) > 0.01
        for: 5m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "{{ $labels.service }} error rate > 1%"
          runbook: "https://docs.ici-platform.com/runbooks/high-error-rate"

      - alert: APILatencyHigh
        expr: |
          histogram_quantile(0.95,
            sum(rate(ici_http_duration_seconds_bucket[5m])) by (service, le)
          ) > 1.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.service }} p95 latency > 1s"

      - alert: KafkaOutboxLag
        expr: ici_kafka_outbox_lag_seconds > 60
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.service }} Kafka outbox lag > 60s"

      - alert: CostEngineBreakdownSlow
        expr: |
          histogram_quantile(0.95,
            rate(cbe_breakdown_duration_seconds_bucket[5m])
          ) > 2.0
        for: 5m
        labels:
          severity: warning

      - alert: ForecastMAPEBreach
        expr: pfe_mape_30d{commodity="STEEL_HRC"} > 0.06
        for: 60m
        labels:
          severity: warning
          team: data-science

      - alert: RiskPortfolioVaRBreach
        expr: crae_portfolio_var_eur > 5000000
        for: 1m
        labels:
          severity: critical
          team: risk

      - alert: PodCrashLooping
        expr: |
          rate(kube_pod_container_status_restarts_total{namespace="ici"}[15m]) > 0
        for: 5m
        labels:
          severity: critical

      - alert: DBConnectionPoolExhausted
        expr: ici_db_pool_size{state="waiting"} > 5
        for: 2m
        labels:
          severity: warning
```

---

## 11. Security Architecture

### 11.1 Security Layers

```
┌────────────────────────────────────────────────────────────┐
│                   Security Architecture                    │
│                                                            │
│  L1 — Perimeter                                            │
│       AWS WAF → CloudFront → ALB                          │
│       DDoS: AWS Shield Standard                           │
│       GeoIP blocking: configurable per tenant             │
│                                                            │
│  L2 — API Gateway                                          │
│       Kong: JWT validation, rate limiting, CORS           │
│       mTLS between gateway and services (Istio)           │
│                                                            │
│  L3 — Authentication & Authorization                       │
│       Keycloak (OIDC / OAuth 2.0)                         │
│       JWT RS256, 15-min expiry, refresh token 8h          │
│       Per-module RBAC (service-scoped roles)              │
│       Tenant claims in JWT: tenant_id                     │
│                                                            │
│  L4 — Service Mesh                                         │
│       Istio mTLS (STRICT mode, all namespaces)            │
│       Network Policies (default deny ingress)             │
│       SPIFFE/SVID identity per pod                        │
│                                                            │
│  L5 — Data                                                 │
│       PostgreSQL: TLS 1.3 in-transit                      │
│       RDS encryption at-rest (AES-256)                    │
│       S3 SSE-KMS (per-tenant CMK)                         │
│       Redis TLS + AUTH token                              │
│       Kafka TLS + SASL/SCRAM                              │
│                                                            │
│  L6 — Secrets Management                                   │
│       AWS Secrets Manager (prod)                          │
│       Vault (on-prem option)                              │
│       K8s ExternalSecrets Operator                        │
│       Zero plaintext secrets in git                       │
│                                                            │
│  L7 — Supply Chain                                         │
│       Sigstore Cosign (image signing)                     │
│       SLSA Level 2 (provenance attestation)               │
│       Trivy in CI (CRITICAL/HIGH block)                   │
│       OWASP Dep-Check (CVSS ≥ 7 block)                   │
└────────────────────────────────────────────────────────────┘
```

### 11.2 Keycloak Realm Configuration

```json
// data/seeds/keycloak-realm.json (excerpt)
{
  "realm": "ici",
  "enabled": true,
  "sslRequired": "external",
  "accessTokenLifespan": 900,
  "refreshTokenMaxReuse": 0,
  "revokeRefreshToken": true,
  "clients": [
    {
      "clientId": "ici-platform",
      "publicClient": false,
      "bearerOnly": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": true,
      "authorizationServicesEnabled": true,
      "defaultClientScopes": ["openid", "profile", "email", "roles"],
      "protocolMappers": [
        {
          "name": "tenant-id-mapper",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-user-attribute-mapper",
          "config": {
            "user.attribute":   "tenant_id",
            "claim.name":       "tenant_id",
            "id.token.claim":   "true",
            "access.token.claim": "true"
          }
        }
      ]
    }
  ],
  "roles": {
    "realm": [
      {"name": "ICI_VIEWER"},
      {"name": "ICI_ANALYST"},
      {"name": "ICI_ENGINEER"},
      {"name": "ICI_ADMIN"},
      {"name": "CBE_VIEWER"}, {"name": "CBE_ANALYST"},
      {"name": "CBE_ENGINEER"}, {"name": "CBE_ADMIN"},
      {"name": "PFE_VIEWER"}, {"name": "PFE_ANALYST"},
      {"name": "PFE_ENGINEER"}, {"name": "PFE_ADMIN"},
      {"name": "CRAE_VIEWER"}, {"name": "CRAE_ANALYST"},
      {"name": "CRAE_RISK_MANAGER"}, {"name": "CRAE_APPROVER"}, {"name": "CRAE_ADMIN"},
      {"name": "SOP_VIEWER"}, {"name": "SOP_OPERATOR"},
      {"name": "SOP_SUPERVISOR"}, {"name": "SOP_ADMIN"},
      {"name": "RFQ_VIEWER"}, {"name": "RFQ_BUYER"},
      {"name": "RFQ_MANAGER"}, {"name": "RFQ_ADMIN"}
    ]
  },
  "passwordPolicy": "length(12) and upperCase(1) and digits(1) and specialChars(1) and notRecentlyUsed(10)",
  "bruteForceProtected": true,
  "failureFactor": 5,
  "waitIncrementSeconds": 60,
  "maxFailureWaitSeconds": 900
}
```

### 11.3 RBAC Matrix (Cross-Module)

| Role Group | ME | PE | SE | SOP | CBE | BOM | CAD | PFE | CRAE | RFQ |
|------------|----|----|----|----|-----|-----|-----|-----|------|-----|
| `ICI_VIEWER` | R | R | R | R | R | R | R | R | R | R |
| `ICI_ANALYST` | R | R | R | R+E | R+E | R | R | R+E | R+E | R+E |
| `ICI_ENGINEER` | R+W | R+W | R+W | R+W+E | R+W+E | R+W | R+W | R+W+E | R+W | R+W |
| `ICI_ADMIN` | ALL | ALL | ALL | ALL | ALL | ALL | ALL | ALL | ALL | ALL |

R=Read, W=Write, E=Export, ALL=Full CRUD+Delete+Config

### 11.4 Istio PeerAuthentication (mTLS STRICT)

```yaml
# infra/k8s/base/istio/peer-auth.yaml
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: ici
spec:
  mtls:
    mode: STRICT
---
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: cost-engine-authz
  namespace: ici
spec:
  selector:
    matchLabels:
      app: cost-engine
  action: ALLOW
  rules:
    - from:
        - source:
            principals:
              - "cluster.local/ns/ici/sa/api-gateway-sa"
              - "cluster.local/ns/ici/sa/rfq-agent-sa"
              - "cluster.local/ns/ici/sa/risk-engine-sa"
              - "cluster.local/ns/monitoring/sa/prometheus-sa"
      to:
        - operation:
            methods: [GET, POST, PUT, PATCH]
            paths: ["/cbe/v1/*", "/metrics", "/health/*"]
```

### 11.5 Secret Management (ExternalSecrets)

```yaml
# infra/k8s/base/external-secrets/cost-engine-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: cost-engine-secrets
  namespace: ici
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: cost-engine-secrets
    creationPolicy: Owner
    deletionPolicy: Retain
    template:
      engineVersion: v2
      data:
        database_url: "{{ .pg_url }}"
  data:
    - secretKey: pg_url
      remoteRef:
        key: ici/prod/cost-engine
        property: database_url
```

### 11.6 Security Scanning CI Gates

| Stage | Tool | Blocks On |
|-------|------|-----------|
| SAST | Semgrep (p/python, p/owasp-top-ten) | Any CRITICAL finding |
| Dependency | OWASP Dependency-Check | CVSS ≥ 7.0 |
| Container | Trivy (image scan) | CRITICAL / HIGH CVE |
| Secrets | Gitleaks | Any secret in diff |
| IaC | Checkov (Terraform + K8s) | CRITICAL misconfiguration |
| DAST | OWASP ZAP (staging) | CRITICAL / HIGH alert |

---

## 12. Multi-Tenant Design

### 12.1 Tenancy Model: Schema-per-Tenant

```
ICI Platform: Hybrid tenancy model

Shared infrastructure:
  ✓ Kubernetes cluster (namespace isolation)
  ✓ Kafka cluster (topic-level isolation via ACL)
  ✓ Redis (key prefix isolation: tenant:{id}:...)
  ✓ Qdrant (payload filter isolation)
  ✓ Observability stack

Isolated per tenant:
  ✓ PostgreSQL: schema per tenant per service
      cbe_tenant_acme, cbe_tenant_siemens, cbe_tenant_bosch
  ✓ S3 data lake: prefix per tenant
      s3://ici-lake/tenants/{tenant_id}/
  ✓ Keycloak: realm or client per tenant
  ✓ KMS key per tenant (S3 SSE)
  ✓ RateLimit quota per tenant (Kong)
```

### 12.2 Tenant Context Propagation

```python
# libs/shared-auth/src/shared_auth/tenant.py
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_tenant_ctx: ContextVar["TenantContext"] = ContextVar("tenant_ctx")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    plan: str               # FREE | PROFESSIONAL | ENTERPRISE
    db_schema_prefix: str   # e.g. "cbe_acme"
    rate_limit_tier: int    # 1-4
    feature_flags: frozenset[str]

    @classmethod
    def from_jwt(cls, payload: dict[str, Any]) -> "TenantContext":
        tid = payload["tenant_id"]
        return cls(
            tenant_id=tid,
            plan=payload.get("plan", "PROFESSIONAL"),
            db_schema_prefix=f"{{service}}_{tid.replace('-', '_')}",
            rate_limit_tier=payload.get("rate_tier", 2),
            feature_flags=frozenset(payload.get("features", [])),
        )


def get_tenant() -> TenantContext:
    return _tenant_ctx.get()


def set_tenant(ctx: TenantContext) -> None:
    _tenant_ctx.set(ctx)
```

### 12.3 Tenant-Aware PostgreSQL Queries

```python
# libs/shared-db/src/shared_db/tenant_repo.py
import asyncpg
from shared_auth.tenant import get_tenant


class TenantAwareRepository:
    """Sets search_path to tenant schema on every connection acquisition."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def acquire(self) -> asyncpg.Connection:
        tenant = get_tenant()
        conn = await self._pool.acquire()
        await conn.execute(f"SET search_path TO {tenant.db_schema_prefix}, shared, public")
        return conn

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        async with await self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with await self.acquire() as conn:
            return await conn.execute(query, *args)
```

### 12.4 Tenant Schema Provisioning

```python
# scripts/provision_tenant.py
"""Run at tenant onboarding to create all service schemas."""
import asyncio, asyncpg
from typing import Awaitable


SERVICES = ["me", "pe", "se", "sop", "cbe", "bom", "cad", "pfe", "crae", "rfq", "le"]

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {user};
GRANT USAGE ON SCHEMA {schema} TO {user};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {user};
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema}
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {user};
"""


async def provision_tenant(pool: asyncpg.Pool, tenant_id: str) -> None:
    safe_id = tenant_id.replace("-", "_").lower()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for svc in SERVICES:
                schema = f"{svc}_{safe_id}"
                user   = f"ici_{svc}_user"
                await conn.execute(SCHEMA_SQL.format(schema=schema, user=user))
                # Run Alembic migration for this schema
                # (called via subprocess: alembic -x schema=cbe_acme upgrade head)
    print(f"Tenant {tenant_id} provisioned: {len(SERVICES)} schemas")
```

### 12.5 Kafka Tenant Isolation

```python
# libs/shared-kafka/src/shared_kafka/tenant_producer.py
"""
Tenant isolation in Kafka:
  - Topic ACLs: each tenant's service account can only produce to its own topics
  - Alternatively: tenant_id in Avro payload + consumer-side filter
  - Premium tenants: dedicated topic partitions (partition.assignment.strategy)
"""
from shared_auth.tenant import get_tenant
from aiokafka import AIOKafkaProducer


class TenantAwareProducer:
    def __init__(self, producer: AIOKafkaProducer, serializer) -> None:
        self._producer = producer
        self._serializer = serializer

    async def send(self, topic: str, payload: dict, key: str | None = None) -> None:
        tenant = get_tenant()
        enriched = {**payload, "tenant_id": tenant.tenant_id}
        value = self._serializer(enriched)
        await self._producer.send_and_wait(
            topic,
            value=value,
            key=(key or tenant.tenant_id).encode(),
            headers=[("tenant_id", tenant.tenant_id.encode())],
        )
```

### 12.6 Tenant Feature Flags

```python
# libs/shared-auth/src/shared_auth/features.py
from enum import StrEnum
from .tenant import get_tenant


class Feature(StrEnum):
    VECTOR_SIMILARITY_SEARCH = "vector_similarity_search"
    LSTM_FORECASTING         = "lstm_forecasting"
    MONTE_CARLO_VAR          = "monte_carlo_var"
    RFQ_AUTOMATION           = "rfq_automation"
    DATA_LAKE_EXPORT         = "data_lake_export"
    MULTI_LOCATION_COMPARE   = "multi_location_compare"
    ACTIVE_LEARNING          = "active_learning"
    STRESS_TEST_SCENARIOS    = "stress_test_scenarios"


def feature_enabled(feature: Feature) -> bool:
    return feature in get_tenant().feature_flags
```

### 12.7 Tenant Plan → Feature Matrix

| Feature | FREE | PROFESSIONAL | ENTERPRISE |
|---------|:----:|:------------:|:----------:|
| Cost Breakdown (CBE) | ✓ | ✓ | ✓ |
| BOM Management | 5 BOMs | ✓ | ✓ |
| Supplier Offer Parser | — | ✓ | ✓ |
| Price Forecasting (SARIMA) | — | ✓ | ✓ |
| Price Forecasting (LSTM/Ensemble) | — | — | ✓ |
| Risk Analysis (CRAE) | — | ✓ | ✓ |
| Monte Carlo VaR | — | — | ✓ |
| Stress Test Scenarios | — | — | ✓ |
| Vector Similarity Search | — | — | ✓ |
| Data Lake Export | — | — | ✓ |
| Multi-Location Compare | — | ✓ | ✓ |
| Active Learning (LE) | — | — | ✓ |
| RFQ Automation | — | ✓ | ✓ |
| API Rate Limit | 60/min | 600/min | 10k/min |
| SLA | — | 99.5% | 99.9% |
| Dedicated DB schema | ✓ | ✓ | ✓ |
| Dedicated KMS key | — | — | ✓ |

### 12.8 Tenant Isolation Test Matrix

| Scenario | Expected | Tested By |
|----------|----------|-----------|
| Tenant A cannot read Tenant B data | 403 | pytest integration |
| Tenant A search_path doesn't leak to Tenant B | assert schema | DB isolation test |
| Kafka consumer filters by tenant_id | only own events | consumer unit test |
| Redis key collisions impossible | key prefix unique | shared-cache test |
| Rate limit applies per-tenant | Tenant B unaffected by Tenant A burst | k6 multi-tenant |
| Vector search scoped to tenant_id filter | no cross-tenant results | Qdrant integration |
| JWT without tenant_id claim rejected | 401 | auth middleware test |
| Expired JWT rejected | 401 | auth middleware test |
| Feature flag enforcement (LSTM gated) | 403 for FREE plan | feature flag test |
