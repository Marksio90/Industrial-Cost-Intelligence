"""Tests for all evaluation metrics — zero external dependencies."""
from __future__ import annotations

import numpy as np
import pytest

from ..evaluation.metrics import (
    RegressionMetrics,
    bias,
    compute_all,
    coverage_at_pct,
    mae,
    mdape,
    mape,
    percentile_errors,
    r2,
    rmse,
    rmsle,
    smape,
    wmape,
)


# ── MAPE ─────────────────────────────────────────────────────────────────────

class TestMAPE:
    def test_perfect_predictions(self):
        y = np.array([10.0, 20.0, 30.0])
        assert mape(y, y) == pytest.approx(0.0)

    def test_known_value(self):
        # |10-12|/10 = 0.2, |20-18|/20 = 0.1 → mean = 0.15
        y_true = np.array([10.0, 20.0])
        y_pred = np.array([12.0, 18.0])
        assert mape(y_true, y_pred) == pytest.approx(0.15, rel=1e-5)

    def test_zero_actual_floored_by_eps(self):
        y_true = np.array([0.0, 10.0])
        y_pred = np.array([5.0, 10.0])
        result = mape(y_true, y_pred, eps=1.0)
        assert np.isfinite(result)

    def test_large_over_prediction(self):
        y_true = np.array([100.0])
        y_pred = np.array([200.0])
        assert mape(y_true, y_pred) == pytest.approx(1.0)


# ── WMAPE ─────────────────────────────────────────────────────────────────────

class TestWMAPE:
    def test_perfect(self):
        y = np.array([10.0, 20.0, 30.0])
        assert wmape(y, y) == pytest.approx(0.0)

    def test_known_value(self):
        # sum(|err|) / sum(actual) = 3 / 30 = 0.1
        y_true = np.array([10.0, 20.0])
        y_pred = np.array([11.0, 22.0])
        assert wmape(y_true, y_pred) == pytest.approx(3.0 / 30.0, rel=1e-5)

    def test_large_actuals_dominate(self):
        y_true = np.array([1.0, 1000.0])
        y_pred = np.array([2.0, 1000.0])   # 100% error on small value
        assert wmape(y_true, y_pred) == pytest.approx(1.0 / 1001.0, rel=1e-4)


# ── SMAPE ─────────────────────────────────────────────────────────────────────

class TestSMAPE:
    def test_perfect(self):
        y = np.array([5.0, 10.0, 15.0])
        assert smape(y, y) == pytest.approx(0.0)

    def test_bounded_above(self):
        y_true = np.array([10.0])
        y_pred = np.array([0.0])
        # |10-0| / ((10+0)/2) = 2
        assert smape(y_true, y_pred) == pytest.approx(2.0)


# ── MAE ───────────────────────────────────────────────────────────────────────

class TestMAE:
    def test_known_value(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([2.0, 2.0, 2.0])
        assert mae(y_true, y_pred) == pytest.approx(2.0 / 3.0, rel=1e-5)


# ── RMSE ──────────────────────────────────────────────────────────────────────

class TestRMSE:
    def test_known_value(self):
        y_true = np.array([0.0, 0.0])
        y_pred = np.array([3.0, 4.0])
        # sqrt((9+16)/2) = sqrt(12.5) ≈ 3.5355
        assert rmse(y_true, y_pred) == pytest.approx(np.sqrt(12.5), rel=1e-5)

    def test_perfect(self):
        y = np.array([10.0, 20.0])
        assert rmse(y, y) == pytest.approx(0.0)


# ── RMSLE ─────────────────────────────────────────────────────────────────────

class TestRMSLE:
    def test_perfect(self):
        y = np.array([1.0, 10.0, 100.0])
        assert rmsle(y, y) == pytest.approx(0.0)

    def test_under_prediction_penalised(self):
        y_true = np.array([100.0])
        y_under = np.array([50.0])
        y_over  = np.array([150.0])
        # ln(51)-ln(101) ≠ ln(151)-ln(101)
        assert rmsle(y_true, y_under) != rmsle(y_true, y_over)


# ── R² ────────────────────────────────────────────────────────────────────────

class TestR2:
    def test_perfect(self):
        y = np.array([1.0, 2.0, 3.0])
        assert r2(y, y) == pytest.approx(1.0)

    def test_mean_predictor(self):
        y = np.array([1.0, 2.0, 3.0])
        pred = np.full_like(y, y.mean())
        assert r2(y, pred) == pytest.approx(0.0, abs=1e-10)

    def test_negative(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_bad  = np.array([3.0, 2.0, 1.0])
        assert r2(y_true, y_bad) < 0


# ── MdAPE ─────────────────────────────────────────────────────────────────────

class TestMdAPE:
    def test_outlier_robust(self):
        y_true = np.array([10.0] * 9 + [10.0])
        y_pred = np.array([11.0] * 9 + [1000.0])  # one huge outlier
        # 8 × 10% errors + 1 huge — median should be 10%
        assert mdape(y_true, y_pred) == pytest.approx(0.10, rel=0.01)


# ── Coverage ──────────────────────────────────────────────────────────────────

class TestCoverage:
    def test_all_within(self):
        y_true = np.array([100.0, 200.0, 300.0])
        y_pred = np.array([105.0, 205.0, 295.0])  # all within 5%
        assert coverage_at_pct(y_true, y_pred, k=0.10) == pytest.approx(1.0)

    def test_none_within(self):
        y_true = np.array([100.0])
        y_pred = np.array([200.0])  # 100% error
        assert coverage_at_pct(y_true, y_pred, k=0.10) == pytest.approx(0.0)


# ── Bias ──────────────────────────────────────────────────────────────────────

class TestBias:
    def test_positive_bias(self):
        assert bias(np.array([10.0]), np.array([12.0])) == pytest.approx(2.0)

    def test_negative_bias(self):
        assert bias(np.array([10.0]), np.array([8.0])) == pytest.approx(-2.0)


# ── Percentile errors ─────────────────────────────────────────────────────────

class TestPercentileErrors:
    def test_returns_all_percentiles(self):
        y = np.arange(1, 101, dtype=float)
        result = percentile_errors(y, y * 1.1)
        assert set(result.keys()) == {"p50_ape", "p75_ape", "p90_ape", "p95_ape", "p99_ape"}
        for v in result.values():
            assert np.isfinite(v)


# ── compute_all integration ───────────────────────────────────────────────────

class TestComputeAll:
    def test_returns_named_tuple(self):
        y = np.linspace(10, 100, 50)
        noise = y + np.random.default_rng(0).normal(0, 2, size=50)
        m = compute_all(y, noise)
        assert isinstance(m, RegressionMetrics)
        assert m.n_samples == 50
        assert m.r2 > 0.9  # noise is small relative to range
        assert m.mape < 0.1

    def test_to_dict_has_all_keys(self):
        y = np.array([1.0, 2.0, 3.0])
        m = compute_all(y, y)
        d = m.to_dict()
        for key in ("mape", "wmape", "smape", "mae", "rmse", "rmsle", "r2", "mdape"):
            assert key in d

    def test_str_representation(self):
        y = np.array([10.0, 20.0])
        m = compute_all(y, y)
        s = str(m)
        assert "MAPE" in s and "RMSE" in s
