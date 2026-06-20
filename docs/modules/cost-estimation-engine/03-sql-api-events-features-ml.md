# Cost Estimation Engine — SQL Schema, API, Events, Feature Engineering, ML Models

## 11. SQL Schema (PostgreSQL 16)

### 11.1 Schemat i typy wyliczeniowe

```sql
CREATE SCHEMA IF NOT EXISTS cee;

-- ENUMs
CREATE TYPE cee.estimation_status AS ENUM (
    'DRAFT', 'COMPUTING', 'COMPLETE', 'APPROVED', 'SUPERSEDED', 'ARCHIVED'
);

CREATE TYPE cee.confidence_level AS ENUM (
    'HIGH', 'MEDIUM', 'LOW', 'INDICATIVE'
);

CREATE TYPE cee.cost_component AS ENUM (
    'MATERIAL', 'PROCESS', 'TOOLING', 'SETUP', 'OVERHEAD',
    'SCRAP', 'LOGISTICS', 'ENERGY', 'QUALITY', 'MARGIN'
);

CREATE TYPE cee.process_class AS ENUM (
    'CUT', 'MAC', 'FOR', 'JOI', 'ASS', 'FIN', 'SUR', 'ADD'
);

CREATE TYPE cee.complexity_class AS ENUM (
    'SIMPLE', 'MODERATE', 'COMPLEX', 'VERY_COMPLEX'
);

CREATE TYPE cee.job_status AS ENUM (
    'PENDING', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED'
);
```

### 11.2 Tabele główne

```sql
-- Estimation inputs (immutable snapshot)
CREATE TABLE cee.estimation_inputs (
    input_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          UUID,
    product_name        VARCHAR(255) NOT NULL,
    -- Geometry
    volume_cm3          NUMERIC(12,4) NOT NULL,
    net_volume_cm3      NUMERIC(12,4) NOT NULL,
    surface_area_cm2    NUMERIC(12,4),
    length_mm           NUMERIC(10,3),
    width_mm            NUMERIC(10,3),
    height_mm           NUMERIC(10,3),
    complexity_class    cee.complexity_class NOT NULL DEFAULT 'MODERATE',
    feature_count       INTEGER DEFAULT 0,
    thin_wall_flag      BOOLEAN DEFAULT FALSE,
    deep_hole_flag      BOOLEAN DEFAULT FALSE,
    undercut_flag       BOOLEAN DEFAULT FALSE,
    buy_to_fly_ratio    NUMERIC(6,3) DEFAULT 1.0,
    -- Material
    material_id         UUID,
    material_code       VARCHAR(100) NOT NULL,
    material_group      VARCHAR(50),
    density_g_cm3       NUMERIC(8,4),
    material_price_eur_per_kg NUMERIC(10,4),
    price_snapshot_at   TIMESTAMPTZ,
    -- Process JSON array
    process_steps       JSONB NOT NULL DEFAULT '[]',
    -- Volume & location
    annual_volume       INTEGER NOT NULL,
    batch_size          INTEGER NOT NULL,
    production_location CHAR(2) NOT NULL,
    target_currency     CHAR(3) NOT NULL DEFAULT 'EUR',
    general_tolerance   VARCHAR(50) DEFAULT 'ISO2768-m',
    surface_finish_ra   NUMERIC(6,3),
    -- Meta
    requested_by        UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_hash          CHAR(64) GENERATED ALWAYS AS (
        encode(sha256(
            (volume_cm3::text || material_code || annual_volume::text ||
             production_location || process_steps::text)::bytea
        ), 'hex')
    ) STORED
);

-- Core estimate record
CREATE TABLE cee.cost_estimates (
    estimate_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_id            UUID NOT NULL REFERENCES cee.estimation_inputs(input_id),
    parent_estimate_id  UUID REFERENCES cee.cost_estimates(estimate_id),
    version             INTEGER NOT NULL DEFAULT 1,
    status              cee.estimation_status NOT NULL DEFAULT 'DRAFT',
    -- Cost components (EUR)
    unit_cost_eur       NUMERIC(14,4),
    total_cost_eur      NUMERIC(16,4),
    material_cost_eur   NUMERIC(14,4),
    process_cost_eur    NUMERIC(14,4),
    tooling_cost_eur    NUMERIC(14,4),
    setup_cost_eur      NUMERIC(14,4),
    overhead_cost_eur   NUMERIC(14,4),
    scrap_cost_eur      NUMERIC(14,4),
    logistics_cost_eur  NUMERIC(14,4),
    energy_cost_eur     NUMERIC(14,4),
    -- Uncertainty
    confidence_level    cee.confidence_level,
    confidence_score    NUMERIC(5,4),
    uncertainty_pct     NUMERIC(6,3),
    cost_low_eur        NUMERIC(14,4),
    cost_high_eur       NUMERIC(14,4),
    -- ML vs formula
    formula_cost_eur    NUMERIC(14,4),
    ml_cost_eur         NUMERIC(14,4),
    similarity_cost_eur NUMERIC(14,4),
    ensemble_weights    JSONB,                          -- {"formula":0.5,"ml":0.3,"similarity":0.2}
    -- ML metadata
    ml_model_version    VARCHAR(100),
    ml_run_id           VARCHAR(100),
    shap_values         JSONB,
    -- Similarity anchors
    similar_quote_ids   UUID[],
    similarity_confidence NUMERIC(5,4),
    -- Approval
    approved_by         UUID,
    approved_at         TIMESTAMPTZ,
    approval_note       TEXT,
    -- Audit
    calculation_duration_ms INTEGER,
    monte_carlo_samples INTEGER DEFAULT 10000,
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (input_id, version)
);

-- Cost line items (granular breakdown)
CREATE TABLE cee.cost_line_items (
    line_item_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id) ON DELETE CASCADE,
    component           cee.cost_component NOT NULL,
    description         VARCHAR(500) NOT NULL,
    quantity            NUMERIC(12,4) NOT NULL DEFAULT 1,
    unit                VARCHAR(50),
    unit_price_eur      NUMERIC(14,6),
    total_eur           NUMERIC(14,4) NOT NULL,
    pct_of_unit_cost    NUMERIC(6,3),
    driver_key          VARCHAR(100),
    driver_value        NUMERIC(14,6),
    driver_unit         VARCHAR(50),
    notes               TEXT,
    sort_order          INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-process-step cost details
CREATE TABLE cee.process_step_costs (
    step_cost_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id) ON DELETE CASCADE,
    step_sequence       INTEGER NOT NULL,
    process_type        VARCHAR(100) NOT NULL,
    process_class       cee.process_class NOT NULL,
    machine_type        VARCHAR(100),
    -- Time components (seconds)
    cycle_time_sec      NUMERIC(10,3),
    setup_time_min      NUMERIC(10,3),
    setup_amort_sec     NUMERIC(10,3),
    -- Cost per piece (EUR)
    machine_cost_eur    NUMERIC(12,4),
    labor_cost_eur      NUMERIC(12,4),
    overhead_cost_eur   NUMERIC(12,4),
    energy_cost_eur     NUMERIC(12,4),
    tooling_wear_eur    NUMERIC(12,4),
    total_step_cost_eur NUMERIC(12,4),
    -- Rates used
    machine_rate_eur_hr NUMERIC(10,4),
    labor_rate_eur_hr   NUMERIC(10,4),
    overhead_rate       NUMERIC(6,4),
    -- Quality
    scrap_rate          NUMERIC(8,6),
    rework_fraction     NUMERIC(6,4),
    cpk_assumed         NUMERIC(6,3),
    it_grade            INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (estimate_id, step_sequence)
);

-- Material price cache (refreshed from MIE)
CREATE TABLE cee.material_price_cache (
    cache_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    material_code       VARCHAR(100) NOT NULL,
    material_id         UUID,
    price_eur_per_kg    NUMERIC(12,4) NOT NULL,
    energy_surcharge_pct NUMERIC(6,4) DEFAULT 0,
    alloy_surcharge_eur_per_kg NUMERIC(8,4) DEFAULT 0,
    source              VARCHAR(50) NOT NULL DEFAULT 'MIE',
    valid_from          TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until         TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '4 hours',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (material_code, valid_from)
);

-- ML predictions log
CREATE TABLE cee.ml_predictions (
    prediction_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    estimate_id         UUID REFERENCES cee.cost_estimates(estimate_id),
    model_name          VARCHAR(100) NOT NULL,
    model_version       VARCHAR(100) NOT NULL,
    mlflow_run_id       VARCHAR(100),
    feature_vector      JSONB NOT NULL,
    predicted_cost_eur  NUMERIC(14,4) NOT NULL,
    prediction_interval_low  NUMERIC(14,4),
    prediction_interval_high NUMERIC(14,4),
    shap_values         JSONB,
    top_features        JSONB,
    prediction_latency_ms INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Benchmark store (aggregated actuals)
CREATE TABLE cee.cost_benchmarks (
    benchmark_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_type      VARCHAR(50) NOT NULL,               -- 'MATERIAL' | 'PROCESS' | 'PART_FAMILY'
    key                 VARCHAR(200) NOT NULL,
    location            CHAR(2),
    p10_eur             NUMERIC(14,4),
    p25_eur             NUMERIC(14,4),
    p50_eur             NUMERIC(14,4),
    p75_eur             NUMERIC(14,4),
    p90_eur             NUMERIC(14,4),
    sample_count        INTEGER,
    volume_range_low    INTEGER,
    volume_range_high   INTEGER,
    valid_from          DATE NOT NULL,
    valid_until         DATE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (benchmark_type, key, location, valid_from)
);

-- Accuracy tracking (actuals vs estimates)
CREATE TABLE cee.accuracy_log (
    log_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    estimate_id         UUID NOT NULL REFERENCES cee.cost_estimates(estimate_id),
    actual_cost_eur     NUMERIC(14,4) NOT NULL,
    actual_source       VARCHAR(100),
    actual_date         DATE,
    absolute_error_eur  NUMERIC(14,4) GENERATED ALWAYS AS (
        ABS(actual_cost_eur - (SELECT unit_cost_eur FROM cee.cost_estimates e WHERE e.estimate_id = log_id))
    ) STORED,
    mape                NUMERIC(8,4),
    within_confidence   BOOLEAN,
    notes               TEXT,
    recorded_by         UUID,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Async job queue
CREATE TABLE cee.estimation_jobs (
    job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_id            UUID NOT NULL REFERENCES cee.estimation_inputs(input_id),
    estimate_id         UUID REFERENCES cee.cost_estimates(estimate_id),
    priority            INTEGER NOT NULL DEFAULT 5,
    status              cee.job_status NOT NULL DEFAULT 'PENDING',
    worker_id           VARCHAR(100),
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    error_message       TEXT,
    scheduled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transactional outbox
CREATE TABLE cee.outbox (
    outbox_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type      VARCHAR(100) NOT NULL,
    aggregate_id        UUID NOT NULL,
    event_type          VARCHAR(100) NOT NULL,
    topic               VARCHAR(200) NOT NULL,
    payload             JSONB NOT NULL,
    headers             JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at        TIMESTAMPTZ,
    retry_count         INTEGER DEFAULT 0
);

CREATE INDEX idx_outbox_unpublished ON cee.outbox (created_at) WHERE published_at IS NULL;
```

### 11.3 Indeksy

```sql
-- cost_estimates
CREATE INDEX idx_ce_input_id        ON cee.cost_estimates (input_id);
CREATE INDEX idx_ce_status          ON cee.cost_estimates (status);
CREATE INDEX idx_ce_created_at      ON cee.cost_estimates (created_at DESC);
CREATE INDEX idx_ce_confidence      ON cee.cost_estimates (confidence_level);

-- estimation_inputs
CREATE INDEX idx_ei_product_id      ON cee.estimation_inputs (product_id);
CREATE INDEX idx_ei_material_code   ON cee.estimation_inputs (material_code);
CREATE INDEX idx_ei_location        ON cee.estimation_inputs (production_location);
CREATE INDEX idx_ei_input_hash      ON cee.estimation_inputs (input_hash);
CREATE INDEX idx_ei_created_at      ON cee.estimation_inputs (created_at DESC);

-- cost_line_items
CREATE INDEX idx_cli_estimate_id    ON cee.cost_line_items (estimate_id);
CREATE INDEX idx_cli_component      ON cee.cost_line_items (component);

-- process_step_costs
CREATE INDEX idx_psc_estimate_id    ON cee.process_step_costs (estimate_id);

-- material_price_cache
CREATE INDEX idx_mpc_material_code  ON cee.material_price_cache (material_code);
CREATE INDEX idx_mpc_valid_until    ON cee.material_price_cache (valid_until);

-- ml_predictions
CREATE INDEX idx_mlp_estimate_id    ON cee.ml_predictions (estimate_id);
CREATE INDEX idx_mlp_model_version  ON cee.ml_predictions (model_name, model_version);

-- estimation_jobs
CREATE INDEX idx_ej_status_priority ON cee.estimation_jobs (status, priority DESC, scheduled_at)
    WHERE status = 'PENDING';
```

### 11.4 Funkcje składowane i triggery

```sql
-- Auto-update updated_at
CREATE OR REPLACE FUNCTION cee.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_ce_updated_at
    BEFORE UPDATE ON cee.cost_estimates
    FOR EACH ROW EXECUTE FUNCTION cee.set_updated_at();

-- Claim next job (skip-locked queue)
CREATE OR REPLACE FUNCTION cee.claim_next_job(p_worker_id VARCHAR)
RETURNS cee.estimation_jobs LANGUAGE plpgsql AS $$
DECLARE v_job cee.estimation_jobs;
BEGIN
    SELECT * INTO v_job FROM cee.estimation_jobs
    WHERE status = 'PENDING' AND attempts < max_attempts AND scheduled_at <= now()
    ORDER BY priority DESC, scheduled_at
    LIMIT 1 FOR UPDATE SKIP LOCKED;

    IF FOUND THEN
        UPDATE cee.estimation_jobs
        SET status = 'RUNNING', worker_id = p_worker_id,
            started_at = now(), attempts = attempts + 1
        WHERE job_id = v_job.job_id;
    END IF;
    RETURN v_job;
END;
$$;

-- Get accuracy metrics
CREATE OR REPLACE FUNCTION cee.get_accuracy_metrics(
    p_location CHAR(2) DEFAULT NULL,
    p_days INTEGER DEFAULT 90
)
RETURNS TABLE (
    metric_name TEXT, value NUMERIC
) LANGUAGE sql AS $$
    SELECT 'mape' AS metric_name,
           AVG(ABS(al.actual_cost_eur - ce.unit_cost_eur) / NULLIF(al.actual_cost_eur, 0) * 100) AS value
    FROM cee.accuracy_log al
    JOIN cee.cost_estimates ce ON al.estimate_id = ce.estimate_id
    JOIN cee.estimation_inputs ei ON ce.input_id = ei.input_id
    WHERE al.recorded_at >= now() - (p_days || ' days')::INTERVAL
      AND (p_location IS NULL OR ei.production_location = p_location)
    UNION ALL
    SELECT 'within_confidence_pct',
           AVG(CASE WHEN al.within_confidence THEN 1.0 ELSE 0.0 END) * 100
    FROM cee.accuracy_log al
    JOIN cee.cost_estimates ce ON al.estimate_id = ce.estimate_id
    JOIN cee.estimation_inputs ei ON ce.input_id = ei.input_id
    WHERE al.recorded_at >= now() - (p_days || ' days')::INTERVAL
      AND (p_location IS NULL OR ei.production_location = p_location);
$$;

-- Outbox write helper
CREATE OR REPLACE FUNCTION cee.write_outbox(
    p_aggregate_type VARCHAR, p_aggregate_id UUID,
    p_event_type VARCHAR, p_topic VARCHAR, p_payload JSONB
)
RETURNS UUID LANGUAGE sql AS $$
    INSERT INTO cee.outbox (aggregate_type, aggregate_id, event_type, topic, payload)
    VALUES (p_aggregate_type, p_aggregate_id, p_event_type, p_topic, p_payload)
    RETURNING outbox_id;
$$;
```

### 11.5 Widoki

```sql
-- Estimate summary with input fields
CREATE VIEW cee.v_estimate_summary AS
SELECT
    ce.estimate_id, ce.version, ce.status, ce.confidence_level, ce.confidence_score,
    ce.unit_cost_eur, ce.total_cost_eur,
    ce.material_cost_eur, ce.process_cost_eur, ce.tooling_cost_eur,
    ce.overhead_cost_eur, ce.scrap_cost_eur, ce.logistics_cost_eur,
    ce.cost_low_eur, ce.cost_high_eur, ce.uncertainty_pct,
    ce.formula_cost_eur, ce.ml_cost_eur, ce.similarity_cost_eur,
    ei.product_name, ei.material_code, ei.annual_volume, ei.batch_size,
    ei.production_location, ei.complexity_class,
    ce.approved_by, ce.approved_at,
    ce.created_at, ce.updated_at
FROM cee.cost_estimates ce
JOIN cee.estimation_inputs ei ON ce.input_id = ei.input_id;

-- Model accuracy dashboard
CREATE VIEW cee.v_model_accuracy AS
SELECT
    DATE_TRUNC('week', al.recorded_at)   AS week,
    ei.production_location,
    ei.complexity_class,
    COUNT(*)                              AS n,
    AVG(ABS(al.actual_cost_eur - ce.unit_cost_eur) / NULLIF(al.actual_cost_eur,0) * 100) AS mape_pct,
    AVG(CASE WHEN al.within_confidence THEN 1.0 ELSE 0.0 END) * 100 AS within_ci_pct
FROM cee.accuracy_log al
JOIN cee.cost_estimates ce ON al.estimate_id = ce.estimate_id
JOIN cee.estimation_inputs ei ON ce.input_id = ei.input_id
GROUP BY 1, 2, 3;
```

---

## 12. API Design (OpenAPI 3.1)

### 12.1 Specyfikacja

```yaml
openapi: "3.1.0"
info:
  title: Cost Estimation Engine API
  version: "1.0.0"
  description: >
    Enterprise API dla Cost Estimation Engine (CEE). Oblicza pełny koszt
    produkcji komponentów przemysłowych z prognozami ML i analizą niepewności.

servers:
  - url: https://api.industrial-cost.internal/v1/cee
    description: Internal production

security:
  - BearerAuth: []

tags:
  - name: Estimate        # Tworzenie i zarządzanie wyceną
  - name: Breakdown       # Szczegółowy rozkład kosztów
  - name: Benchmark       # Dane porównawcze
  - name: Analytics       # Trafność, trendy
  - name: Admin           # Import aktualnych kosztów, modele ML

paths:
  /estimate:
    post:
      tags: [Estimate]
      summary: Uruchom wycenę kosztu produkcji
      operationId: createEstimate
      x-required-role: CEE_USER
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/EstimationRequest'
      responses:
        "202":
          description: Zadanie wyceny przyjęte asynchronicznie
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EstimationJobResponse'
        "200":
          description: Wynik synchroniczny (małe żądanie, <2s)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CostEstimateResponse'

  /estimate/{estimateId}:
    get:
      tags: [Estimate]
      summary: Pobierz wynik wyceny
      operationId: getEstimate
      x-required-role: CEE_VIEWER
      parameters:
        - name: estimateId
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "200":
          description: Wynik wyceny
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CostEstimateResponse'
        "202":
          description: Wycena nadal trwa
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EstimationJobResponse'

  /estimate/{estimateId}/breakdown:
    get:
      tags: [Breakdown]
      summary: Szczegółowy rozkład kosztów z pozycjami
      operationId: getEstimateBreakdown
      x-required-role: CEE_ANALYST
      parameters:
        - name: estimateId
          in: path
          required: true
          schema:
            type: string
            format: uuid
        - name: include_shap
          in: query
          schema:
            type: boolean
            default: false
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EstimateBreakdownResponse'

  /estimate/{estimateId}/sensitivity:
    get:
      tags: [Breakdown]
      summary: Analiza wrażliwości (+/-10/20/50% kluczowych parametrów)
      operationId: getSensitivityAnalysis
      x-required-role: CEE_ANALYST
      parameters:
        - name: estimateId
          in: path
          required: true
          schema:
            type: string
            format: uuid
        - name: parameters
          in: query
          schema:
            type: array
            items:
              type: string
              enum: [material_price, volume, cycle_time, overhead_rate, scrap_rate]
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SensitivityResponse'

  /estimate/{estimateId}/approve:
    post:
      tags: [Estimate]
      summary: Zatwierdź wycenę (przez Cost Engineer)
      operationId: approveEstimate
      x-required-role: CEE_COST_ENGINEER
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                note:
                  type: string
                  maxLength: 1000
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CostEstimateResponse'

  /estimate/batch:
    post:
      tags: [Estimate]
      summary: Batch wyceny (do 100 komponentów)
      operationId: batchEstimate
      x-required-role: CEE_USER
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [requests]
              properties:
                requests:
                  type: array
                  maxItems: 100
                  items:
                    $ref: '#/components/schemas/EstimationRequest'
                priority:
                  type: integer
                  minimum: 1
                  maximum: 10
                  default: 5
      responses:
        "202":
          content:
            application/json:
              schema:
                type: object
                properties:
                  batch_id:
                    type: string
                    format: uuid
                  job_ids:
                    type: array
                    items:
                      type: string
                      format: uuid
                  estimated_completion_sec:
                    type: integer

  /estimate/compare:
    post:
      tags: [Estimate]
      summary: Porównaj warianty (np. materiał A vs B, lokalizacja PL vs CN)
      operationId: compareEstimates
      x-required-role: CEE_ANALYST
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [base, variants]
              properties:
                base:
                  $ref: '#/components/schemas/EstimationRequest'
                variants:
                  type: array
                  maxItems: 5
                  items:
                    type: object
                    properties:
                      label:
                        type: string
                      overrides:
                        type: object
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ComparisonResponse'

  /benchmarks/material/{materialCode}:
    get:
      tags: [Benchmark]
      summary: Dane porównawcze kosztu materiału (percentyle P10-P90)
      operationId: getMaterialBenchmark
      x-required-role: CEE_VIEWER
      parameters:
        - name: materialCode
          in: path
          required: true
          schema:
            type: string
        - name: location
          in: query
          schema:
            type: string
        - name: volume_range
          in: query
          schema:
            type: string
            enum: [LOW, MEDIUM, HIGH]
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/BenchmarkResponse'

  /benchmarks/process/{processType}:
    get:
      tags: [Benchmark]
      summary: Dane porównawcze czasu cyklu i kosztu procesu
      operationId: getProcessBenchmark
      x-required-role: CEE_VIEWER
      parameters:
        - name: processType
          in: path
          required: true
          schema:
            type: string
        - name: machine_type
          in: query
          schema:
            type: string
        - name: location
          in: query
          schema:
            type: string
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/BenchmarkResponse'

  /analytics/accuracy:
    get:
      tags: [Analytics]
      summary: Metryki trafności modelu (MAPE, bias, within_confidence_pct)
      operationId: getAccuracyMetrics
      x-required-role: CEE_ANALYST
      parameters:
        - name: days
          in: query
          schema:
            type: integer
            default: 90
        - name: location
          in: query
          schema:
            type: string
        - name: complexity_class
          in: query
          schema:
            type: string
            enum: [SIMPLE, MODERATE, COMPLEX, VERY_COMPLEX]
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AccuracyMetricsResponse'

  /analytics/cost-drivers:
    get:
      tags: [Analytics]
      summary: Globalne SHAP feature importance z ostatnich N wyceń
      operationId: getCostDrivers
      x-required-role: CEE_ANALYST
      parameters:
        - name: n
          in: query
          schema:
            type: integer
            default: 1000
            maximum: 10000
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  top_features:
                    type: array
                    items:
                      type: object
                      properties:
                        feature_name:
                          type: string
                        mean_shap_abs:
                          type: number
                        pct_contribution:
                          type: number

  /actuals:
    post:
      tags: [Admin]
      summary: Zarejestruj rzeczywisty koszt do nauki modelu
      operationId: recordActual
      x-required-role: CEE_COST_ENGINEER
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [estimate_id, actual_cost_eur, actual_source]
              properties:
                estimate_id:
                  type: string
                  format: uuid
                actual_cost_eur:
                  type: number
                actual_source:
                  type: string
                  enum: [ERP_ACTUAL, INVOICE, SUPPLIER_QUOTE, MANUAL]
                actual_date:
                  type: string
                  format: date
                notes:
                  type: string

components:
  schemas:
    EstimationRequest:
      type: object
      required: [product_name, geometry, material, process_steps, annual_volume,
                 batch_size, production_location]
      properties:
        product_name:
          type: string
          maxLength: 255
        geometry:
          type: object
          required: [volume_cm3, net_volume_cm3]
          properties:
            volume_cm3:
              type: number
              minimum: 0.001
            net_volume_cm3:
              type: number
            surface_area_cm2:
              type: number
            length_mm:
              type: number
            width_mm:
              type: number
            height_mm:
              type: number
            complexity_class:
              type: string
              enum: [SIMPLE, MODERATE, COMPLEX, VERY_COMPLEX]
              default: MODERATE
            feature_count:
              type: integer
              default: 0
            thin_wall_flag:
              type: boolean
              default: false
            deep_hole_flag:
              type: boolean
              default: false
            undercut_flag:
              type: boolean
              default: false
        material:
          type: object
          required: [material_code]
          properties:
            material_code:
              type: string
            material_id:
              type: string
              format: uuid
            density_g_cm3:
              type: number
            price_eur_per_kg:
              type: number
        process_steps:
          type: array
          items:
            type: object
            required: [process_type, process_class]
            properties:
              process_type:
                type: string
              process_class:
                type: string
                enum: [CUT, MAC, FOR, JOI, ASS, FIN, SUR, ADD]
              machine_type:
                type: string
              cycle_time_sec:
                type: number
              setup_time_min:
                type: number
              it_grade:
                type: integer
                minimum: 4
                maximum: 16
              surface_ra_um:
                type: number
        annual_volume:
          type: integer
          minimum: 1
        batch_size:
          type: integer
          minimum: 1
        production_location:
          type: string
          pattern: '^[A-Z]{2}$'
        target_currency:
          type: string
          pattern: '^[A-Z]{3}$'
          default: EUR
        general_tolerance:
          type: string
          default: ISO2768-m
        options:
          type: object
          properties:
            include_monte_carlo:
              type: boolean
              default: true
            include_ml:
              type: boolean
              default: true
            include_similarity:
              type: boolean
              default: true
            async_mode:
              type: boolean
              default: false

    CostEstimateResponse:
      type: object
      properties:
        estimate_id:
          type: string
          format: uuid
        status:
          type: string
        unit_cost_eur:
          type: number
        total_cost_eur:
          type: number
        cost_breakdown:
          type: object
          properties:
            material_cost_eur:
              type: number
            process_cost_eur:
              type: number
            tooling_cost_eur:
              type: number
            setup_cost_eur:
              type: number
            overhead_cost_eur:
              type: number
            scrap_cost_eur:
              type: number
            logistics_cost_eur:
              type: number
        uncertainty:
          type: object
          properties:
            confidence_level:
              type: string
            confidence_score:
              type: number
            uncertainty_pct:
              type: number
            cost_low_eur:
              type: number
            cost_high_eur:
              type: number
        ensemble:
          type: object
          properties:
            formula_cost_eur:
              type: number
            ml_cost_eur:
              type: number
            similarity_cost_eur:
              type: number
            weights:
              type: object
        created_at:
          type: string
          format: date-time

  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
```

### 12.2 Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| CEE_VIEWER | GET /estimate, GET /benchmarks |
| CEE_USER | CEE_VIEWER + POST /estimate, POST /estimate/batch |
| CEE_ANALYST | CEE_USER + breakdown, sensitivity, analytics, compare |
| CEE_COST_ENGINEER | CEE_ANALYST + approve, POST /actuals |
| CEE_DATA_STEWARD | CEE_ANALYST + zarządzanie benchmarkami |
| CEE_OPS | Wszystko + zarządzanie modelami ML, cache flush |
| CEE_ADMIN | Pełny dostęp + DELETE + rollback |

---

## 13. Event Model (Kafka + Avro)

### 13.1 Topiki Kafka

| Temat | Partycje | RF | Retencja | Opis |
|-------|----------|----|----------|------|
| `cee.estimation.requested` | 12 | 3 | 7d | Żądanie wyceny |
| `cee.estimation.completed` | 12 | 3 | 30d | Wynik wyceny |
| `cee.estimation.approved` | 6 | 3 | 90d | Zatwierdzenie wyceny |
| `cee.cost.driver.changed` | 6 | 3 | 7d | Zmiana stawki maszynowej/materiałowej |
| `cee.benchmark.updated` | 4 | 3 | 30d | Aktualizacja danych porównawczych |
| `cee.ml.model.retrained` | 2 | 3 | 90d | Nowa wersja modelu ML |
| `cee.actual.recorded` | 6 | 3 | 365d | Zarejestrowany rzeczywisty koszt |
| `cee.outbox.relay` | 4 | 3 | 1d | Wewnętrzny relay outbox |

### 13.2 Schematy Avro

```json
// cee.estimation.completed
{
  "namespace": "com.ici.cee",
  "type": "record",
  "name": "EstimationCompleted",
  "doc": "Emitted when CEE finishes calculating a cost estimate",
  "fields": [
    {"name": "event_id",       "type": "string"},
    {"name": "occurred_at",    "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "schema_version", "type": "int", "default": 1},
    {"name": "estimate_id",    "type": "string"},
    {"name": "input_id",       "type": "string"},
    {"name": "product_name",   "type": "string"},
    {"name": "unit_cost_eur",  "type": "double"},
    {"name": "total_cost_eur", "type": "double"},
    {"name": "cost_breakdown", "type": {
      "type": "record", "name": "CostBreakdown",
      "fields": [
        {"name": "material_cost_eur",   "type": "double"},
        {"name": "process_cost_eur",    "type": "double"},
        {"name": "tooling_cost_eur",    "type": "double"},
        {"name": "setup_cost_eur",      "type": "double"},
        {"name": "overhead_cost_eur",   "type": "double"},
        {"name": "scrap_cost_eur",      "type": "double"},
        {"name": "logistics_cost_eur",  "type": "double"}
      ]
    }},
    {"name": "confidence_level",   "type": "string"},
    {"name": "confidence_score",   "type": "double"},
    {"name": "cost_low_eur",       "type": "double"},
    {"name": "cost_high_eur",      "type": "double"},
    {"name": "ml_model_version",   "type": ["null", "string"], "default": null},
    {"name": "similar_quote_ids",  "type": {"type": "array", "items": "string"}, "default": []},
    {"name": "annual_volume",      "type": "int"},
    {"name": "production_location","type": "string"},
    {"name": "calculation_duration_ms", "type": "int"}
  ]
}

// cee.estimation.approved
{
  "namespace": "com.ici.cee",
  "type": "record",
  "name": "EstimationApproved",
  "fields": [
    {"name": "event_id",     "type": "string"},
    {"name": "occurred_at",  "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "estimate_id",  "type": "string"},
    {"name": "product_name", "type": "string"},
    {"name": "unit_cost_eur","type": "double"},
    {"name": "approved_by",  "type": "string"},
    {"name": "approval_note","type": ["null", "string"], "default": null}
  ]
}

// cee.actual.recorded
{
  "namespace": "com.ici.cee",
  "type": "record",
  "name": "ActualCostRecorded",
  "fields": [
    {"name": "event_id",           "type": "string"},
    {"name": "occurred_at",        "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "estimate_id",        "type": "string"},
    {"name": "actual_cost_eur",    "type": "double"},
    {"name": "estimated_cost_eur", "type": "double"},
    {"name": "absolute_error_eur", "type": "double"},
    {"name": "mape",               "type": "double"},
    {"name": "actual_source",      "type": "string"},
    {"name": "within_confidence",  "type": "boolean"}
  ]
}

// cee.ml.model.retrained
{
  "namespace": "com.ici.cee",
  "type": "record",
  "name": "MLModelRetrained",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "occurred_at",     "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "model_name",      "type": "string"},
    {"name": "model_version",   "type": "string"},
    {"name": "mlflow_run_id",   "type": "string"},
    {"name": "training_samples","type": "int"},
    {"name": "mape_cv",         "type": "double"},
    {"name": "mape_holdout",    "type": "double"},
    {"name": "champion_mape",   "type": ["null", "double"], "default": null},
    {"name": "promoted",        "type": "boolean"}
  ]
}
```

### 13.3 Outbox Publisher

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid
import json
import asyncpg
from confluent_kafka import Producer
from confluent_kafka.schema_registry.avro import AvroSerializer

@dataclass
class OutboxEvent:
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    topic: str
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)

class CEEOutboxPublisher:
    """Polls cee.outbox and relays to Kafka with at-least-once delivery."""

    BATCH_SIZE = 50
    POLL_INTERVAL_SEC = 2

    def __init__(self, db_pool: asyncpg.Pool, producer: Producer):
        self._db = db_pool
        self._producer = producer

    async def relay_pending_events(self) -> int:
        """Claim a batch, produce to Kafka, mark published. Returns count."""
        async with self._db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT outbox_id, aggregate_type, aggregate_id,
                           event_type, topic, payload, headers
                    FROM cee.outbox
                    WHERE published_at IS NULL
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.BATCH_SIZE,
                )
                if not rows:
                    return 0

                ids_published = []
                for row in rows:
                    try:
                        self._producer.produce(
                            topic=row["topic"],
                            key=str(row["aggregate_id"]).encode(),
                            value=json.dumps(row["payload"]).encode(),
                            headers={
                                "event_type": row["event_type"],
                                "aggregate_type": row["aggregate_type"],
                                **row["headers"],
                            },
                        )
                        ids_published.append(row["outbox_id"])
                    except Exception as exc:
                        # Log but don't fail — will retry next poll
                        logger.error("produce failed outbox_id=%s: %s", row["outbox_id"], exc)

                self._producer.flush(timeout=5)

                if ids_published:
                    await conn.execute(
                        "UPDATE cee.outbox SET published_at = now(), retry_count = retry_count + 1 "
                        "WHERE outbox_id = ANY($1::uuid[])",
                        ids_published,
                    )
                return len(ids_published)

    async def run_forever(self) -> None:
        import asyncio
        while True:
            try:
                n = await self.relay_pending_events()
                if n == 0:
                    await asyncio.sleep(self.POLL_INTERVAL_SEC)
            except Exception as exc:
                logger.exception("outbox relay error: %s", exc)
                await asyncio.sleep(self.POLL_INTERVAL_SEC)
```

### 13.4 Konsumenci zdarzeń zewnętrznych

| Temat wejściowy | Producent | Akcja w CEE |
|----------------|-----------|-------------|
| `mie.material.price.updated` | MIE | Unieważnienie cache cen materiałów |
| `mie.material.updated` | MIE | Aktualizacja profilu materiału w benchmarkach |
| `mpe.process.rate.updated` | MPE | Aktualizacja stawek maszynowych w słowniku |
| `che.quote.recorded` | CHE | Aktualizacja benchmarków, dane treningowe dla ML |
| `sie.supplier.updated` | SIE | Aktualizacja wskaźników dostawców w modelu logistyki |

---

## 14. Feature Engineering (ML)

### 14.1 Cechy geometryczne (22 cechy)

```python
from dataclasses import dataclass
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import EstimationInput

class GeometryFeatureExtractor:
    """22 geometry-derived features for cost ML models."""

    def extract(self, inp: "EstimationInput") -> dict[str, float]:
        g = inp.geometry
        bbox_vol = (g.length_mm or 0) * (g.width_mm or 0) * (g.height_mm or 0) / 1000  # cm3

        features: dict[str, float] = {
            # Raw dimensions
            "volume_cm3":           g.volume_cm3,
            "net_volume_cm3":       g.net_volume_cm3,
            "surface_area_cm2":     g.surface_area_cm2 or 0.0,
            "bounding_box_vol_cm3": bbox_vol,

            # Ratios
            "buy_to_fly_ratio":     g.buy_to_fly_ratio,
            "fill_ratio":           g.net_volume_cm3 / max(g.volume_cm3, 0.001),
            "sa_to_vol_ratio":      (g.surface_area_cm2 or 0.0) / max(g.volume_cm3, 0.001),
            "bbox_fill_ratio":      g.volume_cm3 / max(bbox_vol, 0.001),

            # Log-transforms (stabilize skew)
            "log_volume":           np.log1p(g.volume_cm3),
            "log_net_volume":       np.log1p(g.net_volume_cm3),
            "log_surface_area":     np.log1p(g.surface_area_cm2 or 0.0),

            # Complexity
            "complexity_simple":    int(g.complexity_class == "SIMPLE"),
            "complexity_moderate":  int(g.complexity_class == "MODERATE"),
            "complexity_complex":   int(g.complexity_class == "COMPLEX"),
            "complexity_very":      int(g.complexity_class == "VERY_COMPLEX"),
            "feature_count":        float(g.feature_count),
            "log_feature_count":    np.log1p(g.feature_count),

            # Flags
            "thin_wall_flag":       float(g.thin_wall_flag),
            "deep_hole_flag":       float(g.deep_hole_flag),
            "undercut_flag":        float(g.undercut_flag),
            "flag_sum":             float(g.thin_wall_flag + g.deep_hole_flag + g.undercut_flag),

            # Aspect ratio
            "aspect_ratio_lw":      (g.length_mm or 1) / max(g.width_mm or 1, 0.001),
        }
        return features
```

### 14.2 Cechy materiałowe (18 cechy)

```python
MATERIAL_GROUP_ENCODING = {
    "CARBON_STEEL": 0, "ALLOY_STEEL": 1, "STAINLESS_STEEL": 2, "CAST_IRON": 3,
    "ALUMINUM": 4, "COPPER": 5, "TITANIUM": 6, "NICKEL_ALLOY": 7,
    "PLASTIC_THERMO": 8, "PLASTIC_THERMO_SET": 9, "COMPOSITE": 10, "OTHER": 11
}

class MaterialFeatureExtractor:
    def extract(self, inp: "EstimationInput") -> dict[str, float]:
        m = inp.material
        group_idx = MATERIAL_GROUP_ENCODING.get(m.material_group or "OTHER", 11)
        volume_kg  = inp.geometry.volume_cm3 * (m.density_g_cm3 or 7.85) / 1000
        net_vol_kg = inp.geometry.net_volume_cm3 * (m.density_g_cm3 or 7.85) / 1000
        price      = m.price_eur_per_kg or 1.0

        return {
            "material_group_idx":        float(group_idx),
            "density_g_cm3":             m.density_g_cm3 or 7.85,
            "price_eur_per_kg":          price,
            "log_price_eur_per_kg":      np.log1p(price),
            "machinability_index":       m.machinability_index or 1.0,
            "hardness_hrc":              m.hardness_hrc or 0.0,
            "availability_score":        m.availability_score or 0.8,
            "min_order_kg":              m.min_order_kg or 0.0,
            "leadtime_weeks":            m.leadtime_weeks or 4.0,
            # Derived
            "gross_weight_kg":           volume_kg,
            "net_weight_kg":             net_vol_kg,
            "material_cost_raw_eur":     volume_kg * price,
            "log_material_cost_raw":     np.log1p(volume_kg * price),
            "btf_material_ratio":        inp.geometry.buy_to_fly_ratio,
            "is_exotic":                 float(group_idx in (6, 7, 10)),
            "is_plastic":                float(group_idx in (8, 9)),
            "is_ferrous":                float(group_idx in (0, 1, 2, 3)),
            "machinability_x_hardness":  (m.machinability_index or 1.0) * (m.hardness_hrc or 0.0),
        }
```

### 14.3 Cechy procesowe (32 cechy)

```python
PROCESS_CLASS_ENCODING = {"CUT": 0, "MAC": 1, "FOR": 2, "JOI": 3, "ASS": 4, "FIN": 5, "SUR": 6, "ADD": 7}

class ProcessFeatureExtractor:
    def extract(self, inp: "EstimationInput") -> dict[str, float]:
        steps = inp.process_steps
        if not steps:
            return self._zeros()

        total_cycle    = sum(s.cycle_time_sec or 0 for s in steps)
        total_setup    = sum(s.setup_time_min or 0 for s in steps)
        total_machine  = sum(s.cost_per_hour_eur or 50 for s in steps)
        classes        = [s.process_class for s in steps]
        it_grades      = [s.tolerance_it for s in steps if s.tolerance_it]
        ra_values      = [s.surface_ra_um for s in steps if s.surface_ra_um]

        n = len(steps)
        return {
            "n_process_steps":          float(n),
            "log_n_steps":              np.log1p(n),
            "total_cycle_time_sec":     total_cycle,
            "log_total_cycle":          np.log1p(total_cycle),
            "avg_cycle_per_step":       total_cycle / max(n, 1),
            "total_setup_min":          total_setup,
            "log_setup_min":            np.log1p(total_setup),
            "setup_to_cycle_ratio":     total_setup * 60 / max(total_cycle, 1),
            "avg_machine_rate":         total_machine / max(n, 1),
            "max_machine_rate":         max(s.cost_per_hour_eur or 50 for s in steps),
            "n_cnc_steps":              float(sum(1 for s in steps if "CNC" in (s.machine_type or ""))),
            "n_manual_steps":           float(sum(1 for s in steps if s.automation_level == 0)),
            "n_automated_steps":        float(sum(1 for s in steps if (s.automation_level or 0) >= 2)),
            "has_grinding":             float(any(s.process_type in ("SURFACE_GRINDING","CYL_GRINDING") for s in steps)),
            "has_welding":              float(any(s.process_type in ("MIG_WELDING","TIG_WELDING") for s in steps)),
            "has_forming":              float(any(s.process_class == "FOR" for s in steps)),
            "has_finishing":            float(any(s.process_class == "FIN" for s in steps)),
            "dominant_class_idx":       float(PROCESS_CLASS_ENCODING.get(
                                             max(set(classes), key=classes.count), 1)),
            "class_diversity":          float(len(set(classes))),
            "min_it_grade":             float(min(it_grades, default=9)),
            "max_it_grade":             float(max(it_grades, default=9)),
            "has_tight_tolerance":      float(any(g <= 6 for g in it_grades)),
            "has_precision_finish":     float(any(r <= 0.8 for r in ra_values)),
            "min_ra_um":                float(min(ra_values, default=3.2)),
            "total_operator_count":     float(sum(s.operator_count or 1 for s in steps)),
            "energy_kw_total":          float(sum(s.energy_kw or 0 for s in steps)),
            "avg_tool_wear":            float(np.mean([s.tool_wear_factor or 1.0 for s in steps])),
            "max_tool_wear":            float(max(s.tool_wear_factor or 1.0 for s in steps)),
            "n_steps_sq":               float(n ** 2),
            "setup_x_complexity":       total_setup * inp.geometry.feature_count,
            "cycle_x_btf":              total_cycle * inp.geometry.buy_to_fly_ratio,
        }

    def _zeros(self) -> dict[str, float]:
        return {k: 0.0 for k in [
            "n_process_steps","log_n_steps","total_cycle_time_sec","log_total_cycle",
            "avg_cycle_per_step","total_setup_min","log_setup_min","setup_to_cycle_ratio",
            "avg_machine_rate","max_machine_rate","n_cnc_steps","n_manual_steps",
            "n_automated_steps","has_grinding","has_welding","has_forming","has_finishing",
            "dominant_class_idx","class_diversity","min_it_grade","max_it_grade",
            "has_tight_tolerance","has_precision_finish","min_ra_um","total_operator_count",
            "energy_kw_total","avg_tool_wear","max_tool_wear","n_steps_sq",
            "setup_x_complexity","cycle_x_btf",
        ]}
```

### 14.4 Cechy wolumenu i lokalizacji (16 cechy)

```python
LOCATION_IDX = {
    "DE":1, "PL":2, "CZ":3, "RO":4, "CN":5, "IN":6,
    "MX":7, "US":8, "TR":9, "VN":10, "TH":11, "UA":12,
}
LOCATION_COST_INDEX = {
    "DE":1.00, "AT":0.95, "CH":1.10, "US":0.92, "GB":0.85,
    "FR":0.88, "NL":0.90, "SE":0.87, "PL":0.45, "CZ":0.48,
    "RO":0.38, "HU":0.44, "SK":0.46, "CN":0.30, "IN":0.22,
    "MX":0.35, "VN":0.20, "TH":0.26, "TR":0.32, "UA":0.25,
}

class VolumeLocationFeatureExtractor:
    def extract(self, inp: "EstimationInput") -> dict[str, float]:
        v  = inp.annual_volume
        bs = inp.batch_size
        loc = inp.production_location
        return {
            "annual_volume":            float(v),
            "log_volume":               np.log1p(v),
            "batch_size":               float(bs),
            "log_batch_size":           np.log1p(bs),
            "n_batches_per_year":       float(v / max(bs, 1)),
            "log_n_batches":            np.log1p(v / max(bs, 1)),
            "volume_tier_low":          float(v < 50),
            "volume_tier_medium":       float(50 <= v < 1000),
            "volume_tier_high":         float(1000 <= v < 25000),
            "volume_tier_mass":         float(v >= 25000),
            "location_idx":             float(LOCATION_IDX.get(loc, 0)),
            "location_cost_index":      LOCATION_COST_INDEX.get(loc, 0.5),
            "is_eu_location":           float(loc in ("DE","PL","CZ","RO","AT","FR","HU","SK")),
            "is_asia_location":         float(loc in ("CN","IN","VN","TH")),
            "is_low_cost":              float(LOCATION_COST_INDEX.get(loc, 1.0) < 0.4),
            "volume_x_cost_index":      np.log1p(v) * LOCATION_COST_INDEX.get(loc, 0.5),
        }
```

### 14.5 Cechy historyczne i pochodne (12 cechy)

```python
class DerivedFeatureExtractor:
    """Cross-domain derived features and historical anchors."""

    def extract(
        self,
        inp: "EstimationInput",
        geo: dict, mat: dict, proc: dict, vol: dict,
        similar_costs: list[float] | None = None,
    ) -> dict[str, float]:
        similar_costs = similar_costs or []
        formula_cost  = mat.get("material_cost_raw_eur", 0)
        cycle         = proc.get("total_cycle_time_sec", 0)
        rate          = proc.get("avg_machine_rate", 60)
        ci            = vol.get("location_cost_index", 0.5)

        return {
            # Estimated cost proxies
            "est_process_cost_eur":         cycle / 3600 * rate * ci,
            "est_material_cost_eur":        formula_cost,
            "est_total_proxy_eur":          formula_cost + cycle / 3600 * rate * ci,
            "log_est_total_proxy":          np.log1p(formula_cost + cycle / 3600 * rate * ci),
            "material_to_process_ratio":    formula_cost / max(cycle / 3600 * rate * ci, 0.01),

            # Similar cost anchors (from SCSE)
            "similar_median_eur":           float(np.median(similar_costs)) if similar_costs else 0.0,
            "similar_mean_eur":             float(np.mean(similar_costs))   if similar_costs else 0.0,
            "similar_count":                float(len(similar_costs)),
            "similar_cv":                   float(np.std(similar_costs) / max(np.mean(similar_costs), 1))
                                                if len(similar_costs) >= 2 else 0.0,

            # Cross interactions
            "complexity_x_steps":           geo.get("complexity_very", 0) * proc.get("n_process_steps", 1),
            "btf_x_price":                  geo.get("buy_to_fly_ratio", 1) * mat.get("price_eur_per_kg", 1),
            "setup_fraction":               proc.get("total_setup_min", 0) * 60 / max(
                                                inp.annual_volume * proc.get("total_cycle_time_sec", 1), 1),
        }
```

### 14.6 Feature Vector Assembly

```python
import pandas as pd

class CEEFeatureAssembler:
    """Assembles all extractors into a single feature vector (100 features)."""

    def __init__(self):
        self.geo_extractor  = GeometryFeatureExtractor()
        self.mat_extractor  = MaterialFeatureExtractor()
        self.proc_extractor = ProcessFeatureExtractor()
        self.vol_extractor  = VolumeLocationFeatureExtractor()
        self.der_extractor  = DerivedFeatureExtractor()

    def assemble(
        self,
        inp: "EstimationInput",
        similar_costs: list[float] | None = None,
    ) -> pd.Series:
        geo  = self.geo_extractor.extract(inp)
        mat  = self.mat_extractor.extract(inp)
        proc = self.proc_extractor.extract(inp)
        vol  = self.vol_extractor.extract(inp)
        der  = self.der_extractor.extract(inp, geo, mat, proc, vol, similar_costs)

        combined = {**geo, **mat, **proc, **vol, **der}
        return pd.Series(combined)

    @property
    def feature_names(self) -> list[str]:
        dummy = EstimationInput.dummy()
        return list(self.assemble(dummy).index)
```

---

## 15. ML Models (XGBoost / LightGBM)

### 15.1 Cel i architektura

| Model | Algorytm | Cel | Metryka |
|-------|----------|-----|---------|
| `cee-unit-cost` | XGBoost (reg:squaredlogerror) | unit_cost_eur | MAPE < 10% |
| `cee-material-cost` | LightGBM (regression_l1) | material_cost_eur | MAPE < 5% |
| `cee-process-cost` | LightGBM (regression_l1) | process_cost_eur | MAPE < 8% |
| `cee-overhead-cost` | XGBoost (reg:squarederror) | overhead_cost_eur | MAPE < 6% |
| `cee-confidence` | XGBoost (binary:logistic) | within_confidence_interval | AUC > 0.82 |

### 15.2 XGBoost — model kosztu jednostkowego

```python
import xgboost as xgb
import numpy as np
import pandas as pd
import mlflow
import mlflow.xgboost
from sklearn.model_selection import KFold
from sklearn.preprocessing import RobustScaler

XGB_PARAMS = {
    "objective":          "reg:squaredlogerror",
    "n_estimators":       1200,
    "learning_rate":      0.03,
    "max_depth":          7,
    "min_child_weight":   5,
    "subsample":          0.80,
    "colsample_bytree":   0.75,
    "colsample_bylevel":  0.90,
    "reg_alpha":          0.1,
    "reg_lambda":         1.5,
    "gamma":              0.05,
    "tree_method":        "hist",
    "device":             "cpu",
    "random_state":       42,
    "eval_metric":        ["rmse", "mape"],
    "early_stopping_rounds": 50,
}

class UnitCostXGBModel:
    """XGBoost unit cost predictor with SHAP explainability."""

    MODEL_NAME = "cee-unit-cost-xgb"

    def __init__(self):
        self.model:   xgb.XGBRegressor | None = None
        self.scaler:  RobustScaler = RobustScaler()
        self.feature_names: list[str] = []

    # Training pipeline
    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        X_val: pd.DataFrame,
        y_val: pd.Series,
    ) -> dict[str, float]:
        self.feature_names = list(X.columns)

        X_scaled     = pd.DataFrame(self.scaler.fit_transform(X),     columns=X.columns)
        X_val_scaled = pd.DataFrame(self.scaler.transform(X_val),     columns=X_val.columns)

        self.model = xgb.XGBRegressor(**XGB_PARAMS)
        self.model.fit(
            X_scaled, np.log1p(y),
            eval_set=[(X_val_scaled, np.log1p(y_val))],
            verbose=100,
        )

        preds = np.expm1(self.model.predict(X_val_scaled))
        mape  = float(np.mean(np.abs(y_val - preds) / np.clip(y_val, 1, None)) * 100)
        rmse  = float(np.sqrt(np.mean((y_val - preds) ** 2)))

        return {"mape_pct": mape, "rmse_eur": rmse, "n_trees": self.model.best_iteration}

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert self.model is not None, "Model not trained"
        X_scaled = pd.DataFrame(self.scaler.transform(X), columns=X.columns)
        return np.expm1(self.model.predict(X_scaled))

    def predict_with_interval(
        self, X: pd.DataFrame, alpha: float = 0.10
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Returns (point, lower_P10, upper_P90) using quantile predictions."""
        base   = self.predict(X)
        sigma  = base * 0.12           # Heuristic σ ≈ 12% of prediction
        low    = base * (1 - 1.645 * 0.12)
        high   = base * (1 + 1.645 * 0.12)
        return base, low, high

    def explain(self, X: pd.DataFrame) -> dict[str, list[float]]:
        """SHAP values for explainability."""
        import shap
        explainer = shap.TreeExplainer(self.model)
        X_scaled  = pd.DataFrame(self.scaler.transform(X), columns=X.columns)
        shap_vals = explainer.shap_values(X_scaled)
        return {
            "shap_values":   shap_vals.tolist(),
            "feature_names": self.feature_names,
            "base_value":    float(explainer.expected_value),
        }

    def get_feature_importance(self, top_n: int = 20) -> list[dict]:
        if self.model is None:
            return []
        imp = self.model.feature_importances_
        ranked = sorted(
            zip(self.feature_names, imp),
            key=lambda x: x[1], reverse=True,
        )[:top_n]
        total = sum(v for _, v in ranked)
        return [
            {"feature": name, "importance": float(val), "pct": float(val / max(total, 1e-9) * 100)}
            for name, val in ranked
        ]
```

### 15.3 LightGBM — modele komponentowe

```python
import lightgbm as lgb

LGBM_PARAMS_COST = {
    "objective":       "regression_l1",
    "n_estimators":    800,
    "learning_rate":   0.05,
    "num_leaves":      63,
    "max_depth":       -1,
    "min_child_samples": 10,
    "feature_fraction":  0.80,
    "bagging_fraction":  0.80,
    "bagging_freq":      5,
    "reg_alpha":         0.1,
    "reg_lambda":        0.5,
    "verbose":          -1,
    "random_state":     42,
}

class ComponentCostLGBMModel:
    """LightGBM for material/process/overhead cost components."""

    def __init__(self, target_name: str, model_name: str):
        self.target_name = target_name
        self.model_name  = model_name
        self.model: lgb.LGBMRegressor | None = None

    def train(
        self, X: pd.DataFrame, y: pd.Series,
        X_val: pd.DataFrame, y_val: pd.Series,
    ) -> dict[str, float]:
        callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)]
        self.model = lgb.LGBMRegressor(**LGBM_PARAMS_COST)
        self.model.fit(
            X, np.log1p(y),
            eval_set=[(X_val, np.log1p(y_val))],
            callbacks=callbacks,
        )
        preds = np.expm1(self.model.predict(X_val))
        mape  = float(np.mean(np.abs(y_val - preds) / np.clip(y_val, 1, None)) * 100)
        return {"mape_pct": mape, "n_trees": self.model.best_iteration_}

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert self.model is not None
        return np.expm1(self.model.predict(X))
```

### 15.4 MLflow Model Registry

```python
import mlflow
import mlflow.xgboost
import mlflow.lightgbm
from mlflow.tracking import MlflowClient

MLFLOW_TRACKING_URI = "http://mlflow.internal:5000"
MLFLOW_EXPERIMENT   = "cee-cost-estimation"

class CEEModelRegistry:
    """Manages train/log/promote/load lifecycle via MLflow."""

    def __init__(self):
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT)
        self._client = MlflowClient()

    def log_and_register(
        self,
        model: UnitCostXGBModel | ComponentCostLGBMModel,
        metrics: dict[str, float],
        params: dict,
        artifact_path: str,
    ) -> str:
        with mlflow.start_run() as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)

            if isinstance(model, UnitCostXGBModel):
                mlflow.xgboost.log_model(model.model, artifact_path,
                                         registered_model_name=model.MODEL_NAME)
            else:
                mlflow.lightgbm.log_model(model.model, artifact_path,
                                          registered_model_name=model.model_name)

            return run.info.run_id

    def promote_to_champion(self, model_name: str, version: int) -> None:
        """Archive current champion, promote challenger."""
        current = self._client.get_latest_versions(model_name, stages=["Production"])
        for v in current:
            self._client.transition_model_version_stage(
                model_name, v.version, "Archived"
            )
        self._client.transition_model_version_stage(
            model_name, str(version), "Production"
        )

    def load_champion(self, model_name: str) -> mlflow.pyfunc.PyFuncModel:
        return mlflow.pyfunc.load_model(f"models:/{model_name}/Production")
```

### 15.5 Ensemble Predictor

```python
from dataclasses import dataclass

@dataclass
class EnsemblePrediction:
    unit_cost_eur:      float
    material_cost_eur:  float
    process_cost_eur:   float
    overhead_cost_eur:  float
    cost_low_eur:       float
    cost_high_eur:      float
    weights_used:       dict[str, float]
    model_version:      str
    shap_top5:          list[dict]

class CEEEnsemblePredictor:
    """
    Combines formula, XGBoost, LightGBM, and similarity-based cost.
    Weights adapt based on confidence of each signal.
    """

    DEFAULT_WEIGHTS = {
        "formula":    0.50,
        "ml_unit":    0.30,
        "similarity": 0.20,
    }

    def __init__(self, registry: CEEModelRegistry):
        self._registry  = registry
        self._unit_model:      mlflow.pyfunc.PyFuncModel | None = None
        self._mat_model:       mlflow.pyfunc.PyFuncModel | None = None
        self._proc_model:      mlflow.pyfunc.PyFuncModel | None = None
        self._overhead_model:  mlflow.pyfunc.PyFuncModel | None = None

    def load_models(self) -> None:
        self._unit_model     = self._registry.load_champion("cee-unit-cost-xgb")
        self._mat_model      = self._registry.load_champion("cee-material-cost-lgbm")
        self._proc_model     = self._registry.load_champion("cee-process-cost-lgbm")
        self._overhead_model = self._registry.load_champion("cee-overhead-cost-xgb")

    def predict(
        self,
        feature_vector:   pd.Series,
        formula_cost:     float,
        similarity_cost:  float | None,
        n_similar:        int = 0,
    ) -> EnsemblePrediction:
        X = feature_vector.to_frame().T

        ml_unit     = float(self._unit_model.predict(X)[0])
        mat_cost    = float(self._mat_model.predict(X)[0])
        proc_cost   = float(self._proc_model.predict(X)[0])
        overhead    = float(self._overhead_model.predict(X)[0])

        # Adaptive weights
        w = dict(self.DEFAULT_WEIGHTS)
        if similarity_cost is None or n_similar == 0:
            w["formula"]    += w["similarity"]
            w["similarity"]  = 0.0
        elif n_similar < 3:
            w["formula"]    += w["similarity"] * 0.5
            w["similarity"] *= 0.5

        final_unit = (
            w["formula"]    * formula_cost   +
            w["ml_unit"]    * ml_unit        +
            w["similarity"] * (similarity_cost or formula_cost)
        )

        # Uncertainty: heuristic P10/P90
        spread = final_unit * 0.20
        return EnsemblePrediction(
            unit_cost_eur     = round(final_unit, 4),
            material_cost_eur = round(mat_cost, 4),
            process_cost_eur  = round(proc_cost, 4),
            overhead_cost_eur = round(overhead, 4),
            cost_low_eur      = round(final_unit - spread, 4),
            cost_high_eur     = round(final_unit + spread, 4),
            weights_used      = w,
            model_version     = "production",
            shap_top5         = [],
        )
```

### 15.6 Pipeline treningowy (Airflow DAG)

```python
# dags/cee_model_training.py
from airflow.decorators import dag, task
from datetime import datetime, timedelta

@dag(
    dag_id="cee_model_retraining",
    schedule_interval="@weekly",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["cee", "ml"],
)
def cee_model_retraining_dag():

    @task
    def extract_training_data() -> str:
        """Pull accuracy_log + estimation_inputs + cost_estimates with actual costs."""
        from cee.training import TrainingDataExtractor
        path = TrainingDataExtractor().extract_to_parquet(
            min_samples=500,
            lookback_days=365,
        )
        return path

    @task
    def engineer_features(data_path: str) -> str:
        from cee.training import FeatureEngineeringPipeline
        return FeatureEngineeringPipeline().run(data_path)

    @task
    def train_unit_cost_model(feature_path: str) -> dict:
        from cee.training import UnitCostTrainer
        return UnitCostTrainer().train_and_log(feature_path)

    @task
    def train_component_models(feature_path: str) -> dict:
        from cee.training import ComponentModelTrainer
        return ComponentModelTrainer().train_all(feature_path)

    @task
    def evaluate_and_promote(unit_result: dict, comp_result: dict) -> bool:
        from cee.training import ModelEvaluator
        ev = ModelEvaluator()
        promoted = ev.champion_challenger(unit_result, mape_threshold=10.0)
        if promoted:
            ev.publish_retrain_event(unit_result)
        return promoted

    data_path     = extract_training_data()
    feature_path  = engineer_features(data_path)
    unit_result   = train_unit_cost_model(feature_path)
    comp_result   = train_component_models(feature_path)
    evaluate_and_promote(unit_result, comp_result)

cee_model_retraining_dag()
```

### 15.7 Cross-Validation i walidacja holdout

```python
from sklearn.model_selection import KFold
from sklearn.metrics import mean_absolute_percentage_error

class CVEvaluator:
    """5-fold stratified CV + holdout evaluation for CEE models."""

    N_SPLITS = 5
    HOLDOUT_RATIO = 0.15

    def evaluate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        model_factory,
    ) -> dict[str, float]:
        holdout_n = int(len(X) * self.HOLDOUT_RATIO)
        X_holdout, y_holdout = X.iloc[:holdout_n], y.iloc[:holdout_n]
        X_cv,      y_cv      = X.iloc[holdout_n:], y.iloc[holdout_n:]

        kf = KFold(n_splits=self.N_SPLITS, shuffle=True, random_state=42)
        cv_mapes = []

        for train_idx, val_idx in kf.split(X_cv):
            X_tr, X_vl = X_cv.iloc[train_idx], X_cv.iloc[val_idx]
            y_tr, y_vl = y_cv.iloc[train_idx], y_cv.iloc[val_idx]
            m = model_factory()
            m.train(X_tr, y_tr, X_vl, y_vl)
            preds    = m.predict(X_vl)
            cv_mapes.append(mean_absolute_percentage_error(y_vl, preds) * 100)

        # Final model on all CV data
        final_model = model_factory()
        split_idx   = int(len(X_cv) * 0.85)
        final_model.train(
            X_cv.iloc[:split_idx], y_cv.iloc[:split_idx],
            X_cv.iloc[split_idx:], y_cv.iloc[split_idx:],
        )
        holdout_preds = final_model.predict(X_holdout)
        holdout_mape  = mean_absolute_percentage_error(y_holdout, holdout_preds) * 100

        return {
            "cv_mape_mean":    float(np.mean(cv_mapes)),
            "cv_mape_std":     float(np.std(cv_mapes)),
            "cv_mape_max":     float(np.max(cv_mapes)),
            "holdout_mape":    float(holdout_mape),
            "n_train":         len(X_cv),
            "n_holdout":       holdout_n,
        }
```
