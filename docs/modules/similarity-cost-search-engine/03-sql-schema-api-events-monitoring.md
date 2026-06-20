# Similarity Cost Search Engine — Part 3
# SQL Schema, API, Events, Monitoring

---

## Section 11: SQL Schema

Full PostgreSQL 16 DDL for the `scse` schema. All tables are append-safe; physical deletes are
prohibited. Vector records are soft-deleted by setting `is_active = FALSE`.

### 11.1 Schema and Extensions

```sql
CREATE SCHEMA IF NOT EXISTS scse;

-- Required extensions (must be installed in PostgreSQL 16)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";       -- pgvector for fallback ANN search
```

### 11.2 ENUMs

```sql
CREATE TYPE scse.entity_type AS ENUM (
    'PRODUCT',
    'QUOTE',
    'MATERIAL',
    'PROCESS',
    'SUPPLIER'
);

CREATE TYPE scse.search_mode AS ENUM (
    'SEMANTIC',   -- dense text vector only
    'FEATURE',    -- dense fused vector (text + structured)
    'KEYWORD',    -- sparse BM25 only
    'HYBRID',     -- dense + sparse + RRF (default)
    'FILTERED',   -- dense + mandatory filters
    'RECOMMEND'   -- collaborative filtering via Qdrant recommend API
);

CREATE TYPE scse.confidence_grade AS ENUM (
    'HIGH',       -- overall >= 0.85
    'MEDIUM',     -- 0.70 <= overall < 0.85
    'LOW',        -- 0.55 <= overall < 0.70
    'UNCERTAIN'   -- overall < 0.55
);

CREATE TYPE scse.similarity_label AS ENUM (
    'EXACT',          -- > 0.97  — same entity, different record
    'NEAR_DUPLICATE', -- 0.93–0.97
    'HIGHLY_SIMILAR', -- 0.85–0.92
    'SIMILAR',        -- 0.70–0.84
    'RELATED',        -- 0.50–0.69
    'UNRELATED'       -- < 0.50
);

CREATE TYPE scse.index_status AS ENUM (
    'PENDING',   -- queued for first-time embedding
    'INDEXING',  -- currently being processed
    'ACTIVE',    -- successfully indexed in Qdrant
    'STALE',     -- source entity updated, re-index needed
    'ERROR',     -- indexing failed after max retries
    'DELETED'    -- soft-deleted, excluded from search
);

CREATE TYPE scse.job_status AS ENUM (
    'QUEUED',
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'CANCELLED'
);
```

### 11.3 Core Tables

#### scse.vector_records

```sql
CREATE TABLE scse.vector_records (
    vector_record_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    entity_type         scse.entity_type NOT NULL,
    entity_id           UUID        NOT NULL,
    collection_name     VARCHAR(100) NOT NULL,
    embedding_model     VARCHAR(100) NOT NULL,
    encoder_version     VARCHAR(50)  NOT NULL,
    index_status        scse.index_status NOT NULL DEFAULT 'PENDING',
    qdrant_point_id     UUID,
    faiss_index_position BIGINT,
    payload             JSONB        NOT NULL DEFAULT '{}',
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    indexed_at          TIMESTAMPTZ,
    source_updated_at   TIMESTAMPTZ,
    superseded_by       UUID         REFERENCES scse.vector_records(vector_record_id),

    CONSTRAINT pk_vector_records PRIMARY KEY (vector_record_id),
    CONSTRAINT uq_vector_records_active
        UNIQUE (entity_type, entity_id, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  scse.vector_records IS 'Master registry of all entity embeddings — one active record per entity.';
COMMENT ON COLUMN scse.vector_records.qdrant_point_id IS 'Point ID in Qdrant collection; matches entity_id for simplicity.';
COMMENT ON COLUMN scse.vector_records.faiss_index_position IS 'Row position in nightly FAISS flat index (used for ID mapping).';
COMMENT ON COLUMN scse.vector_records.superseded_by IS 'FK to replacement record during model migration dual-write period.';
```

#### scse.search_queries

```sql
CREATE TABLE scse.search_queries (
    query_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    session_id          UUID,
    user_id             UUID,
    query_text          TEXT,
    query_entity_id     UUID,
    entity_type         scse.entity_type NOT NULL,
    search_mode         scse.search_mode NOT NULL DEFAULT 'HYBRID',
    filters             JSONB        NOT NULL DEFAULT '{}',
    top_k               INTEGER      NOT NULL DEFAULT 20,
    min_similarity      FLOAT        NOT NULL DEFAULT 0.70,
    result_count        INTEGER,
    total_latency_ms    INTEGER,
    embedding_latency_ms INTEGER,
    qdrant_latency_ms   INTEGER,
    rerank_latency_ms   INTEGER,
    cache_hit           BOOLEAN      NOT NULL DEFAULT FALSE,
    executed_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_search_queries PRIMARY KEY (query_id),
    CONSTRAINT chk_sq_similarity CHECK (min_similarity BETWEEN 0.0 AND 1.0),
    CONSTRAINT chk_sq_top_k      CHECK (top_k BETWEEN 1 AND 200)
);

COMMENT ON TABLE  scse.search_queries IS 'Immutable log of all search queries for analytics and quality monitoring.';
COMMENT ON COLUMN scse.search_queries.user_id IS 'SHA-256 hashed after 90 days (GDPR anonymization job).';
```

#### scse.search_results

```sql
CREATE TABLE scse.search_results (
    result_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    query_id            UUID        NOT NULL REFERENCES scse.search_queries(query_id),
    rank                INTEGER     NOT NULL,
    entity_type         scse.entity_type NOT NULL,
    entity_id           UUID        NOT NULL,
    vector_record_id    UUID        REFERENCES scse.vector_records(vector_record_id),
    cosine_similarity   FLOAT,
    structured_similarity FLOAT,
    final_score         FLOAT,
    confidence_overall  FLOAT,
    confidence_grade    scse.confidence_grade,
    confidence_breakdown JSONB       NOT NULL DEFAULT '{}',
    boost_applied       FLOAT        NOT NULL DEFAULT 0.0,
    boost_reasons       TEXT[]       NOT NULL DEFAULT '{}',
    is_clicked          BOOLEAN      NOT NULL DEFAULT FALSE,
    is_selected         BOOLEAN      NOT NULL DEFAULT FALSE,
    user_feedback       scse.similarity_label,
    feedback_at         TIMESTAMPTZ,

    CONSTRAINT pk_search_results PRIMARY KEY (result_id),
    CONSTRAINT chk_sr_rank       CHECK (rank >= 1),
    CONSTRAINT chk_sr_score      CHECK (final_score BETWEEN 0.0 AND 1.0 OR final_score IS NULL)
);

COMMENT ON TABLE scse.search_results IS 'Individual ranked results per search query with confidence and user feedback.';
```

#### scse.similarity_cache

```sql
CREATE TABLE scse.similarity_cache (
    cache_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    entity_type         scse.entity_type NOT NULL,
    source_entity_id    UUID        NOT NULL,
    target_entity_id    UUID        NOT NULL,
    similarity_label    scse.similarity_label NOT NULL,
    cosine_similarity   FLOAT       NOT NULL,
    structured_similarity FLOAT,
    combined_score      FLOAT       NOT NULL,
    rank_position       INTEGER     NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until         TIMESTAMPTZ,

    CONSTRAINT pk_similarity_cache PRIMARY KEY (cache_id),
    CONSTRAINT uq_similarity_cache
        UNIQUE (entity_type, source_entity_id, target_entity_id),
    CONSTRAINT chk_sc_score    CHECK (combined_score BETWEEN 0.0 AND 1.0),
    CONSTRAINT chk_sc_rank     CHECK (rank_position >= 1),
    CONSTRAINT chk_sc_no_self  CHECK (source_entity_id <> target_entity_id)
);

COMMENT ON TABLE scse.similarity_cache IS 'Precomputed top-K similarities from nightly FAISS batch job. Served as cache for repeat queries.';
```

#### scse.embedding_jobs

```sql
CREATE TABLE scse.embedding_jobs (
    job_id          UUID        NOT NULL DEFAULT gen_random_uuid(),
    entity_type     scse.entity_type NOT NULL,
    entity_id       UUID        NOT NULL,
    job_type        VARCHAR(50) NOT NULL,   -- INITIAL_EMBED | REEMBED | BATCH_REINDEX
    status          scse.job_status NOT NULL DEFAULT 'QUEUED',
    priority        INTEGER     NOT NULL DEFAULT 5,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    max_attempts    INTEGER     NOT NULL DEFAULT 3,
    error_message   TEXT,
    scheduled_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_embedding_jobs PRIMARY KEY (job_id),
    CONSTRAINT chk_ej_priority CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT chk_ej_attempts CHECK (attempts <= max_attempts)
);

COMMENT ON TABLE scse.embedding_jobs IS 'Queue for async embedding generation. Workers poll by (status, priority, created_at).';
```

#### scse.faiss_index_versions

```sql
CREATE TABLE scse.faiss_index_versions (
    version_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    entity_type     scse.entity_type NOT NULL,
    version_tag     VARCHAR(50) NOT NULL,           -- e.g. "2026-06-20-v1"
    vector_count    BIGINT      NOT NULL,
    index_type      VARCHAR(50) NOT NULL,           -- IVFFlat | IVFPQ | HNSWFlat
    dimension       INTEGER     NOT NULL,
    nprobe          INTEGER,                        -- IVF search parameter
    s3_path         TEXT        NOT NULL,
    local_path      TEXT,
    file_size_bytes BIGINT,
    build_duration_sec INTEGER,
    is_current      BOOLEAN     NOT NULL DEFAULT FALSE,
    built_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_faiss_index_versions PRIMARY KEY (version_id),
    CONSTRAINT uq_faiss_current
        UNIQUE (entity_type, is_current)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE scse.faiss_index_versions IS 'Version history of nightly FAISS index builds. Only one is_current per entity_type.';
```

#### scse.training_labels

```sql
CREATE TABLE scse.training_labels (
    label_id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    source_entity_type  scse.entity_type NOT NULL,
    source_entity_id    UUID        NOT NULL,
    target_entity_type  scse.entity_type NOT NULL,
    target_entity_id    UUID        NOT NULL,
    similarity_label    scse.similarity_label NOT NULL,
    labeler_id          UUID        NOT NULL,
    labeler_role        VARCHAR(50),
    confidence          FLOAT       NOT NULL DEFAULT 1.0,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_training_labels PRIMARY KEY (label_id),
    CONSTRAINT uq_training_labels
        UNIQUE (source_entity_type, source_entity_id, target_entity_type, target_entity_id),
    CONSTRAINT chk_tl_confidence CHECK (confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT chk_tl_no_self    CHECK (source_entity_id <> target_entity_id)
);

COMMENT ON TABLE scse.training_labels IS 'Human-verified similarity labels used for model training and recall evaluation.';
```

#### scse.model_evaluations

```sql
CREATE TABLE scse.model_evaluations (
    eval_id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    model_version       VARCHAR(50) NOT NULL,
    encoder_version     VARCHAR(50) NOT NULL,
    entity_type         scse.entity_type NOT NULL,
    eval_dataset_size   INTEGER     NOT NULL,
    precision_at_1      FLOAT,
    precision_at_5      FLOAT,
    precision_at_10     FLOAT,
    recall_at_10        FLOAT,
    recall_at_20        FLOAT,
    ndcg_at_10          FLOAT,
    mrr                 FLOAT,
    map                 FLOAT,
    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_model_evaluations PRIMARY KEY (eval_id)
);

COMMENT ON TABLE scse.model_evaluations IS 'Nightly recall evaluation results per entity type and model version.';
```

#### scse.outbox

```sql
CREATE TABLE scse.outbox (
    outbox_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    topic           VARCHAR(200) NOT NULL,
    partition_key   VARCHAR(200),
    payload         JSONB       NOT NULL,
    headers         JSONB,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    attempts        INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    error_message   TEXT,

    CONSTRAINT pk_outbox PRIMARY KEY (outbox_id),
    CONSTRAINT chk_outbox_status CHECK (status IN ('PENDING','PUBLISHED','FAILED'))
);

COMMENT ON TABLE scse.outbox IS 'Transactional outbox for Kafka event publishing. Relay polls PENDING rows.';
```

### 11.4 Stored Functions

```sql
-- Function 1: Retrieve precomputed similar entities from cache
CREATE OR REPLACE FUNCTION scse.get_cached_similar(
    p_entity_type   scse.entity_type,
    p_entity_id     UUID,
    p_min_score     FLOAT   DEFAULT 0.70,
    p_limit         INTEGER DEFAULT 20
) RETURNS TABLE (
    target_entity_id    UUID,
    similarity_label    scse.similarity_label,
    combined_score      FLOAT,
    rank_position       INTEGER
) AS $$
    SELECT
        target_entity_id,
        similarity_label,
        combined_score,
        rank_position
    FROM scse.similarity_cache
    WHERE
        entity_type      = p_entity_type
        AND source_entity_id = p_entity_id
        AND combined_score   >= p_min_score
        AND (valid_until IS NULL OR valid_until > now())
    ORDER BY rank_position ASC
    LIMIT p_limit;
$$ LANGUAGE sql STABLE;

-- Function 2: Search quality metrics for a time window
CREATE OR REPLACE FUNCTION scse.get_search_quality_metrics(
    p_start_ts  TIMESTAMPTZ,
    p_end_ts    TIMESTAMPTZ,
    p_entity_type scse.entity_type DEFAULT NULL
) RETURNS TABLE (
    entity_type         scse.entity_type,
    search_mode         scse.search_mode,
    query_count         BIGINT,
    avg_latency_ms      NUMERIC,
    p95_latency_ms      NUMERIC,
    avg_result_count    NUMERIC,
    zero_result_count   BIGINT,
    cache_hit_count     BIGINT
) AS $$
    SELECT
        sq.entity_type,
        sq.search_mode,
        COUNT(*)                                                        AS query_count,
        ROUND(AVG(sq.total_latency_ms)::NUMERIC, 1)                   AS avg_latency_ms,
        ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
            ORDER BY sq.total_latency_ms)::NUMERIC, 1)                 AS p95_latency_ms,
        ROUND(AVG(sq.result_count)::NUMERIC, 2)                        AS avg_result_count,
        SUM(CASE WHEN sq.result_count = 0 THEN 1 ELSE 0 END)          AS zero_result_count,
        SUM(CASE WHEN sq.cache_hit = TRUE THEN 1 ELSE 0 END)          AS cache_hit_count
    FROM scse.search_queries sq
    WHERE
        sq.executed_at BETWEEN p_start_ts AND p_end_ts
        AND (p_entity_type IS NULL OR sq.entity_type = p_entity_type)
    GROUP BY sq.entity_type, sq.search_mode
    ORDER BY query_count DESC;
$$ LANGUAGE sql STABLE;

-- Function 3: Mark a vector as STALE and queue re-embed job (used by triggers)
CREATE OR REPLACE FUNCTION scse.mark_vector_stale()
RETURNS TRIGGER AS $$
DECLARE
    v_entity_type scse.entity_type;
BEGIN
    -- TG_ARGV[0] passes the entity type as trigger argument
    v_entity_type := TG_ARGV[0]::scse.entity_type;

    -- Mark existing active vector record as STALE
    UPDATE scse.vector_records
    SET
        index_status      = 'STALE',
        source_updated_at = now(),
        updated_at        = now()
    WHERE
        entity_id   = NEW.id        -- assumes source tables have 'id' PK
        AND entity_type = v_entity_type
        AND is_active   = TRUE;

    -- Queue re-embed job (priority 3 = above batch, below realtime)
    INSERT INTO scse.embedding_jobs
        (entity_type, entity_id, job_type, priority, scheduled_at)
    VALUES
        (v_entity_type, NEW.id, 'REEMBED', 3, now())
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function 4: Claim next embedding job (atomic dequeue for worker pool)
CREATE OR REPLACE FUNCTION scse.claim_next_embedding_job(
    p_worker_id VARCHAR(100)
) RETURNS TABLE (
    job_id      UUID,
    entity_type scse.entity_type,
    entity_id   UUID,
    job_type    VARCHAR(50)
) AS $$
    UPDATE scse.embedding_jobs
    SET
        status     = 'RUNNING',
        started_at = now(),
        attempts   = attempts + 1
    WHERE job_id = (
        SELECT job_id
        FROM scse.embedding_jobs
        WHERE
            status IN ('QUEUED')
            AND attempts < max_attempts
            AND (scheduled_at IS NULL OR scheduled_at <= now())
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING job_id, entity_type, entity_id, job_type;
$$ LANGUAGE sql;
```

### 11.5 Triggers

```sql
-- Trigger placeholder: attach mark_vector_stale() to source entity tables.
-- Example for supplier_intelligence.suppliers:
--
-- CREATE TRIGGER trg_supplier_updated_scse
-- AFTER UPDATE ON supplier_intelligence.suppliers
-- FOR EACH ROW
-- WHEN (OLD.updated_at IS DISTINCT FROM NEW.updated_at)
-- EXECUTE FUNCTION scse.mark_vector_stale('SUPPLIER');
--
-- Same pattern for: material_intelligence.products → PRODUCT
--                   material_intelligence.materials → MATERIAL
--                   manufacturing_process_engine.processes → PROCESS
--                   cost_history.quote_records → QUOTE

-- Auto-update updated_at on vector_records
CREATE OR REPLACE FUNCTION scse.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_vr_updated_at
BEFORE UPDATE ON scse.vector_records
FOR EACH ROW EXECUTE FUNCTION scse.set_updated_at();
```

### 11.6 Indexes

```sql
-- vector_records
CREATE INDEX idx_vr_entity_active
    ON scse.vector_records (entity_type, entity_id)
    WHERE is_active = TRUE;

CREATE INDEX idx_vr_status_pending
    ON scse.vector_records (entity_type, index_status, updated_at)
    WHERE index_status IN ('PENDING', 'STALE');

CREATE INDEX idx_vr_collection
    ON scse.vector_records (collection_name, is_active);

-- pgvector fallback: HNSW on fused embedding stored in PostgreSQL
-- (Qdrant is primary; this enables transactional fallback search)
-- Note: vector columns are NOT stored in vector_records (kept in Qdrant).
-- For pgvector fallback, use separate tables per entity type:
-- scse.product_embeddings (product_id, fused_vector vector(1024)) with HNSW index

-- similarity_cache
CREATE INDEX idx_sc_source
    ON scse.similarity_cache (entity_type, source_entity_id, rank_position ASC);

CREATE INDEX idx_sc_target
    ON scse.similarity_cache (entity_type, target_entity_id);

CREATE INDEX idx_sc_score
    ON scse.similarity_cache (entity_type, combined_score DESC);

CREATE INDEX idx_sc_valid
    ON scse.similarity_cache (entity_type, source_entity_id)
    WHERE valid_until > now() OR valid_until IS NULL;

-- search_queries
CREATE INDEX idx_sq_user_time
    ON scse.search_queries (user_id, executed_at DESC);

CREATE INDEX idx_sq_entity_mode
    ON scse.search_queries (entity_type, search_mode, executed_at DESC);

CREATE INDEX idx_sq_slow
    ON scse.search_queries (total_latency_ms DESC)
    WHERE total_latency_ms > 500;

CREATE INDEX idx_sq_brin
    ON scse.search_queries USING BRIN (executed_at);

-- search_results
CREATE INDEX idx_sr_query
    ON scse.search_results (query_id, rank ASC);

CREATE INDEX idx_sr_entity
    ON scse.search_results (entity_type, entity_id);

CREATE INDEX idx_sr_feedback
    ON scse.search_results (user_feedback)
    WHERE user_feedback IS NOT NULL;

-- embedding_jobs
CREATE INDEX idx_ej_queue
    ON scse.embedding_jobs (priority DESC, created_at ASC)
    WHERE status = 'QUEUED';

CREATE INDEX idx_ej_entity
    ON scse.embedding_jobs (entity_type, entity_id, status);

-- outbox
CREATE INDEX idx_outbox_pending
    ON scse.outbox (created_at ASC)
    WHERE status = 'PENDING';

-- training_labels
CREATE INDEX idx_tl_source
    ON scse.training_labels (source_entity_type, source_entity_id);

CREATE INDEX idx_tl_target
    ON scse.training_labels (target_entity_type, target_entity_id);
```

### 11.7 Views

```sql
-- Daily search quality aggregates (used by Grafana dashboard)
CREATE VIEW scse.v_search_quality_daily AS
SELECT
    date_trunc('day', executed_at)                                      AS day,
    entity_type,
    search_mode,
    COUNT(*)                                                            AS query_count,
    ROUND(AVG(total_latency_ms)::NUMERIC, 1)                           AS avg_latency_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY total_latency_ms)::NUMERIC, 1)                         AS p95_latency_ms,
    ROUND(AVG(result_count)::NUMERIC, 2)                               AS avg_result_count,
    SUM(CASE WHEN result_count = 0 THEN 1 ELSE 0 END)                 AS zero_result_count,
    ROUND(AVG(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS cache_hit_rate
FROM scse.search_queries
GROUP BY 1, 2, 3;

-- Embedding freshness summary (used by SLA dashboard and alerts)
CREATE VIEW scse.v_embedding_freshness AS
SELECT
    entity_type,
    index_status,
    COUNT(*)                                AS entity_count,
    MAX(indexed_at)                         AS latest_indexed_at,
    MIN(indexed_at)                         AS oldest_indexed_at,
    EXTRACT(EPOCH FROM (now() - MIN(indexed_at))) / 3600 AS max_age_hours
FROM scse.vector_records
GROUP BY entity_type, index_status;

-- Latest model evaluation per entity type (used by quality dashboard)
CREATE VIEW scse.v_model_eval_latest AS
SELECT DISTINCT ON (entity_type)
    eval_id,
    entity_type,
    model_version,
    encoder_version,
    precision_at_1,
    precision_at_5,
    ndcg_at_10,
    mrr,
    eval_dataset_size,
    evaluated_at
FROM scse.model_evaluations
ORDER BY entity_type, evaluated_at DESC;
```

---

## Section 12: API

Full OpenAPI 3.1 specification for the Similarity Cost Search Engine REST API.

### 12.1 Overview

```yaml
openapi: "3.1.0"
info:
  title: "Similarity Cost Search Engine API"
  version: "1.0.0"
  description: |
    REST API for semantic and hybrid similarity search across products, quotes,
    materials, processes, and suppliers in the Industrial Cost Intelligence platform.
  contact:
    name: "ICI Platform Team"
    email: "platform@ici.internal"

servers:
  - url: "https://api.ici.internal/api/v1/scse"
    description: "Production"
  - url: "https://api-staging.ici.internal/api/v1/scse"
    description: "Staging"

security:
  - bearerAuth: []

tags:
  - name: Search
    description: "Similarity search across entity types"
  - name: Recommendations
    description: "More-like-this and collaborative recommendations"
  - name: Indexing
    description: "Embedding indexing management"
  - name: Cache
    description: "Precomputed similarity cache"
  - name: Feedback
    description: "User relevance feedback for model improvement"
  - name: Analytics
    description: "Search quality and coverage metrics"
  - name: Admin
    description: "Administrative operations (SCSE_OPS / SCSE_ADMIN roles)"
```

### 12.2 Component Schemas

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    EntityType:
      type: string
      enum: [PRODUCT, QUOTE, MATERIAL, PROCESS, SUPPLIER]

    SearchMode:
      type: string
      enum: [SEMANTIC, FEATURE, KEYWORD, HYBRID, FILTERED, RECOMMEND]
      default: HYBRID

    ConfidenceGrade:
      type: string
      enum: [HIGH, MEDIUM, LOW, UNCERTAIN]

    SimilarityLabel:
      type: string
      enum: [EXACT, NEAR_DUPLICATE, HIGHLY_SIMILAR, SIMILAR, RELATED, UNRELATED]

    ConfidenceBreakdown:
      type: object
      properties:
        overall:            { type: number, minimum: 0, maximum: 1 }
        grade:              { $ref: '#/components/schemas/ConfidenceGrade' }
        vector_similarity:  { type: number }
        feature_alignment:  { type: number }
        data_completeness:  { type: number }
        temporal_relevance: { type: number }
        business_relevance: { type: number }
        reasons:            { type: array, items: { type: string } }
        warnings:           { type: array, items: { type: string } }

    SearchRequest:
      type: object
      properties:
        query_text:     { type: string, maxLength: 2000, description: "Free-text query" }
        entity_id:      { type: string, format: uuid, description: "Search by existing entity ID" }
        entity_type:
          $ref: '#/components/schemas/EntityType'
        mode:
          $ref: '#/components/schemas/SearchMode'
        top_k:          { type: integer, minimum: 1, maximum: 200, default: 20 }
        min_similarity: { type: number, minimum: 0, maximum: 1, default: 0.70 }
        filters:
          type: object
          additionalProperties: true
          description: "Entity-type-specific filter fields (e.g. price_min, country_iso)"
      oneOf:
        - required: [query_text, entity_type]
        - required: [entity_id, entity_type]

    SearchResult:
      type: object
      properties:
        rank:                 { type: integer }
        entity_id:            { type: string, format: uuid }
        entity_type:          { $ref: '#/components/schemas/EntityType' }
        name:                 { type: string }
        cosine_similarity:    { type: number }
        structured_similarity: { type: number }
        final_score:          { type: number }
        boost_applied:        { type: number }
        boost_reasons:        { type: array, items: { type: string } }
        confidence:           { $ref: '#/components/schemas/ConfidenceBreakdown' }
        payload:
          type: object
          description: "Entity-specific fields; sensitive fields masked by RBAC role"
          additionalProperties: true

    SearchResponse:
      type: object
      properties:
        query_id:     { type: string, format: uuid }
        total_count:  { type: integer }
        latency_ms:   { type: integer }
        cache_hit:    { type: boolean }
        search_mode:  { $ref: '#/components/schemas/SearchMode' }
        results:
          type: array
          items: { $ref: '#/components/schemas/SearchResult' }

    IndexRequest:
      type: object
      required: [entity_type, entity_id]
      properties:
        entity_type:  { $ref: '#/components/schemas/EntityType' }
        entity_id:    { type: string, format: uuid }
        priority:     { type: integer, minimum: 1, maximum: 10, default: 5 }
        force_reembed: { type: boolean, default: false }

    IndexStatus:
      type: object
      properties:
        entity_type:    { $ref: '#/components/schemas/EntityType' }
        entity_id:      { type: string, format: uuid }
        index_status:   { type: string }
        indexed_at:     { type: string, format: date-time }
        embedding_model: { type: string }
        encoder_version: { type: string }

    FeedbackRequest:
      type: object
      required: [query_id, entity_id, feedback_type]
      properties:
        query_id:        { type: string, format: uuid }
        entity_id:       { type: string, format: uuid }
        feedback_type:   { type: string, enum: [CLICKED, SELECTED, DISMISSED] }
        similarity_label: { $ref: '#/components/schemas/SimilarityLabel' }

    Error:
      type: object
      properties:
        error:   { type: string }
        code:    { type: string }
        details: { type: string }
        request_id: { type: string }
```

### 12.3 Search Endpoints

```yaml
paths:
  /search:
    post:
      tags: [Search]
      summary: "Universal similarity search"
      description: |
        Search across any entity type using free-text or entity-ID query.
        Supports HYBRID mode (dense + sparse + RRF) by default.
        Requires SCSE_VIEWER role or higher.
      operationId: universalSearch
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/SearchRequest' }
            example:
              query_text: "austenitic stainless steel 316L food grade"
              entity_type: MATERIAL
              mode: HYBRID
              top_k: 10
              min_similarity: 0.75
      responses:
        "200":
          description: "Successful search results"
          content:
            application/json:
              schema: { $ref: '#/components/schemas/SearchResponse' }
              example:
                query_id: "550e8400-e29b-41d4-a716-446655440000"
                total_count: 8
                latency_ms: 187
                cache_hit: false
                search_mode: HYBRID
                results:
                  - rank: 1
                    entity_id: "a7c1d2e3-..."
                    entity_type: MATERIAL
                    name: "316L Stainless Steel Sheet"
                    cosine_similarity: 0.932
                    final_score: 0.901
                    confidence:
                      overall: 0.887
                      grade: HIGH
                      reasons: ["Very high semantic similarity", "Strong feature alignment"]
                      warnings: []
        "400": { description: "Invalid request — missing entity_type, query too long" }
        "401": { description: "Missing or invalid JWT" }
        "403": { description: "Insufficient role" }
        "429": { description: "Rate limit exceeded (1000 req/min)" }
        "500": { description: "Internal server error" }

  /search/products:
    post:
      tags: [Search]
      summary: "Product similarity search"
      description: "Search for similar products with product-specific filters."
      operationId: searchProducts
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/SearchRequest'
                - type: object
                  properties:
                    filters:
                      type: object
                      properties:
                        categories:        { type: array, items: { type: string } }
                        price_min:         { type: number }
                        price_max:         { type: number }
                        lifecycle_stages:  { type: array, items: { type: string } }
                        min_supplier_count: { type: integer, minimum: 1 }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }
        "400": { $ref: '#/components/responses/BadRequest' }
        "401": { $ref: '#/components/responses/Unauthorized' }

  /search/quotes:
    post:
      tags: [Search]
      summary: "Quote similarity search"
      description: "Find similar historical quotes with price and supplier filters."
      operationId: searchQuotes
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/SearchRequest'
                - type: object
                  properties:
                    filters:
                      type: object
                      properties:
                        price_min:          { type: number }
                        price_max:          { type: number }
                        min_supplier_score: { type: number, minimum: 0, maximum: 100 }
                        supplier_tiers:     { type: array, items: { type: integer } }
                        countries:          { type: array, items: { type: string } }
                        incoterms:          { type: array, items: { type: string } }
                        validity_after:     { type: string, format: date }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /search/materials:
    post:
      tags: [Search]
      summary: "Material similarity search"
      description: "Find technically similar materials including cross-standard equivalents."
      operationId: searchMaterials
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/SearchRequest'
                - type: object
                  properties:
                    filters:
                      type: object
                      properties:
                        material_groups:    { type: array, items: { type: string } }
                        price_max_per_kg:   { type: number }
                        min_tensile_mpa:    { type: number }
                        max_density:        { type: number }
                        hazmat_classes:     { type: array, items: { type: string } }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /search/processes:
    post:
      tags: [Search]
      summary: "Manufacturing process similarity search"
      operationId: searchProcesses
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/SearchRequest'
                - type: object
                  properties:
                    filters:
                      type: object
                      properties:
                        process_classes:    { type: array, items: { type: string } }
                        max_it_grade:       { type: integer }
                        max_cost_per_hour:  { type: number }
                        min_automation:     { type: number }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /search/suppliers:
    post:
      tags: [Search]
      summary: "Supplier similarity search"
      operationId: searchSuppliers
      requestBody:
        required: true
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/SearchRequest'
                - type: object
                  properties:
                    filters:
                      type: object
                      properties:
                        min_scorecard:      { type: number }
                        countries:          { type: array, items: { type: string } }
                        certifications:     { type: array, items: { type: string } }
                        supplier_types:     { type: array, items: { type: string } }
                        max_lead_time_days: { type: integer }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /search/cross-entity:
    post:
      tags: [Search]
      summary: "Cross-entity type search"
      description: "Search across multiple entity types simultaneously, returning unified ranked results."
      operationId: searchCrossEntity
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [query_text, entity_types]
              properties:
                query_text:   { type: string, maxLength: 2000 }
                entity_types:
                  type: array
                  items: { $ref: '#/components/schemas/EntityType' }
                  minItems: 2
                top_k_per_type: { type: integer, default: 5 }
                min_similarity: { type: number, default: 0.70 }
      responses:
        "200":
          description: "Cross-entity results grouped by type"
          content:
            application/json:
              schema:
                type: object
                properties:
                  query_id: { type: string, format: uuid }
                  latency_ms: { type: integer }
                  results_by_type:
                    type: object
                    additionalProperties:
                      type: array
                      items: { $ref: '#/components/schemas/SearchResult' }

  /recommend/products/{product_id}:
    get:
      tags: [Recommendations]
      summary: "Products similar to a given product"
      operationId: recommendProducts
      parameters:
        - name: product_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: top_k
          in: query
          schema: { type: integer, default: 10, maximum: 50 }
        - name: exclude_same_supplier
          in: query
          schema: { type: boolean, default: false }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }
        "404": { description: "Product not found in index" }

  /recommend/quotes/{quote_id}:
    get:
      tags: [Recommendations]
      summary: "Quotes similar to a given quote"
      operationId: recommendQuotes
      parameters:
        - name: quote_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: top_k
          in: query
          schema: { type: integer, default: 10 }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /recommend/suppliers/{supplier_id}:
    get:
      tags: [Recommendations]
      summary: "Suppliers similar to a given supplier"
      operationId: recommendSuppliers
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: top_k
          in: query
          schema: { type: integer, default: 10 }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /recommend/alternatives/{entity_type}/{entity_id}:
    get:
      tags: [Recommendations]
      summary: "Find alternatives for any entity type"
      operationId: recommendAlternatives
      parameters:
        - name: entity_type
          in: path
          required: true
          schema: { $ref: '#/components/schemas/EntityType' }
        - name: entity_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: top_k
          in: query
          schema: { type: integer, default: 10 }
        - name: min_similarity
          in: query
          schema: { type: number, default: 0.80, description: "Higher threshold for alternatives" }
      responses:
        "200": { $ref: '#/components/responses/SearchOK' }

  /index/entity:
    post:
      tags: [Indexing]
      summary: "Index or re-index a single entity"
      description: "Requires SYSTEM_INTEGRATOR or SCSE_OPS role."
      operationId: indexEntity
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/IndexRequest' }
      responses:
        "202":
          description: "Indexing job queued"
          content:
            application/json:
              schema:
                type: object
                properties:
                  job_id:     { type: string, format: uuid }
                  status:     { type: string, enum: [QUEUED] }
                  message:    { type: string }
        "400": { $ref: '#/components/responses/BadRequest' }

  /index/batch:
    post:
      tags: [Indexing]
      summary: "Batch index multiple entities"
      operationId: batchIndex
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [entities]
              properties:
                entities:
                  type: array
                  items: { $ref: '#/components/schemas/IndexRequest' }
                  maxItems: 1000
      responses:
        "202":
          description: "Batch indexing jobs queued"
          content:
            application/json:
              schema:
                type: object
                properties:
                  batch_id:    { type: string, format: uuid }
                  job_count:   { type: integer }
                  status:      { type: string }

  /index/status/{entity_type}/{entity_id}:
    get:
      tags: [Indexing]
      summary: "Get indexing status for an entity"
      operationId: getIndexStatus
      parameters:
        - name: entity_type
          in: path
          required: true
          schema: { $ref: '#/components/schemas/EntityType' }
        - name: entity_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: '#/components/schemas/IndexStatus' }
        "404": { description: "Entity not found in index" }

  /index/{entity_type}/{entity_id}:
    delete:
      tags: [Indexing]
      summary: "Remove entity from index (soft delete)"
      description: "Sets is_active=FALSE in Qdrant payload. Requires SCSE_ADMIN role."
      operationId: deleteFromIndex
      parameters:
        - name: entity_type
          in: path
          required: true
          schema: { $ref: '#/components/schemas/EntityType' }
        - name: entity_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        "204": { description: "Entity soft-deleted from index" }
        "404": { description: "Entity not in index" }

  /cache/similar/{entity_type}/{entity_id}:
    get:
      tags: [Cache]
      summary: "Get precomputed similar entities"
      description: "Returns top-K from nightly FAISS batch — <5ms latency."
      operationId: getCachedSimilar
      parameters:
        - name: entity_type
          in: path
          required: true
          schema: { $ref: '#/components/schemas/EntityType' }
        - name: entity_id
          in: path
          required: true
          schema: { type: string, format: uuid }
        - name: top_k
          in: query
          schema: { type: integer, default: 20 }
        - name: min_score
          in: query
          schema: { type: number, default: 0.70 }
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  source_entity_id: { type: string }
                  cached_at: { type: string, format: date-time }
                  results:
                    type: array
                    items:
                      type: object
                      properties:
                        target_entity_id: { type: string }
                        similarity_label: { $ref: '#/components/schemas/SimilarityLabel' }
                        combined_score:   { type: number }
                        rank_position:    { type: integer }

  /feedback/relevance:
    post:
      tags: [Feedback]
      summary: "Submit click/selection feedback"
      operationId: submitRelevanceFeedback
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/FeedbackRequest' }
      responses:
        "204": { description: "Feedback recorded" }

  /feedback/label:
    post:
      tags: [Feedback]
      summary: "Submit explicit similarity label"
      description: "Requires SCSE_DATA_STEWARD role."
      operationId: submitLabel
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [source_entity_id, source_entity_type, target_entity_id, target_entity_type, similarity_label]
              properties:
                source_entity_id:    { type: string, format: uuid }
                source_entity_type:  { $ref: '#/components/schemas/EntityType' }
                target_entity_id:    { type: string, format: uuid }
                target_entity_type:  { $ref: '#/components/schemas/EntityType' }
                similarity_label:    { $ref: '#/components/schemas/SimilarityLabel' }
                confidence:          { type: number, default: 1.0 }
                notes:               { type: string }
      responses:
        "201": { description: "Label stored in training_labels table" }

  /analytics/search-quality:
    get:
      tags: [Analytics]
      summary: "Search quality metrics"
      operationId: getSearchQuality
      parameters:
        - name: entity_type
          in: query
          schema: { $ref: '#/components/schemas/EntityType' }
        - name: days
          in: query
          schema: { type: integer, default: 7 }
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  precision_at_1:  { type: number }
                  precision_at_5:  { type: number }
                  ndcg_at_10:      { type: number }
                  mrr:             { type: number }
                  p95_latency_ms:  { type: number }
                  zero_result_rate: { type: number }
                  cache_hit_rate:  { type: number }
                  query_count:     { type: integer }

  /analytics/coverage:
    get:
      tags: [Analytics]
      summary: "Index coverage and freshness"
      operationId: getCoverage
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                additionalProperties:
                  type: object
                  properties:
                    total_entities:   { type: integer }
                    indexed_active:   { type: integer }
                    stale:            { type: integer }
                    coverage_pct:     { type: number }
                    max_age_hours:    { type: number }
```

### 12.4 Redis Caching Strategy

| Cache Key | TTL | Invalidation | Avg Size |
|-----------|-----|-------------|---------|
| `scse:search:{sha256(query+entity_type+mode+filters)}` | 15min | TTL only | ~5KB |
| `scse:similar:{entity_type}:{entity_id}` | 4h | Entity update event | ~2KB |
| `scse:recommend:{entity_type}:{entity_id}` | 1h | Entity update event | ~3KB |
| `scse:embedding:{entity_type}:{entity_id}` | 24h | Re-embed job completion | ~16KB |
| `scse:qdrant:stats:{collection}` | 5min | TTL only | <1KB |
| `scse:eval:latest:{entity_type}` | 1h | Eval job completion | <2KB |
| `scse:coverage:{entity_type}` | 10min | TTL only | <1KB |

---

## Section 13: Events

### 13.1 Kafka Topics

| Topic | Partitions | Retention | Partition Key | Description |
|-------|-----------|-----------|--------------|-------------|
| `scse.entity.indexed` | 12 | 365d | entity_id | Emitted after successful embedding in Qdrant |
| `scse.entity.index-failed` | 3 | 30d | entity_id | DLQ — all retries exhausted |
| `scse.search.executed` | 6 | 90d | session_id | Search query summary (no query text — PII risk) |
| `scse.similarity.batch-computed` | 3 | 30d | entity_type | Nightly FAISS batch job completed |
| `scse.similarity.cache-updated` | 3 | 30d | entity_type | Precomputed cache refreshed for entity type |
| `scse.feedback.received` | 3 | 365d | entity_id | User click/selection/label feedback |
| `scse.model.retrain-requested` | 1 | 30d | entity_type | Drift alert or precision threshold breach |
| `scse.model.evaluation-completed` | 1 | 90d | entity_type | Nightly evaluation results published |

### 13.2 Avro Schemas

**EntityIndexedEvent:**
```json
{
  "type": "record",
  "name": "EntityIndexedEvent",
  "namespace": "com.ici.scse.events",
  "fields": [
    {"name": "event_id",         "type": "string"},
    {"name": "event_type",       "type": "string", "default": "EntityIndexed"},
    {"name": "entity_type",      "type": {"type": "enum", "name": "EntityType",
      "symbols": ["PRODUCT","QUOTE","MATERIAL","PROCESS","SUPPLIER"]}},
    {"name": "entity_id",        "type": "string"},
    {"name": "vector_record_id", "type": "string"},
    {"name": "embedding_model",  "type": "string"},
    {"name": "encoder_version",  "type": "string"},
    {"name": "collection_name",  "type": "string"},
    {"name": "index_action",     "type": {"type": "enum", "name": "IndexAction",
      "symbols": ["CREATED","UPDATED","DELETED"]}},
    {"name": "occurred_at",      "type": "string"},
    {"name": "correlation_id",   "type": ["null", "string"], "default": null}
  ]
}
```

**SearchExecutedEvent:**
```json
{
  "type": "record",
  "name": "SearchExecutedEvent",
  "namespace": "com.ici.scse.events",
  "fields": [
    {"name": "event_id",       "type": "string"},
    {"name": "event_type",     "type": "string", "default": "SearchExecuted"},
    {"name": "session_id",     "type": ["null","string"], "default": null},
    {"name": "entity_type",    "type": "string"},
    {"name": "search_mode",    "type": "string"},
    {"name": "result_count",   "type": "int"},
    {"name": "total_latency_ms","type": "int"},
    {"name": "cache_hit",      "type": "boolean"},
    {"name": "executed_at",    "type": "string"}
  ]
}
```

**BatchSimilarityComputedEvent:**
```json
{
  "type": "record",
  "name": "BatchSimilarityComputedEvent",
  "namespace": "com.ici.scse.events",
  "fields": [
    {"name": "event_id",            "type": "string"},
    {"name": "entity_type",         "type": "string"},
    {"name": "vector_count",        "type": "long"},
    {"name": "pair_count",          "type": "long"},
    {"name": "top_k",               "type": "int"},
    {"name": "duration_sec",        "type": "int"},
    {"name": "faiss_index_version", "type": "string"},
    {"name": "completed_at",        "type": "string"}
  ]
}
```

**FeedbackReceivedEvent:**
```json
{
  "type": "record",
  "name": "FeedbackReceivedEvent",
  "namespace": "com.ici.scse.events",
  "fields": [
    {"name": "event_id",         "type": "string"},
    {"name": "query_id",         "type": "string"},
    {"name": "entity_id",        "type": "string"},
    {"name": "entity_type",      "type": "string"},
    {"name": "feedback_type",    "type": {"type": "enum", "name": "FeedbackType",
      "symbols": ["CLICKED","SELECTED","DISMISSED","LABELED"]}},
    {"name": "similarity_label", "type": ["null","string"], "default": null},
    {"name": "occurred_at",      "type": "string"}
  ]
}
```

**ModelEvaluationCompletedEvent:**
```json
{
  "type": "record",
  "name": "ModelEvaluationCompletedEvent",
  "namespace": "com.ici.scse.events",
  "fields": [
    {"name": "event_id",           "type": "string"},
    {"name": "entity_type",        "type": "string"},
    {"name": "model_version",      "type": "string"},
    {"name": "encoder_version",    "type": "string"},
    {"name": "precision_at_1",     "type": "float"},
    {"name": "ndcg_at_10",         "type": "float"},
    {"name": "mrr",                "type": "float"},
    {"name": "dataset_size",       "type": "int"},
    {"name": "evaluation_duration_sec", "type": "int"},
    {"name": "completed_at",       "type": "string"}
  ]
}
```

### 13.3 Outbox Publisher

```python
import json
import asyncpg
from uuid import uuid4
from datetime import datetime
from dataclasses import asdict

class SCEOutboxPublisher:
    """Write events to scse.outbox within the same DB transaction (transactional outbox pattern)."""
    
    async def publish_entity_indexed(
        self,
        record: "VectorRecord",
        action: str,
        conn: asyncpg.Connection,
    ) -> None:
        event = {
            "event_id":         str(uuid4()),
            "event_type":       "EntityIndexed",
            "entity_type":      record.entity_type.value,
            "entity_id":        str(record.entity_id),
            "vector_record_id": str(record.id),
            "embedding_model":  record.embedding_model,
            "encoder_version":  record.encoder_version,
            "collection_name":  record.collection,
            "index_action":     action,
            "occurred_at":      datetime.utcnow().isoformat(),
        }
        await conn.execute("""
            INSERT INTO scse.outbox (topic, partition_key, payload)
            VALUES ($1, $2, $3::jsonb)
        """, "scse.entity.indexed", str(record.entity_id), json.dumps(event))
    
    async def publish_search_executed(
        self,
        query_id: str,
        entity_type: str,
        search_mode: str,
        result_count: int,
        latency_ms: int,
        cache_hit: bool,
        conn: asyncpg.Connection,
    ) -> None:
        event = {
            "event_id":        str(uuid4()),
            "event_type":      "SearchExecuted",
            "entity_type":     entity_type,
            "search_mode":     search_mode,
            "result_count":    result_count,
            "total_latency_ms": latency_ms,
            "cache_hit":       cache_hit,
            "executed_at":     datetime.utcnow().isoformat(),
        }
        await conn.execute("""
            INSERT INTO scse.outbox (topic, partition_key, payload)
            VALUES ($1, $2, $3::jsonb)
        """, "scse.search.executed", entity_type, json.dumps(event))
    
    async def relay_pending_events(
        self,
        kafka_producer: "AIOKafkaProducer",
        conn: asyncpg.Connection,
        batch_size: int = 100,
    ) -> int:
        """Poll outbox, publish to Kafka, mark as PUBLISHED. Called by background relay thread."""
        rows = await conn.fetch("""
            SELECT outbox_id, topic, partition_key, payload, headers
            FROM scse.outbox
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        """, batch_size)
        
        published = 0
        for row in rows:
            try:
                await kafka_producer.send_and_wait(
                    row["topic"],
                    key=row["partition_key"].encode() if row["partition_key"] else None,
                    value=json.dumps(row["payload"]).encode(),
                )
                await conn.execute("""
                    UPDATE scse.outbox
                    SET status = 'PUBLISHED', published_at = now()
                    WHERE outbox_id = $1
                """, row["outbox_id"])
                published += 1
            except Exception as exc:
                await conn.execute("""
                    UPDATE scse.outbox
                    SET attempts = attempts + 1,
                        error_message = $2,
                        status = CASE WHEN attempts + 1 >= 5 THEN 'FAILED' ELSE 'PENDING' END
                    WHERE outbox_id = $1
                """, row["outbox_id"], str(exc))
        
        return published
```

### 13.4 Event Consumer Map

| Source Topic | SCSE Consumer | Action |
|-------------|--------------|--------|
| `che.cost-snapshot.created` | EmbeddingWorker | Embed new cost snapshot as QUOTE entity |
| `che.quote.recorded` | EmbeddingWorker | Embed new quote record |
| `che.quote.approved` | EmbeddingWorker | Re-embed with updated status payload |
| `sie.supplier.updated` | EmbeddingWorker | Mark STALE + queue REEMBED job (priority 3) |
| `sie.supplier.scorecard-updated` | EmbeddingWorker | Re-embed (scorecard changes feature vector) |
| `mie.material.updated` | EmbeddingWorker | Mark STALE + queue REEMBED |
| `mpe.process.updated` | EmbeddingWorker | Mark STALE + queue REEMBED |
| `scse.feedback.received` | TrainingDataAccumulator | Accumulate in training_labels if LABELED type |
| `scse.model.retrain-requested` | MLPipelineTrigger | Launch StructuredFeatureEncoder training job |

---

## Section 14: Monitoring

### 14.1 Prometheus Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# --- Search latency ---
SEARCH_LATENCY = Histogram(
    "scse_search_latency_seconds",
    "End-to-end search latency including embedding, ANN, re-ranking",
    ["entity_type", "search_mode"],
    buckets=[0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0],
)

EMBEDDING_LATENCY = Histogram(
    "scse_embedding_latency_seconds",
    "OpenAI embedding API call latency",
    ["model", "entity_type"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

QDRANT_SEARCH_LATENCY = Histogram(
    "scse_qdrant_search_latency_seconds",
    "Qdrant ANN search latency (dense or hybrid)",
    ["collection"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5],
)

RERANK_LATENCY = Histogram(
    "scse_rerank_latency_seconds",
    "Re-ranking stage latency (MMR + business rules + confidence)",
    ["entity_type"],
    buckets=[0.005, 0.01, 0.05, 0.1, 0.2, 0.5],
)

# --- Request counters ---
SEARCH_REQUESTS_TOTAL = Counter(
    "scse_search_requests_total",
    "Total search requests",
    ["entity_type", "search_mode", "status"],  # status: success|error
)

EMBEDDING_REQUESTS_TOTAL = Counter(
    "scse_embedding_requests_total",
    "Total embedding API calls",
    ["entity_type", "model"],
)

OPENAI_TOKENS_USED = Counter(
    "scse_openai_tokens_total",
    "Total OpenAI tokens consumed for embeddings",
    ["model"],
)

OPENAI_RATE_LIMIT_HITS = Counter(
    "scse_openai_rate_limit_hits_total",
    "Number of times OpenAI rate limit (429) was hit",
)

SEARCH_ERRORS_TOTAL = Counter(
    "scse_search_errors_total",
    "Search errors by type",
    ["entity_type", "error_type"],  # error_type: qdrant_unavailable|embedding_timeout|invalid_filter
)

INDEXING_JOBS_TOTAL = Counter(
    "scse_indexing_jobs_total",
    "Indexing jobs processed",
    ["entity_type", "job_type", "status"],  # status: completed|failed
)

CACHE_HITS_TOTAL = Counter(
    "scse_similarity_cache_hits_total",
    "Precomputed similarity cache hits",
    ["entity_type"],
)

CACHE_MISSES_TOTAL = Counter(
    "scse_similarity_cache_misses_total",
    "Precomputed similarity cache misses (fell through to Qdrant)",
    ["entity_type"],
)

# --- Operational gauges ---
INDEXED_ENTITIES_TOTAL = Gauge(
    "scse_indexed_entities_total",
    "Total indexed entities by type and status",
    ["entity_type", "index_status"],
)

INDEX_QUEUE_DEPTH = Gauge(
    "scse_index_queue_depth",
    "Number of pending embedding jobs",
    ["entity_type"],
)

INDEX_FRESHNESS_HOURS = Gauge(
    "scse_index_freshness_hours",
    "Hours since oldest active vector was last indexed (staleness indicator)",
    ["entity_type"],
)

QDRANT_COLLECTION_SIZE = Gauge(
    "scse_qdrant_collection_size_vectors",
    "Number of vectors in each Qdrant collection",
    ["collection"],
)

# --- Quality gauges (updated by nightly evaluation job) ---
PRECISION_AT_1 = Gauge(
    "scse_precision_at_1",
    "Precision@1 from nightly recall evaluation",
    ["entity_type"],
)

NDCG_AT_10 = Gauge(
    "scse_ndcg_at_10",
    "NDCG@10 from nightly recall evaluation",
    ["entity_type"],
)

ZERO_RESULT_RATE = Gauge(
    "scse_zero_result_rate",
    "Fraction of searches returning 0 results (rolling 1h window)",
    ["entity_type"],
)

CACHE_HIT_RATIO = Gauge(
    "scse_similarity_cache_hit_ratio",
    "Cache hit ratio for precomputed similarity (rolling 1h)",
    ["entity_type"],
)
```

### 14.2 Alertmanager Rules

```yaml
groups:
  - name: scse_alerts
    rules:

      - alert: SCSESearchLatencyHigh
        expr: >
          histogram_quantile(0.95,
            rate(scse_search_latency_seconds_bucket[5m])
          ) > 0.5
        for: 5m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "SCSE P95 search latency exceeds 500ms"
          description: >
            Entity={{ $labels.entity_type }} mode={{ $labels.search_mode }}
            P95={{ $value | humanizeDuration }}. Check Qdrant cluster and
            embedding service latency.

      - alert: SCSESearchLatencyCritical
        expr: >
          histogram_quantile(0.99,
            rate(scse_search_latency_seconds_bucket[5m])
          ) > 2.0
        for: 2m
        labels:
          severity: critical
          module: scse
        annotations:
          summary: "SCSE P99 search latency critical (>2s)"
          description: >
            Entity={{ $labels.entity_type }} P99={{ $value | humanizeDuration }}.
            Likely Qdrant issue or OpenAI API degradation.

      - alert: SCSEIndexQueueBacklog
        expr: scse_index_queue_depth > 1000
        for: 10m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "SCSE embedding job queue backlog > 1000"
          description: >
            Entity={{ $labels.entity_type }} queue depth={{ $value }}.
            Scale up EmbeddingWorker pods or check OpenAI rate limits.

      - alert: SCSEIndexStaleness
        expr: scse_index_freshness_hours > 24
        for: 5m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "SCSE index staleness exceeds 24 hours"
          description: >
            Entity={{ $labels.entity_type }} oldest indexed={{ $value }}h ago.
            Check EmbeddingWorker health and job queue.

      - alert: SCSEQdrantCollectionEmpty
        expr: scse_qdrant_collection_size_vectors == 0
        for: 2m
        labels:
          severity: critical
          module: scse
        annotations:
          summary: "Qdrant collection has 0 vectors"
          description: >
            Collection={{ $labels.collection }} is empty. Qdrant may be
            recovering from a snapshot or data was accidentally deleted.

      - alert: SCSEPrecisionDegraded
        expr: scse_precision_at_1 < 0.70
        for: 0m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "SCSE Precision@1 below threshold 0.70"
          description: >
            Entity={{ $labels.entity_type }} precision_at_1={{ $value | humanize }}.
            Model recall has degraded — check model_evaluations table and
            consider triggering encoder retraining.

      - alert: SCSEOpenAIRateLimitHigh
        expr: rate(scse_openai_rate_limit_hits_total[5m]) > 10
        for: 3m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "OpenAI rate limit hits > 10/min"
          description: >
            Rate={{ $value }}/min. Embedding ingestion is throttled.
            Check batch size configuration and token-per-minute budget.

      - alert: SCSEZeroResultRateHigh
        expr: scse_zero_result_rate > 0.05
        for: 5m
        labels:
          severity: warning
          module: scse
        annotations:
          summary: "SCSE zero-result search rate exceeds 5%"
          description: >
            Entity={{ $labels.entity_type }} zero_result_rate={{ $value | humanizePercentage }}.
            Min_similarity threshold may be too strict, or index coverage is low.
```

### 14.3 Grafana Dashboards

**Dashboard 1: SCSE Overview**
- Search volume by entity type (requests/min time series)
- P50 / P95 / P99 search latency by entity_type (line chart)
- Zero-result rate by entity type (gauge + threshold lines)
- Error rate by error_type (stacked bar)
- Cache hit ratio trend (line chart)
- Top 10 slowest queries (table: entity_type, search_mode, latency_ms, p95)

**Dashboard 2: Search Quality**
- Precision@1 per entity type (gauge, threshold 0.80 green / 0.70 yellow / below red)
- Precision@5 per entity type
- NDCG@10 per entity type (line chart over last 30 days)
- MRR per entity type
- Recall evaluation trend (multi-line, P@1 / P@5 / NDCG@10 together)
- User feedback distribution (pie: HIGHLY_SIMILAR / SIMILAR / RELATED / UNRELATED)

**Dashboard 3: Indexing Pipeline**
- Index queue depth by entity type (bar chart, threshold line at 1000)
- Indexing throughput (entities/hour, per entity_type)
- Embedding job success/failure rate (stacked bar)
- Index freshness heatmap (entity_type × time, color = age in hours)
- FAISS index build history (table: entity_type, built_at, vector_count, duration_sec)

**Dashboard 4: Qdrant Health**
- Collection size per collection (bar chart, track growth over time)
- Qdrant ANN search latency P50/P99 (line chart per collection)
- Qdrant cluster node status (UP/DOWN table)
- Indexing segments pending optimization (gauge)
- Snapshot age per collection (table: collection, last_snapshot, size_gb)

**Dashboard 5: Embedding Service**
- OpenAI tokens used (cumulative counter, daily cost estimate at $0.13/1M)
- Embedding latency P95 (line chart per model)
- Batch queue size (gauge, embedding_jobs QUEUED count)
- EmbeddingWorker pod count (vs HPA target)
- Rate limit events per hour (bar chart, alert line at 10/min)

**Dashboard 6: User Feedback**
- Click-through rate (clicks / impressions) by entity type
- Selection rate (selected / impressions) by entity type
- Explicit label distribution per entity type (heatmap: label × entity_type × count)
- Training label accumulation rate (labels/day trend)
- Training dataset size per entity type

### 14.4 SLA Table

| Metric | Target (Green) | Warning (Yellow) | Critical (Red) |
|--------|---------------|-----------------|---------------|
| P95 search latency | < 300ms | 300–500ms | > 500ms |
| P99 search latency | < 1000ms | 1000–2000ms | > 2000ms |
| Embedding P95 latency | < 500ms | 500ms–1s | > 1s |
| Zero-result rate | < 2% | 2–5% | > 5% |
| Index freshness | < 4h | 4–24h | > 24h |
| Index queue depth | < 100 | 100–1000 | > 1000 |
| Precision@1 | > 0.80 | 0.70–0.80 | < 0.70 |
| NDCG@10 | > 0.75 | 0.65–0.75 | < 0.65 |
| Index coverage | > 99% | 95–99% | < 95% |
| Cache hit ratio | > 70% | 50–70% | < 50% |
| Qdrant collection size | > 1000 vectors | 100–1000 | 0 (empty) |
| OpenAI error rate | < 0.1% | 0.1–1% | > 1% |
