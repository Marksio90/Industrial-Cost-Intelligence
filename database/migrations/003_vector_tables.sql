-- =============================================================================
-- MIGRATION 003: Vector tables (pgvector)
-- Schema: ici_vectors
--
-- Embedding model dimensions:
--   1024 — intfloat/multilingual-e5-large  (text embeddings)
--    512 — ResNet-50 feature extractor     (CAD/image embeddings)
--    768 — sentence-transformers/all-mpnet (legacy supplier docs)
--
-- Index types:
--   HNSW  — best recall, faster queries, higher build memory
--   IVFFlat — lower memory, good for huge collections (100M+)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. MATERIAL EMBEDDINGS
--    One row per material per model version.
--    Queried for: similar-material lookup, cost estimation context retrieval.
-- ---------------------------------------------------------------------------
CREATE TABLE ici_vectors.material_embeddings (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    material_id     UUID        NOT NULL,
    model_name      VARCHAR(128) NOT NULL DEFAULT 'multilingual-e5-large',
    model_version   VARCHAR(32)  NOT NULL DEFAULT '1.0',

    -- The text that was embedded (for debugging / re-indexing)
    embedded_text   TEXT        NOT NULL,

    -- 1024-dimensional dense vector
    embedding       vector(1024) NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_material_embedding UNIQUE (tenant_id, material_id, model_name, model_version)
);

-- HNSW index: m=16 (graph connections), ef_construction=128 (build quality)
-- cosine distance — best for normalised sentence embeddings
CREATE INDEX idx_material_embeddings_hnsw
    ON ici_vectors.material_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

CREATE INDEX idx_material_embeddings_tenant ON ici_vectors.material_embeddings (tenant_id);
CREATE INDEX idx_material_embeddings_mat_id ON ici_vectors.material_embeddings (material_id);

-- ---------------------------------------------------------------------------
-- 2. CAD / IMAGE EMBEDDINGS  (ResNet-50, 512-dim)
--    Used for visual similarity search of technical drawings / 3-D models.
-- ---------------------------------------------------------------------------
CREATE TABLE ici_vectors.cad_embeddings (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    material_id     UUID        NOT NULL,
    file_key        VARCHAR(512) NOT NULL,  -- S3 / storage key
    file_hash       CHAR(64)    NOT NULL,   -- SHA-256 of source file
    model_name      VARCHAR(128) NOT NULL DEFAULT 'resnet50',
    model_version   VARCHAR(32)  NOT NULL DEFAULT '1.0',

    -- 512-dimensional image feature vector
    embedding       vector(512)  NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_cad_embedding UNIQUE (tenant_id, file_hash, model_name, model_version)
);

-- IVFFlat for image embeddings: nlist=100 (number of clusters)
-- L2 distance — standard for CNN feature vectors
CREATE INDEX idx_cad_embeddings_ivfflat
    ON ici_vectors.cad_embeddings
    USING ivfflat (embedding vector_l2_ops)
    WITH (lists = 100);

CREATE INDEX idx_cad_embeddings_tenant     ON ici_vectors.cad_embeddings (tenant_id);
CREATE INDEX idx_cad_embeddings_material   ON ici_vectors.cad_embeddings (material_id);
CREATE INDEX idx_cad_embeddings_file_hash  ON ici_vectors.cad_embeddings (file_hash);

-- ---------------------------------------------------------------------------
-- 3. SUPPLIER DOCUMENT EMBEDDINGS  (768-dim, all-mpnet)
--    Embedded supplier capability documents, certificates, datasheets.
--    Queried for: "find suppliers who can supply carbon fibre composites".
-- ---------------------------------------------------------------------------
CREATE TABLE ici_vectors.supplier_embeddings (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    supplier_id     UUID        NOT NULL,
    chunk_index     INTEGER     NOT NULL DEFAULT 0,  -- paragraph chunk within document
    document_type   VARCHAR(32) NOT NULL DEFAULT 'CAPABILITY', -- CAPABILITY | CERT | DATASHEET
    source_url      VARCHAR(512),
    chunk_text      TEXT        NOT NULL,
    model_name      VARCHAR(128) NOT NULL DEFAULT 'all-mpnet-base-v2',
    model_version   VARCHAR(32)  NOT NULL DEFAULT '1.0',

    embedding       vector(768)  NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_supplier_embedding UNIQUE (tenant_id, supplier_id, chunk_index, document_type, model_name)
);

CREATE INDEX idx_supplier_embeddings_hnsw
    ON ici_vectors.supplier_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_supplier_embeddings_tenant   ON ici_vectors.supplier_embeddings (tenant_id);
CREATE INDEX idx_supplier_embeddings_supplier ON ici_vectors.supplier_embeddings (supplier_id);

-- ---------------------------------------------------------------------------
-- 4. COST CONTEXT EMBEDDINGS  (1024-dim)
--    Historical cost breakdowns embedded for few-shot retrieval (RAG pipeline).
--    Query: "find similar cost structures to this BOM" → use as LLM context.
-- ---------------------------------------------------------------------------
CREATE TABLE ici_vectors.cost_context_embeddings (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    snapshot_id     UUID        NOT NULL,  -- references ici.cost_snapshots.id
    snapshot_date   DATE        NOT NULL,
    embedded_text   TEXT        NOT NULL,
    model_name      VARCHAR(128) NOT NULL DEFAULT 'multilingual-e5-large',
    model_version   VARCHAR(32)  NOT NULL DEFAULT '1.0',

    embedding       vector(1024) NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_cost_context UNIQUE (tenant_id, snapshot_id, model_name, model_version)
);

CREATE INDEX idx_cost_context_embeddings_hnsw
    ON ici_vectors.cost_context_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 128);  -- m=32 for higher recall on cost queries

CREATE INDEX idx_cost_context_tenant   ON ici_vectors.cost_context_embeddings (tenant_id);
CREATE INDEX idx_cost_context_snapshot ON ici_vectors.cost_context_embeddings (snapshot_id);
CREATE INDEX idx_cost_context_date     ON ici_vectors.cost_context_embeddings (snapshot_date);

-- ---------------------------------------------------------------------------
-- 5. RFQ / QUOTE EMBEDDINGS  (1024-dim)
--    Embed the full RFQ text for semantic duplicate/similarity detection.
-- ---------------------------------------------------------------------------
CREATE TABLE ici_vectors.rfq_embeddings (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    rfq_id          UUID        NOT NULL,
    embedded_text   TEXT        NOT NULL,
    model_name      VARCHAR(128) NOT NULL DEFAULT 'multilingual-e5-large',
    model_version   VARCHAR(32)  NOT NULL DEFAULT '1.0',

    embedding       vector(1024) NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_rfq_embedding UNIQUE (tenant_id, rfq_id, model_name, model_version)
);

CREATE INDEX idx_rfq_embeddings_hnsw
    ON ici_vectors.rfq_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_rfq_embeddings_tenant ON ici_vectors.rfq_embeddings (tenant_id);
CREATE INDEX idx_rfq_embeddings_rfq    ON ici_vectors.rfq_embeddings (rfq_id);

-- ---------------------------------------------------------------------------
-- Helper view: nearest-material search  (called from application layer)
-- Usage: SELECT * FROM ici_vectors.v_nearest_materials('tenant-x', '[0.1,...]'::vector, 10);
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ici_vectors.nearest_materials(
    p_tenant_id   VARCHAR,
    p_query       vector(1024),
    p_limit       INTEGER DEFAULT 10,
    p_threshold   FLOAT   DEFAULT 0.75
)
RETURNS TABLE (
    material_id   UUID,
    similarity    FLOAT,
    embedded_text TEXT
)
LANGUAGE SQL STABLE AS $$
    SELECT  e.material_id,
            1 - (e.embedding <=> p_query)  AS similarity,
            e.embedded_text
    FROM    ici_vectors.material_embeddings e
    WHERE   e.tenant_id = p_tenant_id
      AND   1 - (e.embedding <=> p_query) >= p_threshold
    ORDER BY e.embedding <=> p_query
    LIMIT   p_limit;
$$;

-- Grant
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ici_vectors TO ici_app;
GRANT SELECT                          ON ALL TABLES IN SCHEMA ici_vectors TO ici_readonly;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ici_vectors TO ici_app;
