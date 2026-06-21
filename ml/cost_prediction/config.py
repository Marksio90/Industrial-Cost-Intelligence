from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MLSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ML_",
        env_file=".env",
        case_sensitive=False,
    )

    # Paths
    data_dir: Path = Path("data")
    model_dir: Path = Path("models")
    artifact_dir: Path = Path("artifacts")

    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment_name: str = "ici-cost-prediction"
    mlflow_registry_name: str = "cost-predictor"

    # Training
    target_column: str = "unit_cost_eur"
    test_size: float = 0.2
    val_size: float = 0.1
    random_seed: int = 42
    cv_folds: int = 5
    n_trials: int = 50                  # Optuna HPO trials

    # Model selection
    model_type: Literal["lgbm", "xgboost", "ensemble"] = "ensemble"
    ensemble_weights: list[float] = Field(default=[0.6, 0.4])  # lgbm, xgb

    # Drift detection
    drift_reference_window: int = 1000  # samples in reference window
    drift_detection_window: int = 200   # samples in detection window
    drift_psi_threshold: float = 0.2    # PSI > 0.2 → significant drift
    drift_ks_alpha: float = 0.05        # KS test significance level
    mape_degradation_threshold: float = 0.05  # 5% MAPE increase → retrain

    # Retraining
    retrain_schedule_cron: str = "0 2 * * 1"  # Monday 02:00 UTC
    min_samples_for_retrain: int = 500
    retrain_lookback_days: int = 90

    # Inference
    inference_host: str = "0.0.0.0"
    inference_port: int = 8002
    prediction_cache_ttl_s: int = 300

    # Feature flags
    use_target_encoding: bool = True
    use_polynomial_features: bool = False
    max_categories: int = 50

    # LightGBM defaults
    lgbm_params: dict[str, Any] = Field(default_factory=lambda: {
        "objective": "regression",
        "metric": ["rmse", "mae"],
        "num_leaves": 63,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_child_samples": 20,
        "n_estimators": 1000,
        "early_stopping_rounds": 50,
        "verbose": -1,
    })

    # XGBoost defaults
    xgb_params: dict[str, Any] = Field(default_factory=lambda: {
        "objective": "reg:squarederror",
        "eval_metric": ["rmse", "mae"],
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 1000,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5,
        "early_stopping_rounds": 50,
        "verbosity": 0,
    })


@lru_cache
def get_settings() -> MLSettings:
    return MLSettings()  # type: ignore[call-arg]
