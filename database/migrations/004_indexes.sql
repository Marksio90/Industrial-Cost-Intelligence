-- =============================================================================
-- MIGRATION 004: Index strategy
--
-- Design rules:
--   1. Every FK column gets a B-tree index.
--   2. High-cardinality filter columns (tenant_id + status) get composite B-tree.
--   3. Free-text search columns get GIN trigram index.
--   4. JSONB metadata columns (future) will get GIN jsonb_path_ops.
--   5. Vector indexes are in migration 003.
--   6. Partial indexes exclude soft-deleted rows (is_deleted = FALSE).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- MATERIALS
-- ---------------------------------------------------------------------------
-- Primary access pattern: tenant + status filter (list endpoint)
CREATE INDEX CONCURRENTLY idx_materials_tenant_status
    ON ici.materials (tenant_id, status)
    WHERE is_deleted = FALSE;

-- Price range queries for cost analysis
CREATE INDEX CONCURRENTLY idx_materials_tenant_price
    ON ici.materials (tenant_id, price_eur)
    WHERE is_deleted = FALSE;

-- Free-text search on name + description
CREATE INDEX CONCURRENTLY idx_materials_name_trgm
    ON ici.materials
    USING GIN (name gin_trgm_ops);

CREATE INDEX CONCURRENTLY idx_materials_description_trgm
    ON ici.materials
    USING GIN (description gin_trgm_ops);

-- Material class filter (enum, low cardinality — composite with tenant)
CREATE INDEX CONCURRENTLY idx_materials_tenant_class
    ON ici.materials (tenant_id, material_class)
    WHERE is_deleted = FALSE;

-- Lead time analysis
CREATE INDEX CONCURRENTLY idx_materials_lead_time
    ON ici.materials (tenant_id, lead_time_days)
    WHERE is_deleted = FALSE AND status = 'ACTIVE';

-- FK to preferred supplier
CREATE INDEX CONCURRENTLY idx_materials_supplier_id
    ON ici.materials (supplier_id)
    WHERE supplier_id IS NOT NULL AND is_deleted = FALSE;

-- ---------------------------------------------------------------------------
-- SUPPLIERS
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_suppliers_tenant_status
    ON ici.suppliers (tenant_id, status)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_suppliers_country
    ON ici.suppliers (tenant_id, country_code)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_suppliers_overall_score
    ON ici.suppliers (tenant_id, overall_score DESC NULLS LAST)
    WHERE is_deleted = FALSE AND status = 'QUALIFIED';

CREATE INDEX CONCURRENTLY idx_suppliers_name_trgm
    ON ici.suppliers
    USING GIN (name gin_trgm_ops);

-- GIN on multiple columns via btree_gin extension
CREATE INDEX CONCURRENTLY idx_suppliers_status_country_gin
    ON ici.suppliers
    USING GIN (tenant_id, status, country_code);

-- ---------------------------------------------------------------------------
-- PROCESSES
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_processes_tenant_type
    ON ici.processes (tenant_id, process_type)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_processes_tenant_active
    ON ici.processes (tenant_id, is_active)
    WHERE is_deleted = FALSE;

-- Cost range queries (hourly_cost_eur is a generated column)
CREATE INDEX CONCURRENTLY idx_processes_hourly_cost
    ON ici.processes (tenant_id, hourly_cost_eur)
    WHERE is_deleted = FALSE AND is_active = TRUE;

CREATE INDEX CONCURRENTLY idx_processes_name_trgm
    ON ici.processes
    USING GIN (name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- RFQs
-- ---------------------------------------------------------------------------
-- Most queries filter by tenant_id + status + created_at range
CREATE INDEX CONCURRENTLY idx_rfqs_tenant_status_created
    ON ici.rfqs (tenant_id, status, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_rfqs_tenant_deadline
    ON ici.rfqs (tenant_id, deadline)
    WHERE is_deleted = FALSE AND status IN ('DRAFT', 'SENT');

-- Awarded RFQ lookup
CREATE INDEX CONCURRENTLY idx_rfqs_awarded_supplier
    ON ici.rfqs (awarded_supplier_id)
    WHERE awarded_supplier_id IS NOT NULL AND is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_rfqs_title_trgm
    ON ici.rfqs
    USING GIN (title gin_trgm_ops);

-- RFQ Line Items
CREATE INDEX CONCURRENTLY idx_rfq_line_items_rfq_id
    ON ici.rfq_line_items (rfq_id);

CREATE INDEX CONCURRENTLY idx_rfq_line_items_material_id
    ON ici.rfq_line_items (material_id);

-- RFQ Suppliers
CREATE INDEX CONCURRENTLY idx_rfq_suppliers_rfq_id
    ON ici.rfq_suppliers (rfq_id);

CREATE INDEX CONCURRENTLY idx_rfq_suppliers_supplier_id
    ON ici.rfq_suppliers (supplier_id);

-- ---------------------------------------------------------------------------
-- QUOTES
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_quotes_tenant_status_created
    ON ici.quotes (tenant_id, status, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_quotes_rfq_id
    ON ici.quotes (rfq_id)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_quotes_supplier_id
    ON ici.quotes (supplier_id)
    WHERE is_deleted = FALSE;

-- Validity date for expiry jobs
CREATE INDEX CONCURRENTLY idx_quotes_validity_date
    ON ici.quotes (validity_date)
    WHERE is_deleted = FALSE AND status = 'RECEIVED';

-- Quote Line Items
CREATE INDEX CONCURRENTLY idx_quote_line_items_quote_id
    ON ici.quote_line_items (quote_id);

CREATE INDEX CONCURRENTLY idx_quote_line_items_material_id
    ON ici.quote_line_items (material_id);

-- ---------------------------------------------------------------------------
-- COST SNAPSHOTS
-- ---------------------------------------------------------------------------
-- Primary access: material + date range
CREATE INDEX CONCURRENTLY idx_cost_snapshots_material_date
    ON ici.cost_snapshots (tenant_id, material_id, snapshot_date DESC);

-- Source filter (ML_MODEL vs MANUAL)
CREATE INDEX CONCURRENTLY idx_cost_snapshots_source
    ON ici.cost_snapshots (tenant_id, source, snapshot_date DESC);

-- Total cost range (for benchmarking queries)
CREATE INDEX CONCURRENTLY idx_cost_snapshots_total_cost
    ON ici.cost_snapshots (tenant_id, total_cost_eur)
    WHERE total_cost_eur IS NOT NULL;

-- ---------------------------------------------------------------------------
-- FORECASTS
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_forecasts_material_status
    ON ici.forecasts (tenant_id, material_id, status, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_forecasts_type_horizon
    ON ici.forecasts (tenant_id, forecast_type, horizon)
    WHERE is_deleted = FALSE;

-- Forecast Data Points
CREATE INDEX CONCURRENTLY idx_forecast_data_points_forecast
    ON ici.forecast_data_points (forecast_id, period_date);

CREATE INDEX CONCURRENTLY idx_forecast_data_points_type
    ON ici.forecast_data_points (forecast_id, point_type);

-- ---------------------------------------------------------------------------
-- RISK SCORES
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_risk_scores_tenant_status
    ON ici.risk_scores (tenant_id, status, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_risk_scores_category
    ON ici.risk_scores (tenant_id, category, status)
    WHERE is_deleted = FALSE;

-- RPN descending — "show me highest risks"
CREATE INDEX CONCURRENTLY idx_risk_scores_rpn_desc
    ON ici.risk_scores (tenant_id, rpn DESC)
    WHERE is_deleted = FALSE AND status NOT IN ('RESOLVED', 'ACCEPTED');

-- Cross-entity lookups
CREATE INDEX CONCURRENTLY idx_risk_scores_material
    ON ici.risk_scores (affected_material_id)
    WHERE affected_material_id IS NOT NULL AND is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_risk_scores_supplier
    ON ici.risk_scores (affected_supplier_id)
    WHERE affected_supplier_id IS NOT NULL AND is_deleted = FALSE;

CREATE INDEX CONCURRENTLY idx_risk_scores_rfq
    ON ici.risk_scores (affected_rfq_id)
    WHERE affected_rfq_id IS NOT NULL AND is_deleted = FALSE;

-- Mitigation Actions
CREATE INDEX CONCURRENTLY idx_mitigation_actions_risk
    ON ici.mitigation_actions (risk_id);

CREATE INDEX CONCURRENTLY idx_mitigation_actions_incomplete
    ON ici.mitigation_actions (risk_id, due_date)
    WHERE completed_at IS NULL;

-- ---------------------------------------------------------------------------
-- Statistics update schedule note:
--   Run   ANALYZE ici.<table>;
--   after bulk loads. pg_autovacuum handles ongoing maintenance, but
--   set autovacuum_vacuum_scale_factor = 0.01 on partitioned tables.
-- ---------------------------------------------------------------------------
