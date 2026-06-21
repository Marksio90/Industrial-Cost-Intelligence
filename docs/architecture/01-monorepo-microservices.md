# ICI Platform — Monorepo & Microservices Architecture

## 1. Monorepo Structure

```
industrial-cost-intelligence/
├── apps/
│   ├── api-gateway/              # Kong / custom FastAPI gateway
│   ├── web-ui/                   # React 18 + TypeScript SPA
│   └── admin-portal/             # Internal ops dashboard
│
├── services/
│   ├── material-engine/          # ME  — material catalog & specs
│   ├── process-engine/           # PE  — manufacturing process library
│   ├── supplier-engine/          # SE  — supplier management
│   ├── cost-engine/              # CBE — cost breakdown engine
│   ├── rfq-agent/                # RFQ — RFQ generation & negotiation
│   ├── learning-engine/          # LE  — ML feedback & active learning
│   ├── bom-engine/               # BOM — bill of materials management
│   ├── cad-engine/               # CAD — CAD parsing & feature extraction
│   ├── forecast-engine/          # PFE — price forecasting (SARIMA/Prophet/LSTM)
│   ├── risk-engine/              # CRAE— cost risk analysis
│   └── supplier-offer-parser/    # SOP — offer ingestion & NLP
│
├── libs/
│   ├── shared-models/            # Pydantic V2 cross-service DTOs
│   ├── shared-auth/              # JWT RS256 validation, RBAC helpers
│   ├── shared-kafka/             # Avro producer/consumer base classes
│   ├── shared-db/                # asyncpg pool factory, migration helpers
│   ├── shared-cache/             # Redis client factory, TTL decorators
│   ├── shared-observability/     # Prometheus metrics base, structlog config
│   └── shared-testing/           # testcontainers fixtures, factory_boy
│
├── infra/
│   ├── k8s/
│   │   ├── base/                 # Kustomize base manifests
│   │   ├── overlays/
│   │   │   ├── dev/
│   │   │   ├── staging/
│   │   │   └── prod/
│   │   └── helm/
│   │       ├── ici-platform/     # umbrella chart
│   │       └── charts/           # per-service charts
│   ├── terraform/
│   │   ├── modules/
│   │   │   ├── eks/              # AWS EKS cluster
│   │   │   ├── rds/              # PostgreSQL 16 RDS
│   │   │   ├── msk/              # AWS MSK (Kafka)
│   │   │   ├── elasticache/      # Redis ElastiCache
│   │   │   └── s3-data-lake/     # S3 + Glue catalog
│   │   ├── envs/
│   │   │   ├── dev/
│   │   │   ├── staging/
│   │   │   └── prod/
│   │   └── main.tf
│   └── docker/
│       ├── base-python/          # python:3.12-slim base image
│       └── base-node/            # node:20-alpine base image
│
├── data/
│   ├── migrations/               # Alembic per-service (namespaced)
│   ├── seeds/                    # Dev/staging seed data
│   └── schemas/
│       ├── avro/                 # Kafka Avro schemas
│       └── json/                 # OpenAPI + JSON Schema
│
├── docs/
│   ├── architecture/             # THIS DIRECTORY
│   ├── modules/                  # Per-module documentation
│   ├── adr/                      # Architecture Decision Records
│   └── runbooks/                 # Operational runbooks
│
├── scripts/
│   ├── bootstrap.sh              # Dev environment setup
│   ├── db-migrate.sh             # Run Alembic across all services
│   └── gen-proto.sh              # Generate Avro / Protobuf artifacts
│
├── .github/
│   ├── workflows/                # CI/CD pipelines
│   └── CODEOWNERS
│
├── pyproject.toml                # Workspace root (uv workspaces)
├── package.json                  # pnpm workspace root
├── nx.json                       # Nx monorepo task orchestration
├── .editorconfig
└── CLAUDE.md
```

### 1.1 Toolchain

| Layer | Tooling |
|-------|---------|
| Python workspace | **uv** workspaces (PEP 517, fast lockfile) |
| JS/TS workspace | **pnpm** workspaces |
| Monorepo orchestration | **Nx** (affected builds, task pipeline, remote cache) |
| Task runner | **just** (Justfile per service + root) |
| Code generation | openapi-generator-cli (TypeScript client from FastAPI OpenAPI) |

### 1.2 Nx Task Pipeline

```json
// nx.json (excerpt)
{
  "targetDefaults": {
    "build": {
      "dependsOn": ["^build"],
      "cache": true,
      "inputs": ["production", "^production"]
    },
    "test": {
      "cache": true,
      "inputs": ["default", "^production", "{workspaceRoot}/pytest.ini"]
    },
    "lint": { "cache": true },
    "docker-build": { "dependsOn": ["build"], "cache": false }
  },
  "nxCloudAccessToken": "${NX_CLOUD_ACCESS_TOKEN}"
}
```

---

## 2. Microservices Architecture

### 2.1 Service Catalogue

| Service | Port | Schema | Primary Store | Replicas (prod) |
|---------|------|--------|---------------|-----------------|
| `api-gateway` | 8080 | — | Redis (rate limit) | 3–10 |
| `material-engine` | 8001 | `me` | PostgreSQL 16 | 2–6 |
| `process-engine` | 8002 | `pe` | PostgreSQL 16 | 2–4 |
| `supplier-engine` | 8003 | `se` | PostgreSQL 16 | 2–6 |
| `supplier-offer-parser` | 8004 | `sop` | PostgreSQL 16 | 2–8 |
| `cost-engine` | 8005 | `cbe` | PostgreSQL 16 | 2–8 |
| `bom-engine` | 8006 | `bom` | PostgreSQL 16 | 2–4 |
| `cad-engine` | 8007 | `cad` | PostgreSQL 16 + S3 | 1–4 (GPU) |
| `forecast-engine` | 8008 | `pfe` | PostgreSQL 16 | 2–6 |
| `risk-engine` | 8009 | `crae` | PostgreSQL 16 | 2–6 |
| `rfq-agent` | 8010 | `rfq` | PostgreSQL 16 | 2–6 |
| `learning-engine` | 8011 | `le` | PostgreSQL 16 + MLflow | 1–4 (GPU) |

### 2.2 Service Template

Every service follows a consistent internal structure:

```
services/<service-name>/
├── src/
│   └── <service>/
│       ├── __init__.py
│       ├── main.py               # FastAPI app factory
│       ├── config.py             # pydantic-settings BaseSettings
│       ├── dependencies.py       # FastAPI DI: db_pool, cache, kafka
│       ├── routers/
│       │   ├── __init__.py
│       │   └── v1/
│       │       └── <resource>.py
│       ├── services/             # Business logic (pure, testable)
│       ├── repositories/         # asyncpg DB access layer
│       ├── models/               # Pydantic V2 schemas
│       ├── events/
│       │   ├── producer.py       # Outbox publisher
│       │   └── consumer.py       # Kafka consumer workers
│       └── migrations/           # Alembic (service-scoped)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
├── Dockerfile
├── pyproject.toml
└── justfile
```

### 2.3 FastAPI Application Factory

```python
# src/<service>/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from shared_auth import JWTMiddleware
from shared_db import create_pool
from shared_cache import create_redis
from shared_kafka import create_producer
from shared_observability import configure_logging

from .config import Settings
from .routers.v1 import router as v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = app.state.settings = Settings()
    configure_logging(cfg.log_level)
    app.state.db   = await create_pool(cfg.database_url, min_size=5, max_size=20)
    app.state.cache = await create_redis(cfg.redis_url)
    app.state.kafka = await create_producer(cfg.kafka_brokers)
    yield
    await app.state.db.close()
    await app.state.cache.aclose()
    await app.state.kafka.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="${SERVICE_TITLE}",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(JWTMiddleware, jwks_url="${JWKS_URL}")
    app.add_middleware(CORSMiddleware, allow_origins=["*"])
    app.include_router(v1_router, prefix="/${service}/v1")
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    return app


app = create_app()
```

### 2.4 Service Communication Patterns

```
┌─────────────────────────────────────────────────────────────┐
│                    ICI Platform                             │
│                                                             │
│  ┌──────────┐   sync REST    ┌──────────────────────────┐  │
│  │  API GW  │ ─────────────► │  Service (FastAPI)       │  │
│  └──────────┘                └──────────┬───────────────┘  │
│                                         │                   │
│                               async Kafka events            │
│                                         │                   │
│                              ┌──────────▼───────────┐      │
│  Service A ──── Kafka ──────►│  Service B (consumer)│      │
│                              └──────────────────────┘      │
│                                                             │
│  Service C ──── gRPC ──────► Service D (internal, low-lat) │
└─────────────────────────────────────────────────────────────┘

Rules:
- Cross-domain (UI → service):     REST via API Gateway
- Same-domain sync (svc → svc):    gRPC (protobuf, mTLS)
- Async integration:               Kafka (Avro, Outbox Pattern)
- Cache invalidation:              Redis pub/sub
- ML model calls:                  gRPC (LearningEngine)
```

### 2.5 Inter-Service gRPC (low-latency paths)

```protobuf
// proto/cost_engine/v1/breakdown.proto
syntax = "proto3";
package ici.cost_engine.v1;

service CostEngineService {
  rpc GetBreakdown (BreakdownRequest) returns (BreakdownResponse);
  rpc GetUnitCost  (UnitCostRequest)  returns (UnitCostResponse);
}

message BreakdownRequest {
  string bom_item_id = 1;
  string location    = 2;
  uint32 quantity    = 3;
}

message BreakdownResponse {
  string  breakdown_id   = 1;
  double  unit_cost_eur  = 2;
  double  total_cost_eur = 3;
  string  confidence     = 4;   // HIGH / MEDIUM / LOW
  repeated CostComponent components = 5;
}

message CostComponent {
  string component_type = 1;
  double value_eur      = 2;
  double share_pct      = 3;
}
```

### 2.6 Shared Libraries API

```python
# libs/shared-kafka/src/shared_kafka/producer.py
from dataclasses import dataclass
from typing import Any
import uuid, json
from aiokafka import AIOKafkaProducer
from confluent_kafka.schema_registry.avro import AvroSerializer


@dataclass
class OutboxEvent:
    topic: str
    key: str
    payload: dict[str, Any]
    headers: dict[str, str] | None = None


class OutboxPublisher:
    BATCH_SIZE = 50
    POLL_INTERVAL_S = 0.5

    def __init__(self, pool, producer: AIOKafkaProducer, serializer: AvroSerializer) -> None:
        self._pool = pool
        self._producer = producer
        self._serializer = serializer

    async def run_forever(self) -> None:
        import asyncio
        while True:
            await self._flush_batch()
            await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _flush_batch(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT event_id, topic, key, payload, headers
                   FROM outbox_events
                   WHERE published_at IS NULL
                   ORDER BY created_at
                   LIMIT $1
                   FOR UPDATE SKIP LOCKED""",
                self.BATCH_SIZE,
            )
            for row in rows:
                value = self._serializer(row["payload"])
                await self._producer.send_and_wait(
                    row["topic"],
                    key=row["key"].encode(),
                    value=value,
                )
                await conn.execute(
                    "UPDATE outbox_events SET published_at = NOW() WHERE event_id = $1",
                    row["event_id"],
                )
```

### 2.7 Database Isolation

Each service owns its PostgreSQL schema. No cross-schema JOINs in application code — integration happens via Kafka events or read-model projections.

```sql
-- Per-service schema creation (run at deploy time)
CREATE SCHEMA IF NOT EXISTS me   AUTHORIZATION ici_me_user;
CREATE SCHEMA IF NOT EXISTS pe   AUTHORIZATION ici_pe_user;
CREATE SCHEMA IF NOT EXISTS se   AUTHORIZATION ici_se_user;
CREATE SCHEMA IF NOT EXISTS sop  AUTHORIZATION ici_sop_user;
CREATE SCHEMA IF NOT EXISTS cbe  AUTHORIZATION ici_cbe_user;
CREATE SCHEMA IF NOT EXISTS bom  AUTHORIZATION ici_bom_user;
CREATE SCHEMA IF NOT EXISTS cad  AUTHORIZATION ici_cad_user;
CREATE SCHEMA IF NOT EXISTS pfe  AUTHORIZATION ici_pfe_user;
CREATE SCHEMA IF NOT EXISTS crae AUTHORIZATION ici_crae_user;
CREATE SCHEMA IF NOT EXISTS rfq  AUTHORIZATION ici_rfq_user;
CREATE SCHEMA IF NOT EXISTS le   AUTHORIZATION ici_le_user;

-- Each service user has USAGE only on its own schema
GRANT USAGE ON SCHEMA cbe TO ici_cbe_user;
REVOKE ALL    ON SCHEMA me  FROM ici_cbe_user;
```

### 2.8 Configuration Management

```python
# libs/shared-auth/src/shared_auth/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Base settings — extended by each service."""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Infrastructure
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    kafka_brokers: str = "kafka:9092"
    schema_registry_url: str = "http://schema-registry:8081"

    # Auth
    jwks_url: str = "http://keycloak:8080/realms/ici/protocol/openid-connect/certs"
    jwt_algorithm: str = "RS256"
    jwt_audience: str = "ici-platform"

    # Observability
    log_level: str = "INFO"
    otel_endpoint: str = "http://otel-collector:4317"

    # Feature flags
    enable_cache: bool = True
    enable_outbox: bool = True
```
