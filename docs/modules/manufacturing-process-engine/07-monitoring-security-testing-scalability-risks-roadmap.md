# Manufacturing Process Engine — Monitoring, Security, Testing, Scalability, Risks, Roadmap

## 23. Monitoring

### Metryki Prometheus

```yaml
metrics:

  # Business metrics
  - name: mpe_processes_total
    type: gauge
    labels: [process_class, status]
    description: "Total processes by class and status"

  - name: mpe_machines_total
    type: gauge
    labels: [machine_class, status, plant_id]

  - name: mpe_oee_pct
    type: gauge
    labels: [machine_id, machine_code, machine_class]
    description: "Latest OEE percentage per machine"

  - name: mpe_oee_shifts_recorded_total
    type: counter
    labels: [machine_class, source]
    description: "OEE shift records processed"

  - name: mpe_downtime_duration_seconds_total
    type: counter
    labels: [machine_id, downtime_category, loss_type]
    description: "Cumulative downtime per machine and category"

  - name: mpe_capacity_utilization_pct
    type: gauge
    labels: [resource_code, shift_number]
    description: "Current capacity utilization per resource"

  - name: mpe_bottlenecks_active_total
    type: gauge
    labels: [plant_id, severity]
    description: "Active bottleneck alerts"

  - name: mpe_tools_near_end_of_life_total
    type: gauge
    description: "Tools with <10% life remaining"

  - name: mpe_cost_estimate_requests_total
    type: counter
    labels: [process_class, status]
    description: "Cost estimation calls"

  # API metrics
  - name: mpe_api_requests_total
    type: counter
    labels: [endpoint, method, status_code]

  - name: mpe_api_request_duration_seconds
    type: histogram
    labels: [endpoint, method]
    buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5]

  # ML metrics
  - name: mpe_ml_prediction_requests_total
    type: counter
    labels: [model_name]

  - name: mpe_ml_prediction_duration_seconds
    type: histogram
    labels: [model_name]

  - name: mpe_ml_model_mape_pct
    type: gauge
    labels: [model_name, model_version]
    description: "Current MAPE of ML models"

  - name: mpe_ml_model_drift_detected_total
    type: counter
    labels: [model_name]
    description: "Concept drift detections triggering retraining"

  # Kafka
  - name: mpe_kafka_events_published_total
    type: counter
    labels: [topic]

  - name: mpe_kafka_consumer_lag
    type: gauge
    labels: [topic, consumer_group]
```

### Alerty Alertmanager

```yaml
alerts:
  - name: MachineOEEBelow60
    expr: mpe_oee_pct < 60
    for: 3d
    severity: warning
    message: "Machine {{ $labels.machine_code }} OEE {{ $value }}% below 60% for 3 days"

  - name: BottleneckCritical
    expr: mpe_bottlenecks_active_total{severity="HIGH"} > 0
    severity: critical
    message: "Critical capacity bottleneck detected at plant {{ $labels.plant_id }}"

  - name: MachineDowntimeExtended
    expr: mpe_downtime_duration_seconds_total{downtime_category="BREAKDOWN"} > 14400
    severity: high
    message: "Machine breakdown > 4 hours"

  - name: MLModelDrift
    expr: mpe_ml_model_mape_pct > 25
    severity: warning
    message: "ML model {{ $labels.model_name }} MAPE {{ $value }}% exceeds threshold"

  - name: CapacityOverload
    expr: mpe_capacity_utilization_pct > 95
    for: 2d
    severity: warning
    message: "Resource {{ $labels.resource_code }} at {{ $value }}% capacity"

  - name: APIHighLatency
    expr: histogram_quantile(0.95, mpe_api_request_duration_seconds) > 2
    severity: warning

  - name: ToolsNearExpiry
    expr: mpe_tools_near_end_of_life_total > 10
    severity: warning
    message: "{{ $value }} tools near end of life — check maintenance schedule"

  - name: OEEDataMissing
    expr: |
      (time() - mpe_oee_last_recorded_timestamp) > 86400
    severity: warning
    message: "No OEE data recorded for machine {{ $labels.machine_code }} in 24h"
```

### Dashboardy Grafana

| Dashboard | Panele |
|-----------|--------|
| **MPE Overview** | Liczba procesów/maszyn, OEE fleet average, active bottlenecks |
| **OEE Live** | OEE per maszyna (gauge), trend 30d, losses Pareto (availability/perf/quality) |
| **Machine Health** | MTBF/MTTR, downtime breakdown, maintenance calendar |
| **Capacity Planning** | Heatmap obciążenia zasobów, bottleneck timeline |
| **Cost Analytics** | Rozkład kosztów per proces/maszyna, koszt złomu, energia |
| **ML Models** | MAPE per model, feature drift, retrain history |
| **Downtime Pareto** | Rozkład przyczyn przestojów wg OEE |

### Health Check Endpoint

```json
GET /health/detailed

{
  "status": "healthy",
  "timestamp": "2026-06-18T14:00:00Z",
  "version": "2.1.0",
  "components": {
    "database":       { "status": "healthy", "latency_ms": 4 },
    "vector_store":   { "status": "healthy", "index_size": 3200 },
    "kafka_producer": { "status": "healthy", "lag": 0 },
    "ml_models": {
      "cycle_time":   { "status": "healthy", "mape_pct": 12.3, "last_trained": "2026-06-15" },
      "oee_anomaly":  { "status": "healthy", "f1_score": 0.84, "last_trained": "2026-06-10" }
    },
    "cache":          { "status": "healthy", "hit_rate_pct": 83 }
  },
  "metrics": {
    "total_active_processes":  312,
    "total_active_machines":   48,
    "fleet_oee_30d_avg_pct":   71.4,
    "oee_coverage_pct":        94.3,
    "bottlenecks_active":      1,
    "embeddings_current_pct":  97.8
  }
}
```

---

## 24. Security

### Model uprawnień RBAC

```
Role: PROCESS_VIEWER
  - GET /processes/**
  - GET /machines/**
  - GET /search/**
  - GET /capacity/utilization
  - No cost model access (restricted)

Role: PROCESS_ENGINEER
  - All VIEWER permissions
  - POST/PUT/PATCH /processes/**
  - PUT /processes/{id}/parameters
  - POST /processes/{id}/compatibility
  - POST /ai/**

Role: PRODUCTION_SUPERVISOR
  - All VIEWER permissions
  - POST /machines/{id}/oee
  - POST /machines/{id}/downtime
  - GET /capacity/**

Role: MAINTENANCE_TECHNICIAN
  - GET /machines/**
  - POST /machines/{id}/downtime
  - PUT /machines/{id}/maintenance
  - GET /tools/**
  - Scope: assigned machines only (row-level)

Role: COST_ANALYST
  - All VIEWER permissions
  - GET + POST /processes/{id}/cost-estimate
  - GET all cost models
  - No write access to process definitions

Role: MPE_ADMIN
  - All permissions
  - POST /machines (register new)
  - DELETE /processes (deactivate)
  - Admin endpoints

Role: SYSTEM_INTEGRATOR (MES/ERP/Scheduler)
  - GET all read endpoints
  - POST /machines/{id}/oee (MES writes OEE)
  - POST /machines/{id}/downtime
  - POST /capacity/slots (scheduler updates)
  - Rate limit: 2000 req/min
```

### Row-Level Security (PostgreSQL)

```sql
-- Maintenance technician can only see their assigned machines
CREATE POLICY machine_access_policy ON machines
    USING (
        current_user_has_role('MPE_ADMIN')
        OR current_user_has_role('PROCESS_ENGINEER')
        OR EXISTS (
            SELECT 1 FROM machine_operator_assignments
            WHERE machine_id = machines.machine_id
              AND operator_id = current_user_operator_id()
        )
    );

-- OEE records: only same-plant users
CREATE POLICY oee_plant_access ON oee_records
    USING (
        current_user_has_role('MPE_ADMIN')
        OR machine_id IN (
            SELECT m.machine_id FROM machines m
            JOIN production_resources r ON r.resource_id = m.resource_id
            WHERE r.plant_id = current_user_plant_id()
        )
    );
```

### Bezpieczeństwo danych

| Obszar | Mechanizm |
|--------|-----------|
| Autentykacja | JWT RS256 + mTLS dla integracji MES |
| Autoryzacja | RBAC endpoint + RLS dla izolacji zakładów |
| Transport | TLS 1.3, HSTS |
| Machine hourly rates | Widoczne tylko dla COST_ANALYST+ |
| Operator wages | Tylko dla COST_ANALYST+ i HR role |
| IoT data ingestion | Dedykowany service account z rate limit |
| Input validation | Pydantic v2 / Bean Validation na każdym endpointcie |
| Audit trail | Każda zmiana kosztów → audit_log z user/IP/timestamp |
| SQL injection | Wyłącznie parameterized queries, ORM |
| Secret management | Vault / K8s Secrets — zero plaintext credentials |

---

## 25. Testing

### Strategia testów

```
Unit (75%)        → logika biznesowa (cost formulas, OEE calc, scoring)
Integration (20%) → baza danych, Kafka, Redis (Testcontainers)
E2E (5%)          → kompletne flow API (Postman + Newman)
```

### Testy jednostkowe — kluczowe przypadki

```python
class TestOEECalculation:
    def test_oee_perfect_shift(self):
        record = OEERecord(
            planned_production_time_min=480,
            unplanned_downtime_min=0,
            planned_downtime_min=0,
            ideal_cycle_time_sec=10,
            total_parts_produced=2880,  # 480min / 10sec
            good_parts=2880,
            scrap_parts=0,
        )
        oee = record.calculate_oee()
        assert oee.availability_pct == 100.0
        assert oee.performance_pct == 100.0
        assert oee.quality_pct == 100.0
        assert oee.oee_pct == 100.0

    def test_oee_with_all_losses(self):
        record = OEERecord(
            planned_production_time_min=480,
            unplanned_downtime_min=48,      # 10% availability loss
            planned_downtime_min=0,
            ideal_cycle_time_sec=10,
            total_parts_produced=2160,      # slower — 90% performance
            good_parts=1944,                # 10% quality loss
            scrap_parts=216,
        )
        oee = record.calculate_oee()
        assert oee.availability_pct == pytest.approx(90.0, abs=0.1)
        assert oee.performance_pct  == pytest.approx(90.0, abs=0.5)
        assert oee.quality_pct      == pytest.approx(90.0, abs=0.1)
        assert oee.oee_pct          == pytest.approx(72.9, abs=0.5)  # 0.9³ × 100


class TestSetupCostCalculation:
    def test_setup_cost_amortized_over_large_batch(self):
        setup = SetupCostModel(
            setup_time_min=30,
            machine_hourly_rate=60,
            operator_hourly_rate=25,
        )
        result_small = setup_cost_per_piece(setup, batch_size=10)
        result_large = setup_cost_per_piece(setup, batch_size=100)
        assert result_large['setup_cost_per_piece_eur'] == pytest.approx(
            result_small['setup_cost_per_piece_eur'] / 10, rel=0.05
        )

    def test_setup_cost_increases_with_cnc_programming(self):
        setup_no_prog = SetupCostModel(setup_time_min=30, cnc_programming_time_min=0)
        setup_with_prog = SetupCostModel(setup_time_min=30, cnc_programming_time_min=60)
        r1 = setup_cost_per_piece(setup_no_prog, batch_size=10)
        r2 = setup_cost_per_piece(setup_with_prog, batch_size=10)
        assert r2['total_setup_cost_eur'] > r1['total_setup_cost_eur']


class TestCycleTimePrediction:
    @pytest.mark.parametrize("process,features,expected_range", [
        ("CUT.TH.LC.FIB", {"cut_length_mm": 500, "thickness_mm": 3, "material_class": "METAL"},
         (10, 60)),
        ("MAC.MI.3AX", {"milling_volume_cm3": 50, "material_hardness_hb": 180},
         (120, 600)),
    ])
    def test_prediction_within_physical_bounds(self, process, features, expected_range):
        model = load_cycle_time_model(process)
        pred = model.predict(features)
        assert expected_range[0] <= pred.predicted_cycle_sec <= expected_range[1]


class TestBottleneckDetection:
    def test_detects_bottleneck_after_3_overloaded_days(self):
        slots = [
            CapacitySlot(utilization_pct=92, slot_date=date(2026,6,16)),
            CapacitySlot(utilization_pct=95, slot_date=date(2026,6,17)),
            CapacitySlot(utilization_pct=88, slot_date=date(2026,6,18)),
        ]
        detector = BottleneckDetector()
        alerts = detector.detect_for_slots(slots)
        assert len(alerts) == 1
        assert alerts[0].severity == 'MEDIUM'

    def test_no_bottleneck_with_single_overloaded_day(self):
        slots = [
            CapacitySlot(utilization_pct=95, slot_date=date(2026,6,16)),
            CapacitySlot(utilization_pct=70, slot_date=date(2026,6,17)),
            CapacitySlot(utilization_pct=65, slot_date=date(2026,6,18)),
        ]
        detector = BottleneckDetector()
        alerts = detector.detect_for_slots(slots)
        assert len(alerts) == 0
```

### Testy integracyjne

```python
@pytest.fixture(scope="session")
def pg_db():
    with PostgreSqlContainer("postgres:16-alpine") as pg:
        engine = create_engine(pg.get_connection_url())
        run_migrations(engine, "manufacturing_process")
        seed_reference_data(engine)
        yield engine

class TestMachineRepository:
    def test_register_laser_machine(self, pg_db):
        repo = MachineRepository(pg_db)
        machine = repo.create(MachineFaker.laser_12kw())
        assert machine.machine_id is not None
        assert machine.machine_class == 'LASER'

    def test_get_machines_by_process_type(self, pg_db):
        repo = MachineRepository(pg_db)
        machines = repo.get_by_process_type('CUT.TH.LC.FIB')
        assert all(m.machine_class == 'LASER' for m in machines)

    def test_capacity_slot_generated_after_machine_creation(self, pg_db):
        repo = MachineRepository(pg_db)
        machine = repo.create(MachineFaker.cnc_mill_3ax())
        slots = CapacitySlotRepository(pg_db).get_for_resource(
            machine.resource_id, date.today(), date.today() + timedelta(days=7)
        )
        assert len(slots) > 0


class TestOEEKafkaIntegration:
    def test_oee_event_published_after_record(self, kafka_container, pg_db):
        service = OEEService(pg_db, kafka_producer=KafkaProducer(kafka_container.bootstrap_servers))
        service.record_shift_oee(OEERecordFaker.standard_shift())

        consumer = KafkaConsumer('mpe.oee.recorded', bootstrap_servers=...)
        messages = list(consume_with_timeout(consumer, timeout=5))
        assert len(messages) == 1
        assert messages[0].value['oee_pct'] > 0
```

### Test matrix

| Rodzaj testu | Narzędzie | Coverage Target | CI trigger |
|---|---|---|---|
| Unit | pytest + pytest-cov | ≥ 85% | Each PR |
| Integration | Testcontainers (PG + Kafka) | Key flows | Each PR |
| API | Postman + Newman | 30 scenarios | Deploy to staging |
| Contract | Pact (MPE ↔ Cost Calc, MES, Scheduler) | Critical endpoints | Each PR |
| Load | k6 (P95 < 500ms) | Cost estimate, search | Pre-release |
| Mutation | mutmut | Quality gate | Weekly |
| Security | OWASP ZAP + Bandit/Semgrep | OWASP Top 10 | Pre-release |
| ML regression | pytest-ml | MAPE stability | After model retrain |

---

## 26. Scalability

### Strategia skalowania

```
┌────────────────────────────────────────────────────────────┐
│  API Gateway / Load Balancer (Nginx / Kong)                │
└────────────────────┬───────────────────────────────────────┘
                     │ (Horizontal scaling — stateless pods)
        ┌────────────┴────────────────┐
        │                             │
  ┌─────▼──────┐              ┌──────▼──────┐
  │ MPE API    │  × N pods    │ MPE Worker  │  × M pods
  │ (FastAPI / │  (read-heavy)│ (Kafka      │  (event processing,
  │  Spring)   │              │  consumers) │   ML inference)
  └─────┬──────┘              └──────┬──────┘
        │                            │
        ▼                            ▼
  ┌──────────────────────────────────────────┐
  │  PostgreSQL 16 Primary + N Read Replicas │
  │  + pgvector HNSW (process embeddings)    │
  └──────────────────────────────────────────┘
        │
  ┌─────▼──────────┐   ┌──────────────┐
  │  Redis Cluster │   │  ClickHouse  │
  │  (API cache,   │   │  (OEE time   │
  │   session)     │   │   series,    │
  └────────────────┘   │   analytics) │
                       └──────────────┘
```

### Cacheowanie Redis

| Dane | TTL | Klucz | Invalidacja |
|------|-----|-------|-------------|
| Process detail | 2h | `mpe:proc:{id}:full` | ProcessUpdated event |
| Process parameters | 4h | `mpe:proc:{id}:params` | ProcessUpdated event |
| Machine detail | 1h | `mpe:machine:{id}` | MachineUpdated event |
| OEE 30-day summary | 15 min | `mpe:oee:{id}:30d` | OEERecorded event |
| Capacity slots | 10 min | `mpe:cap:{resource_id}:{date}` | CapacitySlotUpdated |
| Cost estimate | 5 min | `mpe:cost:{hash(request)}` | CostModelUpdated |
| Process search | 5 min | `mpe:search:{hash(query)}` | Auto-expire |
| Taxonomy tree | 12h | `mpe:taxonomy` | TaxonomyUpdated |

### Analityka OEE — ClickHouse

Dla dużych wolumenów danych OEE (>100 maszyn × 3 zmiany × 365 dni = ~100k rekordów/rok × N lat), dedykowana baza analityczna:

```sql
-- ClickHouse table for OEE analytics
CREATE TABLE oee_records_analytics (
    shift_date          Date,
    shift_number        UInt8,
    machine_id          UUID,
    machine_code        String,
    machine_class       LowCardinality(String),
    plant_id            UUID,
    availability_pct    Float32,
    performance_pct     Float32,
    quality_pct         Float32,
    oee_pct             Float32,
    good_parts          UInt32,
    scrap_parts         UInt32,
    energy_kwh          Float32,
    source              LowCardinality(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(shift_date)
ORDER BY (plant_id, machine_id, shift_date, shift_number);

-- Materialized view: monthly OEE summary
CREATE MATERIALIZED VIEW mv_oee_monthly
ENGINE = AggregatingMergeTree()
ORDER BY (plant_id, machine_class, month)
AS SELECT
    toStartOfMonth(shift_date) AS month,
    plant_id,
    machine_class,
    avgState(oee_pct)          AS avg_oee_state,
    sumState(good_parts)       AS total_good_state,
    sumState(scrap_parts)      AS total_scrap_state
FROM oee_records_analytics
GROUP BY month, plant_id, machine_class;
```

### Skalowanie modeli ML

```
ML serving architecture:
├── Online inference: FastAPI + ONNX Runtime (< 50ms P99)
├── Batch inference: Celery workers (cycle time for batch estimation)
├── Feature store: Redis (real-time features) + PostgreSQL (historical)
└── Model registry: MLflow (versions, metrics, artifacts)

Auto-retraining trigger:
  - MAPE drift > 5% from baseline (monitored daily)
  - New training data > 1000 samples since last train
  - Weekly scheduled retrain (Sunday 02:00 UTC)
```

---

## 27. Risks

### Rejestr ryzyk

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|--------------------|-------|-----------|
| R01 | Nierealistyczne stawki maszynogodziny — brak aktualnych danych | Wysokie | Krytyczny | Walidacja vs. benchmarki branżowe; obowiązkowy przegląd roczny stawek |
| R02 | Brak danych OEE — ręczne wprowadzanie = niskie pokrycie | Wysokie | Wysoki | Integracja z MES jako priorytet; fallback benchmark OEE |
| R03 | Model ML niedostatecznie wytrenowany (mała próba) | Średnie | Średni | Fallback na parametryczne formuły; MAPE monitoring |
| R04 | Drift konceptualny ML po zmianach technologicznych | Wysokie (czas) | Wysoki | Monitoring MAPE; trigger retrain na drift > 5% |
| R05 | Niedokładne czasy cyklu — różnica pomiędzy normą a rzeczywistością | Wysokie | Wysoki | Feed rzeczywistych czasów z MES/OperationCompleted events |
| R06 | Duplikacja danych procesowych między MPE a ERP | Średnie | Wysoki | Jedna strona jako master — MPE master, ERP slave; sync job |
| R07 | Niedostosowanie taksonomii do ERP (mismatch operation codes) | Średnie | Wysoki | Tabela mapowania MPE ↔ ERP codes; walidacja przy imporcie |
| R08 | Przepełnienie tablicy capacity_slots (duże horyzonty) | Pewne (czas) | Niski | Partycjonowanie; usuwanie slotów > 1 rok wstecz |
| R09 | Brak danych o maszynie (stare obiekty) | Wysokie (legacy) | Średni | Import z inwentarza środków trwałych; wartości domyślne per klasa |
| R10 | Stawki energii zmienne (kryzys energetyczny) | Pewne | Wysoki | Kwartalna aktualizacja taryf; alert przy zmianie ceny > 15% |
| R11 | Tool life data niedostępna — brak pomiaru zużycia | Wysokie | Średni | Default life z danych producenta; monitoring rzeczywistych zmian |
| R12 | OEE anomaly model — fałszywe alarmy (false positives) | Średnie | Niski | Threshold tuning; human-in-the-loop dla alertów critical |
| R13 | Brak integracji z MES przy starcie | Wysokie | Wysoki | Interfejs ręcznego importu CSV; design API-first |
| R14 | Koszty obliczeń AI (embedding dla 10k+ procesów) | Niskie | Niski | Batch embedding; incremental update only on change |

---

## 28. Roadmap

### Faza 1 — MVP (miesiące 1–3)

**Cel: uruchomienie katalogu procesów z podstawowymi kosztami**

| Sprint | Deliverable |
|--------|-------------|
| S1 | SQL Schema v1.0: process_categories, manufacturing_processes, process_parameters |
| S2 | Taksonomia procesów — 4 poziomy, 50+ typów procesów |
| S3 | REST API CRUD procesów + parametry |
| S4 | Import danych startowych: 16 typów procesów z sekcji CEL + parametry |
| S5 | Machine model + 20 maszyn referencyjnych |
| S6 | Runtime cost model + setup cost model |
| S7 | API `/cost-estimate` — kalkulacja kosztu per operacja |
| S8 | Process-material compatibility matrix (stale + tworzywa) |
| S9 | Full-text search + paginacja |
| S10 | Testy + stabilizacja |

**Wyjście MVP:** 50+ procesów, API kosztowe, search.

---

### Faza 2 — OEE & Resources (miesiące 4–6)

**Cel: zarządzanie maszynami, OEE, zdolności produkcyjne**

| Sprint | Deliverable |
|--------|-------------|
| S11 | OEE recording API + partitioned OEE table |
| S12 | Downtime recording + categorization |
| S13 | Capacity slots — generacja, API |
| S14 | Bottleneck detection algorithm |
| S15 | Energy cost model + EnergyTariff |
| S16 | Scrap cost model |
| S17 | Tool management (catalog + life tracking) |
| S18 | Operator model + certifications |
| S19 | Kafka event publishing (machine.*, oee.*, capacity.*) |
| S20 | Grafana dashboards (OEE, Capacity, Downtime Pareto) |

**Wyjście Fazy 2:** Pełny model zasobów, OEE, zdolności.

---

### Faza 3 — AI & ML (miesiące 7–9)

**Cel: wyszukiwanie semantyczne, predykcja czasu cyklu, rekomendacje**

| Sprint | Deliverable |
|--------|-------------|
| S21 | Process embeddings (pgvector HNSW) |
| S22 | Semantic search API |
| S23 | Cycle time prediction model v1 (XGBoost, MAPE < 20%) |
| S24 | Process recommendation engine |
| S25 | OEE anomaly detection (Isolation Forest) |
| S26 | Tool life prediction model |
| S27 | ML model serving (FastAPI + ONNX) |
| S28 | MLflow model registry + monitoring |
| S29 | Feature store (Redis + PG) |
| S30 | AI endpoints (`/ai/process-recommendation`, `/ai/cycle-time-prediction`) |

**Wyjście Fazy 3:** AI-powered rekomendacje i predykcje.

---

### Faza 4 — Integration & Scale (miesiące 10–12)

**Cel: integracja z MES/ERP, skalowalność, kompletność danych**

| Sprint | Deliverable |
|--------|-------------|
| S31 | MES integration (OEE auto-feed via Kafka consumer) |
| S32 | ERP sync (SAP PP Work Centers, Oracle MFG Resources) |
| S33 | ClickHouse analytics setup (OEE time series) |
| S34 | Redis caching layer (full implementation) |
| S35 | Read replicas PostgreSQL |
| S36 | Multi-plant support (plant isolation, RLS) |
| S37 | Scheduling inputs API (full SchedulingInput package) |
| S38 | Shift calendar management API |
| S39 | Performance testing (k6, P95 < 500ms @ 500 req/s) |
| S40 | Security hardening + pentest + OWASP scan |

**Wyjście Fazy 4:** System produkcyjny klasy enterprise.

---

### Długoterminowe (rok 2+)

| Inicjatywa | Opis |
|------------|------|
| Digital Twin integration | Cyfrowy bliźniak maszyny z real-time parametrami z IoT |
| Predictive Maintenance | ML model przewidujący awarie (MTBF prediction) na bazie wibracji, temperatury |
| Carbon Accounting | Obliczanie emisji CO₂ per operacja (Scope 3) |
| Process Simulation | Integracja z Arena/Plant Simulation dla co-if analysis |
| Augmented Setup | Wsparcie AR dla operatora podczas nastawu (Microsoft HoloLens API) |
| Autonomous Scheduling | AI scheduler optymalizujący kolejność operacji (CP-SAT / reinforcement learning) |
| Multi-site Benchmarking | Porównanie OEE/kosztów między zakładami grupy |
