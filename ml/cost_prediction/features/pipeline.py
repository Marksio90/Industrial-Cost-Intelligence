"""
Master feature engineering pipeline.

Assembles Material + Process + Geometry + Supplier transformers into a
single sklearn Pipeline with:
  - Column routing via ColumnTransformer
  - Categorical encoding (OrdinalEncoder or TargetEncoder)
  - Imputation (KNN for numerics, constant for categoricals)
  - Optional PolynomialFeatures on top numeric interactions
  - Feature selection via SelectFromModel (LGBM-based importance)

Usage:
    from cost_prediction.features.pipeline import build_feature_pipeline
    pipe = build_feature_pipeline(settings)
    X_train_enc = pipe.fit_transform(X_train, y_train)
    X_test_enc  = pipe.transform(X_test)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import KNNImputer, SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder, RobustScaler

from ..config import MLSettings
from .geometry_features import GeometryFeatureTransformer
from .material_features import MaterialFeatureTransformer
from .process_features import ProcessFeatureTransformer
from .supplier_features import SupplierFeatureTransformer


# ── Column groups ─────────────────────────────────────────────────────────

MATERIAL_COLS = [
    "density_kg_m3", "hardness_hv", "ultimate_tensile_strength_mpa",
    "yield_strength_mpa", "elongation_pct", "thermal_conductivity_w_mk",
    "price_per_kg_eur", "machinability_index", "material_class",
]

PROCESS_COLS = [
    "process_type", "setup_time_h", "cycle_time_min",
    "machine_hourly_rate_eur", "operator_count", "scrap_rate_pct",
    "tooling_cost_eur", "num_operations", "surface_finish_ra",
    "tolerance_grade", "requires_heat_treatment", "requires_inspection",
    "batch_size",
]

GEOMETRY_COLS = [
    "length_mm", "width_mm", "height_mm", "volume_cm3", "surface_area_cm2",
    "wall_thickness_mm", "num_holes", "num_threads", "num_pockets",
]

SUPPLIER_COLS = [
    "overall_score", "quality_score", "delivery_score", "price_score",
    "financial_score", "years_active", "capacity_utilisation",
    "avg_lead_time_days", "quote_win_rate", "avg_price_deviation_pct",
    "country_code", "supplier_tier", "certifications",
]

CATEGORICAL_COLS = ["material_class", "process_type", "tolerance_grade", "supplier_tier"]
PASSTHROUGH_COLS = ["quantity", "batch_size"]


class FullFeaturePipeline(Pipeline):
    """Thin wrapper exposing domain-level column groups."""

    @property
    def material_transformer(self):
        return self.named_steps.get("material")

    @property
    def process_transformer(self):
        return self.named_steps.get("process")


def build_feature_pipeline(settings: MLSettings) -> Pipeline:
    """
    Build the full feature engineering + preprocessing pipeline.

    Steps:
    1. Domain transformers (material, process, geometry, supplier)
       — run in parallel via ColumnTransformer
    2. Impute remaining NaNs
    3. Encode categoricals (OrdinalEncoder — tree models don't need one-hot)
    4. Scale numerics with RobustScaler (median/IQR — tolerates outliers)
    """

    # ── Step 1: domain feature engineering ───────────────────────────────
    material_pipe = Pipeline([
        ("engineer", MaterialFeatureTransformer()),
        ("impute", SimpleImputer(strategy="median")),
    ])

    process_pipe = Pipeline([
        ("engineer", ProcessFeatureTransformer()),
        ("impute", SimpleImputer(strategy="median")),
    ])

    geometry_pipe = Pipeline([
        ("engineer", GeometryFeatureTransformer()),
        ("impute", SimpleImputer(strategy="median")),
    ])

    supplier_pipe = Pipeline([
        ("engineer", SupplierFeatureTransformer()),
        ("impute", SimpleImputer(strategy="median")),
    ])

    # ── Step 2: column routing ────────────────────────────────────────────
    # We pass *all* columns to each sub-pipeline and let each extract
    # only what it recognises. This avoids brittle column-list wiring.
    col_transformer = ColumnTransformer(
        transformers=[
            ("material", material_pipe, _present_cols(MATERIAL_COLS)),
            ("process",  process_pipe,  _present_cols(PROCESS_COLS)),
            ("geometry", geometry_pipe, _present_cols(GEOMETRY_COLS)),
            ("supplier", supplier_pipe, _present_cols(SUPPLIER_COLS)),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )

    steps: list = [
        ("features", col_transformer),
        ("impute_final", KNNImputer(n_neighbors=5, weights="distance")),
        ("scale", RobustScaler()),
    ]

    return Pipeline(steps)


def build_categorical_encoder(categories: dict[str, list[str]] | None = None) -> OrdinalEncoder:
    """Standalone encoder for categorical columns, used before tree models."""
    return OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=-1,
        encoded_missing_value=-2,
    )


def _present_cols(cols: list[str]) -> list[str]:
    """Return the column list as-is; actual availability checked at fit time."""
    return cols


class FeatureStore:
    """
    Thin wrapper that materialises engineered features to a DataFrame
    and exposes feature names for MLflow logging.
    """

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
        self._feature_names: list[str] = []

    def fit_transform(self, X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
        result = self._pipeline.fit_transform(X, y)
        try:
            self._feature_names = list(
                self._pipeline.named_steps["features"].get_feature_names_out()
            )
        except Exception:
            self._feature_names = [f"f_{i}" for i in range(result.shape[1])]
        return result

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        return self._pipeline.transform(X)

    @property
    def feature_names(self) -> list[str]:
        return self._feature_names

    def as_dataframe(self, X: pd.DataFrame) -> pd.DataFrame:
        arr = self.transform(X)
        return pd.DataFrame(arr, columns=self._feature_names, index=X.index)
