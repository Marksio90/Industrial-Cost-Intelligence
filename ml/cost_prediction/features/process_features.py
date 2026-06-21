"""
Manufacturing process feature engineering.

Raw inputs                → Engineered features
────────────────────────────────────────────────
process_type              → target-encoded + complexity tier
setup_time_h              → as-is + log
cycle_time_min            → as-is + log
machine_hourly_rate_eur   → as-is + log
operator_count            → as-is
scrap_rate_pct            → logit-transformed (bounded 0-1)
tooling_cost_eur          → log
num_operations            → polynomial (sq)
surface_finish_ra         → binned (rough/medium/fine)
tolerance_grade           → ordinal encoded (IT6 < IT7 < IT8 ...)
requires_heat_treatment   → binary
requires_inspection       → binary
process_cost_rate         → derived: hourly_rate * cycle_time / 60
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

_TOLERANCE_GRADES = {
    "IT4": 0, "IT5": 1, "IT6": 2, "IT7": 3, "IT8": 4,
    "IT9": 5, "IT10": 6, "IT11": 7, "IT12": 8,
}

_PROCESS_COMPLEXITY = {
    "turning": 1, "milling": 2, "grinding": 3, "honing": 4,
    "drilling": 1, "reaming": 2, "broaching": 3,
    "casting": 2, "forging": 3, "stamping": 2,
    "injection_moulding": 2, "extrusion": 1,
    "welding": 2, "laser_cutting": 2, "edm": 4,
    "3d_printing": 3,
}


class ProcessFeatureTransformer(BaseEstimator, TransformerMixin):
    def __init__(self) -> None:
        self._num_means: dict[str, float] = {}

    def fit(self, X: pd.DataFrame, y=None) -> "ProcessFeatureTransformer":
        X = _df(X)
        for col in ["setup_time_h", "cycle_time_min", "machine_hourly_rate_eur",
                    "scrap_rate_pct", "tooling_cost_eur", "num_operations"]:
            if col in X.columns:
                self._num_means[col] = float(X[col].mean())
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X = _df(X).copy()

        for col, mean in self._num_means.items():
            if col in X.columns:
                X[col] = X[col].fillna(mean)

        # Log transforms
        for col in ["setup_time_h", "cycle_time_min", "machine_hourly_rate_eur", "tooling_cost_eur"]:
            if col in X.columns:
                X[f"log_{col}"] = np.log1p(X[col].clip(lower=0))

        # Process cost rate
        if all(c in X.columns for c in ["machine_hourly_rate_eur", "cycle_time_min"]):
            X["process_cost_rate"] = (
                X["machine_hourly_rate_eur"] * X["cycle_time_min"] / 60.0
            )
            X["log_process_cost_rate"] = np.log1p(X["process_cost_rate"].clip(0))

        # Scrap rate: logit transform (maps (0,1) → ℝ)
        if "scrap_rate_pct" in X.columns:
            p = (X["scrap_rate_pct"] / 100.0).clip(1e-4, 1 - 1e-4)
            X["scrap_logit"] = np.log(p / (1 - p))

        # Total setup amortised over batch (if batch_size available)
        if "setup_time_h" in X.columns and "batch_size" in X.columns:
            X["setup_per_unit_h"] = X["setup_time_h"] / X["batch_size"].clip(lower=1)

        # Operations squared (non-linear complexity)
        if "num_operations" in X.columns:
            X["num_operations_sq"] = X["num_operations"] ** 2

        # Process complexity tier
        if "process_type" in X.columns:
            X["process_complexity"] = (
                X["process_type"].str.lower()
                .map(_PROCESS_COMPLEXITY)
                .fillna(2)
                .astype(np.int8)
            )

        # Tolerance grade ordinal
        if "tolerance_grade" in X.columns:
            X["tolerance_ordinal"] = (
                X["tolerance_grade"].map(_TOLERANCE_GRADES).fillna(3).astype(np.int8)
            )

        # Surface finish bins: Ra ≤ 0.8 fine, ≤ 3.2 medium, else rough
        if "surface_finish_ra" in X.columns:
            X["finish_tier"] = pd.cut(
                X["surface_finish_ra"],
                bins=[-np.inf, 0.8, 3.2, np.inf],
                labels=[2, 1, 0],
            ).astype(float)

        # Binary flags
        for col in ["requires_heat_treatment", "requires_inspection", "requires_coating"]:
            if col in X.columns:
                X[col] = X[col].fillna(0).astype(np.int8)

        return X


def _df(X) -> pd.DataFrame:
    return X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
