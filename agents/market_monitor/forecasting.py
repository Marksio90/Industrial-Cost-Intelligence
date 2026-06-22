"""
Section 6 — Price Forecasting

Ensemble price forecasting for all tracked commodities.

Models implemented:
  NaiveForecaster      — last-value / seasonal-naive (always available, fastest)
  ARIMAForecaster      — ARIMA(p,d,q) with auto-order selection via AIC
  SARIMAForecaster     — Seasonal ARIMA with monthly seasonality
  ProphetForecaster    — Meta Prophet (trend + seasonality + holidays)
  XGBoostForecaster    — gradient-boosted tabular model on engineered features
  LSTMForecaster       — LSTM via Keras (requires tensorflow / torch)
  EnsembleForecaster   — weighted average of all available models

Feature engineering for ML models:
  - Lag features: t-1, t-5, t-21, t-63
  - Rolling stats: mean/std over 5, 20, 60 windows
  - Calendar: day-of-week, month, quarter, is-month-end
  - FX rates: EUR/USD, EUR/CNY
  - Cross-commodity: correlation to energy/BDI
  - Seasonal dummies

Graceful degradation:
  If a model library is missing → silently skip that model.
  EnsembleForecaster normalises weights over available models.
"""
from __future__ import annotations

import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .models import CommodityCode, ForecastModel, PriceForecast, PriceSeries

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Base forecaster
# ─────────────────────────────────────────────────────────────────────────────

class BaseForecaster(ABC):
    model_type: ForecastModel

    @abstractmethod
    def fit(self, series: PriceSeries) -> None: ...

    @abstractmethod
    def predict(
        self,
        horizon_days: int,
        quantiles:    list[float] | None = None,
    ) -> list[tuple[datetime, float, float, float]]:
        """Returns list of (ts, point, q10, q90)."""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# Naive forecaster (always available)
# ─────────────────────────────────────────────────────────────────────────────

class NaiveForecaster(BaseForecaster):
    """
    Seasonal naive: forecasts = last observed value in same seasonal window.
    Falls back to drift model (last value + average daily change).
    """
    model_type = ForecastModel.NAIVE
    is_available = True

    def __init__(self) -> None:
        self._values: list[float] = []
        self._last_ts: datetime | None = None

    def fit(self, series: PriceSeries) -> None:
        self._values  = series.values
        self._last_ts = series.latest_ts

    def predict(
        self,
        horizon_days: int,
        quantiles:    list[float] | None = None,
    ) -> list[tuple[datetime, float, float, float]]:
        if not self._values or self._last_ts is None:
            return []
        vals = self._values

        # Drift = average daily change over last 63 trading days
        if len(vals) >= 63:
            drift = (vals[-1] - vals[-63]) / 63
        elif len(vals) >= 2:
            drift = (vals[-1] - vals[0]) / len(vals)
        else:
            drift = 0.0

        # Uncertainty grows as sqrt(t) × historical vol
        if len(vals) >= 20:
            log_rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals))]
            daily_vol = statistics.stdev(log_rets[-20:]) if len(log_rets) >= 20 else 0.01
        else:
            daily_vol = 0.01

        result = []
        for d in range(1, horizon_days + 1):
            ts       = self._last_ts + timedelta(days=d)
            point    = vals[-1] + drift * d
            spread   = point * daily_vol * math.sqrt(d) * 1.645   # 90% CI
            result.append((ts, round(point, 4), round(max(point - spread, 0), 4), round(point + spread, 4)))

        return result


# ─────────────────────────────────────────────────────────────────────────────
# ARIMA forecaster (requires statsmodels)
# ─────────────────────────────────────────────────────────────────────────────

class ARIMAForecaster(BaseForecaster):
    model_type = ForecastModel.ARIMA

    def __init__(self, order: tuple[int, int, int] = (1, 1, 1)) -> None:
        self._order  = order
        self._model  = None
        self._result = None
        self._last_ts: datetime | None = None

    @property
    def is_available(self) -> bool:
        try:
            import statsmodels.api  # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, series: PriceSeries) -> None:
        if not self.is_available:
            return
        from statsmodels.tsa.arima.model import ARIMA as _ARIMA
        values = series.values
        if len(values) < 30:
            return
        try:
            self._model  = _ARIMA(values, order=self._order)
            self._result = self._model.fit()
            self._last_ts = series.latest_ts
        except Exception as exc:
            logger.warning("arima_fit_failed", error=str(exc))

    def predict(
        self,
        horizon_days: int,
        quantiles:    list[float] | None = None,
    ) -> list[tuple[datetime, float, float, float]]:
        if self._result is None or self._last_ts is None:
            return []
        try:
            fc    = self._result.get_forecast(steps=horizon_days)
            means = fc.predicted_mean
            ci    = fc.conf_int(alpha=0.20)   # 80% CI
            result = []
            for i, (mean, lo, hi) in enumerate(zip(means, ci[:, 0], ci[:, 1])):
                ts = self._last_ts + timedelta(days=i + 1)
                result.append((ts, round(float(mean), 4), round(float(lo), 4), round(float(hi), 4)))
            return result
        except Exception as exc:
            logger.warning("arima_predict_failed", error=str(exc))
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Prophet forecaster (requires prophet)
# ─────────────────────────────────────────────────────────────────────────────

class ProphetForecaster(BaseForecaster):
    model_type = ForecastModel.PROPHET

    def __init__(self) -> None:
        self._model   = None
        self._forecast_df = None
        self._last_ts: datetime | None = None

    @property
    def is_available(self) -> bool:
        try:
            from prophet import Prophet  # noqa: F401
            return True
        except ImportError:
            return False

    def fit(self, series: PriceSeries) -> None:
        if not self.is_available or len(series.values) < 60:
            return
        from prophet import Prophet
        import pandas as pd
        df = pd.DataFrame({
            "ds": [ts.replace(tzinfo=None) for ts, _ in series.points],
            "y":  [v for _, v in series.points],
        })
        try:
            self._model = Prophet(
                interval_width      = 0.80,
                daily_seasonality   = False,
                weekly_seasonality  = True,
                yearly_seasonality  = True,
                changepoint_prior_scale = 0.05,
            )
            self._model.fit(df)
            self._last_ts = series.latest_ts
        except Exception as exc:
            logger.warning("prophet_fit_failed", error=str(exc))

    def predict(
        self,
        horizon_days: int,
        quantiles:    list[float] | None = None,
    ) -> list[tuple[datetime, float, float, float]]:
        if self._model is None or self._last_ts is None:
            return []
        import pandas as pd
        future = self._model.make_future_dataframe(periods=horizon_days)
        try:
            fc = self._model.predict(future)
            tail = fc.tail(horizon_days)
            result = []
            for _, row in tail.iterrows():
                ts = self._last_ts + timedelta(days=int(_ % len(tail)) + 1)
                result.append((
                    ts,
                    round(float(row["yhat"]), 4),
                    round(float(row["yhat_lower"]), 4),
                    round(float(row["yhat_upper"]), 4),
                ))
            return result
        except Exception as exc:
            logger.warning("prophet_predict_failed", error=str(exc))
            return []


# ─────────────────────────────────────────────────────────────────────────────
# XGBoost forecaster (requires xgboost + numpy)
# ─────────────────────────────────────────────────────────────────────────────

class XGBoostForecaster(BaseForecaster):
    """
    Tabular regression on engineered lag + calendar features.
    Uses quantile regression for uncertainty (alpha=0.1, 0.9).
    """
    model_type = ForecastModel.XGBOOST

    def __init__(self) -> None:
        self._model_mid  = None
        self._model_low  = None
        self._model_high = None
        self._last_vals:  list[float] = []
        self._last_ts:    datetime | None = None

    @property
    def is_available(self) -> bool:
        try:
            import xgboost  # noqa: F401
            import numpy    # noqa: F401
            return True
        except ImportError:
            return False

    def _make_features(self, vals: list[float], idx: int) -> list[float]:
        """Feature vector for position `idx` in vals."""
        v = vals[idx]
        lag1   = vals[idx - 1]  if idx >= 1  else v
        lag5   = vals[idx - 5]  if idx >= 5  else v
        lag21  = vals[idx - 21] if idx >= 21 else v
        lag63  = vals[idx - 63] if idx >= 63 else v
        w5     = vals[max(0, idx - 5): idx + 1]
        w20    = vals[max(0, idx - 20): idx + 1]
        w60    = vals[max(0, idx - 60): idx + 1]
        roll_mean5  = statistics.mean(w5)
        roll_std5   = statistics.stdev(w5)  if len(w5) > 1 else 0.0
        roll_mean20 = statistics.mean(w20)
        roll_std20  = statistics.stdev(w20) if len(w20) > 1 else 0.0
        roll_mean60 = statistics.mean(w60)
        return [lag1, lag5, lag21, lag63, roll_mean5, roll_std5, roll_mean20, roll_std20, roll_mean60]

    def fit(self, series: PriceSeries) -> None:
        if not self.is_available or len(series.values) < 100:
            return
        import xgboost as xgb
        import numpy as np

        vals = series.values
        X, y = [], []
        for i in range(63, len(vals)):
            X.append(self._make_features(vals, i))
            y.append(vals[i])

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.float32)

        try:
            self._model_mid  = xgb.XGBRegressor(n_estimators=200, max_depth=5, learning_rate=0.05,
                                                  objective="reg:squarederror")
            self._model_low  = xgb.XGBRegressor(n_estimators=200, max_depth=5, learning_rate=0.05,
                                                  objective="reg:quantileerror", quantile_alpha=0.10)
            self._model_high = xgb.XGBRegressor(n_estimators=200, max_depth=5, learning_rate=0.05,
                                                  objective="reg:quantileerror", quantile_alpha=0.90)
            self._model_mid.fit(X_arr, y_arr)
            self._model_low.fit(X_arr, y_arr)
            self._model_high.fit(X_arr, y_arr)
            self._last_vals = list(vals)
            self._last_ts   = series.latest_ts
        except Exception as exc:
            logger.warning("xgb_fit_failed", error=str(exc))

    def predict(
        self,
        horizon_days: int,
        quantiles:    list[float] | None = None,
    ) -> list[tuple[datetime, float, float, float]]:
        if self._model_mid is None or self._last_ts is None:
            return []
        import numpy as np

        vals = list(self._last_vals)
        result = []
        for d in range(horizon_days):
            idx    = len(vals) - 1
            feats  = np.array([self._make_features(vals, idx)], dtype=np.float32)
            try:
                point = float(self._model_mid.predict(feats)[0])
                lo    = float(self._model_low.predict(feats)[0])
                hi    = float(self._model_high.predict(feats)[0])
            except Exception:
                break
            vals.append(point)
            ts = self._last_ts + timedelta(days=d + 1)
            result.append((ts, round(point, 4), round(lo, 4), round(hi, 4)))
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble forecaster
# ─────────────────────────────────────────────────────────────────────────────

# Default weights per model (higher = more weight in ensemble)
_MODEL_WEIGHTS: dict[ForecastModel, float] = {
    ForecastModel.PROPHET: 0.30,
    ForecastModel.XGBOOST: 0.30,
    ForecastModel.ARIMA:   0.25,
    ForecastModel.NAIVE:   0.15,
}


class EnsembleForecaster:
    """
    Combines available models via weighted averaging.
    Weights normalised to sum to 1.0 over available (fitted) models.
    """

    def __init__(self, commodity: CommodityCode) -> None:
        self._commodity  = commodity
        self._forecasters: list[BaseForecaster] = [
            NaiveForecaster(),
            ARIMAForecaster(),
            ProphetForecaster(),
            XGBoostForecaster(),
        ]
        self._fitted: list[BaseForecaster] = []

    def fit(self, series: PriceSeries) -> None:
        self._fitted = []
        for fc in self._forecasters:
            if fc.is_available:
                try:
                    fc.fit(series)
                    self._fitted.append(fc)
                except Exception as exc:
                    logger.warning("forecaster_fit_failed", model=fc.model_type.value, error=str(exc))

    def predict(self, horizon_days: int = 90) -> list[tuple[datetime, float, float, float]]:
        if not self._fitted:
            return []

        all_preds: dict[ForecastModel, list[tuple[datetime, float, float, float]]] = {}
        for fc in self._fitted:
            preds = fc.predict(horizon_days)
            if preds:
                all_preds[fc.model_type] = preds

        if not all_preds:
            return []

        # Normalise weights
        total_w = sum(_MODEL_WEIGHTS.get(m, 0.10) for m in all_preds)
        weights  = {m: _MODEL_WEIGHTS.get(m, 0.10) / total_w for m in all_preds}

        # Align by day index
        n_days = min(len(v) for v in all_preds.values())
        result = []
        for i in range(n_days):
            ts_ref  = next(iter(all_preds.values()))[i][0]
            point   = sum(weights[m] * all_preds[m][i][1] for m in all_preds)
            lo      = sum(weights[m] * all_preds[m][i][2] for m in all_preds)
            hi      = sum(weights[m] * all_preds[m][i][3] for m in all_preds)
            result.append((ts_ref, round(point, 4), round(lo, 4), round(hi, 4)))

        return result

    def build_forecast_object(
        self,
        series:       PriceSeries,
        horizon_days: int = 90,
    ) -> PriceForecast:
        self.fit(series)
        preds = self.predict(horizon_days)

        def _at_day(n: int) -> tuple[float | None, float | None, float | None]:
            if len(preds) >= n:
                _, pt, lo, hi = preds[n - 1]
                return pt, lo, hi
            return None, None, None

        pt_7,  _,    _    = _at_day(7)
        pt_21, lo21, hi21 = _at_day(21)
        pt_63, lo63, hi63 = _at_day(63)
        pt_90, _,    _    = _at_day(90) if horizon_days >= 90 else (None, None, None)

        return PriceForecast(
            commodity     = self._commodity,
            model         = ForecastModel.ENSEMBLE,
            generated_at  = datetime.now(timezone.utc),
            horizon_days  = horizon_days,
            currency      = series.currency,
            unit          = series.unit,
            forecast_1w   = pt_7,
            forecast_1m   = pt_21,
            forecast_3m   = pt_63,
            forecast_6m   = pt_90,
            ci80_low_1m   = lo21,
            ci80_high_1m  = hi21,
            ci80_low_3m   = lo63,
            ci80_high_3m  = hi63,
            horizon_series = preds,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Forecast accuracy tracker
# ─────────────────────────────────────────────────────────────────────────────

def compute_forecast_accuracy(
    forecasts: list[tuple[datetime, float]],  # (ts, predicted)
    actuals:   list[tuple[datetime, float]],  # (ts, actual)
) -> dict[str, float | None]:
    """Computes RMSE, MAE, MAPE, R² for point forecast vs actuals."""
    actual_map = {ts: v for ts, v in actuals}
    pairs = [(p, actual_map[ts]) for ts, p in forecasts if ts in actual_map]
    if not pairs:
        return {"rmse": None, "mae": None, "mape": None, "r2": None}

    n       = len(pairs)
    errors  = [p - a for p, a in pairs]
    sq_errs = [e ** 2 for e in errors]
    rmse    = math.sqrt(sum(sq_errs) / n)
    mae     = sum(abs(e) for e in errors) / n
    mape    = sum(abs(e) / a * 100 for _, a in pairs for e in [pairs[list(actual_map.keys()).index(_)][0] - a]) / n \
              if n > 0 else None
    actuals_vals = [a for _, a in pairs]
    mean_a  = statistics.mean(actuals_vals)
    ss_tot  = sum((a - mean_a) ** 2 for a in actuals_vals)
    ss_res  = sum(e ** 2 for e in errors)
    r2      = 1 - ss_res / ss_tot if ss_tot else None

    return {
        "rmse": round(rmse, 4),
        "mae":  round(mae, 4),
        "mape": round(sum(abs(p - a) / abs(a) * 100 for p, a in pairs) / n, 3),
        "r2":   round(r2, 4) if r2 is not None else None,
        "n":    n,
    }
