"""
Regression evaluation metrics for cost prediction.

All metrics accept numpy arrays or lists.
All functions return float (scalar).

Implemented:
  - MAPE   : Mean Absolute Percentage Error
  - WMAPE  : Weighted MAPE (weighted by actuals — robust to near-zero values)
  - SMAPE  : Symmetric MAPE (bounded 0-200%)
  - MAE    : Mean Absolute Error (in EUR)
  - RMSE   : Root Mean Squared Error
  - RMSLE  : Root Mean Squared Log Error (penalises under-predictions)
  - R2     : Coefficient of Determination
  - MdAPE  : Median APE (robust to outliers)
  - coverage_at_pct: % of predictions within ±k% of actual
"""
from __future__ import annotations

import warnings
from typing import NamedTuple

import numpy as np


class RegressionMetrics(NamedTuple):
    mape: float
    wmape: float
    smape: float
    mae: float
    rmse: float
    rmsle: float
    r2: float
    mdape: float
    coverage_10pct: float
    coverage_20pct: float
    n_samples: int

    def to_dict(self) -> dict[str, float]:
        return self._asdict()

    def __str__(self) -> str:
        return (
            f"MAPE={self.mape:.2%}  WMAPE={self.wmape:.2%}  MAE={self.mae:.2f}€  "
            f"RMSE={self.rmse:.2f}€  R²={self.r2:.4f}  "
            f"Cov@10%={self.coverage_10pct:.2%}  n={self.n_samples}"
        )


def compute_all(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    eps: float = 1.0,
) -> RegressionMetrics:
    """
    Compute all metrics in one pass.

    Args:
        y_true: Actual cost values (EUR)
        y_pred: Predicted cost values (EUR)
        eps:    Floor value to avoid division by zero (default 1.0 EUR)
    """
    y_true = np.asarray(y_true, dtype=np.float64).ravel()
    y_pred = np.asarray(y_pred, dtype=np.float64).ravel()
    assert len(y_true) == len(y_pred), "Arrays must have equal length"
    n = len(y_true)

    return RegressionMetrics(
        mape=mape(y_true, y_pred, eps=eps),
        wmape=wmape(y_true, y_pred, eps=eps),
        smape=smape(y_true, y_pred),
        mae=mae(y_true, y_pred),
        rmse=rmse(y_true, y_pred),
        rmsle=rmsle(y_true, y_pred),
        r2=r2(y_true, y_pred),
        mdape=mdape(y_true, y_pred, eps=eps),
        coverage_10pct=coverage_at_pct(y_true, y_pred, k=0.10),
        coverage_20pct=coverage_at_pct(y_true, y_pred, k=0.20),
        n_samples=n,
    )


def mape(y_true: np.ndarray, y_pred: np.ndarray, *, eps: float = 1.0) -> float:
    """Mean Absolute Percentage Error. Floor actuals at `eps` to avoid ÷0."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    denom = np.maximum(np.abs(y_true), eps)
    return float(np.mean(np.abs(y_true - y_pred) / denom))


def wmape(y_true: np.ndarray, y_pred: np.ndarray, *, eps: float = 1.0) -> float:
    """Weighted MAPE: sum(|error|) / sum(|actual|). Robust to small actuals."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    return float(np.sum(np.abs(y_true - y_pred)) / max(np.sum(np.abs(y_true)), eps))


def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Symmetric MAPE. Bounded [0, 2]."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ratio = np.where(denom == 0, 0.0, np.abs(y_true - y_pred) / denom)
    return float(np.mean(ratio))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def rmsle(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Root Mean Squared Log Error. Clips negatives to 0."""
    y_true = np.asarray(y_true, dtype=np.float64).clip(0)
    y_pred = np.asarray(y_pred, dtype=np.float64).clip(0)
    return float(np.sqrt(np.mean((np.log1p(y_pred) - np.log1p(y_true)) ** 2)))


def r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2)
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def mdape(y_true: np.ndarray, y_pred: np.ndarray, *, eps: float = 1.0) -> float:
    """Median Absolute Percentage Error — robust to outliers."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    denom = np.maximum(np.abs(y_true), eps)
    return float(np.median(np.abs(y_true - y_pred) / denom))


def coverage_at_pct(y_true: np.ndarray, y_pred: np.ndarray, *, k: float = 0.10) -> float:
    """Fraction of predictions within ±k% of actual."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    pct_err = np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), 1.0)
    return float(np.mean(pct_err <= k))


def bias(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean signed error (positive = over-prediction)."""
    return float(np.mean(np.asarray(y_pred) - np.asarray(y_true)))


def percentile_errors(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    percentiles: list[int] | None = None,
) -> dict[str, float]:
    """Absolute percentage error distribution."""
    if percentiles is None:
        percentiles = [50, 75, 90, 95, 99]
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    ape = np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), 1.0)
    return {f"p{p}_ape": float(np.percentile(ape, p)) for p in percentiles}
