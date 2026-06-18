-- ============================================================
-- Supplier Intelligence Engine — PostgreSQL 16 Schema
-- Schema: supplier_intelligence
-- Extensions: pgvector, pg_trgm, uuid-ossp
-- ============================================================

CREATE SCHEMA IF NOT EXISTS supplier_intelligence;
SET search_path TO supplier_intelligence, public;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- ENUMS
-- ============================================================

CREATE TYPE supplier_type AS ENUM (
    'MANUFACTURER', 'WHOLESALER', 'DISTRIBUTOR',
    'SUBCONTRACTOR', 'TRADER', 'AGENT', 'LESSOR'
);

CREATE TYPE supplier_status AS ENUM (
    'PENDING', 'UNDER_REVIEW', 'APPROVED',
    'CONDITIONAL', 'SUSPENDED', 'DEACTIVATED', 'BLACKLISTED'
);

CREATE TYPE strategic_tier AS ENUM (
    'TIER1', 'TIER2', 'TIER3', 'SPOT'
);

CREATE TYPE rating_class AS ENUM ('A', 'B', 'C', 'D', 'E', 'F');

CREATE TYPE contact_role AS ENUM (
    'SALES', 'QUALITY', 'LOGISTICS', 'FINANCE', 'TECHNICAL', 'EXECUTIVE'
);

CREATE TYPE certification_type AS ENUM (
    'ISO9001', 'ISO14001', 'ISO45001', 'ISO50001',
    'IATF16949', 'AS9100', 'EN1090', 'EN15085', 'EN3834',
    'NADCAP', 'VDA65', 'PPAP',
    'FSC', 'PEFC',
    'REACH', 'ROHS', 'CONFLICT_MINERALS',
    'CE_MARKING', 'UL', 'CSA',
    'ISO13485', 'MDSAP',
    'CUSTOM'
);

CREATE TYPE ncr_status AS ENUM (
    'OPEN', 'D1_TEAM', 'D2_PROBLEM_DESC', 'D3_CONTAINMENT',
    'D4_ROOT_CAUSE', 'D5_CORRECTIVE_ACTION', 'D6_IMPLEMENTATION',
    'D7_PREVENT_RECURRENCE', 'D8_CLOSED', 'CANCELLED'
);

CREATE TYPE delay_category AS ENUM (
    'ON_TIME', 'EARLY', 'LATE_1_3D', 'LATE_4_7D',
    'LATE_8_14D', 'LATE_15_30D', 'LATE_GT_30D'
);

CREATE TYPE risk_severity AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');

CREATE TYPE risk_category AS ENUM (
    'FINANCIAL', 'GEOPOLITICAL', 'SUPPLY_CHAIN', 'OPERATIONAL', 'COMPLIANCE'
);

CREATE TYPE price_source_type AS ENUM (
    'RFQ', 'FRAMEWORK_AGREEMENT', 'SPOT_PURCHASE',
    'CATALOGUE', 'ESTIMATED', 'MARKET_BENCHMARK'
);

CREATE TYPE alert_status AS ENUM ('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED');

CREATE TYPE financial_zone AS ENUM ('SAFE', 'GREY', 'DISTRESS', 'UNKNOWN');

-- ============================================================
-- CORE SUPPLIER TABLES
-- ============================================================

CREATE TABLE suppliers (
    supplier_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Identifiers
    erp_vendor_id       VARCHAR(50) UNIQUE,
    duns_number         VARCHAR(9),
    vat_number          VARCHAR(20),
    registration_number VARCHAR(50),
    -- Basic info
    legal_name          VARCHAR(255) NOT NULL,
    trade_name          VARCHAR(255),
    supplier_type       supplier_type NOT NULL,
    status              supplier_status NOT NULL DEFAULT 'PENDING',
    strategic_tier      strategic_tier NOT NULL DEFAULT 'SPOT',
    -- Location
    country_code        CHAR(2) NOT NULL,
    region_code         VARCHAR(10),
    -- Classification
    primary_category_id UUID,
    website             VARCHAR(500),
    -- Scoring cache
    composite_score     NUMERIC(5,2),
    rating_class        rating_class,
    last_scored_at      TIMESTAMPTZ,
    -- Relationship
    account_manager_id  UUID,
    onboarded_at        TIMESTAMPTZ,
    last_activity_at    TIMESTAMPTZ,
    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    version             INTEGER NOT NULL DEFAULT 1,
    -- Full-text search
    search_vector       TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(legal_name, '') || ' ' ||
            coalesce(trade_name, '') || ' ' ||
            coalesce(vat_number, '') || ' ' ||
            coalesce(duns_number, '') || ' ' ||
            coalesce(erp_vendor_id, '')
        )
    ) STORED
);

CREATE INDEX idx_suppliers_status     ON suppliers(status);
CREATE INDEX idx_suppliers_tier       ON suppliers(strategic_tier);
CREATE INDEX idx_suppliers_country    ON suppliers(country_code);
CREATE INDEX idx_suppliers_rating     ON suppliers(rating_class);
CREATE INDEX idx_suppliers_score      ON suppliers(composite_score DESC NULLS LAST);
CREATE INDEX idx_suppliers_fts        ON suppliers USING GIN(search_vector);
CREATE INDEX idx_suppliers_name_trgm  ON suppliers USING GIN(legal_name gin_trgm_ops);
CREATE INDEX idx_suppliers_erp        ON suppliers(erp_vendor_id) WHERE erp_vendor_id IS NOT NULL;

-- ----

CREATE TABLE supplier_addresses (
    address_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id     UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    address_type    VARCHAR(20) NOT NULL DEFAULT 'MAIN',    -- MAIN / INVOICE / WAREHOUSE / PRODUCTION
    street          VARCHAR(300),
    city            VARCHAR(100),
    postal_code     VARCHAR(20),
    country_code    CHAR(2) NOT NULL,
    nuts_code       VARCHAR(10),
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_supplier_addresses_supplier ON supplier_addresses(supplier_id);

-- ----

CREATE TABLE supplier_contacts (
    contact_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id     UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    role            contact_role NOT NULL,
    first_name      VARCHAR(100),
    last_name       VARCHAR(100),
    email           VARCHAR(255),
    phone           VARCHAR(50),
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_supplier_contacts_supplier ON supplier_contacts(supplier_id);
CREATE INDEX idx_supplier_contacts_email    ON supplier_contacts(email) WHERE email IS NOT NULL;

-- ----

CREATE TABLE supplier_certifications (
    certification_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    cert_type           certification_type NOT NULL,
    cert_number         VARCHAR(100),
    issuing_body        VARCHAR(255),
    scope               TEXT,
    valid_from          DATE NOT NULL,
    valid_until         DATE,
    document_url        VARCHAR(500),
    is_verified         BOOLEAN NOT NULL DEFAULT FALSE,
    verified_by         UUID,
    verified_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, cert_type, cert_number)
);

CREATE INDEX idx_certifications_supplier     ON supplier_certifications(supplier_id);
CREATE INDEX idx_certifications_valid_until  ON supplier_certifications(valid_until)
    WHERE valid_until IS NOT NULL;

-- ----

CREATE TABLE supplier_capabilities (
    capability_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    process_class       VARCHAR(20),            -- CUT, MAC, FOR, JOI, ASS, FIN
    process_type        VARCHAR(50),
    material_class      VARCHAR(20),
    max_part_size_mm    JSONB,                  -- {"x": 3000, "y": 1500, "z": 200}
    min_tolerance_mm    NUMERIC(8,4),
    annual_capacity_hrs INTEGER,
    equipment_list      TEXT[],
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_capabilities_supplier      ON supplier_capabilities(supplier_id);
CREATE INDEX idx_capabilities_process       ON supplier_capabilities(process_class, process_type);
CREATE INDEX idx_capabilities_material      ON supplier_capabilities(material_class);

-- ============================================================
-- CATEGORY MAPPING
-- ============================================================

CREATE TABLE supplier_categories (
    mapping_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    category_id         UUID NOT NULL,              -- ref: MIE / procurement category tree
    category_code       VARCHAR(50),
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    approved_at         TIMESTAMPTZ,
    approved_by         UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, category_id)
);

CREATE INDEX idx_supplier_categories_category ON supplier_categories(category_id);

-- ============================================================
-- SCORECARDS
-- ============================================================

CREATE TABLE supplier_scorecards (
    scorecard_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    category_id         UUID,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    -- Component scores (0-100)
    quality_score       NUMERIC(5,2) NOT NULL,
    delivery_score      NUMERIC(5,2) NOT NULL,
    price_score         NUMERIC(5,2) NOT NULL,
    service_score       NUMERIC(5,2) NOT NULL,
    risk_score          NUMERIC(5,2) NOT NULL,
    composite_score     NUMERIC(5,2) NOT NULL,
    rating_class        rating_class NOT NULL,
    -- Detail JSONB
    quality_detail      JSONB,
    delivery_detail     JSONB,
    price_detail        JSONB,
    service_detail      JSONB,
    risk_detail         JSONB,
    -- Trend
    prior_composite     NUMERIC(5,2),
    delta_vs_prior      NUMERIC(5,2),
    trend_direction     VARCHAR(20),            -- IMPROVING / STABLE / DECLINING / CRITICAL_DECLINE
    -- Meta
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    calculated_by       VARCHAR(100) DEFAULT 'SYSTEM',
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scorecards_supplier         ON supplier_scorecards(supplier_id);
CREATE INDEX idx_scorecards_supplier_period  ON supplier_scorecards(supplier_id, period_end DESC);
CREATE INDEX idx_scorecards_category         ON supplier_scorecards(category_id) WHERE category_id IS NOT NULL;
CREATE INDEX idx_scorecards_rating           ON supplier_scorecards(rating_class);

-- ============================================================
-- QUALITY RECORDS
-- ============================================================

CREATE TABLE quality_records (
    record_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    period_year         SMALLINT NOT NULL,
    period_quarter      SMALLINT NOT NULL CHECK (period_quarter BETWEEN 1 AND 4),
    parts_delivered     BIGINT NOT NULL DEFAULT 0,
    parts_defective     BIGINT NOT NULL DEFAULT 0,
    ppm                 NUMERIC(10,2) GENERATED ALWAYS AS (
        CASE WHEN parts_delivered > 0
             THEN (parts_defective::NUMERIC / parts_delivered) * 1000000
             ELSE 0 END
    ) STORED,
    ncr_count           INTEGER NOT NULL DEFAULT 0,
    ncr_critical_count  INTEGER NOT NULL DEFAULT 0,
    open_8d_overdue     INTEGER NOT NULL DEFAULT 0,
    quality_score       NUMERIC(5,2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, period_year, period_quarter)
);

CREATE INDEX idx_quality_records_supplier ON quality_records(supplier_id, period_year DESC, period_quarter DESC);

-- ----

CREATE TABLE ncr_records (
    ncr_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    purchase_order_id   UUID,
    material_id         UUID,
    ncr_number          VARCHAR(50) UNIQUE NOT NULL,
    title               VARCHAR(500) NOT NULL,
    description         TEXT,
    defect_type         VARCHAR(100),
    severity            VARCHAR(20) NOT NULL DEFAULT 'MINOR', -- MINOR / MAJOR / CRITICAL
    quantity_affected   INTEGER,
    financial_impact_eur NUMERIC(12,2),
    status              ncr_status NOT NULL DEFAULT 'OPEN',
    -- 8D tracking
    d1_team_defined_at  TIMESTAMPTZ,
    d3_containment_at   TIMESTAMPTZ,
    d4_root_cause_at    TIMESTAMPTZ,
    d8_closed_at        TIMESTAMPTZ,
    -- Dates
    raised_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    target_closure_date DATE,
    -- Responsibility
    raised_by           UUID,
    owner_id            UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ncr_supplier         ON ncr_records(supplier_id);
CREATE INDEX idx_ncr_status           ON ncr_records(status) WHERE status != 'D8_CLOSED';
CREATE INDEX idx_ncr_raised_at        ON ncr_records(raised_at DESC);

-- ============================================================
-- DELIVERY PERFORMANCE
-- ============================================================

CREATE TABLE delivery_records (
    delivery_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    purchase_order_id   UUID NOT NULL,
    po_line_id          UUID,
    material_id         UUID,
    -- Dates
    po_date             DATE NOT NULL,
    confirmed_delivery  DATE NOT NULL,
    requested_delivery  DATE NOT NULL,
    actual_delivery     DATE,
    -- Quantities
    ordered_qty         NUMERIC(14,4) NOT NULL,
    delivered_qty       NUMERIC(14,4),
    accepted_qty        NUMERIC(14,4),
    -- Computed fields
    delay_days          INTEGER GENERATED ALWAYS AS (
        CASE WHEN actual_delivery IS NOT NULL
             THEN actual_delivery - confirmed_delivery
             ELSE NULL END
    ) STORED,
    delay_category      delay_category,
    is_on_time          BOOLEAN GENERATED ALWAYS AS (
        CASE WHEN actual_delivery IS NOT NULL
             THEN actual_delivery <= confirmed_delivery
             ELSE NULL END
    ) STORED,
    is_in_full          BOOLEAN GENERATED ALWAYS AS (
        CASE WHEN delivered_qty IS NOT NULL AND ordered_qty > 0
             THEN delivered_qty >= ordered_qty * 0.995
             ELSE NULL END
    ) STORED,
    -- Value
    line_value_eur      NUMERIC(14,2),
    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_delivery_supplier      ON delivery_records(supplier_id);
CREATE INDEX idx_delivery_po_date       ON delivery_records(po_date DESC);
CREATE INDEX idx_delivery_on_time       ON delivery_records(supplier_id, is_on_time) WHERE actual_delivery IS NOT NULL;

-- ============================================================
-- LEAD TIME & MOQ
-- ============================================================

CREATE TABLE lead_time_records (
    lt_record_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    material_id         UUID,
    category_id         UUID,
    standard_lt_days    SMALLINT NOT NULL,
    min_lt_days         SMALLINT,
    max_lt_days         SMALLINT,
    expedite_lt_days    SMALLINT,
    valid_from          DATE NOT NULL,
    valid_until         DATE,
    incoterms           VARCHAR(10),
    notes               TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_lt_records_supplier ON lead_time_records(supplier_id);
CREATE INDEX idx_lt_records_material ON lead_time_records(material_id) WHERE material_id IS NOT NULL;

-- ----

CREATE TABLE moq_records (
    moq_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    material_id         UUID,
    category_id         UUID,
    moq_qty             NUMERIC(14,4) NOT NULL,
    moq_unit            VARCHAR(10) NOT NULL DEFAULT 'PCS',
    moq_value_eur       NUMERIC(12,2),
    small_order_surcharge_pct NUMERIC(5,2) DEFAULT 0,
    standard_pack_qty   NUMERIC(14,4),
    valid_from          DATE NOT NULL,
    valid_until         DATE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_moq_supplier ON moq_records(supplier_id);
CREATE INDEX idx_moq_material ON moq_records(material_id) WHERE material_id IS NOT NULL;

-- ============================================================
-- PRICING
-- ============================================================

CREATE TABLE price_offers (
    offer_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    material_id         UUID NOT NULL,
    category_id         UUID,
    -- Offer details
    price_per_unit      NUMERIC(16,6) NOT NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    price_unit          VARCHAR(10) NOT NULL DEFAULT 'PCS',   -- PCS / KG / M / M2 / M3
    moq_qty             NUMERIC(14,4),
    -- Validity
    valid_from          DATE NOT NULL,
    valid_until         DATE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    -- Context
    source              price_source_type NOT NULL,
    rfq_id              UUID,
    purchase_order_id   UUID,
    tooling_cost_eur    NUMERIC(12,2) DEFAULT 0,
    index_reference     VARCHAR(100),           -- e.g. "LME_CU_SPOT"
    index_surcharge_pct NUMERIC(5,2) DEFAULT 0,
    -- Competitiveness
    market_benchmark_eur NUMERIC(16,6),
    vs_benchmark_pct    NUMERIC(5,2),
    -- Meta
    notes               TEXT,
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_price_offers_supplier   ON price_offers(supplier_id);
CREATE INDEX idx_price_offers_material   ON price_offers(material_id);
CREATE INDEX idx_price_offers_active     ON price_offers(material_id, supplier_id)
    WHERE is_active = TRUE;
CREATE INDEX idx_price_offers_valid      ON price_offers(valid_until)
    WHERE valid_until IS NOT NULL AND is_active = TRUE;

-- ============================================================
-- RISK MANAGEMENT
-- ============================================================

CREATE TABLE risk_profiles (
    profile_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    overall_risk_score  NUMERIC(5,2) NOT NULL,
    risk_class          VARCHAR(10) NOT NULL,   -- LOW / MEDIUM / HIGH / CRITICAL
    financial_score     NUMERIC(5,2),
    geopolitical_score  NUMERIC(5,2),
    supply_chain_score  NUMERIC(5,2),
    operational_score   NUMERIC(5,2),
    compliance_score    NUMERIC(5,2),
    factor_detail       JSONB,
    last_assessed       DATE NOT NULL,
    next_review_date    DATE,
    assessed_by         VARCHAR(100) DEFAULT 'SYSTEM',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_risk_profiles_supplier ON risk_profiles(supplier_id);

-- ----

CREATE TABLE risk_alerts (
    alert_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    risk_category       risk_category NOT NULL,
    alert_type          VARCHAR(100) NOT NULL,
    severity            risk_severity NOT NULL,
    title               VARCHAR(500) NOT NULL,
    description         TEXT,
    evidence_url        VARCHAR(500),
    recommended_action  TEXT,
    status              alert_status NOT NULL DEFAULT 'OPEN',
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     UUID,
    resolved_at         TIMESTAMPTZ,
    resolved_by         UUID,
    resolution_notes    TEXT
);

CREATE INDEX idx_risk_alerts_supplier ON risk_alerts(supplier_id);
CREATE INDEX idx_risk_alerts_open     ON risk_alerts(supplier_id, severity)
    WHERE status NOT IN ('RESOLVED', 'DISMISSED');
CREATE INDEX idx_risk_alerts_triggered ON risk_alerts(triggered_at DESC);

-- ============================================================
-- FINANCIAL INTELLIGENCE
-- ============================================================

CREATE TABLE financial_profiles (
    fp_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,
    -- Balance sheet
    total_assets_eur    NUMERIC(16,2),
    total_liabilities_eur NUMERIC(16,2),
    equity_eur          NUMERIC(16,2),
    working_capital_eur NUMERIC(16,2),
    retained_earnings_eur NUMERIC(16,2),
    -- P&L
    revenue_eur         NUMERIC(16,2),
    ebit_eur            NUMERIC(16,2),
    net_income_eur      NUMERIC(16,2),
    -- Ratios
    current_ratio       NUMERIC(6,3),
    quick_ratio         NUMERIC(6,3),
    debt_to_equity      NUMERIC(6,3),
    interest_coverage   NUMERIC(6,3),
    -- Altman
    altman_z_score      NUMERIC(6,3),
    altman_zone         financial_zone NOT NULL DEFAULT 'UNKNOWN',
    -- D&B
    duns_paydex         SMALLINT,
    duns_delinquency    SMALLINT,
    duns_failure_score  SMALLINT,
    duns_credit_limit_eur NUMERIC(12,2),
    duns_days_beyond_terms NUMERIC(5,1),
    -- Computed risk score
    financial_risk_score NUMERIC(5,2),
    -- Meta
    data_source         VARCHAR(100),
    retrieved_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, fiscal_year)
);

CREATE INDEX idx_financial_profiles_supplier ON financial_profiles(supplier_id, fiscal_year DESC);

-- ============================================================
-- EMBEDDINGS (AI)
-- ============================================================

CREATE TABLE supplier_embeddings (
    embedding_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    embedding_vector    VECTOR(1536) NOT NULL,
    model_name          VARCHAR(100) NOT NULL DEFAULT 'text-embedding-3-small',
    embedding_text      TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_embeddings_supplier ON supplier_embeddings(supplier_id);
CREATE INDEX idx_embeddings_hnsw ON supplier_embeddings
    USING hnsw (embedding_vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ============================================================
-- RELATIONSHIPS
-- ============================================================

CREATE TABLE supplier_relationships (
    relationship_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id         UUID NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    -- Relationship type
    is_preferred        BOOLEAN NOT NULL DEFAULT FALSE,
    is_single_source    BOOLEAN NOT NULL DEFAULT FALSE,
    -- Contract
    contract_id         VARCHAR(100),
    contract_start      DATE,
    contract_end        DATE,
    annual_spend_eur    NUMERIC(14,2),
    payment_terms_days  SMALLINT DEFAULT 30,
    incoterms           VARCHAR(10),
    -- Approval
    approved_by         UUID,
    approval_date       DATE,
    next_review_date    DATE,
    -- NDA
    nda_signed          BOOLEAN NOT NULL DEFAULT FALSE,
    nda_valid_until     DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_relationships_supplier ON supplier_relationships(supplier_id);

-- ============================================================
-- AUDIT LOG (PARTITIONED)
-- ============================================================

CREATE TABLE supplier_audit_log (
    log_id          UUID NOT NULL DEFAULT uuid_generate_v4(),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    supplier_id     UUID,
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID,
    action          VARCHAR(20) NOT NULL,   -- INSERT / UPDATE / DELETE / STATUS_CHANGE
    changed_by      UUID,
    changed_by_role VARCHAR(50),
    old_values      JSONB,
    new_values      JSONB,
    correlation_id  UUID
) PARTITION BY RANGE (occurred_at);

CREATE TABLE supplier_audit_log_2024 PARTITION OF supplier_audit_log
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE supplier_audit_log_2025 PARTITION OF supplier_audit_log
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE supplier_audit_log_2026 PARTITION OF supplier_audit_log
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE INDEX idx_audit_supplier    ON supplier_audit_log(supplier_id, occurred_at DESC);
CREATE INDEX idx_audit_entity      ON supplier_audit_log(entity_type, entity_id, occurred_at DESC);
CREATE INDEX idx_audit_occurred_at ON supplier_audit_log(occurred_at DESC);

-- ============================================================
-- FUNCTIONS
-- ============================================================

-- Recalculate supplier scorecard composite score
CREATE OR REPLACE FUNCTION sie.recalculate_supplier_score(p_supplier_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_scorecard supplier_scorecards%ROWTYPE;
BEGIN
    SELECT * INTO v_scorecard
    FROM supplier_scorecards
    WHERE supplier_id = p_supplier_id AND is_active = TRUE
    ORDER BY period_end DESC
    LIMIT 1;

    IF FOUND THEN
        UPDATE suppliers
        SET composite_score  = v_scorecard.composite_score,
            rating_class     = v_scorecard.rating_class,
            last_scored_at   = now(),
            updated_at       = now()
        WHERE supplier_id = p_supplier_id;
    END IF;
END;
$$;

-- Get current active price for a material/supplier pair
CREATE OR REPLACE FUNCTION sie.get_active_price(
    p_supplier_id UUID,
    p_material_id UUID,
    p_date        DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    offer_id        UUID,
    price_per_unit  NUMERIC,
    currency        CHAR(3),
    price_unit      VARCHAR,
    valid_until     DATE
)
LANGUAGE sql STABLE
AS $$
    SELECT offer_id, price_per_unit, currency, price_unit, valid_until
    FROM supplier_intelligence.price_offers
    WHERE supplier_id = p_supplier_id
      AND material_id = p_material_id
      AND is_active = TRUE
      AND valid_from <= p_date
      AND (valid_until IS NULL OR valid_until >= p_date)
    ORDER BY valid_from DESC
    LIMIT 1;
$$;

-- Calculate OTD for a supplier in a given date range
CREATE OR REPLACE FUNCTION sie.calculate_otd(
    p_supplier_id UUID,
    p_from        DATE,
    p_to          DATE
)
RETURNS NUMERIC
LANGUAGE sql STABLE
AS $$
    SELECT CASE
        WHEN COUNT(*) = 0 THEN NULL
        ELSE ROUND(SUM(CASE WHEN is_on_time THEN 1.0 ELSE 0.0 END) / COUNT(*) * 100, 2)
    END
    FROM supplier_intelligence.delivery_records
    WHERE supplier_id    = p_supplier_id
      AND actual_delivery BETWEEN p_from AND p_to;
$$;

-- Semantic supplier search via pgvector cosine similarity
CREATE OR REPLACE FUNCTION sie.search_suppliers_semantic(
    p_query_vector  VECTOR(1536),
    p_limit         INTEGER DEFAULT 10,
    p_min_score     NUMERIC DEFAULT 0.70,
    p_status_filter supplier_status[] DEFAULT ARRAY['APPROVED'::supplier_status]
)
RETURNS TABLE (
    supplier_id     UUID,
    legal_name      VARCHAR,
    country_code    CHAR(2),
    composite_score NUMERIC,
    rating_class    rating_class,
    similarity      FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT s.supplier_id, s.legal_name, s.country_code,
           s.composite_score, s.rating_class,
           1 - (se.embedding_vector <=> p_query_vector) AS similarity
    FROM supplier_intelligence.supplier_embeddings se
    JOIN supplier_intelligence.suppliers s ON se.supplier_id = s.supplier_id
    WHERE s.status = ANY(p_status_filter)
      AND 1 - (se.embedding_vector <=> p_query_vector) >= p_min_score
    ORDER BY se.embedding_vector <=> p_query_vector
    LIMIT p_limit;
$$;

-- ============================================================
-- TRIGGERS
-- ============================================================

CREATE OR REPLACE FUNCTION sie.supplier_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    NEW.version    = OLD.version + 1;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_suppliers_updated_at
    BEFORE UPDATE ON suppliers
    FOR EACH ROW EXECUTE FUNCTION sie.supplier_updated_at();

CREATE TRIGGER trg_scorecards_sync
    AFTER INSERT ON supplier_scorecards
    FOR EACH ROW
    WHEN (NEW.is_active = TRUE)
    EXECUTE FUNCTION sie.sync_scorecard_to_supplier();

CREATE OR REPLACE FUNCTION sie.sync_scorecard_to_supplier()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE supplier_intelligence.suppliers
    SET composite_score = NEW.composite_score,
        rating_class    = NEW.rating_class,
        last_scored_at  = now(),
        updated_at      = now()
    WHERE supplier_id = NEW.supplier_id;
    RETURN NULL;
END;
$$;

-- ============================================================
-- VIEWS
-- ============================================================

CREATE VIEW v_supplier_overview AS
SELECT
    s.supplier_id,
    s.legal_name,
    s.trade_name,
    s.supplier_type,
    s.status,
    s.strategic_tier,
    s.country_code,
    s.composite_score,
    s.rating_class,
    s.last_scored_at,
    -- Latest quality
    qr.ppm,
    qr.ncr_count,
    -- Latest delivery
    sie.calculate_otd(s.supplier_id, CURRENT_DATE - 365, CURRENT_DATE) AS otd_12m,
    -- Risk
    rp.risk_class,
    rp.overall_risk_score,
    -- Alerts
    (SELECT COUNT(*) FROM risk_alerts ra
     WHERE ra.supplier_id = s.supplier_id
       AND ra.status NOT IN ('RESOLVED','DISMISSED')
       AND ra.severity IN ('HIGH','CRITICAL')) AS critical_alerts,
    -- Active certs
    (SELECT COUNT(*) FROM supplier_certifications sc
     WHERE sc.supplier_id = s.supplier_id
       AND (sc.valid_until IS NULL OR sc.valid_until >= CURRENT_DATE)
       AND sc.is_verified = TRUE) AS active_cert_count
FROM suppliers s
LEFT JOIN LATERAL (
    SELECT ppm, ncr_count FROM quality_records
    WHERE supplier_id = s.supplier_id
    ORDER BY period_year DESC, period_quarter DESC LIMIT 1
) qr ON TRUE
LEFT JOIN risk_profiles rp ON rp.supplier_id = s.supplier_id;

-- ----

CREATE VIEW v_expiring_certifications AS
SELECT
    s.supplier_id,
    s.legal_name,
    s.strategic_tier,
    sc.cert_type,
    sc.cert_number,
    sc.valid_until,
    sc.valid_until - CURRENT_DATE AS days_until_expiry
FROM supplier_certifications sc
JOIN suppliers s ON sc.supplier_id = s.supplier_id
WHERE sc.valid_until IS NOT NULL
  AND sc.valid_until BETWEEN CURRENT_DATE AND CURRENT_DATE + 90
  AND s.status = 'APPROVED'
ORDER BY sc.valid_until;

-- ----

CREATE VIEW v_supplier_ranking AS
WITH latest_sc AS (
    SELECT DISTINCT ON (supplier_id, category_id)
        supplier_id, category_id, composite_score, rating_class, period_end
    FROM supplier_scorecards
    WHERE is_active = TRUE
    ORDER BY supplier_id, category_id, period_end DESC
)
SELECT
    ls.supplier_id,
    s.legal_name,
    s.country_code,
    s.strategic_tier,
    ls.category_id,
    ls.composite_score,
    ls.rating_class,
    RANK() OVER (PARTITION BY ls.category_id ORDER BY ls.composite_score DESC) AS rank_in_category,
    ROUND(PERCENT_RANK() OVER (PARTITION BY ls.category_id ORDER BY ls.composite_score) * 100, 1) AS percentile
FROM latest_sc ls
JOIN suppliers s ON ls.supplier_id = s.supplier_id
WHERE s.status = 'APPROVED';

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE suppliers               ENABLE ROW LEVEL SECURITY;
ALTER TABLE supplier_scorecards     ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_offers            ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_profiles      ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_alerts             ENABLE ROW LEVEL SECURITY;

-- SUPPLIER_VIEWER: can read approved suppliers
CREATE POLICY supplier_viewer_read ON suppliers
    FOR SELECT
    USING (
        status IN ('APPROVED','CONDITIONAL') OR
        current_setting('app.user_role', TRUE) IN ('SUPPLIER_MANAGER','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR')
    );

-- PRICE_MANAGER: can see all prices (otherwise only own category)
CREATE POLICY price_read_policy ON price_offers
    FOR SELECT
    USING (
        current_setting('app.user_role', TRUE) IN ('PRICE_MANAGER','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR') OR
        category_id::TEXT = current_setting('app.category_filter', TRUE)
    );

-- Financial profiles — restricted
CREATE POLICY financial_read_policy ON financial_profiles
    FOR SELECT
    USING (
        current_setting('app.user_role', TRUE) IN ('VENDOR_RISK_ANALYST','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR')
    );

-- ============================================================
-- INDEXES (additional)
-- ============================================================

CREATE INDEX idx_delivery_records_otd_calc ON delivery_records(supplier_id, actual_delivery)
    WHERE actual_delivery IS NOT NULL;

CREATE INDEX idx_ncr_records_open ON ncr_records(supplier_id, raised_at DESC)
    WHERE status NOT IN ('D8_CLOSED', 'CANCELLED');
