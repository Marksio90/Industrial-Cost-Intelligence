"""Tests for drift detection — no external ML services required."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ..drift.detector import DriftDetector, DriftLevel, MAPEDriftMonitor


@pytest.fixture
def detector() -> DriftDetector:
    return DriftDetector(psi_threshold_moderate=0.1, psi_threshold_high=0.2, ks_alpha=0.05)


@pytest.fixture
def reference_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "price_per_kg_eur": rng.normal(5.0, 1.0, 500),
        "cycle_time_min": rng.exponential(10.0, 500),
        "volume_cm3": rng.lognormal(2.0, 0.5, 500),
        "material_class": rng.choice(["METAL", "PLASTIC", "COMPOSITE"], 500),
    })


class TestDriftDetector:
    def test_no_drift_identical_distribution(self, detector, reference_df):
        rng = np.random.default_rng(99)
        detector.set_reference(reference_df)
        # Current drawn from same distribution
        current = pd.DataFrame({
            "price_per_kg_eur": rng.normal(5.0, 1.0, 200),
            "cycle_time_min": rng.exponential(10.0, 200),
            "volume_cm3": rng.lognormal(2.0, 0.5, 200),
            "material_class": rng.choice(["METAL", "PLASTIC", "COMPOSITE"], 200),
        })
        report = detector.detect(current)
        # No severe drift expected (may have moderate by chance — accept both)
        assert report.overall_level in (DriftLevel.NONE, DriftLevel.MODERATE)

    def test_high_drift_completely_different(self, detector, reference_df):
        rng = np.random.default_rng(7)
        detector.set_reference(reference_df)
        # Completely different distribution
        current = pd.DataFrame({
            "price_per_kg_eur": rng.normal(50.0, 5.0, 200),   # 10× higher mean
            "cycle_time_min": rng.exponential(100.0, 200),    # 10× longer
            "volume_cm3": rng.lognormal(5.0, 0.5, 200),       # 3× higher log mean
            "material_class": rng.choice(["CERAMIC", "OTHER"], 200),  # different cats
        })
        report = detector.detect(current)
        assert report.overall_level == DriftLevel.HIGH
        assert len(report.features_with_high_drift) > 0

    def test_report_has_recommendation(self, detector, reference_df):
        rng = np.random.default_rng(0)
        detector.set_reference(reference_df)
        current = pd.DataFrame({
            "price_per_kg_eur": rng.normal(5.0, 1.0, 100),
        })
        report = detector.detect(current)
        assert report.recommendation
        assert report.timestamp

    def test_report_to_dict(self, detector, reference_df):
        detector.set_reference(reference_df)
        current = reference_df.sample(100, random_state=0)
        report = detector.detect(current)
        d = report.to_dict()
        assert "overall_level" in d
        assert "recommendation" in d
        assert isinstance(d["feature_drift"], list)

    def test_requires_reference(self, detector):
        with pytest.raises(AssertionError):
            detector.detect(pd.DataFrame({"x": [1, 2, 3]}))

    def test_prediction_drift_tracked(self, detector, reference_df):
        rng = np.random.default_rng(42)
        ref_preds = rng.normal(100, 10, 500)
        detector.set_reference(reference_df, y_pred_ref=ref_preds)
        current = reference_df.sample(200, random_state=1)
        # Predictions shifted significantly
        cur_preds = rng.normal(300, 10, 200)
        report = detector.detect(current, y_pred_current=cur_preds)
        assert report.prediction_drift is not None
        assert "prediction_mean_ref" in report.prediction_drift.details


class TestMAPEDriftMonitor:
    def test_no_alert_below_threshold(self):
        monitor = MAPEDriftMonitor(baseline_mape=0.10, threshold=0.05, alpha=0.1)
        # Feed MAPE values at baseline — no alert
        for _ in range(10):
            triggered = monitor.update(0.10)
        assert not triggered

    def test_alert_above_threshold(self):
        monitor = MAPEDriftMonitor(baseline_mape=0.10, threshold=0.05, alpha=1.0)
        # Single update with high MAPE (alpha=1.0 means instant update)
        triggered = monitor.update(0.20)  # 100% above baseline
        assert triggered

    def test_ewma_smoothing(self):
        monitor = MAPEDriftMonitor(baseline_mape=0.10, threshold=0.05, alpha=0.1)
        monitor.update(0.10)  # baseline
        monitor.update(0.15)
        # EWMA should be between 0.10 and 0.15
        assert monitor.current_ewma is not None
        assert 0.10 < monitor.current_ewma < 0.15

    def test_gradual_degradation_triggers(self):
        monitor = MAPEDriftMonitor(baseline_mape=0.10, threshold=0.05, alpha=0.3)
        # Gradually increasing MAPE
        triggered = False
        for mape in np.linspace(0.10, 0.25, 30):
            triggered = monitor.update(float(mape))
            if triggered:
                break
        assert triggered


class TestPSICalculation:
    """Test PSI directly via detector internals."""

    def test_psi_zero_identical(self, detector):
        ref = np.random.default_rng(0).normal(0, 1, 1000)
        result = detector._psi_numeric("x", ref, ref)
        assert result.statistic == pytest.approx(0.0, abs=1e-6)

    def test_psi_increases_with_shift(self, detector):
        rng = np.random.default_rng(0)
        ref = rng.normal(0, 1, 1000)
        slight = rng.normal(0.2, 1, 200)
        large  = rng.normal(2.0, 1, 200)
        r_slight = detector._psi_numeric("x", ref, slight)
        r_large  = detector._psi_numeric("x", ref, large)
        assert r_large.statistic > r_slight.statistic
