# CLS — Sekcje 13–17: Alerting, Testing, Scalability, Risks, Roadmap

---

## 13. Alerting

### 13.1 Reguły Alertmanager

```yaml
# alertmanager/rules/cls_alerts.yaml
groups:
  - name: cls_model_quality
    interval: 5m
    rules:

      - alert: CLSModelMAPECritical
        expr: cls_model_mape{window_days="7"} > 15
        for: 15m
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "MAPE {{ $labels.model_name }} critically high: {{ $value | printf \"%.1f\" }}%"
          description: |
            Model {{ $labels.model_name }} 7-day MAPE = {{ $value | printf \"%.1f\" }}%
            (threshold: 15%). Automatic retraining may be triggered.
          runbook: https://wiki/cls/runbooks/high-mape
          dashboard: https://grafana/d/cls-accuracy

      - alert: CLSModelMAPEWarning
        expr: cls_model_mape{window_days="7"} > 10
        for: 30m
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "MAPE {{ $labels.model_name }} elevated: {{ $value | printf \"%.1f\" }}%"
          description: "7-day MAPE > 10% for {{ $labels.model_name }}. Monitor closely."

      - alert: CLSModelBiasCritical
        expr: abs(cls_model_bias_pct{window_days="7"}) > 5
        for: 15m
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "Bias critical for {{ $labels.model_name }}: {{ $value | printf \"%.1f\" }}%"
          description: |
            Systematic bias detected in {{ $labels.model_name }}.
            Positive = over-prediction, negative = under-prediction.

      - alert: CLSModelBiasWarning
        expr: abs(cls_model_bias_pct{window_days="7"}) > 3
        for: 30m
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Bias warning for {{ $labels.model_name }}: {{ $value | printf \"%.1f\" }}%"

  - name: cls_drift
    interval: 5m
    rules:

      - alert: CLSDriftCriticalDetected
        expr: increase(cls_drift_signals_total{severity="CRITICAL"}[15m]) > 0
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "Critical drift detected for {{ $labels.model_name }}"
          description: |
            Drift type {{ $labels.drift_type }} critical for {{ $labels.model_name }}.
            Automatic retraining pipeline will start within 5 minutes.
          runbook: https://wiki/cls/runbooks/drift-critical

      - alert: CLSDriftWarningAccumulation
        expr: |
          sum by (model_name) (
            increase(cls_drift_signals_total{severity="WARNING"}[24h])
          ) >= 3
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Multiple drift warnings for {{ $labels.model_name }}"
          description: "≥3 WARNING drift signals in 24h for {{ $labels.model_name }}. Retraining scheduled."

      - alert: CLSFeaturePSIHigh
        expr: cls_feature_psi > 0.20
        for: 10m
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "PSI critical for {{ $labels.feature_name }} ({{ $labels.model_name }})"
          description: "PSI = {{ $value | printf \"%.3f\" }} > 0.20 for feature {{ $labels.feature_name }}."

  - name: cls_retraining
    interval: 5m
    rules:

      - alert: CLSRetrainingJobFailed
        expr: increase(cls_retraining_jobs_total{status="FAILED"}[10m]) > 0
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "Retraining job FAILED for {{ $labels.model_name }}"
          description: "Trigger: {{ $labels.trigger }}. Check CLS dashboard and logs."
          runbook: https://wiki/cls/runbooks/retrain-failed

      - alert: CLSRetrainingDurationHigh
        expr: cls_retraining_duration_minutes > 180
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Retraining taking > 3h for {{ $labels.model_name }}"

      - alert: CLSNoRecentRetraining
        expr: |
          (time() - cls_last_retrain_timestamp) > 864000  # 10 dni
        for: 1h
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "No retraining in 10+ days for {{ $labels.model_name }}"

      - alert: CLSRollbackOccurred
        expr: increase(cls_model_rollbacks_total[5m]) > 0
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "MODEL ROLLBACK for {{ $labels.model_name }}: {{ $labels.reason }}"
          description: "Investigate immediately. Check evaluation metrics and challenger performance."

  - name: cls_data
    interval: 5m
    rules:

      - alert: CLSInsufficientActualData
        expr: cls_training_samples_available < 200
        for: 6h
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Low actual cost coverage for {{ $labels.model_name }}"
          description: "Only {{ $value }} samples available. Check SAP/RFQA integration."
          runbook: https://wiki/cls/runbooks/low-actual-data

      - alert: CLSDataQualityFailureHigh
        expr: |
          rate(cls_data_quality_failures_total[15m]) /
          rate(cls_actuals_ingested_total[15m]) > 0.10
        for: 15m
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Data quality failure rate > 10% for {{ $labels.source }}"

      - alert: CLSActualLagHigh
        expr: histogram_quantile(0.90, cls_actual_lag_days_bucket) > 90
        for: 1h
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "90th pct actual lag > 90 days for {{ $labels.source }}"
          description: "Actual costs arriving too late for effective drift detection."

  - name: cls_infrastructure
    interval: 5m
    rules:

      - alert: CLSOutboxLagHigh
        expr: cls_outbox_unpublished_count > 500
        for: 10m
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "CLS outbox lag: {{ $value }} unpublished events"
          description: "Check Kafka connectivity and outbox relay worker."

      - alert: CLSOutboxLagCritical
        expr: cls_outbox_unpublished_count > 2000
        for: 5m
        labels:
          severity: critical
          team: ml-ops
          module: cls
        annotations:
          summary: "CLS outbox CRITICAL: {{ $value }} unpublished events"

      - alert: CLSOnlineStoreHitRateLow
        expr: |
          rate(cls_online_store_cache_hits_total{result="hit"}[10m]) /
          rate(cls_online_store_cache_hits_total[10m]) < 0.80
        for: 15m
        labels:
          severity: warning
          team: ml-ops
          module: cls
        annotations:
          summary: "Online feature store hit rate < 80% for {{ $labels.feature_group }}"
```

### 13.2 Routing Alertmanager

```yaml
# alertmanager/config.yaml (fragment)
route:
  group_by: [alertname, model_name]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: ml-ops-default

  routes:
    - match:
        severity: critical
        module: cls
      receiver: ml-ops-pagerduty
      repeat_interval: 1h

    - match:
        alertname: CLSRollbackOccurred
      receiver: ml-ops-pagerduty
      repeat_interval: 30m

    - match:
        severity: warning
        module: cls
      receiver: ml-ops-slack

receivers:
  - name: ml-ops-pagerduty
    pagerduty_configs:
      - routing_key: "<PAGERDUTY_KEY>"
        description: "{{ .GroupLabels.alertname }}: {{ .CommonAnnotations.summary }}"

  - name: ml-ops-slack
    slack_configs:
      - api_url: "<SLACK_WEBHOOK>"
        channel: "#ml-ops-alerts"
        title: "CLS Alert: {{ .GroupLabels.alertname }}"
        text: "{{ range .Alerts }}{{ .Annotations.description }}\n{{ end }}"
        color: '{{ if eq .Status "firing" }}{{ if eq .CommonLabels.severity "critical" }}danger{{ else }}warning{{ end }}{{ else }}good{{ end }}'

  - name: ml-ops-default
    slack_configs:
      - api_url: "<SLACK_WEBHOOK>"
        channel: "#ml-ops-info"
```

---

## 14. Testing

### 14.1 Macierz testów

| Typ | Narzędzie | Zakres | Pokrycie |
|-----|-----------|--------|----------|
| Unit | pytest | Metryki (MAPE, RMSE, Bias), PSI, CUSUM, stat tests | 90%+ |
| Integration | pytest + Testcontainers | DB schema, ingestion pipeline, outbox relay | 80%+ |
| ML Model | pytest + MLflow | Trening, predykcja, feature schema, CV | 80%+ |
| Drift Detection | pytest | PSI thresholds, CUSUM alarm, KS-test, end-to-end | 85%+ |
| Retraining Pipeline | pytest + Airflow test | DAG tasks, trigger logic, promotion/rollback | 75%+ |
| API Contract | schemathesis | OpenAPI spec vs implementation | 100% endpoints |
| Load | k6 | Ingestion throughput, metrics read latency | steady state |
| Data Quality | pytest | Validator rules, null rates, outlier detection | 90%+ |

### 14.2 Testy jednostkowe

```python
# tests/unit/test_metrics.py
import numpy as np
import pytest
from cls.evaluation import ModelEvaluator


class TestMAPE:
    def test_perfect_prediction(self):
        y_true = np.array([100.0, 200.0, 300.0])
        y_pred = np.array([100.0, 200.0, 300.0])
        assert ModelEvaluator._mape(y_true, y_pred) == pytest.approx(0.0)

    def test_10pct_error(self):
        y_true = np.array([100.0, 100.0])
        y_pred = np.array([110.0, 90.0])
        assert ModelEvaluator._mape(y_true, y_pred) == pytest.approx(10.0)

    def test_excludes_zero_actual(self):
        y_true = np.array([0.0, 100.0])
        y_pred = np.array([50.0, 110.0])
        # Zero excluded → only second element
        assert ModelEvaluator._mape(y_true, y_pred) == pytest.approx(10.0)

    def test_asymmetry(self):
        # MAPE over/under-prediction nie jest symetryczne
        y_true = np.array([100.0])
        y_pred_over = np.array([200.0])   # +100%
        y_pred_under = np.array([50.0])   # -50%
        assert ModelEvaluator._mape(y_true, y_pred_over) > ModelEvaluator._mape(y_true, y_pred_under)


class TestBias:
    def test_over_prediction_positive(self):
        y_true = np.array([100.0])
        y_pred = np.array([110.0])
        assert ModelEvaluator._bias_pct(y_true, y_pred) == pytest.approx(10.0)

    def test_under_prediction_negative(self):
        y_true = np.array([100.0])
        y_pred = np.array([90.0])
        assert ModelEvaluator._bias_pct(y_true, y_pred) == pytest.approx(-10.0)

    def test_unbiased(self):
        y_true = np.array([100.0, 100.0])
        y_pred = np.array([110.0, 90.0])
        assert ModelEvaluator._bias_pct(y_true, y_pred) == pytest.approx(0.0)


# tests/unit/test_psi.py
from cls.drift import PSICalculator
import numpy as np


class TestPSICalculator:
    def setup_method(self):
        self.calc = PSICalculator()

    def test_identical_distributions_near_zero(self):
        ref = np.random.normal(100, 10, 1000)
        psi = self.calc.compute(ref, ref)
        assert psi < 0.01

    def test_shifted_distribution_warning(self):
        ref = np.random.normal(100, 10, 1000)
        cur = np.random.normal(115, 10, 500)  # +1.5 sigma shift
        psi = self.calc.compute(ref, cur)
        assert psi > 0.10

    def test_severity_thresholds(self):
        assert self.calc.severity(0.05) == "OK"
        assert self.calc.severity(0.15) == "WARNING"
        assert self.calc.severity(0.25) == "CRITICAL"

    def test_psi_formula(self):
        # Ręczna weryfikacja PSI dla 2 binów
        ref = np.array([1.0] * 50 + [2.0] * 50)
        cur = np.array([1.0] * 80 + [2.0] * 20)
        psi = self.calc.compute(ref, cur)
        # expected ≈ (0.80-0.50)*ln(0.80/0.50) + (0.20-0.50)*ln(0.20/0.50)
        assert psi > 0.20


# tests/unit/test_cusum.py
from cls.drift import CUSUMDetector, CUSUMState
import pytest


class TestCUSUMDetector:
    def setup_method(self):
        self.detector = CUSUMDetector(k_sigma=0.5, h_sigma=5.0, sigma=0.10)

    def test_no_alarm_stable(self):
        state = CUSUMState(0, 0, 0, False, None)
        for _ in range(20):
            state = self.detector.update(np.random.normal(0, 0.05), state)
        assert not state.alarm

    def test_alarm_over_prediction(self):
        """Ciągłe over-prediction → alarm OVER_PREDICTION."""
        state = CUSUMState(0, 0, 0, False, None)
        for _ in range(30):
            state = self.detector.update(0.15, state)  # +15% over-prediction
        assert state.alarm
        assert state.alarm_type == "OVER_PREDICTION"

    def test_alarm_under_prediction(self):
        state = CUSUMState(0, 0, 0, False, None)
        for _ in range(30):
            state = self.detector.update(-0.15, state)  # -15% under-prediction
        assert state.alarm
        assert state.alarm_type == "UNDER_PREDICTION"

    def test_reset_after_alarm(self):
        state = CUSUMState(10.0, 0, 0, True, "OVER_PREDICTION")
        state = self.detector.update(0.0, state)
        assert state.cusum_pos == pytest.approx(0.0)  # reset
```

### 14.3 Testy integracyjne

```python
# tests/integration/test_ingestion.py
import pytest
import asyncpg
from testcontainers.postgres import PostgresContainer
from cls.data_collection import ERPSAPIngester, DataQualityValidator
from datetime import date, timedelta


@pytest.fixture(scope="module")
async def pg_container():
    with PostgresContainer("postgres:16") as pg:
        pool = await asyncpg.create_pool(pg.get_connection_url())
        # Apply migrations
        async with pool.acquire() as conn:
            await conn.execute(open("migrations/cls_schema.sql").read())
        yield pool
        await pool.close()


@pytest.mark.asyncio
async def test_sap_ingestion_deduplication(pg_container, mocker):
    """Weryfikacja że duplikaty nie są ingesterowane dwa razy."""
    mocker.patch(
        "cls.data_collection.ERPSAPIngester._fetch_sap_orders",
        return_value=[
            {"sap_order": "1001", "material": "ST-01", "unit_cost": 150.0, "settled_date": date.today()},
            {"sap_order": "1001", "material": "ST-01", "unit_cost": 150.0, "settled_date": date.today()},
        ],
    )
    ingester = ERPSAPIngester(pg_container, sap_url="http://mock")
    n = await ingester.ingest(date.today() - timedelta(days=7), date.today())
    assert n == 1  # Tylko jeden insert, nie dwa


@pytest.mark.asyncio
async def test_data_quality_validator_null_rate(pg_container):
    validator = DataQualityValidator()
    good_record = {
        "actual_unit_cost_eur": 200.0,
        "actual_date": date.today(),
        "estimate_id": "abc",
        "source": "MANUAL",
    }
    assert validator.validate([good_record]).passed

    bad_records = [{"source": "MANUAL"}] * 10  # wszystkie braki
    report = validator.validate(bad_records)
    assert not report.passed
    assert "null_rate" in report.failed_checks[0]


@pytest.mark.asyncio
async def test_outbox_relay(pg_container, mocker):
    """Weryfikacja że outbox events są publikowane do Kafka."""
    published = []
    mock_kafka = mocker.AsyncMock()
    mock_kafka.send_and_wait = mocker.AsyncMock(
        side_effect=lambda topic, key, value: published.append(topic)
    )

    from cls.events import CLSOutboxPublisher
    publisher = CLSOutboxPublisher(pg_container, mock_kafka, batch_size=10)

    # Wstaw test event do outbox
    async with pg_container.acquire() as conn:
        await conn.execute(
            "INSERT INTO cls.outbox (topic, key, payload) VALUES ($1, $2, $3::jsonb)",
            "cls.test.topic", "key1", '{"test": true}',
        )

    n = await publisher.relay_pending_events()
    assert n == 1
    assert "cls.test.topic" in published
```

### 14.4 Testy ML Pipeline

```python
# tests/ml/test_retraining.py
import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from cls.orchestrator import RetrainingOrchestrator, RetrainingTrigger, InsufficientDataError


@pytest.fixture
def mock_train_df():
    np.random.seed(42)
    n = 600
    return pd.DataFrame({
        "estimate_id": [f"est-{i}" for i in range(n)],
        "volume_cm3": np.random.lognormal(5, 1, n),
        "price_eur_per_kg": np.random.uniform(2, 50, n),
        "actual_unit_cost_eur": np.random.lognormal(5, 0.5, n),
        "production_location": np.random.choice(["DE", "PL", "CN"], n),
        "complexity_class": np.random.choice(["SIMPLE", "STANDARD", "COMPLEX"], n),
    })


@pytest.mark.asyncio
async def test_insufficient_data_raises(mock_orchestrator):
    mock_orchestrator._count_new_actuals = AsyncMock(return_value=100)  # < 500
    with pytest.raises(InsufficientDataError):
        await mock_orchestrator.trigger_retraining(
            "cee-unit-cost-xgb", RetrainingTrigger.SCHEDULED
        )


@pytest.mark.asyncio
async def test_promotion_threshold_not_met(mock_orchestrator, mock_train_df):
    """Challenger gorszy niż champion → nie promuj."""
    mock_orchestrator._count_new_actuals = AsyncMock(return_value=600)

    # Challenger MAPE 12% (gorszy od champion 10%)
    mock_orchestrator.evaluator.evaluate = AsyncMock(
        return_value=MagicMock(mape=12.0, bias_pct=1.0, p_value=0.03)
    )
    mock_orchestrator.evaluator.evaluate_champion = AsyncMock(
        return_value=MagicMock(mape=10.0, bias_pct=0.5, p_value=0.03)
    )

    with patch.object(mock_orchestrator, "_train_challenger", AsyncMock(return_value=("v2", "run123"))):
        with patch.object(mock_orchestrator, "_persist_job", AsyncMock()):
            with patch.object(mock_orchestrator, "_update_job", AsyncMock()):
                with patch.object(mock_orchestrator, "_emit_retraining_failed", AsyncMock()):
                    with patch.object(mock_orchestrator.registry, "archive_version", AsyncMock()):
                        job = await mock_orchestrator.trigger_retraining(
                            "cee-unit-cost-xgb", RetrainingTrigger.SCHEDULED
                        )
                        await asyncio.sleep(0.1)  # let task complete
                        assert not job.promoted
```

### 14.5 Load Test (k6)

```javascript
// tests/load/cls_ingestion_load.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("error_rate");
const ingestionLatency = new Trend("ingestion_latency_ms");

export const options = {
  stages: [
    { duration: "2m", target: 20 },   // warm-up
    { duration: "5m", target: 50 },   // steady state: 50 concurrent users
    { duration: "2m", target: 100 },  // peak
    { duration: "1m", target: 0 },    // cool-down
  ],
  thresholds: {
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    error_rate: ["rate<0.01"],
  },
};

const BASE_URL = __ENV.CLS_URL || "http://localhost:8080";

function randomActualCost() {
  return {
    estimate_id: `est-${Math.random().toString(36).slice(2)}`,
    source: "MANUAL",
    actual_date: "2026-01-15",
    actual_unit_cost_eur: 100 + Math.random() * 900,
    actual_production_location: ["DE", "PL", "CZ"][Math.floor(Math.random() * 3)],
  };
}

export default function () {
  const payload = JSON.stringify(randomActualCost());
  const params = {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${__ENV.CLS_TOKEN}`,
    },
  };

  const res = http.post(`${BASE_URL}/cls/v1/actuals`, payload, params);
  const success = check(res, {
    "status 201 or 409": (r) => r.status === 201 || r.status === 409,
    "latency < 500ms": (r) => r.timings.duration < 500,
  });

  errorRate.add(!success);
  ingestionLatency.add(res.timings.duration);
  sleep(0.1);
}

export function handleSummary(data) {
  return {
    "tests/load/cls_results.json": JSON.stringify(data, null, 2),
  };
}
```

---

## 15. Scalability

### 15.1 Poziomy skalowania

| Poziom | Wolumen | Konfiguracja |
|--------|---------|-------------|
| L1 — Dev | < 100 actual/dzień | 1 API pod, 1 worker, 1 Airflow |
| L2 — Small | 100–1K actual/dzień | 2 API pods, 2 workers, scheduler Airflow |
| L3 — Medium | 1K–10K actual/dzień | HPA API 2–10, 3 workers, Kafka partition=3 |
| L4 — Enterprise | > 10K actual/dzień | HPA API 2–20, 5 workers, Kafka partition=12, Redis Cluster |

### 15.2 Kubernetes HPA

```yaml
# k8s/cls/hpa-api.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cls-api-hpa
  namespace: industrial-cost
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cls-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 65
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 75
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "50"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 3
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300

---
# k8s/cls/hpa-retraining-worker.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cls-retraining-worker-hpa
  namespace: industrial-cost
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cls-retraining-worker
  minReplicas: 1
  maxReplicas: 5
  metrics:
    - type: External
      external:
        metric:
          name: kafka_consumer_lag
          selector:
            matchLabels:
              topic: cls.drift.detected
        target:
          type: AverageValue
          averageValue: "5"
```

### 15.3 Współbieżność retrainingu

```python
import asyncio
from collections import defaultdict


class RetrainingWorkerPool:
    """
    Zarządza współbieżnym retrainingiem maksymalnie 2 modeli na raz.
    Modele z zależnościami (material → unit) są serializowane.
    """

    # Zależności: model → musi być wytrenowany po tych modelach
    MODEL_DEPENDENCIES = {
        "cee-unit-cost-xgb": ["cee-material-cost-lgbm", "cee-process-cost-lgbm"],
        "cee-confidence": ["cee-unit-cost-xgb"],
    }

    def __init__(self, orchestrator: "RetrainingOrchestrator", max_concurrent: int = 2):
        self.orchestrator = orchestrator
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._model_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._completed: set[str] = set()

    async def retrain_all(
        self,
        model_names: list[str],
        trigger: "RetrainingTrigger",
    ) -> dict[str, bool]:
        """Retrenuje modele z uwzględnieniem kolejności zależności."""
        results = {}

        async def _retrain_with_deps(model_name: str) -> bool:
            # Czekaj na dependencje
            deps = self.MODEL_DEPENDENCIES.get(model_name, [])
            while not all(d in self._completed for d in deps):
                await asyncio.sleep(5)

            async with self._semaphore:
                try:
                    job = await self.orchestrator.trigger_retraining(model_name, trigger)
                    # Poczekaj na zakończenie joba
                    while job.status in ("PENDING", "RUNNING"):
                        await asyncio.sleep(10)
                        # Job status jest aktualizowany przez _run_retraining_pipeline
                    self._completed.add(model_name)
                    return job.promoted
                except Exception:
                    self._completed.add(model_name)  # żeby nie blokować dependentów
                    return False

        tasks = [_retrain_with_deps(m) for m in model_names]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        return {m: r for m, r in zip(model_names, results_list) if not isinstance(r, Exception)}
```

### 15.4 Partycjonowanie danych

```sql
-- Partycjonowanie cls.prediction_errors po miesiącu
-- (dla tabel > 10M wierszy)
CREATE TABLE cls.prediction_errors_2026_01
    PARTITION OF cls.prediction_errors
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE cls.prediction_errors_2026_02
    PARTITION OF cls.prediction_errors
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- Automatyczne partycje przez pg_partman (extension)
SELECT partman.create_parent(
    p_parent_table => 'cls.prediction_errors',
    p_control => 'computed_at',
    p_type => 'native',
    p_interval => 'monthly',
    p_premake => 3
);
```

### 15.5 Feature Store scaling

```python
# Redis Cluster dla Online Store przy > L3
REDIS_CLUSTER_CONFIG = {
    "startup_nodes": [
        {"host": "redis-node-1", "port": 6379},
        {"host": "redis-node-2", "port": 6379},
        {"host": "redis-node-3", "port": 6379},
    ],
    "decode_responses": False,
    "max_connections": 50,
}

# Offline Store: read replicas dla treningu (nie obciążaj primary)
OFFLINE_STORE_REPLICA_DSN = "postgresql://replica:5432/industrial_cost"
```

---

## 16. Risks

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|-------------------|-------|-----------|
| R01 | Brak actual costs — mało zamówień wraca z ERP | Wysokie | Krytyczny | Integracja z wieloma źródłami (RFQA, CHE, MANUAL); alert gdy < 200 sampli |
| R02 | Data leakage — feature znane tylko po fakcie użyte w treningu | Średnie | Krytyczny | Point-in-time correct joins z feature_timestamp; test suite weryfikuje |
| R03 | Challenger gorszy we wszystkich segmentach ale lepszy aggregate | Średnie | Wysoki | Segmentowe metryki w promotion decision; require within_10pct ≥ aggregate |
| R04 | Model drift bez nowych danych — distribution drift przy stałej MAPE | Niskie | Wysoki | PSI monitoring cech niezależnie od MAPE; alert po PSI >0.20 |
| R05 | Retraining loop — każdy nowy model generuje gorsze predykcje → rollback → retrain | Niskie | Krytyczny | Max 3 kolejne failed promotions → PAUSED + alert; wymaga interwencji człowieka |
| R06 | Zbyt mała próbka holdout — wyniki niestatystycznie istotne | Średnie | Wysoki | Min. 60-day holdout; min 100 sampli; Wilcoxon test dla małych n |
| R07 | SAP API niedostępne — brak ingestionu | Średnie | Wysoki | Retry exponential backoff; fallback do CHE i RFQA; alert po 24h braku danych |
| R08 | Feature drift powoduje retraining, ale model nie poprawi się (root cause zmiana biznesowa) | Niskie | Wysoki | Drift signal → HITL gdy 3 rollbacki z rzędu; business review cycle |
| R09 | Zbyt częsty retraining — overfitting na nowych danych (catastrophic forgetting) | Niskie | Wysoki | Min cooldown 6h; warm-start dla scheduled; cold-start tylko critical |
| R10 | Niezgodność schema cech między offline a online store | Średnie | Wysoki | Feature versioning; automatyczne testy zgodności schema przed promotion |
| R11 | MLflow storage przepełnienie — artefakty modeli zajmują za dużo miejsca | Niskie | Średni | Archiwizacja po 6 miesiącach; S3 lifecycle policy; compressed artifacts |
| R12 | Opóźnienie actual costs > 90 dni — drift wykryty za późno | Wysokie | Wysoki | CHE + RFQA jako szybkie źródła (< 7 dni lag); SAP tylko walidacja |
| R13 | Bias ukryty przez agregację — zła metryka per segment | Średnie | Wysoki | Mandatowe testy per lokalizacja; alarm gdy ≥1 segment MAPE > 2× target |
| R14 | Cold-start przy pierwszym wdrożeniu — brak historycznych actuals | Pewne | Wysoki | Synthetic data generation; expert annotation pierwszych 500 wycen; transfer learning |
| R15 | Concurrent retraining race condition — dwa modele modyfikują wspólny feature store | Niskie | Wysoki | Semaphore max_concurrent=2; model-level locks; feature_version atomic increment |

---

## 17. Roadmap

### Fazy i sprinty

| Faza | Sprinty | Cel | Kluczowe deliverables |
|------|---------|-----|-----------------------|
| **Foundation** | S1–S8 | Podstawy infrastruktury | DB schema, actual ingestion SAP+RFQA, feedback store, rolling metrics, outbox relay |
| **Drift Detection** | S9–S16 | Automatyczna detekcja | PSI, CUSUM, KS-test, StatisticalDriftDetector, alert Alertmanager, Grafana dashboards |
| **Retraining Pipeline** | S17–S24 | Autonomiczny retraining | RetrainingOrchestrator, Airflow DAG, champion-challenger promotion, rollback, MLflow registry |
| **Intelligence** | S25–S32 | Optymalizacja ML | Feature Store v2, A/B testing, segment-aware promotion, HITL dla rollback, 5-model ensemble |

### Szczegółowy plan

```
S1  DB schema cls.*, migracje, cee.actual_costs integration
S2  ERPSAPIngester (SAP CO REST, batch 500, delta load)
S3  RFQActualIngester + CHE consumer, DataQualityValidator
S4  PredictionFeedbackStore — compute_and_store(), SQL join cee.*
S5  RollingMetricsCalculator (7/30/90d, MAPE/RMSE/bias)
S6  CLS API v1 — /actuals, /metrics/accuracy, /metrics/errors
S7  Outbox + Kafka topics + Avro schemas
S8  Airflow cls_data_ingestion DAG, Grafana CLS Overview

S9  PSICalculator — 10-bin, PSI formula, severity thresholds
S10 CUSUMDetector — two-sided, k/h params, alarm types, DB state
S11 StatisticalDriftDetector — integrate PSI + CUSUM + KS-test
S12 ContinuousLearningOrchestrator — monitoring loop, trigger table
S13 DriftSignal persistence + /drift/signals API
S14 Alertmanager rules — MAPE, bias, PSI, CUSUM, data quality
S15 Grafana dashboards — Drift Monitor, Model Accuracy, Ingestion
S16 Airflow cls_drift_detection DAG (hourly)

S17 OfflineFeatureStore — point-in-time correct queries, backfill
S18 OnlineFeatureStore — Redis Hash, TTL per group, batch get
S19 FeatureMaterializer Airflow DAG (hourly sync)
S20 CLSModelRegistry — MLflow wrapper, stage management, lineage
S21 ModelEvaluator — MAPE/RMSE/bias/R²/CI coverage, segmented, t-test
S22 RetrainingOrchestrator — warm/cold start, trigger, promotion logic
S23 Airflow cls_model_retraining DAG (weekly + event-triggered)
S24 Rollback mechanism + /models/{name}/rollback API, alert CLSRollbackOccurred

S25 A/B testing — ABModelRouter (10% challenger traffic), outcome recording
S26 Segment-aware promotion — per-location MAPE thresholds
S27 RetrainingWorkerPool — concurrent semaphore, dependency ordering
S28 HITL trigger dla repeated rollback (3×) — integracja z RFQA HITLGateway
S29 Feature versioning v2 — schema diff detection, migration guard
S30 Calibration check + Brier score, reliability diagram export
S31 Performance optimization — pg_partman, Redis Cluster, read replicas
S32 Full E2E audit — security review, load test 100K actuals, DR drill
```

### Docelowe KPIs po pełnym wdrożeniu (S32)

| Metryka | Baseline (bez CLS) | Cel (po S32) |
|---------|-------------------|--------------|
| Unit cost MAPE | ~18% | < 10% |
| Material cost MAPE | ~14% | < 8% |
| Prediction bias | ±6% | |bias| < 3% |
| Within ±10% rate | ~50% | > 70% |
| Time-to-detect drift | manualne (tygodnie) | < 24h |
| Time-to-retrain (CRITICAL) | manualne (dni) | < 2h |
| Model freshness | co kwartał | co tydzień |
| Actual cost coverage | < 40% | > 85% |
| Auto-promotion rate | 0% | > 60% |
| Rollback rate | — | < 10% |
