# Price Forecasting Engine — Sections 1–3

## 1. Data Sources

### 1.1 Taksonomia źródeł danych

System integruje 4 klasy źródeł: ceny rynkowe, dane wewnętrzne, sygnały makro i
dane alternatywne.

```
Price Forecasting Engine — źródła danych
│
├── MARKET_PRICES
│   ├── LME (London Metal Exchange)    — Al, Cu, Ni, Zn, Pb, Sn (daily OHLC)
│   ├── CME (Chicago Mercantile Exc.)  — HRC Steel, CRC Steel, FeScrap futures
│   ├── Platts / S&P Global            — European HRC, CRC, HDG (assessed prices)
│   ├── MEPS International             — regional steel benchmarks (DE/PL/CN)
│   ├── EEX (European Energy Exchange) — DE Baseload electricity, TTF gas
│   ├── ICE (Intercontinental Exc.)    — Brent crude, nat. gas
│   └── ECB / NBP / NBÏ               — EUR/USD/CNY/PLN FX rates (daily)
│
├── INTERNAL_DATA
│   ├── SOP (Supplier Offer Parser)    — historyczne ceny z ofert dostawców
│   ├── CBE (Cost Breakdown Engine)    — zatwierdzone unit_cost_eur per material
│   └── Procurement ERP               — faktyczne ceny zakupów (invoice prices)
│
├── MACRO_SIGNALS
│   ├── Eurostat / OECD               — HICP (harmonized CPI), PPI metals/energy
│   ├── PMI (IHS Markit)              — manufacturing PMI DE, CN, US (monthly)
│   ├── Baltic Dry Index (BDI)        — koszty transportu morskiego
│   └── World Steel Association       — capacity utilization, global output
│
└── ALTERNATIVE_DATA
    ├── Google Trends                  — "steel price", "aluminum scarcity"
    ├── News sentiment                 — NLP na newsy surowcowe (Reuters, Bloomberg)
    └── Weather (NOAA)                 — temperatura → energochłonność hutnictwa
```

### 1.2 DataSourceConnector — abstrakcja

```python
from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import AsyncIterator

class DataSourceType(str, Enum):
    MARKET_PRICE  = "MARKET_PRICE"
    INTERNAL      = "INTERNAL"
    MACRO         = "MACRO"
    ALTERNATIVE   = "ALTERNATIVE"

class PriceFrequency(str, Enum):
    TICK      = "TICK"      # intraday
    DAILY     = "DAILY"
    WEEKLY    = "WEEKLY"
    MONTHLY   = "MONTHLY"

@dataclass
class PriceRecord:
    source_id:    str
    commodity:    str          # "STEEL_HRC", "ALUMINUM_LME", "ELECTRICITY_DE"
    price_date:   date
    price:        Decimal
    currency:     str          = "EUR"
    unit:         str          = "t"   # t / MWh / MBtu
    frequency:    PriceFrequency = PriceFrequency.DAILY
    open_:        Decimal | None = None
    high:         Decimal | None = None
    low:          Decimal | None = None
    volume:       Decimal | None = None
    quality_score: float        = 1.0   # 0–1 data quality flag

class DataSourceConnector(ABC):
    source_id:   str
    source_type: DataSourceType
    frequency:   PriceFrequency

    @abstractmethod
    async def fetch_latest(self) -> list[PriceRecord]: ...

    @abstractmethod
    async def fetch_range(
        self, start: date, end: date
    ) -> list[PriceRecord]: ...

    @abstractmethod
    async def stream(self) -> AsyncIterator[PriceRecord]: ...
```

### 1.3 Konektory rynkowe

```python
import aiohttp
from datetime import date, timedelta

class LMEConnector(DataSourceConnector):
    """London Metal Exchange — ceny settlement przez LME REST API."""
    source_id   = "LME"
    source_type = DataSourceType.MARKET_PRICE
    frequency   = PriceFrequency.DAILY

    COMMODITIES = {
        "ALUMINUM_LME": "AH",   # Aluminium 3M
        "COPPER_LME":   "CA",
        "NICKEL_LME":   "NI",
        "ZINC_LME":     "ZS",
    }

    def __init__(self, api_key: str, base_url: str):
        self._api_key  = api_key
        self._base_url = base_url

    async def fetch_range(self, start: date, end: date) -> list[PriceRecord]:
        records = []
        async with aiohttp.ClientSession() as session:
            for commodity, code in self.COMMODITIES.items():
                url = (f"{self._base_url}/metals/{code}/prices"
                       f"?from={start.isoformat()}&to={end.isoformat()}")
                async with session.get(url, headers={"X-API-Key": self._api_key}) as r:
                    r.raise_for_status()
                    data = await r.json()
                for row in data["prices"]:
                    records.append(PriceRecord(
                        source_id=self.source_id,
                        commodity=commodity,
                        price_date=date.fromisoformat(row["date"]),
                        price=Decimal(str(row["settlement"])),
                        currency="USD",
                        unit="t",
                    ))
        return records

class EEXConnector(DataSourceConnector):
    """European Energy Exchange — energia elektryczna DE Baseload."""
    source_id   = "EEX"
    source_type = DataSourceType.MARKET_PRICE
    frequency   = PriceFrequency.DAILY

    COMMODITIES = {
        "ELECTRICITY_DE": "PHELIX_DAY_BASE",
        "GAS_TTF":        "TTF_SPOT",
    }

    async def fetch_range(self, start: date, end: date) -> list[PriceRecord]:
        records = []
        async with aiohttp.ClientSession() as session:
            for commodity, product in self.COMMODITIES.items():
                url = (f"{self._base_url}/spot/{product}"
                       f"?start={start}&end={end}&format=json")
                async with session.get(url, headers={"Authorization": f"Bearer {self._token}"}) as r:
                    data = await r.json()
                for row in data:
                    records.append(PriceRecord(
                        source_id=self.source_id,
                        commodity=commodity,
                        price_date=date.fromisoformat(row["delivery_date"]),
                        price=Decimal(str(row["price_eur_mwh"])),
                        currency="EUR",
                        unit="MWh",
                    ))
        return records

class SOPInternalConnector(DataSourceConnector):
    """Wewnętrzny: ceny z Supplier Offer Parser."""
    source_id   = "SOP_INTERNAL"
    source_type = DataSourceType.INTERNAL
    frequency   = PriceFrequency.DAILY

    def __init__(self, db: "AsyncpgPool"):
        self._db = db

    async def fetch_range(self, start: date, end: date) -> list[PriceRecord]:
        rows = await self._db.fetch("""
            SELECT
                oli.material_designation AS commodity,
                DATE(oli.created_at)     AS price_date,
                AVG(oli.unit_price_eur)  AS price,
                'EUR'                    AS currency,
                oli.unit_si              AS unit,
                COUNT(*)                 AS n
            FROM sop.offer_line_items oli
            WHERE oli.unit_price_eur IS NOT NULL
              AND DATE(oli.created_at) BETWEEN $1 AND $2
            GROUP BY 1, 2, 4, 5
            HAVING COUNT(*) >= 3   -- min 3 oferty dla wiarygodności
        """, start, end)
        return [PriceRecord(
            source_id=self.source_id,
            commodity=row["commodity"],
            price_date=row["price_date"],
            price=row["price"],
            currency=row["currency"],
            unit=row["unit"],
            quality_score=min(1.0, row["n"] / 10),
        ) for row in rows]

class EurostatMacroConnector(DataSourceConnector):
    """Eurostat SDMX-JSON — PPI metale, HICP."""
    source_id   = "EUROSTAT"
    source_type = DataSourceType.MACRO
    frequency   = PriceFrequency.MONTHLY

    DATASETS = {
        "PPI_METALS_DE":   "sts_inppd_m",   # PPI — metale DE
        "PPI_ENERGY_DE":   "sts_inppd_m",   # PPI — energia DE
        "HICP_EU":         "prc_hicp_midx",
    }

    async def fetch_range(self, start: date, end: date) -> list[PriceRecord]:
        records = []
        async with aiohttp.ClientSession() as session:
            url = (f"https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"
                   f"/sts_inppd_m/M.I15.METALS.DE"
                   f"?startPeriod={start.strftime('%Y-%m')}"
                   f"&endPeriod={end.strftime('%Y-%m')}&format=JSON")
            async with session.get(url) as r:
                data = await r.json()
            for period, obs in data["dataSets"][0]["series"]["0:0:0:0"]["observations"].items():
                idx   = int(period)
                label = data["structure"]["dimensions"]["observation"][0]["values"][idx]["id"]
                y, m  = label[:4], label[5:7]
                records.append(PriceRecord(
                    source_id=self.source_id,
                    commodity="PPI_METALS_DE",
                    price_date=date(int(y), int(m), 1),
                    price=Decimal(str(obs[0])),
                    currency="INDEX",
                    unit="index_2015=100",
                    frequency=PriceFrequency.MONTHLY,
                ))
        return records
```

### 1.4 DataIngestionPipeline

```python
import asyncio
from datetime import date, timedelta
from typing import Callable

class DataIngestionPipeline:
    """Orkiestrator pobierania i zapisu danych cenowych."""

    def __init__(
        self,
        connectors: list[DataSourceConnector],
        repo:       "PriceRepository",
        fx_svc:     "FXNormalizer",
        validator:  "PriceDataValidator",
    ):
        self._connectors = connectors
        self._repo       = repo
        self._fx         = fx_svc
        self._validator  = validator

    async def ingest_daily(self) -> IngestReport:
        end   = date.today()
        start = end - timedelta(days=7)   # nadmiarowy lookback — idempotentny UPSERT
        results = await asyncio.gather(
            *[self._ingest_one(c, start, end) for c in self._connectors],
            return_exceptions=True,
        )
        return IngestReport(results)

    async def _ingest_one(
        self,
        connector: DataSourceConnector,
        start: date,
        end: date,
    ) -> int:
        records = await connector.fetch_range(start, end)
        # Normalizuj do EUR
        normalized = [await self._fx.to_eur(r) for r in records]
        # Walidacja
        valid = [r for r in normalized if self._validator.is_valid(r)]
        # UPSERT
        await self._repo.upsert_many(valid)
        return len(valid)
```

### 1.5 PriceDataValidator

```python
from decimal import Decimal

class PriceDataValidator:
    MAX_DAILY_CHANGE_PCT = 0.30   # > 30% zmiany dziennej → suspect
    MIN_PRICE            = Decimal("0.01")
    MAX_PRICE_EUR_T      = Decimal("500_000")  # > 500k EUR/t → anomalia

    def is_valid(self, r: PriceRecord) -> bool:
        if r.price <= self.MIN_PRICE:
            return False
        if r.currency == "EUR" and r.unit == "t" and r.price > self.MAX_PRICE_EUR_T:
            return False
        if r.quality_score < 0.30:
            return False
        return True

    def check_continuity(
        self, series: list[PriceRecord]
    ) -> list[str]:
        """Wykryj luki w szeregu (brakujące dni robocze)."""
        issues: list[str] = []
        sorted_s = sorted(series, key=lambda r: r.price_date)
        for i in range(1, len(sorted_s)):
            gap = (sorted_s[i].price_date - sorted_s[i - 1].price_date).days
            if gap > 5:  # tydzień → podejrzane
                issues.append(
                    f"Gap {gap}d in {sorted_s[i].commodity} "
                    f"between {sorted_s[i-1].price_date} and {sorted_s[i].price_date}"
                )
        return issues
```

---

## 2. Time Series Models

### 2.1 Model registry — ForecastModel ABC

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import numpy as np

@dataclass
class ForecastPoint:
    forecast_date: date
    predicted:     float
    lower_80:      float
    upper_80:      float
    lower_95:      float
    upper_95:      float

@dataclass
class ForecastResult:
    model_id:     str
    commodity:    str
    horizon_days: int
    currency:     str
    unit:         str
    generated_at: date
    points:       list[ForecastPoint]
    mae:          float | None = None   # in-sample / backtest
    rmse:         float | None = None
    mape:         float | None = None
    coverage_80:  float | None = None   # empirical coverage
    coverage_95:  float | None = None
    metadata:     dict        = field(default_factory=dict)

class ForecastModel(ABC):
    model_id:   str
    commodity:  str

    @abstractmethod
    def fit(self, series: np.ndarray, dates: list[date], **kwargs) -> None: ...

    @abstractmethod
    def predict(self, horizon: int) -> ForecastResult: ...

    @abstractmethod
    def backtest(
        self,
        series: np.ndarray,
        dates: list[date],
        n_splits: int = 5,
    ) -> dict[str, float]: ...
```

### 2.2 ARIMA / SARIMA

```python
import warnings
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import adfuller
import numpy as np
import pandas as pd
from datetime import date

class ARIMAModel(ForecastModel):
    """
    Auto-ARIMA z grid search po (p,d,q)(P,D,Q,s).
    Domyślnie SARIMA(1,1,1)(1,1,0,52) dla tygodniowych danych.
    """
    model_id = "SARIMA"

    def __init__(
        self,
        commodity: str,
        order: tuple[int, int, int]         = (1, 1, 1),
        seasonal_order: tuple[int, int, int, int] = (1, 1, 0, 52),
        auto_order: bool                    = True,
    ):
        self.commodity      = commodity
        self._order         = order
        self._seasonal_order = seasonal_order
        self._auto          = auto_order
        self._model         = None
        self._result        = None
        self._last_dates:   list[date] = []
        self._fitted_values: np.ndarray | None = None

    def fit(self, series: np.ndarray, dates: list[date], **kwargs) -> None:
        self._last_dates = dates
        log_series = np.log(series + 1e-9)  # log-transform → stabilizacja wariancji

        if self._auto:
            self._order, self._seasonal_order = self._auto_order(log_series)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model  = SARIMAX(
                log_series,
                order=self._order,
                seasonal_order=self._seasonal_order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            self._result = self._model.fit(disp=False, maxiter=200)
        self._fitted_values = np.exp(self._result.fittedvalues)

    def predict(self, horizon: int) -> ForecastResult:
        fc = self._result.get_forecast(steps=horizon)
        mean  = np.exp(fc.predicted_mean.values)
        ci_80 = np.exp(fc.conf_int(alpha=0.20).values)
        ci_95 = np.exp(fc.conf_int(alpha=0.05).values)

        last_date = self._last_dates[-1]
        from datetime import timedelta
        points = []
        for i in range(horizon):
            d = last_date + timedelta(days=i + 1)
            points.append(ForecastPoint(
                forecast_date=d,
                predicted=float(mean[i]),
                lower_80=float(ci_80[i, 0]),
                upper_80=float(ci_80[i, 1]),
                lower_95=float(ci_95[i, 0]),
                upper_95=float(ci_95[i, 1]),
            ))
        return ForecastResult(
            model_id=self.model_id,
            commodity=self.commodity,
            horizon_days=horizon,
            currency="EUR", unit="t",
            generated_at=date.today(),
            points=points,
            metadata={
                "order":         self._order,
                "seasonal_order": self._seasonal_order,
                "aic":           self._result.aic,
                "bic":           self._result.bic,
            },
        )

    def backtest(
        self,
        series: np.ndarray,
        dates: list[date],
        n_splits: int = 5,
    ) -> dict[str, float]:
        from sklearn.model_selection import TimeSeriesSplit
        tscv    = TimeSeriesSplit(n_splits=n_splits, test_size=30)
        maes, rmses, mapes = [], [], []
        for train_idx, test_idx in tscv.split(series):
            self.fit(series[train_idx], [dates[i] for i in train_idx])
            fc = self.predict(len(test_idx))
            pred   = np.array([p.predicted for p in fc.points[:len(test_idx)]])
            actual = series[test_idx]
            maes.append(np.mean(np.abs(pred - actual)))
            rmses.append(np.sqrt(np.mean((pred - actual) ** 2)))
            mask = actual != 0
            mapes.append(np.mean(np.abs((pred[mask] - actual[mask]) / actual[mask])))
        return {
            "mae":  float(np.mean(maes)),
            "rmse": float(np.mean(rmses)),
            "mape": float(np.mean(mapes)),
        }

    def _auto_order(
        self, series: np.ndarray
    ) -> tuple[tuple, tuple]:
        """Prosty grid search po (p,d,q) minimalizujący AIC."""
        best_aic   = np.inf
        best_order = (1, 1, 1)
        d = 1 if adfuller(series)[1] > 0.05 else 0
        for p in range(0, 4):
            for q in range(0, 4):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        m = SARIMAX(series, order=(p, d, q)).fit(disp=False)
                    if m.aic < best_aic:
                        best_aic   = m.aic
                        best_order = (p, d, q)
                except Exception:
                    continue
        return best_order, (1, 1, 0, 52)
```

### 2.3 Prophet

```python
from prophet import Prophet
import pandas as pd
import numpy as np
from datetime import date, timedelta

class ProphetModel(ForecastModel):
    """
    Facebook/Meta Prophet — automatyczna detekcja sezonowości rocznej,
    changepoints i regresory zewnętrzne (inflation, FX, PMI).
    """
    model_id = "PROPHET"

    def __init__(
        self,
        commodity: str,
        changepoint_prior_scale: float = 0.15,
        seasonality_prior_scale: float = 10.0,
        yearly_seasonality: bool       = True,
        weekly_seasonality: bool       = False,  # metale — nie ma wzorca tygodniowego
        regressors: list[str]          = None,
    ):
        self.commodity  = commodity
        self._cp_scale  = changepoint_prior_scale
        self._sp_scale  = seasonality_prior_scale
        self._regressors = regressors or []
        self._model:     Prophet | None = None
        self._last_df:   pd.DataFrame | None = None
        self._yearly     = yearly_seasonality
        self._weekly     = weekly_seasonality

    def fit(
        self,
        series: np.ndarray,
        dates: list[date],
        regressor_df: pd.DataFrame | None = None,
        **kwargs,
    ) -> None:
        df = pd.DataFrame({"ds": pd.to_datetime(dates), "y": series})

        self._model = Prophet(
            changepoint_prior_scale=self._cp_scale,
            seasonality_prior_scale=self._sp_scale,
            yearly_seasonality=self._yearly,
            weekly_seasonality=self._weekly,
            daily_seasonality=False,
            uncertainty_samples=1000,
        )

        if regressor_df is not None:
            for col in self._regressors:
                if col in regressor_df.columns:
                    self._model.add_regressor(col, standardize=True)
            df = df.merge(regressor_df[["ds"] + self._regressors], on="ds", how="left")
            df[self._regressors] = df[self._regressors].fillna(method="ffill")

        self._model.fit(df)
        self._last_df = df

    def predict(
        self,
        horizon: int,
        future_regressors: pd.DataFrame | None = None,
    ) -> ForecastResult:
        future = self._model.make_future_dataframe(periods=horizon, freq="D")

        if future_regressors is not None:
            future = future.merge(
                future_regressors[["ds"] + self._regressors], on="ds", how="left"
            ).fillna(method="ffill")

        fc = self._model.predict(future)
        fc_tail = fc.tail(horizon)

        points = []
        for _, row in fc_tail.iterrows():
            points.append(ForecastPoint(
                forecast_date=row["ds"].date(),
                predicted=max(0.0, float(row["yhat"])),
                lower_80=max(0.0, float(row.get("yhat_lower", row["yhat"] * 0.88))),
                upper_80=max(0.0, float(row.get("yhat_upper", row["yhat"] * 1.12))),
                lower_95=max(0.0, float(row.get("yhat_lower", row["yhat"] * 0.80))),
                upper_95=max(0.0, float(row.get("yhat_upper", row["yhat"] * 1.20))),
            ))
        return ForecastResult(
            model_id=self.model_id,
            commodity=self.commodity,
            horizon_days=horizon,
            currency="EUR", unit="t",
            generated_at=date.today(),
            points=points,
            metadata={
                "changepoint_prior_scale": self._cp_scale,
                "regressors": self._regressors,
            },
        )

    def backtest(
        self,
        series: np.ndarray,
        dates: list[date],
        n_splits: int = 5,
    ) -> dict[str, float]:
        from sklearn.model_selection import TimeSeriesSplit
        tscv = TimeSeriesSplit(n_splits=n_splits, test_size=30)
        maes, rmses, mapes = [], [], []
        for train_idx, test_idx in tscv.split(series):
            self.fit(series[train_idx], [dates[i] for i in train_idx])
            fc = self.predict(len(test_idx))
            pred   = np.array([p.predicted for p in fc.points[:len(test_idx)]])
            actual = series[test_idx]
            maes.append(np.mean(np.abs(pred - actual)))
            rmses.append(np.sqrt(np.mean((pred - actual) ** 2)))
            mask = actual != 0
            mapes.append(np.mean(np.abs((pred[mask] - actual[mask]) / actual[mask])))
        return {"mae": float(np.mean(maes)), "rmse": float(np.mean(rmses)),
                "mape": float(np.mean(mapes))}
```

### 2.4 LSTM

```python
import torch
import torch.nn as nn
import numpy as np
from datetime import date, timedelta
from sklearn.preprocessing import MinMaxScaler

class LSTMPriceNet(nn.Module):
    def __init__(
        self,
        input_size:  int   = 16,   # features
        hidden_size: int   = 128,
        num_layers:  int   = 2,
        dropout:     float = 0.20,
        horizon:     int   = 30,
    ):
        super().__init__()
        self.horizon = horizon
        self.lstm    = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            batch_first=True,
        )
        self.attn    = nn.MultiheadAttention(
            embed_dim=hidden_size, num_heads=4, batch_first=True
        )
        self.fc_mean = nn.Linear(hidden_size, horizon)
        self.fc_std  = nn.Sequential(
            nn.Linear(hidden_size, horizon),
            nn.Softplus(),   # σ > 0
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """x: (batch, seq_len, input_size) → mean, std (batch, horizon)."""
        lstm_out, _ = self.lstm(x)
        attn_out, _ = self.attn(lstm_out, lstm_out, lstm_out)
        context     = attn_out[:, -1, :]
        return self.fc_mean(context), self.fc_std(context)


class LSTMModel(ForecastModel):
    """
    LSTM z attention + probabilistic output (Gaussian NLL loss).
    Lookback window = 90 dni; horizon = 30 dni.
    """
    model_id = "LSTM"

    LOOKBACK   = 90
    EPOCHS     = 100
    BATCH_SIZE = 32
    LR         = 1e-3
    DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"

    def __init__(
        self,
        commodity:   str,
        horizon:     int  = 30,
        hidden_size: int  = 128,
        num_layers:  int  = 2,
        input_size:  int  = 16,
    ):
        self.commodity   = commodity
        self._horizon    = horizon
        self._scaler     = MinMaxScaler()
        self._net        = LSTMPriceNet(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            horizon=horizon,
        ).to(self.DEVICE)
        self._last_window: np.ndarray | None = None
        self._last_dates:  list[date]        = []

    def fit(
        self,
        series: np.ndarray,
        dates: list[date],
        feature_matrix: np.ndarray | None = None,
        **kwargs,
    ) -> None:
        self._last_dates = dates
        # Łączymy cenę + features
        X_raw = (
            np.column_stack([series, feature_matrix])
            if feature_matrix is not None
            else series.reshape(-1, 1)
        )
        X_scaled = self._scaler.fit_transform(X_raw)

        # Budujemy okna (X, y)
        X_wins, y_wins = [], []
        for i in range(self.LOOKBACK, len(X_scaled) - self._horizon + 1):
            X_wins.append(X_scaled[i - self.LOOKBACK : i])
            y_wins.append(X_scaled[i : i + self._horizon, 0])  # tylko cena

        X_t = torch.tensor(np.array(X_wins), dtype=torch.float32).to(self.DEVICE)
        y_t = torch.tensor(np.array(y_wins), dtype=torch.float32).to(self.DEVICE)

        optimizer = torch.optim.AdamW(self._net.parameters(), lr=self.LR,
                                      weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=self.EPOCHS)

        dataset    = torch.utils.data.TensorDataset(X_t, y_t)
        dataloader = torch.utils.data.DataLoader(
            dataset, batch_size=self.BATCH_SIZE, shuffle=True)

        self._net.train()
        for epoch in range(self.EPOCHS):
            for xb, yb in dataloader:
                optimizer.zero_grad()
                mu, sigma = self._net(xb)
                # Gaussian NLL loss
                loss = nn.GaussianNLLLoss()(mu, yb, sigma ** 2)
                loss.backward()
                nn.utils.clip_grad_norm_(self._net.parameters(), 1.0)
                optimizer.step()
            scheduler.step()

        # Zapamiętaj ostatnie okno
        self._last_window = X_scaled[-self.LOOKBACK:]
        self._net.eval()

    def predict(self, horizon: int | None = None) -> ForecastResult:
        h = horizon or self._horizon
        x = torch.tensor(
            self._last_window[np.newaxis, :, :], dtype=torch.float32
        ).to(self.DEVICE)

        with torch.no_grad():
            mu, sigma = self._net(x)

        mu_np    = mu.cpu().numpy()[0, :h]
        sigma_np = sigma.cpu().numpy()[0, :h]

        # Odwróć skalowanie (tylko kolumna ceny)
        dummy = np.zeros((h, self._scaler.n_features_in_))
        dummy[:, 0] = mu_np
        mu_orig = self._scaler.inverse_transform(dummy)[:, 0]

        dummy[:, 0] = sigma_np
        s_orig = self._scaler.inverse_transform(dummy)[:, 0]

        z80, z95 = 1.282, 1.960
        last_date = self._last_dates[-1]
        points = []
        for i in range(h):
            d = last_date + timedelta(days=i + 1)
            points.append(ForecastPoint(
                forecast_date=d,
                predicted=float(mu_orig[i]),
                lower_80=float(mu_orig[i] - z80 * s_orig[i]),
                upper_80=float(mu_orig[i] + z80 * s_orig[i]),
                lower_95=float(mu_orig[i] - z95 * s_orig[i]),
                upper_95=float(mu_orig[i] + z95 * s_orig[i]),
            ))
        return ForecastResult(
            model_id=self.model_id,
            commodity=self.commodity,
            horizon_days=h,
            currency="EUR", unit="t",
            generated_at=date.today(),
            points=points,
            metadata={"lookback": self.LOOKBACK, "hidden": self._net.lstm.hidden_size},
        )

    def backtest(
        self,
        series: np.ndarray,
        dates: list[date],
        n_splits: int = 5,
    ) -> dict[str, float]:
        from sklearn.model_selection import TimeSeriesSplit
        tscv = TimeSeriesSplit(n_splits=n_splits, test_size=self._horizon)
        maes, rmses, mapes = [], [], []
        for train_idx, test_idx in tscv.split(series):
            self.fit(series[train_idx], [dates[i] for i in train_idx])
            fc = self.predict(len(test_idx))
            pred   = np.array([p.predicted for p in fc.points[:len(test_idx)]])
            actual = series[test_idx]
            maes.append(np.mean(np.abs(pred - actual)))
            rmses.append(np.sqrt(np.mean((pred - actual) ** 2)))
            mask = actual != 0
            mapes.append(np.mean(np.abs((pred[mask] - actual[mask]) / actual[mask])))
        return {"mae": float(np.mean(maes)), "rmse": float(np.mean(rmses)),
                "mape": float(np.mean(mapes))}
```

### 2.5 Ensemble — ModelEnsemble

```python
class EnsembleMethod(str, Enum):
    EQUAL_WEIGHT    = "EQUAL_WEIGHT"
    INVERSE_RMSE    = "INVERSE_RMSE"   # wagi = 1/RMSE
    STACKING        = "STACKING"       # meta-learner (Ridge)

class ModelEnsemble:
    """
    Łączy SARIMA + Prophet + LSTM. Domyślnie INVERSE_RMSE weighting.
    Produkuje wspólne przedziały ufności przez kwantyle Monte Carlo.
    """
    model_id = "ENSEMBLE"

    def __init__(
        self,
        models:  list[ForecastModel],
        method:  EnsembleMethod = EnsembleMethod.INVERSE_RMSE,
    ):
        self._models  = models
        self._method  = method
        self._weights: list[float] = []

    def fit(
        self,
        series: np.ndarray,
        dates: list[date],
        **kwargs,
    ) -> None:
        metrics_list = []
        for m in self._models:
            m.fit(series, dates, **kwargs)
            bt = m.backtest(series, dates, n_splits=5)
            metrics_list.append(bt)

        if self._method == EnsembleMethod.INVERSE_RMSE:
            rmses = [max(bt["rmse"], 1e-9) for bt in metrics_list]
            inv   = [1.0 / r for r in rmses]
            total = sum(inv)
            self._weights = [i / total for i in inv]
        else:
            n = len(self._models)
            self._weights = [1.0 / n] * n

    def predict(self, horizon: int) -> ForecastResult:
        results = [m.predict(horizon) for m in self._models]

        # Ważona średnia predicted
        combined_points = []
        for i in range(horizon):
            w_pred  = sum(w * r.points[i].predicted  for w, r in zip(self._weights, results))
            w_lo80  = sum(w * r.points[i].lower_80   for w, r in zip(self._weights, results))
            w_hi80  = sum(w * r.points[i].upper_80   for w, r in zip(self._weights, results))
            w_lo95  = sum(w * r.points[i].lower_95   for w, r in zip(self._weights, results))
            w_hi95  = sum(w * r.points[i].upper_95   for w, r in zip(self._weights, results))
            combined_points.append(ForecastPoint(
                forecast_date=results[0].points[i].forecast_date,
                predicted=w_pred,
                lower_80=w_lo80, upper_80=w_hi80,
                lower_95=w_lo95, upper_95=w_hi95,
            ))
        return ForecastResult(
            model_id=self.model_id,
            commodity=results[0].commodity,
            horizon_days=horizon,
            currency="EUR", unit="t",
            generated_at=date.today(),
            points=combined_points,
            metadata={
                "method": self._method,
                "weights": {m.model_id: w
                            for m, w in zip(self._models, self._weights)},
            },
        )
```

### 2.6 Modele per klasa cenowa

| Klasa | Commodity | Rekomendowany model | Horizon |
|-------|-----------|--------------------:|:-------:|
| Stal | STEEL_HRC, STEEL_CRC, STEEL_SCRAP | ENSEMBLE | 30/90 dni |
| Aluminium | ALUMINUM_LME, ALUMINUM_ALLOY | Prophet + LSTM | 30/90 dni |
| Energia elektryczna | ELECTRICITY_DE | SARIMA (sezonowość) | 7/30 dni |
| Gaz ziemny | GAS_TTF | SARIMA + Prophet | 7/30 dni |
| Usługi produkcyjne | MACHINING_RATE_DE, MACHINING_RATE_PL | Prophet (trend) | 90/180 dni |
| Indeksy makro | PPI_METALS_DE, HICP_EU | ARIMA | 30/90 dni |

---

## 3. Feature Engineering

### 3.1 FeatureEngineeringPipeline

```python
import numpy as np
import pandas as pd
from dataclasses import dataclass

@dataclass
class FeatureMatrix:
    dates:    list[date]
    features: np.ndarray          # (n_days, n_features)
    names:    list[str]

class FeatureEngineeringPipeline:
    """Buduje macierz cech dla LSTM i Prophet regressors."""

    def build(
        self,
        price_series:  pd.Series,         # index=date, values=EUR price
        external_df:   pd.DataFrame,      # makro + FX + PMI
    ) -> FeatureMatrix:
        df = pd.DataFrame({"price": price_series})
        df = df.join(external_df, how="left").sort_index().ffill().bfill()

        # ── Lag features ─────────────────────────────────────────────
        for lag in [1, 5, 10, 20, 30, 60, 90]:
            df[f"lag_{lag}"] = df["price"].shift(lag)

        # ── Rolling statistics ────────────────────────────────────────
        for w in [5, 10, 20, 60]:
            df[f"roll_mean_{w}"]  = df["price"].rolling(w).mean()
            df[f"roll_std_{w}"]   = df["price"].rolling(w).std()
            df[f"roll_min_{w}"]   = df["price"].rolling(w).min()
            df[f"roll_max_{w}"]   = df["price"].rolling(w).max()

        # ── Return features ───────────────────────────────────────────
        df["ret_1d"]   = df["price"].pct_change(1)
        df["ret_5d"]   = df["price"].pct_change(5)
        df["ret_20d"]  = df["price"].pct_change(20)
        df["ret_60d"]  = df["price"].pct_change(60)

        # ── Momentum & RSI ────────────────────────────────────────────
        df["momentum_20"] = df["price"] / df["price"].shift(20) - 1
        df["rsi_14"]      = self._rsi(df["price"], 14)

        # ── Volatility (Garman-Klass aproksymacja) ────────────────────
        df["vol_20d"]  = df["ret_1d"].rolling(20).std() * np.sqrt(252)

        # ── Sezonowość ────────────────────────────────────────────────
        df["month_sin"] = np.sin(2 * np.pi * df.index.month / 12)
        df["month_cos"] = np.cos(2 * np.pi * df.index.month / 12)
        df["week_sin"]  = np.sin(2 * np.pi * df.index.isocalendar().week / 52)
        df["week_cos"]  = np.cos(2 * np.pi * df.index.isocalendar().week / 52)
        df["quarter"]   = df.index.quarter

        # ── Crossover ─────────────────────────────────────────────────
        df["ma20_over_ma60"] = (
            (df["roll_mean_20"] > df["roll_mean_60"]).astype(int)
        )

        # Usuń NaN (pierwsze ~90 dni po lagach)
        df = df.dropna()

        feature_cols = [c for c in df.columns if c != "price"]
        return FeatureMatrix(
            dates=list(df.index.date),
            features=df[feature_cols].values.astype(np.float32),
            names=feature_cols,
        )

    @staticmethod
    def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
        delta  = series.diff()
        gain   = delta.clip(lower=0).rolling(window).mean()
        loss   = (-delta.clip(upper=0)).rolling(window).mean()
        rs     = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
```

### 3.2 Opis cech

| Cecha | Typ | Opis |
|-------|-----|------|
| `lag_N` | Numeryczna | Cena N dni temu (1, 5, 10, 20, 30, 60, 90) |
| `roll_mean_W` | Numeryczna | Średnia krocząca W-dniowa |
| `roll_std_W` | Numeryczna | Odchylenie standardowe W-dniowe (zmienność) |
| `roll_min_W` / `roll_max_W` | Numeryczna | Poziomy wsparcia / oporu |
| `ret_Nd` | Numeryczna | Stopa zwrotu N-dniowa |
| `momentum_20` | Numeryczna | Momentum 20-dniowe |
| `rsi_14` | Numeryczna | RSI 14-dniowy (0–100) |
| `vol_20d` | Numeryczna | Zmienność historyczna 20-dniowa (annualized) |
| `month_sin/cos` | Cykliczna | Kodowanie miesiąca jako sin/cos |
| `week_sin/cos` | Cykliczna | Kodowanie tygodnia jako sin/cos |
| `quarter` | Kategorialna | Kwartał (1–4) |
| `ma20_over_ma60` | Binarna | Sygnał złotego krzyża |
| `pmi_de` | Makro | PMI manufacturing Niemcy |
| `eurusd` | FX | Kurs EUR/USD |
| `cny_eur` | FX | Kurs CNY/EUR |
| `hicp_eu` | Makro | HICP EU (inflacja) |
| `bdi` | Makro | Baltic Dry Index |
| `electricity_de` | Cross | Cena energii DE (wpływ na koszty hutnictwa) |

### 3.3 Feature selection — ważność cech (importances)

```python
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.inspection import permutation_importance

class FeatureSelector:
    """Permutation importance na GBM — wybiera top-K cech dla LSTM."""

    TOP_K = 20

    def select(
        self,
        fm: FeatureMatrix,
        target: np.ndarray,   # cena za N dni
        k: int = TOP_K,
    ) -> list[str]:
        model = GradientBoostingRegressor(n_estimators=200, max_depth=4,
                                          random_state=42)
        split = int(len(fm.features) * 0.8)
        model.fit(fm.features[:split], target[:split])
        perm = permutation_importance(
            model, fm.features[split:], target[split:],
            n_repeats=10, random_state=42
        )
        ranked = np.argsort(perm.importances_mean)[::-1]
        return [fm.names[i] for i in ranked[:k]]
```
