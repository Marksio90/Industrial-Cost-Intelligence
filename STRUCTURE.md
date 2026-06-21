# ICI Platform — Monorepo Structure

```
Industrial-Cost-Intelligence/
│
├── Makefile                    # One-command task runner (make up / make test / …)
├── docker-compose.yml          # Base compose — all services
├── docker-compose.dev.yml      # Dev overrides — hot-reload, mailhog, pgAdmin
├── docker-compose.prod.yml     # Prod overrides — resource limits, healthchecks
├── .env.example                # Template — copy to .env before first run
├── .gitignore
│
├── scripts/                    # Shell + Python helper scripts
│   ├── bootstrap.sh            # Full one-command startup (called by make up)
│   ├── health-check.sh         # Validates every service is alive (make health)
│   ├── e2e-test.sh             # End-to-end integration test (make test)
│   ├── env-check.sh            # Validates .env completeness (make env-check)
│   └── seed_vectors.py         # Embeds materials + loads Qdrant (make seed-vectors)
│
├── backend/                    # FastAPI application (Python 3.11)
│   ├── Dockerfile              → docker/backend/Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/                # DB migration scripts
│   ├── scripts/                # Python scripts runnable inside container
│   │   └── seed_vectors.py
│   └── src/
│       ├── main.py             # App factory + middleware wiring
│       ├── config.py           # Pydantic settings (reads .env)
│       ├── database.py         # SQLAlchemy async engine + session factory
│       ├── security/           # Auth, RBAC, ABAC, encryption, audit
│       │   ├── auth.py         # JWT RS256, OAuth2 PKCE, token family rotation
│       │   ├── rbac.py         # 6-role hierarchy, 35 permissions
│       │   ├── abac.py         # Deny-overrides policy engine (9 built-in policies)
│       │   ├── api_hardening.py # SecurityHeaders, RateLimit, RequestValidation
│       │   ├── encryption.py   # AES-256-GCM field encryption, KEK ring
│       │   ├── secrets.py      # Multi-backend secrets manager
│       │   └── audit.py        # Tamper-evident audit log (chain hash)
│       ├── observability/      # Metrics, logging, tracing, incident detection
│       │   ├── metrics.py      # Prometheus counters/histograms
│       │   ├── logging.py      # structlog configuration + sensitive field redaction
│       │   ├── tracing.py      # OpenTelemetry + @traced decorator
│       │   ├── middleware.py   # ObservabilityMiddleware + /metrics endpoint
│       │   └── incident_detector.py # Threshold/statistical/rate-of-change rules
│       └── modules/            # Domain modules
│           ├── materials/      # Material master data
│           ├── processes/      # Manufacturing processes
│           ├── suppliers/      # Supplier intelligence
│           ├── costs/          # Cost snapshots + history
│           ├── rfq/            # RFQ session management
│           ├── quotes/         # Quote comparison
│           ├── forecasting/    # Price forecasting
│           ├── risk/           # Cost risk analysis
│           └── search/         # Vector similarity search
│
├── ml/                         # ML inference service (Python 3.11, LightGBM/XGBoost)
│   ├── Dockerfile              → docker/ml/Dockerfile
│   ├── pyproject.toml
│   └── cost_prediction/
│       ├── inference/
│       │   └── api.py          # FastAPI app (GET /health, POST /predict)
│       ├── models/             # Serialized model artefacts (.pkl / .joblib)
│       ├── features/           # Feature engineering pipelines
│       └── training/           # Training scripts (run via MLflow)
│
├── agents/                     # RFQ autonomous agent (LangChain + Anthropic)
│   ├── rfq_agent/
│   │   ├── agent.py            # Main agent loop
│   │   ├── email_sender.py     # SMTP integration
│   │   ├── parser.py           # Supplier response parser
│   │   └── api.py              # FastAPI app (GET /health, POST /rfq)
│   └── pyproject.toml
│
├── database/
│   ├── migrations/             # Raw SQL migrations (applied by init container)
│   │   ├── 001_extensions_and_schemas.sql
│   │   ├── 002_core_schema.sql
│   │   ├── 003_vector_tables.sql
│   │   ├── 004_indexes.sql
│   │   └── 005_audit_log.sql
│   ├── seeds/
│   │   └── 007_seed_data.sql   # Demo tenant + 10 suppliers + cost records
│   └── policies/               # Row-level security policies (future)
│
├── docker/                     # Dockerfiles + service configs
│   ├── backend/
│   │   ├── Dockerfile          # 4-stage: base → builder → dev → prod
│   │   └── logging.json        # uvicorn JSON log config
│   ├── ml/
│   │   └── Dockerfile          # 4-stage ML image with OpenBLAS/LAPACK
│   ├── nginx/
│   │   ├── Dockerfile
│   │   ├── nginx.conf          # Worker config, gzip, security headers
│   │   └── conf.d/upstream.conf # Upstreams + rate limit zones
│   ├── postgres/
│   │   └── init/01_extensions.sql # pgvector, uuid-ossp, pg_trgm, etc.
│   ├── prometheus/
│   │   └── prometheus.yml      # Scrape config for all 8 services
│   ├── grafana/
│   │   ├── provisioning/
│   │   │   ├── datasources/    # Prometheus datasource
│   │   │   └── dashboards/     # Dashboard provider config
│   │   └── dashboards/
│   │       ├── ici-overview.json
│   │       ├── ici-ml.json
│   │       └── ici-rfq.json
│   └── pgadmin/
│       └── servers.json        # Pre-configured DB connection
│
├── k8s/                        # Kubernetes production manifests
│   ├── base/                   # Namespace, ResourceQuota, NetworkPolicy
│   ├── configmaps/             # App config (40+ keys)
│   ├── secrets/                # Secrets template (no real values)
│   ├── rbac/                   # ServiceAccounts (minimal permissions)
│   ├── deployments/            # backend, worker, ml-inference, rfq-agent
│   ├── services/               # ClusterIP services
│   ├── ingress/                # Public + admin ingress + TLS (cert-manager)
│   ├── hpa/                    # HPA v2 + KEDA ScaledObject
│   ├── storage/                # PersistentVolumeClaims (gp3 + EFS)
│   ├── observability/          # Prometheus, Grafana, Loki, alert rules
│   ├── security/               # External Secrets Operator (AWS SM + Vault)
│   └── kustomization.yaml      # Kustomize entry point
│
└── docs/
    ├── threat-model.md         # STRIDE analysis + mitigations matrix
    ├── architecture/           # Architecture decision records
    └── modules/                # Per-module domain specs
```

## Quick-Start Reference

| Command | Description |
|---------|-------------|
| `make up` | **Full stack**: build → start → migrate → seed → health-check |
| `make down` | Stop all containers (preserve data volumes) |
| `make reset` | Wipe volumes + full rebuild (destructive) |
| `make test` | Run 10-step e2e integration test |
| `make health` | Check every service is alive |
| `make logs` | Tail all service logs |
| `make db-shell` | Open psql prompt |
| `make migrate` | Apply Alembic migrations |
| `make seed` | Reload SQL seed data |
| `make seed-vectors` | Re-embed materials into Qdrant |
| `make lint` | ruff + mypy inside container |

## Service Map

| Service | Dev port | Description |
|---------|----------|-------------|
| nginx | :80 | Reverse proxy + rate limiting |
| backend | :8000 | Main FastAPI API |
| worker | — | ARQ background jobs |
| ml-inference | :8002 | Cost prediction ML service |
| rfq-agent | :8001 | Autonomous RFQ agent |
| postgres | :5432 | PostgreSQL 16 + pgvector |
| redis | :6379 | Cache + queue + pub/sub |
| qdrant | :6333 | Vector similarity search |
| mlflow | :5000 | ML experiment tracking |
| prometheus | :9090 | Metrics scraper |
| grafana | :3000 | Dashboards |
| mailhog | :8025 | SMTP catch-all (dev) |
| pgAdmin | :5050 | DB browser (dev) |
| redis-commander | :8081 | Queue inspector (dev) |
