# CLS — Sekcje 5–8: Retraining Strategy, Feature Store, Model Registry, Evaluation Metrics

---

## 5. Retraining Strategy

### 5.1 Zasady ogólne

| Parametr | Wartość |
|----------|---------|
| Scheduled retraining | Co tydzień (niedziele 02:00 UTC) |
| Event-triggered retraining | Po CRITICAL drift lub ≥3 WARNING w 24h |
| Min. samples required | 500 nowych actual costs od ostatniego treningu |
| Max training window | 730 dni (2 lata) rolling |
| Warm-start | Tak — fine-tune champion weights z nowych danych |
| Holdout split | Ostatnie 60 dni → test set (time-based, nie random) |
| CV folds | 5-fold TimeSeriesSplit |
| Promotion threshold | MAPE_challenger < MAPE_champion − 0.5pp AND bias < ±2% |

### 5.2 RetrainingOrchestrator

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
import uuid

import mlflow
import pandas as pd
from airflow.models import Variable

from cls.data_collection import DataQualityValidator
from cls.evaluation import ModelEvaluator, EvaluationResult
from cls.feature_store import OfflineFeatureStore
from cls.model_registry import CLSModelRegistry
from cls.drift import DriftSignal

logger = logging.getLogger(__name__)

UTC = timezone.utc


class RetrainingTrigger(str, Enum):
    SCHEDULED = "SCHEDULED"
    DRIFT_CRITICAL = "DRIFT_CRITICAL"
    DRIFT_WARNING_ACCUMULATION = "DRIFT_WARNING_ACCUMULATION"
    MANUAL = "MANUAL"
    ACCURACY_DEGRADATION = "ACCURACY_DEGRADATION"


@dataclass
class RetrainingJob:
    job_id: str
    model_name: str
    trigger: RetrainingTrigger
    drift_signal_id: Optional[str]
    min_train_date: datetime
    max_train_date: datetime
    holdout_start: datetime
    warm_start: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "PENDING"  # PENDING / RUNNING / SUCCESS / FAILED / ROLLED_BACK
    challenger_version: Optional[str] = None
    challenger_mape: Optional[float] = None
    champion_mape: Optional[float] = None
    promoted: bool = False
    rolled_back: bool = False
    failure_reason: Optional[str] = None


@dataclass
class PromotionDecision:
    promote: bool
    reason: str
    challenger_mape: float
    champion_mape: float
    mape_delta: float  # champion - challenger (positive = challenger better)
    challenger_bias: float
    statistical_significance: bool
    p_value: float


PROMOTION_THRESHOLDS = {
    "cee-unit-cost-xgb":      {"mape_improvement_pp": 0.5, "max_bias_pct": 2.0},
    "cee-material-cost-lgbm": {"mape_improvement_pp": 0.5, "max_bias_pct": 2.0},
    "cee-process-cost-lgbm":  {"mape_improvement_pp": 0.5, "max_bias_pct": 2.0},
    "cee-overhead-cost-xgb":  {"mape_improvement_pp": 1.0, "max_bias_pct": 3.0},
    "cee-confidence":         {"mape_improvement_pp": 0.3, "max_bias_pct": 1.5},
}

# Minimum samples przed retrainingiem (per model)
MIN_NEW_SAMPLES = {
    "cee-unit-cost-xgb":      500,
    "cee-material-cost-lgbm": 300,
    "cee-process-cost-lgbm":  300,
    "cee-overhead-cost-xgb":  300,
    "cee-confidence":         200,
}


class RetrainingOrchestrator:
    """Zarządza pełnym cyklem retrainingu: dane → trening → ewaluacja → promocja/rollback."""

    def __init__(
        self,
        feature_store: OfflineFeatureStore,
        evaluator: ModelEvaluator,
        registry: CLSModelRegistry,
        db_pool,
        kafka_producer,
    ):
        self.feature_store = feature_store
        self.evaluator = evaluator
        self.registry = registry
        self.db = db_pool
        self.kafka = kafka_producer
        self._active_jobs: dict[str, RetrainingJob] = {}

    async def trigger_retraining(
        self,
        model_name: str,
        trigger: RetrainingTrigger,
        drift_signal: Optional[DriftSignal] = None,
    ) -> RetrainingJob:
        now = datetime.now(UTC)
        holdout_start = now - timedelta(days=60)
        min_train_date = now - timedelta(days=730)

        # Sprawdź cooldown (nie retrenuj częściej niż co 6h na ten sam model)
        if model_name in self._active_jobs:
            existing = self._active_jobs[model_name]
            if existing.status == "RUNNING":
                logger.warning("Retraining already running for %s", model_name)
                return existing

        # Sprawdź minimalną liczbę nowych sampli
        new_count = await self._count_new_actuals(model_name, holdout_start)
        if new_count < MIN_NEW_SAMPLES.get(model_name, 500):
            logger.info(
                "Insufficient new samples for %s: %d < %d",
                model_name, new_count, MIN_NEW_SAMPLES[model_name],
            )
            raise InsufficientDataError(
                f"Need {MIN_NEW_SAMPLES[model_name]} samples, got {new_count}"
            )

        job = RetrainingJob(
            job_id=str(uuid.uuid4()),
            model_name=model_name,
            trigger=trigger,
            drift_signal_id=drift_signal.signal_id if drift_signal else None,
            min_train_date=min_train_date,
            max_train_date=holdout_start,
            holdout_start=holdout_start,
            warm_start=trigger != RetrainingTrigger.DRIFT_CRITICAL,
        )
        await self._persist_job(job)
        self._active_jobs[model_name] = job

        asyncio.create_task(self._run_retraining_pipeline(job))
        return job

    async def _run_retraining_pipeline(self, job: RetrainingJob) -> None:
        job.status = "RUNNING"
        job.started_at = datetime.now(UTC)
        await self._update_job(job)

        try:
            # 1. Pobierz dane treningowe z Feature Store
            train_df = await self.feature_store.get_training_data(
                model_name=job.model_name,
                start_date=job.min_train_date,
                end_date=job.max_train_date,
            )
            holdout_df = await self.feature_store.get_training_data(
                model_name=job.model_name,
                start_date=job.holdout_start,
                end_date=datetime.now(UTC),
            )

            logger.info(
                "Retraining %s: train=%d, holdout=%d",
                job.model_name, len(train_df), len(holdout_df),
            )

            # 2. Trening challengera
            challenger_version, challenger_run_id = await self._train_challenger(
                job=job,
                train_df=train_df,
                holdout_df=holdout_df,
            )
            job.challenger_version = challenger_version

            # 3. Ewaluacja
            eval_result = await self.evaluator.evaluate(
                model_name=job.model_name,
                version=challenger_version,
                holdout_df=holdout_df,
            )
            job.challenger_mape = eval_result.mape
            await self._update_job(job)

            # 4. Pobierz metryki championa
            champion_eval = await self.evaluator.evaluate_champion(
                model_name=job.model_name,
                holdout_df=holdout_df,
            )
            job.champion_mape = champion_eval.mape

            # 5. Decyzja o promocji
            decision = self._decide_promotion(
                job=job,
                challenger_eval=eval_result,
                champion_eval=champion_eval,
            )

            if decision.promote:
                await self.registry.promote_to_production(
                    model_name=job.model_name,
                    version=challenger_version,
                )
                job.promoted = True
                logger.info(
                    "Promoted %s v%s (MAPE: %.2f%% → %.2f%%)",
                    job.model_name, challenger_version,
                    job.champion_mape, job.challenger_mape,
                )
                await self._emit_model_promoted(job, decision)
            else:
                logger.info(
                    "Challenger not promoted for %s: %s",
                    job.model_name, decision.reason,
                )
                await self.registry.archive_version(job.model_name, challenger_version)

            job.status = "SUCCESS"
        except Exception as exc:
            job.status = "FAILED"
            job.failure_reason = str(exc)
            logger.exception("Retraining failed for %s", job.model_name)
            await self._emit_retraining_failed(job)
        finally:
            job.completed_at = datetime.now(UTC)
            await self._update_job(job)
            self._active_jobs.pop(job.model_name, None)

    async def _train_challenger(
        self,
        job: RetrainingJob,
        train_df: pd.DataFrame,
        holdout_df: pd.DataFrame,
    ) -> tuple[str, str]:
        """Trenuje model challengera w MLflow experiment run."""
        from cls.trainers import get_trainer_for_model

        trainer = get_trainer_for_model(job.model_name)

        with mlflow.start_run(
            experiment_id=self.registry.get_experiment_id(job.model_name),
            run_name=f"retrain_{job.trigger.value}_{job.job_id[:8]}",
        ) as run:
            mlflow.log_params({
                "job_id": job.job_id,
                "trigger": job.trigger.value,
                "warm_start": job.warm_start,
                "train_samples": len(train_df),
                "holdout_samples": len(holdout_df),
                "train_start": job.min_train_date.isoformat(),
                "train_end": job.max_train_date.isoformat(),
            })

            if job.warm_start:
                champion_artifact = await self.registry.load_champion_artifact(
                    job.model_name
                )
                version = trainer.fine_tune(
                    base_model=champion_artifact,
                    train_df=train_df,
                    holdout_df=holdout_df,
                )
            else:
                version = trainer.train_from_scratch(
                    train_df=train_df,
                    holdout_df=holdout_df,
                )

            mlflow.log_metric("challenger_cv_mape", trainer.last_cv_mape)
            mlflow.log_metric("challenger_holdout_mape", trainer.last_holdout_mape)

            return version, run.info.run_id

    def _decide_promotion(
        self,
        job: RetrainingJob,
        challenger_eval: "EvaluationResult",
        champion_eval: "EvaluationResult",
    ) -> PromotionDecision:
        thresholds = PROMOTION_THRESHOLDS[job.model_name]
        mape_delta = champion_eval.mape - challenger_eval.mape  # positive = challenger better
        bias_ok = abs(challenger_eval.bias_pct) <= thresholds["max_bias_pct"]
        mape_ok = mape_delta >= thresholds["mape_improvement_pp"]
        sig_ok = challenger_eval.p_value < 0.05  # paired t-test vs champion

        promote = mape_ok and bias_ok and sig_ok

        reason_parts = []
        if not mape_ok:
            reason_parts.append(
                f"MAPE improvement {mape_delta:.2f}pp < {thresholds['mape_improvement_pp']}pp required"
            )
        if not bias_ok:
            reason_parts.append(
                f"bias {challenger_eval.bias_pct:.2f}% > {thresholds['max_bias_pct']}% limit"
            )
        if not sig_ok:
            reason_parts.append(f"not significant (p={challenger_eval.p_value:.3f})")

        return PromotionDecision(
            promote=promote,
            reason="; ".join(reason_parts) if reason_parts else "All thresholds met",
            challenger_mape=challenger_eval.mape,
            champion_mape=champion_eval.mape,
            mape_delta=mape_delta,
            challenger_bias=challenger_eval.bias_pct,
            statistical_significance=sig_ok,
            p_value=challenger_eval.p_value,
        )

    async def rollback(self, model_name: str, reason: str) -> None:
        """Przywraca poprzednią wersję produkcyjną."""
        prev_version = await self.registry.get_previous_production_version(model_name)
        if not prev_version:
            raise RollbackError(f"No previous production version for {model_name}")
        await self.registry.promote_to_production(model_name, prev_version)
        logger.warning("ROLLBACK %s → v%s: %s", model_name, prev_version, reason)
        await self._emit_rollback(model_name, prev_version, reason)

    async def _count_new_actuals(self, model_name: str, since: datetime) -> int:
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT COUNT(*) FROM cls.actual_costs
                WHERE recorded_at >= $1 AND quality_passed = TRUE
                """,
                since,
            )

    async def _persist_job(self, job: RetrainingJob) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cls.retraining_jobs (
                    job_id, model_name, trigger, drift_signal_id,
                    min_train_date, max_train_date, holdout_start,
                    warm_start, status, created_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                job.job_id, job.model_name, job.trigger.value, job.drift_signal_id,
                job.min_train_date, job.max_train_date, job.holdout_start,
                job.warm_start, job.status, job.created_at,
            )

    async def _update_job(self, job: RetrainingJob) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE cls.retraining_jobs SET
                    status=$2, challenger_version=$3, challenger_mape=$4,
                    champion_mape=$5, promoted=$6, rolled_back=$7,
                    failure_reason=$8, started_at=$9, completed_at=$10
                WHERE job_id=$1
                """,
                job.job_id, job.status, job.challenger_version, job.challenger_mape,
                job.champion_mape, job.promoted, job.rolled_back,
                job.failure_reason, job.started_at, job.completed_at,
            )

    async def _emit_model_promoted(self, job: RetrainingJob, decision: PromotionDecision):
        await self.kafka.send(
            "cls.model.promoted",
            {
                "job_id": job.job_id,
                "model_name": job.model_name,
                "new_version": job.challenger_version,
                "trigger": job.trigger.value,
                "challenger_mape": job.challenger_mape,
                "champion_mape": job.champion_mape,
                "mape_delta": decision.mape_delta,
                "promoted_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _emit_retraining_failed(self, job: RetrainingJob):
        await self.kafka.send(
            "cls.retraining.failed",
            {
                "job_id": job.job_id,
                "model_name": job.model_name,
                "trigger": job.trigger.value,
                "failure_reason": job.failure_reason,
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _emit_rollback(self, model_name: str, version: str, reason: str):
        await self.kafka.send(
            "cls.model.rolled_back",
            {
                "model_name": model_name,
                "rolled_back_to_version": version,
                "reason": reason,
                "rolled_back_at": datetime.now(UTC).isoformat(),
            },
        )


class InsufficientDataError(Exception):
    pass


class RollbackError(Exception):
    pass
```

### 5.3 Strategia warm-start vs cold-start

```
Trigger                          Strategia
─────────────────────────────────────────────────────
SCHEDULED (tygodniowy)          warm-start: fine-tune champion z nowymi danymi
DRIFT_WARNING (≥3 w 24h)        warm-start: fine-tune + zwiększony learning rate
DRIFT_CRITICAL                  cold-start: trening od zera (ignore champion weights)
MANUAL                          warm-start (domyślnie, można nadpisać)
ACCURACY_DEGRADATION            cold-start: oznacza strukturalną zmianę
```

### 5.4 Airflow DAG — retraining

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor
from datetime import datetime, timedelta

with DAG(
    dag_id="cls_model_retraining",
    schedule_interval="0 2 * * 0",  # Co niedzielę o 02:00 UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "cls-team",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(hours=4),
        "email_on_failure": True,
        "email": ["ml-ops@company.com"],
    },
    tags=["cls", "retraining", "ml"],
) as dag:

    wait_for_ingestion = ExternalTaskSensor(
        task_id="wait_for_ingestion",
        external_dag_id="cls_data_ingestion",
        external_task_id="validate_and_emit",
        timeout=3600,
        mode="reschedule",
    )

    def retrain_model(model_name: str, **context):
        import asyncio
        from cls.orchestrator import RetrainingOrchestrator, RetrainingTrigger
        from cls.deps import get_orchestrator
        orch = get_orchestrator()
        asyncio.run(orch.trigger_retraining(model_name, RetrainingTrigger.SCHEDULED))

    retrain_tasks = []
    MODELS_ORDER = [
        "cee-material-cost-lgbm",   # material najpierw (dependency)
        "cee-process-cost-lgbm",
        "cee-overhead-cost-xgb",
        "cee-unit-cost-xgb",        # unit cost zależy od powyższych
        "cee-confidence",           # confidence ostatni
    ]
    for model in MODELS_ORDER:
        t = PythonOperator(
            task_id=f"retrain_{model.replace('-', '_')}",
            python_callable=retrain_model,
            op_kwargs={"model_name": model},
        )
        retrain_tasks.append(t)

    # Sekwencja: każdy model po poprzednim (ze względu na zależności)
    wait_for_ingestion >> retrain_tasks[0]
    for i in range(1, len(retrain_tasks)):
        retrain_tasks[i - 1] >> retrain_tasks[i]
```

---

## 6. Feature Store

### 6.1 Architektura

```
┌─────────────────────────────────────────────────────────────────┐
│                        Feature Store                            │
│                                                                 │
│  ┌──────────────────┐          ┌──────────────────────────────┐ │
│  │  Offline Store   │          │       Online Store           │ │
│  │  (PostgreSQL)    │          │       (Redis 7+)             │ │
│  │                  │          │                              │ │
│  │ - Point-in-time  │  ──────► │ - Low-latency serving        │ │
│  │   correct joins  │  sync    │ - TTL per feature group      │ │
│  │ - Training data  │          │ - Hash map per entity        │ │
│  │ - Backfill       │          │ - CEE inference (<2ms)       │ │
│  └──────────────────┘          └──────────────────────────────┘ │
│            ▲                               ▲                    │
│            │                               │                    │
│  ┌─────────┴─────────────────────────────┐ │                   │
│  │      FeatureMaterializer (Airflow)    │─┘                   │
│  │  - Hourly online sync                 │                      │
│  │  - Daily offline materialization      │                      │
│  │  - Feature versioning                 │                      │
│  └───────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 OfflineFeatureStore

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import pandas as pd

UTC = timezone.utc


class OfflineFeatureStore:
    """
    Point-in-time correct feature retrieval dla treningu modeli.
    Używa AS OF SYSTEM TIME (snapshot isolation) lub temporal joins
    żeby uniknąć data leakage z przyszłości.
    """

    FEATURE_VIEWS = {
        "cee-unit-cost-xgb": "cls.fv_unit_cost_features",
        "cee-material-cost-lgbm": "cls.fv_material_cost_features",
        "cee-process-cost-lgbm": "cls.fv_process_cost_features",
        "cee-overhead-cost-xgb": "cls.fv_overhead_cost_features",
        "cee-confidence": "cls.fv_confidence_features",
    }

    TARGET_COLUMNS = {
        "cee-unit-cost-xgb": "actual_unit_cost_eur",
        "cee-material-cost-lgbm": "actual_material_cost_eur",
        "cee-process-cost-lgbm": "actual_process_cost_eur",
        "cee-overhead-cost-xgb": "actual_overhead_cost_eur",
        "cee-confidence": "within_tolerance_flag",  # binary: 1 if |error|<10%
    }

    def __init__(self, db_pool):
        self.db = db_pool

    async def get_training_data(
        self,
        model_name: str,
        start_date: datetime,
        end_date: datetime,
        as_of: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Zwraca dane treningowe z point-in-time correct feature values.
        as_of: timestamp dla feature lookup (domyślnie end_date)
        """
        view = self.FEATURE_VIEWS[model_name]
        target_col = self.TARGET_COLUMNS[model_name]
        as_of_ts = as_of or end_date

        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    ac.actual_id,
                    ac.estimate_id,
                    ac.actual_date,
                    ac.{target_col},
                    fv.*
                FROM cls.actual_costs ac
                JOIN {view} fv
                    ON fv.estimate_id = ac.estimate_id
                    -- Point-in-time: użyj feature values sprzed as_of
                    AND fv.feature_timestamp <= $3
                    -- Bierz najnowsze feature values dla każdego estimate
                    AND fv.feature_version = (
                        SELECT MAX(fv2.feature_version)
                        FROM {view} fv2
                        WHERE fv2.estimate_id = fv.estimate_id
                          AND fv2.feature_timestamp <= $3
                    )
                WHERE ac.actual_date BETWEEN $1 AND $2
                  AND ac.quality_passed = TRUE
                ORDER BY ac.actual_date
                """,
                start_date, end_date, as_of_ts,
            )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df = df.dropna(subset=[target_col])
        return df

    async def get_feature_schema(self, model_name: str) -> list[dict]:
        """Zwraca schemat cech (name, dtype, version) dla danego modelu."""
        view = self.FEATURE_VIEWS[model_name]
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema || '.' || table_name = $1
                ORDER BY ordinal_position
                """,
                view,
            )
        return [{"name": r["column_name"], "dtype": r["data_type"]} for r in rows]

    async def backfill_features(
        self,
        model_name: str,
        from_date: datetime,
        to_date: datetime,
    ) -> int:
        """Backfill feature values dla historycznych actual costs."""
        view = self.FEATURE_VIEWS[model_name]
        async with self.db.acquire() as conn:
            result = await conn.execute(
                f"""
                INSERT INTO {view} (estimate_id, feature_timestamp, feature_version, ...)
                SELECT
                    ei.estimate_id,
                    $1 AS feature_timestamp,
                    1 AS feature_version,
                    -- wszystkie kolumny z cee.estimation_inputs
                    ei.*
                FROM cee.estimation_inputs ei
                JOIN cls.actual_costs ac ON ac.estimate_id = ei.estimate_id
                WHERE ac.actual_date BETWEEN $1 AND $2
                ON CONFLICT (estimate_id, feature_version) DO NOTHING
                """,
                from_date, to_date,
            )
        return int(result.split()[-1])
```

### 6.3 OnlineFeatureStore

```python
import json
from datetime import timedelta
from typing import Any, Optional

import redis.asyncio as aioredis


ONLINE_TTL = {
    "material_price": timedelta(hours=6),
    "location_index": timedelta(hours=24),
    "supplier_score": timedelta(hours=12),
    "exchange_rate": timedelta(hours=1),
}

FEATURE_GROUPS = {
    # group_name → (key_pattern, TTL)
    "material_prices": ("cls:feat:mat:{material_code}", ONLINE_TTL["material_price"]),
    "location_indices": ("cls:feat:loc:{country}", ONLINE_TTL["location_index"]),
    "supplier_scores": ("cls:feat:sup:{supplier_id}", ONLINE_TTL["supplier_score"]),
    "fx_rates": ("cls:feat:fx:{currency_pair}", ONLINE_TTL["exchange_rate"]),
}


class OnlineFeatureStore:
    """Redis-based feature serving dla CEE inference (<2ms p99)."""

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def get_features(
        self,
        group: str,
        entity_id: str,
        feature_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        pattern, _ = FEATURE_GROUPS[group]
        key = pattern.format(
            material_code=entity_id,
            country=entity_id,
            supplier_id=entity_id,
            currency_pair=entity_id,
        )
        if feature_names:
            values = await self.redis.hmget(key, feature_names)
            return {k: json.loads(v) if v else None for k, v in zip(feature_names, values)}
        else:
            raw = await self.redis.hgetall(key)
            return {k.decode(): json.loads(v) for k, v in raw.items()}

    async def set_features(
        self,
        group: str,
        entity_id: str,
        features: dict[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> None:
        pattern, default_ttl = FEATURE_GROUPS[group]
        key = pattern.format(
            material_code=entity_id,
            country=entity_id,
            supplier_id=entity_id,
            currency_pair=entity_id,
        )
        serialized = {k: json.dumps(v) for k, v in features.items()}
        async with self.redis.pipeline() as pipe:
            await pipe.hset(key, mapping=serialized)
            await pipe.expire(key, int((ttl or default_ttl).total_seconds()))
            await pipe.execute()

    async def get_batch(
        self,
        group: str,
        entity_ids: list[str],
        feature_names: Optional[list[str]] = None,
    ) -> dict[str, dict[str, Any]]:
        results = {}
        async with self.redis.pipeline() as pipe:
            pattern, _ = FEATURE_GROUPS[group]
            for eid in entity_ids:
                key = pattern.format(
                    material_code=eid, country=eid,
                    supplier_id=eid, currency_pair=eid,
                )
                if feature_names:
                    await pipe.hmget(key, feature_names)
                else:
                    await pipe.hgetall(key)
            raw_results = await pipe.execute()

        for eid, raw in zip(entity_ids, raw_results):
            if isinstance(raw, dict):
                results[eid] = {k.decode(): json.loads(v) for k, v in raw.items()}
            else:
                results[eid] = {
                    k: json.loads(v) if v else None
                    for k, v in zip(feature_names or [], raw)
                }
        return results
```

### 6.4 FeatureMaterializer (Airflow DAG)

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

with DAG(
    dag_id="cls_feature_materialization",
    schedule_interval="0 * * * *",   # Co godzinę
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "cls-team",
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=30),
    },
    tags=["cls", "features", "materialization"],
) as dag:

    def sync_material_prices(**context):
        """Sync aktualnych cen materiałów z cee.material_price_cache → Redis."""
        import asyncio
        from cls.deps import get_online_store, get_db
        async def _run():
            db = await get_db()
            store = get_online_store()
            async with db.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT material_code, price_eur_per_kg, supplier_id,
                           currency, valid_from
                    FROM cee.material_price_cache
                    WHERE valid_until IS NULL OR valid_until > NOW()
                    """
                )
            for r in rows:
                await store.set_features(
                    group="material_prices",
                    entity_id=r["material_code"],
                    features={
                        "price_eur_per_kg": float(r["price_eur_per_kg"]),
                        "supplier_id": str(r["supplier_id"]) if r["supplier_id"] else None,
                        "valid_from": r["valid_from"].isoformat(),
                    },
                )
        asyncio.run(_run())

    def sync_location_indices(**context):
        """Sync indeksów kosztów lokalizacji → Redis."""
        from cls.deps import get_online_store
        import asyncio
        LOCATION_COST_INDEX = {
            "DE": 1.00, "PL": 0.42, "CZ": 0.48, "RO": 0.35, "SK": 0.46,
            "CN": 0.28, "IN": 0.22, "MX": 0.38, "US": 0.95, "TR": 0.32,
            "HU": 0.44, "FR": 0.92,
        }
        async def _run():
            store = get_online_store()
            for country, idx in LOCATION_COST_INDEX.items():
                await store.set_features(
                    group="location_indices",
                    entity_id=country,
                    features={"location_cost_index": idx, "country": country},
                )
        asyncio.run(_run())

    def sync_fx_rates(**context):
        """Sync kursów walut z cee.fx_rates → Redis."""
        import asyncio
        from cls.deps import get_online_store, get_db
        async def _run():
            db = await get_db()
            store = get_online_store()
            async with db.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT from_currency || '_' || to_currency AS pair,
                           rate, fetched_at
                    FROM cee.fx_rates
                    WHERE fetched_at = (
                        SELECT MAX(fetched_at) FROM cee.fx_rates
                    )
                    """
                )
            for r in rows:
                await store.set_features(
                    group="fx_rates",
                    entity_id=r["pair"],
                    features={"rate": float(r["rate"]), "fetched_at": r["fetched_at"].isoformat()},
                )
        asyncio.run(_run())

    def materialize_offline_features(**context):
        """Offline: materialization wektorów cech dla nowych actual costs."""
        import asyncio
        from cls.deps import get_offline_store
        from datetime import timezone
        async def _run():
            store = get_offline_store()
            now = datetime.now(timezone.utc)
            since = now - timedelta(hours=2)   # overlap 1h żeby nie zgubić
            for model in [
                "cee-unit-cost-xgb", "cee-material-cost-lgbm",
                "cee-process-cost-lgbm", "cee-overhead-cost-xgb", "cee-confidence",
            ]:
                n = await store.backfill_features(model, since, now)
                print(f"Backfilled {n} feature rows for {model}")
        asyncio.run(_run())

    t1 = PythonOperator(task_id="sync_material_prices", python_callable=sync_material_prices)
    t2 = PythonOperator(task_id="sync_location_indices", python_callable=sync_location_indices)
    t3 = PythonOperator(task_id="sync_fx_rates", python_callable=sync_fx_rates)
    t4 = PythonOperator(task_id="materialize_offline_features", python_callable=materialize_offline_features)

    [t1, t2, t3] >> t4
```

### 6.5 Feature Versioning

```
Każda zmiana schematu cech → nowy feature_version (integer, monotonic).
Reguły:
  - Dodanie cechy:     nowy feature_version, backfill = NULL dla historii
  - Usunięcie cechy:   nowy feature_version, deprecation flag
  - Rename:            nowy feature_version + alias w widoku
  - Model trenowany na konkretnym feature_version
  - Serving: online store klucze zawierają feature_version hash
```

---

## 7. Model Registry

### 7.1 CLSModelRegistry

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
import mlflow
from mlflow.tracking import MlflowClient
from mlflow.entities.model_registry import ModelVersion

logger = logging.getLogger(__name__)
UTC = timezone.utc

MODEL_NAMES = {
    "cee-unit-cost-xgb":      "CLS_UnitCostXGB",
    "cee-material-cost-lgbm": "CLS_MaterialCostLGBM",
    "cee-process-cost-lgbm":  "CLS_ProcessCostLGBM",
    "cee-overhead-cost-xgb":  "CLS_OverheadCostXGB",
    "cee-confidence":         "CLS_ConfidenceModel",
}

MLFLOW_STAGES = {
    "none":       "None",
    "staging":    "Staging",
    "production": "Production",
    "archived":   "Archived",
}


class CLSModelRegistry:
    """
    Wrapper nad MLflow Model Registry.
    Zarządza cyklem życia: None → Staging → Production → Archived.
    """

    def __init__(self, tracking_uri: str, db_pool):
        mlflow.set_tracking_uri(tracking_uri)
        self.client = MlflowClient()
        self.db = db_pool
        self._experiment_ids: dict[str, str] = {}

    def get_experiment_id(self, model_name: str) -> str:
        if model_name not in self._experiment_ids:
            exp_name = f"cls/{model_name}"
            exp = mlflow.get_experiment_by_name(exp_name)
            if exp is None:
                exp_id = mlflow.create_experiment(
                    exp_name,
                    tags={"model": model_name, "team": "cls"},
                )
            else:
                exp_id = exp.experiment_id
            self._experiment_ids[model_name] = exp_id
        return self._experiment_ids[model_name]

    def register_run(
        self,
        run_id: str,
        model_name: str,
        model_artifact_path: str = "model",
    ) -> ModelVersion:
        """Rejestruje artifact z MLflow run jako nową wersję modelu."""
        mlflow_name = MODEL_NAMES[model_name]
        model_uri = f"runs:/{run_id}/{model_artifact_path}"
        mv = mlflow.register_model(model_uri, mlflow_name)
        logger.info(
            "Registered %s v%s from run %s",
            model_name, mv.version, run_id,
        )
        return mv

    async def promote_to_production(
        self,
        model_name: str,
        version: str,
    ) -> None:
        """
        Promuje wersję do Production.
        Poprzednia wersja Production → Archived.
        """
        mlflow_name = MODEL_NAMES[model_name]

        # Archiwizuj aktualną produkcyjną
        current_prod = self._get_production_version(mlflow_name)
        if current_prod and current_prod.version != version:
            self.client.transition_model_version_stage(
                name=mlflow_name,
                version=current_prod.version,
                stage=MLFLOW_STAGES["archived"],
                archive_existing_versions=False,
            )
            logger.info("Archived %s v%s", model_name, current_prod.version)
            # Zapisz poprzednią wersję do DB (dla rollbacku)
            await self._record_previous_version(model_name, current_prod.version)

        # Promuj challengera
        self.client.transition_model_version_stage(
            name=mlflow_name,
            version=version,
            stage=MLFLOW_STAGES["production"],
            archive_existing_versions=False,
        )
        logger.info("Promoted %s v%s to Production", model_name, version)
        await self._record_promotion(model_name, version)

    async def promote_to_staging(self, model_name: str, version: str) -> None:
        mlflow_name = MODEL_NAMES[model_name]
        self.client.transition_model_version_stage(
            name=mlflow_name,
            version=version,
            stage=MLFLOW_STAGES["staging"],
        )

    async def archive_version(self, model_name: str, version: str) -> None:
        mlflow_name = MODEL_NAMES[model_name]
        self.client.transition_model_version_stage(
            name=mlflow_name,
            version=version,
            stage=MLFLOW_STAGES["archived"],
        )

    def load_production_model(self, model_name: str):
        """Ładuje aktualny model produkcyjny."""
        mlflow_name = MODEL_NAMES[model_name]
        return mlflow.pyfunc.load_model(f"models:/{mlflow_name}/Production")

    async def load_champion_artifact(self, model_name: str):
        """Ładuje artifact championa (np. XGBoost Booster) dla warm-start."""
        mlflow_name = MODEL_NAMES[model_name]
        mv = self._get_production_version(mlflow_name)
        if mv is None:
            return None
        run = self.client.get_run(mv.run_id)
        artifact_uri = run.info.artifact_uri
        # Ładuje np. XGBoost model z artifact
        return mlflow.xgboost.load_model(f"{artifact_uri}/model")

    async def get_previous_production_version(self, model_name: str) -> Optional[str]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT previous_version FROM cls.model_promotions
                WHERE model_name = $1
                ORDER BY promoted_at DESC
                LIMIT 1
                """,
                model_name,
            )
        return row["previous_version"] if row else None

    def get_all_versions(self, model_name: str) -> list[ModelVersion]:
        mlflow_name = MODEL_NAMES[model_name]
        return self.client.search_model_versions(f"name='{mlflow_name}'")

    def get_production_version(self, model_name: str) -> Optional[str]:
        mlflow_name = MODEL_NAMES[model_name]
        mv = self._get_production_version(mlflow_name)
        return mv.version if mv else None

    def _get_production_version(self, mlflow_name: str) -> Optional[ModelVersion]:
        versions = self.client.get_latest_versions(
            mlflow_name, stages=[MLFLOW_STAGES["production"]]
        )
        return versions[0] if versions else None

    async def _record_promotion(self, model_name: str, new_version: str) -> None:
        prev = await self.get_previous_production_version(model_name)
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cls.model_promotions
                    (model_name, new_version, previous_version, promoted_at)
                VALUES ($1, $2, $3, $4)
                """,
                model_name, new_version, prev, datetime.now(UTC),
            )

    async def _record_previous_version(self, model_name: str, version: str) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE cls.model_promotions
                SET previous_version = $2
                WHERE model_name = $1
                  AND id = (
                      SELECT id FROM cls.model_promotions
                      WHERE model_name = $1
                      ORDER BY promoted_at DESC LIMIT 1
                  )
                """,
                model_name, version,
            )
```

### 7.2 Model lineage

```
MLflow run_id → ModelVersion → stage transition → cls.model_promotions log
                    │
                    ├── params: model_name, trigger, warm_start, train_samples
                    ├── metrics: cv_mape, holdout_mape, holdout_rmse, bias_pct, p_value
                    ├── tags: job_id, drift_signal_id, feature_version
                    └── artifacts: model/, feature_schema.json, eval_report.json
```

### 7.3 A/B Testing (champion-challenger)

```python
import random
from typing import Any

class ABModelRouter:
    """
    Router do A/B testowania champion vs challenger.
    Używany tylko gdy challenger jest w Staging i ma > 100 predictions.
    """

    def __init__(self, registry: CLSModelRegistry, challenger_traffic_pct: float = 0.10):
        self.registry = registry
        self.challenger_pct = challenger_traffic_pct  # 10% ruchu do challengera

    def route(self, model_name: str) -> tuple[str, str]:
        """Zwraca (stage, version) do użycia dla tego żądania."""
        use_challenger = random.random() < self.challenger_pct
        if use_challenger:
            staging = self.registry.client.get_latest_versions(
                MODEL_NAMES[model_name], stages=["Staging"]
            )
            if staging:
                return "Staging", staging[0].version
        prod = self.registry.get_production_version(model_name)
        return "Production", prod

    async def record_ab_outcome(
        self,
        model_name: str,
        version: str,
        stage: str,
        prediction: float,
        actual: float,
        db_pool,
    ) -> None:
        error = abs(prediction - actual) / actual if actual != 0 else None
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cls.ab_test_results
                    (model_name, version, stage, prediction, actual, relative_error, recorded_at)
                VALUES ($1,$2,$3,$4,$5,$6,NOW())
                """,
                model_name, version, stage, prediction, actual, error,
            )
```

---

## 8. Evaluation Metrics (MAPE, RMSE, Bias)

### 8.1 EvaluationResult & ModelEvaluator

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import numpy as np
import pandas as pd
from scipy import stats
import mlflow

UTC = timezone.utc


@dataclass
class SegmentMetrics:
    segment_name: str
    segment_value: str
    n_samples: int
    mape: float
    rmse: float
    mae: float
    bias_pct: float    # signed: positive = over-prediction
    within_10pct: float


@dataclass
class EvaluationResult:
    model_name: str
    version: str
    evaluated_at: datetime
    n_samples: int

    # Aggregate metrics
    mape: float          # Mean Absolute Percentage Error [%]
    rmse: float          # Root Mean Square Error [EUR]
    mae: float           # Mean Absolute Error [EUR]
    bias_pct: float      # (mean(pred) - mean(actual)) / mean(actual) × 100 [%]
    within_5pct: float   # % predictions within ±5% of actual
    within_10pct: float  # % predictions within ±10% of actual
    within_20pct: float  # % predictions within ±20% of actual
    r2: float            # R² coefficient of determination

    # Statistical significance vs champion
    p_value: float       # paired t-test (absolute errors: challenger vs champion)
    t_statistic: float

    # CI coverage
    ci_coverage: float   # % actuals within predicted [lower, upper] 95% CI

    # Segmented metrics
    by_location: list[SegmentMetrics] = field(default_factory=list)
    by_complexity: list[SegmentMetrics] = field(default_factory=list)
    by_volume_tier: list[SegmentMetrics] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "version": self.version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "n_samples": self.n_samples,
            "mape": round(self.mape, 4),
            "rmse": round(self.rmse, 2),
            "mae": round(self.mae, 2),
            "bias_pct": round(self.bias_pct, 4),
            "within_5pct": round(self.within_5pct, 4),
            "within_10pct": round(self.within_10pct, 4),
            "within_20pct": round(self.within_20pct, 4),
            "r2": round(self.r2, 4),
            "p_value": round(self.p_value, 6),
            "ci_coverage": round(self.ci_coverage, 4),
        }


class ModelEvaluator:
    """Kompleksowa ewaluacja modeli CEE na zbiorze holdout."""

    METRICS_THRESHOLDS = {
        "cee-unit-cost-xgb":      {"mape_warn": 10.0, "mape_crit": 15.0, "bias_max": 3.0},
        "cee-material-cost-lgbm": {"mape_warn": 8.0,  "mape_crit": 12.0, "bias_max": 2.5},
        "cee-process-cost-lgbm":  {"mape_warn": 12.0, "mape_crit": 18.0, "bias_max": 4.0},
        "cee-overhead-cost-xgb":  {"mape_warn": 15.0, "mape_crit": 22.0, "bias_max": 5.0},
        "cee-confidence":         {"mape_warn": 5.0,  "mape_crit": 10.0, "bias_max": 2.0},
    }

    def __init__(self, registry: CLSModelRegistry):
        self.registry = registry

    async def evaluate(
        self,
        model_name: str,
        version: str,
        holdout_df: pd.DataFrame,
    ) -> EvaluationResult:
        """Ewaluacja modelu challengera na zbiorze holdout."""
        model = mlflow.pyfunc.load_model(
            f"models:/{MODEL_NAMES[model_name]}/{version}"
        )

        feature_cols = self._get_feature_cols(holdout_df, model_name)
        target_col = OfflineFeatureStore.TARGET_COLUMNS[model_name]

        X = holdout_df[feature_cols]
        y_true = holdout_df[target_col].values
        y_pred = model.predict(X)

        # CI z modelu (jeśli wspiera predict_interval)
        ci_coverage = self._compute_ci_coverage(model, X, y_true)

        # Porównanie z aktualnym championem
        champion_errors = await self._get_champion_errors_on_holdout(
            model_name, holdout_df, feature_cols, target_col
        )
        challenger_errors = np.abs(y_pred - y_true)
        t_stat, p_val = stats.ttest_rel(challenger_errors, champion_errors)

        result = EvaluationResult(
            model_name=model_name,
            version=version,
            evaluated_at=datetime.now(UTC),
            n_samples=len(y_true),
            mape=self._mape(y_true, y_pred),
            rmse=float(np.sqrt(np.mean((y_pred - y_true) ** 2))),
            mae=float(np.mean(np.abs(y_pred - y_true))),
            bias_pct=self._bias_pct(y_true, y_pred),
            within_5pct=self._within_tolerance(y_true, y_pred, 0.05),
            within_10pct=self._within_tolerance(y_true, y_pred, 0.10),
            within_20pct=self._within_tolerance(y_true, y_pred, 0.20),
            r2=float(1 - np.sum((y_true - y_pred) ** 2) / np.sum((y_true - np.mean(y_true)) ** 2)),
            p_value=float(p_val),
            t_statistic=float(t_stat),
            ci_coverage=ci_coverage,
            by_location=self._segment_metrics(holdout_df, y_true, y_pred, "production_location"),
            by_complexity=self._segment_metrics(holdout_df, y_true, y_pred, "complexity_class"),
            by_volume_tier=self._segment_metrics(holdout_df, y_true, y_pred, "volume_tier"),
        )

        self._log_to_mlflow(result)
        return result

    async def evaluate_champion(
        self,
        model_name: str,
        holdout_df: pd.DataFrame,
    ) -> EvaluationResult:
        """Ewaluacja aktualnego championa na tym samym zbiorze holdout."""
        prod_version = self.registry.get_production_version(model_name)
        if prod_version is None:
            # Brak championa (pierwsze trenowanie) — zwróć sentinel z bardzo złą MAPE
            return EvaluationResult(
                model_name=model_name, version="none",
                evaluated_at=datetime.now(UTC), n_samples=0,
                mape=99.0, rmse=0.0, mae=0.0, bias_pct=0.0,
                within_5pct=0.0, within_10pct=0.0, within_20pct=0.0,
                r2=0.0, p_value=0.0, t_statistic=0.0, ci_coverage=0.0,
            )
        return await self.evaluate(model_name, prod_version, holdout_df)

    # ── Metric helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        mask = y_true != 0
        return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)

    @staticmethod
    def _bias_pct(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """Signed bias: + = over-prediction, − = under-prediction."""
        mask = y_true != 0
        return float(np.mean((y_pred[mask] - y_true[mask]) / y_true[mask]) * 100)

    @staticmethod
    def _within_tolerance(y_true: np.ndarray, y_pred: np.ndarray, tol: float) -> float:
        mask = y_true != 0
        within = np.abs((y_pred[mask] - y_true[mask]) / y_true[mask]) <= tol
        return float(np.mean(within))

    @staticmethod
    def _compute_ci_coverage(model, X: pd.DataFrame, y_true: np.ndarray) -> float:
        """Procent actual values mieszczących się w 95% CI modelu."""
        try:
            # XGBoost/LGBM nie mają natywnych CI — szacujemy z quantile regression
            q05 = model.predict(X, method="quantile", quantile=0.025)
            q95 = model.predict(X, method="quantile", quantile=0.975)
            within = (y_true >= q05) & (y_true <= q95)
            return float(np.mean(within))
        except Exception:
            return float("nan")

    def _segment_metrics(
        self,
        df: pd.DataFrame,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        segment_col: str,
    ) -> list[SegmentMetrics]:
        if segment_col not in df.columns:
            return []
        results = []
        df = df.copy()
        df["_y_true"] = y_true
        df["_y_pred"] = y_pred
        for val, grp in df.groupby(segment_col):
            yt = grp["_y_true"].values
            yp = grp["_y_pred"].values
            results.append(SegmentMetrics(
                segment_name=segment_col,
                segment_value=str(val),
                n_samples=len(yt),
                mape=self._mape(yt, yp),
                rmse=float(np.sqrt(np.mean((yp - yt) ** 2))),
                mae=float(np.mean(np.abs(yp - yt))),
                bias_pct=self._bias_pct(yt, yp),
                within_10pct=self._within_tolerance(yt, yp, 0.10),
            ))
        return sorted(results, key=lambda x: x.mape, reverse=True)

    async def _get_champion_errors_on_holdout(
        self,
        model_name: str,
        holdout_df: pd.DataFrame,
        feature_cols: list[str],
        target_col: str,
    ) -> np.ndarray:
        prod_version = self.registry.get_production_version(model_name)
        if not prod_version:
            return np.zeros(len(holdout_df))
        champion = mlflow.pyfunc.load_model(
            f"models:/{MODEL_NAMES[model_name]}/Production"
        )
        y_true = holdout_df[target_col].values
        y_pred_champ = champion.predict(holdout_df[feature_cols])
        return np.abs(y_pred_champ - y_true)

    @staticmethod
    def _get_feature_cols(df: pd.DataFrame, model_name: str) -> list[str]:
        EXCLUDE = {
            "actual_id", "estimate_id", "actual_date",
            "actual_unit_cost_eur", "actual_material_cost_eur",
            "actual_process_cost_eur", "actual_overhead_cost_eur",
            "within_tolerance_flag", "_y_true", "_y_pred",
        }
        return [c for c in df.columns if c not in EXCLUDE]

    def _log_to_mlflow(self, result: EvaluationResult) -> None:
        mlflow.log_metrics({
            "eval_mape": result.mape,
            "eval_rmse": result.rmse,
            "eval_mae": result.mae,
            "eval_bias_pct": result.bias_pct,
            "eval_within_5pct": result.within_5pct,
            "eval_within_10pct": result.within_10pct,
            "eval_within_20pct": result.within_20pct,
            "eval_r2": result.r2,
            "eval_p_value": result.p_value,
            "eval_ci_coverage": result.ci_coverage,
        })
```

### 8.2 Metryki — definicje i progi

| Metryka | Formuła | Cel (unit cost) | WARNING | CRITICAL |
|---------|---------|-----------------|---------|---------|
| **MAPE** | `mean(|y - ŷ| / y) × 100` | < 10% | ≥ 10% | ≥ 15% |
| **RMSE** | `sqrt(mean((y - ŷ)²))` | monitoring | — | — |
| **MAE** | `mean(|y - ŷ|)` | monitoring | — | — |
| **Bias** | `mean((ŷ - y) / y) × 100` | |bias| < 3% | ≥ 3% | ≥ 5% |
| **Within ±10%** | `% samples s.t. |error| ≤ 10%` | > 70% | ≤ 70% | ≤ 50% |
| **CI Coverage** | `% actual ∈ [lower, upper]` | ~95% | < 90% | < 80% |
| **R²** | `1 - SS_res/SS_tot` | > 0.85 | ≤ 0.80 | ≤ 0.70 |

### 8.3 Segmentowane metryki — progi per segment

```python
# Przykładowe progi per lokalizacja (MAPE)
LOCATION_MAPE_TARGETS = {
    "DE": 8.0,   "PL": 10.0, "CZ": 11.0, "RO": 12.0,
    "CN": 14.0,  "IN": 15.0, "MX": 13.0, "US": 9.0,
    "TR": 13.0,  "HU": 11.0,
}

# Progi per klasa złożoności
COMPLEXITY_MAPE_TARGETS = {
    "SIMPLE":   7.0,
    "STANDARD": 10.0,
    "COMPLEX":  14.0,
    "VERY_COMPLEX": 18.0,
}

# Progi per tier wolumenowy
VOLUME_TIER_MAPE_TARGETS = {
    "LOW":    15.0,   # < 100 szt/rok
    "MEDIUM": 10.0,   # 100–1000 szt/rok
    "HIGH":   8.0,    # 1000–10K szt/rok
    "MASS":   7.0,    # > 10K szt/rok
}
```

### 8.4 Kalibracja i Brier Score

```python
def calibration_check(
    predictions: np.ndarray,
    actuals: np.ndarray,
    confidence_intervals: np.ndarray,  # shape (n, 2): [lower, upper]
    n_bins: int = 10,
) -> dict:
    """
    Sprawdza kalibrację: czy 95% CI faktycznie zawiera 95% wartości rzeczywistych.
    Generuje dane do reliability diagram.
    """
    nominal_coverage = np.linspace(0.1, 1.0, n_bins)
    empirical_coverage = []

    for level in nominal_coverage:
        # Symetryczny CI przy danym poziomie
        alpha = 1 - level
        margin = confidence_intervals[:, 1] - predictions  # zakładamy symetryczny CI
        lower = predictions - margin * (1 - alpha / 2)
        upper = predictions + margin * (1 - alpha / 2)
        within = (actuals >= lower) & (actuals <= upper)
        empirical_coverage.append(float(np.mean(within)))

    # Brier score dla binary: within_10pct
    within_10pct_binary = (np.abs(actuals - predictions) / actuals <= 0.10).astype(float)
    # Użyj normalizowanego absolute error jako "probability" — proxy Brier
    brier_proxy = np.mean((within_10pct_binary - 0.70) ** 2)

    return {
        "nominal_coverage": nominal_coverage.tolist(),
        "empirical_coverage": empirical_coverage,
        "calibration_error": float(np.mean(np.abs(
            np.array(empirical_coverage) - nominal_coverage
        ))),
        "brier_score_proxy": float(brier_proxy),
    }
```

### 8.5 Statistical significance testing

```python
from scipy import stats


def paired_mape_test(
    errors_champion: np.ndarray,
    errors_challenger: np.ndarray,
    alpha: float = 0.05,
) -> dict:
    """
    Paired t-test na absolute errors champion vs challenger.
    H0: mean(errors_champ) = mean(errors_challeng)
    H1: mean(errors_challeng) < mean(errors_champ)  (one-tailed: challenger jest lepszy)
    """
    t_stat, p_two = stats.ttest_rel(errors_champion, errors_challenger)
    p_one = p_two / 2 if t_stat > 0 else 1.0  # one-tailed: challenger better

    return {
        "t_statistic": float(t_stat),
        "p_value_two_tailed": float(p_two),
        "p_value_one_tailed": float(p_one),
        "significant": p_one < alpha,
        "n_samples": len(errors_champion),
        "mean_champion_error": float(np.mean(errors_champion)),
        "mean_challenger_error": float(np.mean(errors_challenger)),
        "improvement_pp": float(
            (np.mean(errors_champion) - np.mean(errors_challenger))
            / np.mean(errors_champion) * 100
        ),
    }


def wilcoxon_test(
    errors_champion: np.ndarray,
    errors_challenger: np.ndarray,
    alpha: float = 0.05,
) -> dict:
    """Wilcoxon signed-rank test — dla małych próbek lub braku normalności."""
    stat, p_val = stats.wilcoxon(errors_champion, errors_challenger, alternative="greater")
    return {
        "statistic": float(stat),
        "p_value": float(p_val),
        "significant": p_val < alpha,
    }
```
