"""
Automated retraining scheduler.

Triggers:
  1. Schedule-based (cron) — weekly by default
  2. Drift-triggered    — when DriftDetector raises HIGH alert
  3. MAPE-triggered     — when rolling MAPE degrades > threshold
  4. Volume-triggered   — when N new labelled samples arrive

The scheduler exposes:
  - RetrainingScheduler.check_and_retrain() — call from cron/worker
  - RetrainingScheduler.trigger_now()       — on-demand trigger
  - RetrainingScheduler.record_prediction() — feed inference data back
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ..config import MLSettings, get_settings
from ..drift.detector import DriftDetector, DriftLevel, MAPEDriftMonitor
from ..evaluation.metrics import mape as compute_mape

log = structlog.get_logger(__name__)


@dataclass
class RetrainTrigger:
    reason: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrainingResult:
    triggered: bool
    trigger: RetrainTrigger | None
    training_result: Any | None = None  # TrainResult
    error: str | None = None


class RetrainingScheduler:
    """
    Monitors production model health and triggers retraining when needed.
    Designed to be called from a cron job or async background task.
    """

    def __init__(
        self,
        settings: MLSettings | None = None,
        data_loader=None,        # callable(lookback_days) → pd.DataFrame
        trainer=None,             # CostPredictionTrainer instance
    ) -> None:
        self.settings = settings or get_settings()
        self._data_loader = data_loader
        self._trainer = trainer

        # Monitoring state
        self._drift_detector = DriftDetector(
            psi_threshold_high=self.settings.drift_psi_threshold,
            ks_alpha=self.settings.drift_ks_alpha,
        )
        self._mape_monitor: MAPEDriftMonitor | None = None
        self._prediction_buffer: list[dict[str, Any]] = []
        self._last_retrain: datetime | None = None
        self._baseline_mape: float | None = None

    def set_baseline(self, reference_df: pd.DataFrame, baseline_mape: float) -> None:
        """Call once after initial training to establish reference distribution."""
        self._drift_detector.set_reference(reference_df)
        self._baseline_mape = baseline_mape
        self._mape_monitor = MAPEDriftMonitor(
            baseline_mape=baseline_mape,
            threshold=self.settings.mape_degradation_threshold,
        )
        log.info("baseline_set", mape=f"{baseline_mape:.2%}")

    def record_prediction(
        self,
        features: dict[str, Any],
        prediction: float,
        actual: float | None = None,
    ) -> None:
        """Buffer a production prediction (with optional ground truth)."""
        self._prediction_buffer.append({
            "features": features,
            "prediction": prediction,
            "actual": actual,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def check_and_retrain(self) -> RetrainingResult:
        """
        Main entry point. Checks all triggers and retrains if needed.
        Call from cron: `asyncio.run(scheduler.check_and_retrain())`
        """
        trigger = await self._check_triggers()
        if trigger is None:
            log.info("retraining_not_needed")
            return RetrainingResult(triggered=False, trigger=None)

        log.info("retraining_triggered", reason=trigger.reason)
        return await self._execute_retraining(trigger)

    async def trigger_now(self, reason: str = "manual") -> RetrainingResult:
        """Force an immediate retraining cycle."""
        trigger = RetrainTrigger(
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return await self._execute_retraining(trigger)

    # ── Trigger checks ─────────────────────────────────────────────────────

    async def _check_triggers(self) -> RetrainTrigger | None:
        # 1. Volume trigger
        labelled = [p for p in self._prediction_buffer if p["actual"] is not None]
        if len(labelled) >= self.settings.min_samples_for_retrain:
            return RetrainTrigger(
                reason="volume",
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata={"n_labelled": len(labelled)},
            )

        # 2. MAPE degradation trigger
        if self._mape_monitor is not None and len(labelled) >= 50:
            actuals = np.array([p["actual"] for p in labelled])
            preds   = np.array([p["prediction"] for p in labelled])
            batch_mape = compute_mape(actuals, preds)
            if self._mape_monitor.update(batch_mape):
                return RetrainTrigger(
                    reason="mape_degradation",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    metadata={
                        "batch_mape": batch_mape,
                        "ewma_mape": self._mape_monitor.current_ewma,
                        "baseline": self._baseline_mape,
                    },
                )

        # 3. Drift trigger
        if len(self._prediction_buffer) >= self.settings.drift_detection_window:
            features_df = pd.DataFrame(
                [p["features"] for p in self._prediction_buffer[-self.settings.drift_detection_window:]]
            )
            predictions = np.array(
                [p["prediction"] for p in self._prediction_buffer[-self.settings.drift_detection_window:]]
            )
            drift_report = self._drift_detector.detect(features_df, predictions)
            if drift_report.overall_level == DriftLevel.HIGH:
                return RetrainTrigger(
                    reason="data_drift",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    metadata={
                        "drifted_features": drift_report.features_with_high_drift,
                        "n_window": self.settings.drift_detection_window,
                    },
                )

        return None

    # ── Retraining execution ───────────────────────────────────────────────

    async def _execute_retraining(self, trigger: RetrainTrigger) -> RetrainingResult:
        log.info("retraining_start", trigger=trigger.reason)
        try:
            df = await self._load_training_data()
            if len(df) < self.settings.min_samples_for_retrain:
                log.warning("insufficient_data", n=len(df))
                return RetrainingResult(
                    triggered=True,
                    trigger=trigger,
                    error=f"Insufficient data: {len(df)} < {self.settings.min_samples_for_retrain}",
                )

            loop = asyncio.get_event_loop()
            train_result = await loop.run_in_executor(
                None,
                lambda: self._trainer.train(
                    df,
                    run_name=f"retrain-{trigger.reason}",
                    register=True,
                ),
            )

            self._last_retrain = datetime.now(timezone.utc)
            self._prediction_buffer.clear()
            log.info("retraining_complete", run_id=train_result.run_id)

            return RetrainingResult(
                triggered=True,
                trigger=trigger,
                training_result=train_result,
            )

        except Exception as exc:
            log.exception("retraining_failed")
            return RetrainingResult(
                triggered=True,
                trigger=trigger,
                error=str(exc),
            )

    async def _load_training_data(self) -> pd.DataFrame:
        if self._data_loader is None:
            raise RuntimeError("No data_loader configured for retraining")
        if asyncio.iscoroutinefunction(self._data_loader):
            return await self._data_loader(self.settings.retrain_lookback_days)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._data_loader, self.settings.retrain_lookback_days
        )


class CronRetrainingWorker:
    """
    Thin wrapper to run the scheduler on a cron schedule via APScheduler.
    """

    def __init__(self, scheduler: RetrainingScheduler) -> None:
        self._scheduler = scheduler

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        cron_expr = self.settings.retrain_schedule_cron.split()
        trigger = CronTrigger(
            minute=cron_expr[0],
            hour=cron_expr[1],
            day=cron_expr[2],
            month=cron_expr[3],
            day_of_week=cron_expr[4],
        )
        aps = AsyncIOScheduler()
        aps.add_job(self._run, trigger=trigger, id="cost_model_retrain")
        aps.start()
        log.info("retrain_cron_started", cron=self.settings.retrain_schedule_cron)

    async def _run(self) -> None:
        result = await self._scheduler.check_and_retrain()
        log.info("cron_retrain_result", triggered=result.triggered, error=result.error)

    @property
    def settings(self) -> MLSettings:
        return self._scheduler.settings
