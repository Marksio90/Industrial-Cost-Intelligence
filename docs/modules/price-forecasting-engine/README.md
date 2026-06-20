# Price Forecasting Engine (PFE)

System prognozowania cen materiałów i usług produkcyjnych dla platformy
Industrial Cost Intelligence. Integruje dane rynkowe (LME, EEX, CME, Platts),
makroekonomiczne (Eurostat, PMI, BDI) i wewnętrzne (SOP, CBE), buduje szeregi
czasowe i generuje prognozy przy użyciu SARIMA, Prophet i LSTM z attention,
łącząc je w ensemble ważony odwrotnością RMSE.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-data-sources-models-features.md](./01-data-sources-models-features.md) | Data Sources (4 klasy: market/internal/macro/alternative, LMEConnector, EEXConnector, EurostatConnector, SOPInternalConnector, DataIngestionPipeline, PriceDataValidator), Time Series Models (ARIMAModel auto-order SARIMA, ProphetModel z regressorami, LSTMModel z attention + GaussianNLL, ModelEnsemble INVERSE_RMSE), Feature Engineering (FeatureEngineeringPipeline: 20+ cech, lag/rolling/RSI/momentum/cykliczne, FeatureSelector permutation importance) |
| [02-external-signals-sql-api.md](./02-external-signals-sql-api.md) | External Signals (ExternalSignalService, MacroSnapshot, FXNormalizer ECB, InflationAdjuster real prices base 2020, tabela korelacji sygnałów wiodących), SQL Schema PostgreSQL 16 (schemat `pfe`, 5 ENUMów, 8 tabel, BRIN index, triggery, widoki v_latest_forecasts + v_price_history), OpenAPI 3.1 (4 role RBAC, 20 endpointów, scenarios, correlation-matrix, sensitivity) |
| [03-events-monitoring-drift.md](./03-events-monitoring-drift.md) | Event System (9 tematów Kafka, 3 schematy Avro, PFEOutboxPublisher, ForecastingEngine worker), Monitoring (26 metryk Prometheus, 7 dashboardów Grafana, 9 reguł Alertmanager, SLI/SLO), Drift Detection (DriftDetector: PSI + KS test + MAPE rolling, RetrainScheduler APScheduler, ForecastEvaluator ex-post, thresholds tabela) |
| [04-testing-risks-roadmap.md](./04-testing-risks-roadmap.md) | Testing (12 typów: unit SARIMA/Prophet/LSTM/Ensemble/FeatureEng/Drift/Validator, integration DB+Outbox, accuracy golden set 6 commodities, k6 load, API contract), 15 Ryzyk (R01–R15), Roadmap 32 sprinty 4 fazy (Foundation S1-S8, ML+Signals S9-S18, Scale S19-S28, Advanced S29-S32) |

## Architektura

```
Zewnętrzne źródła danych
LME / EEX / CME / Platts / ECB / Eurostat / PMI / BDI
                │
                ▼
┌───────────────────────────────────────┐
│       DataIngestionPipeline           │
│  connectors → FXNormalizer → UPSERT  │
│  PriceDataValidator (anomaly check)  │
└────────────────┬──────────────────────┘
                 │ pfe.price_series (BRIN index)
                 │
    ┌────────────┴──────────────┐
    │                          │
    ▼                          ▼
pfe.price_series          pfe.macro_snapshots
(OHLC daily)              (PMI, FX, HICP, BDI)
    │                          │
    └────────────┬─────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────┐
│          FeatureEngineeringPipeline                    │
│  lag(1–90) + rolling(5–60) + RSI + momentum           │
│  + cyclical(month/week) + macro regressors             │
└──────────────────────┬─────────────────────────────────┘
                       │
          ┌────────────┼──────────────────┐
          │            │                  │
          ▼            ▼                  ▼
   ARIMAModel    ProphetModel        LSTMModel
   (SARIMA)      (+ regressors)      (attention +
   auto-order    yearly/weekly        GaussianNLL)
   AIC search    seasonality         LOOKBACK=90d
          │            │                  │
          └────────────┼──────────────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │   ModelEnsemble     │
            │  INVERSE_RMSE       │
            │  weighted average   │
            └──────────┬──────────┘
                       │
          ┌────────────┼───────────────────┐
          │            │                   │
          ▼            ▼                   ▼
    pfe.forecasts  DriftDetector     Kafka Outbox
    + points       PSI + KS + MAPE   pfe.forecast.ready
    + evaluations  RetrainScheduler  → CBE / SOP / UI
```

## Obsługiwane commodities

| Klasa | Commodity | Źródło | Model |
|-------|-----------|--------|-------|
| Stal | STEEL_HRC, STEEL_CRC | CME / Platts | ENSEMBLE |
| Stal | STEEL_SCRAP | CME | ENSEMBLE |
| Aluminium | ALUMINUM_LME | LME | Prophet + LSTM |
| Miedź | COPPER_LME | LME | ENSEMBLE |
| Energia | ELECTRICITY_DE | EEX PHELIX | SARIMA |
| Energia | GAS_TTF | ICE/EEX | SARIMA + Prophet |
| Makro | PPI_METALS_DE | Eurostat | ARIMA |
| Makro | HICP_EU | Eurostat | ARIMA |
| Usługi | MACHINING_RATE_DE/PL | CBE + SOP internal | Prophet |

## Modele prognostyczne

| Model | Architektura | Zalety | Ograniczenia |
|-------|-------------|--------|-------------|
| **SARIMA** | Statsmodels SARIMAX; auto AIC grid | Dobra sezonowość; interpretowalny | Liniowy; słaby na zmiany reżimu |
| **Prophet** | Meta Prophet; changepoints; regressory | Automatyczna sezonowość; regressory makro | Wolniejszy retrain; wymaga czystych danych |
| **LSTM** | 2-layer LSTM + MultiheadAttention; GaussianNLL | Nieliniowość; probabilistyczny output | Dużo danych; ryzyko overfitting; GPU recommended |
| **ENSEMBLE** | Ważona średnia 3 modeli (wagi = 1/RMSE) | Najlepsza accuracy; robustny na błędy jednego | Złożoność; czas generowania ~3× |

## Sygnały zewnętrzne — korelacje z ceną stali

| Sygnał | Opóźnienie | Korelacja |
|--------|:----------:|:---------:|
| HICP EU (inflacja) | 1–3 mies. | +0.71 |
| Capacity util. steel | 0–1 mies. | +0.68 |
| PMI Manufacturing DE | 1–3 mies. | +0.62 |
| EUR/USD | 0–4 tyg. | −0.45 |
| Baltic Dry Index | 2–6 tyg. | +0.48 |
| Cena energii DE | 0–2 tyg. | +0.38 |

## Feature Engineering — top cechy dla LSTM

| Cecha | Znaczenie |
|-------|-----------|
| `lag_1`, `lag_5`, `lag_20` | Momentum krótkoterminowy |
| `roll_mean_60`, `roll_std_60` | Trend i zmienność długoterminowa |
| `rsi_14` | Wykupienie / wyprzedanie |
| `vol_20d` | Historyczna zmienność (annualized) |
| `month_sin/cos` | Sezonowość roczna |
| `pmi_de` | Sygnał wiodący popytu |
| `eurusd` | Efekt walutowy |
| `nat_gas_eur` | Koszty energii hutnictwa |

## Drift Detection — progi

| Miara | OK | WARNING | DRIFT | RETRAIN |
|-------|----|:-------:|:-----:|:-------:|
| PSI | < 0.10 | 0.10–0.20 | — | > 0.20 |
| KS p-value | > 0.10 | 0.05–0.10 | < 0.05 | — |
| MAPE_7d / baseline | < 1.2× | 1.2–1.5× | > 1.5× | > 2.0× |
| CI 95% coverage | ≥ 88% | 80–88% | < 80% | — |
| Model age | ≤ 30d | 30–60d | — | > 60d |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `PFE_VIEWER` | GET forecasts, price-series, commodities, scenarios |
| `PFE_ANALYST` | PFE_VIEWER + analytics, backtest, drift reports, export |
| `PFE_ENGINEER` | PFE_ANALYST + POST generate-forecast, POST retrain |
| `PFE_ADMIN` | Wszystko + manage connectors, model-registry, DELETE |

## SQL Schema (schemat `pfe`)

| Tabela | Opis |
|--------|------|
| `price_series` | Historyczne ceny EUR (BRIN index, UPSERT idempotent) |
| `macro_snapshots` | Dzienne snapshoty PMI, FX, HICP, BDI, energy |
| `model_registry` | Wersje modeli z hyperparams, artifact_path, backtest metrics |
| `forecasts` | Nagłówki prognoz ze statusem i metadanymi |
| `forecast_points` | Punkty prognozy (predicted, lower/upper 80/95) |
| `forecast_evaluations` | Ex-post accuracy (MAE, RMSE, MAPE, coverage) |
| `drift_reports` | PSI, KS, MAPE rolling, drift status, retrain flag |
| `outbox_events` | Transactional Outbox dla Kafka |

## Event System (9 tematów Kafka)

| Temat | Trigger | Konsumenci |
|-------|---------|------------|
| `pfe.price.ingested` | Daily ingest | Audit, Feature Store |
| `pfe.price.anomaly` | Validator reject | Alert, DLQ |
| `pfe.forecast.requested` | POST /generate | Forecast Worker |
| `pfe.forecast.ready` | DB Trigger (DONE) | **CBE**, SOP, UI |
| `pfe.forecast.failed` | Status FAILED | Alert, DLQ |
| `pfe.model.retrained` | Retrain done | Audit, Registry |
| `pfe.drift.detected` | DriftDetector alert | Alert, Retrain |
| `pfe.scenario.computed` | POST /scenarios | CBE, UI |
| `pfe.macro.updated` | Macro snapshot | Feature Store |

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| ENSEMBLE MAPE 30-day (stal) | ≤ 6% |
| CI 95% empirical coverage | ≥ 88% |
| `pfe_forecast_duration_seconds` p95 | ≤ 120s |
| `pfe_api_duration_seconds` p95 GET | ≤ 500ms |
| `pfe_data_freshness_hours` (DAILY) | ≤ 26h |
| `pfe_drift_psi_score` alert | > 0.20 → retrain |
| Failure rate (forecast) | < 2% |
| Availability | ≥ 99.5% |

## Skalowalność

| Poziom | Wolumen | Infrastruktura |
|--------|---------|----------------|
| L1 | ≤ 10 forecasts/h | 1 API pod, 1 worker (CPU LSTM) |
| L2 | ≤ 50 forecasts/h | 2–4 API pods, 2–3 workers (GPU LSTM) |
| L3 | ≤ 200 forecasts/h | HPA 3–10 API + 2–5 GPU workers, Redis cache |
| L4 | > 200 forecasts/h | Multi-region, Kafka streaming, TimescaleDB |

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Time Series:** statsmodels (SARIMAX), Prophet 1.x (Meta)
- **Deep Learning:** PyTorch 2.x (LSTM + MultiheadAttention, GaussianNLL)
- **Feature Engineering:** pandas, scikit-learn, NumPy
- **Drift Detection:** scipy (KS test), custom PSI
- **Scheduler:** APScheduler 3.x (cron jobs)
- **Database:** PostgreSQL 16 (schemat `pfe`, BRIN index, TimescaleDB opcjonalne)
- **Cache:** Redis 7+ (macro snapshots TTL=3600s, GET /forecast cache)
- **Messaging:** Apache Kafka 3+ (9 tematów, Avro + Schema Registry, Transactional Outbox)
- **Monitoring:** Prometheus (26 metryk) + Grafana (7 dashboardów) + Alertmanager (9 reguł)
- **Security:** JWT RS256, RBAC 4 role
- **Kubernetes:** HPA pfe-api 2–10 pods, pfe-worker 1–5 pods (GPU nodepool)

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| LME | ← | REST API | Al/Cu/Ni/Zn daily settlement |
| EEX | ← | REST API | Electricity DE, TTF gas |
| ECB | ← | REST/XML | EUR/USD/CNY/PLN daily FX |
| Eurostat | ← | SDMX-JSON | PPI metals/energy, HICP |
| IHS Markit PMI | ← | REST API | Manufacturing PMI DE/CN/US |
| BDI | ← | REST API | Baltic Dry Index |
| SOP | ← | Kafka / DB | Ceny z ofert dostawców (INTERNAL) |
| CBE | → | Kafka | `pfe.forecast.ready` → auto-update material_rates |
| Grafana / Prometheus | ← | Pull | Metrics scraping |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | DB, LME/EEX ingest, SARIMA, API, monitoring, CI MAPE gate |
| ML + Signals | S9–S18 | Prophet z regressorami, LSTM, Ensemble, Drift Detection, Scenarios |
| Intelligence + Scale | S19–S28 | 9 commodities, volatility forecast, HPA, TimescaleDB, CBE integration |
| Advanced | S29–S32 | TFT model, conformal prediction, copula, real-time tick streaming |
