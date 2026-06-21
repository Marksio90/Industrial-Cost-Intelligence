"""
Data drift + prediction drift detection.

Implemented methods:
  - PSI  (Population Stability Index): feature distribution shift
  - KS   (Kolmogorov-Smirnov test): numeric feature drift
  - Chi² : categorical feature drift
  - MAPE degradation: model performance drift (requires ground truth)
  - Z-score: prediction mean/variance shift (label-free)

Drift levels:
  PSI < 0.1       → No significant drift
  0.1 ≤ PSI < 0.2 → Moderate drift (monitor closely)
  PSI ≥ 0.2       → Significant drift → trigger retraining
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)


class DriftLevel(str, Enum):
    NONE     = "none"
    MODERATE = "moderate"
    HIGH     = "high"


@dataclass
class FeatureDriftResult:
    feature: str
    method: str
    statistic: float
    p_value: float | None
    level: DriftLevel
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftReport:
    timestamp: str
    n_reference: int
    n_current: int
    feature_results: list[FeatureDriftResult]
    prediction_drift: FeatureDriftResult | None
    overall_level: DriftLevel
    features_with_high_drift: list[str]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "n_reference": self.n_reference,
            "n_current": self.n_current,
            "overall_level": self.overall_level.value,
            "features_with_high_drift": self.features_with_high_drift,
            "recommendation": self.recommendation,
            "feature_drift": [
                {
                    "feature": r.feature,
                    "method": r.method,
                    "statistic": round(r.statistic, 4),
                    "level": r.level.value,
                }
                for r in self.feature_results
            ],
        }


class DriftDetector:
    """
    Detects distributional drift between a reference dataset and
    a current production window.

    Usage:
        detector = DriftDetector(settings)
        detector.set_reference(X_train)
        report = detector.detect(X_production_window)
    """

    def __init__(
        self,
        psi_threshold_moderate: float = 0.1,
        psi_threshold_high: float = 0.2,
        ks_alpha: float = 0.05,
        n_bins: int = 10,
    ) -> None:
        self.psi_moderate = psi_threshold_moderate
        self.psi_high = psi_threshold_high
        self.ks_alpha = ks_alpha
        self.n_bins = n_bins
        self._reference: pd.DataFrame | None = None
        self._reference_pred: np.ndarray | None = None

    def set_reference(
        self,
        X_ref: pd.DataFrame,
        y_pred_ref: np.ndarray | None = None,
    ) -> None:
        self._reference = X_ref.copy()
        self._reference_pred = np.asarray(y_pred_ref) if y_pred_ref is not None else None
        log.info("drift_reference_set", n=len(X_ref))

    def detect(
        self,
        X_current: pd.DataFrame,
        y_pred_current: np.ndarray | None = None,
    ) -> DriftReport:
        from datetime import datetime, timezone

        assert self._reference is not None, "Call set_reference() first"
        results: list[FeatureDriftResult] = []

        for col in self._reference.columns:
            if col not in X_current.columns:
                continue
            ref_col = self._reference[col].dropna()
            cur_col = X_current[col].dropna()

            if pd.api.types.is_numeric_dtype(ref_col):
                psi_result = self._psi_numeric(col, ref_col.values, cur_col.values)
                ks_result  = self._ks_test(col, ref_col.values, cur_col.values)
                results.append(psi_result)
                results.append(ks_result)
            else:
                chi_result = self._chi2_categorical(col, ref_col, cur_col)
                results.append(chi_result)

        # Prediction drift
        pred_drift = None
        if self._reference_pred is not None and y_pred_current is not None:
            pred_drift = self._psi_numeric(
                "predictions",
                self._reference_pred,
                np.asarray(y_pred_current),
            )
            # Z-score check on prediction mean
            ref_mean = self._reference_pred.mean()
            cur_mean = np.asarray(y_pred_current).mean()
            ref_std  = self._reference_pred.std() or 1.0
            z_score  = abs((cur_mean - ref_mean) / ref_std)
            pred_drift.details["prediction_mean_ref"] = float(ref_mean)
            pred_drift.details["prediction_mean_cur"] = float(cur_mean)
            pred_drift.details["z_score"] = float(z_score)

        high_drift = [r.feature for r in results if r.level == DriftLevel.HIGH]
        any_high = bool(high_drift)
        any_moderate = any(r.level == DriftLevel.MODERATE for r in results)

        if any_high:
            overall = DriftLevel.HIGH
            rec = f"RETRAIN: {len(high_drift)} feature(s) show significant drift: {high_drift[:5]}"
        elif any_moderate:
            overall = DriftLevel.MODERATE
            rec = "MONITOR: Moderate drift detected. Consider retraining if it persists."
        else:
            overall = DriftLevel.NONE
            rec = "No action required."

        report = DriftReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            n_reference=len(self._reference),
            n_current=len(X_current),
            feature_results=results,
            prediction_drift=pred_drift,
            overall_level=overall,
            features_with_high_drift=high_drift,
            recommendation=rec,
        )

        log.info(
            "drift_detection_done",
            overall=overall.value,
            high_features=len(high_drift),
            recommendation=rec,
        )
        return report

    # ── PSI ───────────────────────────────────────────────────────────────

    def _psi_numeric(
        self,
        feature: str,
        reference: np.ndarray,
        current: np.ndarray,
    ) -> FeatureDriftResult:
        ref_clean = reference[np.isfinite(reference)]
        cur_clean = current[np.isfinite(current)]

        if len(ref_clean) == 0 or len(cur_clean) == 0:
            return FeatureDriftResult(feature, "psi", 0.0, None, DriftLevel.NONE)

        bins = np.percentile(ref_clean, np.linspace(0, 100, self.n_bins + 1))
        bins[0] -= 1e-9
        bins[-1] += 1e-9
        bins = np.unique(bins)
        if len(bins) < 2:
            return FeatureDriftResult(feature, "psi", 0.0, None, DriftLevel.NONE)

        ref_counts, _ = np.histogram(ref_clean, bins=bins)
        cur_counts, _ = np.histogram(cur_clean, bins=bins)

        ref_pct = (ref_counts / len(ref_clean)).clip(1e-9)
        cur_pct = (cur_counts / len(cur_clean)).clip(1e-9)

        psi = float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))

        return FeatureDriftResult(
            feature=feature,
            method="psi",
            statistic=psi,
            p_value=None,
            level=self._psi_level(psi),
            details={"bins": len(bins) - 1},
        )

    def _psi_level(self, psi: float) -> DriftLevel:
        if psi >= self.psi_high:
            return DriftLevel.HIGH
        if psi >= self.psi_moderate:
            return DriftLevel.MODERATE
        return DriftLevel.NONE

    # ── KS test ───────────────────────────────────────────────────────────

    def _ks_test(
        self,
        feature: str,
        reference: np.ndarray,
        current: np.ndarray,
    ) -> FeatureDriftResult:
        from scipy import stats as scipy_stats

        ref_c = reference[np.isfinite(reference)]
        cur_c = current[np.isfinite(current)]
        if len(ref_c) == 0 or len(cur_c) == 0:
            return FeatureDriftResult(feature, "ks", 0.0, 1.0, DriftLevel.NONE)

        ks_stat, p_value = scipy_stats.ks_2samp(ref_c, cur_c)
        level = DriftLevel.HIGH if p_value < self.ks_alpha else DriftLevel.NONE
        return FeatureDriftResult(
            feature=feature,
            method="ks",
            statistic=float(ks_stat),
            p_value=float(p_value),
            level=level,
            details={"n_ref": len(ref_c), "n_cur": len(cur_c)},
        )

    # ── Chi-squared ───────────────────────────────────────────────────────

    def _chi2_categorical(
        self,
        feature: str,
        reference: pd.Series,
        current: pd.Series,
    ) -> FeatureDriftResult:
        from scipy.stats import chi2_contingency

        all_cats = set(reference.unique()) | set(current.unique())
        ref_counts = reference.value_counts().reindex(all_cats, fill_value=0)
        cur_counts = current.value_counts().reindex(all_cats, fill_value=0)

        contingency = np.array([ref_counts.values, cur_counts.values])
        if contingency.min() == 0:
            contingency = contingency + 1  # Laplace smoothing

        try:
            chi2, p_value, _, _ = chi2_contingency(contingency)
        except Exception:
            return FeatureDriftResult(feature, "chi2", 0.0, 1.0, DriftLevel.NONE)

        level = DriftLevel.HIGH if p_value < self.ks_alpha else DriftLevel.NONE
        return FeatureDriftResult(
            feature=feature, method="chi2",
            statistic=float(chi2), p_value=float(p_value), level=level,
        )


class MAPEDriftMonitor:
    """
    Monitors rolling MAPE using an EWMA (Exponentially Weighted Moving Average).
    Triggers alert when rolling MAPE exceeds baseline * (1 + threshold).
    """

    def __init__(self, baseline_mape: float, threshold: float = 0.05, alpha: float = 0.1) -> None:
        self.baseline = baseline_mape
        self.threshold = threshold
        self.alpha = alpha
        self._ewma: float | None = None
        self._history: list[float] = []

    def update(self, batch_mape: float) -> bool:
        """Update with a new batch MAPE. Returns True if drift is detected."""
        self._history.append(batch_mape)
        if self._ewma is None:
            self._ewma = batch_mape
        else:
            self._ewma = self.alpha * batch_mape + (1 - self.alpha) * self._ewma

        limit = self.baseline * (1 + self.threshold)
        drift = self._ewma > limit
        if drift:
            log.warning(
                "mape_drift_detected",
                ewma_mape=f"{self._ewma:.2%}",
                baseline=f"{self.baseline:.2%}",
                limit=f"{limit:.2%}",
            )
        return drift

    @property
    def current_ewma(self) -> float | None:
        return self._ewma
