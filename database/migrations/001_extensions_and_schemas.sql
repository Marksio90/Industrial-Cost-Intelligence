-- =============================================================================
-- MIGRATION 001: Extensions, schemas, roles
-- PostgreSQL 15+  |  pgvector 0.7+
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";          -- uuid_generate_v4()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";            -- gen_random_uuid(), pgp_sym_encrypt
CREATE EXTENSION IF NOT EXISTS "vector";              -- pgvector
CREATE EXTENSION IF NOT EXISTS "pg_trgm";             -- trigram GIN indexes for ILIKE
CREATE EXTENSION IF NOT EXISTS "btree_gin";           -- GIN on scalar types
CREATE EXTENSION IF NOT EXISTS "pg_partman";          -- partition management (optional)
CREATE EXTENSION IF NOT EXISTS "tablefunc";           -- crosstab pivot queries
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";  -- query performance tracking

-- ---------------------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------------------
-- ici          : core business tables (multi-tenant, partitioned)
-- ici_vectors  : pgvector embedding tables
-- ici_audit    : immutable audit log (append-only)
-- ici_archive  : cold-storage for expired partitions
CREATE SCHEMA IF NOT EXISTS ici;
CREATE SCHEMA IF NOT EXISTS ici_vectors;
CREATE SCHEMA IF NOT EXISTS ici_audit;
CREATE SCHEMA IF NOT EXISTS ici_archive;

-- ---------------------------------------------------------------------------
-- Application roles (principle of least privilege)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- API service account — DML on ici + ici_vectors, read on ici_audit
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ici_app') THEN
        CREATE ROLE ici_app LOGIN PASSWORD 'change_in_production';
    END IF;
    -- Read-only analytics / BI
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ici_readonly') THEN
        CREATE ROLE ici_readonly;
    END IF;
    -- Migration runner — full DDL
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ici_migrator') THEN
        CREATE ROLE ici_migrator LOGIN PASSWORD 'change_in_production';
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA ici         TO ici_app, ici_readonly;
GRANT USAGE ON SCHEMA ici_vectors TO ici_app, ici_readonly;
GRANT USAGE ON SCHEMA ici_audit   TO ici_readonly;
GRANT ALL   ON SCHEMA ici         TO ici_migrator;
GRANT ALL   ON SCHEMA ici_vectors TO ici_migrator;
GRANT ALL   ON SCHEMA ici_audit   TO ici_migrator;
GRANT ALL   ON SCHEMA ici_archive TO ici_migrator;
