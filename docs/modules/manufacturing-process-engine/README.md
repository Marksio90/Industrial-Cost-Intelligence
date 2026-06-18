# Manufacturing Process Engine

Centralny moduł modelowania procesów produkcyjnych platformy Industrial Cost Intelligence.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-domain-model.md](./01-domain-model.md) | Model domenowy, agregaty, zdarzenia, context map |
| [02-taxonomy-hierarchy-parameters.md](./02-taxonomy-hierarchy-parameters.md) | Taksonomia procesów (6 klas, 80+ typów), hierarchia, parametry per proces |
| [03-resource-machine-tooling-operator-oee.md](./03-resource-machine-tooling-operator-oee.md) | Model zasobów, maszyn, narzędzi, operatorów, OEE |
| [04-cost-models.md](./04-cost-models.md) | Setup Cost, Runtime Cost, Energy Cost, Maintenance Cost, Scrap Cost, formuły |
| [05-capacity-scheduling-sql.md](./05-capacity-scheduling-sql.md) | Capacity Planning, Scheduling Inputs, kompletny SQL Schema PostgreSQL 16 |
| [06-api-events-ai-ml.md](./06-api-events-ai-ml.md) | REST API (OpenAPI), Kafka Events (Avro), AI Layer, ML Feature Store, modele |
| [07-monitoring-security-testing-scalability-risks-roadmap.md](./07-monitoring-security-testing-scalability-risks-roadmap.md) | Monitoring, Security (RBAC+RLS), Testing, Scalability, Risks, Roadmap |

## Obsługiwane procesy

| Klasa | Procesy |
|-------|---------|
| **CUT** — Cięcie | Laser CO₂/Fiber, Plasma, Waterjet, Gilotyna, Wykrawanie |
| **MAC** — Obróbka skrawaniem | Toczenie CNC, Frezowanie 3/5-oś, Wiercenie, Szlifowanie, EDM |
| **FOR** — Formowanie | Gięcie (air/bottom), Tłoczenie głębokie, Rollforming, Hydroformowanie |
| **JOI** — Łączenie | Spawanie MIG/MAG/TIG/Laser/FSW, Klejenie, Zgrzewanie, Lutowanie |
| **ASS** — Montaż | Ręczny, półauto, robotyczny, elektronika SMT/THT |
| **FIN** — Wykańczanie | Malowanie proszkowe, Cynkowanie, Anodowanie, Galwanizacja, Polerowanie, Piaskowanie |

## Kluczowe możliwości

- **Kalkulacja kosztu operacji** — 5-składnikowy model: setup + runtime + energia + utrzymanie + złom
- **OEE Tracking** — Dostępność × Wydajność × Jakość, Downtime Pareto, benchmarki per klasa
- **Capacity Planning** — capacity slots, bottleneck detection (TOC), scheduling inputs dla ERP/MES
- **Process Recommendation** — AI scoring procesu dla danej geometrii części i materiału
- **Cycle Time Prediction** — XGBoost model z feature store (geometry + material + machine)
- **Semantic Search** — pgvector HNSW, text-embedding-3-small, natural language queries

## Zależności techniczne

- **Baza danych:** PostgreSQL 16+ (`pg_trgm`, `pgvector`, `uuid-ossp`)
- **Analytics:** ClickHouse (OEE time series dla >100 maszyn)
- **Cache:** Redis 7+
- **Messaging:** Apache Kafka 3+ (17 topiców)
- **ML Serving:** ONNX Runtime + FastAPI, MLflow registry
- **AI Embeddings:** OpenAI text-embedding-3-small (1536 dim) + HNSW
- **Monitoring:** Prometheus + Grafana + Alertmanager

## Integracje

| System | Kierunek | Protokół |
|--------|----------|---------|
| Material Intelligence Engine | ← | REST API (material-process compatibility) |
| Cost Calculation Engine | → | REST API + Kafka events |
| BOM / Routing Engine | → | REST API (process definitions, times) |
| Scheduling Engine | ↔ | REST API (capacity slots, scheduling inputs) |
| ERP (SAP PP / Oracle MFG) | ↔ | REST API sync + Kafka |
| MES | → | Kafka (OEE, downtime, operation.completed) |
| RFQ Engine | → | REST API (cost estimates) |
