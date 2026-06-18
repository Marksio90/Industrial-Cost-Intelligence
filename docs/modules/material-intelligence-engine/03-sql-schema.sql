-- =============================================================================
-- MATERIAL INTELLIGENCE ENGINE — SQL SCHEMA
-- Database: PostgreSQL 16+ with pgvector extension
-- Schema: material_intelligence
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS material_intelligence;
SET search_path TO material_intelligence, public;

-- Enable pgvector for AI embeddings
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm; -- for trigram text search

-- =============================================================================
-- ENUMS
-- =============================================================================

CREATE TYPE material_class_enum AS ENUM (
    'METAL', 'POLYMER', 'WOOD', 'PACKAGING', 'COMPOSITE', 'SPECIAL'
);

CREATE TYPE material_status_enum AS ENUM (
    'ACTIVE', 'PHASED_OUT', 'REPLACED', 'DRAFT', 'DISCONTINUED'
);

CREATE TYPE form_factor_enum AS ENUM (
    'SHEET', 'COIL', 'BAR', 'TUBE', 'PROFILE', 'GRANULE',
    'LIQUID', 'ROLL', 'BLOCK', 'PLATE', 'WIRE', 'FOAM', 'FILM'
);

CREATE TYPE standard_system_enum AS ENUM (
    'ISO', 'DIN', 'EN', 'ASTM', 'BS', 'JIS', 'GB', 'PN', 'GOST', 'AISI', 'SAE'
);

CREATE TYPE equivalence_type_enum AS ENUM (
    'IDENTICAL', 'EQUIVALENT', 'SIMILAR', 'REPLACED_BY'
);

CREATE TYPE price_type_enum AS ENUM (
    'MARKET_INDEX', 'PURCHASE', 'QUOTED', 'FORECAST', 'SPOT', 'CONTRACT'
);

CREATE TYPE price_source_type_enum AS ENUM (
    'EXCHANGE', 'INDEX', 'SUPPLIER', 'INTERNAL', 'MANUAL'
);

CREATE TYPE update_frequency_enum AS ENUM (
    'REALTIME', 'DAILY', 'WEEKLY', 'MONTHLY', 'MANUAL'
);

CREATE TYPE price_unit_enum AS ENUM (
    'PER_KG', 'PER_TON', 'PER_M2', 'PER_M3', 'PER_PIECE', 'PER_M', 'PER_LITER'
);

CREATE TYPE unit_of_measure_enum AS ENUM (
    'KG', 'TON', 'M', 'M2', 'M3', 'PIECE', 'LITER', 'ROLL'
);

CREATE TYPE substitution_type_enum AS ENUM (
    'DIRECT', 'CONDITIONAL', 'PARTIAL', 'PROCESS_CHANGE_NEEDED'
);

CREATE TYPE process_category_enum AS ENUM (
    'CUTTING', 'FORMING', 'WELDING', 'MACHINING', 'COATING',
    'MOLDING', 'ASSEMBLY', 'FINISHING', 'PRINTING', 'JOINING'
);

CREATE TYPE compatibility_level_enum AS ENUM (
    'OPTIMAL', 'ACCEPTABLE', 'POSSIBLE_WITH_CAUTION', 'NOT_RECOMMENDED', 'FORBIDDEN'
);

CREATE TYPE supply_risk_enum AS ENUM (
    'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
);

CREATE TYPE audit_operation_enum AS ENUM (
    'CREATE', 'UPDATE', 'DELETE', 'RESTORE', 'IMPORT', 'EXPORT'
);

-- =============================================================================
-- TABLE: material_categories (Taxonomy)
-- =============================================================================

CREATE TABLE material_categories (
    category_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parent_id            UUID REFERENCES material_categories(category_id),
    category_code        VARCHAR(30) NOT NULL,
    category_name        VARCHAR(100) NOT NULL,
    category_name_en     VARCHAR(100),
    hierarchy_level      SMALLINT NOT NULL CHECK (hierarchy_level BETWEEN 1 AND 5),
    materialized_path    VARCHAR(500) NOT NULL,
    sort_order           SMALLINT DEFAULT 0,
    is_leaf              BOOLEAN NOT NULL DEFAULT FALSE,
    icon_code            VARCHAR(50),
    color_hex            CHAR(7),
    description          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_category_code UNIQUE (category_code),
    CONSTRAINT uq_parent_sort UNIQUE (parent_id, sort_order)
);

CREATE INDEX idx_material_categories_parent ON material_categories(parent_id);
CREATE INDEX idx_material_categories_path ON material_categories USING GIST (materialized_path gist_trgm_ops);
CREATE INDEX idx_material_categories_level ON material_categories(hierarchy_level);

-- =============================================================================
-- TABLE: materials (Core entity)
-- =============================================================================

CREATE TABLE materials (
    material_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_code            VARCHAR(50) NOT NULL,
    material_name            VARCHAR(200) NOT NULL,
    material_name_short      VARCHAR(60),
    category_id              UUID NOT NULL REFERENCES material_categories(category_id),
    material_class           material_class_enum NOT NULL,
    material_subclass        VARCHAR(100),
    grade                    VARCHAR(100),
    form_factor              form_factor_enum,
    status                   material_status_enum NOT NULL DEFAULT 'DRAFT',
    replaced_by_id           UUID REFERENCES materials(material_id),
    description              TEXT,
    internal_notes           TEXT,
    is_hazardous             BOOLEAN NOT NULL DEFAULT FALSE,
    regulatory_flags         JSONB DEFAULT '{}',
    erp_material_number      VARCHAR(50),
    customs_tariff_code      VARCHAR(20),
    search_vector            TSVECTOR,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by               UUID,
    version                  INTEGER NOT NULL DEFAULT 1,

    CONSTRAINT uq_material_code UNIQUE (material_code),
    CONSTRAINT chk_replaced_by CHECK (
        replaced_by_id IS NULL OR status = 'REPLACED'
    )
);

CREATE INDEX idx_materials_category ON materials(category_id);
CREATE INDEX idx_materials_class ON materials(material_class);
CREATE INDEX idx_materials_status ON materials(status) WHERE status = 'ACTIVE';
CREATE INDEX idx_materials_grade ON materials(grade);
CREATE INDEX idx_materials_erp ON materials(erp_material_number) WHERE erp_material_number IS NOT NULL;
CREATE INDEX idx_materials_search_vector ON materials USING GIN(search_vector);
CREATE INDEX idx_materials_code_trgm ON materials USING GIN(material_code gin_trgm_ops);
CREATE INDEX idx_materials_name_trgm ON materials USING GIN(material_name gin_trgm_ops);

-- Trigger to update search_vector
CREATE OR REPLACE FUNCTION materials_search_vector_update() RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', COALESCE(NEW.material_code, '')), 'A') ||
        setweight(to_tsvector('simple', COALESCE(NEW.material_name, '')), 'A') ||
        setweight(to_tsvector('simple', COALESCE(NEW.grade, '')), 'B') ||
        setweight(to_tsvector('simple', COALESCE(NEW.material_subclass, '')), 'C') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'D');
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_materials_search_vector
    BEFORE INSERT OR UPDATE ON materials
    FOR EACH ROW EXECUTE FUNCTION materials_search_vector_update();

-- =============================================================================
-- TABLE: material_properties (Physical properties)
-- =============================================================================

CREATE TABLE material_properties (
    property_id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id                  UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    density_kg_m3                NUMERIC(10,4),
    density_tolerance_pct        NUMERIC(5,2) DEFAULT 0.5,
    density_source               VARCHAR(100),
    thermal_conductivity_w_mk    NUMERIC(10,4),
    thermal_expansion_1e6_k      NUMERIC(10,4),
    melting_point_c              NUMERIC(8,2),
    max_service_temp_c           NUMERIC(8,2),
    min_service_temp_c           NUMERIC(8,2),
    electrical_resistivity_ohm_m NUMERIC(15,10),
    specific_heat_j_kgk          NUMERIC(10,2),
    moisture_absorption_pct      NUMERIC(6,3),
    flammability_class           VARCHAR(20),
    unit_of_measure              unit_of_measure_enum NOT NULL DEFAULT 'KG',
    standard_thickness_mm        NUMERIC(8,3),
    standard_width_mm            NUMERIC(8,2),
    standard_length_mm           NUMERIC(8,2),
    surface_finish               VARCHAR(50),
    valid_from                   DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to                     DATE,

    CONSTRAINT uq_material_properties UNIQUE (material_id, valid_from),
    CONSTRAINT chk_density_positive CHECK (density_kg_m3 > 0),
    CONSTRAINT chk_valid_dates CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX idx_mat_props_material ON material_properties(material_id);
CREATE INDEX idx_mat_props_density ON material_properties(density_kg_m3);

-- =============================================================================
-- TABLE: material_mechanical_properties
-- =============================================================================

CREATE TABLE material_mechanical_properties (
    mech_prop_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id              UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    tensile_strength_mpa     NUMERIC(8,2),
    tensile_strength_min_mpa NUMERIC(8,2),
    tensile_strength_max_mpa NUMERIC(8,2),
    yield_strength_mpa       NUMERIC(8,2),
    yield_strength_min_mpa   NUMERIC(8,2),
    elongation_pct           NUMERIC(6,2),
    hardness_hb              NUMERIC(6,1),
    hardness_hrc             NUMERIC(6,2),
    hardness_hv              NUMERIC(6,1),
    impact_energy_j          NUMERIC(8,2),
    impact_temp_c            NUMERIC(6,1),
    youngs_modulus_gpa       NUMERIC(8,2),
    shear_modulus_gpa        NUMERIC(8,2),
    poissons_ratio           NUMERIC(6,4),
    fatigue_limit_mpa        NUMERIC(8,2),
    fracture_toughness_mpa_m NUMERIC(8,3),
    condition                VARCHAR(50),
    test_standard            VARCHAR(50),
    temperature_c            NUMERIC(6,1) DEFAULT 20,
    notes                    TEXT,

    CONSTRAINT uq_mech_props UNIQUE (material_id, condition, temperature_c)
);

CREATE INDEX idx_mech_props_material ON material_mechanical_properties(material_id);

-- =============================================================================
-- TABLE: material_polymer_properties (Polymer-specific)
-- =============================================================================

CREATE TABLE material_polymer_properties (
    polymer_prop_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id                  UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    melt_flow_index_g_10min      NUMERIC(8,3),
    melt_temperature_c           NUMERIC(8,2),
    glass_transition_temp_c      NUMERIC(8,2),
    vicat_softening_temp_c       NUMERIC(8,2),
    izod_impact_notched_j_m      NUMERIC(10,2),
    charpy_impact_j_m2           NUMERIC(10,2),
    flexural_modulus_mpa         NUMERIC(10,2),
    flexural_strength_mpa        NUMERIC(8,2),
    dielectric_constant          NUMERIC(8,4),
    uv_resistance                VARCHAR(20),
    chemical_resistance          JSONB,
    filler_type                  VARCHAR(50),
    filler_pct                   NUMERIC(5,2),
    color_masterbatch_compatible BOOLEAN DEFAULT TRUE,
    recyclable                   BOOLEAN,
    recycling_code               VARCHAR(10),
    rohs_compliant               BOOLEAN,
    fda_approved                 BOOLEAN,

    CONSTRAINT uq_polymer_props UNIQUE (material_id)
);

CREATE INDEX idx_polymer_props_material ON material_polymer_properties(material_id);

-- =============================================================================
-- TABLE: material_wood_properties (Wood-specific)
-- =============================================================================

CREATE TABLE material_wood_properties (
    wood_prop_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id              UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    density_dry_kg_m3        NUMERIC(8,2),
    moisture_content_pct     NUMERIC(5,2),
    bending_strength_mpa     NUMERIC(8,2),
    compression_strength_mpa NUMERIC(8,2),
    e1_modulus_mpa           NUMERIC(10,2),
    thickness_tolerance_mm   NUMERIC(5,3),
    formaldehyde_class       VARCHAR(10),
    glue_type                VARCHAR(50),
    surface_quality_class    VARCHAR(10),
    fire_retardant           BOOLEAN DEFAULT FALSE,
    moisture_resistant       BOOLEAN DEFAULT FALSE,
    species                  VARCHAR(100),
    certification            VARCHAR(50),  -- FSC, PEFC

    CONSTRAINT uq_wood_props UNIQUE (material_id)
);

-- =============================================================================
-- TABLE: material_standards
-- =============================================================================

CREATE TABLE material_standards (
    standard_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id             UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    standard_system         standard_system_enum NOT NULL,
    standard_number         VARCHAR(50) NOT NULL,
    standard_grade          VARCHAR(50),
    standard_title          VARCHAR(200),
    standard_year           SMALLINT,
    is_primary              BOOLEAN NOT NULL DEFAULT FALSE,
    equivalence_type        equivalence_type_enum,
    equivalence_notes       TEXT,
    certificate_required    BOOLEAN DEFAULT FALSE,
    certificate_type        VARCHAR(20),  -- 2.1, 3.1, 3.2
    url_reference           VARCHAR(500),

    CONSTRAINT uq_material_standard UNIQUE (material_id, standard_system, standard_number)
);

CREATE INDEX idx_mat_standards_material ON material_standards(material_id);
CREATE INDEX idx_mat_standards_system_number ON material_standards(standard_system, standard_number);

-- =============================================================================
-- TABLE: material_cost_coefficients
-- =============================================================================

CREATE TABLE material_cost_coefficients (
    coeff_id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id                 UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    scrap_rate_pct              NUMERIC(6,3) NOT NULL DEFAULT 0,
    yield_rate_pct              NUMERIC(6,3) NOT NULL DEFAULT 100,
    machining_allowance_mm      NUMERIC(6,2) DEFAULT 0,
    forming_allowance_pct       NUMERIC(6,3) DEFAULT 0,
    handling_cost_pct           NUMERIC(6,3) DEFAULT 1.5,
    storage_cost_pct_month      NUMERIC(6,4) DEFAULT 0.5,
    certification_cost_eur_kg   NUMERIC(10,4) DEFAULT 0,
    minimum_order_surcharge_pct NUMERIC(6,3) DEFAULT 0,
    cutting_waste_pct           NUMERIC(6,3) DEFAULT 5,
    nesting_efficiency_pct      NUMERIC(6,3) DEFAULT 80,
    density_tolerance_impact    NUMERIC(6,4) DEFAULT 0,
    custom_coefficients         JSONB DEFAULT '{}',
    currency                    CHAR(3) NOT NULL DEFAULT 'EUR',
    valid_from                  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to                    DATE,

    CONSTRAINT uq_cost_coefficients UNIQUE (material_id, valid_from),
    CONSTRAINT chk_yield_rate CHECK (yield_rate_pct BETWEEN 0 AND 100),
    CONSTRAINT chk_scrap_rate CHECK (scrap_rate_pct BETWEEN 0 AND 100)
);

CREATE INDEX idx_cost_coeff_material ON material_cost_coefficients(material_id);
CREATE INDEX idx_cost_coeff_valid ON material_cost_coefficients(valid_from, valid_to);

-- =============================================================================
-- TABLE: price_sources
-- =============================================================================

CREATE TABLE price_sources (
    source_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_code          VARCHAR(50) NOT NULL,
    source_name          VARCHAR(200) NOT NULL,
    source_type          price_source_type_enum NOT NULL,
    url                  VARCHAR(500),
    update_frequency     update_frequency_enum NOT NULL DEFAULT 'DAILY',
    api_connector_class  VARCHAR(200),
    api_config           JSONB DEFAULT '{}',
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    reliability_score    SMALLINT DEFAULT 5 CHECK (reliability_score BETWEEN 1 AND 10),
    currency             CHAR(3) NOT NULL DEFAULT 'EUR',
    last_sync_at         TIMESTAMPTZ,
    last_sync_status     VARCHAR(50),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_source_code UNIQUE (source_code)
);

-- =============================================================================
-- TABLE: market_price_records
-- =============================================================================

CREATE TABLE market_price_records (
    price_record_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id      UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    source_id        UUID NOT NULL REFERENCES price_sources(source_id),
    price_type       price_type_enum NOT NULL,
    price_date       DATE NOT NULL,
    price_value      NUMERIC(14,4) NOT NULL,
    currency         CHAR(3) NOT NULL DEFAULT 'EUR',
    unit             price_unit_enum NOT NULL DEFAULT 'PER_KG',
    price_region     VARCHAR(50) DEFAULT 'EU',
    confidence_level SMALLINT DEFAULT 3 CHECK (confidence_level BETWEEN 1 AND 5),
    notes            TEXT,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_price_record UNIQUE (material_id, source_id, price_type, price_date, price_region),
    CONSTRAINT chk_price_positive CHECK (price_value > 0)
);

CREATE INDEX idx_price_records_material ON market_price_records(material_id);
CREATE INDEX idx_price_records_date ON market_price_records(price_date DESC);
CREATE INDEX idx_price_records_active ON market_price_records(material_id, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_price_records_source ON market_price_records(source_id);
CREATE INDEX idx_price_records_composite ON market_price_records(material_id, price_type, price_date DESC);

-- =============================================================================
-- TABLE: material_substitutions
-- =============================================================================

CREATE TABLE material_substitutions (
    substitution_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_material_id             UUID NOT NULL REFERENCES materials(material_id),
    substitute_material_id         UUID NOT NULL REFERENCES materials(material_id),
    substitution_type              substitution_type_enum NOT NULL,
    compatibility_score            NUMERIC(5,2) NOT NULL CHECK (compatibility_score BETWEEN 0 AND 100),
    cost_impact_pct                NUMERIC(8,3) DEFAULT 0,
    weight_impact_pct              NUMERIC(8,3) DEFAULT 0,
    conditions                     JSONB DEFAULT '{}',
    process_changes                TEXT,
    engineering_approval_required  BOOLEAN NOT NULL DEFAULT FALSE,
    valid_applications             TEXT[],
    excluded_applications          TEXT[],
    approved_by                    UUID,
    approved_at                    TIMESTAMPTZ,
    is_active                      BOOLEAN NOT NULL DEFAULT TRUE,
    notes                          TEXT,
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_substitution UNIQUE (source_material_id, substitute_material_id),
    CONSTRAINT chk_no_self_substitution CHECK (source_material_id <> substitute_material_id)
);

CREATE INDEX idx_substitutions_source ON material_substitutions(source_material_id) WHERE is_active = TRUE;
CREATE INDEX idx_substitutions_substitute ON material_substitutions(substitute_material_id) WHERE is_active = TRUE;
CREATE INDEX idx_substitutions_score ON material_substitutions(compatibility_score DESC);

-- =============================================================================
-- TABLE: process_compatibility
-- =============================================================================

CREATE TABLE process_compatibility (
    compat_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id             UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    process_code            VARCHAR(50) NOT NULL,
    process_category        process_category_enum NOT NULL,
    compatibility_level     compatibility_level_enum NOT NULL,
    min_thickness_mm        NUMERIC(6,2),
    max_thickness_mm        NUMERIC(6,2),
    min_hardness_hb         NUMERIC(6,1),
    max_hardness_hb         NUMERIC(6,1),
    speed_factor            NUMERIC(6,3) DEFAULT 1.0,
    quality_notes           TEXT,
    tooling_requirements    TEXT,
    parameter_overrides     JSONB DEFAULT '{}',
    waste_factor_override   NUMERIC(6,3),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_process_compat UNIQUE (material_id, process_code),
    CONSTRAINT chk_speed_factor CHECK (speed_factor > 0)
);

CREATE INDEX idx_process_compat_material ON process_compatibility(material_id);
CREATE INDEX idx_process_compat_process ON process_compatibility(process_code);
CREATE INDEX idx_process_compat_level ON process_compatibility(compatibility_level);

-- =============================================================================
-- TABLE: supplier_material_mapping
-- =============================================================================

CREATE TABLE supplier_material_mapping (
    mapping_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id              UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    supplier_id              UUID NOT NULL,  -- FK to supplier_intelligence schema
    supplier_material_code   VARCHAR(100),
    supplier_material_name   VARCHAR(200),
    is_preferred             BOOLEAN NOT NULL DEFAULT FALSE,
    is_approved              BOOLEAN NOT NULL DEFAULT FALSE,
    approval_date            DATE,
    min_order_quantity       NUMERIC(14,4),
    min_order_unit           price_unit_enum,
    lead_time_days           SMALLINT,
    lead_time_days_express   SMALLINT,
    price_validity_days      SMALLINT DEFAULT 30,
    incoterms                VARCHAR(10),
    origin_country           CHAR(2),
    supply_risk_level        supply_risk_enum NOT NULL DEFAULT 'MEDIUM',
    is_single_source         BOOLEAN NOT NULL DEFAULT FALSE,
    active                   BOOLEAN NOT NULL DEFAULT TRUE,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_supplier_material UNIQUE (material_id, supplier_id)
);

CREATE INDEX idx_supplier_mat_material ON supplier_material_mapping(material_id) WHERE active = TRUE;
CREATE INDEX idx_supplier_mat_supplier ON supplier_material_mapping(supplier_id) WHERE active = TRUE;
CREATE INDEX idx_supplier_mat_preferred ON supplier_material_mapping(material_id, is_preferred) WHERE is_preferred = TRUE;
CREATE INDEX idx_supplier_mat_risk ON supplier_material_mapping(supply_risk_level);

-- =============================================================================
-- TABLE: material_embeddings (pgvector)
-- =============================================================================

CREATE TABLE material_embeddings (
    embedding_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id      UUID NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    embedding_model  VARCHAR(100) NOT NULL,
    embedding_version VARCHAR(20) NOT NULL,
    vector           VECTOR(1536),
    text_input       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_current       BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT uq_embedding_current UNIQUE (material_id, embedding_model, is_current)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX idx_embeddings_material ON material_embeddings(material_id) WHERE is_current = TRUE;
-- HNSW index for fast ANN search
CREATE INDEX idx_embeddings_vector_hnsw ON material_embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- TABLE: material_price_forecasts
-- =============================================================================

CREATE TABLE material_price_forecasts (
    forecast_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id        UUID NOT NULL REFERENCES materials(material_id),
    forecast_date      DATE NOT NULL,
    horizon_days       SMALLINT NOT NULL,
    price_forecast     NUMERIC(14,4) NOT NULL,
    price_lower_95     NUMERIC(14,4),
    price_upper_95     NUMERIC(14,4),
    currency           CHAR(3) NOT NULL DEFAULT 'EUR',
    unit               price_unit_enum NOT NULL DEFAULT 'PER_KG',
    model_name         VARCHAR(100),
    model_version      VARCHAR(20),
    mape_pct           NUMERIC(6,3),
    confidence_pct     NUMERIC(5,2),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_forecast UNIQUE (material_id, forecast_date, horizon_days, model_name)
);

CREATE INDEX idx_forecasts_material ON material_price_forecasts(material_id, forecast_date);

-- =============================================================================
-- TABLE: material_audit_log
-- =============================================================================

CREATE TABLE material_audit_log (
    audit_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id   UUID REFERENCES materials(material_id),
    entity_type   VARCHAR(50) NOT NULL,
    entity_id     UUID NOT NULL,
    operation     audit_operation_enum NOT NULL,
    changed_fields JSONB,
    changed_by    UUID,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason        TEXT,
    ip_address    INET,
    session_id    VARCHAR(100),
    request_id    VARCHAR(100)
) PARTITION BY RANGE (changed_at);

CREATE TABLE material_audit_log_2025 PARTITION OF material_audit_log
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE material_audit_log_2026 PARTITION OF material_audit_log
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE material_audit_log_2027 PARTITION OF material_audit_log
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_audit_material ON material_audit_log(material_id, changed_at DESC);
CREATE INDEX idx_audit_entity ON material_audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_user ON material_audit_log(changed_by, changed_at DESC);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Active materials with current price
CREATE VIEW v_materials_with_price AS
SELECT
    m.material_id,
    m.material_code,
    m.material_name,
    m.material_class,
    m.grade,
    m.form_factor,
    m.status,
    mp.density_kg_m3,
    mp.unit_of_measure,
    mpr.price_value AS current_price,
    mpr.currency    AS price_currency,
    mpr.unit        AS price_unit,
    mpr.price_date  AS price_date,
    mcc.scrap_rate_pct,
    mcc.yield_rate_pct,
    mcc.cutting_waste_pct
FROM materials m
LEFT JOIN material_properties mp
    ON mp.material_id = m.material_id
    AND mp.valid_to IS NULL
LEFT JOIN LATERAL (
    SELECT price_value, currency, unit, price_date
    FROM market_price_records
    WHERE material_id = m.material_id
      AND price_type = 'PURCHASE'
      AND is_active = TRUE
    ORDER BY price_date DESC
    LIMIT 1
) mpr ON TRUE
LEFT JOIN LATERAL (
    SELECT scrap_rate_pct, yield_rate_pct, cutting_waste_pct
    FROM material_cost_coefficients
    WHERE material_id = m.material_id
      AND valid_to IS NULL
    LIMIT 1
) mcc ON TRUE
WHERE m.status = 'ACTIVE';

-- Substitution network view
CREATE VIEW v_substitution_network AS
SELECT
    s.substitution_id,
    src.material_code    AS source_code,
    src.material_name    AS source_name,
    sub.material_code    AS substitute_code,
    sub.material_name    AS substitute_name,
    s.substitution_type,
    s.compatibility_score,
    s.cost_impact_pct,
    s.weight_impact_pct,
    s.engineering_approval_required
FROM material_substitutions s
JOIN materials src ON src.material_id = s.source_material_id
JOIN materials sub ON sub.material_id = s.substitute_material_id
WHERE s.is_active = TRUE
  AND src.status = 'ACTIVE'
  AND sub.status = 'ACTIVE';

-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- Get effective density for a material (with fallback)
CREATE OR REPLACE FUNCTION get_material_density(p_material_id UUID)
RETURNS NUMERIC AS $$
DECLARE
    v_density NUMERIC;
BEGIN
    SELECT density_kg_m3 INTO v_density
    FROM material_properties
    WHERE material_id = p_material_id
      AND valid_to IS NULL
    ORDER BY valid_from DESC
    LIMIT 1;

    RETURN v_density;
END;
$$ LANGUAGE plpgsql STABLE;

-- Get current purchase price for a material
CREATE OR REPLACE FUNCTION get_current_price(
    p_material_id UUID,
    p_currency CHAR(3) DEFAULT 'EUR',
    p_region VARCHAR(50) DEFAULT 'EU'
)
RETURNS TABLE(price_value NUMERIC, currency CHAR(3), unit price_unit_enum, price_date DATE) AS $$
BEGIN
    RETURN QUERY
    SELECT mpr.price_value, mpr.currency, mpr.unit, mpr.price_date
    FROM market_price_records mpr
    WHERE mpr.material_id = p_material_id
      AND mpr.price_type = 'PURCHASE'
      AND mpr.currency = p_currency
      AND mpr.price_region = p_region
      AND mpr.is_active = TRUE
    ORDER BY mpr.price_date DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- Calculate material cost per kg with coefficients
CREATE OR REPLACE FUNCTION calculate_material_cost_per_unit(
    p_material_id UUID,
    p_quantity_kg NUMERIC DEFAULT 1
)
RETURNS JSONB AS $$
DECLARE
    v_base_price      NUMERIC;
    v_scrap_rate      NUMERIC;
    v_yield_rate      NUMERIC;
    v_handling_pct    NUMERIC;
    v_storage_pct     NUMERIC;
    v_cert_cost       NUMERIC;
    v_total_cost      NUMERIC;
    v_currency        CHAR(3);
BEGIN
    -- Get base price
    SELECT price_value, currency INTO v_base_price, v_currency
    FROM market_price_records
    WHERE material_id = p_material_id
      AND price_type = 'PURCHASE'
      AND is_active = TRUE
    ORDER BY price_date DESC
    LIMIT 1;

    -- Get cost coefficients
    SELECT scrap_rate_pct, yield_rate_pct, handling_cost_pct,
           storage_cost_pct_month, certification_cost_eur_kg
    INTO v_scrap_rate, v_yield_rate, v_handling_pct, v_storage_pct, v_cert_cost
    FROM material_cost_coefficients
    WHERE material_id = p_material_id
      AND valid_to IS NULL
    LIMIT 1;

    -- Calculate
    v_total_cost := v_base_price
        * (1 + COALESCE(v_scrap_rate, 5) / 100)
        / (COALESCE(v_yield_rate, 95) / 100)
        * (1 + COALESCE(v_handling_pct, 1.5) / 100)
        + COALESCE(v_cert_cost, 0);

    RETURN jsonb_build_object(
        'material_id',    p_material_id,
        'base_price',     v_base_price,
        'total_cost',     ROUND(v_total_cost, 4),
        'currency',       v_currency,
        'scrap_rate_pct', v_scrap_rate,
        'yield_rate_pct', v_yield_rate
    );
END;
$$ LANGUAGE plpgsql STABLE;
