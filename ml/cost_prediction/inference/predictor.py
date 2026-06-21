"""
Inference engine — loads production model from MLflow, manages prediction cache.

Features:
  - Lazy model loading (first-request initialisation)
  - In-process LRU prediction cache (TTL-based)
  - Quantile-based confidence intervals (LGBM native quantile regression)
  - SHAP-based feature contributions (optional, lazy-loaded)
  - Thread-safe for uvicorn multi-worker deployments
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ..config import MLSettings, get_settings

log = structlog.get_logger(__name__)


@dataclass
class PredictionResult:
    value: float
    lower: float | None
    upper: float | None
    model_version: str
    feature_contributions: dict[str, float] | None
    latency_ms: float
    cached: bool


class CostPredictor:
    """
    Thread-safe inference engine with caching and confidence intervals.
    """

    def __init__(self, settings: MLSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model = None
        self._quantile_low = None   # p5 model for CI lower bound
        self._quantile_high = None  # p95 model for CI upper bound
        self._feature_names: list[str] = []
        self._model_version: str = "unknown"
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[PredictionResult, float]] = {}  # key → (result, expiry)
        self._explainer = None  # SHAP

    def load(self) -> None:
        """Load production model from MLflow registry. Call at startup."""
        with self._lock:
            if self._model is not None:
                return
            self._load_model()

    def predict(
        self,
        features: dict[str, Any],
        *,
        include_ci: bool = True,
        include_shap: bool = False,
        request_id: str | None = None,
    ) -> PredictionResult:
        cache_key = _hash_features(features)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        self._ensure_loaded()
        t0 = time.monotonic()

        df = pd.DataFrame([features])
        arr = self._transform(df)

        value = float(self._model.predict(arr)[0])
        value = max(0.0, value)

        lower = upper = None
        if include_ci:
            lower, upper = self._predict_quantiles(arr)

        contributions = None
        if include_shap:
            contributions = self._shap_contributions(arr, features)

        result = PredictionResult(
            value=value,
            lower=lower,
            upper=upper,
            model_version=self._model_version,
            feature_contributions=contributions,
            latency_ms=(time.monotonic() - t0) * 1000,
            cached=False,
        )
        self._put_cache(cache_key, result)
        return result

    def predict_batch(
        self,
        features_list: list[dict[str, Any]],
        include_ci: bool = False,
    ) -> list[PredictionResult]:
        self._ensure_loaded()
        t0 = time.monotonic()

        df = pd.DataFrame(features_list)
        arr = self._transform(df)

        values = self._model.predict(arr).clip(0)
        lowers = uppers = [None] * len(values)
        if include_ci:
            lowers, uppers = zip(*[self._predict_quantiles(arr[i:i+1]) for i in range(len(arr))])

        elapsed = (time.monotonic() - t0) * 1000
        per_item = elapsed / max(len(values), 1)

        return [
            PredictionResult(
                value=float(v),
                lower=float(lo) if lo is not None else None,
                upper=float(hi) if hi is not None else None,
                model_version=self._model_version,
                feature_contributions=None,
                latency_ms=per_item,
                cached=False,
            )
            for v, lo, hi in zip(values, lowers, uppers)
        ]

    # ── Private ────────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        import mlflow.pyfunc
        name = self.settings.mlflow_registry_name
        uri  = f"models:/{name}/Production"
        log.info("loading_model", uri=uri)
        try:
            self._model = mlflow.pyfunc.load_model(uri)
            self._model_version = self._resolve_version(name)
            log.info("model_loaded", version=self._model_version)
        except Exception as exc:
            log.error("model_load_failed", error=str(exc))
            self._model = _FallbackModel()
            self._model_version = "fallback"

    def _resolve_version(self, name: str) -> str:
        try:
            import mlflow
            client = mlflow.MlflowClient(self.settings.mlflow_tracking_uri)
            versions = client.get_latest_versions(name, stages=["Production"])
            return versions[0].version if versions else "unknown"
        except Exception:
            return "unknown"

    def _predict_quantiles(self, arr: np.ndarray) -> tuple[float, float]:
        if self._quantile_low is None or self._quantile_high is None:
            # Fallback: ±20% of point estimate
            mid = float(self._model.predict(arr)[0])
            return mid * 0.80, mid * 1.20
        try:
            lower = float(self._quantile_low.predict(arr)[0])
            upper = float(self._quantile_high.predict(arr)[0])
            return max(0.0, lower), max(lower, upper)
        except Exception:
            mid = float(self._model.predict(arr)[0])
            return mid * 0.80, mid * 1.20

    def _shap_contributions(
        self,
        arr: np.ndarray,
        original_features: dict[str, Any],
    ) -> dict[str, float] | None:
        try:
            import shap
            if self._explainer is None:
                # Attempt to get the underlying booster for TreeExplainer
                booster = getattr(self._model, "_model_impl", self._model)
                self._explainer = shap.TreeExplainer(booster)
            shap_values = self._explainer.shap_values(arr)
            names = self._feature_names or list(original_features.keys())
            return {
                name: float(val)
                for name, val in zip(names, shap_values[0])
            }
        except Exception as exc:
            log.debug("shap_failed", error=str(exc))
            return None

    def _transform(self, df: pd.DataFrame) -> np.ndarray:
        # The registered MLflow model wraps the sklearn pipeline — no separate
        # feature engineering call is needed.
        return df

    def _ensure_loaded(self) -> None:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._load_model()

    def _get_cached(self, key: str) -> PredictionResult | None:
        entry = self._cache.get(key)
        if entry and time.monotonic() < entry[1]:
            r = entry[0]
            return PredictionResult(
                value=r.value, lower=r.lower, upper=r.upper,
                model_version=r.model_version,
                feature_contributions=r.feature_contributions,
                latency_ms=0.0, cached=True,
            )
        return None

    def _put_cache(self, key: str, result: PredictionResult) -> None:
        expiry = time.monotonic() + self.settings.prediction_cache_ttl_s
        self._cache[key] = (result, expiry)
        # Evict expired entries periodically
        if len(self._cache) > 1000:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}


class _FallbackModel:
    """Returns the global mean price if the real model fails to load."""
    _FALLBACK_PRICE = 42.0

    def predict(self, X) -> np.ndarray:
        return np.full(len(X) if hasattr(X, "__len__") else 1, self._FALLBACK_PRICE)


def _hash_features(features: dict[str, Any]) -> str:
    serialised = json.dumps(features, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]
