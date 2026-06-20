# Price Forecasting Engine — Sections 7–9

## 7. Event System

### 7.1 Topologie Kafka (9 tematów)

| Temat | Trigger | Producent | Konsumenci |
|-------|---------|-----------|------------|
| `pfe.price.ingested` | DataIngestionPipeline upsert | Ingest Worker | Audit, Feature Store |
| `pfe.price.anomaly` | PriceDataValidator reject | Ingest Worker | Alert, DLQ |
| `pfe.forecast.requested` | POST /forecasts/generate | API | Forecast Worker |
| `pfe.forecast.ready` | DB Trigger (status=DONE) | DB Trigger | CBE, SOP, UI |
| `pfe.forecast.failed` | status=FAILED | Forecast Worker | Alert, DLQ |
| `pfe.model.retrained` | Retrain zakończony | Forecast Worker | Audit, Registry |
| `pfe.drift.detected` | DriftDetector alert | Drift Worker | Alert, Retrain trigger |
| `pfe.scenario.computed` | POST /scenarios | Forecast Worker | CBE, UI |
| `pfe.macro.updated` | ExternalSignalService upsert | Macro Worker | Feature Store, Alert |

### 7.2 Avro schemas

```json
// pfe.forecast.ready — v1
{
  "namespace": "com.industrial_cost_intelligence.pfe",
  "type": "record",
  "name": "ForecastReady",
  "fields": [
    {"name": "forecast_id",    "type": "string"},
    {"name": "commodity",      "type": "string"},
    {"name": "model_type",     "type": {"type": "enum", "name": "ForecastModel",
                                        "symbols": ["SARIMA","PROPHET","LSTM","ENSEMBLE"]}},
    {"name": "generated_at",   "type": "string"},   // ISO date
    {"name": "horizon_days",   "type": "int"},
    {"name": "next_30d_predicted", "type": "double"},
    {"name": "next_30d_lower_95",  "type": "double"},
    {"name": "next_30d_upper_95",  "type": "double"},
    {"name": "backtest_mape",  "type": ["null","double"], "default": null},
    {"name": "coverage_95",    "type": ["null","double"], "default": null}
  ]
}
```

```json
// pfe.drift.detected — v1
{
  "namespace": "com.industrial_cost_intelligence.pfe",
  "type": "record",
  "name": "DriftDetected",
  "fields": [
    {"name": "drift_id",       "type": "string"},
    {"name": "commodity",      "type": "string"},
    {"name": "model_type",     "type": "string"},
    {"name": "check_date",     "type": "string"},
    {"name": "drift_status",   "type": {"type": "enum", "name": "DriftStatus",
                                        "symbols": ["WARNING","DRIFT_DETECTED","RETRAIN_REQUIRED"]}},
    {"name": "psi_score",      "type": ["null","double"], "default": null},
    {"name": "mape_7d",        "type": ["null","double"], "default": null},
    {"name": "mape_baseline",  "type": ["null","double"], "default": null},
    {"name": "retrain_triggered", "type": "boolean"}
  ]
}
```

```json
// pfe.price.anomaly — v1
{
  "namespace": "com.industrial_cost_intelligence.pfe",
  "type": "record",
  "name": "PriceAnomaly",
  "fields": [
    {"name": "source_id",      "type": "string"},
    {"name": "commodity",      "type": "string"},
    {"name": "price_date",     "type": "string"},
    {"name": "price_eur",      "type": "double"},
    {"name": "reason",         "type": "string"},
    {"name": "quality_score",  "type": "float"},
    {"name": "detected_at",    "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

### 7.3 PFEOutboxPublisher

```python
import asyncio

class PFEOutboxPublisher:
    """Transactional Outbox — publikuje eventy z pfe.outbox_events."""

    POLL_INTERVAL_S = 0.5
    BATCH_SIZE      = 100

    def __init__(
        self,
        db:       "AsyncpgPool",
        kafka:    "AIOKafkaProducer",
        registry: "ConfluentSchemaRegistry",
    ):
        self._db       = db
        self._kafka    = kafka
        self._registry = registry

    async def run(self) -> None:
        while True:
            n = await self._publish_batch()
            if n < self.BATCH_SIZE:
                await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _publish_batch(self) -> int:
        async with self._db.transaction() as conn:
            rows = await conn.fetch("""
                SELECT event_id, topic, key, payload
                  FROM pfe.outbox_events
                 WHERE published = FALSE
                 ORDER BY created_at
                 LIMIT $1
                   FOR UPDATE SKIP LOCKED
            """, self.BATCH_SIZE)
            for row in rows:
                schema  = await self._registry.get_schema(row["topic"])
                encoded = schema.encode(row["payload"])
                await self._kafka.send(
                    row["topic"],
                    key=row["key"].encode(),
                    value=encoded,
                )
                await conn.execute(
                    "UPDATE pfe.outbox_events SET published = TRUE WHERE event_id = $1",
                    row["event_id"],
                )
            return len(rows)
```

### 7.4 ForecastingEngine — worker

```python
import asyncio
import time
from uuid import UUID
from datetime import date

class ForecastingEngine:
    """Worker: pobiera dane → trenuje → generuje prognozę → zapisuje."""

    def __init__(
        self,
        repo:          "PriceRepository",
        signal_svc:    "ExternalSignalService",
        feature_pipe:  "FeatureEngineeringPipeline",
        model_factory: "ModelFactory",
        db:            "AsyncpgPool",
    ):
        self._repo    = repo
        self._signals = signal_svc
        self._feats   = feature_pipe
        self._factory = model_factory
        self._db      = db

    async def run_forecast(
        self,
        forecast_id: UUID,
        req: "GenerateForecastRequest",
    ) -> None:
        t0 = time.monotonic()
        try:
            await self._db.update_forecast_status(forecast_id, "RUNNING")

            # 1. Dane historyczne (min 2 lata)
            end   = date.today()
            start = date(end.year - 3, end.month, end.day)
            series_df = await self._repo.get_price_series(
                req.commodity, start, end
            )
            prices = series_df["price_eur"].values
            dates  = list(series_df.index.date)

            # 2. Cechy zewnętrzne
            external_df = await self._signals.get_dataframe(start, end)
            fm = self._feats.build(series_df["price_eur"], external_df)

            # 3. Model
            model = self._model_factory.create(
                req.model_type, req.commodity, req.horizon_days
            )
            model.fit(prices, dates, feature_matrix=fm.features)

            # 4. Prognoza
            result = model.predict(req.horizon_days)

            # 5. Backtest
            bt = model.backtest(prices, dates, n_splits=5)
            result.mae  = bt["mae"]
            result.rmse = bt["rmse"]
            result.mape = bt["mape"]

            # 6. Zapis
            duration = time.monotonic() - t0
            await self._db.save_forecast_result(
                forecast_id=forecast_id,
                result=result,
                duration_s=duration,
            )
            await self._db.update_forecast_status(forecast_id, "DONE")

        except Exception as exc:
            await self._db.update_forecast_status(
                forecast_id, "FAILED",
                error=str(exc),
            )
            raise
```

---

## 8. Monitoring

### 8.1 Metryki Prometheus (26 metryk)

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# ─── Ingestia danych ─────────────────────────────────────────

pfe_ingest_records_total = Counter(
    "pfe_ingest_records_total",
    "Łączna liczba pobranych rekordów cenowych",
    ["source_id", "commodity"]
)

pfe_ingest_anomalies_total = Counter(
    "pfe_ingest_anomalies_total",
    "Odrzucone rekordy z powodu anomalii cenowej",
    ["source_id", "commodity", "reason"]
)

pfe_ingest_duration_seconds = Histogram(
    "pfe_ingest_duration_seconds",
    "Czas pobierania danych per konektor",
    ["source_id"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60]
)

pfe_data_freshness_hours = Gauge(
    "pfe_data_freshness_hours",
    "Ile godzin od ostatniego rekordu",
    ["source_id", "commodity"]
)

pfe_price_quality_score = Gauge(
    "pfe_price_quality_score",
    "Mediana quality_score w ostatnich 24h",
    ["source_id", "commodity"]
)

# ─── Prognozowanie ────────────────────────────────────────────

pfe_forecast_total = Counter(
    "pfe_forecast_total",
    "Łączna liczba wygenerowanych prognoz",
    ["model_type", "commodity", "status"]
)

pfe_forecast_duration_seconds = Histogram(
    "pfe_forecast_duration_seconds",
    "Czas generowania prognozy",
    ["model_type", "commodity"],
    buckets=[5, 10, 30, 60, 120, 300, 600]
)

pfe_forecast_mape = Gauge(
    "pfe_forecast_mape",
    "Backtest MAPE aktywnego modelu",
    ["model_type", "commodity", "horizon_days"]
)

pfe_forecast_coverage_95 = Gauge(
    "pfe_forecast_coverage_95",
    "Empiryczne pokrycie 95% CI",
    ["model_type", "commodity"]
)

pfe_forecast_bias = Gauge(
    "pfe_forecast_bias",
    "Średni błąd (bias) prognozy",
    ["model_type", "commodity", "horizon_days"]
)

pfe_ensemble_weight = Gauge(
    "pfe_ensemble_weight",
    "Wagi modeli w ensemble",
    ["model_type", "commodity"]
)

# ─── Drift ───────────────────────────────────────────────────

pfe_drift_psi_score = Gauge(
    "pfe_drift_psi_score",
    "Population Stability Index (PSI)",
    ["commodity", "model_type"]
)

pfe_drift_ks_statistic = Gauge(
    "pfe_drift_ks_statistic",
    "Kolmogorov-Smirnov statistic",
    ["commodity", "model_type"]
)

pfe_drift_mape_7d = Gauge(
    "pfe_drift_mape_7d",
    "MAPE prognoz z ostatnich 7 dni (performance drift)",
    ["commodity", "model_type"]
)

pfe_retrain_total = Counter(
    "pfe_retrain_total",
    "Liczba retrain wyzwolonych",
    ["commodity", "model_type", "trigger"]   # trigger: "drift" | "schedule" | "manual"
)

# ─── Modele ───────────────────────────────────────────────────

pfe_model_age_days = Gauge(
    "pfe_model_age_days",
    "Wiek aktywnego modelu [dni od trained_at]",
    ["commodity", "model_type"]
)

pfe_model_train_duration_seconds = Histogram(
    "pfe_model_train_duration_seconds",
    "Czas trenowania modelu",
    ["model_type", "commodity"],
    buckets=[10, 30, 60, 120, 300, 600, 1800]
)

# ─── API ─────────────────────────────────────────────────────

pfe_api_request_total = Counter(
    "pfe_api_request_total",
    "Żądania HTTP do PFE API",
    ["method", "endpoint", "status_code"]
)

pfe_api_duration_seconds = Histogram(
    "pfe_api_duration_seconds",
    "Latencja PFE API",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5]
)

# ─── Outbox / Kafka ───────────────────────────────────────────

pfe_outbox_lag_seconds = Gauge(
    "pfe_outbox_lag_seconds",
    "Opóźnienie publikacji eventów Outbox"
)

pfe_outbox_published_total = Counter(
    "pfe_outbox_published_total",
    "Opublikowane eventy Outbox",
    ["topic"]
)

# ─── Makro / FX ───────────────────────────────────────────────

pfe_macro_staleness_hours = Gauge(
    "pfe_macro_staleness_hours",
    "Wiek najnowszego macro snapshot [h]"
)

pfe_fx_staleness_hours = Gauge(
    "pfe_fx_staleness_hours",
    "Wiek kursu walutowego ECB [h]",
    ["currency"]
)
```

### 8.2 Dashboardy Grafana (7 dashboardów)

| Dashboard | Panele |
|-----------|--------|
| **PFE Overview** | Prognozy/dzień, MAPE per commodity, drift status heatmap, aktywne modele |
| **Price History** | OHLC chart per surowiec, QoQ/YoY zmiana, korelacja par |
| **Forecast Accuracy** | Backtest MAPE vs. horizon (1/7/14/30/90 dni), coverage CI, residuals |
| **Ensemble Performance** | Wagi ensemble w czasie, który model dominuje, volatility forecast vs. realized |
| **Drift & Retraining** | PSI trend, KS-statistic, MAPE rolling 7d vs. baseline, retrain history |
| **Data Quality** | Freshness per source, anomaly rate, missing days heatmap, quality_score distribution |
| **API Performance** | P50/P95/P99 latency, forecast generation duration, Outbox lag |

### 8.3 Reguły Alertmanager (9 reguł)

```yaml
groups:
  - name: pfe_alerts
    rules:

      - alert: PFEDataFreshnessHigh
        expr: pfe_data_freshness_hours{source_id=~"LME|EEX|EUROSTAT"} > 26
        for: 30m
        severity: warning
        annotations:
          summary: "Brak danych z {{ $labels.source_id }}/{{ $labels.commodity }} > 26h"

      - alert: PFEForecastMAPEHigh
        expr: pfe_forecast_mape{horizon_days="30"} > 0.08
        for: 1h
        severity: warning
        annotations:
          summary: "MAPE 30-dniowa {{ $labels.commodity }}/{{ $labels.model_type }} > 8%"

      - alert: PFEDriftDetected
        expr: pfe_drift_psi_score > 0.20
        for: 5m
        severity: critical
        annotations:
          summary: "PSI={{ $value }} dla {{ $labels.commodity }} — model drift, retrain wymagany"

      - alert: PFECoverageBelow80
        expr: pfe_forecast_coverage_95 < 0.80
        for: 1h
        severity: warning
        annotations:
          summary: "CI 95% coverage < 80% dla {{ $labels.commodity }} — złe przedziały ufności"

      - alert: PFEModelTooOld
        expr: pfe_model_age_days > 60
        for: 1h
        severity: warning
        annotations:
          summary: "Model {{ $labels.model_type }}/{{ $labels.commodity }} nieodświeżony > 60 dni"

      - alert: PFEOutboxLag
        expr: pfe_outbox_lag_seconds > 60
        for: 5m
        severity: critical
        annotations:
          summary: "Outbox event lag > 60s — problem z Kafka publishing"

      - alert: PFEForecastFailed
        expr: rate(pfe_forecast_total{status="FAILED"}[30m]) > 0
        for: 5m
        severity: critical
        annotations:
          summary: "Nieudane generowanie prognoz — sprawdź forecast worker"

      - alert: PFEMacroStale
        expr: pfe_macro_staleness_hours > 30
        for: 30m
        severity: warning
        annotations:
          summary: "Makro snapshot starszy niż 30h — brak danych ECB/Eurostat"

      - alert: PFEPriceAnomalySpike
        expr: rate(pfe_ingest_anomalies_total[15m]) > 10
        for: 5m
        severity: warning
        annotations:
          summary: "Wzrost anomalii cenowych > 10/min — sprawdź źródło danych"
```

### 8.4 SLI / SLO

| SLI | SLO |
|-----|-----|
| Forecast generation P95 (ENSEMBLE) | ≤ 120s |
| API P95 GET /forecast | ≤ 500ms |
| Data freshness (DAILY sources) | ≤ 26h |
| MAPE 30-day horizon (ENSEMBLE) | ≤ 6% |
| CI 95% empirical coverage | ≥ 88% |
| Failure rate (forecast) | < 2% |
| Outbox lag | < 30s (p99) |
| API Availability | ≥ 99.5% |

---

## 9. Drift Detection

### 9.1 Typy driftu

| Typ | Opis | Wykrywana przez |
|-----|------|----------------|
| **Data drift** | Rozkład cen wejściowych zmienił się vs. dane treningowe | PSI, KS test |
| **Concept drift** | Relacja cecha → cena uległa zmianie (np. nowy reżim rynkowy) | MAPE rolling > baseline × 1.5 |
| **Covariate drift** | Rozkład zmiennych zewnętrznych (FX, PMI) się zmienił | PSI per feature |
| **Distribution drift** | Ogon rozkładu (tail risk) nie jest pokryty przez CI | Coverage check |

### 9.2 DriftDetector

```python
import numpy as np
from scipy import stats
from datetime import date, timedelta
from dataclasses import dataclass

@dataclass
class DriftReport:
    commodity:       str
    model_type:      str
    check_date:      date
    drift_status:    str   # OK / WARNING / DRIFT_DETECTED / RETRAIN_REQUIRED
    psi_score:       float | None
    ks_statistic:    float | None
    ks_p_value:      float | None
    mape_7d:         float | None
    mape_30d:        float | None
    mape_baseline:   float | None
    details:         dict
    retrain_triggered: bool = False

class DriftDetector:
    """
    Wykrywa drift modeli prognozowania.
    Uruchamiany codziennie jako scheduled job.
    """

    PSI_WARNING   = 0.10
    PSI_CRITICAL  = 0.20
    KS_P_WARNING  = 0.05
    MAPE_RATIO    = 1.50    # MAPE_7d > baseline × 1.5 → warning
    COVERAGE_MIN  = 0.80    # 95% CI coverage < 80% → warning

    def __init__(
        self,
        repo:      "PriceRepository",
        eval_repo: "EvaluationRepository",
        db:        "AsyncpgPool",
    ):
        self._repo      = repo
        self._eval_repo = eval_repo
        self._db        = db

    async def check(
        self,
        commodity:  str,
        model_type: str,
    ) -> DriftReport:
        today = date.today()

        # Dane treningowe (referencja)
        model_meta  = await self._db.get_active_model(commodity, model_type)
        train_start = model_meta["train_start"]
        train_end   = model_meta["train_end"]
        mape_baseline = model_meta["backtest_mape"]

        train_prices = await self._repo.get_price_array(
            commodity, train_start, train_end)
        recent_prices = await self._repo.get_price_array(
            commodity,
            today - timedelta(days=90),
            today,
        )

        # ── PSI (Population Stability Index) ─────────────────────
        psi = self._compute_psi(train_prices, recent_prices, n_bins=10)

        # ── KS test ───────────────────────────────────────────────
        ks_stat, ks_p = stats.ks_2samp(train_prices, recent_prices)

        # ── MAPE rolling ─────────────────────────────────────────
        mape_7d  = await self._eval_repo.get_rolling_mape(commodity, model_type, 7)
        mape_30d = await self._eval_repo.get_rolling_mape(commodity, model_type, 30)

        # ── Coverage check ────────────────────────────────────────
        coverage = await self._eval_repo.get_recent_coverage(
            commodity, model_type, days=30)

        # ── Status ────────────────────────────────────────────────
        drift_status   = "OK"
        retrain        = False
        details: dict  = {}

        if psi > self.PSI_CRITICAL:
            drift_status = "RETRAIN_REQUIRED"
            retrain      = True
            details["psi_reason"] = f"PSI={psi:.4f} > {self.PSI_CRITICAL} (critical)"
        elif psi > self.PSI_WARNING:
            drift_status = "DRIFT_DETECTED"
            details["psi_reason"] = f"PSI={psi:.4f} > {self.PSI_WARNING} (warning)"

        if mape_7d and mape_baseline and mape_7d > mape_baseline * self.MAPE_RATIO:
            drift_status = max(drift_status, "DRIFT_DETECTED",
                               key=["OK","WARNING","DRIFT_DETECTED","RETRAIN_REQUIRED"].index)
            details["mape_reason"] = (
                f"MAPE_7d={mape_7d:.4f} > {self.MAPE_RATIO}×baseline={mape_baseline:.4f}"
            )

        if ks_p < self.KS_P_WARNING:
            if drift_status == "OK":
                drift_status = "WARNING"
            details["ks_reason"] = f"KS p-value={ks_p:.4f} < {self.KS_P_WARNING}"

        if coverage is not None and coverage < self.COVERAGE_MIN:
            if drift_status == "OK":
                drift_status = "WARNING"
            details["coverage_reason"] = f"95% CI coverage={coverage:.3f} < {self.COVERAGE_MIN}"

        report = DriftReport(
            commodity=commodity,
            model_type=model_type,
            check_date=today,
            drift_status=drift_status,
            psi_score=psi,
            ks_statistic=float(ks_stat),
            ks_p_value=float(ks_p),
            mape_7d=mape_7d,
            mape_30d=mape_30d,
            mape_baseline=mape_baseline,
            details=details,
            retrain_triggered=retrain,
        )
        await self._db.save_drift_report(report)

        if retrain:
            await self._trigger_retrain(commodity, model_type)

        return report

    # ── PSI ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_psi(
        expected: np.ndarray,
        actual:   np.ndarray,
        n_bins:   int = 10,
    ) -> float:
        """
        Population Stability Index.
        PSI < 0.10: stable; 0.10–0.20: some drift; > 0.20: significant drift.
        """
        bins     = np.percentile(expected, np.linspace(0, 100, n_bins + 1))
        bins[0]  = -np.inf
        bins[-1] = np.inf

        exp_pct = np.histogram(expected, bins=bins)[0] / len(expected)
        act_pct = np.histogram(actual,   bins=bins)[0] / len(actual)

        # Unikaj log(0)
        exp_pct = np.where(exp_pct == 0, 1e-9, exp_pct)
        act_pct = np.where(act_pct == 0, 1e-9, act_pct)

        psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
        return float(psi)

    async def _trigger_retrain(self, commodity: str, model_type: str) -> None:
        """Publikuje event retrainu → Forecast Worker."""
        await self._db.insert_outbox_event(
            topic="pfe.drift.detected",
            key=f"{commodity}:{model_type}",
            payload={
                "commodity":   commodity,
                "model_type":  model_type,
                "trigger":     "drift",
                "action":      "retrain",
            },
        )
```

### 9.3 Automatyczny retrain — schedule

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class RetrainScheduler:
    """
    Harmonogram retrenowania:
    - Codzienny drift check
    - Miesięczny forced retrain (niezależnie od driftu)
    - Natychmiastowy na sygnał PSI > 0.20
    """

    FORCED_RETRAIN_DAYS = 30   # co 30 dni bezwarunkowo

    def __init__(
        self,
        drift_detector: DriftDetector,
        forecasting_engine: "ForecastingEngine",
        db: "AsyncpgPool",
    ):
        self._drift   = drift_detector
        self._engine  = forecasting_engine
        self._db      = db
        self._sched   = AsyncIOScheduler()

    def start(self) -> None:
        self._sched.add_job(
            self._daily_drift_check,
            trigger="cron",
            hour=6, minute=0,     # 06:00 UTC (po ingestion)
        )
        self._sched.add_job(
            self._forced_monthly_retrain,
            trigger="cron",
            day=1, hour=2, minute=0,
        )
        self._sched.start()

    async def _daily_drift_check(self) -> None:
        models = await self._db.get_active_model_list()
        for commodity, model_type in models:
            report = await self._drift.check(commodity, model_type)
            if report.retrain_triggered:
                await self._engine.retrain(commodity, model_type, trigger="drift")

    async def _forced_monthly_retrain(self) -> None:
        models = await self._db.get_active_model_list()
        for commodity, model_type in models:
            meta = await self._db.get_active_model(commodity, model_type)
            age  = (date.today() - meta["trained_at"].date()).days
            if age >= self.FORCED_RETRAIN_DAYS:
                await self._engine.retrain(commodity, model_type, trigger="schedule")
```

### 9.4 Forecast Evaluator — ex-post accuracy

```python
class ForecastEvaluator:
    """
    Porównuje ex-ante prognozy z ex-post rzeczywistymi cenami.
    Uruchamiany codziennie — ocenia wszystkie forecasts z N dni temu.
    """

    def __init__(self, db: "AsyncpgPool", repo: "PriceRepository"):
        self._db   = db
        self._repo = repo

    async def evaluate_all(self, as_of: date) -> None:
        # Pobierz prognozy wygenerowane N dni temu (dla N = 1,7,14,30,90)
        for horizon in [1, 7, 14, 30, 90]:
            forecast_date = as_of - timedelta(days=horizon)
            forecasts = await self._db.get_forecasts_generated_on(forecast_date)
            for fc in forecasts:
                await self._evaluate_one(fc, as_of, horizon)

    async def _evaluate_one(
        self, fc: dict, as_of: date, horizon: int
    ) -> None:
        # Punkty prognozy do dziś (lub do końca horyzontu)
        points = await self._db.get_forecast_points(
            fc["forecast_id"],
            end_date=as_of,
        )
        if not points:
            return
        actual = await self._repo.get_price_array(
            fc["commodity"],
            points[0]["forecast_date"],
            points[-1]["forecast_date"],
        )
        if len(actual) < len(points):
            return  # dane jeszcze niekompletne

        pred      = np.array([p["predicted"] for p in points[:len(actual)]])
        lo95      = np.array([p["lower_95"]  for p in points[:len(actual)]])
        hi95      = np.array([p["upper_95"]  for p in points[:len(actual)]])

        mae       = float(np.mean(np.abs(pred - actual)))
        rmse      = float(np.sqrt(np.mean((pred - actual) ** 2)))
        mask      = actual != 0
        mape      = float(np.mean(np.abs((pred[mask] - actual[mask]) / actual[mask])))
        cov_95    = float(np.mean((actual >= lo95) & (actual <= hi95)))

        await self._db.save_evaluation(
            forecast_id=fc["forecast_id"],
            eval_date=as_of,
            horizon_days=horizon,
            mae=mae, rmse=rmse, mape=mape, coverage_95=cov_95,
            n_points=len(actual),
        )
        # Aktualizuj metrykę Prometheus
        pfe_drift_mape_7d.labels(
            commodity=fc["commodity"],
            model_type=fc["model_type"],
        ).set(mape)
```

### 9.5 Thresholds driftu

| Miara | OK | WARNING | DRIFT_DETECTED | RETRAIN_REQUIRED |
|-------|----|:-------:|:--------------:|:----------------:|
| PSI | < 0.10 | 0.10–0.20 | — | > 0.20 |
| KS p-value | > 0.10 | 0.05–0.10 | < 0.05 | — |
| MAPE_7d / MAPE_baseline | < 1.2× | 1.2–1.5× | > 1.5× | > 2.0× |
| CI 95% coverage | ≥ 88% | 80–88% | < 80% | — |
| Model age | ≤ 30d | 30–60d | — | > 60d |
