-- =============================================================================
-- MIGRATION 002: Core business tables
-- All tables live in schema `ici`.
-- Multi-tenancy: every table carries tenant_id (string, max 64 chars).
-- Soft-delete:   is_deleted BOOLEAN + deleted_at TIMESTAMPTZ.
-- Optimistic lock: version INTEGER (incremented on every UPDATE).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ENUM types  (shared across tables)
-- ---------------------------------------------------------------------------
CREATE TYPE ici.material_class_t AS ENUM (
    'METAL', 'PLASTIC', 'COMPOSITE', 'CERAMIC',
    'RUBBER', 'CHEMICAL', 'ELECTRONIC', 'OTHER'
);

CREATE TYPE ici.material_status_t AS ENUM (
    'ACTIVE', 'DEPRECATED', 'PROTOTYPE', 'BLOCKED'
);

CREATE TYPE ici.supplier_status_t AS ENUM (
    'PENDING', 'QUALIFIED', 'SUSPENDED', 'BLACKLISTED'
);

CREATE TYPE ici.process_type_t AS ENUM (
    'MACHINING', 'CASTING', 'FORGING', 'STAMPING',
    'INJECTION_MOLDING', 'WELDING', 'ASSEMBLY', 'SURFACE_TREATMENT', 'OTHER'
);

CREATE TYPE ici.rfq_status_t AS ENUM (
    'DRAFT', 'SENT', 'CLOSED', 'CANCELLED', 'AWARDED'
);

CREATE TYPE ici.quote_status_t AS ENUM (
    'RECEIVED', 'UNDER_REVIEW', 'ACCEPTED', 'REJECTED', 'EXPIRED'
);

CREATE TYPE ici.risk_category_t AS ENUM (
    'SUPPLY', 'PRICE', 'MATERIAL', 'PROCESS',
    'GEOPOLITICAL', 'FINANCIAL', 'QUALITY', 'COMPLIANCE'
);

CREATE TYPE ici.risk_status_t AS ENUM (
    'OPEN', 'ACKNOWLEDGED', 'MITIGATING', 'RESOLVED', 'ACCEPTED'
);

CREATE TYPE ici.forecast_horizon_t AS ENUM ('SHORT', 'MEDIUM', 'LONG');
CREATE TYPE ici.forecast_method_t  AS ENUM ('SARIMA', 'LSTM', 'PROPHET', 'ENSEMBLE', 'MOVING_AVERAGE');
CREATE TYPE ici.forecast_status_t  AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED');

-- ---------------------------------------------------------------------------
-- HELPER: updated_at auto-trigger function
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    NEW.version    = OLD.version + 1;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. MATERIALS
--    Partitioned by tenant_id hash (8 partitions) for large multi-tenant scale.
--    A GIN trigram index enables fast full-text search on name/description.
-- ---------------------------------------------------------------------------
CREATE TABLE ici.materials (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)     NOT NULL,
    material_number VARCHAR(64)     NOT NULL,
    name            VARCHAR(256)    NOT NULL,
    description     TEXT            NOT NULL DEFAULT '',
    material_class  ici.material_class_t NOT NULL DEFAULT 'OTHER',
    base_unit       VARCHAR(8)      NOT NULL DEFAULT 'KG',

    -- Physical properties
    density_g_cm3   NUMERIC(10, 4)  CHECK (density_g_cm3 > 0),
    tensile_strength_mpa NUMERIC(10, 2),
    melting_point_c NUMERIC(8, 2),

    -- Pricing (always stored in EUR; currency is informational)
    price_eur       NUMERIC(18, 4)  NOT NULL CHECK (price_eur >= 0),
    currency        CHAR(3)         NOT NULL DEFAULT 'EUR',
    price_updated_at TIMESTAMPTZ,

    -- Procurement
    lead_time_days  INTEGER         NOT NULL DEFAULT 0 CHECK (lead_time_days >= 0),
    min_order_qty   NUMERIC(18, 4)  NOT NULL DEFAULT 1 CHECK (min_order_qty > 0),
    supplier_id     UUID,           -- preferred supplier FK (soft ref)

    status          ici.material_status_t NOT NULL DEFAULT 'ACTIVE',

    -- Audit / concurrency
    version         INTEGER         NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(256),
    updated_by      VARCHAR(256),
    is_deleted      BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,

    -- Partition key must be part of PK for declarative partitioning
    PRIMARY KEY (id, tenant_id),
    CONSTRAINT uq_materials_tenant_number UNIQUE (tenant_id, material_number)
) PARTITION BY HASH (tenant_id);

-- 8 hash partitions — adjust modulus/remainder for your tenant count
CREATE TABLE ici.materials_p0 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 0);
CREATE TABLE ici.materials_p1 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 1);
CREATE TABLE ici.materials_p2 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 2);
CREATE TABLE ici.materials_p3 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 3);
CREATE TABLE ici.materials_p4 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 4);
CREATE TABLE ici.materials_p5 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 5);
CREATE TABLE ici.materials_p6 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 6);
CREATE TABLE ici.materials_p7 PARTITION OF ici.materials FOR VALUES WITH (MODULUS 8, REMAINDER 7);

CREATE TRIGGER trg_materials_updated_at
    BEFORE UPDATE ON ici.materials
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

-- ---------------------------------------------------------------------------
-- 2. SUPPLIERS
-- ---------------------------------------------------------------------------
CREATE TABLE ici.suppliers (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)     NOT NULL,
    code            VARCHAR(32)     NOT NULL,
    name            VARCHAR(256)    NOT NULL,

    -- Address
    country_code    CHAR(2)         NOT NULL,
    city            VARCHAR(128)    NOT NULL DEFAULT '',
    address_line1   VARCHAR(256)    NOT NULL DEFAULT '',
    address_line2   VARCHAR(256),
    postal_code     VARCHAR(16)     NOT NULL DEFAULT '',
    region          VARCHAR(128),

    -- Contact
    contact_email   VARCHAR(256)    NOT NULL DEFAULT '',
    contact_phone   VARCHAR(32)     NOT NULL DEFAULT '',
    website         VARCHAR(512),

    -- Performance scores  [0, 1]
    quality_score   NUMERIC(5, 4)   CHECK (quality_score BETWEEN 0 AND 1),
    delivery_score  NUMERIC(5, 4)   CHECK (delivery_score BETWEEN 0 AND 1),
    price_score     NUMERIC(5, 4)   CHECK (price_score BETWEEN 0 AND 1),
    financial_score NUMERIC(5, 4)   CHECK (financial_score BETWEEN 0 AND 1),
    -- Weighted overall: quality 35% + delivery 35% + price 20% + financial 10%
    overall_score   NUMERIC(5, 4)
        GENERATED ALWAYS AS (
            COALESCE(quality_score,  0) * 0.35 +
            COALESCE(delivery_score, 0) * 0.35 +
            COALESCE(price_score,    0) * 0.20 +
            COALESCE(financial_score,0) * 0.10
        ) STORED,

    status          ici.supplier_status_t NOT NULL DEFAULT 'PENDING',
    suspension_reason VARCHAR(512)  NOT NULL DEFAULT '',
    qualified_at    TIMESTAMPTZ,

    version         INTEGER         NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(256),
    updated_by      VARCHAR(256),
    is_deleted      BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,

    PRIMARY KEY (id, tenant_id),
    CONSTRAINT uq_suppliers_tenant_code UNIQUE (tenant_id, code)
) PARTITION BY HASH (tenant_id);

CREATE TABLE ici.suppliers_p0 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 0);
CREATE TABLE ici.suppliers_p1 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 1);
CREATE TABLE ici.suppliers_p2 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 2);
CREATE TABLE ici.suppliers_p3 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 3);
CREATE TABLE ici.suppliers_p4 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 4);
CREATE TABLE ici.suppliers_p5 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 5);
CREATE TABLE ici.suppliers_p6 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 6);
CREATE TABLE ici.suppliers_p7 PARTITION OF ici.suppliers FOR VALUES WITH (MODULUS 8, REMAINDER 7);

CREATE TRIGGER trg_suppliers_updated_at
    BEFORE UPDATE ON ici.suppliers
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

-- ---------------------------------------------------------------------------
-- 3. MANUFACTURING PROCESSES
-- ---------------------------------------------------------------------------
CREATE TABLE ici.processes (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(64)     NOT NULL,
    code                VARCHAR(32)     NOT NULL,
    name                VARCHAR(256)    NOT NULL,
    process_type        ici.process_type_t NOT NULL DEFAULT 'OTHER',
    description         TEXT            NOT NULL DEFAULT '',

    -- Cost drivers
    machine_rate_eur_hr NUMERIC(10, 4)  NOT NULL CHECK (machine_rate_eur_hr >= 0),
    labor_rate_eur_hr   NUMERIC(10, 4)  NOT NULL CHECK (labor_rate_eur_hr >= 0),
    cycle_time_seconds  NUMERIC(10, 2)  NOT NULL CHECK (cycle_time_seconds >= 0),
    setup_time_seconds  NUMERIC(10, 2)  NOT NULL DEFAULT 0 CHECK (setup_time_seconds >= 0),
    scrap_rate          NUMERIC(6, 5)   NOT NULL DEFAULT 0 CHECK (scrap_rate BETWEEN 0 AND 1),

    -- Computed cost per hour (machine + labor)
    hourly_cost_eur     NUMERIC(10, 4)
        GENERATED ALWAYS AS (machine_rate_eur_hr + labor_rate_eur_hr) STORED,

    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,

    version             INTEGER         NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          VARCHAR(256),
    updated_by          VARCHAR(256),
    is_deleted          BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at          TIMESTAMPTZ,

    PRIMARY KEY (id, tenant_id),
    CONSTRAINT uq_processes_tenant_code UNIQUE (tenant_id, code)
) PARTITION BY HASH (tenant_id);

CREATE TABLE ici.processes_p0 PARTITION OF ici.processes FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE ici.processes_p1 PARTITION OF ici.processes FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE ici.processes_p2 PARTITION OF ici.processes FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE ici.processes_p3 PARTITION OF ici.processes FOR VALUES WITH (MODULUS 4, REMAINDER 3);

CREATE TRIGGER trg_processes_updated_at
    BEFORE UPDATE ON ici.processes
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

-- ---------------------------------------------------------------------------
-- 4. RFQ (Request for Quotation)
--    Partitioned by RANGE on created_at (monthly) — high write volume.
-- ---------------------------------------------------------------------------
CREATE TABLE ici.rfqs (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)     NOT NULL,
    rfq_number      VARCHAR(64)     NOT NULL,
    title           VARCHAR(256)    NOT NULL,
    description     TEXT            NOT NULL DEFAULT '',
    status          ici.rfq_status_t NOT NULL DEFAULT 'DRAFT',

    deadline        DATE            NOT NULL,
    delivery_date   DATE,
    delivery_terms  VARCHAR(16)     NOT NULL DEFAULT 'DAP',
    payment_terms   VARCHAR(8)      NOT NULL DEFAULT 'NET30',
    currency        CHAR(3)         NOT NULL DEFAULT 'EUR',
    notes           TEXT            NOT NULL DEFAULT '',

    awarded_supplier_id UUID,
    awarded_at      TIMESTAMPTZ,

    version         INTEGER         NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(256),
    updated_by      VARCHAR(256),
    is_deleted      BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,

    PRIMARY KEY (id, created_at),
    CONSTRAINT uq_rfqs_tenant_number UNIQUE (tenant_id, rfq_number, created_at)
) PARTITION BY RANGE (created_at);

-- Monthly partitions 2024-2026 (extend as needed)
CREATE TABLE ici.rfqs_y2024m01 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE ici.rfqs_y2024m02 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE ici.rfqs_y2024m03 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
CREATE TABLE ici.rfqs_y2024m04 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-04-01') TO ('2024-05-01');
CREATE TABLE ici.rfqs_y2024m05 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');
CREATE TABLE ici.rfqs_y2024m06 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-06-01') TO ('2024-07-01');
CREATE TABLE ici.rfqs_y2024m07 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-07-01') TO ('2024-08-01');
CREATE TABLE ici.rfqs_y2024m08 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-08-01') TO ('2024-09-01');
CREATE TABLE ici.rfqs_y2024m09 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-09-01') TO ('2024-10-01');
CREATE TABLE ici.rfqs_y2024m10 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-10-01') TO ('2024-11-01');
CREATE TABLE ici.rfqs_y2024m11 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-11-01') TO ('2024-12-01');
CREATE TABLE ici.rfqs_y2024m12 PARTITION OF ici.rfqs FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');
CREATE TABLE ici.rfqs_y2025m01 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE ici.rfqs_y2025m02 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE ici.rfqs_y2025m03 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE ici.rfqs_y2025m04 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE ici.rfqs_y2025m05 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE ici.rfqs_y2025m06 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE ici.rfqs_y2025m07 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE ici.rfqs_y2025m08 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE ici.rfqs_y2025m09 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE ici.rfqs_y2025m10 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE ici.rfqs_y2025m11 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE ici.rfqs_y2025m12 PARTITION OF ici.rfqs FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE ici.rfqs_y2026m01 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE ici.rfqs_y2026m02 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE ici.rfqs_y2026m03 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE ici.rfqs_y2026m04 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE ici.rfqs_y2026m05 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE ici.rfqs_y2026m06 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE ici.rfqs_y2026m07 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE ici.rfqs_y2026m08 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE ici.rfqs_y2026m09 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE ici.rfqs_y2026m10 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE ici.rfqs_y2026m11 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE ici.rfqs_y2026m12 PARTITION OF ici.rfqs FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TRIGGER trg_rfqs_updated_at
    BEFORE UPDATE ON ici.rfqs
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

-- RFQ Line Items (child of rfqs — no partition, FK references parent PK subset)
CREATE TABLE ici.rfq_line_items (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    rfq_id          UUID            NOT NULL,
    rfq_created_at  TIMESTAMPTZ     NOT NULL,  -- needed to locate correct rfq partition
    material_id     UUID            NOT NULL,
    description     VARCHAR(512)    NOT NULL,
    quantity        NUMERIC(18, 4)  NOT NULL CHECK (quantity > 0),
    unit_of_measure VARCHAR(8)      NOT NULL,
    target_price_eur NUMERIC(18, 4) CHECK (target_price_eur >= 0),
    FOREIGN KEY (rfq_id, rfq_created_at) REFERENCES ici.rfqs (id, created_at) ON DELETE CASCADE
);

-- RFQ Suppliers (invited)
CREATE TABLE ici.rfq_suppliers (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    rfq_id          UUID            NOT NULL,
    rfq_created_at  TIMESTAMPTZ     NOT NULL,
    supplier_id     UUID            NOT NULL,
    invited_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    responded_at    TIMESTAMPTZ,
    FOREIGN KEY (rfq_id, rfq_created_at) REFERENCES ici.rfqs (id, created_at) ON DELETE CASCADE,
    CONSTRAINT uq_rfq_supplier UNIQUE (rfq_id, supplier_id)
);

-- ---------------------------------------------------------------------------
-- 5. QUOTES
--    Partitioned by RANGE on created_at (monthly).
-- ---------------------------------------------------------------------------
CREATE TABLE ici.quotes (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)     NOT NULL,
    rfq_id          UUID            NOT NULL,
    supplier_id     UUID            NOT NULL,
    quote_number    VARCHAR(64)     NOT NULL,
    validity_date   DATE            NOT NULL,
    status          ici.quote_status_t NOT NULL DEFAULT 'RECEIVED',
    currency        CHAR(3)         NOT NULL DEFAULT 'EUR',
    delivery_terms  VARCHAR(16)     NOT NULL DEFAULT 'DAP',
    payment_terms   VARCHAR(8)      NOT NULL DEFAULT 'NET30',
    notes           TEXT            NOT NULL DEFAULT '',
    rejection_reason VARCHAR(1024)  NOT NULL DEFAULT '',

    version         INTEGER         NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(256),
    updated_by      VARCHAR(256),
    is_deleted      BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,

    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE ici.quotes_y2024 PARTITION OF ici.quotes FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ici.quotes_y2025 PARTITION OF ici.quotes FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ici.quotes_y2026 PARTITION OF ici.quotes FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ici.quotes_y2027 PARTITION OF ici.quotes FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TRIGGER trg_quotes_updated_at
    BEFORE UPDATE ON ici.quotes
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

CREATE TABLE ici.quote_line_items (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    quote_id        UUID            NOT NULL,
    quote_created_at TIMESTAMPTZ    NOT NULL,
    material_id     UUID            NOT NULL,
    description     VARCHAR(512)    NOT NULL,
    quantity        NUMERIC(18, 4)  NOT NULL CHECK (quantity > 0),
    unit_of_measure VARCHAR(8)      NOT NULL,
    unit_price_eur  NUMERIC(18, 4)  NOT NULL CHECK (unit_price_eur >= 0),
    total_price_eur NUMERIC(18, 4)
        GENERATED ALWAYS AS (unit_price_eur * quantity) STORED,
    lead_time_days  INTEGER         NOT NULL DEFAULT 0,
    notes           VARCHAR(1024)   NOT NULL DEFAULT '',
    FOREIGN KEY (quote_id, quote_created_at) REFERENCES ici.quotes (id, created_at) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- 6. COST SNAPSHOTS
--    Immutable point-in-time cost records (append-only, never updated).
--    Partitioned by RANGE on snapshot_date (quarterly).
-- ---------------------------------------------------------------------------
CREATE TABLE ici.cost_snapshots (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)     NOT NULL,
    material_id     UUID            NOT NULL,
    snapshot_date   DATE            NOT NULL,

    -- Cost breakdown components (EUR)
    material_cost_eur   NUMERIC(18, 4) NOT NULL DEFAULT 0,
    process_cost_eur    NUMERIC(18, 4) NOT NULL DEFAULT 0,
    overhead_cost_eur   NUMERIC(18, 4) NOT NULL DEFAULT 0,
    logistics_cost_eur  NUMERIC(18, 4) NOT NULL DEFAULT 0,
    tooling_cost_eur    NUMERIC(18, 4) NOT NULL DEFAULT 0,
    total_cost_eur      NUMERIC(18, 4)
        GENERATED ALWAYS AS (
            material_cost_eur + process_cost_eur +
            overhead_cost_eur + logistics_cost_eur + tooling_cost_eur
        ) STORED,

    -- Metadata
    reference_quantity  NUMERIC(18, 4) NOT NULL DEFAULT 1,
    currency            CHAR(3)        NOT NULL DEFAULT 'EUR',
    version             INTEGER        NOT NULL DEFAULT 1,
    source              VARCHAR(64)    NOT NULL DEFAULT 'MANUAL', -- MANUAL | ML_MODEL | IMPORT
    model_version       VARCHAR(32),
    notes               TEXT           NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    created_by          VARCHAR(256),

    PRIMARY KEY (id, snapshot_date)
) PARTITION BY RANGE (snapshot_date);

-- Quarterly partitions
CREATE TABLE ici.cost_snapshots_y2024q1 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2024-01-01') TO ('2024-04-01');
CREATE TABLE ici.cost_snapshots_y2024q2 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2024-04-01') TO ('2024-07-01');
CREATE TABLE ici.cost_snapshots_y2024q3 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2024-07-01') TO ('2024-10-01');
CREATE TABLE ici.cost_snapshots_y2024q4 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2024-10-01') TO ('2025-01-01');
CREATE TABLE ici.cost_snapshots_y2025q1 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2025-01-01') TO ('2025-04-01');
CREATE TABLE ici.cost_snapshots_y2025q2 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2025-04-01') TO ('2025-07-01');
CREATE TABLE ici.cost_snapshots_y2025q3 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2025-07-01') TO ('2025-10-01');
CREATE TABLE ici.cost_snapshots_y2025q4 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2025-10-01') TO ('2026-01-01');
CREATE TABLE ici.cost_snapshots_y2026q1 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2026-01-01') TO ('2026-04-01');
CREATE TABLE ici.cost_snapshots_y2026q2 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2026-04-01') TO ('2026-07-01');
CREATE TABLE ici.cost_snapshots_y2026q3 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2026-07-01') TO ('2026-10-01');
CREATE TABLE ici.cost_snapshots_y2026q4 PARTITION OF ici.cost_snapshots FOR VALUES FROM ('2026-10-01') TO ('2027-01-01');

-- ---------------------------------------------------------------------------
-- 7. FORECASTS
--    Partitioned by created_at (quarterly) — can grow very large.
-- ---------------------------------------------------------------------------
CREATE TABLE ici.forecasts (
    id              UUID                    NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(64)             NOT NULL,
    material_id     UUID                    NOT NULL,
    forecast_type   VARCHAR(8)              NOT NULL DEFAULT 'PRICE', -- PRICE | DEMAND
    horizon         ici.forecast_horizon_t  NOT NULL,
    method          ici.forecast_method_t   NOT NULL,
    status          ici.forecast_status_t   NOT NULL DEFAULT 'PENDING',
    model_version   VARCHAR(32)             NOT NULL DEFAULT '1.0',
    notes           TEXT                    NOT NULL DEFAULT '',

    -- Accuracy metrics (populated after completion)
    mae             NUMERIC(18, 6),
    mape            NUMERIC(10, 6),
    rmse            NUMERIC(18, 6),
    r2              NUMERIC(10, 6),

    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(256),
    is_deleted      BOOLEAN                 NOT NULL DEFAULT FALSE,

    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE ici.forecasts_y2024 PARTITION OF ici.forecasts FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ici.forecasts_y2025 PARTITION OF ici.forecasts FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ici.forecasts_y2026 PARTITION OF ici.forecasts FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ici.forecasts_y2027 PARTITION OF ici.forecasts FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

-- Forecast data points (child, not partitioned — queried via forecast_id)
CREATE TABLE ici.forecast_data_points (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    forecast_id     UUID            NOT NULL,
    forecast_created_at TIMESTAMPTZ NOT NULL,
    period_date     DATE            NOT NULL,
    value           NUMERIC(18, 4)  NOT NULL,
    point_type      VARCHAR(12)     NOT NULL DEFAULT 'PREDICTED', -- HISTORICAL | PREDICTED | ACTUAL
    ci_lower        NUMERIC(18, 4),
    ci_upper        NUMERIC(18, 4),
    ci_level        NUMERIC(4, 2)   CHECK (ci_level BETWEEN 0 AND 1),
    FOREIGN KEY (forecast_id, forecast_created_at) REFERENCES ici.forecasts (id, created_at) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- 8. RISK SCORES
--    Partitioned by created_at (quarterly).
-- ---------------------------------------------------------------------------
CREATE TABLE ici.risk_scores (
    id                  UUID                    NOT NULL DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(64)             NOT NULL,
    category            ici.risk_category_t     NOT NULL,
    title               VARCHAR(256)            NOT NULL,
    description         TEXT                    NOT NULL DEFAULT '',

    -- FMEA components  [0, 1]
    probability         NUMERIC(5, 4)           NOT NULL CHECK (probability BETWEEN 0 AND 1),
    impact              NUMERIC(5, 4)           NOT NULL CHECK (impact BETWEEN 0 AND 1),
    detectability       NUMERIC(5, 4)           NOT NULL CHECK (detectability BETWEEN 0 AND 1),
    -- RPN = probability × impact × (1 − detectability)
    rpn                 NUMERIC(8, 6)
        GENERATED ALWAYS AS (
            probability * impact * (1 - detectability)
        ) STORED,

    -- Financial exposure
    estimated_cost_eur  NUMERIC(18, 2),
    cost_variance_pct   NUMERIC(8, 2),
    impact_area         VARCHAR(16),  -- COST | DELIVERY | QUALITY | COMPLIANCE

    -- References (soft FK — cross-partition safe)
    affected_material_id  UUID,
    affected_supplier_id  UUID,
    affected_rfq_id       UUID,

    status              ici.risk_status_t       NOT NULL DEFAULT 'OPEN',
    notes               TEXT                    NOT NULL DEFAULT '',
    resolved_at         TIMESTAMPTZ,

    version             INTEGER                 NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    created_by          VARCHAR(256),
    updated_by          VARCHAR(256),
    is_deleted          BOOLEAN                 NOT NULL DEFAULT FALSE,
    deleted_at          TIMESTAMPTZ,

    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE ici.risk_scores_y2024 PARTITION OF ici.risk_scores FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ici.risk_scores_y2025 PARTITION OF ici.risk_scores FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE ici.risk_scores_y2026 PARTITION OF ici.risk_scores FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE ici.risk_scores_y2027 PARTITION OF ici.risk_scores FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TRIGGER trg_risk_scores_updated_at
    BEFORE UPDATE ON ici.risk_scores
    FOR EACH ROW EXECUTE FUNCTION ici.set_updated_at();

-- Mitigation actions (child of risk_scores)
CREATE TABLE ici.mitigation_actions (
    id              UUID            NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    risk_id         UUID            NOT NULL,
    risk_created_at TIMESTAMPTZ     NOT NULL,
    description     TEXT            NOT NULL,
    owner           VARCHAR(128)    NOT NULL,
    due_date        TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    FOREIGN KEY (risk_id, risk_created_at) REFERENCES ici.risk_scores (id, created_at) ON DELETE CASCADE
);

-- Grant DML to app role on all tables in ici schema
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ici TO ici_app;
GRANT SELECT                          ON ALL TABLES IN SCHEMA ici TO ici_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ici GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ici_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ici GRANT SELECT ON TABLES TO ici_readonly;
