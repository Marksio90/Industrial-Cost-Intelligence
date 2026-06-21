"""
FastAPI inference server for cost prediction.

Endpoints:
  POST /predict           — single prediction
  POST /predict/batch     — batch up to 500 items
  GET  /model/info        — current production model metadata
  GET  /model/versions    — all registered versions
  POST /model/reload      — hot-reload production model
  GET  /drift/report      — latest drift analysis
  POST /drift/analyze     — run drift analysis on provided window
  POST /feedback          — submit actual cost for monitoring
  GET  /health
  GET  /metrics           — Prometheus metrics
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.responses import ORJSONResponse
from prometheus_client import Counter, Histogram, Summary, generate_latest

from ..config import MLSettings, get_settings
from ..drift.detector import DriftDetector, DriftReport
from ..registry.model_registry import ModelRegistry
from ..retraining.scheduler import RetrainingScheduler
from .predictor import CostPredictor
from .schemas import (
    BatchPredictionRequest, BatchPredictionResponse,
    CostPredictionRequest, CostPredictionResponse,
    ModelInfoResponse,
)

log = structlog.get_logger(__name__)

# ── Prometheus metrics ─────────────────────────────────────────────────────

_PRED_LATENCY = Histogram(
    "cost_prediction_latency_seconds",
    "Prediction latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
_PRED_COUNTER = Counter("cost_prediction_total", "Total predictions", ["cached"])
_PRED_VALUE   = Summary("cost_prediction_eur", "Predicted cost distribution")
_DRIFT_ALERTS = Counter("drift_alerts_total", "Drift alerts", ["level"])

# ── App factory ────────────────────────────────────────────────────────────

def create_inference_app(
    settings: MLSettings | None = None,
    predictor: CostPredictor | None = None,
    retrain_scheduler: RetrainingScheduler | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    _predictor = predictor or CostPredictor(settings)
    _registry  = ModelRegistry(settings)
    _scheduler = retrain_scheduler
    _drift_detector = DriftDetector(
        psi_threshold_high=settings.drift_psi_threshold,
    )
    _drift_report: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("inference_api_startup")
        _predictor.load()
        yield
        log.info("inference_api_shutdown")

    app = FastAPI(
        title="ICI Cost Prediction API",
        version="1.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # ── Single prediction ─────────────────────────────────────────────────

    @app.post(
        "/predict",
        response_model=CostPredictionResponse,
        summary="Predict manufacturing cost",
    )
    async def predict(body: CostPredictionRequest) -> CostPredictionResponse:
        req_id = body.request_id or str(uuid.uuid4())
        features = body.to_flat_dict()

        t0 = time.monotonic()
        with _PRED_LATENCY.time():
            result = _predictor.predict(
                features,
                include_ci=True,
                include_shap=False,
            )
        latency_ms = (time.monotonic() - t0) * 1000

        _PRED_COUNTER.labels(cached=str(result.cached)).inc()
        _PRED_VALUE.observe(result.value)

        # Feed to scheduler for monitoring (non-blocking)
        if _scheduler:
            _scheduler.record_prediction(features, result.value)

        ci = None
        if result.lower is not None and result.upper is not None:
            from .schemas import ConfidenceInterval
            ci = ConfidenceInterval(
                lower=result.lower, upper=result.upper, confidence_level=0.90
            )

        return CostPredictionResponse(
            request_id=req_id,
            predicted_cost_eur=round(result.value, 4),
            confidence_interval=ci,
            model_version=result.model_version,
            feature_contributions=result.feature_contributions,
            latency_ms=round(latency_ms, 2),
            cached=result.cached,
        )

    # ── Batch prediction ──────────────────────────────────────────────────

    @app.post(
        "/predict/batch",
        response_model=BatchPredictionResponse,
        summary="Batch cost prediction (up to 500 items)",
    )
    async def predict_batch(body: BatchPredictionRequest) -> BatchPredictionResponse:
        t0 = time.monotonic()
        features_list = [item.to_flat_dict() for item in body.items]

        results = _predictor.predict_batch(features_list, include_ci=False)
        total_ms = (time.monotonic() - t0) * 1000

        responses = []
        for item, result in zip(body.items, results):
            _PRED_COUNTER.labels(cached="False").inc()
            responses.append(CostPredictionResponse(
                request_id=item.request_id,
                predicted_cost_eur=round(result.value, 4),
                model_version=result.model_version,
                latency_ms=round(result.latency_ms, 2),
                cached=False,
            ))

        return BatchPredictionResponse(
            predictions=responses,
            n_items=len(responses),
            total_latency_ms=round(total_ms, 2),
        )

    # ── Model info ────────────────────────────────────────────────────────

    @app.get("/model/info", response_model=ModelInfoResponse)
    async def model_info() -> ModelInfoResponse:
        versions = _registry.list_versions()
        prod = next((v for v in versions if v.stage == "Production"), None)
        return ModelInfoResponse(
            model_name=settings.mlflow_registry_name,
            version=prod.version if prod else "none",
            stage=prod.stage if prod else "none",
            mape=prod.mape if prod else None,
            rmse=prod.rmse if prod else None,
            n_features=len(_predictor._feature_names),
        )

    @app.get("/model/versions")
    async def model_versions() -> list[dict[str, Any]]:
        return [
            {"version": v.version, "stage": v.stage, "mape": v.mape, "rmse": v.rmse}
            for v in _registry.list_versions()
        ]

    @app.post("/model/reload", status_code=status.HTTP_202_ACCEPTED)
    async def reload_model(background_tasks: BackgroundTasks):
        def _reload():
            _predictor._model = None
            _predictor.load()
            log.info("model_reloaded")
        background_tasks.add_task(_reload)
        return {"status": "reload_scheduled"}

    # ── Drift ─────────────────────────────────────────────────────────────

    @app.get("/drift/report")
    async def drift_report() -> dict[str, Any]:
        return _drift_report or {"status": "no_report_yet"}

    @app.post("/drift/analyze")
    async def analyze_drift(background_tasks: BackgroundTasks):
        """Trigger async drift analysis on the current prediction buffer."""
        def _analyze():
            nonlocal _drift_report
            if _scheduler and _scheduler._prediction_buffer:
                import pandas as pd
                import numpy as np
                features_df = pd.DataFrame(
                    [p["features"] for p in _scheduler._prediction_buffer]
                )
                predictions = np.array(
                    [p["prediction"] for p in _scheduler._prediction_buffer]
                )
                report = _drift_detector.detect(features_df, predictions)
                _drift_report = report.to_dict()
                _DRIFT_ALERTS.labels(level=report.overall_level.value).inc()
        background_tasks.add_task(_analyze)
        return {"status": "drift_analysis_scheduled"}

    # ── Feedback ──────────────────────────────────────────────────────────

    @app.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
    async def submit_feedback(
        request_id: str,
        actual_cost_eur: float,
        features: dict[str, Any] | None = None,
    ) -> None:
        if _scheduler and features:
            _scheduler.record_prediction(
                features or {},
                prediction=0.0,
                actual=actual_cost_eur,
            )

    # ── Health / Metrics ──────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_version": _predictor._model_version,
            "model_loaded": _predictor._model is not None,
        }

    @app.get("/metrics")
    async def metrics():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(generate_latest(), media_type="text/plain")

    return app


def serve(settings: MLSettings | None = None) -> None:
    settings = settings or get_settings()
    app = create_inference_app(settings)
    uvicorn.run(
        app,
        host=settings.inference_host,
        port=settings.inference_port,
        workers=1,
        log_level="info",
    )
