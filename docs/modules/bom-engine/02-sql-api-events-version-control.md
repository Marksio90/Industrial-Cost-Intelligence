# BOM Engine — Sekcje 6–9: SQL Schema, API, Event Model, Version Control

---

## 6. SQL Schema

### 6.1 Schemat `bome` — ENUMy

```sql
-- PostgreSQL 16
CREATE SCHEMA IF NOT EXISTS bome;

CREATE TYPE bome.bom_type AS ENUM (
    'ENGINEERING', 'MANUFACTURING', 'SERVICE', 'COSTING', 'PLANNING'
);

CREATE TYPE bome.bom_status AS ENUM (
    'DRAFT', 'IN_REVIEW', 'RELEASED', 'FROZEN', 'OBSOLETE', 'SUPERSEDED'
);

CREATE TYPE bome.bom_line_type AS ENUM (
    'COMPONENT', 'SUBASSEMBLY', 'RAW_MATERIAL', 'SEMI_FINISHED',
    'FASTENER', 'PACKAGING', 'TOOLING', 'PHANTOM', 'REFERENCE'
);

CREATE TYPE bome.make_or_buy AS ENUM ('MAKE', 'BUY', 'CONSIGNED');

CREATE TYPE bome.uom AS ENUM ('PC', 'KG', 'M', 'M2', 'M3', 'L', 'SET', 'LOT');

CREATE TYPE bome.change_type AS ENUM (
    'INITIAL_RELEASE', 'ECO', 'MCO', 'DCO', 'DEVIATION', 'CORRECTION'
);

CREATE TYPE bome.change_status AS ENUM (
    'DRAFT', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'REJECTED',
    'IMPLEMENTING', 'CLOSED', 'CANCELLED'
);

CREATE TYPE bome.approval_status AS ENUM (
    'PENDING', 'APPROVED', 'CONDITIONALLY_APPROVED', 'REJECTED', 'EXPIRED'
);

CREATE TYPE bome.substitution_reason AS ENUM (
    'COST_REDUCTION', 'SUPPLY_SHORTAGE', 'TECHNICAL_EQUIV',
    'REGULATORY', 'LEAD_TIME', 'SINGLE_SOURCE', 'QUALIFICATION'
);
```

### 6.2 Tabele

```sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Material Master (uproszczony — pełny w ERP)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.material_master (
    item_code               VARCHAR(50) PRIMARY KEY,
    description             VARCHAR(255) NOT NULL,
    material_group          VARCHAR(50) NOT NULL,
    base_uom                bome.uom NOT NULL DEFAULT 'PC',
    weight_kg_per_uom       NUMERIC(12,6),
    density_g_cm3           NUMERIC(8,4),
    list_price_eur          NUMERIC(14,4),
    last_po_price_eur       NUMERIC(14,4),
    last_po_date            DATE,
    preferred_supplier      VARCHAR(100),
    lead_time_days          INTEGER,
    min_order_qty           NUMERIC(12,4),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    is_critical             BOOLEAN NOT NULL DEFAULT FALSE,  -- long lead-time / single source
    is_hazardous            BOOLEAN NOT NULL DEFAULT FALSE,
    rohs_compliant          BOOLEAN NOT NULL DEFAULT TRUE,
    reach_compliant         BOOLEAN NOT NULL DEFAULT TRUE,
    -- PLM/ERP references
    erp_material_id         VARCHAR(50),
    plm_item_id             VARCHAR(100),
    -- Metadata
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_mm_material_group ON bome.material_master(material_group);
CREATE INDEX idx_mm_preferred_supplier ON bome.material_master(preferred_supplier);
CREATE INDEX idx_mm_active ON bome.material_master(is_active, item_code);


-- ─────────────────────────────────────────────────────────────────────────────
-- BOM Headers
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.bom_headers (
    bom_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code            VARCHAR(50) NOT NULL,
    product_description     VARCHAR(255),
    revision                VARCHAR(10) NOT NULL DEFAULT 'A',
    bom_type                bome.bom_type NOT NULL,
    status                  bome.bom_status NOT NULL DEFAULT 'DRAFT',
    effective_from          DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to            DATE,
    production_location     VARCHAR(5),
    annual_volume           INTEGER,
    base_quantity           NUMERIC(12,4) NOT NULL DEFAULT 1,
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    -- Relationships
    source_bom_id           UUID REFERENCES bome.bom_headers(bom_id),  -- EBOM → MBOM
    superseded_by_bom_id    UUID REFERENCES bome.bom_headers(bom_id),
    -- CAD / PLM link
    cad_document_id         VARCHAR(100),
    cad_document_version    VARCHAR(20),
    -- Change management
    change_order_id         UUID,   -- FK → bome.change_orders
    -- Release
    released_by             VARCHAR(100),
    released_at             TIMESTAMPTZ,
    -- Metadata
    created_by              VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Unikalność: jeden BOM per product_code + revision + type
    CONSTRAINT uq_bom_product_revision_type UNIQUE (product_code, revision, bom_type),
    CONSTRAINT chk_effective_dates CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE INDEX idx_bom_headers_product ON bome.bom_headers(product_code, bom_type, status);
CREATE INDEX idx_bom_headers_status ON bome.bom_headers(status, effective_from DESC);
CREATE INDEX idx_bom_headers_source ON bome.bom_headers(source_bom_id) WHERE source_bom_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- BOM Lines (adjacency list dla multi-level)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.bom_lines (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id                  UUID NOT NULL REFERENCES bome.bom_headers(bom_id) ON DELETE CASCADE,
    parent_line_id          UUID REFERENCES bome.bom_lines(line_id),
    position                INTEGER NOT NULL DEFAULT 10,    -- krok 10 (SAP-style)
    item_code               VARCHAR(50) NOT NULL REFERENCES bome.material_master(item_code),
    item_description        VARCHAR(255),
    line_type               bome.bom_line_type NOT NULL DEFAULT 'COMPONENT',
    quantity                NUMERIC(14,6) NOT NULL DEFAULT 1,
    uom                     bome.uom NOT NULL DEFAULT 'PC',
    scrap_factor_pct        NUMERIC(6,3) NOT NULL DEFAULT 0
        CONSTRAINT chk_scrap_pct CHECK (scrap_factor_pct >= 0 AND scrap_factor_pct < 100),
    effective_quantity      NUMERIC(14,6) GENERATED ALWAYS AS (
        quantity / (1 - scrap_factor_pct / 100.0)
    ) STORED,
    make_or_buy             bome.make_or_buy NOT NULL DEFAULT 'BUY',
    phantom                 BOOLEAN NOT NULL DEFAULT FALSE,
    critical_item           BOOLEAN NOT NULL DEFAULT FALSE,
    lead_time_days          INTEGER,
    reference_designator    VARCHAR(500),   -- "R1,R2,C5"
    find_number             VARCHAR(20),    -- balloon number
    -- Cost
    cost_override_eur       NUMERIC(14,4),  -- ręczne nadpisanie
    -- CAD / PLM
    cad_reference           VARCHAR(100),
    -- Substitution
    substitution_request_id UUID,
    -- Change management
    change_order_id         UUID,
    added_in_revision       VARCHAR(10),
    removed_in_revision     VARCHAR(10),    -- NULL = aktywna
    -- Flags
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    -- Metadata
    created_by              VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bom_line_position UNIQUE (bom_id, parent_line_id, position)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX idx_bom_lines_bom_id ON bome.bom_lines(bom_id, is_active);
CREATE INDEX idx_bom_lines_parent ON bome.bom_lines(parent_line_id) WHERE parent_line_id IS NOT NULL;
CREATE INDEX idx_bom_lines_item_code ON bome.bom_lines(item_code, is_active);


-- ─────────────────────────────────────────────────────────────────────────────
-- Material Substitutes
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.material_substitutes (
    substitute_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_item_code      VARCHAR(50) NOT NULL REFERENCES bome.material_master(item_code),
    substitute_item_code    VARCHAR(50) NOT NULL REFERENCES bome.material_master(item_code),
    priority                INTEGER NOT NULL DEFAULT 1,
    interchangeable         BOOLEAN NOT NULL DEFAULT TRUE,
    quantity_factor         NUMERIC(8,6) NOT NULL DEFAULT 1.0,
    cost_delta_pct          NUMERIC(8,3),
    valid_from              DATE,
    valid_until             DATE,
    approval_status         bome.approval_status NOT NULL DEFAULT 'PENDING',
    approved_by             VARCHAR(100),
    approved_at             TIMESTAMPTZ,
    reason                  bome.substitution_reason,
    note                    TEXT,
    created_by              VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_substitute UNIQUE (original_item_code, substitute_item_code),
    CONSTRAINT chk_not_self_substitute CHECK (original_item_code != substitute_item_code)
);
CREATE INDEX idx_substitutes_original ON bome.material_substitutes(original_item_code, approval_status);


-- ─────────────────────────────────────────────────────────────────────────────
-- Substitution Requests (MDR — Material Deviation Request)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.substitution_requests (
    request_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_item_code      VARCHAR(50) NOT NULL,
    substitute_item_code    VARCHAR(50) NOT NULL,
    reason                  bome.substitution_reason NOT NULL,
    affected_bom_ids        UUID[] NOT NULL DEFAULT '{}',
    requested_by            VARCHAR(100) NOT NULL,
    valid_from              DATE,
    valid_until             DATE,
    quantity_factor         NUMERIC(8,6) NOT NULL DEFAULT 1.0,
    cost_impact_eur         NUMERIC(14,4),
    quality_impact          VARCHAR(20) NOT NULL DEFAULT 'NONE',  -- NONE/MINOR/MAJOR
    process_change_required BOOLEAN NOT NULL DEFAULT FALSE,
    approval_status         bome.approval_status NOT NULL DEFAULT 'PENDING',
    approvals               JSONB NOT NULL DEFAULT '[]',
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Tooling Items (amortyzowane)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.tooling_items (
    tooling_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id                  UUID NOT NULL REFERENCES bome.bom_headers(bom_id),
    tooling_code            VARCHAR(50) NOT NULL,
    description             VARCHAR(255),
    tooling_cost_eur        NUMERIC(14,4) NOT NULL,
    amortization_qty        INTEGER NOT NULL,   -- ilość sztuk do pełnej amortyzacji
    tooling_type            VARCHAR(50),        -- MOLD / DIE / FIXTURE / JIG
    supplier                VARCHAR(100),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tooling_bom ON bome.tooling_items(bom_id, is_active);


-- ─────────────────────────────────────────────────────────────────────────────
-- Cost Rollup Cache
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.cost_rollups (
    rollup_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id                  UUID NOT NULL REFERENCES bome.bom_headers(bom_id),
    volume                  INTEGER NOT NULL DEFAULT 1,
    material_cost_eur       NUMERIC(14,4) NOT NULL,
    process_cost_eur        NUMERIC(14,4) NOT NULL,
    overhead_cost_eur       NUMERIC(14,4) NOT NULL,
    tooling_amortization_eur NUMERIC(14,4) NOT NULL DEFAULT 0,
    total_cost_eur          NUMERIC(14,4) NOT NULL,
    rollup_confidence       NUMERIC(5,4) NOT NULL,
    warnings                JSONB NOT NULL DEFAULT '[]',
    rolled_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rollup_bom_volume UNIQUE (bom_id, volume)
);
CREATE INDEX idx_rollup_bom ON bome.cost_rollups(bom_id, rolled_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Product Families & Variants
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.product_families (
    family_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_code             VARCHAR(50) NOT NULL UNIQUE,
    family_description      VARCHAR(255),
    features                TEXT[] NOT NULL DEFAULT '{}',   -- ["SIZE","COLOR","GRADE"]
    generic_bom_id          UUID REFERENCES bome.bom_headers(bom_id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE bome.variant_options (
    option_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id               UUID NOT NULL REFERENCES bome.product_families(family_id),
    feature_name            VARCHAR(50) NOT NULL,
    option_code             VARCHAR(50) NOT NULL,
    option_description      VARCHAR(255),
    is_default              BOOLEAN NOT NULL DEFAULT FALSE,
    is_mandatory            BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_family_feature_option UNIQUE (family_id, feature_name, option_code)
);

CREATE TABLE bome.variant_rules (
    rule_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id               UUID NOT NULL REFERENCES bome.product_families(family_id),
    line_id                 UUID NOT NULL REFERENCES bome.bom_lines(line_id),
    condition_expr          TEXT NOT NULL DEFAULT 'True',
    quantity_expr           TEXT,
    item_override           VARCHAR(50) REFERENCES bome.material_master(item_code),
    sort_order              INTEGER NOT NULL DEFAULT 10
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Change Orders (ECO/MCO/DCO)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.change_orders (
    change_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_number           VARCHAR(20) NOT NULL UNIQUE,    -- np. "ECO-2026-0042"
    change_type             bome.change_type NOT NULL,
    title                   VARCHAR(255) NOT NULL,
    description             TEXT,
    status                  bome.change_status NOT NULL DEFAULT 'DRAFT',
    affected_bom_ids        UUID[] NOT NULL DEFAULT '{}',
    affected_item_codes     VARCHAR(50)[] NOT NULL DEFAULT '{}',
    requester               VARCHAR(100) NOT NULL,
    owner                   VARCHAR(100),
    implementation_date     DATE,
    cost_impact_eur         NUMERIC(14,4),
    risk_level              VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',  -- LOW/MEDIUM/HIGH/CRITICAL
    approval_chain          JSONB NOT NULL DEFAULT '[]',
    attachments             JSONB NOT NULL DEFAULT '[]',
    erp_change_id           VARCHAR(50),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at               TIMESTAMPTZ
);
CREATE INDEX idx_change_orders_status ON bome.change_orders(status, created_at DESC);
CREATE INDEX idx_change_orders_boms ON bome.change_orders USING gin(affected_bom_ids);


-- ─────────────────────────────────────────────────────────────────────────────
-- BOM Versions (snapshot per każda zmiana statusu)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.bom_versions (
    version_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_id                  UUID NOT NULL REFERENCES bome.bom_headers(bom_id),
    version_number          INTEGER NOT NULL,
    snapshot                JSONB NOT NULL,     -- pełna kopia BOM (header + lines)
    status_at_snapshot      bome.bom_status NOT NULL,
    change_order_id         UUID REFERENCES bome.change_orders(change_id),
    comment                 TEXT,
    created_by              VARCHAR(100) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bom_version UNIQUE (bom_id, version_number)
);
CREATE INDEX idx_bom_versions_bom ON bome.bom_versions(bom_id, version_number DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Audit Log (append-only)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.audit_log (
    audit_id                BIGSERIAL PRIMARY KEY,
    entity_type             VARCHAR(50) NOT NULL,   -- 'BOM_HEADER' / 'BOM_LINE' / 'CHANGE_ORDER'
    entity_id               UUID NOT NULL,
    action                  VARCHAR(20) NOT NULL,   -- 'CREATE' / 'UPDATE' / 'DELETE' / 'RELEASE'
    changed_by              VARCHAR(100) NOT NULL,
    changed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    old_value               JSONB,
    new_value               JSONB,
    change_reason           TEXT,
    ip_address              INET,
    session_id              VARCHAR(100)
) PARTITION BY RANGE (changed_at);

CREATE TABLE bome.audit_log_2026
    PARTITION OF bome.audit_log
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE bome.audit_log_2027
    PARTITION OF bome.audit_log
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_audit_entity ON bome.audit_log(entity_id, changed_at DESC);
CREATE INDEX idx_audit_changed_at ON bome.audit_log(changed_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Outbox
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE bome.outbox (
    outbox_id       BIGSERIAL PRIMARY KEY,
    topic           VARCHAR(200) NOT NULL,
    key             VARCHAR(500),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    published       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_bome_outbox_unpublished ON bome.outbox(published, created_at)
    WHERE published = FALSE;
```

### 6.3 Stored Functions & Triggers

```sql
-- Auto-update updated_at
CREATE OR REPLACE FUNCTION bome.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

CREATE TRIGGER trg_bom_headers_updated_at
    BEFORE UPDATE ON bome.bom_headers
    FOR EACH ROW EXECUTE FUNCTION bome.set_updated_at();

CREATE TRIGGER trg_bom_lines_updated_at
    BEFORE UPDATE ON bome.bom_lines
    FOR EACH ROW EXECUTE FUNCTION bome.set_updated_at();

-- Walidacja: nie można edytować RELEASED/FROZEN BOM bez ECO
CREATE OR REPLACE FUNCTION bome.validate_bom_edit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE v_status bome.bom_status;
BEGIN
    SELECT status INTO v_status FROM bome.bom_headers WHERE bom_id = NEW.bom_id;
    IF v_status IN ('RELEASED', 'FROZEN') THEN
        IF NEW.change_order_id IS NULL THEN
            RAISE EXCEPTION 'Cannot edit RELEASED/FROZEN BOM without change_order_id';
        END IF;
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_validate_bom_line_edit
    BEFORE INSERT OR UPDATE ON bome.bom_lines
    FOR EACH ROW EXECUTE FUNCTION bome.validate_bom_edit();

-- Auto-snapshot przy zmianie statusu BOM
CREATE OR REPLACE FUNCTION bome.snapshot_bom_on_status_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_version_num INTEGER;
    v_snapshot JSONB;
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT COALESCE(MAX(version_number), 0) + 1
        INTO v_version_num
        FROM bome.bom_versions
        WHERE bom_id = NEW.bom_id;

        SELECT jsonb_build_object(
            'header', row_to_json(NEW)::jsonb,
            'lines', (
                SELECT jsonb_agg(row_to_json(l)::jsonb ORDER BY l.position)
                FROM bome.bom_lines l
                WHERE l.bom_id = NEW.bom_id AND l.is_active = TRUE
            )
        ) INTO v_snapshot;

        INSERT INTO bome.bom_versions (
            bom_id, version_number, snapshot, status_at_snapshot,
            change_order_id, created_by
        ) VALUES (
            NEW.bom_id, v_version_num, v_snapshot, NEW.status,
            NEW.change_order_id, NEW.released_by
        );
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_snapshot_bom
    AFTER UPDATE OF status ON bome.bom_headers
    FOR EACH ROW EXECUTE FUNCTION bome.snapshot_bom_on_status_change();

-- Get effective BOM (latest released dla product_code)
CREATE OR REPLACE FUNCTION bome.get_effective_bom(
    p_product_code  TEXT,
    p_bom_type      TEXT DEFAULT 'MANUFACTURING',
    p_as_of_date    DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (bom_id UUID, revision TEXT, status bome.bom_status)
LANGUAGE sql STABLE AS $$
    SELECT bom_id, revision, status
    FROM bome.bom_headers
    WHERE product_code = p_product_code
      AND bom_type = p_bom_type::bome.bom_type
      AND status IN ('RELEASED', 'FROZEN')
      AND effective_from <= p_as_of_date
      AND (effective_to IS NULL OR effective_to >= p_as_of_date)
    ORDER BY effective_from DESC
    LIMIT 1;
$$;

-- Claim outbox batch
CREATE OR REPLACE FUNCTION bome.claim_outbox_batch(p_limit INTEGER DEFAULT 100)
RETURNS SETOF bome.outbox LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    UPDATE bome.outbox SET published = TRUE, published_at = NOW()
    WHERE outbox_id IN (
        SELECT outbox_id FROM bome.outbox
        WHERE published = FALSE
        ORDER BY created_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *;
END; $$;

-- Write outbox (transactional)
CREATE OR REPLACE FUNCTION bome.write_outbox(
    p_topic TEXT, p_key TEXT, p_payload JSONB
) RETURNS VOID LANGUAGE sql AS $$
    INSERT INTO bome.outbox (topic, key, payload) VALUES (p_topic, p_key, p_payload);
$$;
```

### 6.4 Widoki

```sql
-- Podsumowanie BOM z kosztem roll-up
CREATE OR REPLACE VIEW bome.v_bom_summary AS
SELECT
    bh.bom_id,
    bh.product_code,
    bh.product_description,
    bh.revision,
    bh.bom_type,
    bh.status,
    bh.effective_from,
    bh.effective_to,
    bh.production_location,
    bh.annual_volume,
    COUNT(bl.line_id) AS total_lines,
    COUNT(DISTINCT bl.item_code) AS unique_components,
    SUM(CASE WHEN bl.make_or_buy = 'MAKE' THEN 1 ELSE 0 END) AS make_count,
    SUM(CASE WHEN bl.make_or_buy = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
    SUM(CASE WHEN bl.critical_item THEN 1 ELSE 0 END) AS critical_items,
    cr.total_cost_eur,
    cr.rollup_confidence,
    cr.rolled_at,
    bh.updated_at
FROM bome.bom_headers bh
LEFT JOIN bome.bom_lines bl ON bl.bom_id = bh.bom_id AND bl.is_active = TRUE
LEFT JOIN bome.cost_rollups cr ON cr.bom_id = bh.bom_id
    AND cr.volume = COALESCE(bh.annual_volume, 1)
GROUP BY bh.bom_id, cr.total_cost_eur, cr.rollup_confidence, cr.rolled_at;


-- Where-used (inverted BOM)
CREATE OR REPLACE VIEW bome.v_where_used AS
SELECT
    bl.item_code,
    mm.description AS item_description,
    bh.bom_id,
    bh.product_code,
    bh.revision,
    bh.bom_type,
    bh.status,
    bl.quantity,
    bl.uom,
    bl.make_or_buy
FROM bome.bom_lines bl
JOIN bome.bom_headers bh ON bh.bom_id = bl.bom_id
JOIN bome.material_master mm ON mm.item_code = bl.item_code
WHERE bl.is_active = TRUE
  AND bh.status IN ('RELEASED', 'FROZEN', 'DRAFT');


-- Aktywne Change Orders z detalami
CREATE OR REPLACE VIEW bome.v_active_change_orders AS
SELECT
    co.change_id,
    co.change_number,
    co.change_type,
    co.title,
    co.status,
    co.risk_level,
    co.requester,
    co.owner,
    co.implementation_date,
    co.cost_impact_eur,
    ARRAY_LENGTH(co.affected_bom_ids, 1) AS affected_boms_count,
    co.created_at,
    co.updated_at
FROM bome.change_orders co
WHERE co.status NOT IN ('CLOSED', 'CANCELLED')
ORDER BY co.risk_level DESC, co.created_at DESC;
```

---

## 7. API

### 7.1 OpenAPI 3.1 — BOME REST API

```yaml
openapi: "3.1.0"
info:
  title: BOM Engine API
  version: "1.0.0"
  description: |
    REST API dla Bill of Materials Engine — zarządzanie strukturami BOM,
    wariantami, substytutami i cost roll-up.
servers:
  - url: https://api.industrial-cost.internal/bome/v1

security:
  - BearerAuth: []

paths:
  # ── BOM Headers ───────────────────────────────────────────────────────────
  /boms:
    post:
      operationId: createBOM
      summary: Utwórz nowy BOM
      tags: [bom]
      x-required-role: BOME_ENGINEER
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/BOMCreate' }
      responses:
        "201":
          description: BOM utworzony
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMHeader' }
        "409":
          description: BOM product_code + revision + type już istnieje

    get:
      operationId: listBOMs
      summary: Lista BOM (z filtrowaniem)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: product_code
          in: query
          schema: { type: string }
        - name: bom_type
          in: query
          schema: { type: string, enum: [ENGINEERING, MANUFACTURING, SERVICE, COSTING, PLANNING] }
        - name: status
          in: query
          schema: { type: string }
          description: Można podać wielokrotnie (OR)
        - name: production_location
          in: query
          schema: { type: string }
        - name: limit
          in: query
          schema: { type: integer, default: 50, maximum: 500 }
        - name: offset
          in: query
          schema: { type: integer, default: 0 }
      responses:
        "200":
          description: Lista BOM
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: { type: array, items: { $ref: '#/components/schemas/BOMSummary' } }
                  total: { type: integer }

  /boms/{bom_id}:
    get:
      operationId: getBOM
      summary: Pobierz BOM (header + lines top-level)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          description: BOM header + linie
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMHeader' }
        "404":
          description: BOM nie znaleziony

    put:
      operationId: updateBOM
      summary: Aktualizuj nagłówek BOM
      tags: [bom]
      x-required-role: BOME_ENGINEER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/BOMUpdate' }
      responses:
        "200":
          description: Zaktualizowany BOM
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMHeader' }
        "409":
          description: BOM w stanie RELEASED/FROZEN — wymagane change_order_id

    delete:
      operationId: deleteBOM
      summary: Usuń BOM (tylko DRAFT)
      tags: [bom]
      x-required-role: BOME_ADMIN
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "204":
          description: BOM usunięty

  /boms/{bom_id}/status:
    patch:
      operationId: changeBOMStatus
      summary: Zmień status BOM (DRAFT→IN_REVIEW→RELEASED itd.)
      tags: [bom]
      x-required-role: BOME_REVIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [new_status]
              properties:
                new_status:
                  type: string
                  enum: [IN_REVIEW, RELEASED, FROZEN, OBSOLETE]
                comment: { type: string }
                change_order_id: { type: string, format: uuid }
      responses:
        "200":
          description: Status zmieniony
        "422":
          description: Nieprawidłowe przejście statusu

  /boms/{bom_id}/tree:
    get:
      operationId: getBOMTree
      summary: Pełne drzewo BOM (wielopoziomowe, JSON)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: expand_phantoms
          in: query
          schema: { type: boolean, default: true }
        - name: max_depth
          in: query
          schema: { type: integer, default: 20 }
      responses:
        "200":
          description: Drzewo BOM
          content:
            application/json:
              schema:
                type: object
                properties:
                  bom_id: { type: string }
                  product_code: { type: string }
                  nodes: { type: array, items: { $ref: '#/components/schemas/BOMNode' } }

  /boms/{bom_id}/indented:
    get:
      operationId: getIndentedBOM
      summary: Flat lista z wcięciami (eksport / UI)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          description: Wcięta lista pozycji
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/IndentedBOMLine' }

  /boms/{bom_id}/cost-rollup:
    post:
      operationId: runCostRollup
      summary: Uruchom cost roll-up dla BOM
      tags: [bom, cost]
      x-required-role: BOME_ANALYST
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                volume: { type: integer, default: 1 }
                include_tooling: { type: boolean, default: true }
                use_cee_for_process: { type: boolean, default: true }
      responses:
        "200":
          description: Wynik roll-up
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMRollupResult' }

  /boms/{bom_id}/compare/{other_bom_id}:
    get:
      operationId: compareBOMs
      summary: Porównaj dwa BOM (diff)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: other_bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          description: BOM diff
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMDiff' }

  /boms/{bom_id}/export:
    get:
      operationId: exportBOM
      summary: Eksport BOM (CSV / Excel / JSON)
      tags: [bom]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: format
          in: query
          schema: { type: string, enum: [csv, excel, json], default: csv }
        - name: include_cost
          in: query
          schema: { type: boolean, default: true }
      responses:
        "200":
          description: Plik eksportu
          content:
            text/csv:
              schema: { type: string }
            application/vnd.openxmlformats-officedocument.spreadsheetml.sheet:
              schema: { type: string, format: binary }

  # ── BOM Lines ─────────────────────────────────────────────────────────────
  /boms/{bom_id}/lines:
    post:
      operationId: addBOMLine
      summary: Dodaj pozycję do BOM
      tags: [bom-lines]
      x-required-role: BOME_ENGINEER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/BOMLineCreate' }
      responses:
        "201":
          description: Linia dodana
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMLine' }
        "422":
          description: Circular reference lub walidacja nieudana

    get:
      operationId: listBOMLines
      summary: Lista pozycji BOM (flat, filtrowanie)
      tags: [bom-lines]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: parent_line_id
          in: query
          schema: { type: string, format: uuid }
        - name: line_type
          in: query
          schema: { type: string }
      responses:
        "200":
          description: Lista linii
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/BOMLine' }

  /boms/{bom_id}/lines/{line_id}:
    put:
      operationId: updateBOMLine
      summary: Aktualizuj pozycję BOM
      tags: [bom-lines]
      x-required-role: BOME_ENGINEER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: line_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/BOMLineUpdate' }
      responses:
        "200":
          description: Zaktualizowana linia

    delete:
      operationId: deleteBOMLine
      summary: Usuń pozycję BOM (soft-delete)
      tags: [bom-lines]
      x-required-role: BOME_ENGINEER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: line_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                change_order_id: { type: string, format: uuid }
                reason: { type: string }
      responses:
        "204":
          description: Linia usunięta (soft-delete)

  # ── Where Used ────────────────────────────────────────────────────────────
  /materials/{item_code}/where-used:
    get:
      operationId: getWhereUsed
      summary: Gdzie-jest-używany dla komponentu
      tags: [materials]
      x-required-role: BOME_VIEWER
      parameters:
        - name: item_code
          in: path
          required: true
          schema: { type: string }
        - name: bom_type
          in: query
          schema: { type: string }
        - name: status
          in: query
          schema: { type: string }
      responses:
        "200":
          description: Lista BOM używających komponentu
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/WhereUsedEntry' }

  # ── Substitutes ───────────────────────────────────────────────────────────
  /materials/{item_code}/substitutes:
    get:
      operationId: getSubstitutes
      summary: Zatwierdzone substytuty dla materiału
      tags: [materials]
      x-required-role: BOME_VIEWER
      parameters:
        - name: item_code
          in: path
          required: true
          schema: { type: string }
      responses:
        "200":
          description: Lista substytutów
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/MaterialSubstitute' }

  /substitutions:
    post:
      operationId: requestSubstitution
      summary: Złóż wniosek o substytucję materiału
      tags: [substitutions]
      x-required-role: BOME_ENGINEER
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/SubstitutionRequest' }
      responses:
        "201":
          description: Wniosek złożony
          content:
            application/json:
              schema: { $ref: '#/components/schemas/SubstitutionEvaluation' }

  /substitutions/{request_id}/approve:
    post:
      operationId: approveSubstitution
      summary: Zatwierdź substytucję
      tags: [substitutions]
      x-required-role: BOME_REVIEWER
      parameters:
        - name: request_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                decision: { type: string, enum: [APPROVED, CONDITIONALLY_APPROVED, REJECTED] }
                comment: { type: string }
      responses:
        "200":
          description: Decyzja zapisana

  # ── Variants ──────────────────────────────────────────────────────────────
  /families/{family_id}/configure:
    post:
      operationId: configureBOM
      summary: Skonfiguruj BOM dla wybranych opcji wariantu
      tags: [variants]
      x-required-role: BOME_VIEWER
      parameters:
        - name: family_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [options]
              properties:
                options:
                  type: object
                  description: feature → option_code
                  additionalProperties: { type: string }
                run_cost_rollup:
                  type: boolean
                  default: false
      responses:
        "200":
          description: Skonfigurowane drzewo BOM
          content:
            application/json:
              schema:
                type: object
                properties:
                  nodes: { type: array, items: { $ref: '#/components/schemas/BOMNode' } }
                  config_hash: { type: string }
                  rollup: { $ref: '#/components/schemas/BOMRollupResult' }

  # ── Change Orders ─────────────────────────────────────────────────────────
  /change-orders:
    post:
      operationId: createChangeOrder
      summary: Utwórz Change Order (ECO/MCO/DCO)
      tags: [changes]
      x-required-role: BOME_ENGINEER
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/ChangeOrderCreate' }
      responses:
        "201":
          description: Change Order utworzony
          content:
            application/json:
              schema: { $ref: '#/components/schemas/ChangeOrder' }

    get:
      operationId: listChangeOrders
      summary: Lista Change Orders
      tags: [changes]
      x-required-role: BOME_VIEWER
      parameters:
        - name: status
          in: query
          schema: { type: string }
        - name: change_type
          in: query
          schema: { type: string }
        - name: risk_level
          in: query
          schema: { type: string }
      responses:
        "200":
          description: Lista Change Orders
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/ChangeOrder' }

  /change-orders/{change_id}/approve:
    post:
      operationId: approveChangeOrder
      summary: Zatwierdź Change Order
      tags: [changes]
      x-required-role: BOME_REVIEWER
      parameters:
        - name: change_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [decision]
              properties:
                decision: { type: string, enum: [APPROVED, REJECTED] }
                comment: { type: string }
      responses:
        "200":
          description: Decyzja zapisana

  # ── Versions ──────────────────────────────────────────────────────────────
  /boms/{bom_id}/versions:
    get:
      operationId: listBOMVersions
      summary: Historia wersji BOM
      tags: [versions]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          description: Lista wersji
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/BOMVersion' }

  /boms/{bom_id}/versions/{version_number}:
    get:
      operationId: getBOMVersion
      summary: Pobierz konkretną wersję BOM (snapshot)
      tags: [versions]
      x-required-role: BOME_VIEWER
      parameters:
        - name: bom_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: version_number
          in: path
          required: true
          schema: { type: integer }
      responses:
        "200":
          description: Snapshot BOM
          content:
            application/json:
              schema: { $ref: '#/components/schemas/BOMVersion' }

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    BOMCreate:
      type: object
      required: [product_code, bom_type]
      properties:
        product_code: { type: string, maxLength: 50 }
        product_description: { type: string, maxLength: 255 }
        revision: { type: string, default: "A", maxLength: 10 }
        bom_type:
          type: string
          enum: [ENGINEERING, MANUFACTURING, SERVICE, COSTING, PLANNING]
        production_location: { type: string, maxLength: 5 }
        annual_volume: { type: integer }
        cad_document_id: { type: string }
        source_bom_id: { type: string, format: uuid }

    BOMUpdate:
      type: object
      properties:
        product_description: { type: string }
        production_location: { type: string }
        annual_volume: { type: integer }
        effective_from: { type: string, format: date }
        effective_to: { type: string, format: date }
        change_order_id: { type: string, format: uuid }

    BOMHeader:
      type: object
      properties:
        bom_id: { type: string, format: uuid }
        product_code: { type: string }
        revision: { type: string }
        bom_type: { type: string }
        status: { type: string }
        effective_from: { type: string, format: date }
        effective_to: { type: string, format: date }
        production_location: { type: string }
        annual_volume: { type: integer }
        total_lines: { type: integer }
        released_by: { type: string }
        released_at: { type: string, format: date-time }
        created_at: { type: string, format: date-time }
        updated_at: { type: string, format: date-time }

    BOMSummary:
      type: object
      properties:
        bom_id: { type: string, format: uuid }
        product_code: { type: string }
        revision: { type: string }
        bom_type: { type: string }
        status: { type: string }
        total_lines: { type: integer }
        unique_components: { type: integer }
        total_cost_eur: { type: number }
        rollup_confidence: { type: number }
        updated_at: { type: string, format: date-time }

    BOMLineCreate:
      type: object
      required: [item_code, quantity, uom]
      properties:
        parent_line_id: { type: string, format: uuid }
        position: { type: integer, default: 10 }
        item_code: { type: string }
        quantity: { type: number, exclusiveMinimum: 0 }
        uom: { type: string }
        line_type: { type: string }
        scrap_factor_pct: { type: number, minimum: 0, exclusiveMaximum: 100 }
        make_or_buy: { type: string, enum: [MAKE, BUY, CONSIGNED] }
        phantom: { type: boolean, default: false }
        critical_item: { type: boolean, default: false }
        reference_designator: { type: string }
        cost_override_eur: { type: number }
        change_order_id: { type: string, format: uuid }

    BOMLine:
      allOf:
        - $ref: '#/components/schemas/BOMLineCreate'
        - type: object
          properties:
            line_id: { type: string, format: uuid }
            bom_id: { type: string, format: uuid }
            item_description: { type: string }
            effective_quantity: { type: number }
            rolled_unit_cost_eur: { type: number }
            created_at: { type: string, format: date-time }

    BOMLineUpdate:
      type: object
      properties:
        quantity: { type: number }
        scrap_factor_pct: { type: number }
        cost_override_eur: { type: number }
        critical_item: { type: boolean }
        make_or_buy: { type: string }
        change_order_id: { type: string, format: uuid }

    BOMNode:
      type: object
      properties:
        line: { $ref: '#/components/schemas/BOMLine' }
        depth: { type: integer }
        path: { type: string }
        children:
          type: array
          items: { $ref: '#/components/schemas/BOMNode' }

    IndentedBOMLine:
      type: object
      properties:
        depth: { type: integer }
        indent: { type: string }
        position: { type: integer }
        item_code: { type: string }
        description: { type: string }
        quantity: { type: number }
        uom: { type: string }
        line_type: { type: string }
        make_or_buy: { type: string }
        unit_cost_eur: { type: number }
        total_cost_eur: { type: number }

    BOMRollupResult:
      type: object
      properties:
        bom_id: { type: string }
        product_code: { type: string }
        volume: { type: integer }
        material_cost_eur: { type: number }
        process_cost_eur: { type: number }
        overhead_cost_eur: { type: number }
        tooling_amortization_eur: { type: number }
        total_cost_eur: { type: number }
        material_pct: { type: number }
        process_pct: { type: number }
        rollup_confidence: { type: number }
        warnings: { type: array, items: { type: string } }
        rolled_at: { type: string, format: date-time }

    BOMDiff:
      type: object
      properties:
        bom_id_a: { type: string }
        bom_id_b: { type: string }
        added_lines: { type: array, items: { $ref: '#/components/schemas/BOMLine' } }
        removed_lines: { type: array, items: { $ref: '#/components/schemas/BOMLine' } }
        changed_lines:
          type: array
          items:
            type: object
            properties:
              line_a: { $ref: '#/components/schemas/BOMLine' }
              line_b: { $ref: '#/components/schemas/BOMLine' }
              delta_fields: { type: array, items: { type: string } }
        cost_delta_eur: { type: number }

    MaterialSubstitute:
      type: object
      properties:
        substitute_id: { type: string, format: uuid }
        substitute_item_code: { type: string }
        priority: { type: integer }
        interchangeable: { type: boolean }
        quantity_factor: { type: number }
        cost_delta_pct: { type: number }
        approval_status: { type: string }
        valid_until: { type: string, format: date }

    SubstitutionRequest:
      type: object
      required: [original_item_code, substitute_item_code, reason]
      properties:
        original_item_code: { type: string }
        substitute_item_code: { type: string }
        reason: { type: string }
        affected_bom_ids: { type: array, items: { type: string, format: uuid } }
        valid_from: { type: string, format: date }
        valid_until: { type: string, format: date }
        quantity_factor: { type: number, default: 1.0 }
        process_change_required: { type: boolean, default: false }
        notes: { type: string }

    SubstitutionEvaluation:
      type: object
      properties:
        request_id: { type: string, format: uuid }
        original_cost_eur: { type: number }
        substitute_cost_eur: { type: number }
        cost_delta_eur: { type: number }
        cost_delta_pct: { type: number }
        is_interchangeable: { type: boolean }
        can_substitute: { type: boolean }
        warnings: { type: array, items: { type: string } }
        blocking_issues: { type: array, items: { type: string } }

    WhereUsedEntry:
      type: object
      properties:
        bom_id: { type: string, format: uuid }
        product_code: { type: string }
        revision: { type: string }
        bom_type: { type: string }
        status: { type: string }
        quantity: { type: number }
        uom: { type: string }

    ChangeOrderCreate:
      type: object
      required: [change_type, title, requester]
      properties:
        change_type: { type: string, enum: [INITIAL_RELEASE, ECO, MCO, DCO, DEVIATION, CORRECTION] }
        title: { type: string, maxLength: 255 }
        description: { type: string }
        affected_bom_ids: { type: array, items: { type: string, format: uuid } }
        affected_item_codes: { type: array, items: { type: string } }
        requester: { type: string }
        implementation_date: { type: string, format: date }
        risk_level: { type: string, enum: [LOW, MEDIUM, HIGH, CRITICAL], default: MEDIUM }

    ChangeOrder:
      allOf:
        - $ref: '#/components/schemas/ChangeOrderCreate'
        - type: object
          properties:
            change_id: { type: string, format: uuid }
            change_number: { type: string }
            status: { type: string }
            owner: { type: string }
            cost_impact_eur: { type: number }
            approval_chain: { type: array, items: { type: object } }
            created_at: { type: string, format: date-time }
            updated_at: { type: string, format: date-time }

    BOMVersion:
      type: object
      properties:
        version_id: { type: string, format: uuid }
        bom_id: { type: string, format: uuid }
        version_number: { type: integer }
        status_at_snapshot: { type: string }
        snapshot: { type: object }
        comment: { type: string }
        created_by: { type: string }
        created_at: { type: string, format: date-time }
```

### 7.2 RBAC

| Rola | Uprawnienia |
|------|-------------|
| `BOME_VIEWER` | GET bom, tree, indented, where-used, substitutes, versions, change orders |
| `BOME_ENGINEER` | BOME_VIEWER + CREATE/UPDATE bom headers, lines, substitution requests, change orders |
| `BOME_ANALYST` | BOME_VIEWER + cost roll-up, compare, export, variant configuration |
| `BOME_REVIEWER` | BOME_ANALYST + approve change orders, approve substitutions, change BOM status |
| `BOME_PROCUREMENT` | BOME_VIEWER + manage material substitutes, manage material master |
| `BOME_ADMIN` | Pełny dostęp + DELETE + manage families/variants, release frozen BOMs |

---

## 8. Event Model

### 8.1 Tematy Kafka

| Temat | Producent | Konsumenci | Retention |
|-------|-----------|------------|-----------|
| `bome.bom.created` | BOME API | Monitoring, Dashboard | 30 dni |
| `bome.bom.released` | BOME API | CEE, CLS, ERP Connector, Monitoring | 90 dni |
| `bome.bom.updated` | BOME API | CEE (invalidate cache), Monitoring | 30 dni |
| `bome.bom.obsoleted` | BOME API | CEE, ERP Connector, Monitoring | 90 dni |
| `bome.cost.rolled_up` | BOME Cost Worker | CEE, MIE, Dashboard, DWH | 30 dni |
| `bome.substitution.approved` | BOME API | CEE (price update), RFQA, Monitoring | 90 dni |
| `bome.change_order.approved` | BOME API | ERP Connector, Monitoring, Slack | 90 dni |
| `bome.change_order.implemented` | BOME API | ERP Connector, Monitoring | 90 dni |
| `bome.material.price_updated` | Price Service | BOME Cost Worker (invalidate rollup) | 7 dni |
| `bome.outbox.relay` | BOME Outbox Worker | — (relay) | 1 dzień |

### 8.2 Avro Schemas

```json
// bome.bom.released
{
  "namespace": "com.industrial_cost.bome",
  "type": "record",
  "name": "BOMReleased",
  "fields": [
    {"name": "bom_id",           "type": "string"},
    {"name": "product_code",     "type": "string"},
    {"name": "revision",         "type": "string"},
    {"name": "bom_type",         "type": "string"},
    {"name": "production_location", "type": ["null","string"], "default": null},
    {"name": "annual_volume",    "type": ["null","int"],    "default": null},
    {"name": "change_order_id",  "type": ["null","string"], "default": null},
    {"name": "released_by",      "type": "string"},
    {"name": "released_at",      "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// bome.cost.rolled_up
{
  "namespace": "com.industrial_cost.bome",
  "type": "record",
  "name": "BOMCostRolledUp",
  "fields": [
    {"name": "bom_id",                    "type": "string"},
    {"name": "product_code",              "type": "string"},
    {"name": "revision",                  "type": "string"},
    {"name": "volume",                    "type": "int"},
    {"name": "material_cost_eur",         "type": "double"},
    {"name": "process_cost_eur",          "type": "double"},
    {"name": "overhead_cost_eur",         "type": "double"},
    {"name": "tooling_amortization_eur",  "type": "double"},
    {"name": "total_cost_eur",            "type": "double"},
    {"name": "rollup_confidence",         "type": "double"},
    {"name": "warnings_count",            "type": "int"},
    {"name": "rolled_at",                 "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// bome.substitution.approved
{
  "namespace": "com.industrial_cost.bome",
  "type": "record",
  "name": "SubstitutionApproved",
  "fields": [
    {"name": "request_id",             "type": "string"},
    {"name": "original_item_code",     "type": "string"},
    {"name": "substitute_item_code",   "type": "string"},
    {"name": "reason",                 "type": "string"},
    {"name": "quantity_factor",        "type": "double"},
    {"name": "cost_delta_pct",         "type": ["null","double"], "default": null},
    {"name": "affected_bom_ids",       "type": {"type": "array", "items": "string"}, "default": []},
    {"name": "approved_by",            "type": "string"},
    {"name": "approved_at",            "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}

// bome.change_order.approved
{
  "namespace": "com.industrial_cost.bome",
  "type": "record",
  "name": "ChangeOrderApproved",
  "fields": [
    {"name": "change_id",          "type": "string"},
    {"name": "change_number",      "type": "string"},
    {"name": "change_type",        "type": "string"},
    {"name": "title",              "type": "string"},
    {"name": "risk_level",         "type": "string"},
    {"name": "affected_bom_ids",   "type": {"type": "array", "items": "string"}, "default": []},
    {"name": "cost_impact_eur",    "type": ["null","double"], "default": null},
    {"name": "implementation_date","type": ["null","string"], "default": null},
    {"name": "approved_by",        "type": "string"},
    {"name": "approved_at",        "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

---

## 9. Version Control

### 9.1 Model wersjonowania

```
BOM Version Control — dwa poziomy:

1. Revision (litera/numer)  — formalna rewizja produktu
   HOUSING-001 rev.A → rev.B → rev.C
   Każda rewizja = nowy rekord w bom_headers

2. Snapshot version (integer) — auto-snapshot przy każdej zmianie statusu
   BOM rev.B: v1 (DRAFT), v2 (IN_REVIEW), v3 (RELEASED), v4 (FROZEN)
   Snapshot = pełna kopia JSONB (header + lines)
```

### 9.2 BOMVersionControl

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc
logger = logging.getLogger(__name__)


@dataclass
class BOMDiffEntry:
    field: str
    old_value: object
    new_value: object


@dataclass
class BOMLineDiff:
    item_code: str
    action: str           # ADDED / REMOVED / CHANGED
    changes: list[BOMDiffEntry] = field(default_factory=list)


@dataclass
class BOMDiff:
    bom_id_a: str
    bom_id_b: str
    revision_a: str
    revision_b: str
    header_changes: list[BOMDiffEntry]
    line_diffs: list[BOMLineDiff]
    cost_delta_eur: Optional[float]
    lines_added: int
    lines_removed: int
    lines_changed: int


class BOMVersionControl:
    """
    Zarządza historią wersji BOM, diffowaniem i przywracaniem.
    Snapshot jest tworzony automatycznie przez trigger przy zmianie statusu.
    """

    def __init__(self, db_pool):
        self.db = db_pool

    async def list_versions(self, bom_id: str) -> list[dict]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    bv.version_id,
                    bv.version_number,
                    bv.status_at_snapshot,
                    bv.comment,
                    bv.created_by,
                    bv.created_at,
                    co.change_number,
                    co.change_type
                FROM bome.bom_versions bv
                LEFT JOIN bome.change_orders co ON co.change_id = bv.change_order_id
                WHERE bv.bom_id = $1
                ORDER BY bv.version_number DESC
                """,
                bom_id,
            )
        return [dict(r) for r in rows]

    async def get_snapshot(self, bom_id: str, version_number: int) -> dict:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT snapshot, status_at_snapshot, created_by, created_at
                FROM bome.bom_versions
                WHERE bom_id=$1 AND version_number=$2
                """,
                bom_id, version_number,
            )
        if not row:
            raise ValueError(f"Version {version_number} not found for BOM {bom_id}")
        return {
            "snapshot": dict(row["snapshot"]),
            "status": row["status_at_snapshot"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat(),
        }

    async def diff_versions(
        self,
        bom_id_a: str,
        version_a: Optional[int],
        bom_id_b: str,
        version_b: Optional[int],
    ) -> BOMDiff:
        """Porównaj dwa snapshoty BOM."""
        snap_a = await self._load_snapshot(bom_id_a, version_a)
        snap_b = await self._load_snapshot(bom_id_b, version_b)

        header_a = snap_a["header"]
        header_b = snap_b["header"]
        lines_a = {l["item_code"]: l for l in (snap_a.get("lines") or [])}
        lines_b = {l["item_code"]: l for l in (snap_b.get("lines") or [])}

        # Header diff
        COMPARE_FIELDS = [
            "product_code", "revision", "status", "production_location",
            "annual_volume", "base_quantity",
        ]
        header_changes = [
            BOMDiffEntry(f, header_a.get(f), header_b.get(f))
            for f in COMPARE_FIELDS
            if header_a.get(f) != header_b.get(f)
        ]

        # Lines diff
        all_items = set(lines_a) | set(lines_b)
        line_diffs: list[BOMLineDiff] = []
        for item in sorted(all_items):
            if item in lines_a and item not in lines_b:
                line_diffs.append(BOMLineDiff(item_code=item, action="REMOVED"))
            elif item not in lines_a and item in lines_b:
                line_diffs.append(BOMLineDiff(item_code=item, action="ADDED"))
            else:
                la, lb = lines_a[item], lines_b[item]
                changes = [
                    BOMDiffEntry(f, la.get(f), lb.get(f))
                    for f in ["quantity", "uom", "scrap_factor_pct", "make_or_buy", "cost_override_eur"]
                    if la.get(f) != lb.get(f)
                ]
                if changes:
                    line_diffs.append(BOMLineDiff(item_code=item, action="CHANGED", changes=changes))

        # Cost delta
        cost_a = snap_a.get("_rolled_cost")
        cost_b = snap_b.get("_rolled_cost")
        cost_delta = (cost_b - cost_a) if (cost_a and cost_b) else None

        return BOMDiff(
            bom_id_a=bom_id_a,
            bom_id_b=bom_id_b,
            revision_a=header_a.get("revision", "?"),
            revision_b=header_b.get("revision", "?"),
            header_changes=header_changes,
            line_diffs=line_diffs,
            cost_delta_eur=cost_delta,
            lines_added=sum(1 for d in line_diffs if d.action == "ADDED"),
            lines_removed=sum(1 for d in line_diffs if d.action == "REMOVED"),
            lines_changed=sum(1 for d in line_diffs if d.action == "CHANGED"),
        )

    async def create_new_revision(
        self,
        source_bom_id: str,
        new_revision: str,
        change_order_id: str,
        created_by: str,
        comment: Optional[str] = None,
    ) -> str:
        """
        Tworzy nową rewizję BOM na bazie istniejącej.
        Kopiuje header + wszystkie aktywne linie.
        Nowa rewizja startuje w statusie DRAFT.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Pobierz source header
                source = await conn.fetchrow(
                    "SELECT * FROM bome.bom_headers WHERE bom_id=$1", source_bom_id
                )
                if not source:
                    raise ValueError(f"Source BOM {source_bom_id} not found")

                # Sprawdź czy rewizja już istnieje
                existing = await conn.fetchval(
                    """
                    SELECT bom_id FROM bome.bom_headers
                    WHERE product_code=$1 AND revision=$2 AND bom_type=$3
                    """,
                    source["product_code"], new_revision, source["bom_type"],
                )
                if existing:
                    raise ValueError(
                        f"Revision {new_revision} already exists for {source['product_code']}"
                    )

                # Utwórz nowy header
                new_bom_id = await conn.fetchval(
                    """
                    INSERT INTO bome.bom_headers (
                        product_code, product_description, revision, bom_type,
                        status, effective_from, production_location, annual_volume,
                        base_quantity, currency, source_bom_id, cad_document_id,
                        change_order_id, created_by
                    ) VALUES ($1,$2,$3,$4,'DRAFT',CURRENT_DATE,$5,$6,$7,$8,$9,$10,$11,$12)
                    RETURNING bom_id
                    """,
                    source["product_code"], source["product_description"],
                    new_revision, source["bom_type"],
                    source["production_location"], source["annual_volume"],
                    source["base_quantity"], source["currency"],
                    source_bom_id, source["cad_document_id"],
                    change_order_id, created_by,
                )

                # Kopiuj linie (rekurencyjnie, zachowując hierarchię)
                await self._copy_lines(conn, source_bom_id, str(new_bom_id))

                # Oznacz poprzednią rewizję jako SUPERSEDED
                await conn.execute(
                    """
                    UPDATE bome.bom_headers
                    SET status = 'SUPERSEDED', superseded_by_bom_id = $1, updated_at = NOW()
                    WHERE bom_id = $2
                    """,
                    new_bom_id, source_bom_id,
                )

        logger.info(
            "Created revision %s for %s (new bom_id=%s)",
            new_revision, source["product_code"], new_bom_id,
        )
        return str(new_bom_id)

    async def _copy_lines(
        self,
        conn,
        source_bom_id: str,
        target_bom_id: str,
    ) -> dict[str, str]:
        """Kopiuje linie, zachowując hierarchię parent-child. Zwraca mapę old→new line_id."""
        rows = await conn.fetch(
            "SELECT * FROM bome.bom_lines WHERE bom_id=$1 AND is_active=TRUE ORDER BY position",
            source_bom_id,
        )
        id_map: dict[str, str] = {}

        for row in rows:
            new_line_id = await conn.fetchval(
                """
                INSERT INTO bome.bom_lines (
                    bom_id, parent_line_id, position, item_code, item_description,
                    line_type, quantity, uom, scrap_factor_pct, make_or_buy,
                    phantom, critical_item, lead_time_days, reference_designator,
                    find_number, cost_override_eur, cad_reference,
                    added_in_revision, created_by
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                RETURNING line_id
                """,
                target_bom_id,
                id_map.get(str(row["parent_line_id"])) if row["parent_line_id"] else None,
                row["position"], row["item_code"], row["item_description"],
                row["line_type"], row["quantity"], row["uom"], row["scrap_factor_pct"],
                row["make_or_buy"], row["phantom"], row["critical_item"],
                row["lead_time_days"], row["reference_designator"], row["find_number"],
                row["cost_override_eur"], row["cad_reference"],
                None, "system",
            )
            id_map[str(row["line_id"])] = str(new_line_id)

        return id_map

    async def _load_snapshot(self, bom_id: str, version: Optional[int]) -> dict:
        if version is not None:
            data = await self.get_snapshot(bom_id, version)
            return data["snapshot"]
        # Brak wersji → aktualny stan
        async with self.db.acquire() as conn:
            header = await conn.fetchrow(
                "SELECT * FROM bome.bom_headers WHERE bom_id=$1", bom_id
            )
            lines = await conn.fetch(
                "SELECT * FROM bome.bom_lines WHERE bom_id=$1 AND is_active=TRUE ORDER BY position",
                bom_id,
            )
        return {
            "header": dict(header),
            "lines": [dict(l) for l in lines],
        }
```
