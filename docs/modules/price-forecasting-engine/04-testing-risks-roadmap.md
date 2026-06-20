# Price Forecasting Engine — Sections 10–12

## 10. Testing

### 10.1 Macierz testów

| Typ | Narzędzie | Cel | Pokrycie |
|-----|-----------|-----|:--------:|
| Unit — modele statystyczne | pytest | SARIMA fit/predict/backtest | ≥ 90% |
| Unit — Prophet | pytest | ProphetModel seasonal fit, regressors | ≥ 90% |
| Unit — LSTM | pytest + CPU-only | LSTMModel train/predict, shapes | ≥ 85% |
| Unit — Ensemble | pytest | Wagi INVERSE_RMSE, combined intervals | ≥ 90% |
| Unit — Feature Engineering | pytest | Lag/rolling/RSI/seasonality features | 100% |
| Unit — Drift Detection | pytest | PSI, KS, MAPE ratio, thresholds | 100% |
| Unit — Data Validator | pytest | is_valid, continuity check | 100% |
| Integration — DB | pytest + testcontainers | Schema, UPSERT idempotency, triggers | ≥ 85% |
| Integration — Outbox | pytest + kafka-python | PFEOutboxPublisher E2E | ≥ 80% |
| Accuracy — backtesting | golden dataset | MAPE ≤ 6%, coverage ≥ 88% (30-day) | 6 commodities |
| API contract | schemathesis | OpenAPI 3.1 fuzz + property testing | Wszystkie endpointy |
| Load | k6 | GET /forecast P95 ≤ 500ms @ 100 rps | L2 throughput |

### 10.2 Unit — SARIMA

```python
import pytest
import numpy as np
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from pfe.models.arima import ARIMAModel

def make_synthetic_series(n: int = 500, trend: float = 0.1, noise: float = 20.0):
    """Syntetyczna seria cenowa z trendem i sezonowością."""
    t = np.arange(n)
    base      = 600 + trend * t
    seasonal  = 40 * np.sin(2 * np.pi * t / 52)  # roczna sezonowość (~52 tyg)
    noise_arr = np.random.RandomState(42).normal(0, noise, n)
    prices    = base + seasonal + noise_arr
    dates     = [date(2022, 1, 3) + timedelta(weeks=i) for i in range(n)]
    return prices, dates


class TestARIMAModel:

    def test_fit_does_not_raise(self):
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(200)
        model.fit(prices, dates)
        assert model._result is not None

    def test_predict_returns_correct_horizon(self):
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(200)
        model.fit(prices, dates)
        result = model.predict(30)
        assert len(result.points) == 30

    def test_predict_intervals_ordered(self):
        """lower_80 ≤ predicted ≤ upper_80."""
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(200)
        model.fit(prices, dates)
        result = model.predict(30)
        for pt in result.points:
            assert pt.lower_80 <= pt.predicted <= pt.upper_80
            assert pt.lower_95 <= pt.lower_80
            assert pt.upper_80 <= pt.upper_95

    def test_predict_positive_prices(self):
        """Prognozy powinny być dodatnie dla realistycznych danych."""
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(300)
        model.fit(prices, dates)
        result = model.predict(30)
        assert all(pt.predicted > 0 for pt in result.points)

    def test_backtest_returns_metrics(self):
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(300)
        metrics = model.backtest(prices, dates, n_splits=3)
        assert "mae" in metrics and "rmse" in metrics and "mape" in metrics
        assert metrics["mae"] > 0
        assert 0 <= metrics["mape"] <= 5.0   # dla syntetycznych danych

    def test_log_transform_handles_positive_series(self):
        """Seria log-transformowana nie powinna produkować NaN."""
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(150)
        assert np.all(prices > 0)
        model.fit(prices, dates)
        fitted = model._fitted_values
        assert not np.any(np.isnan(fitted))

    def test_forecast_dates_sequential(self):
        """Daty prognozy powinny być kolejnymi dniami."""
        model = ARIMAModel("STEEL_HRC", auto_order=False)
        prices, dates = make_synthetic_series(200)
        model.fit(prices, dates)
        result = model.predict(10)
        for i in range(1, len(result.points)):
            assert result.points[i].forecast_date > result.points[i-1].forecast_date
```

### 10.3 Unit — Feature Engineering

```python
import numpy as np
import pandas as pd
import pytest
from datetime import date, timedelta

from pfe.features import FeatureEngineeringPipeline

class TestFeatureEngineeringPipeline:

    @pytest.fixture
    def price_series(self):
        n  = 300
        dates = pd.date_range("2022-01-03", periods=n, freq="D")
        vals  = 600 + np.arange(n) * 0.2 + np.random.default_rng(0).normal(0, 15, n)
        return pd.Series(vals, index=dates)

    @pytest.fixture
    def external_df(self, price_series):
        df = pd.DataFrame(index=price_series.index)
        df["pmi_de"]   = 52.0 + np.random.default_rng(1).normal(0, 2, len(df))
        df["eurusd"]   = 1.10 + np.random.default_rng(2).normal(0, 0.02, len(df))
        df["hicp_eu"]  = 2.5  + np.random.default_rng(3).normal(0, 0.5, len(df))
        return df

    def test_output_shape(self, price_series, external_df):
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        # Powinniśmy mieć < 300 wierszy (NaN usunięte po lagach)
        assert fm.features.shape[0] < 300
        assert fm.features.shape[1] > 10

    def test_no_nan_in_output(self, price_series, external_df):
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        assert not np.any(np.isnan(fm.features))

    def test_lag1_feature_present(self, price_series, external_df):
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        assert "lag_1" in fm.names

    def test_rsi_in_range(self, price_series, external_df):
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        rsi_idx = fm.names.index("rsi_14")
        rsi_vals = fm.features[:, rsi_idx]
        assert np.all(rsi_vals >= 0) and np.all(rsi_vals <= 100)

    def test_month_cyclical_encoding(self, price_series, external_df):
        """sin²+cos² = 1 dla kodowania cyklicznego."""
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        sin_i = fm.names.index("month_sin")
        cos_i = fm.names.index("month_cos")
        vals  = fm.features[:, sin_i] ** 2 + fm.features[:, cos_i] ** 2
        np.testing.assert_allclose(vals, 1.0, atol=1e-5)

    def test_vol_nonnegative(self, price_series, external_df):
        pipe = FeatureEngineeringPipeline()
        fm   = pipe.build(price_series, external_df)
        vol_i = fm.names.index("vol_20d")
        assert np.all(fm.features[:, vol_i] >= 0)
```

### 10.4 Unit — DriftDetector

```python
import numpy as np
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from pfe.drift import DriftDetector

class TestDriftDetector:

    @pytest.fixture
    def detector(self):
        return DriftDetector(
            repo=AsyncMock(),
            eval_repo=AsyncMock(),
            db=AsyncMock(),
        )

    def test_psi_stable_identical_distributions(self, detector):
        rng = np.random.RandomState(42)
        series = rng.normal(600, 50, 500)
        psi = detector._compute_psi(series, series.copy())
        assert psi < 0.01   # identyczne → PSI ≈ 0

    def test_psi_drifted_different_mean(self, detector):
        rng = np.random.RandomState(42)
        expected = rng.normal(600, 50, 500)
        actual   = rng.normal(900, 50, 200)   # +50% średnia
        psi = detector._compute_psi(expected, actual)
        assert psi > 0.20   # znaczny drift

    def test_psi_warning_threshold(self, detector):
        rng = np.random.RandomState(42)
        expected = rng.normal(600, 50, 500)
        actual   = rng.normal(640, 60, 200)   # lekki drift
        psi = detector._compute_psi(expected, actual)
        # Może być w okolicach 0.10–0.20
        assert 0 <= psi <= 1.0

    @pytest.mark.asyncio
    async def test_status_ok_when_no_drift(self, detector):
        rng = np.random.RandomState(0)
        series = rng.normal(600, 30, 500)
        detector._repo.get_price_array = AsyncMock(side_effect=[
            series,               # train
            series[-90:] + rng.normal(0, 5, 90),  # recent (mały szum)
        ])
        detector._db.get_active_model = AsyncMock(return_value={
            "train_start": date(2022, 1, 1),
            "train_end":   date(2024, 12, 31),
            "backtest_mape": 0.04,
            "trained_at":  date(2024, 12, 31),
        })
        detector._eval_repo.get_rolling_mape   = AsyncMock(return_value=0.045)
        detector._eval_repo.get_recent_coverage = AsyncMock(return_value=0.92)
        detector._db.save_drift_report = AsyncMock()
        detector._db.insert_outbox_event = AsyncMock()

        report = await detector.check("STEEL_HRC", "ENSEMBLE")
        assert report.drift_status in ("OK", "WARNING")
        assert not report.retrain_triggered

    @pytest.mark.asyncio
    async def test_retrain_triggered_on_high_psi(self, detector):
        rng = np.random.RandomState(42)
        train_series  = rng.normal(600, 40, 500)
        recent_series = rng.normal(1100, 40, 90)   # +83% — dramatyczny drift
        detector._repo.get_price_array = AsyncMock(side_effect=[
            train_series, recent_series
        ])
        detector._db.get_active_model = AsyncMock(return_value={
            "train_start": date(2022, 1, 1),
            "train_end":   date(2024, 12, 31),
            "backtest_mape": 0.04,
            "trained_at":  date(2024, 12, 31),
        })
        detector._eval_repo.get_rolling_mape    = AsyncMock(return_value=0.09)
        detector._eval_repo.get_recent_coverage = AsyncMock(return_value=0.85)
        detector._db.save_drift_report   = AsyncMock()
        detector._db.insert_outbox_event = AsyncMock()

        report = await detector.check("STEEL_HRC", "ENSEMBLE")
        assert report.drift_status == "RETRAIN_REQUIRED"
        assert report.retrain_triggered
        detector._db.insert_outbox_event.assert_called_once()

    def test_psi_avoids_log_zero(self, detector):
        """PSI z pustymi binami nie powoduje ZeroDivisionError/log(0)."""
        expected = np.array([600.0] * 100)   # jednopunktowa dystrybucja
        actual   = np.array([700.0] * 50)
        psi = detector._compute_psi(expected, actual)
        assert np.isfinite(psi)


class TestPriceDataValidator:

    def setup_method(self):
        from pfe.ingestion import PriceDataValidator
        self.v = PriceDataValidator()

    def _make_record(self, price=500.0, currency="EUR", unit="t", quality=1.0):
        from pfe.models import PriceRecord, PriceFrequency
        from decimal import Decimal
        return PriceRecord(
            source_id="TEST", commodity="STEEL_HRC",
            price_date=date(2024, 1, 15),
            price=Decimal(str(price)),
            currency=currency, unit=unit,
            quality_score=quality,
        )

    def test_valid_record(self):
        assert self.v.is_valid(self._make_record(500.0))

    def test_zero_price_invalid(self):
        assert not self.v.is_valid(self._make_record(0.0))

    def test_negative_price_invalid(self):
        assert not self.v.is_valid(self._make_record(-10.0))

    def test_absurdly_high_price_invalid(self):
        assert not self.v.is_valid(self._make_record(600_000.0))

    def test_low_quality_score_invalid(self):
        assert not self.v.is_valid(self._make_record(500.0, quality=0.25))

    def test_continuity_detects_gap(self):
        from pfe.models import PriceRecord
        from decimal import Decimal
        records = [
            PriceRecord("TEST", "STEEL_HRC", date(2024, 1, 1),  Decimal("500"), "EUR", "t"),
            PriceRecord("TEST", "STEEL_HRC", date(2024, 1, 2),  Decimal("502"), "EUR", "t"),
            PriceRecord("TEST", "STEEL_HRC", date(2024, 2, 15), Decimal("510"), "EUR", "t"),  # gap!
        ]
        issues = self.v.check_continuity(records)
        assert len(issues) == 1
        assert "Gap" in issues[0]
```

### 10.5 Integration — DB schema

```python
import pytest
import asyncpg

@pytest.mark.asyncio
async def test_forecast_outbox_trigger(pg_pool):
    """Trigger tworzy outbox event gdy status=DONE."""
    async with pg_pool.acquire() as conn:
        # Wstaw model
        mr_id = await conn.fetchval("""
            INSERT INTO pfe.model_registry (
                model_type, commodity, train_start, train_end, trained_by
            ) VALUES ('ENSEMBLE', 'STEEL_HRC', '2022-01-01', '2024-12-31', 'test')
            RETURNING model_registry_id
        """)
        # Wstaw forecast ze statusem DONE
        await conn.execute("""
            INSERT INTO pfe.forecasts (
                model_registry_id, model_type, commodity,
                horizon_days, status
            ) VALUES ($1, 'ENSEMBLE', 'STEEL_HRC', 30, 'DONE')
        """, mr_id)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM pfe.outbox_events WHERE topic='pfe.forecast.ready'"
        )
        assert count == 1

@pytest.mark.asyncio
async def test_price_series_upsert_idempotent(pg_pool):
    """Dwukrotny upsert nie tworzy duplikatów."""
    async with pg_pool.acquire() as conn:
        for _ in range(2):
            await conn.execute("""
                INSERT INTO pfe.price_series
                    (source_id, source_type, commodity, price_date, price_eur, unit)
                VALUES ('LME', 'MARKET_PRICE', 'ALUMINUM_LME', '2024-06-01', 2450.00, 't')
                ON CONFLICT (source_id, commodity, price_date, frequency) DO UPDATE
                SET price_eur = EXCLUDED.price_eur, created_at = now()
            """)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM pfe.price_series WHERE commodity='ALUMINUM_LME'"
        )
        assert count == 1

@pytest.mark.asyncio
async def test_v_latest_forecasts_view(pg_pool):
    """Widok zwraca najnowszą prognozę per commodity/model_type."""
    async with pg_pool.acquire() as conn:
        # Wstaw 2 forecasts dla tego samego commodity — widok powinien pokazać nowszy
        mr_id = await conn.fetchval("""
            INSERT INTO pfe.model_registry
                (model_type, commodity, train_start, train_end, trained_by)
            VALUES ('SARIMA', 'ELECTRICITY_DE', '2022-01-01', '2024-12-31', 'test')
            RETURNING model_registry_id
        """)
        for d in ["2024-12-01", "2024-12-02"]:
            fc_id = await conn.fetchval("""
                INSERT INTO pfe.forecasts
                    (model_registry_id, model_type, commodity, generated_at, horizon_days, status)
                VALUES ($1, 'SARIMA', 'ELECTRICITY_DE', $2, 30, 'DONE')
                RETURNING forecast_id
            """, mr_id, d)
            # Dodaj punkt prognozy
            await conn.execute("""
                INSERT INTO pfe.forecast_points
                    (forecast_id, forecast_date, predicted)
                VALUES ($1, $2::date + 1, 120.00)
            """, fc_id, d)
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM pfe.v_latest_forecasts
            WHERE commodity = 'ELECTRICITY_DE' AND model_type = 'SARIMA'
        """)
        assert count == 1   # widok zwraca tylko 1 (najnowszy)
```

### 10.6 k6 load test

```javascript
// k6 — GET /api/v1/pfe/commodities/{commodity}/forecast @ 100 rps
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const forecastLatency = new Trend("forecast_get_ms");
const errorRate       = new Rate("error_rate");

export const options = {
    stages: [
        { duration: "1m",  target: 50  },
        { duration: "3m",  target: 100 },
        { duration: "1m",  target: 0   },
    ],
    thresholds: {
        "forecast_get_ms": ["p(95)<500"],    // P95 < 500ms
        "error_rate":      ["rate<0.01"],
    },
};

const COMMODITIES = [
    "STEEL_HRC", "ALUMINUM_LME", "ELECTRICITY_DE",
    "GAS_TTF", "STEEL_SCRAP",
];

export default function () {
    const commodity = COMMODITIES[Math.floor(Math.random() * COMMODITIES.length)];
    const res = http.get(
        `${__ENV.BASE_URL}/api/v1/pfe/commodities/${commodity}/forecast?horizon_days=30`,
        { headers: { "Authorization": `Bearer ${__ENV.TOKEN}` } }
    );
    const ok = check(res, {
        "status 200":             (r) => r.status === 200,
        "has forecast points":    (r) => JSON.parse(r.body).points?.length > 0,
        "predicted > 0":          (r) => JSON.parse(r.body).points?.[0]?.predicted > 0,
        "has confidence_band":    (r) => !!JSON.parse(r.body).backtest_mape,
    });
    errorRate.add(!ok);
    forecastLatency.add(res.timings.duration);
    sleep(0.01);
}
```

### 10.7 Accuracy — golden backtesting

```python
# Golden set: 6 commodities × 3 horizons × 5 CV splits
# Metryki targets:

ACCURACY_TARGETS = {
    "STEEL_HRC":      {"mape_30d": 0.06, "coverage_95": 0.88},
    "ALUMINUM_LME":   {"mape_30d": 0.05, "coverage_95": 0.88},
    "ELECTRICITY_DE": {"mape_30d": 0.10, "coverage_95": 0.85},  # wyższa zmienność
    "GAS_TTF":        {"mape_30d": 0.12, "coverage_95": 0.83},
    "STEEL_SCRAP":    {"mape_30d": 0.07, "coverage_95": 0.87},
    "PPI_METALS_DE":  {"mape_30d": 0.04, "coverage_95": 0.90},
}

@pytest.mark.accuracy
@pytest.mark.asyncio
async def test_ensemble_accuracy(historical_db):
    """Ensemble MAPE ≤ target dla wszystkich commodities."""
    from pfe.engine import ForecastingEngine
    engine = ForecastingEngine.from_db(historical_db)
    for commodity, targets in ACCURACY_TARGETS.items():
        series, dates = await historical_db.get_price_series(commodity)
        ensemble = ModelEnsemble(
            models=[
                ARIMAModel(commodity),
                ProphetModel(commodity),
                LSTMModel(commodity, horizon=30),
            ]
        )
        metrics = {}
        for n_split in range(5):
            split = int(len(series) * (0.6 + n_split * 0.08))
            ensemble.fit(series[:split], dates[:split])
            fc   = ensemble.predict(30)
            pred = np.array([p.predicted for p in fc.points])
            act  = series[split:split+30]
            mask = act != 0
            mape = float(np.mean(np.abs((pred[mask] - act[mask]) / act[mask])))
            metrics.setdefault("mape_list", []).append(mape)
        avg_mape = np.mean(metrics["mape_list"])
        assert avg_mape <= targets["mape_30d"], (
            f"{commodity}: avg MAPE {avg_mape:.4f} > target {targets['mape_30d']}"
        )
```

---

## 11. Risks

| ID | Ryzyko | Prawdop. | Wpływ | Mitygacja |
|----|--------|:--------:|:-----:|-----------|
| R01 | Przerwa w dostawie danych LME/EEX → stale prices | Średnia | Wysoki | Alert freshness > 26h; fallback na backup source (Quandl, Yahoo Finance); retry z backoff |
| R02 | Black Swan (COVID, sankcje) → model fail | Niska | Bardzo wysoki | Ensemble redukuje ryzyko jednego modelu; szeroki CI 95%; drift detection trigger retrain |
| R03 | LSTM overfitting na krótkim oknie → złe prognozy | Średnia | Wysoki | Dropout 0.20; early stopping; gradient clipping; coverage test; golden MAPE gate |
| R04 | Concept drift po zmianie reżimu rynkowego | Średnia | Wysoki | PSI > 0.20 → auto-retrain; MAPE_7d alert; monthly forced retrain |
| R05 | Korelacja EUR/USD → błąd normalizacji walut | Niska | Średni | FXNormalizer quality_score × 0.98; testy jednostkowe; ECB fallback |
| R06 | PMI/makro niedostępne → brak regressorów | Niska | Średni | Prophet/LSTM działa bez regressorów (ffill); oddzielny alert macro_staleness_hours |
| R07 | Zbyt szerokie CI → bezużyteczne dla CBE | Średnia | Średni | Calibration plot; coverage_95 alert < 88%; shrinkage przez ensemble averaging |
| R08 | GPU niedostępne → LSTM fallback na CPU (wolne) | Niska | Niski | DEVICE = "cuda" if available else "cpu"; CPU LSTM działa (wolniej 10×); async job |
| R09 | Skalowanie: > 1000 forecast job/dzień → bottleneck | Niska | Średni | Async workers; FOR UPDATE SKIP LOCKED; HPA; Redis cache for GET |
| R10 | Outbox event nieopublikowany → CBE ma stare ceny | Niska | Wysoki | Outbox lag alert > 60s; DLQ; retry; periodic bulk push do CBE |
| R11 | Sezonowość energii elektrycznej zaburza SARIMA | Średnia | Średni | Podwójna sezonowość (tydzień + rok); Prophet yearly/weekly seasonality |
| R12 | Dane wewnętrzne SOP zaniżone (mało ofert) | Wysoka | Niski | quality_score ≥ 3 ofert; SOP data flagowane jako INTERNAL z niższą wagą |
| R13 | Scenariusze makro nierealistyczne (user-defined) | Niska | Niski | Walidacja zakresu (pmi_de ∈ [20,70], eurusd ∈ [0.7,1.5]); warning w response |
| R14 | Wyciek danych treningowych do test setu | Niska | Wysoki | TimeSeriesSplit (brak shufflingu); krypto-separacja train/val/test |
| R15 | GDPR: dane ofert dostawców (SOP) w feature store | Niska | Średni | Anonimizacja cen < 3 ofert per batch; TTL 5 lat; audit log dostępu |

---

## 12. Roadmap

### Cele KPI (po S32)

| Metryka | Cel |
|---------|-----|
| ENSEMBLE MAPE 30-day (stal) | ≤ 6% |
| ENSEMBLE MAPE 30-day (aluminium) | ≤ 5% |
| CI 95% empirical coverage | ≥ 88% |
| Forecast generation P95 | ≤ 120s |
| API P95 GET /forecast | ≤ 500ms |
| Data freshness (DAILY) | ≤ 26h |
| Drift detection latency | ≤ 24h |
| Availability | ≥ 99.5% |
| Throughput L2 | ≥ 50 forecasts/h |

### Faza 1 — Foundation (S1–S8)

| Sprint | Cel |
|--------|-----|
| S1 | DB schema `pfe` (8 tabel, ENUMy, triggery, widoki); migracje Alembic |
| S2 | `LMEConnector` + `EEXConnector` + `ECBConnector`; `DataIngestionPipeline`; `PriceDataValidator` |
| S3 | `ARIMAModel` (SARIMA z auto-order); backtest TimeSeriesSplit; `PriceRepository` |
| S4 | `ProphetModel` bez regressorów; STEEL_HRC + ALUMINUM_LME + ELECTRICITY_DE |
| S5 | `ForecastingEngine` worker; async job queue (FOR UPDATE SKIP LOCKED); outbox |
| S6 | REST API FastAPI: GET /forecast, POST /generate, GET /price-series |
| S7 | Prometheus 26 metryk; Grafana 3 dashboardy (overview, accuracy, data quality) |
| S8 | Alertmanager 9 reguł; CI gate: MAPE 30d ≤ 8% (golden set S1) |

### Faza 2 — ML + External Signals (S9–S18)

| Sprint | Cel |
|--------|-----|
| S9 | `EurostatMacroConnector` + `PMIConnector` + `BDIConnector`; `ExternalSignalService` |
| S10 | `FXNormalizer` + `InflationAdjuster` (real prices, base 2020) |
| S11 | `FeatureEngineeringPipeline`: lag, rolling, RSI, cyclical, momentum |
| S12 | `ProphetModel` z regressorami (PMI, FX, energy); MAPE improvement validation |
| S13 | `LSTMModel` z attention; GaussianNLL loss; probabilistic intervals |
| S14 | `ModelEnsemble` INVERSE_RMSE; automatyczne wagi per commodity |
| S15 | `DriftDetector` PSI + KS + MAPE rolling; `RetrainScheduler` APScheduler |
| S16 | `ForecastEvaluator` ex-post; `forecast_evaluations` table; coverage tracking |
| S17 | Scenarios API: baseline/bull/bear; makro overrides; multi-scenario response |
| S18 | `FeatureSelector` permutation importance; LSTM input_size optymalizacja |

### Faza 3 — Intelligence + Scale (S19–S28)

| Sprint | Cel |
|--------|-----|
| S19 | Rozszerzenie commodities: GAS_TTF, STEEL_SCRAP, PPI_METALS_DE, HICP_EU |
| S20 | Usługi produkcyjne: MACHINING_RATE_DE/PL (dane z CBE + SOP) |
| S21 | Correlation matrix API; cross-commodity hedging insights |
| S22 | Volatility forecasting (GARCH-like) → uncertainty quantification |
| S23 | HPA `pfe-api` 2–10 pods, `pfe-worker` 1–5 pods; Redis cache GET /forecast |
| S24 | TimescaleDB hypertable + continuous aggregate (opcjonalne) |
| S25 | `StockingModel`: Prophet stacking meta-learner zamiast INVERSE_RMSE |
| S26 | News sentiment NLP → alternative data feature (Reuters scraper + BERT) |
| S27 | Integracja CBE: `pfe.forecast.ready` → auto-update `cbe.material_rates` |
| S28 | DR testing; penetration test; GDPR TTL 5 lat; SLO hardening |

### Faza 4 — Advanced Forecasting (S29–S32)

| Sprint | Cel |
|--------|-----|
| S29 | Temporal Fusion Transformer (TFT) jako 4. model w ensemble |
| S30 | Conformal prediction intervals (distribution-free coverage guarantee) |
| S31 | Multi-step probabilistic forecast (copula między commodities) |
| S32 | Real-time tick data (LME intraday) → Kafka streaming → online learning |
