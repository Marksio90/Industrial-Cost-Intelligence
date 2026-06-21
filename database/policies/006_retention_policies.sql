-- =============================================================================
-- RETENTION POLICIES
-- PostgreSQL 15+  —  manual partition detach + archive strategy.
--
-- Strategy per table family:
--   ici.rfqs            — keep HOT 2 years, ARCHIVE 3 years, DROP after 5 years
--   ici.quotes          — keep HOT 2 years, ARCHIVE 3 years, DROP after 5 years
--   ici.cost_snapshots  — keep HOT 3 years, ARCHIVE forever (regulatory)
--   ici.forecasts       — keep HOT 1 year,  DROP after 2 years
--   ici.risk_scores     — keep HOT 3 years, ARCHIVE 7 years (risk register)
--   ici_audit.events    — keep HOT 1 year,  ARCHIVE 6 years, DROP after 7 years
--
-- Execution: run by pg_cron or external scheduler (e.g., Argo CronWorkflow).
-- Archive: partition is detached from parent, re-attached under ici_archive
--          schema (still queryable), then S3-exported via pg_dump --table.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Retention configuration table (source of truth for the automation script)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ici.retention_config (
    id              SERIAL PRIMARY KEY,
    schema_name     VARCHAR(64)  NOT NULL,
    table_name      VARCHAR(128) NOT NULL,
    hot_months      INTEGER      NOT NULL,  -- keep in active partition
    archive_months  INTEGER,               -- move to ici_archive (NULL = never archive)
    drop_months     INTEGER,               -- drop entirely (NULL = never drop)
    notes           TEXT,
    CONSTRAINT uq_retention UNIQUE (schema_name, table_name)
);

INSERT INTO ici.retention_config (schema_name, table_name, hot_months, archive_months, drop_months, notes) VALUES
    ('ici',       'rfqs',           24, 36,  60,  'Standard procurement lifecycle'),
    ('ici',       'quotes',         24, 36,  60,  'Standard procurement lifecycle'),
    ('ici',       'cost_snapshots', 36, NULL, NULL,'Regulatory — keep forever'),
    ('ici',       'forecasts',      12, NULL, 24,  'ML artifacts, replace with retrain'),
    ('ici',       'risk_scores',    36, 84,  NULL,'Risk register — 7 year regulatory'),
    ('ici_audit', 'events',         12, 84,  84,  'SOX / GDPR 7-year minimum');

-- ---------------------------------------------------------------------------
-- Archive function: detach old partition → re-attach to ici_archive
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici.archive_partition(
    p_schema        VARCHAR,
    p_table         VARCHAR,
    p_partition     VARCHAR  -- exact partition table name, e.g. 'rfqs_y2022m01'
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    -- Detach from parent
    EXECUTE format('ALTER TABLE %I.%I DETACH PARTITION %I.%I',
        p_schema, p_table, p_schema, p_partition);

    -- Re-attach to archive schema (table must be moved first)
    EXECUTE format('ALTER TABLE %I.%I SET SCHEMA ici_archive', p_schema, p_partition);

    RAISE NOTICE 'Archived partition: %.% → ici_archive.%',
        p_schema, p_partition, p_partition;
END;
$$;

-- ---------------------------------------------------------------------------
-- Drop function: completely remove an archived partition
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici_archive.drop_partition(p_partition VARCHAR)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('DROP TABLE IF EXISTS ici_archive.%I', p_partition);
    RAISE NOTICE 'Dropped archived partition: ici_archive.%', p_partition;
END;
$$;

-- ---------------------------------------------------------------------------
-- Automated maintenance procedure (run monthly by pg_cron)
-- Checks retention_config and detaches/drops partitions past thresholds.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE ici.run_retention_maintenance()
LANGUAGE plpgsql AS $$
DECLARE
    r       RECORD;
    cutoff  DATE;
    part    RECORD;
BEGIN
    FOR r IN SELECT * FROM ici.retention_config LOOP
        -- Check for partitions to archive
        IF r.archive_months IS NOT NULL THEN
            cutoff := DATE_TRUNC('month', NOW() - (r.archive_months || ' months')::INTERVAL)::DATE;

            FOR part IN
                SELECT c.relname AS partition_name
                FROM   pg_inherits i
                JOIN   pg_class   c ON c.oid = i.inhrelid
                JOIN   pg_class   p ON p.oid = i.inhparent
                JOIN   pg_namespace ns ON ns.oid = p.relnamespace
                WHERE  ns.nspname = r.schema_name
                  AND  p.relname  = r.table_name
                  -- Extract year from partition name suffix e.g. rfqs_y2022m01
                  AND  TO_DATE(
                           SUBSTRING(c.relname FROM '[0-9]{4}m[0-9]{2}$'),
                           'YYYYmMM'
                       ) < cutoff
            LOOP
                PERFORM ici.archive_partition(r.schema_name, r.table_name, part.partition_name);
            END LOOP;
        END IF;

        -- Check for archived partitions to drop
        IF r.drop_months IS NOT NULL THEN
            cutoff := DATE_TRUNC('month', NOW() - (r.drop_months || ' months')::INTERVAL)::DATE;

            FOR part IN
                SELECT c.relname AS partition_name
                FROM   pg_class c
                JOIN   pg_namespace ns ON ns.oid = c.relnamespace
                WHERE  ns.nspname = 'ici_archive'
                  AND  c.relname LIKE r.table_name || '_%'
                  AND  TO_DATE(
                           SUBSTRING(c.relname FROM '[0-9]{4}m[0-9]{2}$'),
                           'YYYYmMM'
                       ) < cutoff
            LOOP
                PERFORM ici_archive.drop_partition(part.partition_name);
            END LOOP;
        END IF;
    END LOOP;

    RAISE NOTICE 'Retention maintenance completed at %', NOW();
END;
$$;

-- ---------------------------------------------------------------------------
-- pg_cron job (requires pg_cron extension + superuser)
-- Run on the 1st of each month at 02:00 UTC.
-- Uncomment when pg_cron is installed:
-- ---------------------------------------------------------------------------
-- SELECT cron.schedule('retention-maintenance', '0 2 1 * *', 'CALL ici.run_retention_maintenance()');

-- ---------------------------------------------------------------------------
-- GDPR: right-to-erasure helper
-- Soft-delete + audit-log anonymisation for a specific user.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici.gdpr_erase_user_data(
    p_tenant_id   VARCHAR,
    p_user_email  VARCHAR,
    p_requester   VARCHAR
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    -- Anonymise created_by / updated_by references in business tables
    UPDATE ici.materials SET created_by = '[REDACTED]', updated_by = '[REDACTED]'
    WHERE tenant_id = p_tenant_id AND (created_by = p_user_email OR updated_by = p_user_email);
    GET DIAGNOSTICS v_count = ROW_COUNT;

    UPDATE ici.suppliers SET created_by = '[REDACTED]', updated_by = '[REDACTED]'
    WHERE tenant_id = p_tenant_id AND (created_by = p_user_email OR updated_by = p_user_email);

    UPDATE ici.rfqs SET created_by = '[REDACTED]', updated_by = '[REDACTED]'
    WHERE tenant_id = p_tenant_id AND (created_by = p_user_email OR updated_by = p_user_email);

    UPDATE ici.quotes SET created_by = '[REDACTED]', updated_by = '[REDACTED]'
    WHERE tenant_id = p_tenant_id AND (created_by = p_user_email OR updated_by = p_user_email);

    -- Audit log: anonymise app_user (preserve the operation record itself)
    UPDATE ici_audit.events SET app_user = '[REDACTED]'
    WHERE tenant_id = p_tenant_id AND app_user = p_user_email;

    -- Log the erasure itself
    INSERT INTO ici_audit.events (
        tenant_id, operation, schema_name, table_name, row_id,
        session_user, app_user, new_data
    ) VALUES (
        p_tenant_id, 'U', 'ici', 'gdpr_erasure', p_user_email,
        SESSION_USER, p_requester,
        jsonb_build_object('erased_email', p_user_email, 'requested_by', p_requester, 'ts', NOW())
    );

    RETURN v_count;
END;
$$;

GRANT EXECUTE ON FUNCTION ici.archive_partition TO ici_migrator;
GRANT EXECUTE ON PROCEDURE ici.run_retention_maintenance TO ici_migrator;
GRANT EXECUTE ON FUNCTION ici.gdpr_erase_user_data TO ici_app;
