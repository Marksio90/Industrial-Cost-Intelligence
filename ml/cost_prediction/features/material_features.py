"""
Material feature engineering.

Raw inputs                 → Engineered features
──────────────────────────────────────────────────
material_class             → one-hot / target-encoded
density_kg_m3              → log-transformed (right-skewed)
hardness_hv                → as-is + log
ultimate_tensile_strength  → as-is + normalised
yield_strength             → as-is
elongation_pct             → as-is
thermal_conductivity       → binned into quartiles
price_per_kg_eur           → log + interaction with volume
machinability_index        → as-is (0-1 scale)
material_cost_index        → log, derived = price_per_kg * density * volume
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


_MATERIAL_NUMERIC = [
    "density_kg_m3",
    "hardness_hv",
    "ultimate_tensile_strength_mpa",
    "yield_strength_mpa",
    "elongation_pct",
    "thermal_conductivity_w_mk",
    "price_per_kg_eur",
    "machinability_index",
]

_LOG_FEATURES = [
    "density_kg_m3",
    "hardness_hv",
    "ultimate_tensile_strength_mpa",
    "price_per_kg_eur",
]


class MaterialFeatureTransformer(BaseEstimator, TransformerMixin):
    """
    Produces engineered material features from raw material DataFrame columns.
    Designed to plug into a sklearn Pipeline as the first step.
    """

    def __init__(
        self,
        log_features: list[str] = _LOG_FEATURES,
        derive_cost_index: bool = True,
    ) -> None:
        self.log_features = log_features
        self.derive_cost_index = derive_cost_index
        self._feature_stats: dict[str, tuple[float, float]] = {}

    def fit(self, X: pd.DataFrame, y=None) -> "MaterialFeatureTransformer":
        X = self._ensure_df(X)
        for col in _MATERIAL_NUMERIC:
            if col in X.columns:
                self._feature_stats[col] = (
                    float(X[col].mean()),
                    float(X[col].std()) or 1.0,
                )
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X = self._ensure_df(X).copy()

        # Fill missing numerics with fitted means
        for col, (mean, _) in self._feature_stats.items():
            if col in X.columns:
                X[col] = X[col].fillna(mean)

        # Log-transform skewed features
        for col in self.log_features:
            if col in X.columns:
                X[f"log_{col}"] = np.log1p(X[col].clip(lower=0))

        # Hardness × machinability interaction
        if "hardness_hv" in X.columns and "machinability_index" in X.columns:
            X["hardness_machinability"] = (
                X["hardness_hv"] * X["machinability_index"]
            )

        # Strength-to-weight ratio
        if all(c in X.columns for c in ["ultimate_tensile_strength_mpa", "density_kg_m3"]):
            X["strength_to_weight"] = (
                X["ultimate_tensile_strength_mpa"]
                / X["density_kg_m3"].clip(lower=1e-3)
            )

        # Material cost index: price_per_kg × density → price_per_m3
        if self.derive_cost_index:
            if all(c in X.columns for c in ["price_per_kg_eur", "density_kg_m3"]):
                X["material_cost_per_m3"] = (
                    X["price_per_kg_eur"] * X["density_kg_m3"]
                )
                X["log_material_cost_per_m3"] = np.log1p(X["material_cost_per_m3"].clip(0))

        # Ductility indicator (elongation > 10% → ductile)
        if "elongation_pct" in X.columns:
            X["is_ductile"] = (X["elongation_pct"] > 10).astype(np.int8)

        # Thermal bin (quartile-based)
        if "thermal_conductivity_w_mk" in X.columns:
            X["thermal_bin"] = pd.qcut(
                X["thermal_conductivity_w_mk"].rank(method="first"),
                q=4, labels=[0, 1, 2, 3]
            ).astype(float)

        return X

    @staticmethod
    def _ensure_df(X) -> pd.DataFrame:
        if not isinstance(X, pd.DataFrame):
            return pd.DataFrame(X)
        return X

    def get_feature_names_out(self, input_features=None) -> list[str]:
        base = list(_MATERIAL_NUMERIC)
        derived = [f"log_{c}" for c in self.log_features]
        derived += [
            "hardness_machinability", "strength_to_weight",
            "material_cost_per_m3", "log_material_cost_per_m3",
            "is_ductile", "thermal_bin",
        ]
        return base + derived
