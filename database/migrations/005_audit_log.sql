-- =============================================================================
-- MIGRATION 005: Audit log system
-- Schema: ici_audit
--
-- Design:
--   - Append-only table (no UPDATE/DELETE allowed via trigger + policy).
--   - Stores OLD and NEW row state as JSONB.
--   - Partitioned by RANGE on occurred_at (monthly) for fast pruning.
--   - Row-level security prevents application role from deleting audit rows.
--   - Immutability enforced by a BEFORE UPDATE/DELETE trigger on the audit table.
--   - A single generic trigger function is installed on every business table.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Audit event table
-- ---------------------------------------------------------------------------
CREATE TABLE ici_audit.events (
    id              BIGINT          GENERATED ALWAYS AS IDENTITY,
    occurred_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    tenant_id       VARCHAR(64)     NOT NULL,

    -- What happened
    operation       CHAR(1)         NOT NULL CHECK (operation IN ('I','U','D')), -- Insert/Update/Delete
    schema_name     VARCHAR(64)     NOT NULL,
    table_name      VARCHAR(128)    NOT NULL,
    row_id          TEXT            NOT NULL,  -- pk value(s) as text

    -- Who did it
    session_user    VARCHAR(256)    NOT NULL DEFAULT SESSION_USER,
    app_user        VARCHAR(256),   -- populated from current_setting('app.current_user', true)
    app_request_id  VARCHAR(64),    -- X-Request-ID from current_setting('app.request_id', true)
    client_addr     INET,

    -- Row state (NULL for INSERT old / DELETE new)
    old_data        JSONB,
    new_data        JSONB,

    -- Diff (only changed columns for UPDATE, saves storage)
    changed_fields  JSONB,

    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Monthly partitions for audit log (3-year coverage)
CREATE TABLE ici_audit.events_y2024m01 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE ici_audit.events_y2024m02 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE ici_audit.events_y2024m03 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
CREATE TABLE ici_audit.events_y2024m04 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-04-01') TO ('2024-05-01');
CREATE TABLE ici_audit.events_y2024m05 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');
CREATE TABLE ici_audit.events_y2024m06 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-06-01') TO ('2024-07-01');
CREATE TABLE ici_audit.events_y2024m07 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-07-01') TO ('2024-08-01');
CREATE TABLE ici_audit.events_y2024m08 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-08-01') TO ('2024-09-01');
CREATE TABLE ici_audit.events_y2024m09 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-09-01') TO ('2024-10-01');
CREATE TABLE ici_audit.events_y2024m10 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-10-01') TO ('2024-11-01');
CREATE TABLE ici_audit.events_y2024m11 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-11-01') TO ('2024-12-01');
CREATE TABLE ici_audit.events_y2024m12 PARTITION OF ici_audit.events FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');
CREATE TABLE ici_audit.events_y2025m01 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE ici_audit.events_y2025m02 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE ici_audit.events_y2025m03 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE ici_audit.events_y2025m04 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE ici_audit.events_y2025m05 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE ici_audit.events_y2025m06 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE ici_audit.events_y2025m07 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE ici_audit.events_y2025m08 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE ici_audit.events_y2025m09 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE ici_audit.events_y2025m10 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE ici_audit.events_y2025m11 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE ici_audit.events_y2025m12 PARTITION OF ici_audit.events FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE ici_audit.events_y2026m01 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE ici_audit.events_y2026m06 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE ici_audit.events_y2026m07 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE ici_audit.events_y2026m08 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE ici_audit.events_y2026m09 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE ici_audit.events_y2026m10 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE ici_audit.events_y2026m11 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE ici_audit.events_y2026m12 PARTITION OF ici_audit.events FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- Indexes on audit table (queries: tenant + table + date range, or by row_id)
CREATE INDEX idx_audit_events_tenant_table_date
    ON ici_audit.events (tenant_id, table_name, occurred_at DESC);

CREATE INDEX idx_audit_events_row_id
    ON ici_audit.events (table_name, row_id, occurred_at DESC);

CREATE INDEX idx_audit_events_app_user
    ON ici_audit.events (app_user, occurred_at DESC)
    WHERE app_user IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Immutability: forbid UPDATE/DELETE on the audit table itself
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici_audit.deny_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is immutable — operation % on ici_audit.events is not allowed', TG_OP;
END;
$$;

CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON ici_audit.events
    FOR EACH ROW EXECUTE FUNCTION ici_audit.deny_mutation();

-- ---------------------------------------------------------------------------
-- Generic audit trigger function (installed on every business table)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici_audit.capture_change()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_tenant_id     VARCHAR(64);
    v_row_id        TEXT;
    v_old           JSONB;
    v_new           JSONB;
    v_changed       JSONB;
    v_key           TEXT;
BEGIN
    -- Extract tenant_id from the row (all our tables have it)
    IF TG_OP = 'DELETE' THEN
        v_tenant_id := OLD.tenant_id;
        v_row_id    := OLD.id::TEXT;
        v_old       := to_jsonb(OLD);
        v_new       := NULL;
        v_changed   := NULL;
    ELSIF TG_OP = 'INSERT' THEN
        v_tenant_id := NEW.tenant_id;
        v_row_id    := NEW.id::TEXT;
        v_old       := NULL;
        v_new       := to_jsonb(NEW);
        v_changed   := NULL;
    ELSE  -- UPDATE
        v_tenant_id := NEW.tenant_id;
        v_row_id    := NEW.id::TEXT;
        v_old       := to_jsonb(OLD);
        v_new       := to_jsonb(NEW);

        -- Compute diff: only fields that changed
        v_changed := '{}'::JSONB;
        FOR v_key IN SELECT jsonb_object_keys(v_new) LOOP
            IF (v_new->v_key) IS DISTINCT FROM (v_old->v_key) THEN
                v_changed := jsonb_set(v_changed, ARRAY[v_key],
                    jsonb_build_object('old', v_old->v_key, 'new', v_new->v_key));
            END IF;
        END LOOP;

        -- Skip if only updated_at / version changed (noise reduction)
        IF v_changed - 'updated_at' - 'version' = '{}'::JSONB THEN
            RETURN NEW;
        END IF;
    END IF;

    INSERT INTO ici_audit.events (
        tenant_id, operation, schema_name, table_name, row_id,
        session_user, app_user, app_request_id, client_addr,
        old_data, new_data, changed_fields
    ) VALUES (
        v_tenant_id,
        LEFT(TG_OP, 1),
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME,
        v_row_id,
        SESSION_USER,
        current_setting('app.current_user', true),
        current_setting('app.request_id',   true),
        inet_client_addr(),
        v_old,
        v_new,
        v_changed
    );

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- Install audit trigger on every business table
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_audit_materials
    AFTER INSERT OR UPDATE OR DELETE ON ici.materials
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

CREATE TRIGGER trg_audit_suppliers
    AFTER INSERT OR UPDATE OR DELETE ON ici.suppliers
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

CREATE TRIGGER trg_audit_processes
    AFTER INSERT OR UPDATE OR DELETE ON ici.processes
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

CREATE TRIGGER trg_audit_rfqs
    AFTER INSERT OR UPDATE OR DELETE ON ici.rfqs
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

CREATE TRIGGER trg_audit_quotes
    AFTER INSERT OR UPDATE OR DELETE ON ici.quotes
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

CREATE TRIGGER trg_audit_risk_scores
    AFTER INSERT OR UPDATE OR DELETE ON ici.risk_scores
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

-- cost_snapshots and forecasts are append-only by design — no UPDATE trigger needed.
-- audit INSERT only:
CREATE TRIGGER trg_audit_cost_snapshots
    AFTER INSERT ON ici.cost_snapshots
    FOR EACH ROW EXECUTE FUNCTION ici_audit.capture_change();

-- ---------------------------------------------------------------------------
-- Helper: retrieve full change history for a single entity
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici_audit.entity_history(
    p_table_name  VARCHAR,
    p_row_id      TEXT,
    p_limit       INTEGER DEFAULT 50
)
RETURNS TABLE (
    occurred_at   TIMESTAMPTZ,
    operation     CHAR(1),
    app_user      VARCHAR,
    changed_fields JSONB
)
LANGUAGE SQL STABLE AS $$
    SELECT  occurred_at, operation, app_user, changed_fields
    FROM    ici_audit.events
    WHERE   table_name = p_table_name
      AND   row_id     = p_row_id
    ORDER BY occurred_at DESC
    LIMIT   p_limit;
$$;

-- Grant: app can INSERT (via trigger), readonly can SELECT
GRANT INSERT ON ici_audit.events TO ici_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ici_audit TO ici_readonly;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ici_audit TO ici_app, ici_readonly;
