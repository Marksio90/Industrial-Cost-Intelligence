"""
Geometric feature engineering.

Raw inputs          → Engineered features
──────────────────────────────────────────
length_mm           → log + volume
width_mm            → log
height_mm           → log
volume_cm3          → log (if not provided: L×W×H/1000)
surface_area_cm2    → log + area-to-volume ratio
wall_thickness_mm   → as-is + thin-wall flag (<2mm)
num_holes           → as-is + sq
num_threads         → as-is
bounding_box_ratio  → length/max(width,height): shape compactness
aspect_ratio        → max_dim / min_dim
complexity_score    → composite: holes + threads + thin_wall + tolerance
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class GeometryFeatureTransformer(BaseEstimator, TransformerMixin):
    def __init__(self, thin_wall_threshold_mm: float = 2.0) -> None:
        self.thin_wall_threshold_mm = thin_wall_threshold_mm
        self._dim_means: dict[str, float] = {}

    def fit(self, X: pd.DataFrame, y=None) -> "GeometryFeatureTransformer":
        X = _df(X)
        for col in ["length_mm", "width_mm", "height_mm", "wall_thickness_mm"]:
            if col in X.columns:
                self._dim_means[col] = float(X[col].median())
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X = _df(X).copy()

        for col, med in self._dim_means.items():
            if col in X.columns:
                X[col] = X[col].fillna(med)

        dims = ["length_mm", "width_mm", "height_mm"]

        # Derive volume if not present
        if "volume_cm3" not in X.columns:
            if all(c in X.columns for c in dims):
                X["volume_cm3"] = X["length_mm"] * X["width_mm"] * X["height_mm"] / 1000.0
            else:
                X["volume_cm3"] = np.nan

        X["log_volume_cm3"] = np.log1p(X["volume_cm3"].clip(0))

        # Log dimensions
        for col in dims:
            if col in X.columns:
                X[f"log_{col}"] = np.log1p(X[col].clip(0))

        # Bounding box volume (may differ from part volume due to pockets/holes)
        if all(c in X.columns for c in dims):
            X["bbox_volume_cm3"] = (
                X["length_mm"] * X["width_mm"] * X["height_mm"] / 1000.0
            )
            if "volume_cm3" in X.columns:
                X["material_utilisation"] = (
                    X["volume_cm3"] / X["bbox_volume_cm3"].clip(lower=1e-6)
                ).clip(0, 1)

        # Surface area log
        if "surface_area_cm2" in X.columns:
            X["log_surface_area_cm2"] = np.log1p(X["surface_area_cm2"].clip(0))
            if "volume_cm3" in X.columns:
                X["area_to_volume_ratio"] = (
                    X["surface_area_cm2"] / X["volume_cm3"].clip(1e-6)
                )

        # Aspect ratio (max_dim / min_dim) — high ratio → slender / risky
        if all(c in X.columns for c in dims):
            dim_matrix = X[dims].clip(lower=0.1)
            X["aspect_ratio"] = dim_matrix.max(axis=1) / dim_matrix.min(axis=1)
            X["log_aspect_ratio"] = np.log(X["aspect_ratio"])

            # Compactness: how close to a cube
            vol = dim_matrix.prod(axis=1)
            X["shape_compactness"] = vol ** (1 / 3) / dim_matrix.mean(axis=1)

        # Thin-wall indicator
        if "wall_thickness_mm" in X.columns:
            X["is_thin_wall"] = (
                X["wall_thickness_mm"] < self.thin_wall_threshold_mm
            ).astype(np.int8)
            X["log_wall_thickness"] = np.log1p(X["wall_thickness_mm"].clip(0))

        # Feature count interactions
        if "num_holes" in X.columns:
            X["num_holes_sq"] = X["num_holes"].fillna(0) ** 2

        if "num_threads" in X.columns:
            X["num_threads"] = X["num_threads"].fillna(0)

        if "num_pockets" in X.columns:
            X["num_pockets"] = X["num_pockets"].fillna(0)

        # Composite geometric complexity score
        complexity_cols = {
            "num_holes": 1.0,
            "num_threads": 1.5,
            "num_pockets": 2.0,
            "is_thin_wall": 3.0,
        }
        score = pd.Series(np.zeros(len(X)), index=X.index)
        for col, weight in complexity_cols.items():
            if col in X.columns:
                score += X[col].fillna(0) * weight
        X["geometric_complexity_score"] = score

        return X


def _df(X) -> pd.DataFrame:
    return X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
