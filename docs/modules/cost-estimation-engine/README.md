# Cost Estimation Engine

Silnik kalkulacji kosztów produkcji dla platformy Industrial Cost Intelligence.
Oblicza pełny koszt jednostkowy i całkowity dowolnego komponentu przemysłowego
z prognozami ML, analizą niepewności Monte Carlo i integracją podobieństwa (SCSE).

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-domain-model-cbs-formula-material-process.md](./01-domain-model-cbs-formula-material-process.md) | Model domenowy (EstimationInput, ProductGeometry, MaterialSpec, ProcessStep, CostEstimate), Cost Breakdown Structure (5 poziomów), formuła master kosztu, MaterialCostEngine (BTF + surcharges + LME), ProcessCostEngine (24 typy maszyn, MRR-based cycle time), CalculationOrchestrator (5-stage async pipeline), 9 lokalizacji produkcji |
| [02-overhead-setup-volume-scrap-time.md](./02-overhead-setup-volume-scrap-time.md) | OverheadModel (absorption + ABC, 9 lokalizacji, LOCATION_COST_INDEX 20 krajów), SetupCostEngine (SMED 4 poziomy, FAI, programming), ToolingCostEngine (amortyzacja), VolumeDiscountModel (Wright's Law + 7 progów cenowych), ScrapModel (Cpk→scrap via scipy, rework fraction), TimeEstimationEngine (10 modeli analitycznych), MonteCarloUncertaintyEngine (10K sampli, 4 rozkłady) |
| [03-sql-api-events-features-ml.md](./03-sql-api-events-features-ml.md) | Schemat PostgreSQL 16 (6 ENUMów, 9 tabel, 4 funkcje, triggery, widoki), OpenAPI 3.1 (11 endpointów, 7 ról RBAC), 8 tematów Kafka, 4 schematy Avro, CEEOutboxPublisher, Feature Engineering (100 cech: geometria/materiał/proces/wolumen/pochodne), XGBoost (unit cost), LightGBM (komponenty), SHAP, MLflow, Airflow DAG retrain, CV 5-fold |
| [04-confidence-similarity-monitoring-testing-scalability-risks-roadmap.md](./04-confidence-similarity-monitoring-testing-scalability-risks-roadmap.md) | Confidence Score (5 komponentów, OOD detection LOF), Similarity Injection Layer (SCSE Bayesian blend), 20 metryk Prometheus, 8 reguł Alertmanager, 6 dashboardów Grafana, macierz 8 testów (pytest/k6/Pact/Chaos), 4 poziomy skalowalności, HPA Kubernetes, 14 ryzyk, roadmap 32 sprinty (4 fazy) |

## Architektura kalkulacji

```
EstimationInput (geometry + material + processes + volume + location)
        │
        ▼
CalculationOrchestrator (async 5-stage pipeline)
        │
        ├─► Stage 1: Deterministic Formula
        │       ├─ MaterialCostEngine  (BTF, LME, surcharges)
        │       ├─ ProcessCostEngine   (MRR cycle time, machine rates)
        │       ├─ OverheadModel       (absorption / ABC)
        │       ├─ SetupCostEngine     (SMED, FAI, tooling amort)
        │       ├─ VolumeDiscountModel (Wright's Law)
        │       └─ ScrapModel          (Cpk → defect rate)
        │
        ├─► Stage 2: ML Ensemble
        │       ├─ XGBoost (unit cost, 100 features, SHAP)
        │       ├─ LightGBM (material / process / overhead components)
        │       └─ OOD Detector (LOF score)
        │
        ├─► Stage 3: Similarity Injection
        │       ├─ SCSE /search/quotes (top-20 similar, threshold 0.70)
        │       ├─ IQR outlier filtering
        │       └─ Bayesian Gaussian blend (prior=formula×0.50, ML×0.30, sim×0.20)
        │
        ├─► Stage 4: Monte Carlo Uncertainty
        │       ├─ 10 000 sampli
        │       ├─ material price: log-normal σ=10%
        │       ├─ cycle time: normal σ=15%
        │       ├─ scrap rate: beta
        │       └─ overhead: normal σ=5%
        │       → P10 / P50 / P90 / uncertainty_pct
        │
        └─► Stage 5: Confidence Scoring
                ├─ formula_coverage × 0.30
                ├─ ml_confidence    × 0.25
                ├─ data_freshness   × 0.20
                ├─ similarity_support × 0.15
                └─ process_coverage × 0.10
                → HIGH / MEDIUM / LOW / INDICATIVE
```

## Model kosztowy — składniki

| Składnik | Symbol | Opis |
|----------|--------|------|
| Koszt materiału | M | Masa brutto × cena/kg × korekty surcharges |
| Koszt procesu | P | Σ (czas cyklu × stawka maszyny) po krokach |
| Amortyzacja narzędzi | T | Koszt narzędzia / planowana liczba strzałów |
| Koszt ustawienia | S | (czas setup + FAI + programowanie) / wolumen |
| Narzut ogólny | OH | Robocizna bezpośrednia × overhead_rate × location_factor |
| Koszt braków | SC | P(defekt) × koszt naprawy/utylizacji |
| Koszt logistyki | L | Masa × logistics_factor(kraj) |
| Marża/zysk | MG | Opcjonalny składnik, konfigurowalny % |

**Formuła master:**
```
unit_cost = M + P + T + S + OH + SC + L + MG
```

## Lokalizacje produkcji

| Kraj | Region | Stawka robocizna EUR/h | Overhead factor | Location Cost Index |
|------|--------|----------------------|-----------------|---------------------|
| DE | EU-WEST | 45.0 | 1.00 | 1.00 |
| AT | EU-WEST | 42.0 | 0.98 | 0.95 |
| PL | EU-EAST | 18.0 | 0.72 | 0.45 |
| CZ | EU-EAST | 20.0 | 0.75 | 0.48 |
| RO | EU-EAST | 14.0 | 0.65 | 0.38 |
| CN | ASIA | 8.0 | 0.55 | 0.30 |
| IN | ASIA | 6.0 | 0.50 | 0.22 |
| MX | AMERICAS | 9.0 | 0.58 | 0.35 |
| US | AMERICAS | 38.0 | 0.90 | 0.92 |
| TR | MENA | 12.0 | 0.62 | 0.32 |

## ML Models

| Model | Algorytm | Target | MAPE target |
|-------|----------|--------|-------------|
| `cee-unit-cost-xgb` | XGBoost (squaredlogerror) | unit_cost_eur | < 10% |
| `cee-material-cost-lgbm` | LightGBM (regression_l1) | material_cost_eur | < 5% |
| `cee-process-cost-lgbm` | LightGBM (regression_l1) | process_cost_eur | < 8% |
| `cee-overhead-cost-xgb` | XGBoost (squarederror) | overhead_cost_eur | < 6% |

Feature vector: **100 cech** (22 geometria + 18 materiał + 32 proces + 16 wolumen/lokalizacja + 12 pochodne)

## Confidence Score — progi

| Poziom | Zakres | MAPE historyczny | Zastosowanie |
|--------|--------|-----------------|--------------|
| HIGH | ≥ 0.85 | < 5% | Oferty, zatwierdzenia budżetu |
| MEDIUM | 0.70–0.84 | 5–15% | Wstępna wycena, benchmarking |
| LOW | 0.55–0.69 | 15–30% | Orientacyjna kalkulacja |
| INDICATIVE | < 0.55 | > 30% | Tylko wskaźnik rządu wielkości |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| CEE_VIEWER | GET /estimate, GET /benchmarks |
| CEE_USER | CEE_VIEWER + POST /estimate, batch |
| CEE_ANALYST | CEE_USER + breakdown, sensitivity, analytics, compare |
| CEE_COST_ENGINEER | CEE_ANALYST + approve, POST /actuals |
| CEE_DATA_STEWARD | CEE_ANALYST + benchmarks management |
| CEE_OPS | Wszystko + ML models, cache flush |
| CEE_ADMIN | Pełny dostęp + DELETE + rollback |

## SLA

| Metryka | Cel | Ostrzeżenie | Krytyczny |
|---------|-----|-------------|-----------|
| P95 latency (sync) | < 3s | 3–5s | > 5s |
| P99 latency (sync) | < 10s | 10–20s | > 20s |
| MAPE (HIGH confidence) | < 5% | 5–10% | > 15% |
| Within CI (HIGH) | > 90% | 85–90% | < 85% |
| Material price freshness | < 4h | 4–24h | > 24h |
| Uptime | > 99.5% | 99–99.5% | < 99% |

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg
- **Baza danych:** PostgreSQL 16 (schemat `cee`, 9 tabel, partycjonowanie miesięczne)
- **Cache:** Redis 7+ (6 wzorców, TTL 30min–24h)
- **ML:** XGBoost 2.x + LightGBM 4.x + scikit-learn + SHAP
- **ML Registry:** MLflow (tracking + model registry + artifact store)
- **Batch Orchestration:** Apache Airflow (retrain DAG tygodniowy)
- **Messaging:** Apache Kafka 3+ (8 tematów, Avro + Schema Registry)
- **Uncertainty:** NumPy Monte Carlo (10K sampli)
- **Monitoring:** Prometheus (20 metryk) + Grafana (6 dashboardów) + Alertmanager (8 reguł)
- **Kubernetes:** HPA (2–20 podów), vertical pod autoscaler dla ML inference

## Integracje

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| Material Intelligence Engine (MIE) | ← | REST + Kafka | Ceny materiałów, właściwości |
| Manufacturing Process Engine (MPE) | ← | Kafka | Stawki maszynowe, aktualizacje |
| Cost History Engine (CHE) | ← | Kafka | Rzeczywiste koszty → dane treningowe ML |
| Similarity Cost Search Engine (SCSE) | ← | REST | Podobne wyceny → Similarity Injection Layer |
| Supplier Intelligence Engine (SIE) | ← | Kafka | Wskaźniki dostawców → logistics model |
| RFQ Engine | → | REST | Wycena do ofert |
| Procurement Portal | → | REST | Wycena interaktywna |
| ERP (SAP CO) | ↔ | REST/MQ | Rzeczywiste koszty ← ERP, wyceny → ERP |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | Schema PG, formuły deterministyczne, 5 typów procesów, API sync/async |
| ML & Intelligence | S9–S16 | Feature engineering, XGBoost/LightGBM, SCSE SIL, Confidence Scoring |
| Production Hardening | S17–S24 | Accuracy feedback, auto-retrain, HPA, k6 load test |
| Scale & Optimization | S25–S32 | GPU inference, multi-region, ABC costing, SAP integration |
