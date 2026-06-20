# Continuous Learning System (CLS)

Autonomiczny system ciągłego uczenia modeli kosztowych dla platformy
Industrial Cost Intelligence. Monitoruje jakość predykcji CEE, wykrywa
drift danych i modeli, automatycznie retrenuje i promuje lepsze wersje —
z minimalną interwencją człowieka.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-learning-loop-data-collection-feedback-drift.md](./01-learning-loop-data-collection-feedback-drift.md) | Learning Loop Architecture (ContinuousLearningOrchestrator, 7 stanów, 9 triggerów), Data Collection Pipeline (ERPSAPIngester, RFQActualIngester, DataQualityValidator, Airflow DAG hourly), Feedback System (PredictionFeedbackStore, RollingMetricsCalculator, MAPE/RMSE/bias breakdowns), Drift Detection (PSICalculator, CUSUMDetector, StatisticalDriftDetector, KS-test) |
| [02-retraining-feature-store-registry-evaluation.md](./02-retraining-feature-store-registry-evaluation.md) | Retraining Strategy (RetrainingOrchestrator, warm/cold-start, promotion thresholds, rollback, Airflow DAG weekly), Feature Store (OfflineFeatureStore point-in-time correct, OnlineFeatureStore Redis, FeatureMaterializer hourly, feature versioning), Model Registry (CLSModelRegistry MLflow wrapper, stage management, A/B testing ABModelRouter), Evaluation Metrics (ModelEvaluator, MAPE/RMSE/bias/R²/CI, segmented metrics, paired t-test, Wilcoxon, calibration) |
| [03-sql-api-events-monitoring.md](./03-sql-api-events-monitoring.md) | SQL Schema PostgreSQL 16 (7 ENUMów, 10 tabel, 3 funkcje, triggery, 3 widoki, partycjonowanie), OpenAPI 3.1 (14 endpointów, 5 ról RBAC), Event System (10 tematów Kafka, 4 schematy Avro, 5 konsumentów zewnętrznych, CLSOutboxPublisher), Monitoring (25 metryk Prometheus, 7 dashboardów Grafana) |
| [04-alerting-testing-scalability-risks-roadmap.md](./04-alerting-testing-scalability-risks-roadmap.md) | Alerting (12 reguł Alertmanager, routing PagerDuty+Slack), Testing (8 typów: unit/integration/ML/drift/pipeline/contract/load k6/data quality), Scalability (4 poziomy, HPA Kubernetes, RetrainingWorkerPool, partycjonowanie, Redis Cluster), 15 Ryzyk, Roadmap 32 sprinty 4 fazy |

## Architektura

```
Actual Costs (ERP SAP / RFQA / CHE / MANUAL)
        │
        ▼
DataIngestionPipeline ──► DataQualityValidator ──► cls.actual_costs
        │
        ▼
PredictionFeedbackStore ──► cls.prediction_errors
        │
        ▼
StatisticalDriftDetector
  ├── PSICalculator        (feature distribution: PSI <0.10/0.20)
  ├── CUSUMDetector        (bias drift: k=0.5σ, h=5.0σ)
  └── KS-test              (target drift: p<0.01 WARNING, p<0.001 CRITICAL)
        │
        ▼ DriftSignal (CRITICAL / WARNING accumulation)
        │
ContinuousLearningOrchestrator
  └── RetrainingOrchestrator
        ├── OfflineFeatureStore ──► Point-in-time correct training data
        ├── Model Trainer         ──► warm-start (fine-tune) / cold-start
        ├── ModelEvaluator        ──► MAPE, bias, t-test vs champion
        ├── CLSModelRegistry      ──► MLflow staging → production
        └── Promotion / Rollback  ──► CEE API hot-reload via Kafka
```

## Cykl uczenia — stany

```
MONITORING → DRIFT_DETECTED → RETRAINING → EVALUATING
           → PROMOTING → [Production] → MONITORING
                      → ROLLBACK → [Previous version] → MONITORING
```

## Triggery retrainingu

| Trigger | Próg | Priorytet | Cooldown |
|---------|------|-----------|---------|
| MAPE > 15% (7-day) | CRITICAL | 1 | 6h |
| CUSUM alarm | CRITICAL | 1 | 6h |
| PSI > 0.20 (dowolna cecha) | CRITICAL | 1 | 6h |
| KS-test p < 0.001 | CRITICAL | 1 | 6h |
| ≥3 WARNING w 24h | WARNING | 2 | 12h |
| MAPE > 10% (7-day) | WARNING | 2 | 12h |
| PSI > 0.10 (dowolna cecha) | WARNING | 2 | 12h |
| Scheduled (niedziele 02:00 UTC) | — | 3 | — |
| Manual | — | 3 | — |

## Progi promocji (unit cost XGB)

| Kryterium | Wymagane |
|-----------|---------|
| MAPE improvement | ≥ 0.5pp vs champion |
| Bias challenger | |bias| < 2% |
| Statystyczna istotność | p < 0.05 (paired t-test) |
| Min. holdout samples | ≥ 100 |

## Modele monitorowane

| Model | Typ | MAPE target | Bias max |
|-------|-----|-------------|---------|
| `cee-unit-cost-xgb` | XGBoost | < 10% | ±3% |
| `cee-material-cost-lgbm` | LightGBM | < 8% | ±2.5% |
| `cee-process-cost-lgbm` | LightGBM | < 12% | ±4% |
| `cee-overhead-cost-xgb` | XGBoost | < 15% | ±5% |
| `cee-confidence` | LightGBM | < 5% | ±1.5% |

## Drift Detection — algorytmy

| Algorytm | Zastosowanie | Próg WARNING | Próg CRITICAL |
|----------|-------------|-------------|--------------|
| **PSI** | Zmiana rozkładu cech | > 0.10 | > 0.20 |
| **CUSUM** | Systematyczny bias predykcji | alarm | alarm (natychmiast) |
| **KS-test** | Zmiana rozkładu target (actual cost) | p < 0.01 | p < 0.001 |
| **MAPE monitor** | Degradacja jakości | > 10% | > 15% |

## Stack techniczny

- **ML Framework:** XGBoost 2.x, LightGBM 4.x, scikit-learn
- **Experiment Tracking:** MLflow 2.x (tracking, registry, artifacts)
- **Orchestration:** Apache Airflow 2.x (ingestion DAG hourly, retraining DAG weekly)
- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `cls`, 10 tabel, partycjonowanie)
- **Feature Store offline:** PostgreSQL (point-in-time correct views)
- **Feature Store online:** Redis 7+ (Hash per entity, TTL per group)
- **Messaging:** Apache Kafka 3+ (10 tematów, Avro + Schema Registry)
- **Monitoring:** Prometheus (25 metryk) + Grafana (7 dashboardów) + Alertmanager (12 reguł)
- **Security:** JWT RS256, RBAC 5 ról
- **Kubernetes:** HPA API 2–20 pods, retraining workers 1–5

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CLS_VIEWER` | GET metrics, drift signals, jobs, models |
| `CLS_CONTRIBUTOR` | CLS_VIEWER + submit actuals (single + batch) |
| `CLS_ANALYST` | CLS_CONTRIBUTOR + prediction errors + A/B results |
| `CLS_OPS` | CLS_ANALYST + trigger retraining + rollback + run drift |
| `CLS_ADMIN` | CLS_OPS + manual promote + DELETE + schema management |

## SLA i KPIs (cel po S32)

| Metryka | Cel |
|---------|-----|
| Unit cost MAPE | < 10% |
| Prediction bias | |bias| < 3% |
| Within ±10% rate | > 70% |
| Time-to-detect drift | < 24h |
| Time-to-retrain (CRITICAL) | < 2h |
| Auto-promotion rate | > 60% |
| Rollback rate | < 10% |
| Actual cost coverage | > 85% |
| API P95 latency | < 500ms |
| Ingestion throughput | > 1000 actuals/min |

## Integracje

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| CEE API | ← / → | REST + Kafka | Predykcje (input) / nowe wersje modeli (output) |
| SAP ERP (CO module) | ← | REST (SAP OData) | Settled production orders + cost elements |
| RFQ Agent (RFQA) | ← | Kafka | Winner offers → actual market prices |
| Cost History Engine (CHE) | ← | Kafka | Potwierdzone koszty historyczne |
| MLflow Server | ↔ | HTTP (MLflow REST) | Experiment tracking, model registry, artifacts |
| Airflow | → | Trigger/sensor | DAG execution, schedule, status |
| Grafana / Prometheus | ← | Pull (HTTP) | Metrics scraping |
| Slack / PagerDuty | → | Webhook / API | Drift alerts, promotion notifications |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | DB, SAP/RFQA ingestion, feedback store, rolling metrics, outbox |
| Drift Detection | S9–S16 | PSI, CUSUM, KS-test, detektory, alerty, dashboardy |
| Retraining Pipeline | S17–S24 | Feature Store, Model Registry, RetrainingOrchestrator, Airflow DAG |
| Intelligence | S25–S32 | A/B testing, segment-aware promotion, HITL, partycjonowanie, DR |
