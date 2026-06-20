# CLS — Sekcje 9–12: SQL Schema, API, Event System, Monitoring

---

## 9. SQL Schema

### 9.1 Schemat `cls` — ENUMy

```sql
-- PostgreSQL 16
CREATE SCHEMA IF NOT EXISTS cls;

CREATE TYPE cls.loop_state AS ENUM (
    'MONITORING', 'DRIFT_DETECTED', 'RETRAINING',
    'EVALUATING', 'PROMOTING', 'ROLLBACK', 'PAUSED'
);

CREATE TYPE cls.drift_type AS ENUM (
    'FEATURE_DRIFT', 'TARGET_DRIFT', 'PREDICTION_DRIFT',
    'CONCEPT_DRIFT', 'DATA_QUALITY'
);

CREATE TYPE cls.drift_severity AS ENUM ('OK', 'WARNING', 'CRITICAL');

CREATE TYPE cls.actual_source AS ENUM (
    'ERP_SAP', 'INVOICE', 'RFQA', 'CHE', 'MANUAL'
);

CREATE TYPE cls.retrain_trigger AS ENUM (
    'SCHEDULED', 'DRIFT_CRITICAL', 'DRIFT_WARNING_ACCUMULATION',
    'MANUAL', 'ACCURACY_DEGRADATION'
);

CREATE TYPE cls.job_status AS ENUM (
    'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'ROLLED_BACK'
);

CREATE TYPE cls.model_stage AS ENUM (
    'None', 'Staging', 'Production', 'Archived'
);
```

### 9.2 Tabele

```sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Rzeczywiste koszty (ground truth)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.actual_costs (
    actual_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id),
    source              cls.actual_source NOT NULL,
    source_reference    VARCHAR(255),       -- np. SAP order ID, invoice number
    actual_date         DATE NOT NULL,
    actual_unit_cost_eur        NUMERIC(14,4) NOT NULL,
    actual_material_cost_eur    NUMERIC(14,4),
    actual_process_cost_eur     NUMERIC(14,4),
    actual_overhead_cost_eur    NUMERIC(14,4),
    actual_quantity             INTEGER,
    actual_production_location  VARCHAR(5),
    -- SAP breakdown
    sap_cost_element            JSONB,
    -- Data quality
    quality_passed      BOOLEAN NOT NULL DEFAULT FALSE,
    quality_flags       JSONB,              -- np. {"null_fields": ["material_cost"]}
    -- Audyt
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_by         VARCHAR(100),
    CONSTRAINT actual_costs_unit_cost_positive CHECK (actual_unit_cost_eur > 0)
);
CREATE INDEX idx_actual_costs_estimate_id ON cls.actual_costs(estimate_id);
CREATE INDEX idx_actual_costs_actual_date ON cls.actual_costs(actual_date DESC);
CREATE INDEX idx_actual_costs_source ON cls.actual_costs(source);
CREATE INDEX idx_actual_costs_quality ON cls.actual_costs(quality_passed, actual_date DESC);
CREATE UNIQUE INDEX idx_actual_costs_source_ref
    ON cls.actual_costs(source, source_reference)
    WHERE source_reference IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- Błędy predykcji (computed po otrzymaniu actual)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.prediction_errors (
    error_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actual_id           UUID NOT NULL REFERENCES cls.actual_costs(actual_id),
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id),
    model_name          VARCHAR(100) NOT NULL,
    model_version       VARCHAR(50),
    predicted_value     NUMERIC(14,4) NOT NULL,
    actual_value        NUMERIC(14,4) NOT NULL,
    absolute_error      NUMERIC(14,4) GENERATED ALWAYS AS (ABS(predicted_value - actual_value)) STORED,
    relative_error      NUMERIC(10,6) GENERATED ALWAYS AS (
        CASE WHEN actual_value != 0
             THEN (predicted_value - actual_value) / actual_value
             ELSE NULL END
    ) STORED,
    -- Kontekst do segmentacji
    production_location VARCHAR(5),
    complexity_class    VARCHAR(20),
    volume_tier         VARCHAR(20),
    material_group      VARCHAR(50),
    -- CI coverage
    predicted_lower     NUMERIC(14,4),
    predicted_upper     NUMERIC(14,4),
    within_ci           BOOLEAN GENERATED ALWAYS AS (
        actual_value BETWEEN predicted_lower AND predicted_upper
    ) STORED,
    lag_days            INTEGER,           -- dni od estimate do actual
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pred_errors_model ON cls.prediction_errors(model_name, computed_at DESC);
CREATE INDEX idx_pred_errors_relative ON cls.prediction_errors(model_name, relative_error);
CREATE INDEX idx_pred_errors_location ON cls.prediction_errors(production_location, model_name);


-- ─────────────────────────────────────────────────────────────────────────────
-- Sygnały drift
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.drift_signals (
    signal_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name          VARCHAR(100) NOT NULL,
    drift_type          cls.drift_type NOT NULL,
    severity            cls.drift_severity NOT NULL,
    metric_name         VARCHAR(100) NOT NULL,
    metric_value        NUMERIC(12,6) NOT NULL,
    threshold           NUMERIC(12,6) NOT NULL,
    features_affected   JSONB,              -- lista cech dla FEATURE_DRIFT
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    recommendation      TEXT,
    acknowledged        BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_by     VARCHAR(100),
    acknowledged_at     TIMESTAMPTZ,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_drift_signals_model ON cls.drift_signals(model_name, detected_at DESC);
CREATE INDEX idx_drift_signals_severity ON cls.drift_signals(severity, acknowledged, detected_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Cykle uczenia / retraining jobs
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.retraining_jobs (
    job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name          VARCHAR(100) NOT NULL,
    trigger             cls.retrain_trigger NOT NULL,
    drift_signal_id     UUID REFERENCES cls.drift_signals(signal_id),
    loop_state          cls.loop_state NOT NULL DEFAULT 'MONITORING',
    status              cls.job_status NOT NULL DEFAULT 'PENDING',
    warm_start          BOOLEAN NOT NULL DEFAULT TRUE,
    min_train_date      DATE NOT NULL,
    max_train_date      DATE NOT NULL,
    holdout_start       DATE NOT NULL,
    train_samples       INTEGER,
    holdout_samples     INTEGER,
    feature_version     INTEGER,
    -- Challenger metrics
    challenger_version  VARCHAR(50),
    challenger_mape     NUMERIC(8,4),
    challenger_rmse     NUMERIC(12,4),
    challenger_bias_pct NUMERIC(8,4),
    challenger_p_value  NUMERIC(8,6),
    -- Champion metrics (at time of comparison)
    champion_version    VARCHAR(50),
    champion_mape       NUMERIC(8,4),
    -- Wynik
    promoted            BOOLEAN NOT NULL DEFAULT FALSE,
    rolled_back         BOOLEAN NOT NULL DEFAULT FALSE,
    failure_reason      TEXT,
    -- Czasy
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    duration_minutes    NUMERIC(8,2) GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (completed_at - started_at)) / 60
    ) STORED
);
CREATE INDEX idx_retrain_jobs_model ON cls.retraining_jobs(model_name, created_at DESC);
CREATE INDEX idx_retrain_jobs_status ON cls.retraining_jobs(status, created_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Rejestr promocji modeli
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.model_promotions (
    id                  BIGSERIAL PRIMARY KEY,
    model_name          VARCHAR(100) NOT NULL,
    new_version         VARCHAR(50) NOT NULL,
    previous_version    VARCHAR(50),
    job_id              UUID REFERENCES cls.retraining_jobs(job_id),
    mape_before         NUMERIC(8,4),
    mape_after          NUMERIC(8,4),
    promoted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_by         VARCHAR(100) NOT NULL DEFAULT 'cls-auto'
);
CREATE INDEX idx_model_promotions_model ON cls.model_promotions(model_name, promoted_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Rolling metryki (pre-computed, hourly)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.rolling_metrics (
    metric_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name          VARCHAR(100) NOT NULL,
    window_days         INTEGER NOT NULL,   -- 7, 30, 90
    segment_type        VARCHAR(50),        -- NULL = aggregate; 'location', 'complexity', 'volume_tier'
    segment_value       VARCHAR(50),        -- NULL = aggregate; 'DE', 'COMPLEX', 'HIGH'
    n_samples           INTEGER NOT NULL,
    mape                NUMERIC(8,4),
    rmse                NUMERIC(12,4),
    mae                 NUMERIC(12,4),
    bias_pct            NUMERIC(8,4),
    within_5pct         NUMERIC(6,4),
    within_10pct        NUMERIC(6,4),
    within_20pct        NUMERIC(6,4),
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_rolling_metrics_model ON cls.rolling_metrics(model_name, window_days, computed_at DESC);
CREATE INDEX idx_rolling_metrics_segment ON cls.rolling_metrics(model_name, segment_type, segment_value, computed_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Feature views (offline store) — jedna tabela per model
-- Przykład dla unit cost XGB
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.fv_unit_cost_features (
    fv_id               BIGSERIAL PRIMARY KEY,
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id),
    feature_version     INTEGER NOT NULL DEFAULT 1,
    feature_timestamp   TIMESTAMPTZ NOT NULL,
    -- Geometry features
    volume_cm3          NUMERIC(14,4),
    surface_area_cm2    NUMERIC(14,4),
    bounding_box_vol    NUMERIC(14,4),
    wall_thickness_min  NUMERIC(8,4),
    wall_thickness_avg  NUMERIC(8,4),
    aspect_ratio        NUMERIC(8,4),
    -- Material features
    material_group      VARCHAR(50),
    price_eur_per_kg    NUMERIC(14,4),
    density_g_cm3       NUMERIC(8,4),
    material_weight_kg  NUMERIC(10,4),
    buy_to_fly_ratio    NUMERIC(8,4),
    -- Process features
    process_class       VARCHAR(50),
    total_cycle_time_sec NUMERIC(10,2),
    setup_time_sec      NUMERIC(10,2),
    -- Volume & location
    annual_volume       INTEGER,
    volume_tier         VARCHAR(20),
    production_location VARCHAR(5),
    location_cost_index NUMERIC(6,4),
    -- Derived
    complexity_class    VARCHAR(20),
    feature_count       INTEGER,
    UNIQUE (estimate_id, feature_version)
);
CREATE INDEX idx_fv_unit_estimate ON cls.fv_unit_cost_features(estimate_id, feature_timestamp DESC);

-- Analogicznie: cls.fv_material_cost_features, cls.fv_process_cost_features,
--               cls.fv_overhead_cost_features, cls.fv_confidence_features


-- ─────────────────────────────────────────────────────────────────────────────
-- A/B test results
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.ab_test_results (
    ab_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name          VARCHAR(100) NOT NULL,
    version             VARCHAR(50) NOT NULL,
    stage               VARCHAR(20) NOT NULL,   -- 'Production' | 'Staging'
    prediction          NUMERIC(14,4) NOT NULL,
    actual              NUMERIC(14,4),           -- NULL do czasu otrzymania actual
    relative_error      NUMERIC(10,6),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ab_test_model ON cls.ab_test_results(model_name, version, recorded_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- CUSUM state (per model, persisted between cycles)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.cusum_state (
    model_name          VARCHAR(100) PRIMARY KEY,
    cusum_pos           NUMERIC(10,6) NOT NULL DEFAULT 0,
    cusum_neg           NUMERIC(10,6) NOT NULL DEFAULT 0,
    n_samples           INTEGER NOT NULL DEFAULT 0,
    last_alarm          cls.drift_severity,
    alarm_type          VARCHAR(30),     -- 'UNDER_PREDICTION' | 'OVER_PREDICTION'
    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Outbox (Transactional Outbox Pattern → Kafka relay)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE cls.outbox (
    outbox_id       BIGSERIAL PRIMARY KEY,
    topic           VARCHAR(200) NOT NULL,
    key             VARCHAR(500),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    published       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_cls_outbox_unpublished ON cls.outbox(published, created_at)
    WHERE published = FALSE;


-- ─────────────────────────────────────────────────────────────────────────────
-- Stored functions
-- ─────────────────────────────────────────────────────────────────────────────

-- Trigger: auto-update updated_at
CREATE OR REPLACE FUNCTION cls.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

-- Compute prediction errors batch
CREATE OR REPLACE FUNCTION cls.compute_prediction_errors(p_model_name TEXT)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    v_inserted INTEGER;
BEGIN
    INSERT INTO cls.prediction_errors (
        actual_id, estimate_id, model_name, model_version,
        predicted_value, actual_value,
        production_location, complexity_class, volume_tier,
        predicted_lower, predicted_upper, lag_days
    )
    SELECT
        ac.actual_id,
        ac.estimate_id,
        p_model_name,
        mp.model_version,
        mp.predicted_value,
        ac.actual_unit_cost_eur,
        ac.actual_production_location,
        ei.complexity_class,
        CASE
            WHEN ei.annual_volume < 100    THEN 'LOW'
            WHEN ei.annual_volume < 1000   THEN 'MEDIUM'
            WHEN ei.annual_volume < 10000  THEN 'HIGH'
            ELSE 'MASS'
        END,
        mp.predicted_lower,
        mp.predicted_upper,
        (ac.actual_date - ce.created_at::DATE)
    FROM cls.actual_costs ac
    JOIN cee.cost_estimates ce ON ce.estimate_id = ac.estimate_id
    JOIN cee.estimation_inputs ei ON ei.estimate_id = ac.estimate_id
    JOIN cee.ml_predictions mp
        ON mp.estimate_id = ac.estimate_id
        AND mp.model_name = p_model_name
    WHERE ac.quality_passed = TRUE
      AND NOT EXISTS (
          SELECT 1 FROM cls.prediction_errors pe
          WHERE pe.actual_id = ac.actual_id
            AND pe.model_name = p_model_name
      );
    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RETURN v_inserted;
END;
$$;

-- Relay outbox → Kafka (called by background worker)
CREATE OR REPLACE FUNCTION cls.claim_outbox_batch(p_limit INTEGER DEFAULT 100)
RETURNS SETOF cls.outbox
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    UPDATE cls.outbox SET published = TRUE, published_at = NOW()
    WHERE outbox_id IN (
        SELECT outbox_id FROM cls.outbox
        WHERE published = FALSE
        ORDER BY created_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *;
END;
$$;

-- Widoki
CREATE OR REPLACE VIEW cls.v_model_accuracy AS
SELECT
    rm.model_name,
    rm.window_days,
    rm.n_samples,
    rm.mape,
    rm.rmse,
    rm.bias_pct,
    rm.within_10pct,
    rm.computed_at,
    mp.new_version AS current_production_version,
    mp.promoted_at AS last_promoted_at
FROM cls.rolling_metrics rm
LEFT JOIN LATERAL (
    SELECT new_version, promoted_at
    FROM cls.model_promotions
    WHERE model_name = rm.model_name
    ORDER BY promoted_at DESC LIMIT 1
) mp ON TRUE
WHERE rm.segment_type IS NULL
  AND rm.computed_at = (
      SELECT MAX(computed_at) FROM cls.rolling_metrics r2
      WHERE r2.model_name = rm.model_name AND r2.window_days = rm.window_days
        AND r2.segment_type IS NULL
  );

CREATE OR REPLACE VIEW cls.v_active_drift_signals AS
SELECT
    ds.signal_id,
    ds.model_name,
    ds.drift_type,
    ds.severity,
    ds.metric_name,
    ds.metric_value,
    ds.threshold,
    ds.features_affected,
    ds.recommendation,
    ds.detected_at,
    rj.job_id AS triggered_retrain_job,
    rj.status AS retrain_status
FROM cls.drift_signals ds
LEFT JOIN cls.retraining_jobs rj ON rj.drift_signal_id = ds.signal_id
WHERE ds.acknowledged = FALSE
ORDER BY ds.severity DESC, ds.detected_at DESC;

CREATE OR REPLACE VIEW cls.v_retraining_history AS
SELECT
    rj.job_id,
    rj.model_name,
    rj.trigger,
    rj.status,
    rj.warm_start,
    rj.train_samples,
    rj.challenger_mape,
    rj.champion_mape,
    ROUND(rj.champion_mape - rj.challenger_mape, 3) AS mape_improvement,
    rj.promoted,
    rj.rolled_back,
    rj.duration_minutes,
    rj.created_at,
    rj.completed_at,
    ds.drift_type,
    ds.severity AS drift_severity
FROM cls.retraining_jobs rj
LEFT JOIN cls.drift_signals ds ON ds.signal_id = rj.drift_signal_id
ORDER BY rj.created_at DESC;
```

---

## 10. API

### 10.1 OpenAPI 3.1 — CLS REST API

```yaml
openapi: "3.1.0"
info:
  title: Continuous Learning System API
  version: "1.0.0"
  description: |
    REST API dla CLS — zarządzanie cyklem uczenia modeli CEE.
servers:
  - url: https://api.industrial-cost.internal/cls/v1

security:
  - BearerAuth: []

paths:
  # ── Actual Costs ──────────────────────────────────────────────────────────
  /actuals:
    post:
      operationId: submitActualCost
      summary: Zgłoś rzeczywisty koszt produkcji
      tags: [actuals]
      x-required-role: CLS_CONTRIBUTOR
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ActualCostSubmit'
      responses:
        "201":
          description: Actual cost zarejestrowany
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ActualCost'
        "409":
          description: Duplikat (source + source_reference już istnieje)

    get:
      operationId: listActualCosts
      summary: Lista rzeczywistych kosztów
      tags: [actuals]
      x-required-role: CLS_VIEWER
      parameters:
        - name: model_name
          in: query
          schema: { type: string }
        - name: from_date
          in: query
          schema: { type: string, format: date }
        - name: to_date
          in: query
          schema: { type: string, format: date }
        - name: source
          in: query
          schema: { type: string, enum: [ERP_SAP, INVOICE, RFQA, CHE, MANUAL] }
        - name: quality_passed
          in: query
          schema: { type: boolean }
        - name: limit
          in: query
          schema: { type: integer, default: 100, maximum: 1000 }
        - name: offset
          in: query
          schema: { type: integer, default: 0 }
      responses:
        "200":
          description: Lista actual costs
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: { type: array, items: { $ref: '#/components/schemas/ActualCost' } }
                  total: { type: integer }

  /actuals/batch:
    post:
      operationId: submitActualCostBatch
      summary: Batch import rzeczywistych kosztów (max 500)
      tags: [actuals]
      x-required-role: CLS_CONTRIBUTOR
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                items:
                  type: array
                  items: { $ref: '#/components/schemas/ActualCostSubmit' }
                  maxItems: 500
      responses:
        "200":
          description: Wyniki importu
          content:
            application/json:
              schema:
                type: object
                properties:
                  inserted: { type: integer }
                  skipped_duplicates: { type: integer }
                  failed_validation: { type: integer }
                  errors: { type: array, items: { type: object } }

  # ── Drift Signals ─────────────────────────────────────────────────────────
  /drift/signals:
    get:
      operationId: listDriftSignals
      summary: Lista sygnałów drift
      tags: [drift]
      x-required-role: CLS_VIEWER
      parameters:
        - name: model_name
          in: query
          schema: { type: string }
        - name: severity
          in: query
          schema: { type: string, enum: [OK, WARNING, CRITICAL] }
        - name: acknowledged
          in: query
          schema: { type: boolean, default: false }
        - name: limit
          in: query
          schema: { type: integer, default: 50 }
      responses:
        "200":
          description: Sygnały drift
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: { type: array, items: { $ref: '#/components/schemas/DriftSignal' } }
                  total: { type: integer }

  /drift/signals/{signal_id}/acknowledge:
    post:
      operationId: acknowledgeDriftSignal
      summary: Potwierdzenie odczytu sygnału
      tags: [drift]
      x-required-role: CLS_ANALYST
      parameters:
        - name: signal_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                comment: { type: string }
      responses:
        "200":
          description: Sygnał potwierdzony

  /drift/run:
    post:
      operationId: runDriftDetection
      summary: Uruchom detekcję drift ręcznie
      tags: [drift]
      x-required-role: CLS_OPS
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                model_names:
                  type: array
                  items: { type: string }
      responses:
        "200":
          description: Wyniki detekcji
          content:
            application/json:
              schema:
                type: object
                properties:
                  signals_detected: { type: integer }
                  retraining_triggered: { type: boolean }

  # ── Retraining Jobs ───────────────────────────────────────────────────────
  /retrain/jobs:
    get:
      operationId: listRetrainingJobs
      summary: Historia retraining jobs
      tags: [retraining]
      x-required-role: CLS_VIEWER
      parameters:
        - name: model_name
          in: query
          schema: { type: string }
        - name: status
          in: query
          schema: { type: string }
        - name: limit
          in: query
          schema: { type: integer, default: 20 }
      responses:
        "200":
          description: Lista jobów
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: { type: array, items: { $ref: '#/components/schemas/RetrainingJob' } }

    post:
      operationId: triggerRetraining
      summary: Manualnie uruchom retraining
      tags: [retraining]
      x-required-role: CLS_OPS
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [model_name]
              properties:
                model_name:
                  type: string
                  enum: [cee-unit-cost-xgb, cee-material-cost-lgbm,
                         cee-process-cost-lgbm, cee-overhead-cost-xgb, cee-confidence]
                warm_start:
                  type: boolean
                  default: true
      responses:
        "202":
          description: Job zainicjowany
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RetrainingJob'
        "409":
          description: Job już w toku dla tego modelu

  /retrain/jobs/{job_id}:
    get:
      operationId: getRetrainingJob
      summary: Szczegóły retraining job
      tags: [retraining]
      x-required-role: CLS_VIEWER
      parameters:
        - name: job_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          description: Szczegóły joba
          content:
            application/json:
              schema: { $ref: '#/components/schemas/RetrainingJob' }

  # ── Models ────────────────────────────────────────────────────────────────
  /models:
    get:
      operationId: listModels
      summary: Lista modeli i ich aktualnych wersji
      tags: [models]
      x-required-role: CLS_VIEWER
      responses:
        "200":
          description: Lista modeli
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/ModelInfo' }

  /models/{model_name}/rollback:
    post:
      operationId: rollbackModel
      summary: Rollback modelu do poprzedniej wersji
      tags: [models]
      x-required-role: CLS_OPS
      parameters:
        - name: model_name
          in: path
          required: true
          schema: { type: string }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [reason]
              properties:
                reason: { type: string }
      responses:
        "200":
          description: Rollback wykonany
        "404":
          description: Brak poprzedniej wersji

  /models/{model_name}/promote/{version}:
    post:
      operationId: manualPromote
      summary: Ręczna promocja wersji do Production
      tags: [models]
      x-required-role: CLS_ADMIN
      parameters:
        - name: model_name
          in: path
          required: true
          schema: { type: string }
        - name: version
          in: path
          required: true
          schema: { type: string }
      responses:
        "200":
          description: Model promowany

  # ── Metrics ───────────────────────────────────────────────────────────────
  /metrics/accuracy:
    get:
      operationId: getAccuracyMetrics
      summary: Rolling metryki jakości predykcji
      tags: [metrics]
      x-required-role: CLS_VIEWER
      parameters:
        - name: model_name
          in: query
          schema: { type: string }
        - name: window_days
          in: query
          schema: { type: integer, enum: [7, 30, 90], default: 30 }
        - name: segment_type
          in: query
          schema: { type: string, enum: [location, complexity, volume_tier] }
      responses:
        "200":
          description: Metryki
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/RollingMetrics' }

  /metrics/errors:
    get:
      operationId: listPredictionErrors
      summary: Lista błędów predykcji
      tags: [metrics]
      x-required-role: CLS_ANALYST
      parameters:
        - name: model_name
          in: query
          required: true
          schema: { type: string }
        - name: from_date
          in: query
          schema: { type: string, format: date }
        - name: to_date
          in: query
          schema: { type: string, format: date }
        - name: limit
          in: query
          schema: { type: integer, default: 200, maximum: 5000 }
      responses:
        "200":
          description: Błędy predykcji
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: { type: array, items: { $ref: '#/components/schemas/PredictionError' } }
                  summary: { $ref: '#/components/schemas/ErrorSummary' }

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    ActualCostSubmit:
      type: object
      required: [estimate_id, source, actual_date, actual_unit_cost_eur]
      properties:
        estimate_id: { type: string, format: uuid }
        source: { type: string, enum: [ERP_SAP, INVOICE, RFQA, CHE, MANUAL] }
        source_reference: { type: string }
        actual_date: { type: string, format: date }
        actual_unit_cost_eur: { type: number, minimum: 0.01 }
        actual_material_cost_eur: { type: number }
        actual_process_cost_eur: { type: number }
        actual_overhead_cost_eur: { type: number }
        actual_quantity: { type: integer }
        actual_production_location: { type: string, maxLength: 5 }
        sap_cost_element: { type: object }

    ActualCost:
      allOf:
        - $ref: '#/components/schemas/ActualCostSubmit'
        - type: object
          properties:
            actual_id: { type: string, format: uuid }
            quality_passed: { type: boolean }
            quality_flags: { type: object }
            recorded_at: { type: string, format: date-time }

    DriftSignal:
      type: object
      properties:
        signal_id: { type: string, format: uuid }
        model_name: { type: string }
        drift_type: { type: string }
        severity: { type: string }
        metric_name: { type: string }
        metric_value: { type: number }
        threshold: { type: number }
        features_affected: { type: array, items: { type: string } }
        recommendation: { type: string }
        detected_at: { type: string, format: date-time }
        acknowledged: { type: boolean }

    RetrainingJob:
      type: object
      properties:
        job_id: { type: string, format: uuid }
        model_name: { type: string }
        trigger: { type: string }
        status: { type: string }
        warm_start: { type: boolean }
        train_samples: { type: integer }
        challenger_mape: { type: number }
        champion_mape: { type: number }
        promoted: { type: boolean }
        rolled_back: { type: boolean }
        failure_reason: { type: string }
        duration_minutes: { type: number }
        created_at: { type: string, format: date-time }
        completed_at: { type: string, format: date-time }

    ModelInfo:
      type: object
      properties:
        model_name: { type: string }
        production_version: { type: string }
        staging_version: { type: string }
        current_mape_30d: { type: number }
        last_promoted_at: { type: string, format: date-time }
        active_drift_signals: { type: integer }

    RollingMetrics:
      type: object
      properties:
        model_name: { type: string }
        window_days: { type: integer }
        segment_type: { type: string }
        segment_value: { type: string }
        n_samples: { type: integer }
        mape: { type: number }
        rmse: { type: number }
        bias_pct: { type: number }
        within_10pct: { type: number }
        computed_at: { type: string, format: date-time }

    PredictionError:
      type: object
      properties:
        error_id: { type: string, format: uuid }
        estimate_id: { type: string, format: uuid }
        model_name: { type: string }
        predicted_value: { type: number }
        actual_value: { type: number }
        absolute_error: { type: number }
        relative_error: { type: number }
        within_ci: { type: boolean }
        production_location: { type: string }
        complexity_class: { type: string }
        volume_tier: { type: string }
        computed_at: { type: string, format: date-time }

    ErrorSummary:
      type: object
      properties:
        total: { type: integer }
        mape: { type: number }
        bias_pct: { type: number }
        within_10pct: { type: number }
        worst_location: { type: string }
        worst_location_mape: { type: number }
```

### 10.2 RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CLS_VIEWER` | GET metrics, drift signals, jobs, models |
| `CLS_CONTRIBUTOR` | CLS_VIEWER + POST actuals (single + batch) |
| `CLS_ANALYST` | CLS_CONTRIBUTOR + GET prediction errors + A/B results |
| `CLS_OPS` | CLS_ANALYST + trigger retraining + rollback + run drift |
| `CLS_ADMIN` | CLS_OPS + manual promote + DELETE + feature schema mgmt |

---

## 11. Event System

### 11.1 Tematy Kafka

| Temat | Producent | Konsumenci | Retention |
|-------|-----------|------------|-----------|
| `cls.actual.received` | CLS API, SAP Ingester, RFQA Ingester | CLS Feedback Store, Monitoring | 30 dni |
| `cls.prediction.error.computed` | CLS Feedback Store | CLS Drift Detector, Monitoring, Dashboard | 30 dni |
| `cls.drift.detected` | CLS Drift Detector | CLS Orchestrator, Alertmanager, Slack | 7 dni |
| `cls.retraining.started` | CLS Orchestrator | Monitoring, Dashboard | 7 dni |
| `cls.retraining.completed` | CLS Orchestrator | Monitoring, Dashboard | 7 dni |
| `cls.model.promoted` | CLS Registry | CEE API (model reload), Monitoring, Slack | 90 dni |
| `cls.model.rolled_back` | CLS Registry | CEE API (model reload), Monitoring, Slack | 90 dni |
| `cls.metrics.updated` | CLS Metrics Worker | Grafana, Dashboard | 7 dni |
| `cls.outbox.relay` | CLS Outbox Worker | — (relay only) | 1 dzień |
| `cee.actual.recorded` | CEE API (external) | CLS Actual Ingester | 30 dni |

### 11.2 Avro Schemas

```json
// cls.actual.received
{
  "namespace": "com.industrial_cost.cls",
  "type": "record",
  "name": "ActualCostReceived",
  "fields": [
    {"name": "actual_id",             "type": "string"},
    {"name": "estimate_id",           "type": "string"},
    {"name": "source",                "type": "string"},
    {"name": "actual_date",           "type": {"type": "int", "logicalType": "date"}},
    {"name": "actual_unit_cost_eur",  "type": "double"},
    {"name": "actual_material_cost_eur", "type": ["null", "double"], "default": null},
    {"name": "actual_process_cost_eur",  "type": ["null", "double"], "default": null},
    {"name": "quality_passed",        "type": "boolean"},
    {"name": "recorded_at",           "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// cls.drift.detected
{
  "namespace": "com.industrial_cost.cls",
  "type": "record",
  "name": "DriftDetected",
  "fields": [
    {"name": "signal_id",         "type": "string"},
    {"name": "model_name",        "type": "string"},
    {"name": "drift_type",        "type": "string"},
    {"name": "severity",          "type": "string"},
    {"name": "metric_name",       "type": "string"},
    {"name": "metric_value",      "type": "double"},
    {"name": "threshold",         "type": "double"},
    {"name": "features_affected", "type": {"type": "array", "items": "string"}, "default": []},
    {"name": "recommendation",    "type": ["null", "string"], "default": null},
    {"name": "detected_at",       "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// cls.model.promoted
{
  "namespace": "com.industrial_cost.cls",
  "type": "record",
  "name": "ModelPromoted",
  "fields": [
    {"name": "model_name",        "type": "string"},
    {"name": "new_version",       "type": "string"},
    {"name": "previous_version",  "type": ["null", "string"], "default": null},
    {"name": "job_id",            "type": "string"},
    {"name": "trigger",           "type": "string"},
    {"name": "challenger_mape",   "type": "double"},
    {"name": "champion_mape",     "type": "double"},
    {"name": "mape_delta",        "type": "double"},
    {"name": "promoted_at",       "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// cls.retraining.completed
{
  "namespace": "com.industrial_cost.cls",
  "type": "record",
  "name": "RetrainingCompleted",
  "fields": [
    {"name": "job_id",              "type": "string"},
    {"name": "model_name",          "type": "string"},
    {"name": "trigger",             "type": "string"},
    {"name": "status",              "type": "string"},
    {"name": "promoted",            "type": "boolean"},
    {"name": "challenger_mape",     "type": ["null", "double"], "default": null},
    {"name": "champion_mape",       "type": ["null", "double"], "default": null},
    {"name": "duration_minutes",    "type": ["null", "double"], "default": null},
    {"name": "failure_reason",      "type": ["null", "string"], "default": null},
    {"name": "completed_at",        "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

### 11.3 Konsumenci zewnętrzni

| Konsument | Temat | Akcja |
|-----------|-------|-------|
| CEE API | `cls.model.promoted` | Hot-reload modelu bez restartu |
| CEE API | `cls.model.rolled_back` | Hot-reload poprzedniej wersji |
| Dashboard (WebSocket) | `cls.drift.detected`, `cls.model.promoted` | Push powiadomień do UI |
| PagerDuty / Opsgenie | `cls.drift.detected` (severity=CRITICAL) | Alert on-call |
| Data Warehouse | `cls.actual.received`, `cls.prediction.error.computed` | Ingestion do DWH |

### 11.4 CLSOutboxPublisher

```python
import asyncio
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)
UTC = timezone.utc


class CLSOutboxPublisher:
    """
    Relay pattern: PostgreSQL outbox → Kafka.
    Gwarantuje exactly-once delivery przez transactional outbox.
    """

    def __init__(self, db_pool, kafka_producer: AIOKafkaProducer, batch_size: int = 100):
        self.db = db_pool
        self.kafka = kafka_producer
        self.batch_size = batch_size
        self._running = False

    async def relay_pending_events(self) -> int:
        """Pobiera i publikuje unpublished events z outbox. Zwraca liczbę opublikowanych."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM cls.claim_outbox_batch($1)",
                self.batch_size,
            )

        if not rows:
            return 0

        published = 0
        for row in rows:
            try:
                await self.kafka.send_and_wait(
                    topic=row["topic"],
                    key=(row["key"] or "").encode(),
                    value=json.dumps(row["payload"]).encode(),
                )
                published += 1
            except Exception as exc:
                logger.error("Failed to publish outbox event %s: %s", row["outbox_id"], exc)
                # Rollback: un-publish (set published=FALSE)
                async with self.db.acquire() as conn:
                    await conn.execute(
                        "UPDATE cls.outbox SET published=FALSE, published_at=NULL WHERE outbox_id=$1",
                        row["outbox_id"],
                    )

        return published

    async def run_forever(self, interval_seconds: float = 5.0) -> None:
        self._running = True
        while self._running:
            try:
                n = await self.relay_pending_events()
                if n > 0:
                    logger.debug("Relayed %d outbox events", n)
            except Exception as exc:
                logger.exception("Outbox relay error: %s", exc)
            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        self._running = False
```

---

## 12. Monitoring

### 12.1 Metryki Prometheus

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# ── Actual Costs ──────────────────────────────────────────────────────────────
CLS_ACTUALS_INGESTED = Counter(
    "cls_actuals_ingested_total",
    "Łączna liczba actual costs zaingerstowanych",
    ["source", "quality_passed"],
)

CLS_ACTUALS_INGESTION_LATENCY = Histogram(
    "cls_actuals_ingestion_duration_seconds",
    "Czas ingestionu batch actual costs",
    ["source"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ── Prediction Errors ─────────────────────────────────────────────────────────
CLS_MAPE_GAUGE = Gauge(
    "cls_model_mape",
    "Aktualny rolling MAPE modelu [%]",
    ["model_name", "window_days"],
)

CLS_BIAS_GAUGE = Gauge(
    "cls_model_bias_pct",
    "Aktualny rolling bias [%] (+ = over-prediction)",
    ["model_name", "window_days"],
)

CLS_WITHIN_10PCT_GAUGE = Gauge(
    "cls_model_within_10pct",
    "Frakcja predykcji w zakresie ±10% actual",
    ["model_name", "window_days"],
)

CLS_RMSE_GAUGE = Gauge(
    "cls_model_rmse_eur",
    "Rolling RMSE [EUR]",
    ["model_name", "window_days"],
)

CLS_PREDICTION_ERRORS = Counter(
    "cls_prediction_errors_computed_total",
    "Błędy predykcji obliczone",
    ["model_name"],
)

# ── Drift Detection ───────────────────────────────────────────────────────────
CLS_DRIFT_SIGNALS = Counter(
    "cls_drift_signals_total",
    "Sygnały drift wykryte",
    ["model_name", "drift_type", "severity"],
)

CLS_PSI_GAUGE = Gauge(
    "cls_feature_psi",
    "Population Stability Index per cecha",
    ["model_name", "feature_name"],
)

CLS_CUSUM_GAUGE = Gauge(
    "cls_cusum_value",
    "Aktualny stan CUSUM",
    ["model_name", "direction"],  # direction: pos | neg
)

CLS_MAPE_DRIFT_DELTA = Gauge(
    "cls_mape_drift_delta_pct",
    "Różnica MAPE (current - reference) [pp]",
    ["model_name"],
)

# ── Retraining ────────────────────────────────────────────────────────────────
CLS_RETRAIN_JOBS = Counter(
    "cls_retraining_jobs_total",
    "Retraining jobs",
    ["model_name", "trigger", "status"],
)

CLS_RETRAIN_DURATION = Histogram(
    "cls_retraining_duration_minutes",
    "Czas retrainingu [minuty]",
    ["model_name", "trigger"],
    buckets=[5, 15, 30, 60, 120, 240],
)

CLS_PROMOTION_RATE = Counter(
    "cls_model_promotions_total",
    "Liczba promocji modeli",
    ["model_name", "trigger"],
)

CLS_MAPE_IMPROVEMENT = Histogram(
    "cls_mape_improvement_pp",
    "Poprawa MAPE po retrainingu [pp]",
    ["model_name"],
    buckets=[-5, -2, -1, 0, 0.5, 1, 2, 5, 10],
)

CLS_ROLLBACK_COUNTER = Counter(
    "cls_model_rollbacks_total",
    "Liczba rollbacków",
    ["model_name", "reason"],
)

# ── Feature Store ─────────────────────────────────────────────────────────────
CLS_FEATURE_STORE_LATENCY = Histogram(
    "cls_feature_store_latency_seconds",
    "Czas pobierania cech",
    ["store_type", "operation"],  # store_type: offline | online
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

CLS_ONLINE_STORE_CACHE_HIT = Counter(
    "cls_online_store_cache_hits_total",
    "Cache hit/miss w online feature store",
    ["feature_group", "result"],  # result: hit | miss
)

# ── Data Quality ──────────────────────────────────────────────────────────────
CLS_DATA_QUALITY_FAILURES = Counter(
    "cls_data_quality_failures_total",
    "Błędy jakości danych",
    ["source", "check_type"],
)

CLS_ACTUAL_LAG_DAYS = Histogram(
    "cls_actual_lag_days",
    "Opóźnienie actual cost vs estimate [dni]",
    ["source"],
    buckets=[1, 7, 14, 30, 60, 90, 180],
)

# ── Outbox ────────────────────────────────────────────────────────────────────
CLS_OUTBOX_LAG = Gauge(
    "cls_outbox_unpublished_count",
    "Liczba nieopublikowanych zdarzeń w outbox",
)

CLS_OUTBOX_RELAY_LATENCY = Histogram(
    "cls_outbox_relay_latency_seconds",
    "Czas relay outbox → Kafka",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# ── Business ──────────────────────────────────────────────────────────────────
CLS_TRAINING_SAMPLES_GAUGE = Gauge(
    "cls_training_samples_available",
    "Dostępne próbki treningowe",
    ["model_name"],
)

CLS_MODEL_VERSION_GAUGE = Gauge(
    "cls_model_version_info",
    "Informacja o aktualnej wersji modelu (label = version string)",
    ["model_name", "stage", "version"],
)
```

### 12.2 Instrumentacja FastAPI

```python
import time
from fastapi import FastAPI, Request, Response
from prometheus_client import make_asgi_app

app = FastAPI(title="CLS API")

# Metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration = time.perf_counter() - start
    # Shared API latency histogram (reused pattern z CEE/RFQA)
    CLS_API_LATENCY.labels(
        endpoint=request.url.path,
        method=request.method,
        status=response.status_code,
    ).observe(duration)
    return response
```

### 12.3 Dashboardy Grafana

| Dashboard | Panele |
|-----------|--------|
| **CLS Overview** | MAPE trend (7/30/90d), active drift signals, jobs status, promotions timeline |
| **Model Accuracy** | MAPE per model × window, bias gauge, within-10pct heatmap, worst segments |
| **Drift Monitor** | PSI heatmap per feature × model, CUSUM chart, drift signal log, KS-test p-values |
| **Retraining Pipeline** | Job Gantt (duration per model), trigger breakdown, promotion rate, MAPE improvement distribution |
| **Data Ingestion** | Actual costs per source/day, data quality failure rate, actual lag distribution, coverage per model |
| **Feature Store** | Online store latency, cache hit rate, offline materialization lag, feature version timeline |
| **Infrastructure** | Outbox lag, Kafka consumer lag, PostgreSQL connections, Redis memory, pod count (HPA) |
