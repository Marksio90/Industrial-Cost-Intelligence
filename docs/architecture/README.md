# ICI Platform — Production Architecture Blueprint

Pełny blueprint produkcyjny platformy **Industrial Cost Intelligence** integrujący
11 mikroserwisów w event-driven monorepo z Data Lake, Vector DB, ML Pipeline,
GitOps CI/CD, Kubernetes HPA, pełnym stosem observability, zabezpieczeniami
enterprise-grade i multi-tenant design.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-monorepo-microservices.md](./01-monorepo-microservices.md) | Monorepo (uv+pnpm+Nx), 11 service catalogue, FastAPI app factory, DB schema isolation per service, gRPC inter-service, Outbox publisher base, service template |
| [02-event-driven-api-gateway.md](./02-event-driven-api-gateway.md) | Kafka topology (11 namespaces × N topics), RFQ lifecycle event flow, Avro schemas (3 core), consumer group base, Saga pattern (AwardSaga), Kong Gateway config, Istio VirtualService + DestinationRule, rate limiting tiers, OpenAPI aggregation |
| [03-data-lake-vector-ml.md](./03-data-lake-vector-ml.md) | Data Lake (S3 Raw→Curated→Features, Kafka Connect S3 Sink, Glue ETL PySpark, Delta Lake, Athena views), Vector DB (Qdrant 5 collections, multilingual-e5-large, CAD ResNet-50, similarity search), ML Platform (Feature Store S3+Redis, MLflow registry, Argo Workflows training pipeline, Active Learning XGBoost) |
| [04-cicd-docker-kubernetes.md](./04-cicd-docker-kubernetes.md) | CI/CD 17-stage pipeline (GitHub Actions + Argo CD GitOps), Nx affected builds, multi-arch Docker (amd64+arm64), Trivy+Semgrep+OWASP security gates, K8s Deployment (securityContext, probes, topology spread), HPA matrix (12 services), PDB, NetworkPolicy, Kustomize overlays (dev/staging/prod) |
| [05-observability-security-multitenant.md](./05-observability-security-multitenant.md) | Observability (OTEL→Jaeger/Tempo, Prometheus 9 shared metrics, Loki structlog, 9 Grafana dashboards, 8 Alertmanager rules), Security (7-layer: WAF→mTLS→JWT RS256→Istio STRICT→TLS at-rest→Secrets Manager→Sigstore), Multi-Tenant (schema-per-tenant, TenantContext propagation, Kafka isolation, feature flags, 3-plan matrix) |

## Architektura systemu

```
                        ┌─────────────────────────────────────┐
                        │         External Users              │
                        │   Web UI / API clients / Partners   │
                        └──────────────┬──────────────────────┘
                                       │ HTTPS / JWT RS256
                                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AWS Infrastructure                            │
│                                                                  │
│  CloudFront → WAF → ALB                                         │
│                       │                                          │
│               ┌───────▼────────┐                                │
│               │  API Gateway   │  Kong + Istio ingress          │
│               │  Rate limit    │  mTLS, JWT validation          │
│               │  CORS / cache  │  OpenTelemetry trace           │
│               └───────┬────────┘                                │
│                       │                                          │
│    ┌──────────────────┼──────────────────────────────┐          │
│    │                  │ Kubernetes (EKS)              │          │
│    │   ┌──────────────┼──────────────────────────┐   │          │
│    │   │          Namespace: ici                  │   │          │
│    │   │                                         │   │          │
│    │   │  ME  PE  SE  SOP  CBE  BOM  CAD         │   │          │
│    │   │  PFE  CRAE  RFQ  LE                     │   │          │
│    │   │  (all: HPA, mTLS, NetworkPolicy)        │   │          │
│    │   └─────────────────────────────────────────┘   │          │
│    │                  │                               │          │
│    │          ┌───────┴──────────┐                   │          │
│    │          │                  │                   │          │
│    │    PostgreSQL 16       Apache Kafka 3+          │          │
│    │    (11 schemas,        (MSK, 3 brokers,         │          │
│    │     per-tenant)        Avro Schema Reg.)        │          │
│    │          │                  │                   │          │
│    │    Redis 7+           Kafka Connect             │          │
│    │    (cache+online       → S3 Sink                │          │
│    │     feature store)          │                   │          │
│    │                             ▼                   │          │
│    │                    ┌─────────────────┐          │          │
│    │                    │   Data Lake     │          │          │
│    │                    │  S3 (Raw→       │          │          │
│    │                    │  Curated→       │          │          │
│    │                    │  Features)      │          │          │
│    │                    │  Glue + Athena  │          │          │
│    │                    └────────┬────────┘          │          │
│    │                             │                   │          │
│    │          ┌──────────────────┤                   │          │
│    │          │                  │                   │          │
│    │    Qdrant Cluster      MLflow Server            │          │
│    │    (5 collections,     + Argo Workflows         │          │
│    │     Vector Search)     (PFE+CRAE+LE            │          │
│    │                         training)               │          │
│    │                                                 │          │
│    │  ┌──────────────────────────────────────────┐  │          │
│    │  │         Observability                    │  │          │
│    │  │  Prometheus → Grafana → Alertmanager    │  │          │
│    │  │  OTEL Collector → Jaeger/Tempo          │  │          │
│    │  │  Loki (structured JSON logs)            │  │          │
│    │  └──────────────────────────────────────────┘  │          │
│    └──────────────────────────────────────────────── ┘          │
│                                                                  │
│  Keycloak (OIDC)    Secrets Manager    KMS (per-tenant CMK)     │
└──────────────────────────────────────────────────────────────────┘
```

## 11 Mikroserwisów — integracje

```
                    ┌──────────────────────┐
                    │    BOM Engine        │
                    │  bom.bom.created ───►│─── ME, CBE, CAD
                    └──────────────────────┘
                              │
                    ┌─────────▼────────────┐
                    │    CAD Engine        │
                    │  cad.features.       │
                    │  extracted ──────────│─── ME, BOM, CBE
                    └──────────────────────┘
                              │
   LME/EEX/CME ──►  ┌────────▼─────────────────────────┐
   Eurostat    ──►  │    Price Forecasting Engine (PFE) │
   PMI/BDI     ──►  │  pfe.forecast.ready ─────────────│──► CBE, CRAE
                    └────────────────────────────────────┘
                              │
   SOP         ──►  ┌─────────▼───────────────────────────────────────┐
   SE          ──►  │     Cost Breakdown Engine (CBE)                 │
   PE          ──►  │  cbe.breakdown.approved ────────────────────────│──► RFQ, CRAE, LE
   CAD         ──►  └────────────────────────────────────────────────┘
   PFE         ──►            │
                    ┌─────────▼────────────────────────────────────┐
   CBE         ──►  │   Cost Risk Analysis Engine (CRAE)           │
   PFE         ──►  │  crae.risk.critical ─────────────────────────│──► Alerts, RFQ
   MES/ERP     ──►  └──────────────────────────────────────────────┘
                              │
                    ┌─────────▼────────────────────────────┐
   CBE         ──►  │        RFQ Agent                     │
   CRAE        ──►  │  rfq.awarded ────────────────────────│──► SE, SOP, LE
   SOP         ──►  └──────────────────────────────────────┘
                              │
                    ┌─────────▼────────────────────────────┐
   CBE         ──►  │     Learning Engine (LE)             │
   RFQ         ──►  │  le.model.retrained ─────────────────│──► CBE, PFE, CRAE
   CRAE        ──►  └──────────────────────────────────────┘
```

## Stack techniczny — podsumowanie

| Warstwa | Technologia |
|---------|-------------|
| **Backend** | Python 3.12, FastAPI, asyncpg, asyncio, uvicorn + uvloop |
| **Inter-service sync** | gRPC (protobuf, mTLS) |
| **Messaging** | Apache Kafka 3+ (MSK), Avro + Schema Registry, Outbox Pattern |
| **Databases** | PostgreSQL 16 (per-service schema), Redis 7+, Qdrant |
| **Data Lake** | AWS S3, Glue ETL (PySpark), Delta Lake, Athena |
| **ML** | PyTorch (LSTM), statsmodels (SARIMA), Prophet, XGBoost, sentence-transformers |
| **ML Ops** | MLflow, Argo Workflows, Feature Store (S3+Redis) |
| **Frontend** | React 18, TypeScript, Vite, TanStack Query |
| **API Gateway** | Kong, Istio (mTLS, VirtualService, DestinationRule) |
| **Auth** | Keycloak (OIDC/OAuth2), JWT RS256, RBAC per-module |
| **Containers** | Docker (multi-arch amd64+arm64), Buildx, Cosign (signing) |
| **Orchestration** | Kubernetes (EKS), HPA, PDB, NetworkPolicy, Kustomize |
| **CI/CD** | GitHub Actions (Nx affected), Argo CD (GitOps) |
| **Observability** | Prometheus, Grafana, Alertmanager, OTEL, Jaeger, Loki |
| **Security** | WAF, Istio mTLS STRICT, Secrets Manager, KMS, Trivy, Semgrep, OWASP |
| **IaC** | Terraform (EKS, RDS, MSK, ElastiCache, S3), Checkov |
| **Monorepo** | Nx (affected builds, remote cache), uv (Python), pnpm (JS) |

## SLO — platforma

| Metryka | SLO |
|---------|-----|
| API Gateway p95 latency | ≤ 100ms |
| Cost Breakdown p95 latency | ≤ 2s |
| Price Forecast generation p95 | ≤ 120s |
| Risk Analysis p95 | ≤ 5s |
| Platform availability | ≥ 99.9% (Enterprise), ≥ 99.5% (Professional) |
| Error rate (all services) | < 1% |
| Kafka outbox lag | ≤ 60s |
| PFE MAPE 30d (STEEL_HRC) | ≤ 6% |
| CBE golden set MAPE | ≤ 5% |
| CRAE alert dispatch latency | ≤ 30s |

## Roadmap integracji

| Faza | Cel | Sprinty |
|------|-----|---------|
| Foundation | ME+PE+SE+CBE+BOM+SOP online, Kafka topology, API GW, basic auth | 1–12 |
| Intelligence | PFE SARIMA→Ensemble, CRAE MVP, CAD parsing, Vector DB | 13–24 |
| Scale | Full ML pipeline (LSTM, XGBoost, LE active learning), Data Lake, HPA | 25–36 |
| Enterprise | Multi-tenant provisioning, per-tenant KMS, Argo CD multi-cluster, TFT | 37–44 |
