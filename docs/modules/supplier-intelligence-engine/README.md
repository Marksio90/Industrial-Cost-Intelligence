# Supplier Intelligence Engine

Centralny system oceny i zarządzania dostawcami platformy Industrial Cost Intelligence.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-domain-model.md](./01-domain-model.md) | Model domenowy, agregaty, zdarzenia domenowe, context map, typy dostawców |
| [02-master-data-metrics.md](./02-master-data-metrics.md) | Master data (Supplier, Address, Contact, Certification, Capability), metryki jakości (PPM, NCR, 8D), lead time (OTD, OTIF), MOQ, historical pricing, delivery KPIs |
| [03-scoring-ranking-risk.md](./03-scoring-ranking-risk.md) | Scorecard 5-komponentowy (Quality 35% + Delivery 25% + Price 20% + Service 10% + Risk 10%), ranking per kategoria, analiza ryzyka (financial, geopolitical, concentration), Altman Z-Score, D&B integration |
| [04-sql-schema.sql](./04-sql-schema.sql) | Kompletny schemat PostgreSQL 16 (20 tabel, 14 enumów, funkcje, triggery, widoki, RLS, pgvector HNSW) |
| [05-api-events.md](./05-api-events.md) | REST API OpenAPI 3.1 (25 endpointów), 15 Kafka topics, Avro schemas, event consumer matrix |
| [06-ai-similarity-recommendation.md](./06-ai-similarity-recommendation.md) | AI Layer (embeddingi text-embedding-3-small 1536d), semantic search (pgvector HNSW), hybrid search (FTS + vector + RRF), SupplierRecommendationEngine, RAG context builder, ML feature store (34 features), anomaly detection |
| [07-monitoring-security-testing-scalability-risks-roadmap.md](./07-monitoring-security-testing-scalability-risks-roadmap.md) | Prometheus metrics (18), Alertmanager (8 reguł), Grafana (8 dashboardów), RBAC (7 ról), RLS, security controls, testy (unit/integration/contract/load), k6, Redis caching, scalability tiers, rejestr ryzyk (14), roadmap (36 sprintów) |

## Obsługiwane typy dostawców

| Typ | Kod |
|-----|-----|
| Producent | MANUFACTURER |
| Hurtownia | WHOLESALER |
| Dystrybutor | DISTRIBUTOR |
| Podwykonawca | SUBCONTRACTOR |
| Trader / Broker | TRADER |
| Agent | AGENT |
| Leasingodawca | LESSOR |

## Scorecard — wagi

| Komponent | Waga | Kluczowe KPI |
|-----------|------|-------------|
| Quality | 35% | PPM, NCR count, 8D closure, certifications |
| Delivery | 25% | OTD %, OTIF %, avg delay days |
| Price | 20% | vs benchmark, YoY trend, stability CV |
| Service | 10% | Response time, 8D closure days, portal compliance |
| Risk | 10% | Financial (Altman Z), geopolitical (Coface), concentration (HHI) |

## Rating Classes

| Klasa | Zakres | Znaczenie operacyjne |
|-------|--------|---------------------|
| A | 85–100 | Preferred — fast-track RFQ, auto-approval |
| B | 70–84 | Approved — standard process |
| C | 55–69 | Conditional — corrective action plan |
| D | 40–54 | Probation — enhanced monitoring |
| E | 25–39 | Suspension candidate |
| F | 0–24 | Suspended / Blacklisted |

## Kluczowe możliwości

- **Composite Scorecard** — 5-komponentowy model z wagami, historia trendów, klasy A–F
- **Risk Engine** — financial (Altman Z + D&B), geopolitical (Coface + sanctions), concentration (HHI)
- **Semantic Supplier Search** — pgvector HNSW, text-embedding-3-small, hybrid RRF re-ranking
- **Recommendation Engine** — multi-criteria scoring z diversity bonus i hard cert filters
- **RAG Context Builder** — kontekst dostawcy dla AI Cost Calculation / RFQ / Risk Decision
- **Anomaly Detection** — Isolation Forest na 34 featurach KPI

## Zależności techniczne

- **Baza danych:** PostgreSQL 16+ (`pgvector`, `pg_trgm`, `uuid-ossp`)
- **Cache:** Redis 7+
- **Messaging:** Apache Kafka 3+ (15 topiców)
- **AI Embeddings:** OpenAI text-embedding-3-small (1536 dim) + HNSW
- **External data:** D&B API, Coface API, OFAC/EU/UK sanctions feeds
- **Monitoring:** Prometheus + Grafana + Alertmanager

## Integracje

| System | Kierunek | Protokół |
|--------|----------|---------|
| ERP (SAP MM / Oracle SCM) | ↔ | REST + Kafka (PO, GR, vendor sync) |
| MES / Quality System | ← | Kafka (NCR, inspection results) |
| D&B / Creditsafe | ← | REST API (financial signals) |
| Coface / Control Risks | ← | REST API (geopolitical risk) |
| OFAC / EU Sanctions | ← | REST API daily refresh |
| Cost Calculation Engine | → | REST + Kafka (supplier price inputs) |
| Material Intelligence Engine | → | REST (supplier-material mappings) |
| RFQ Engine | → | REST + Kafka (preferred lists, scores) |
| Manufacturing Process Engine | → | REST (lead times, MOQ) |
| Procurement Workflows | → | Kafka events (approval thresholds) |
