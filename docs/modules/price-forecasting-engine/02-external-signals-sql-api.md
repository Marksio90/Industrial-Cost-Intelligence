# Price Forecasting Engine — Sections 4–6

## 4. External Signals (Inflation, FX, PMI)

### 4.1 ExternalSignalService

```python
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import aiohttp
import pandas as pd

@dataclass
class MacroSnapshot:
    """Wszystkie zewnętrzne sygnały na dany dzień."""
    snapshot_date:   date
    eurusd:          float | None = None
    eurcny:          float | None = None
    eurpln:          float | None = None
    hicp_eu:         float | None = None   # % yoy
    ppi_metals_de:   float | None = None   # index 2015=100
    ppi_energy_de:   float | None = None
    pmi_de:          float | None = None   # 50 = neutral
    pmi_cn:          float | None = None
    pmi_us:          float | None = None
    bdi:             float | None = None   # Baltic Dry Index
    brent_usd:       float | None = None   # USD/bbl
    nat_gas_eur:     float | None = None   # EUR/MWh TTF
    capacity_util_steel: float | None = None  # % World Steel Assoc.

class ExternalSignalService:
    """Agreguje zewnętrzne sygnały makro + FX dla modeli prognozowania."""

    def __init__(
        self,
        ecb_connector:       "ECBConnector",
        eurostat_connector:  "EurostatMacroConnector",
        pmi_connector:       "PMIConnector",
        bdi_connector:       "BDIConnector",
        energy_connector:    "EEXConnector",
        repo:                "MacroRepository",
        redis:               "aioredis.Redis",
    ):
        self._ecb       = ecb_connector
        self._eurostat  = eurostat_connector
        self._pmi       = pmi_connector
        self._bdi       = bdi_connector
        self._energy    = energy_connector
        self._repo      = repo
        self._redis     = redis

    async def get_snapshot(self, d: date) -> MacroSnapshot:
        cache_key = f"pfe:macro:{d.isoformat()}"
        cached = await self._redis.get(cache_key)
        if cached:
            import json
            return MacroSnapshot(**json.loads(cached))

        fx, ppi, pmi, bdi_val, energy = await asyncio.gather(
            self._ecb.get_rates(d),
            self._eurostat.get_ppi(d),
            self._pmi.get_pmi(d),
            self._bdi.get_bdi(d),
            self._energy.get_spot(d),
            return_exceptions=True,
        )

        snap = MacroSnapshot(
            snapshot_date    = d,
            eurusd           = fx.get("EURUSD")        if not isinstance(fx, Exception) else None,
            eurcny           = fx.get("EURCNY")        if not isinstance(fx, Exception) else None,
            eurpln           = fx.get("EURPLN")        if not isinstance(fx, Exception) else None,
            hicp_eu          = ppi.get("hicp_eu")      if not isinstance(ppi, Exception) else None,
            ppi_metals_de    = ppi.get("ppi_metals")   if not isinstance(ppi, Exception) else None,
            ppi_energy_de    = ppi.get("ppi_energy")   if not isinstance(ppi, Exception) else None,
            pmi_de           = pmi.get("DE")           if not isinstance(pmi, Exception) else None,
            pmi_cn           = pmi.get("CN")           if not isinstance(pmi, Exception) else None,
            pmi_us           = pmi.get("US")           if not isinstance(pmi, Exception) else None,
            bdi              = bdi_val                 if not isinstance(bdi_val, Exception) else None,
            nat_gas_eur      = energy.get("TTF")       if not isinstance(energy, Exception) else None,
        )

        import json, dataclasses
        ttl = 86400 if d < date.today() else 3600
        await self._redis.setex(cache_key, ttl, json.dumps(dataclasses.asdict(snap), default=str))
        await self._repo.upsert_snapshot(snap)
        return snap

    async def get_dataframe(
        self, start: date, end: date
    ) -> pd.DataFrame:
        """Zwraca DataFrame z sygnałami makro do użycia jako regressors."""
        snaps = await self._repo.fetch_range(start, end)
        return pd.DataFrame([
            {
                "ds":            s.snapshot_date,
                "eurusd":        s.eurusd,
                "eurcny":        s.eurcny,
                "eurpln":        s.eurpln,
                "hicp_eu":       s.hicp_eu,
                "ppi_metals_de": s.ppi_metals_de,
                "ppi_energy_de": s.ppi_energy_de,
                "pmi_de":        s.pmi_de,
                "pmi_cn":        s.pmi_cn,
                "bdi":           s.bdi,
                "nat_gas_eur":   s.nat_gas_eur,
            }
            for s in snaps
        ]).set_index("ds").sort_index().ffill()
```

### 4.2 FXNormalizer — normalizacja walut do EUR

```python
from decimal import Decimal

class FXNormalizer:
    """Konwertuje PriceRecord do EUR używając ECB daily rates."""

    def __init__(self, ecb: "ECBConnector", redis: "aioredis.Redis"):
        self._ecb   = ecb
        self._redis = redis

    async def to_eur(self, record: "PriceRecord") -> "PriceRecord":
        if record.currency == "EUR":
            return record
        rate = await self._get_rate(record.currency, record.price_date)
        if rate is None:
            record.quality_score *= 0.7  # obniż score gdy brak kursu
            return record
        converted = record.price / rate   # np. USD → EUR = price / EURUSD
        return PriceRecord(
            source_id=record.source_id,
            commodity=record.commodity,
            price_date=record.price_date,
            price=converted,
            currency="EUR",
            unit=record.unit,
            frequency=record.frequency,
            quality_score=record.quality_score * 0.98,  # mała korekta za konwersję
        )

    async def _get_rate(self, currency: str, d: date) -> Decimal | None:
        key = f"pfe:fx:{currency}:{d.isoformat()}"
        cached = await self._redis.get(key)
        if cached:
            return Decimal(cached)
        rates = await self._ecb.get_rates(d)
        rate  = rates.get(f"EUR{currency}")
        if rate:
            await self._redis.setex(key, 86400 * 7, str(rate))
        return rate
```

### 4.3 Inflation Adjustment (real prices)

```python
class InflationAdjuster:
    """
    Deflates nominal prices to real (base year = 2020).
    Używa HICP EU lub PPI metals DE w zależności od klasy cenowej.
    """

    BASE_YEAR = 2020

    async def to_real(
        self,
        series: pd.Series,       # index=date, values=nominal EUR
        commodity_class: str,    # "METAL" | "ENERGY" | "SERVICES"
    ) -> pd.Series:
        index_col = {
            "METAL":    "ppi_metals_de",
            "ENERGY":   "ppi_energy_de",
            "SERVICES": "hicp_eu",
        }.get(commodity_class, "hicp_eu")

        macro_df = await self._signal_svc.get_dataframe(
            series.index.min().date(), series.index.max().date()
        )
        deflator = macro_df[index_col].reindex(series.index, method="ffill")

        # Base index = wartość deflator w roku bazowym
        base_mask   = deflator.index.year == self.BASE_YEAR
        base_index  = deflator[base_mask].mean()
        real_series = series * (base_index / deflator)
        return real_series
```

### 4.4 Korelacje — sygnały wiodące

| Sygnał | Opóźnienie | Korelacja z ceną stali | Uzasadnienie |
|--------|:----------:|:----------------------:|-------------|
| PMI Manufacturing DE | 1–3 mies. | +0.62 | Popyt na stal z przemysłu |
| PMI Manufacturing CN | 2–4 mies. | +0.55 | Chiny = 50%+ global steel output |
| Baltic Dry Index | 2–6 tyg. | +0.48 | Koszty transportu rud żelaza |
| EUR/USD | 0–4 tyg. | −0.45 | Stal wyceniana w USD → siła EUR obniża EUR-price |
| Cena energii DE | 0–2 tyg. | +0.38 | Energia = 20–30% kosztów hutnictwa |
| HICP EU | 1–3 mies. | +0.71 | Inflacja ogólna podnosi ceny surowców |
| Capacity utilization steel | 0–1 mies. | +0.68 | Wysoka utilizacja → wyższe ceny |

---

## 5. SQL Schema

### 5.1 Schemat PostgreSQL 16 — `pfe`

```sql
-- =============================================================
--  PRICE FORECASTING ENGINE — schemat `pfe`
--  PostgreSQL 16, extensions: pgcrypto, timescaledb (opcjonalne)
-- =============================================================

CREATE SCHEMA IF NOT EXISTS pfe;

-- ─────────────────────────────────────────────────────────────
-- ENUMy
-- ─────────────────────────────────────────────────────────────

CREATE TYPE pfe.price_frequency AS ENUM (
    'TICK', 'DAILY', 'WEEKLY', 'MONTHLY'
);

CREATE TYPE pfe.data_source_type AS ENUM (
    'MARKET_PRICE', 'INTERNAL', 'MACRO', 'ALTERNATIVE'
);

CREATE TYPE pfe.forecast_model AS ENUM (
    'SARIMA', 'PROPHET', 'LSTM', 'ENSEMBLE', 'NAIVE_BASELINE'
);

CREATE TYPE pfe.forecast_status AS ENUM (
    'PENDING', 'RUNNING', 'DONE', 'FAILED', 'STALE'
);

CREATE TYPE pfe.drift_status AS ENUM (
    'OK', 'WARNING', 'DRIFT_DETECTED', 'RETRAIN_REQUIRED'
);

-- ─────────────────────────────────────────────────────────────
-- Historyczne ceny (time series)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.price_series (
    price_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       TEXT          NOT NULL,
    source_type     pfe.data_source_type NOT NULL,
    commodity       TEXT          NOT NULL,
    price_date      DATE          NOT NULL,
    frequency       pfe.price_frequency NOT NULL DEFAULT 'DAILY',
    price_eur       NUMERIC(16,6) NOT NULL,
    price_orig      NUMERIC(16,6),          -- cena w oryginalnej walucie
    currency_orig   CHAR(3),
    unit            TEXT          NOT NULL DEFAULT 't',
    open_eur        NUMERIC(16,6),
    high_eur        NUMERIC(16,6),
    low_eur         NUMERIC(16,6),
    volume          NUMERIC(20,4),
    quality_score   NUMERIC(4,3)  NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (source_id, commodity, price_date, frequency)
);

-- Indeks BRIN — bardzo efektywny dla time series (sekwencyjny insert)
CREATE INDEX pfe_price_series_brin_idx
    ON pfe.price_series USING BRIN (price_date);

CREATE INDEX pfe_price_series_commodity_date_idx
    ON pfe.price_series (commodity, price_date DESC);

-- ─────────────────────────────────────────────────────────────
-- Zewnętrzne sygnały makro
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.macro_snapshots (
    snapshot_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date        DATE        NOT NULL UNIQUE,
    eurusd               NUMERIC(10,6),
    eurcny               NUMERIC(10,6),
    eurpln               NUMERIC(10,6),
    hicp_eu              NUMERIC(8,4),
    ppi_metals_de        NUMERIC(10,4),
    ppi_energy_de        NUMERIC(10,4),
    pmi_de               NUMERIC(6,2),
    pmi_cn               NUMERIC(6,2),
    pmi_us               NUMERIC(6,2),
    bdi                  NUMERIC(10,2),
    brent_usd            NUMERIC(10,4),
    nat_gas_eur          NUMERIC(10,4),
    capacity_util_steel  NUMERIC(6,3),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pfe_macro_date_idx ON pfe.macro_snapshots (snapshot_date DESC);

-- ─────────────────────────────────────────────────────────────
-- Modele i wersje
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.model_registry (
    model_registry_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_type          pfe.forecast_model NOT NULL,
    commodity           TEXT        NOT NULL,
    version             INT         NOT NULL DEFAULT 1,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    train_start         DATE        NOT NULL,
    train_end           DATE        NOT NULL,
    hyperparams         JSONB       NOT NULL DEFAULT '{}',
    feature_names       JSONB       NOT NULL DEFAULT '[]',
    artifact_path       TEXT,       -- S3 path do skryptu/wag modelu
    backtest_mae        NUMERIC(12,6),
    backtest_rmse       NUMERIC(12,6),
    backtest_mape       NUMERIC(8,6),
    coverage_80         NUMERIC(5,4),
    coverage_95         NUMERIC(5,4),
    trained_by          TEXT        NOT NULL DEFAULT 'system',
    trained_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_type, commodity, version)
);

CREATE INDEX pfe_model_registry_active_idx
    ON pfe.model_registry (commodity, model_type)
    WHERE is_active = TRUE;

-- ─────────────────────────────────────────────────────────────
-- Prognozy
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.forecasts (
    forecast_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_registry_id UUID      NOT NULL REFERENCES pfe.model_registry,
    model_type      pfe.forecast_model NOT NULL,
    commodity       TEXT        NOT NULL,
    generated_at    DATE        NOT NULL DEFAULT CURRENT_DATE,
    horizon_days    INT         NOT NULL,
    currency        CHAR(3)     NOT NULL DEFAULT 'EUR',
    unit            TEXT        NOT NULL DEFAULT 't',
    status          pfe.forecast_status NOT NULL DEFAULT 'DONE',
    run_duration_s  NUMERIC(8,3),
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pfe_forecasts_commodity_idx
    ON pfe.forecasts (commodity, generated_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Punkty prognozy (per dzień)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.forecast_points (
    point_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    forecast_id     UUID        NOT NULL REFERENCES pfe.forecasts ON DELETE CASCADE,
    forecast_date   DATE        NOT NULL,
    predicted       NUMERIC(16,6) NOT NULL,
    lower_80        NUMERIC(16,6),
    upper_80        NUMERIC(16,6),
    lower_95        NUMERIC(16,6),
    upper_95        NUMERIC(16,6),
    UNIQUE (forecast_id, forecast_date)
);

CREATE INDEX pfe_forecast_points_forecast_idx ON pfe.forecast_points (forecast_id);
CREATE INDEX pfe_forecast_points_date_idx
    ON pfe.forecast_points (forecast_date, forecast_id);

-- ─────────────────────────────────────────────────────────────
-- Ewaluacja — porównanie prognoza vs. rzeczywistość
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.forecast_evaluations (
    eval_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    forecast_id     UUID        NOT NULL REFERENCES pfe.forecasts,
    eval_date       DATE        NOT NULL,
    horizon_days    INT         NOT NULL,
    mae             NUMERIC(12,6),
    rmse            NUMERIC(12,6),
    mape            NUMERIC(8,6),
    coverage_80     NUMERIC(5,4),
    coverage_95     NUMERIC(5,4),
    n_points        INT,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- Drift detection
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.drift_reports (
    drift_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    commodity       TEXT        NOT NULL,
    model_type      pfe.forecast_model NOT NULL,
    check_date      DATE        NOT NULL,
    drift_status    pfe.drift_status NOT NULL,
    psi_score       NUMERIC(8,6),   -- Population Stability Index
    ks_statistic    NUMERIC(8,6),   -- Kolmogorov-Smirnov
    ks_p_value      NUMERIC(8,6),
    mape_7d         NUMERIC(8,6),   -- MAPE ostatnich 7 dni (performance drift)
    mape_30d        NUMERIC(8,6),
    mape_baseline   NUMERIC(8,6),   -- MAPE z okresu trenowania
    details         JSONB       NOT NULL DEFAULT '{}',
    retrain_triggered BOOLEAN   NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pfe_drift_commodity_idx ON pfe.drift_reports (commodity, check_date DESC);

-- ─────────────────────────────────────────────────────────────
-- Outbox Kafka
-- ─────────────────────────────────────────────────────────────

CREATE TABLE pfe.outbox_events (
    event_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           TEXT        NOT NULL,
    key             TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    published       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX pfe_outbox_unpublished_idx
    ON pfe.outbox_events (created_at) WHERE published = FALSE;

-- ─────────────────────────────────────────────────────────────
-- Triggery — outbox na nową prognozę
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION pfe.publish_forecast_ready()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'DONE' THEN
        INSERT INTO pfe.outbox_events (topic, key, payload)
        VALUES (
            'pfe.forecast.ready',
            NEW.forecast_id::TEXT,
            jsonb_build_object(
                'forecast_id',   NEW.forecast_id,
                'commodity',     NEW.commodity,
                'model_type',    NEW.model_type,
                'generated_at',  NEW.generated_at,
                'horizon_days',  NEW.horizon_days
            )
        );
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_forecast_ready
    AFTER INSERT OR UPDATE OF status ON pfe.forecasts
    FOR EACH ROW EXECUTE FUNCTION pfe.publish_forecast_ready();

-- ─────────────────────────────────────────────────────────────
-- Widoki
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW pfe.v_latest_forecasts AS
SELECT DISTINCT ON (f.commodity, f.model_type)
    f.forecast_id,
    f.commodity,
    f.model_type,
    f.generated_at,
    f.horizon_days,
    f.status,
    -- Najbliższy punkt prognozy
    fp.predicted  AS next_day_predicted,
    fp.lower_95   AS next_day_lower_95,
    fp.upper_95   AS next_day_upper_95,
    -- Ostatni znany backtest
    mr.backtest_mape,
    mr.coverage_95
FROM pfe.forecasts f
JOIN pfe.forecast_points fp
    ON fp.forecast_id = f.forecast_id
   AND fp.forecast_date = f.generated_at + 1
JOIN pfe.model_registry mr ON mr.model_registry_id = f.model_registry_id
WHERE f.status = 'DONE'
ORDER BY f.commodity, f.model_type, f.generated_at DESC;

CREATE OR REPLACE VIEW pfe.v_price_history AS
SELECT
    commodity,
    price_date,
    AVG(price_eur)        AS price_eur,
    MAX(quality_score)    AS quality_score,
    COUNT(*)              AS n_sources
FROM pfe.price_series
GROUP BY commodity, price_date
ORDER BY commodity, price_date;
```

### 5.2 TimescaleDB — opcjonalne rozszerzenie

```sql
-- Jeśli TimescaleDB dostępne — hypertable na price_series
-- SELECT create_hypertable('pfe.price_series', 'price_date',
--     chunk_time_interval => INTERVAL '3 months');

-- Continuous aggregate — miesięczne OHLC
-- CREATE MATERIALIZED VIEW pfe.mv_monthly_ohlc
-- WITH (timescaledb.continuous) AS
-- SELECT
--     commodity,
--     time_bucket('1 month', price_date::timestamptz) AS month,
--     FIRST(price_eur, price_date) AS open_eur,
--     MAX(price_eur)               AS high_eur,
--     MIN(price_eur)               AS low_eur,
--     LAST(price_eur, price_date)  AS close_eur
-- FROM pfe.price_series
-- GROUP BY commodity, month;
```

---

## 6. API

### 6.1 Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `PFE_VIEWER` | GET forecasts, price-series, commodities, scenarios |
| `PFE_ANALYST` | PFE_VIEWER + GET analytics, backtest results, drift reports |
| `PFE_ENGINEER` | PFE_ANALYST + POST retrain, POST generate-forecast |
| `PFE_ADMIN` | Wszystko + manage connectors, model-registry, DELETE |

### 6.2 Endpointy

```
GET  /api/v1/pfe/commodities                         Lista surowców/indeksów
GET  /api/v1/pfe/commodities/{commodity}/price-series  Historia cen (range, freq)
GET  /api/v1/pfe/commodities/{commodity}/latest-price  Ostatnia dostępna cena

GET  /api/v1/pfe/forecasts                           Lista prognoz (filter: commodity, model)
POST /api/v1/pfe/forecasts/generate                  Wyzwól generowanie prognozy
GET  /api/v1/pfe/forecasts/{forecast_id}             Szczegóły prognozy
GET  /api/v1/pfe/forecasts/{forecast_id}/points      Punkty prognozy (paginacja)
GET  /api/v1/pfe/forecasts/{forecast_id}/evaluation  Ewaluacja vs. rzeczywistość

GET  /api/v1/pfe/commodities/{commodity}/forecast    Najnowsza prognoza dla surowca
GET  /api/v1/pfe/commodities/{commodity}/forecast/compare  Porównanie SARIMA / Prophet / LSTM

GET  /api/v1/pfe/scenarios                           Scenariusze (baseline/bull/bear)
POST /api/v1/pfe/scenarios                           Stwórz własny scenariusz makro

GET  /api/v1/pfe/models                              Model registry
POST /api/v1/pfe/models/{model_type}/{commodity}/retrain  Wyzwól retrain
GET  /api/v1/pfe/models/{model_registry_id}/backtest       Backtest metrics

GET  /api/v1/pfe/drift                               Lista drift reportów
GET  /api/v1/pfe/drift/{commodity}                   Drift status dla surowca

GET  /api/v1/pfe/analytics/correlation-matrix        Macierz korelacji cen
GET  /api/v1/pfe/analytics/volatility                Zmienność historyczna per commodity
GET  /api/v1/pfe/analytics/sensitivity/{commodity}   Analiza wrażliwości na sygnały makro

GET  /api/v1/pfe/admin/ingest-status                 Status konektorów danych
POST /api/v1/pfe/admin/ingest/trigger                Manualne pobranie danych
```

### 6.3 Przykładowe żądania/odpowiedzi

```http
POST /api/v1/pfe/forecasts/generate
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "commodity": "STEEL_HRC",
  "model_type": "ENSEMBLE",
  "horizon_days": 90,
  "use_external_signals": true
}
```

```json
HTTP/1.1 202 Accepted

{
  "forecast_id": "a1b2c3d4-...",
  "commodity": "STEEL_HRC",
  "model_type": "ENSEMBLE",
  "status": "RUNNING",
  "estimated_duration_s": 45,
  "webhook_topic": "pfe.forecast.ready"
}
```

```http
GET /api/v1/pfe/commodities/STEEL_HRC/forecast?horizon_days=30
```

```json
{
  "forecast_id": "a1b2c3d4-...",
  "commodity": "STEEL_HRC",
  "model_type": "ENSEMBLE",
  "generated_at": "2025-06-20",
  "horizon_days": 30,
  "currency": "EUR",
  "unit": "t",
  "current_price_eur": 680.50,
  "ensemble_weights": {
    "SARIMA":  0.28,
    "PROPHET": 0.35,
    "LSTM":    0.37
  },
  "backtest_mape": 0.038,
  "coverage_95": 0.923,
  "points": [
    {
      "forecast_date": "2025-06-21",
      "predicted": 683.20,
      "lower_80":  658.40,
      "upper_80":  708.00,
      "lower_95":  641.10,
      "upper_95":  725.30
    },
    {
      "forecast_date": "2025-07-20",
      "predicted": 712.80,
      "lower_80":  665.50,
      "upper_80":  760.10,
      "lower_95":  638.20,
      "upper_95":  787.40
    }
  ],
  "summary": {
    "30d_change_pct": 4.75,
    "30d_high_95":    787.40,
    "30d_low_95":     638.20,
    "trend":          "RISING"
  }
}
```

### 6.4 Scenarios endpoint

```http
POST /api/v1/pfe/scenarios
Content-Type: application/json

{
  "commodity": "STEEL_HRC",
  "horizon_days": 90,
  "scenarios": [
    {
      "name": "baseline",
      "macro_overrides": {}
    },
    {
      "name": "recession",
      "macro_overrides": {
        "pmi_de": 42.0,
        "pmi_cn": 44.0,
        "eurusd": 1.15,
        "bdi":    850
      }
    },
    {
      "name": "supply_shock",
      "macro_overrides": {
        "capacity_util_steel": 95.0,
        "bdi": 3200,
        "nat_gas_eur": 85.0
      }
    }
  ]
}
```

```json
{
  "commodity": "STEEL_HRC",
  "horizon_days": 90,
  "scenarios": {
    "baseline":      {"price_90d_eur": 712.80, "change_pct": 4.75},
    "recession":     {"price_90d_eur": 591.20, "change_pct": -13.1},
    "supply_shock":  {"price_90d_eur": 834.60, "change_pct": 22.6}
  }
}
```

### 6.5 FastAPI implementation sketch

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from uuid import UUID

router = APIRouter(prefix="/api/v1/pfe", tags=["price-forecasting-engine"])

@router.post("/forecasts/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=ForecastJobResponse)
async def generate_forecast(
    req:      GenerateForecastRequest,
    tasks:    BackgroundTasks,
    engine:   "ForecastingEngine" = Depends(get_forecasting_engine),
    db:       AsyncpgPool         = Depends(get_db),
    user:     TokenPayload        = Depends(require_role("PFE_ENGINEER")),
) -> ForecastJobResponse:
    fc_id = await db.create_forecast_job(
        commodity=req.commodity,
        model_type=req.model_type,
        horizon=req.horizon_days,
        created_by=user.sub,
    )
    tasks.add_task(engine.run_forecast, fc_id, req)
    return ForecastJobResponse(
        forecast_id=fc_id, status="RUNNING",
        estimated_duration_s=engine.estimate_duration(req),
    )

@router.get("/commodities/{commodity}/forecast",
            response_model=ForecastResponse)
async def get_latest_forecast(
    commodity:    str,
    horizon_days: int = 30,
    model_type:   str = "ENSEMBLE",
    db:           AsyncpgPool = Depends(get_db),
    user:         TokenPayload = Depends(require_role("PFE_VIEWER")),
) -> ForecastResponse:
    row = await db.get_latest_forecast(commodity, model_type, horizon_days)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No forecast found for {commodity}")
    return ForecastResponse.from_db(row)

@router.get("/analytics/correlation-matrix",
            response_model=CorrelationMatrixResponse)
async def correlation_matrix(
    commodities: list[str],
    lookback_days: int = 365,
    db:  AsyncpgPool = Depends(get_db),
    user: TokenPayload = Depends(require_role("PFE_ANALYST")),
) -> CorrelationMatrixResponse:
    series_map = await db.get_price_series_multi(commodities, lookback_days)
    df         = pd.DataFrame(series_map)
    corr       = df.corr(method="spearman").round(4).to_dict()
    return CorrelationMatrixResponse(matrix=corr, lookback_days=lookback_days)
```
