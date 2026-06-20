# Continuous Learning System — Learning Loop Architecture, Data Collection, Feedback, Drift Detection

## 1. Learning Loop Architecture

### 1.1 Przegląd systemu

Continuous Learning System (CLS) zamyka pętlę między predykcjami modeli kosztowych
a rzeczywistymi kosztami produkcji. System automatycznie wykrywa degradację modelu,
gromadzi dane treningowe z wielu źródeł, wyzwala retraining i promuje nową wersję
do produkcji — z pełną audytowalnością każdej decyzji.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     Continuous Learning Loop                            │
│                                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐  │
│  │Production│    │  Data        │    │  Drift       │    │Retraining│  │
│  │  Models  │───►│  Collection  │───►│  Detection   │───►│ Pipeline │  │
│  │(CEE/RFQA)│    │  Pipeline    │    │  Engine      │    │          │  │
│  └──────────┘    └──────────────┘    └──────────────┘    └──────────┘  │
│       ▲                │                    │                  │        │
│       │                ▼                    ▼                  ▼        │
│       │          ┌──────────┐    ┌──────────────────┐  ┌──────────┐   │
│       │          │ Feature  │    │   Alert &        │  │  Model   │   │
│       └──────────│  Store   │    │   HITL Gateway   │  │ Registry │   │
│     (promote)    └──────────┘    └──────────────────┘  └──────────┘   │
│                       │                                      │         │
│                       └──────────────────────────────────────┘         │
│                              (training data + evaluation)               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Komponenty i odpowiedzialności

| Komponent | Klasa | Odpowiedzialność |
|-----------|-------|-----------------|
| DataCollectionPipeline | `ActualCostIngester` | Pobieranie rzeczywistych kosztów z ERP/CHE/RFQA |
| FeedbackSystem | `PredictionFeedbackStore` | Łączenie predykcji z aktualami, obliczanie błędów |
| DriftDetectionEngine | `StatisticalDriftDetector` | PSI, KS-test, CUSUM, feature drift |
| RetrainingPipeline | `RetrainingOrchestrator` | Airflow DAG: extract → feature → train → eval → promote |
| FeatureStore | `OfflineFeatureStore` / `OnlineFeatureStore` | Point-in-time correct features, serving |
| ModelRegistry | `MLflowModelRegistry` | Wersjonowanie, staging, champion-challenger |
| EvaluationEngine | `ModelEvaluator` | MAPE, RMSE, Bias, calibration, holdout |
| MonitoringService | `ContinuousMonitor` | Prometheus metrics, dashboards, health checks |

### 1.3 Główna pętla uczenia

```python
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Any
import uuid

class LearningLoopState(Enum):
    MONITORING       = "MONITORING"      # Brak drift — zbieramy dane
    DRIFT_DETECTED   = "DRIFT_DETECTED"  # PSI > threshold, alert wysłany
    RETRAINING       = "RETRAINING"      # Pipeline retrainingu uruchomiony
    EVALUATING       = "EVALUATING"      # Porównanie champion vs challenger
    PROMOTING        = "PROMOTING"       # Nowy model promowany do produkcji
    ROLLBACK         = "ROLLBACK"        # Rollback do poprzedniej wersji
    PAUSED           = "PAUSED"          # Wstrzymane (np. data quality issues)

class DriftType(Enum):
    FEATURE_DRIFT    = "FEATURE_DRIFT"   # Rozkład cech wejściowych zmienił się
    TARGET_DRIFT     = "TARGET_DRIFT"    # Rozkład kosztu rzeczywistego zmienił się
    PREDICTION_DRIFT = "PREDICTION_DRIFT"# Rozkład predykcji zmienił się
    CONCEPT_DRIFT    = "CONCEPT_DRIFT"   # Relacja cech → koszt zmieniła się
    DATA_QUALITY     = "DATA_QUALITY"    # Problemy z jakością danych wejściowych

@dataclass
class DriftSignal:
    signal_id:        str = field(default_factory=lambda: str(uuid.uuid4()))
    model_name:       str = ""
    drift_type:       DriftType = DriftType.CONCEPT_DRIFT
    metric_name:      str = ""
    metric_value:     float = 0.0
    threshold:        float = 0.0
    severity:         str = "WARNING"    # "WARNING" | "CRITICAL"
    features_affected:list[str] = field(default_factory=list)
    detected_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    window_start:     datetime | None = None
    window_end:       datetime | None = None
    recommendation:   str = ""

@dataclass
class LearningCycleRecord:
    cycle_id:         str = field(default_factory=lambda: str(uuid.uuid4()))
    model_name:       str = ""
    trigger_reason:   str = ""          # "SCHEDULED" | "DRIFT" | "MANUAL" | "PERFORMANCE"
    drift_signal_id:  str | None = None
    state:            LearningLoopState = LearningLoopState.MONITORING
    # Data
    training_samples: int = 0
    validation_mape:  float | None = None
    holdout_mape:     float | None = None
    champion_mape:    float | None = None
    # Outcome
    promoted:         bool = False
    new_version:      str | None = None
    rollback:         bool = False
    # Timing
    started_at:       datetime | None = None
    completed_at:     datetime | None = None
    duration_minutes: float | None = None

class ContinuousLearningOrchestrator:
    """
    Top-level coordinator for the continuous learning loop.
    Wakes up periodically (or on drift signal) and drives the cycle.
    """

    MONITORED_MODELS = [
        "cee-unit-cost-xgb",
        "cee-material-cost-lgbm",
        "cee-process-cost-lgbm",
        "cee-overhead-cost-xgb",
        "cee-confidence",
    ]

    def __init__(
        self,
        drift_detector:   "StatisticalDriftDetector",
        retraining_pipe:  "RetrainingOrchestrator",
        model_registry:   "MLflowModelRegistry",
        evaluator:        "ModelEvaluator",
        db:               "asyncpg.Pool",
        kafka:            "AIOKafkaProducer",
    ):
        self._drift    = drift_detector
        self._retrain  = retraining_pipe
        self._registry = model_registry
        self._eval     = evaluator
        self._db       = db
        self._kafka    = kafka

    async def run_monitoring_cycle(self) -> list[DriftSignal]:
        """Called every hour by Airflow/scheduler. Returns detected drift signals."""
        signals: list[DriftSignal] = []

        for model_name in self.MONITORED_MODELS:
            model_signals = await self._drift.detect_all(model_name)
            signals.extend(model_signals)

            for sig in model_signals:
                await self._handle_signal(model_name, sig)

        return signals

    async def _handle_signal(self, model_name: str, signal: DriftSignal) -> None:
        await self._persist_signal(signal)
        await self._emit_event("cls.drift.detected", signal.__dict__)

        if signal.severity == "CRITICAL":
            # Immediate retraining
            cycle = await self.start_retraining(
                model_name=model_name,
                trigger="DRIFT",
                drift_signal_id=signal.signal_id,
            )
        else:
            # WARNING: accumulate signals; retrain when N warnings in window
            count = await self._count_recent_warnings(model_name, hours=24)
            if count >= 3:
                await self.start_retraining(
                    model_name=model_name,
                    trigger="DRIFT",
                    drift_signal_id=signal.signal_id,
                )

    async def start_retraining(
        self,
        model_name:      str,
        trigger:         str,
        drift_signal_id: str | None = None,
    ) -> LearningCycleRecord:
        cycle = LearningCycleRecord(
            model_name=model_name,
            trigger_reason=trigger,
            drift_signal_id=drift_signal_id,
            state=LearningLoopState.RETRAINING,
            started_at=datetime.now(timezone.utc),
        )
        await self._persist_cycle(cycle)
        await self._retrain.trigger(cycle)
        return cycle

    async def complete_cycle(
        self,
        cycle_id:       str,
        evaluation:     "EvaluationResult",
        promoted:       bool,
        new_version:    str | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._db.acquire() as conn:
            await conn.execute(
                """UPDATE cls.learning_cycles
                   SET state=$1, promoted=$2, new_version=$3,
                       validation_mape=$4, holdout_mape=$5, champion_mape=$6,
                       training_samples=$7, completed_at=$8,
                       duration_minutes=EXTRACT(EPOCH FROM ($8 - started_at))/60
                   WHERE cycle_id=$9""",
                LearningLoopState.COMPLETED.value if promoted else LearningLoopState.MONITORING.value,
                promoted, new_version,
                evaluation.validation_mape, evaluation.holdout_mape, evaluation.champion_mape,
                evaluation.n_training, now, cycle_id,
            )
        await self._emit_event(
            "cls.cycle.completed",
            {"cycle_id": cycle_id, "promoted": promoted, "new_version": new_version},
        )

    async def _emit_event(self, topic: str, payload: dict) -> None:
        import json
        await self._kafka.send(
            topic,
            key=payload.get("cycle_id", "cls").encode(),
            value=json.dumps(payload).encode(),
        )

    async def _count_recent_warnings(self, model_name: str, hours: int) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM cls.drift_signals "
                "WHERE model_name=$1 AND severity='WARNING' "
                "AND detected_at > now() - ($2 || ' hours')::INTERVAL",
                model_name, str(hours),
            )
        return int(row["n"]) if row else 0
```

### 1.4 Triggery retrainingu

| Trigger | Typ | Priorytety | Cooldown |
|---------|-----|-----------|---------|
| PSI > 0.20 (feature) | DRIFT | CRITICAL | 0h |
| PSI > 0.10 (feature) | DRIFT | WARNING | 24h |
| MAPE rolling 7d > 15% | PERFORMANCE | CRITICAL | 0h |
| MAPE rolling 7d > 10% | PERFORMANCE | WARNING | 48h |
| CUSUM alarm (bias) | DRIFT | CRITICAL | 0h |
| KS-test p < 0.01 | DRIFT | WARNING | 24h |
| 3 × WARNING w 24h | DRIFT | CRITICAL | 0h |
| Scheduled weekly | SCHEDULED | — | 7d |
| Manual trigger (API) | MANUAL | — | 0h |
| N_actuals > 500 nowych | DATA | — | 14d |

---

## 2. Data Collection Pipeline

### 2.1 Źródła danych rzeczywistych

```python
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import AsyncIterator
import httpx
import asyncpg

class ActualSource(Enum):
    ERP_SAP    = "ERP_SAP"      # SAP CO actual cost orders
    INVOICE    = "INVOICE"       # Supplier invoices (from RFQA)
    RFQA       = "RFQA"          # Confirmed RFQ winner prices
    CHE        = "CHE"           # Cost History Engine confirmed quotes
    MANUAL     = "MANUAL"        # Manual entry by cost engineer

@dataclass
class ActualCostRecord:
    record_id:          str
    estimate_id:        str                  # CEE estimate that is being evaluated
    actual_cost_eur:    float
    actual_source:      ActualSource
    product_code:       str
    material_code:      str
    quantity:           float
    production_location:str
    actual_date:        datetime
    # Components (optional — from ERP breakdown)
    actual_material_eur:  float | None = None
    actual_process_eur:   float | None = None
    actual_overhead_eur:  float | None = None
    actual_scrap_eur:     float | None = None
    # Metadata
    source_reference:   str | None = None   # ERP order number / invoice ID
    ingested_at:        datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    validated:          bool = False
    validation_errors:  list[str] = field(default_factory=list)
```

### 2.2 Ingestory danych

```python
class ERPSAPIngester:
    """
    Pulls actual cost data from SAP CO (Controlling) via REST API.
    Fetches closed production orders where actual costs are settled.
    """

    SAP_API_URL = "http://sap-gateway.internal/api/v1"

    def __init__(self, http: httpx.AsyncClient, db: asyncpg.Pool):
        self._http = http
        self._db   = db

    async def ingest(
        self,
        from_date: datetime,
        to_date:   datetime,
        batch_size: int = 500,
    ) -> int:
        """Returns count of new records ingested."""
        offset  = 0
        total   = 0

        while True:
            try:
                resp = await self._http.get(
                    f"{self.SAP_API_URL}/co/actual-costs",
                    params={
                        "from_date":  from_date.date().isoformat(),
                        "to_date":    to_date.date().isoformat(),
                        "status":     "SETTLED",
                        "limit":      batch_size,
                        "offset":     offset,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                await self._log_error("SAP_FETCH", str(exc))
                break

            orders = data.get("orders", [])
            if not orders:
                break

            records = [self._map_order(o) for o in orders]
            validated = [r for r in records if self._validate(r)]
            await self._upsert_batch(validated)

            total  += len(validated)
            offset += batch_size

            if len(orders) < batch_size:
                break

        return total

    def _map_order(self, order: dict) -> ActualCostRecord:
        return ActualCostRecord(
            record_id           = f"SAP-{order['order_id']}",
            estimate_id         = order.get("rfq_reference", ""),
            actual_cost_eur     = float(order["actual_total_eur"]),
            actual_source       = ActualSource.ERP_SAP,
            product_code        = order.get("material_number", ""),
            material_code       = order.get("material_type", ""),
            quantity            = float(order.get("qty_produced", 1)),
            production_location = order.get("plant_code", ""),
            actual_date         = datetime.fromisoformat(order["settlement_date"]),
            actual_material_eur = order.get("actual_material_eur"),
            actual_process_eur  = order.get("actual_labor_machine_eur"),
            actual_overhead_eur = order.get("actual_overhead_eur"),
            actual_scrap_eur    = order.get("actual_scrap_eur"),
            source_reference    = order["order_id"],
        )

    def _validate(self, r: ActualCostRecord) -> bool:
        errors: list[str] = []
        if r.actual_cost_eur <= 0:
            errors.append("actual_cost_eur must be positive")
        if r.quantity <= 0:
            errors.append("quantity must be positive")
        if not r.product_code:
            errors.append("product_code missing")
        r.validation_errors = errors
        r.validated = len(errors) == 0
        return r.validated

    async def _upsert_batch(self, records: list[ActualCostRecord]) -> None:
        async with self._db.acquire() as conn:
            await conn.executemany(
                """INSERT INTO cls.actual_costs
                   (record_id, estimate_id, actual_cost_eur, actual_source,
                    product_code, material_code, quantity, production_location,
                    actual_date, actual_material_eur, actual_process_eur,
                    actual_overhead_eur, actual_scrap_eur, source_reference,
                    ingested_at, validated)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                   ON CONFLICT (record_id) DO UPDATE
                   SET actual_cost_eur=$3, validated=$16, ingested_at=$15""",
                [
                    (r.record_id, r.estimate_id, r.actual_cost_eur, r.actual_source.value,
                     r.product_code, r.material_code, r.quantity, r.production_location,
                     r.actual_date, r.actual_material_eur, r.actual_process_eur,
                     r.actual_overhead_eur, r.actual_scrap_eur, r.source_reference,
                     r.ingested_at, r.validated)
                    for r in records
                ],
            )

    async def _log_error(self, step: str, message: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO cls.ingestion_errors (step, message, created_at) VALUES ($1,$2,now())",
                step, message,
            )

class RFQActualIngester:
    """Pulls confirmed winner prices from RFQA market_price_index."""

    def __init__(self, rfqa_db: asyncpg.Pool, cls_db: asyncpg.Pool):
        self._rfqa = rfqa_db
        self._cls  = cls_db

    async def ingest(self, since: datetime) -> int:
        rows = await self._rfqa.fetch(
            """SELECT mpi.material_code, mpi.unit_price_eur, mpi.quantity,
                      mpi.location, mpi.rfq_id, mpi.recorded_at,
                      rc.target_price_eur
               FROM rfqa.market_price_index mpi
               JOIN rfqa.rfq_cycles rc ON mpi.rfq_id = rc.rfq_id
               WHERE mpi.is_winner = TRUE AND mpi.recorded_at > $1""",
            since,
        )
        if not rows:
            return 0

        async with self._cls.acquire() as conn:
            await conn.executemany(
                """INSERT INTO cls.actual_costs
                   (record_id, estimate_id, actual_cost_eur, actual_source,
                    product_code, material_code, quantity, production_location,
                    actual_date, source_reference, ingested_at, validated)
                   VALUES (gen_random_uuid(), $1, $2, 'RFQA', $3, $4, $5, $6, $7, $8, now(), TRUE)
                   ON CONFLICT DO NOTHING""",
                [
                    (str(r["rfq_id"]), float(r["unit_price_eur"]), r["material_code"],
                     r["material_code"], float(r["quantity"]), r["location"] or "XX",
                     r["recorded_at"], str(r["rfq_id"]))
                    for r in rows
                ],
            )
        return len(rows)
```

### 2.3 Data Quality Validator

```python
import numpy as np
from dataclasses import dataclass

@dataclass
class DataQualityReport:
    total_records:       int
    valid_records:       int
    invalid_records:     int
    null_rate:           dict[str, float]
    outlier_count:       int
    duplicate_count:     int
    coverage_pct:        float        # % estimates that have matching actuals
    issues:              list[str]
    passed:              bool

class DataQualityValidator:
    """Validates the incoming actual cost batch before it enters training."""

    REQUIRED_FIELDS = [
        "actual_cost_eur", "product_code", "material_code",
        "quantity", "production_location", "actual_date",
    ]
    MAX_NULL_RATE    = 0.05
    OUTLIER_Z_SCORE  = 4.0
    MIN_COVERAGE_PCT = 0.30     # At least 30% of estimates need actuals

    def validate(self, records: list[ActualCostRecord]) -> DataQualityReport:
        issues: list[str] = []
        n = len(records)

        # Null rates
        null_rates: dict[str, float] = {}
        for field_name in self.REQUIRED_FIELDS:
            null_count = sum(1 for r in records if not getattr(r, field_name, None))
            null_rates[field_name] = null_count / max(n, 1)
            if null_rates[field_name] > self.MAX_NULL_RATE:
                issues.append(f"{field_name} null rate {null_rates[field_name]:.1%} > {self.MAX_NULL_RATE:.0%}")

        # Outlier detection (Z-score on actual_cost_eur)
        costs = np.array([r.actual_cost_eur for r in records if r.actual_cost_eur > 0])
        if len(costs) > 10:
            z       = np.abs((costs - np.mean(costs)) / np.std(costs))
            outliers = int(np.sum(z > self.OUTLIER_Z_SCORE))
            if outliers > 0:
                issues.append(f"{outliers} outlier records (Z > {self.OUTLIER_Z_SCORE})")
        else:
            outliers = 0

        # Duplicates (by record_id)
        ids       = [r.record_id for r in records]
        dup_count = len(ids) - len(set(ids))
        if dup_count > 0:
            issues.append(f"{dup_count} duplicate record_ids")

        valid   = sum(1 for r in records if r.validated)
        invalid = n - valid
        passed  = len(issues) == 0 and invalid / max(n, 1) < 0.10

        return DataQualityReport(
            total_records   = n,
            valid_records   = valid,
            invalid_records = invalid,
            null_rate       = null_rates,
            outlier_count   = outliers,
            duplicate_count = dup_count,
            coverage_pct    = valid / max(n, 1),
            issues          = issues,
            passed          = passed,
        )
```

### 2.4 Airflow DAG — ingestion

```python
from airflow.decorators import dag, task
from datetime import datetime, timedelta

@dag(
    dag_id="cls_data_ingestion",
    schedule_interval="0 * * * *",     # Every hour
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args={"retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["cls", "ingestion"],
)
def cls_data_ingestion_dag():

    @task
    def ingest_sap() -> dict:
        from cls.ingestion import ERPSAPIngester
        from datetime import timezone
        ingester = ERPSAPIngester.from_env()
        since    = datetime.now(timezone.utc) - timedelta(hours=2)  # 2h overlap
        n        = ingester.ingest_sync(since, datetime.now(timezone.utc))
        return {"source": "SAP", "records": n}

    @task
    def ingest_rfqa() -> dict:
        from cls.ingestion import RFQActualIngester
        ingester = RFQActualIngester.from_env()
        n        = ingester.ingest_sync(datetime.now() - timedelta(hours=2))
        return {"source": "RFQA", "records": n}

    @task
    def ingest_che() -> dict:
        from cls.ingestion import CHEIngester
        ingester = CHEIngester.from_env()
        n        = ingester.ingest_sync(datetime.now() - timedelta(hours=2))
        return {"source": "CHE", "records": n}

    @task
    def validate_and_emit(sap: dict, rfqa: dict, che: dict) -> dict:
        from cls.quality import DataQualityValidator
        from cls.events import emit_ingestion_complete
        total  = sap["records"] + rfqa["records"] + che["records"]
        report = DataQualityValidator.validate_latest_batch()
        if not report.passed:
            raise ValueError(f"Data quality failed: {report.issues}")
        emit_ingestion_complete(total, report)
        return {"total": total, "passed": report.passed}

    sap  = ingest_sap()
    rfqa = ingest_rfqa()
    che  = ingest_che()
    validate_and_emit(sap, rfqa, che)

cls_data_ingestion_dag()
```

---

## 3. Feedback System

### 3.1 PredictionFeedbackStore

```python
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

@dataclass
class PredictionError:
    prediction_id:      str
    estimate_id:        str
    model_name:         str
    model_version:      str
    predicted_cost_eur: float
    actual_cost_eur:    float
    absolute_error:     float
    relative_error:     float         # (actual - predicted) / actual
    mape_contribution:  float         # abs(relative_error)
    within_ci:          bool          # Was actual inside [cost_low, cost_high]?
    product_code:       str
    material_code:      str
    production_location:str
    complexity_class:   str
    annual_volume:      int
    prediction_date:    datetime
    actual_date:        datetime
    lag_days:           int           # actual_date - prediction_date

class PredictionFeedbackStore:
    """
    Joins CEE predictions with actual costs to produce error metrics.
    The central data structure for model learning.
    """

    def __init__(self, db: asyncpg.Pool):
        self._db = db

    async def compute_and_store(
        self,
        actual: ActualCostRecord,
    ) -> PredictionError | None:
        """Find matching prediction and compute error."""
        pred_row = await self._find_prediction(actual.estimate_id, actual.product_code)
        if not pred_row:
            return None

        predicted  = float(pred_row["predicted_cost_eur"])
        actual_val = actual.actual_cost_eur
        abs_err    = abs(actual_val - predicted)
        rel_err    = (actual_val - predicted) / max(actual_val, 0.01)

        error = PredictionError(
            prediction_id       = str(pred_row["prediction_id"]),
            estimate_id         = actual.estimate_id,
            model_name          = pred_row["model_name"],
            model_version       = pred_row["model_version"],
            predicted_cost_eur  = predicted,
            actual_cost_eur     = actual_val,
            absolute_error      = abs_err,
            relative_error      = rel_err,
            mape_contribution   = abs(rel_err),
            within_ci           = (
                float(pred_row["cost_low_eur"]) <= actual_val
                <= float(pred_row["cost_high_eur"])
            ) if pred_row["cost_low_eur"] else False,
            product_code        = actual.product_code,
            material_code       = actual.material_code,
            production_location = actual.production_location,
            complexity_class    = pred_row.get("complexity_class", "UNKNOWN"),
            annual_volume       = pred_row.get("annual_volume", 0),
            prediction_date     = pred_row["prediction_date"],
            actual_date         = actual.actual_date,
            lag_days            = (actual.actual_date - pred_row["prediction_date"].replace(
                                   tzinfo=timezone.utc)).days,
        )

        await self._persist_error(error)
        return error

    async def _find_prediction(
        self, estimate_id: str, product_code: str
    ) -> asyncpg.Record | None:
        async with self._db.acquire() as conn:
            return await conn.fetchrow(
                """SELECT mp.prediction_id, mp.model_name, mp.model_version,
                          mp.predicted_cost_eur, ce.cost_low_eur, ce.cost_high_eur,
                          ce.created_at AS prediction_date,
                          ei.complexity_class, ei.annual_volume
                   FROM cee.ml_predictions mp
                   JOIN cee.cost_estimates ce ON mp.estimate_id = ce.estimate_id
                   JOIN cee.estimation_inputs ei ON ce.input_id = ei.input_id
                   WHERE ce.estimate_id = $1
                   ORDER BY mp.created_at DESC
                   LIMIT 1""",
                estimate_id,
            )

    async def _persist_error(self, error: PredictionError) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO cls.prediction_errors
                   (prediction_id, estimate_id, model_name, model_version,
                    predicted_cost_eur, actual_cost_eur, absolute_error,
                    relative_error, mape_contribution, within_ci,
                    product_code, material_code, production_location,
                    complexity_class, annual_volume,
                    prediction_date, actual_date, lag_days)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                   ON CONFLICT (prediction_id) DO UPDATE
                   SET actual_cost_eur=$6, absolute_error=$7, relative_error=$8,
                       mape_contribution=$9, within_ci=$10""",
                error.prediction_id, error.estimate_id, error.model_name,
                error.model_version, error.predicted_cost_eur, error.actual_cost_eur,
                error.absolute_error, error.relative_error, error.mape_contribution,
                error.within_ci, error.product_code, error.material_code,
                error.production_location, error.complexity_class, error.annual_volume,
                error.prediction_date, error.actual_date, error.lag_days,
            )
```

### 3.2 Rolling Metrics Calculator

```python
@dataclass
class RollingMetrics:
    model_name:      str
    window_days:     int
    n_samples:       int
    mape_pct:        float
    rmse_eur:        float
    mae_eur:         float
    bias_pct:        float      # Signed: positive = over-prediction
    within_ci_pct:   float
    by_location:     dict[str, float]   # location → MAPE
    by_complexity:   dict[str, float]   # complexity → MAPE
    by_volume_tier:  dict[str, float]   # LOW/MED/HIGH → MAPE
    computed_at:     datetime

class RollingMetricsCalculator:

    def __init__(self, db: asyncpg.Pool):
        self._db = db

    async def compute(
        self, model_name: str, window_days: int = 7
    ) -> RollingMetrics:
        async with self._db.acquire() as conn:
            # Core metrics
            core = await conn.fetchrow(
                """SELECT
                     COUNT(*) AS n,
                     AVG(mape_contribution) * 100 AS mape_pct,
                     SQRT(AVG(POWER(actual_cost_eur - predicted_cost_eur, 2))) AS rmse_eur,
                     AVG(ABS(actual_cost_eur - predicted_cost_eur)) AS mae_eur,
                     AVG(relative_error) * 100 AS bias_pct,
                     AVG(within_ci::INT::FLOAT) * 100 AS within_ci_pct
                   FROM cls.prediction_errors
                   WHERE model_name = $1
                     AND actual_date > now() - ($2 || ' days')::INTERVAL""",
                model_name, str(window_days),
            )

            # Breakdown by location
            loc_rows = await conn.fetch(
                """SELECT production_location,
                          AVG(mape_contribution) * 100 AS mape_pct
                   FROM cls.prediction_errors
                   WHERE model_name=$1
                     AND actual_date > now() - ($2 || ' days')::INTERVAL
                   GROUP BY production_location""",
                model_name, str(window_days),
            )

            # Breakdown by complexity
            comp_rows = await conn.fetch(
                """SELECT complexity_class,
                          AVG(mape_contribution) * 100 AS mape_pct
                   FROM cls.prediction_errors
                   WHERE model_name=$1
                     AND actual_date > now() - ($2 || ' days')::INTERVAL
                   GROUP BY complexity_class""",
                model_name, str(window_days),
            )

            # Breakdown by volume tier
            vol_rows = await conn.fetch(
                """SELECT
                     CASE
                       WHEN annual_volume < 50    THEN 'LOW'
                       WHEN annual_volume < 5000  THEN 'MEDIUM'
                       ELSE 'HIGH'
                     END AS tier,
                     AVG(mape_contribution) * 100 AS mape_pct
                   FROM cls.prediction_errors
                   WHERE model_name=$1
                     AND actual_date > now() - ($2 || ' days')::INTERVAL
                   GROUP BY tier""",
                model_name, str(window_days),
            )

        return RollingMetrics(
            model_name    = model_name,
            window_days   = window_days,
            n_samples     = int(core["n"]),
            mape_pct      = round(float(core["mape_pct"] or 0), 4),
            rmse_eur      = round(float(core["rmse_eur"] or 0), 4),
            mae_eur       = round(float(core["mae_eur"] or 0), 4),
            bias_pct      = round(float(core["bias_pct"] or 0), 4),
            within_ci_pct = round(float(core["within_ci_pct"] or 0), 4),
            by_location   = {r["production_location"]: round(float(r["mape_pct"]), 2) for r in loc_rows},
            by_complexity = {r["complexity_class"]: round(float(r["mape_pct"]), 2) for r in comp_rows},
            by_volume_tier= {r["tier"]: round(float(r["mape_pct"]), 2) for r in vol_rows},
            computed_at   = datetime.now(timezone.utc),
        )
```

---

## 4. Drift Detection

### 4.1 Population Stability Index (PSI)

```python
import numpy as np
from scipy import stats as scipy_stats

class PSICalculator:
    """
    Population Stability Index: measures shift in feature distributions.
    PSI < 0.10 → stable
    PSI 0.10–0.20 → minor shift (WARNING)
    PSI > 0.20 → major shift (CRITICAL → retrain)
    """

    N_BINS         = 10
    EPSILON        = 1e-6   # Avoid log(0)
    WARN_THRESHOLD = 0.10
    CRIT_THRESHOLD = 0.20

    def compute(
        self,
        reference: np.ndarray,
        current:   np.ndarray,
    ) -> float:
        if len(reference) < 50 or len(current) < 20:
            return 0.0

        # Build bins on reference distribution
        bins   = np.percentile(reference, np.linspace(0, 100, self.N_BINS + 1))
        bins   = np.unique(bins)           # Handle duplicates

        ref_pct = np.histogram(reference, bins=bins)[0] / len(reference)
        cur_pct = np.histogram(current,   bins=bins)[0] / len(current)

        ref_pct = np.clip(ref_pct, self.EPSILON, None)
        cur_pct = np.clip(cur_pct, self.EPSILON, None)

        # Normalize
        ref_pct /= ref_pct.sum()
        cur_pct /= cur_pct.sum()

        psi = float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))
        return round(psi, 6)

    def severity(self, psi: float) -> str:
        if psi >= self.CRIT_THRESHOLD:
            return "CRITICAL"
        if psi >= self.WARN_THRESHOLD:
            return "WARNING"
        return "OK"
```

### 4.2 CUSUM — wykrywanie bias drift

```python
@dataclass
class CUSUMState:
    cusum_pos: float = 0.0      # Cumulative upward deviation
    cusum_neg: float = 0.0      # Cumulative downward deviation
    n_samples: int   = 0
    alarm:     bool  = False
    alarm_type:str   = ""       # "OVER_PREDICTION" | "UNDER_PREDICTION"

class CUSUMDetector:
    """
    CUSUM (Cumulative Sum) control chart for detecting sustained bias drift.
    Uses prediction errors (actual - predicted) as the monitored process.
    """

    def __init__(
        self,
        target_mean: float = 0.0,    # Expected mean error (0 = unbiased)
        k_sigma:     float = 0.5,    # Allowable slack in standard deviations
        h_sigma:     float = 5.0,    # Decision threshold in standard deviations
        sigma:       float | None = None,
    ):
        self._target = target_mean
        self._k      = k_sigma
        self._h      = h_sigma
        self._sigma  = sigma or 0.10  # 10% default std of relative errors
        self._state  = CUSUMState()

    def update(self, relative_error: float) -> CUSUMState:
        """Feed one new observation (relative error). Returns updated state."""
        # Standardize
        z = (relative_error - self._target) / self._sigma

        # Two-sided CUSUM
        self._state.cusum_pos = max(0, self._state.cusum_pos + z - self._k)
        self._state.cusum_neg = max(0, self._state.cusum_neg - z - self._k)
        self._state.n_samples += 1

        h_threshold = self._h
        alarm = False
        alarm_type = ""

        if self._state.cusum_pos > h_threshold:
            alarm      = True
            alarm_type = "UNDER_PREDICTION"   # Model consistently predicts too low
            self._state.cusum_pos = 0          # Reset after alarm
        elif self._state.cusum_neg > h_threshold:
            alarm      = True
            alarm_type = "OVER_PREDICTION"    # Model consistently predicts too high
            self._state.cusum_neg = 0

        self._state.alarm      = alarm
        self._state.alarm_type = alarm_type
        return self._state

    def reset(self) -> None:
        self._state = CUSUMState()
```

### 4.3 StatisticalDriftDetector — kompleksowa detekcja

```python
from scipy.stats import ks_2samp
import pandas as pd

@dataclass
class DriftReport:
    model_name:     str
    drift_signals:  list[DriftSignal]
    psi_by_feature: dict[str, float]
    ks_pvalue:      float
    cusum_state:    CUSUMState
    overall_severity: str        # "OK" | "WARNING" | "CRITICAL"
    computed_at:    datetime

class StatisticalDriftDetector:
    """
    Comprehensive drift detector combining PSI, KS-test, CUSUM, and feature-level analysis.
    """

    REFERENCE_DAYS = 90        # Training data window
    CURRENT_DAYS   = 14        # Recent predictions window
    MIN_SAMPLES    = 30        # Minimum samples for drift detection

    MONITORED_FEATURES = [
        "volume_cm3", "price_eur_per_kg", "annual_volume",
        "total_cycle_time_sec", "buy_to_fly_ratio",
        "feature_count", "location_cost_index",
    ]

    def __init__(self, db: asyncpg.Pool):
        self._db     = db
        self._psi    = PSICalculator()
        self._cusums: dict[str, CUSUMDetector] = {}

    async def detect_all(self, model_name: str) -> list[DriftSignal]:
        signals: list[DriftSignal] = []

        # 1. MAPE performance drift
        mape_signal = await self._check_mape_drift(model_name)
        if mape_signal:
            signals.append(mape_signal)

        # 2. Feature distribution drift (PSI)
        feat_signals = await self._check_feature_drift(model_name)
        signals.extend(feat_signals)

        # 3. Target distribution drift (KS-test on actual costs)
        ks_signal = await self._check_target_drift(model_name)
        if ks_signal:
            signals.append(ks_signal)

        # 4. Bias drift (CUSUM)
        bias_signal = await self._check_bias_drift(model_name)
        if bias_signal:
            signals.append(bias_signal)

        return signals

    async def _check_mape_drift(self, model_name: str) -> DriftSignal | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                     AVG(mape_contribution) * 100 AS mape_7d
                   FROM cls.prediction_errors
                   WHERE model_name=$1
                     AND actual_date > now() - INTERVAL '7 days'""",
                model_name,
            )

        if not row or not row["mape_7d"]:
            return None

        mape = float(row["mape_7d"])

        if mape > 15.0:
            return DriftSignal(
                model_name=model_name, drift_type=DriftType.CONCEPT_DRIFT,
                metric_name="mape_7d", metric_value=mape, threshold=15.0,
                severity="CRITICAL",
                recommendation=f"MAPE {mape:.1f}% > 15% — immediate retraining required",
            )
        if mape > 10.0:
            return DriftSignal(
                model_name=model_name, drift_type=DriftType.CONCEPT_DRIFT,
                metric_name="mape_7d", metric_value=mape, threshold=10.0,
                severity="WARNING",
                recommendation=f"MAPE {mape:.1f}% > 10% — schedule retraining",
            )
        return None

    async def _check_feature_drift(self, model_name: str) -> list[DriftSignal]:
        signals: list[DriftSignal] = []

        ref_rows = await self._load_feature_vectors(model_name, days=self.REFERENCE_DAYS)
        cur_rows = await self._load_feature_vectors(model_name, days=self.CURRENT_DAYS)

        if len(ref_rows) < self.MIN_SAMPLES or len(cur_rows) < self.MIN_SAMPLES:
            return signals

        ref_df = pd.DataFrame(ref_rows)
        cur_df = pd.DataFrame(cur_rows)

        for feat in self.MONITORED_FEATURES:
            if feat not in ref_df.columns or feat not in cur_df.columns:
                continue
            ref_vals = ref_df[feat].dropna().values
            cur_vals = cur_df[feat].dropna().values
            if len(ref_vals) < 10 or len(cur_vals) < 10:
                continue

            psi = self._psi.compute(ref_vals, cur_vals)
            severity = self._psi.severity(psi)

            if severity != "OK":
                signals.append(DriftSignal(
                    model_name=model_name, drift_type=DriftType.FEATURE_DRIFT,
                    metric_name=f"psi_{feat}", metric_value=psi,
                    threshold=self._psi.WARN_THRESHOLD,
                    severity=severity,
                    features_affected=[feat],
                    recommendation=f"Feature '{feat}' PSI={psi:.3f} — distribution shifted",
                ))

        return signals

    async def _check_target_drift(self, model_name: str) -> DriftSignal | None:
        async with self._db.acquire() as conn:
            ref = await conn.fetch(
                "SELECT actual_cost_eur FROM cls.actual_costs "
                "WHERE ingested_at BETWEEN now()-INTERVAL '90 days' AND now()-INTERVAL '14 days'",
            )
            cur = await conn.fetch(
                "SELECT actual_cost_eur FROM cls.actual_costs "
                "WHERE ingested_at > now() - INTERVAL '14 days'",
            )

        ref_vals = np.array([float(r["actual_cost_eur"]) for r in ref])
        cur_vals = np.array([float(r["actual_cost_eur"]) for r in cur])

        if len(ref_vals) < self.MIN_SAMPLES or len(cur_vals) < self.MIN_SAMPLES:
            return None

        _, p_value = ks_2samp(ref_vals, cur_vals)

        if p_value < 0.01:
            return DriftSignal(
                model_name=model_name, drift_type=DriftType.TARGET_DRIFT,
                metric_name="ks_pvalue_target", metric_value=p_value, threshold=0.01,
                severity="CRITICAL" if p_value < 0.001 else "WARNING",
                recommendation=f"Target distribution shifted (KS p={p_value:.4f})",
            )
        return None

    async def _check_bias_drift(self, model_name: str) -> DriftSignal | None:
        cusum = self._cusums.setdefault(model_name, CUSUMDetector())

        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT relative_error FROM cls.prediction_errors "
                "WHERE model_name=$1 ORDER BY actual_date DESC LIMIT 100",
                model_name,
            )

        cusum.reset()
        final_state = CUSUMState()
        for row in reversed(rows):
            final_state = cusum.update(float(row["relative_error"]))

        if final_state.alarm:
            return DriftSignal(
                model_name=model_name, drift_type=DriftType.CONCEPT_DRIFT,
                metric_name="cusum_bias", metric_value=max(
                    final_state.cusum_pos, final_state.cusum_neg
                ),
                threshold=5.0,
                severity="CRITICAL",
                recommendation=f"CUSUM alarm: {final_state.alarm_type} — sustained prediction bias detected",
            )
        return None

    async def _load_feature_vectors(
        self, model_name: str, days: int
    ) -> list[dict]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT mp.feature_vector
                   FROM cee.ml_predictions mp
                   WHERE mp.model_name=$1
                     AND mp.created_at > now() - ($2 || ' days')::INTERVAL
                   LIMIT 5000""",
                model_name, str(days),
            )
        import json
        return [json.loads(r["feature_vector"]) for r in rows]
```
