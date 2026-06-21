"""
Training pipeline with LightGBM + XGBoost ensemble and MLflow tracking.

Flow:
  1. Load data
  2. Feature engineering (FeatureStore)
  3. Train/val/test split (time-aware)
  4. HPO with Optuna (optional)
  5. Final training of LGB + XGB
  6. Ensemble via weighted average
  7. Evaluate + log to MLflow
  8. Register best model in MLflow Model Registry
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog
from sklearn.model_selection import KFold, TimeSeriesSplit

from ..config import MLSettings, get_settings
from ..evaluation.metrics import RegressionMetrics, compute_all
from ..evaluation.report import build_segment_report, log_to_mlflow
from ..features.pipeline import FeatureStore, build_feature_pipeline

log = structlog.get_logger(__name__)


@dataclass
class TrainResult:
    lgbm_metrics: RegressionMetrics | None = None
    xgb_metrics: RegressionMetrics | None = None
    ensemble_metrics: RegressionMetrics | None = None
    best_model_uri: str = ""
    run_id: str = ""
    feature_names: list[str] = field(default_factory=list)
    lgbm_importances: dict[str, float] = field(default_factory=dict)
    xgb_importances: dict[str, float] = field(default_factory=dict)
    training_time_s: float = 0.0


class CostPredictionTrainer:
    def __init__(self, settings: MLSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def train(
        self,
        df: pd.DataFrame,
        *,
        run_name: str | None = None,
        hpo: bool = False,
        register: bool = True,
    ) -> TrainResult:
        import mlflow
        import mlflow.lightgbm
        import mlflow.xgboost

        mlflow.set_tracking_uri(self.settings.mlflow_tracking_uri)
        mlflow.set_experiment(self.settings.mlflow_experiment_name)

        t0 = time.time()
        result = TrainResult()

        with mlflow.start_run(run_name=run_name or _run_name()) as run:
            result.run_id = run.info.run_id
            log.info("training_start", run_id=result.run_id)

            # ── Split ──────────────────────────────────────────────────────
            target = self.settings.target_column
            X, y = _split_xy(df, target)
            X_train, X_val, X_test, y_train, y_val, y_test = _temporal_split(
                X, y,
                test_frac=self.settings.test_size,
                val_frac=self.settings.val_size,
            )
            mlflow.log_params({
                "n_train": len(X_train),
                "n_val":   len(X_val),
                "n_test":  len(X_test),
                "model_type": self.settings.model_type,
            })

            # ── Feature engineering ────────────────────────────────────────
            feature_pipe = build_feature_pipeline(self.settings)
            store = FeatureStore(feature_pipe)
            X_train_enc = store.fit_transform(X_train, y_train.values)
            X_val_enc   = store.transform(X_val)
            X_test_enc  = store.transform(X_test)
            result.feature_names = store.feature_names
            mlflow.log_param("n_features", len(result.feature_names))
            mlflow.log_dict(
                {"features": result.feature_names}, "feature_names.json"
            )

            # ── HPO ────────────────────────────────────────────────────────
            lgbm_params = dict(self.settings.lgbm_params)
            xgb_params  = dict(self.settings.xgb_params)
            if hpo:
                log.info("hpo_start", n_trials=self.settings.n_trials)
                lgbm_params, xgb_params = self._run_hpo(
                    X_train_enc, y_train.values,
                    X_val_enc, y_val.values,
                )
            mlflow.log_params({f"lgbm_{k}": v for k, v in lgbm_params.items() if not isinstance(v, (list, dict))})
            mlflow.log_params({f"xgb_{k}":  v for k, v in xgb_params.items()  if not isinstance(v, (list, dict))})

            # ── Train models ───────────────────────────────────────────────
            lgbm_model = xgb_model = None

            if self.settings.model_type in ("lgbm", "ensemble"):
                lgbm_model, lgbm_imp = _train_lgbm(
                    X_train_enc, y_train.values,
                    X_val_enc,   y_val.values,
                    params=lgbm_params,
                    feature_names=result.feature_names,
                )
                result.lgbm_importances = lgbm_imp
                lgbm_pred_test = lgbm_model.predict(X_test_enc)
                result.lgbm_metrics = compute_all(y_test.values, lgbm_pred_test)
                log_to_mlflow(run, result.lgbm_metrics, y_test.values, lgbm_pred_test, prefix="lgbm_test")
                mlflow.lightgbm.log_model(lgbm_model, "lgbm_model")
                log.info("lgbm_trained", **_metric_summary(result.lgbm_metrics))

            if self.settings.model_type in ("xgboost", "ensemble"):
                xgb_model, xgb_imp = _train_xgb(
                    X_train_enc, y_train.values,
                    X_val_enc,   y_val.values,
                    params=xgb_params,
                    feature_names=result.feature_names,
                )
                result.xgb_importances = xgb_imp
                xgb_pred_test = xgb_model.predict(X_test_enc)
                result.xgb_metrics = compute_all(y_test.values, xgb_pred_test)
                log_to_mlflow(run, result.xgb_metrics, y_test.values, xgb_pred_test, prefix="xgb_test")
                mlflow.xgboost.log_model(xgb_model, "xgb_model")
                log.info("xgb_trained", **_metric_summary(result.xgb_metrics))

            # ── Ensemble ───────────────────────────────────────────────────
            if self.settings.model_type == "ensemble" and lgbm_model and xgb_model:
                w_lgbm, w_xgb = self.settings.ensemble_weights
                ens_pred = w_lgbm * lgbm_pred_test + w_xgb * xgb_pred_test
                result.ensemble_metrics = compute_all(y_test.values, ens_pred)
                log_to_mlflow(run, result.ensemble_metrics, y_test.values, ens_pred, prefix="ensemble_test")
                mlflow.log_params({"ensemble_w_lgbm": w_lgbm, "ensemble_w_xgb": w_xgb})
                log.info("ensemble_metrics", **_metric_summary(result.ensemble_metrics))

            # ── Segment report ─────────────────────────────────────────────
            if "material_class" in X_test.columns:
                seg_report = build_segment_report(
                    y_test.values,
                    ens_pred if result.ensemble_metrics else lgbm_pred_test,
                    X_test["material_class"].reset_index(drop=True),
                )
                mlflow.log_table(seg_report, "segment_report.json")

            # ── Feature importance ─────────────────────────────────────────
            if result.lgbm_importances:
                imp_df = pd.DataFrame.from_dict(
                    result.lgbm_importances, orient="index", columns=["importance"]
                ).sort_values("importance", ascending=False)
                mlflow.log_table(imp_df.reset_index().rename(columns={"index": "feature"}),
                                 "lgbm_feature_importance.json")

            # ── Register ───────────────────────────────────────────────────
            best_metrics = (
                result.ensemble_metrics
                or result.lgbm_metrics
                or result.xgb_metrics
            )
            if register and best_metrics:
                model_artifact = "lgbm_model" if lgbm_model else "xgb_model"
                model_uri = f"runs:/{run.info.run_id}/{model_artifact}"
                result.best_model_uri = model_uri
                if register:
                    from ..registry.model_registry import ModelRegistry
                    registry = ModelRegistry(self.settings)
                    registry.register(
                        run_id=run.info.run_id,
                        artifact_path=model_artifact,
                        metrics=best_metrics,
                    )

            result.training_time_s = time.time() - t0
            mlflow.log_metric("training_time_s", result.training_time_s)
            log.info("training_done", run_id=result.run_id, time_s=result.training_time_s)

        return result

    def cross_validate(
        self,
        df: pd.DataFrame,
        *,
        n_splits: int | None = None,
        time_series: bool = True,
    ) -> list[RegressionMetrics]:
        """K-fold or time-series cross-validation."""
        n_splits = n_splits or self.settings.cv_folds
        target = self.settings.target_column
        X, y = _split_xy(df, target)

        cv = TimeSeriesSplit(n_splits=n_splits) if time_series else KFold(n_splits=n_splits, shuffle=True, random_state=self.settings.random_seed)
        fold_metrics: list[RegressionMetrics] = []

        for fold, (train_idx, val_idx) in enumerate(cv.split(X)):
            log.info("cv_fold", fold=fold + 1, train=len(train_idx), val=len(val_idx))
            X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
            y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]

            store = FeatureStore(build_feature_pipeline(self.settings))
            X_tr_enc = store.fit_transform(X_tr, y_tr.values)
            X_va_enc = store.transform(X_va)

            model, _ = _train_lgbm(
                X_tr_enc, y_tr.values,
                X_va_enc, y_va.values,
                params=dict(self.settings.lgbm_params),
            )
            preds = model.predict(X_va_enc)
            m = compute_all(y_va.values, preds)
            fold_metrics.append(m)
            log.info("fold_result", fold=fold + 1, mape=f"{m.mape:.2%}", mae=f"{m.mae:.2f}")

        avg_mape = np.mean([m.mape for m in fold_metrics])
        log.info("cv_done", avg_mape=f"{avg_mape:.2%}", folds=len(fold_metrics))
        return fold_metrics

    def _run_hpo(
        self,
        X_train: np.ndarray, y_train: np.ndarray,
        X_val: np.ndarray, y_val: np.ndarray,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def lgbm_objective(trial):
            import lightgbm as lgb
            params = {
                "objective": "regression",
                "metric": "rmse",
                "verbose": -1,
                "num_leaves": trial.suggest_int("num_leaves", 20, 150),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
                "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
                "bagging_freq": trial.suggest_int("bagging_freq", 1, 10),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
                "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
                "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
                "n_estimators": 500,
                "early_stopping_rounds": 30,
            }
            model = lgb.LGBMRegressor(**params)
            model.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(30, verbose=False)])
            preds = model.predict(X_val)
            from ..evaluation.metrics import wmape
            return wmape(y_val, preds)

        study = optuna.create_study(direction="minimize", study_name="lgbm_hpo")
        study.optimize(lgbm_objective, n_trials=self.settings.n_trials, show_progress_bar=False)
        best_lgbm = {**self.settings.lgbm_params, **study.best_params}

        return best_lgbm, dict(self.settings.xgb_params)


# ── Standalone train functions ─────────────────────────────────────────────

def _train_lgbm(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val: np.ndarray, y_val: np.ndarray,
    params: dict[str, Any],
    feature_names: list[str] | None = None,
) -> tuple[Any, dict[str, float]]:
    import lightgbm as lgb

    early = params.pop("early_stopping_rounds", 50)
    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        feature_name=feature_names or "auto",
        callbacks=[
            lgb.early_stopping(early, verbose=False),
            lgb.log_evaluation(period=100),
        ],
    )
    importance = dict(zip(
        feature_names or [f"f{i}" for i in range(X_train.shape[1])],
        model.feature_importances_.tolist(),
    ))
    return model, importance


def _train_xgb(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val: np.ndarray, y_val: np.ndarray,
    params: dict[str, Any],
    feature_names: list[str] | None = None,
) -> tuple[Any, dict[str, float]]:
    import xgboost as xgb

    early = params.pop("early_stopping_rounds", 50)
    model = xgb.XGBRegressor(**params, early_stopping_rounds=early)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    scores = model.get_booster().get_score(importance_type="gain")
    names = feature_names or [f"f{i}" for i in range(X_train.shape[1])]
    importance = {n: float(scores.get(f"f{i}", 0)) for i, n in enumerate(names)}
    return model, importance


def _split_xy(df: pd.DataFrame, target: str) -> tuple[pd.DataFrame, pd.Series]:
    assert target in df.columns, f"Target column '{target}' not in DataFrame"
    return df.drop(columns=[target]), df[target]


def _temporal_split(
    X: pd.DataFrame, y: pd.Series,
    test_frac: float, val_frac: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame,
           pd.Series, pd.Series, pd.Series]:
    n = len(X)
    n_test = max(1, int(n * test_frac))
    n_val  = max(1, int(n * val_frac))
    n_train = n - n_test - n_val

    return (
        X.iloc[:n_train], X.iloc[n_train:n_train + n_val], X.iloc[n_train + n_val:],
        y.iloc[:n_train], y.iloc[n_train:n_train + n_val], y.iloc[n_train + n_val:],
    )


def _run_name() -> str:
    from datetime import datetime
    return f"cost-pred-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"


def _metric_summary(m: RegressionMetrics) -> dict[str, str]:
    return {"mape": f"{m.mape:.2%}", "mae": f"{m.mae:.2f}", "rmse": f"{m.rmse:.2f}", "r2": f"{m.r2:.4f}"}
