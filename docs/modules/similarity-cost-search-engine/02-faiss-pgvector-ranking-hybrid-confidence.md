# Similarity Cost Search Engine (SCSE) — Module Documentation
## Part 02: FAISS Design, pgvector Design, Ranking Algorithms, Hybrid Search, and Confidence Score

**Document Series:** SCSE-DOC-002  
**Covers Sections:** 6 through 10  
**Audience:** Senior engineers, platform architects, procurement data scientists  
**Status:** Living document — updated with each SCSE release  
**Related Documents:** SCSE-DOC-001 (Architecture Overview, Qdrant Design, Embedding Pipeline), ADR-002 (FAISS Adoption), ADR-003 (pgvector Adoption)

---

## Table of Contents

- [Section 6: FAISS Design](#section-6-faiss-design)
  - [6.1 Why FAISS (ADR-002)](#61-why-faiss-adr-002)
  - [6.2 Index Types per Use Case](#62-index-types-per-use-case)
  - [6.3 FAISS Index Builder](#63-faiss-index-builder)
  - [6.4 FAISS Search Engine](#64-faiss-search-engine)
  - [6.5 Batch Similarity Job](#65-batch-similarity-job)
  - [6.6 FAISS Index Persistence](#66-faiss-index-persistence)
- [Section 7: pgvector Design](#section-7-pgvector-design)
  - [7.1 Why pgvector (ADR-003)](#71-why-pgvector-adr-003)
  - [7.2 Schema Setup](#72-schema-setup)
  - [7.3 HNSW Indexes](#73-hnsw-indexes)
  - [7.4 pgvector Search Queries](#74-pgvector-search-queries)
  - [7.5 pgvector Repository](#75-pgvector-repository)
- [Section 8: Ranking Algorithms](#section-8-ranking-algorithms)
  - [8.1 Two-Stage Retrieval Pipeline](#81-two-stage-retrieval-pipeline)
  - [8.2 Re-Ranker Architecture](#82-re-ranker-architecture)
  - [8.3 MMR for Diversity](#83-mmr-maximal-marginal-relevance-for-diversity)
  - [8.4 Business Rules Ranker](#84-business-rules-ranker)
  - [8.5 Cross-Entity Ranking](#85-cross-entity-ranking)
- [Section 9: Hybrid Search](#section-9-hybrid-search)
  - [9.1 Hybrid Search Architecture](#91-hybrid-search-architecture)
  - [9.2 Sparse Vector Generation (BM25)](#92-sparse-vector-generation-bm25)
  - [9.3 Query Expansion](#93-query-expansion)
  - [9.4 Reciprocal Rank Fusion (RRF)](#94-reciprocal-rank-fusion-rrf)
  - [9.5 Filter Strategy](#95-filter-strategy)
  - [9.6 Search Modes](#96-search-modes)
- [Section 10: Confidence Score](#section-10-confidence-score)
  - [10.1 Confidence Score Model](#101-confidence-score-model)
  - [10.2 Confidence Calculator](#102-confidence-calculator)
  - [10.3 Confidence Calibration](#103-confidence-calibration)
  - [10.4 Explanation API Response](#104-explanation-api-response)

---

## Section 6: FAISS Design

FAISS (Facebook AI Similarity Search) is the offline workhorse of the SCSE infrastructure. While Qdrant serves as the primary online vector database for low-latency real-time queries, FAISS fills a critical complementary role: high-throughput batch operations, offline similarity pre-computation, and large-scale candidate generation. Understanding when and why to use each system — and how they interoperate — is fundamental to maintaining the SCSE platform effectively.

This section covers the architectural decision behind adopting FAISS (ADR-002), the index type selection matrix, the Python implementation of the index builder and search engine, the nightly batch similarity job, and the persistence and versioning strategy for FAISS indexes.

---

### 6.1 Why FAISS (ADR-002)

The decision to incorporate FAISS alongside Qdrant and pgvector was captured in ADR-002. The core insight is that no single vector database excels at every workload simultaneously. Qdrant optimises for low-latency online search with rich payload filtering. pgvector optimises for transactional consistency within PostgreSQL. FAISS optimises for raw throughput and batch processing at massive scale, particularly when GPU acceleration is available.

**Primary use cases driving FAISS adoption:**

1. **Nightly full re-index and batch similarity computation.** Every night, the SCSE recomputes similarity scores across all entity pairs (materials, products, quotes, processes, suppliers) and caches the top-20 most similar items per entity in PostgreSQL. This pre-computed cache powers instant "similar items" panels in the procurement UI without any real-time vector computation. FAISS handles the all-vs-all similarity pass efficiently via batch search APIs that are unavailable in Qdrant.

2. **Cross-entity batch similarity jobs.** When the procurement team onboards a new supplier catalogue containing thousands of new products, SCSE must compute similarity between all incoming products and all existing materials and processes. This is a large batch job unsuited for incremental online queries. FAISS loads all vectors into memory and executes chunked batch searches in minutes rather than hours.

3. **Large-scale candidate generation as pre-filter for Qdrant.** For some analytical workflows (e.g., generating procurement recommendations across a 2M-product catalogue), FAISS performs an approximate first pass to reduce the candidate set from 2M to ~500 items, which are then re-ranked using Qdrant's richer payload filtering. This two-tier approach avoids saturating Qdrant's query quota during batch analytics.

4. **GPU-accelerated embedding generation at scale.** FAISS ships native CUDA support. When re-embedding large corpora after a model upgrade, FAISS GPU indexes are loaded directly on the embedding server's GPU, enabling throughput of ~500K vectors/second compared to ~20K/second for CPU-only workloads.

**Comparison of vector backends:**

| Dimension | Qdrant | FAISS | pgvector |
|---|---|---|---|
| Query latency (P50) | < 5 ms | 2–20 ms (in-process) | 10–50 ms |
| Query latency (P99) | < 15 ms | 50–200 ms (batch) | 50–200 ms |
| Throughput (QPS, single node) | 2,000–5,000 | 50,000+ (batch) | 200–500 |
| Payload filtering | Native, indexed | None (post-filter only) | Full SQL WHERE |
| Clustering / analytics | No | Yes (kmeans, PCA) | No |
| Exact search support | Yes (via flat) | Yes (IndexFlatIP) | Yes (sequential scan) |
| Multi-tenancy | Collections + namespaces | Manual sharding | Schemas / RLS |
| Persistence | Native WAL + snapshots | Manual (write_index) | PostgreSQL MVCC |
| GPU support | No | Yes (CUDA) | No |
| Operational complexity | Medium (dedicated service) | Low (library, in-process) | Low (PostgreSQL extension) |
| Horizontal scaling | Native distributed | Manual sharding + merge | pgBouncer + read replicas |

The table makes the tradeoffs clear. FAISS is not a replacement for Qdrant's filtering capabilities or pgvector's transactional guarantees. It is the right tool for pure batch similarity computation at scale. The SCSE architecture uses all three systems in concert, routing each workload to the backend that handles it most efficiently.

---

### 6.2 Index Types per Use Case

FAISS offers a rich menu of index types, each embodying a different set of accuracy/speed/memory tradeoffs. Choosing the wrong index type for an entity population can result in either unacceptable latency, excessive memory usage, or accuracy degradation below the SCSE quality SLA. The following table documents the selected index type per entity type and workload, along with the rationale.

| Index Type | Use Case | Target Entities | Trade-off |
|---|---|---|---|
| IndexFlatIP | Ground truth / evaluation baseline | All entities | Exact nearest neighbour — O(n) scan, no approximation. Used exclusively in offline evaluation harnesses to measure ANN recall against ground truth. Not used in production query paths. |
| IndexIVFFlat | Batch recall (corpus < 1M vectors) | Materials, Processes | Inverted-file index with flat (uncompressed) cluster storage. Requires training to build cluster centroids. nprobe controls the accuracy/speed tradeoff. Typical recall@100 > 98% with nprobe=32 on 256 clusters. |
| IndexIVFPQ | Memory-efficient large-scale search (corpus > 1M vectors) | Products, Quotes | Combines IVF clustering with Product Quantization compression. Reduces memory ~32x at the cost of ~3–5% accuracy loss on recall@100. Acceptable for the Products and Quotes corpora which can exceed 5M vectors. |
| IndexHNSWFlat | Low-latency ANN within FAISS (in-process) | Suppliers | Hierarchical Navigable Small World graph. No training required. Delivers P99 < 10 ms for the Suppliers corpus (< 200K vectors). Best choice when FAISS is used for synchronous lookup during request handling rather than bulk batch jobs. |
| IndexIVFSQ8 | GPU-optimised batch processing | Cross-entity batch jobs | Scalar Quantization to INT8 (8-bit). Compatible with FAISS GPU kernels. Used in the nightly cross-entity similarity job when an A100 GPU instance is available. Offers ~4x throughput vs FP32 on GPU. |

**Operational notes:**

- `IndexIVFFlat` and `IndexIVFPQ` require a training phase. The `train()` call must receive at least `39 * n_clusters` representative vectors (FAISS internal heuristic). For small entity populations that have not yet accumulated enough vectors, fall back to `IndexFlatIP` until the training threshold is met.
- `nprobe` on IVF indexes is a runtime parameter and can be adjusted without rebuilding the index. The SCSE ships a per-entity `nprobe` configuration in `config/faiss_config.yaml`. Increasing `nprobe` improves recall at the cost of latency; the nightly evaluation job reports recall@20 and P99 latency for each entity and will alert (via PagerDuty) if recall@20 drops below 0.95.
- HNSW graphs are not serialisable to Qdrant's HNSW format. FAISS HNSW indexes are stored in FAISS's own binary format and cannot be migrated to Qdrant. The two systems maintain independent index structures.

---

### 6.3 FAISS Index Builder

The `FAISSIndexBuilder` class encapsulates all index construction logic. It is invoked by the nightly Airflow DAG (`scse_reindex`) as well as ad-hoc during the onboarding of large supplier catalogues. The class is intentionally stateless so that multiple builder instances can run concurrently in the batch job without interference.

All vectors are L2-normalised before indexing. This is required because the SCSE uses inner-product similarity (cosine similarity on unit-normalised vectors equals inner product), and FAISS's `METRIC_INNER_PRODUCT` correctly computes this only when vectors have unit norm. Failing to normalise produces silently incorrect similarity scores that are difficult to debug in production.

```python
import faiss
import numpy as np

class FAISSIndexBuilder:
    def __init__(self, dim: int = 1024, index_type: str = "IVFFlat"):
        self.dim = dim
        self.index_type = index_type
    
    def build_ivfflat(self, vectors: np.ndarray, n_clusters: int = 256) -> faiss.Index:
        quantizer = faiss.IndexFlatIP(self.dim)
        index = faiss.IndexIVFFlat(quantizer, self.dim, n_clusters, faiss.METRIC_INNER_PRODUCT)
        faiss.normalize_L2(vectors)
        index.train(vectors)
        index.add(vectors)
        index.nprobe = 32  # search 32 clusters (12.5% of 256)
        return index
    
    def build_ivfpq(self, vectors: np.ndarray, n_clusters: int = 1024, 
                    m_subvectors: int = 16, bits: int = 8) -> faiss.Index:
        quantizer = faiss.IndexFlatIP(self.dim)
        index = faiss.IndexIVFPQ(quantizer, self.dim, n_clusters, m_subvectors, bits)
        faiss.normalize_L2(vectors)
        index.train(vectors)
        index.add(vectors)
        return index
    
    def build_hnsw(self, vectors: np.ndarray, M: int = 32, ef_construction: int = 128) -> faiss.Index:
        index = faiss.IndexHNSWFlat(self.dim, M, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = ef_construction
        index.hnsw.efSearch = 64
        faiss.normalize_L2(vectors)
        index.add(vectors)
        return index
    
    def save(self, index: faiss.Index, path: str) -> None:
        faiss.write_index(index, path)
    
    def load(self, path: str) -> faiss.Index:
        return faiss.read_index(path)
    
    def to_gpu(self, index: faiss.Index) -> faiss.Index:
        res = faiss.StandardGpuResources()
        return faiss.index_cpu_to_gpu(res, 0, index)
```

**Design notes on parameter choices:**

- `n_clusters=256` for `IndexIVFFlat` (Materials, Processes): Materials and Processes corpora are expected to remain below 500K vectors for the foreseeable roadmap. The FAISS rule of thumb is `n_clusters ≈ sqrt(N)`. For N=500K, `sqrt(500K) ≈ 707`. We use 256 as a conservative starting point that provides good recall without requiring extremely large training sets. This will be revisited when the corpus exceeds 200K vectors.

- `n_clusters=1024` for `IndexIVFPQ` (Products, Quotes): The Products corpus is projected to reach 2M+ vectors. With 1024 clusters and `nprobe=64`, we probe ~6.25% of the index per query, which empirically delivers recall@100 > 0.94 in offline evaluation.

- `m_subvectors=16, bits=8` for `IndexIVFPQ`: The 1024-dimensional fused vector is divided into 16 sub-vectors of 64 dimensions each, each quantised to 256 centroids (8 bits). This yields a 16-byte compressed representation per vector versus 4096 bytes for FP32 — a 256x memory reduction. The accuracy tradeoff is acceptable for batch candidate generation where a subsequent re-ranking pass compensates for recall loss.

- `M=32, ef_construction=128` for HNSW: M controls the number of bidirectional links per node in the HNSW graph. Higher M improves recall but increases memory and build time. `M=32` is the standard SCSE recommendation for corpora under 500K vectors. `ef_construction=128` controls build-time graph quality — higher values produce better graphs but take longer to build. `efSearch=64` is set at query time and can be raised to trade latency for recall.

- The `to_gpu()` method wraps a CPU index for GPU execution using a single-GPU resource. For multi-GPU batch jobs, use `faiss.index_cpu_to_all_gpus()` instead, which FAISS automatically distributes across all visible CUDA devices.

---

### 6.4 FAISS Search Engine

The `FAISSSearchEngine` provides a unified search interface over a collection of pre-built FAISS indexes. Each entity type maps to its own index, enabling parallel search across entity types when performing cross-entity queries. The ID map is critical: FAISS indexes operate on integer row positions (0 to N-1), but SCSE entities are identified by UUIDs. The `id_maps` dictionary bridges this gap by mapping FAISS position integers back to the corresponding entity UUIDs.

```python
class FAISSSearchEngine:
    def __init__(self, indexes: dict[str, faiss.Index], id_maps: dict[str, list[UUID]]):
        self.indexes = indexes  # entity_type → faiss.Index
        self.id_maps = id_maps  # entity_type → [UUID at position i]
    
    def search(
        self,
        entity_type: str,
        query_vector: np.ndarray,
        k: int = 100,  # large k for candidate generation
    ) -> list[tuple[UUID, float]]:
        faiss.normalize_L2(query_vector.reshape(1, -1))
        distances, indices = self.indexes[entity_type].search(query_vector.reshape(1, -1), k)
        results = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx != -1:  # -1 means not found
                results.append((self.id_maps[entity_type][idx], float(dist)))
        return results
    
    def batch_search(
        self,
        entity_type: str,
        query_vectors: np.ndarray,
        k: int = 50,
    ) -> list[list[tuple[UUID, float]]]:
        faiss.normalize_L2(query_vectors)
        distances, indices = self.indexes[entity_type].search(query_vectors, k)
        # ... build results per query
```

**Key implementation details:**

The `idx != -1` guard is essential. When an IVF index is queried with `k` greater than the number of indexed vectors, or when `nprobe` is set aggressively low, FAISS returns -1 as a sentinel for unfilled result slots. Failing to filter these results causes `IndexError` when looking up positions in the `id_maps` list — a class of bug that is easy to introduce and hard to diagnose under load.

The `batch_search` method accepts a matrix of query vectors and dispatches a single FAISS search call, which is substantially more efficient than looping individual `search()` calls. FAISS internally parallelises batch searches across CPU cores using OpenMP threading. For the nightly batch job, batches of 1,000–5,000 query vectors are processed per FAISS call, with chunking logic in the `BatchSimilarityJob` to bound peak memory usage.

The `k=100` default for `search()` reflects the SCSE two-stage retrieval architecture. FAISS is used for the first-stage candidate generation pass, returning a large candidate set that is subsequently re-ranked by the Python re-ranker (Section 8). Requesting more candidates at this stage costs latency linearly but significantly improves the quality of the final top-K after re-ranking — a standard information retrieval tradeoff.

The `id_maps` are constructed and persisted alongside the FAISS index file during the nightly rebuild. They are stored as JSON (for small corpora) or as numpy arrays of UUID bytes (for corpora > 100K entities) in the same S3 prefix as the `.index` file. Loading the wrong `id_map` for a given index file produces silently incorrect UUID mappings, so both artifacts include a shared `index_version` UUID in their metadata that the loading logic validates at startup.

---

### 6.5 Batch Similarity Job

The `BatchSimilarityJob` is the most computationally intensive component of the SCSE platform. It runs nightly to pre-compute pairwise similarity for all active entities and populate the `similarity_cache` PostgreSQL table, which powers the "Similar Items" feature in the procurement UI without any online vector computation.

```python
class BatchSimilarityJob:
    """Nightly job: recompute similarity for all entities, store in PostgreSQL cache."""
    
    async def run(self, entity_type: EntityType, top_k: int = 20) -> JobResult:
        # 1. Load all active embeddings from PostgreSQL (scse_vector_records)
        # 2. Build FAISS index in memory
        # 3. Batch search all-vs-all (chunked to avoid OOM)
        # 4. Write top-K pairs to similarity_cache table
        # 5. Publish BatchSimilarityComputedEvent to Kafka
        
    async def _chunk_search(self, vectors: np.ndarray, index: faiss.Index, 
                             chunk_size: int = 1000) -> np.ndarray:
        # Process in chunks to limit memory to ~4GB peak
```

**Operational design principles:**

1. **Chunked processing to bound memory.** An all-vs-all similarity computation over N entities produces N² distance values, which for N=500K would be 250 billion floats — clearly infeasible to hold in memory. The `_chunk_search` method processes the entity corpus in chunks of 1,000 vectors per batch search call. For each chunk, FAISS returns a `(chunk_size, top_k)` distance matrix (~1,000 × 50 × 4 bytes = 200 KB per chunk). Total peak memory for the batch job is bounded at approximately 4 GB regardless of corpus size.

2. **Write-back via PostgreSQL COPY.** Rather than issuing individual `INSERT` statements for each similarity pair, the job accumulates results in memory and uses PostgreSQL `COPY FROM` (via asyncpg's `copy_records_to_table`) to bulk-insert the entire chunk's results in a single network round-trip. This reduces write time from ~hours to ~minutes for large corpora.

3. **Kafka event on completion.** Publishing a `BatchSimilarityComputedEvent` to the `scse.events.similarity` topic allows downstream services (e.g., the recommendation engine, the procurement dashboard cache invalidator) to react to fresh similarity data without polling. The event payload includes the `entity_type`, `job_run_id`, `total_pairs_written`, and `completed_at` timestamp.

4. **Idempotency.** The job is designed to be re-runnable without producing duplicate data. The `similarity_cache` table has a unique constraint on `(entity_id, similar_entity_id, entity_type)`. The write-back uses `INSERT ... ON CONFLICT DO UPDATE` to overwrite stale similarity scores rather than accumulating duplicates. If the job fails mid-way, the next run safely overwrites any partial results.

5. **Monitoring.** The job emits Prometheus metrics: `scse_batch_job_duration_seconds`, `scse_batch_job_pairs_written_total`, and `scse_batch_job_entities_processed_total`. A Grafana alert fires if the job does not complete within 4 hours (the SLO for the nightly window).

---

### 6.6 FAISS Index Persistence

FAISS does not provide a built-in persistence layer — indexes exist purely in memory and must be explicitly serialised to disk. The SCSE implements a disciplined persistence and versioning strategy to ensure that index files are consistent with the vector data they represent, and that rollback is possible if a nightly rebuild produces a degraded index.

**Rebuild schedule:**
- Trigger: Airflow DAG `scse_reindex` scheduled at **02:00 UTC** daily
- Duration SLO: < 2 hours for all entity types combined
- Pre-requisite: The `scse_embed` DAG (embedding generation) must have completed successfully for the day, validated via Airflow sensor on XCom state

**Storage layout in S3:**

```
s3://ici-scse-indexes/
  faiss/
    materials/
      2024-01-15/
        index.faiss
        id_map.json
        metadata.json        ← includes index_version, vector_count, build_duration_s
      current -> 2024-01-15  ← S3 object tag, not a symlink
    products/
      ...
    quotes/
      ...
    processes/
      ...
    suppliers/
      ...
```

**Local cache path:** `/var/lib/scse/faiss/{entity_type}/current.index`

Each SCSE API pod downloads and caches the current index file on startup. The download is performed asynchronously and non-blocking — the pod becomes healthy and begins serving requests backed by Qdrant immediately, while FAISS indexes warm up in the background. A readiness probe variant (`/health/faiss`) reports whether all FAISS indexes are loaded, used by workloads that specifically require FAISS (e.g., batch analytics endpoints).

**Warm-up sequence:**
1. Pod starts, Qdrant connection pool initialised — pod enters `READY` state
2. Background task begins downloading `current.index` files from S3 for each entity type
3. For each downloaded file: load into memory via `faiss.read_index()`, validate vector dimensions match configured `dim`, register in the `FAISSSearchEngine`
4. Once all entity indexes are loaded, set internal flag `faiss_ready=True`; the `/health/faiss` probe returns 200

**Version tracking:**

The `faiss_index_versions` PostgreSQL table records metadata for every index build, enabling audit trails and rollback:

```sql
CREATE TABLE scse.faiss_index_versions (
    version_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type         VARCHAR(50) NOT NULL,
    build_date          DATE NOT NULL,
    s3_path             TEXT NOT NULL,
    vector_count        INTEGER NOT NULL,
    index_type          VARCHAR(50) NOT NULL,
    n_clusters          INTEGER,
    nprobe              INTEGER,
    recall_at_20        FLOAT,          -- from offline evaluation
    p99_latency_ms      FLOAT,
    is_current          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Before promoting a new index build to `is_current = TRUE`, the nightly job runs the SCSE offline evaluation harness (using `IndexFlatIP` ground truth) to compute `recall@20`. If `recall@20 < 0.95`, the promotion is blocked, a PagerDuty alert fires, and the previous night's index remains active.

---

## Section 7: pgvector Design

Where FAISS handles bulk offline workloads and Qdrant handles online ANN queries, pgvector serves a third distinct purpose: transactional vector search within the same PostgreSQL database that stores the relational procurement data. This section covers the architectural rationale for pgvector (ADR-003), the schema design, index configuration, production SQL query patterns, and the Python repository layer.

---

### 7.1 Why pgvector (ADR-003)

ADR-003 captures the decision to use pgvector for transactional similarity search within the SCSE platform. The core argument is that certain procurement workflows require vector search to participate in a broader database transaction, with access to relational data, without tolerating any eventual consistency lag.

**The canonical example motivating pgvector adoption:**

> "Find the 10 most similar quotes to the current draft quote, considering only quotes from suppliers with OTD (on-time delivery) > 90%, scorecard > 80, active contracts, and a validity date in the future."

This query requires joining vector similarity with supplier scorecard data, contract status, and quote validity dates — all relational data living in PostgreSQL. Executing this with Qdrant would require a two-step process: fetch candidates from Qdrant, then join with PostgreSQL to apply relational filters. This two-step pattern has several failure modes: stale Qdrant payload data (Qdrant payloads are eventually consistent with PostgreSQL), race conditions during quote creation where newly inserted supplier records are not yet indexed in Qdrant, and increased application complexity.

With pgvector, the entire query executes in a single SQL statement within the same transaction that is creating the quote, ensuring complete data consistency.

**pgvector strengths in the SCSE context:**

- **Transactional search:** vector queries can participate in `BEGIN`/`COMMIT` transactions alongside `INSERT`, `UPDATE`, and `DELETE` operations on relational tables
- **Strong consistency:** no replication lag or eventual-consistency window between vector index and relational data
- **SQL composability:** `WHERE`, `JOIN`, `GROUP BY`, `HAVING`, window functions, and CTEs can be freely combined with vector distance operators
- **Operational simplicity:** no additional infrastructure — vectors live in the same PostgreSQL cluster already operated by the platform team

**pgvector limitations to be aware of:**

- **Throughput ceiling:** pgvector HNSW handles approximately 200–500 QPS per PostgreSQL instance for 1024-dimensional vectors. For higher-throughput workloads, requests must be routed to Qdrant
- **Vector count ceiling:** pgvector performs well up to approximately 10M vectors per table. Beyond this scale, index build times, maintenance overhead, and query latency degrade. The Products corpus (projected 2M vectors) is within bounds; if it exceeds 8M, a migration plan to shard across schema-partitioned tables or migrate to Qdrant must be evaluated
- **No sparse vector support:** pgvector does not natively support sparse vectors. BM25 / SPLADE sparse search is not available in pgvector; this capability is exclusively handled by Qdrant (Section 9)

---

### 7.2 Schema Setup

The pgvector schema is designed to co-locate embeddings with their corresponding entity records while keeping the vector tables strictly separate from the relational core schemas. This separation allows independent vacuuming, index maintenance, and access-control policies for the high-churn vector tables without impacting the stability of the transactional schemas.

The `scse` schema is owned by the `scse_service` role and is accessible read-only to the `analytics_reader` role. Write access to the `scse` schema is restricted to the `scse_embedding_writer` role used exclusively by the Embedding Pipeline service.

```sql
CREATE SCHEMA scse;
CREATE EXTENSION IF NOT EXISTS vector;

-- Per-entity embedding tables
CREATE TABLE scse.product_embeddings (
    embedding_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      UUID NOT NULL REFERENCES material_intelligence.products(product_id),
    text_vector     vector(3072),
    structured_vector vector(512),
    fused_vector    vector(1024),
    model_version   VARCHAR(50) NOT NULL,
    encoder_version VARCHAR(50) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_product_embedding_active UNIQUE (product_id, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

-- Similar tables for quotes, materials, processes, suppliers
-- (same structure with appropriate FK references)
```

**Schema design decisions:**

1. **Three vector columns per entity.** Each embedding table stores the `text_vector` (3072-dimensional output of the text encoder), `structured_vector` (512-dimensional output of the structured feature encoder), and `fused_vector` (1024-dimensional concatenated and projected fusion). Storing all three enables the SQL query layer to select the most appropriate similarity metric for each use case without requiring separate index tables. See Section 4 (SCSE-DOC-001) for the fusion architecture.

2. **`is_active` boolean with partial unique constraint.** The `UNIQUE (product_id, is_active) DEFERRABLE INITIALLY DEFERRED` constraint ensures at most one active embedding per entity at any time, while allowing the upsert pattern to atomically deactivate the old embedding and insert the new one within a single transaction. The `DEFERRABLE INITIALLY DEFERRED` clause is required because the constraint check is deferred to commit time, allowing the intermediate state (two rows with `is_active=TRUE` for the same product) to exist transiently within the transaction.

3. **Model version tracking.** `model_version` and `encoder_version` are stored alongside each embedding. This is critical for detecting embedding staleness: when the embedding model is upgraded, a background job queries `WHERE model_version != $current_model_version` to identify entities that need re-embedding. Without these columns, the system cannot distinguish fresh from stale embeddings.

4. **Foreign key constraints.** Each embedding table references its counterpart in the appropriate relational schema (`material_intelligence.products`, `cost_history.quote_records`, etc.). This ensures referential integrity: an embedding cannot exist for a deleted entity. Cascade delete is not configured — if a product is deleted, its embedding row must be explicitly deactivated (set `is_active = FALSE`) before the product record is deleted, to allow the deletion audit trail to capture the final embedding state.

---

### 7.3 HNSW Indexes

pgvector supports two index types: `IVFFlat` (inverted file, similar to FAISS's `IndexIVFFlat`) and `HNSW` (hierarchical navigable small world). For SCSE's query patterns, HNSW is preferred for online query paths due to its superior latency profile and the absence of a training phase. IVFFlat is retained for bulk operations where build time and index size matter more than query latency.

```sql
-- HNSW indexes for each vector type and entity
CREATE INDEX idx_product_emb_fused_hnsw ON scse.product_embeddings
    USING hnsw (fused_vector vector_cosine_ops)
    WITH (m = 32, ef_construction = 128);

CREATE INDEX idx_product_emb_text_hnsw ON scse.product_embeddings
    USING hnsw (text_vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- IVFFlat for bulk operations
CREATE INDEX idx_material_emb_fused_ivfflat ON scse.material_embeddings
    USING ivfflat (fused_vector vector_cosine_ops)
    WITH (lists = 100);
```

**Index parameter guidance:**

- **`m = 32` for fused_vector HNSW:** The `m` parameter (bidirectional link count) controls the graph connectivity and directly impacts both query recall and index size. `m=32` is the standard recommendation for 1024-dimensional vectors in a procurement domain search task, providing recall@10 > 0.97 in benchmarks on the SCSE evaluation dataset.

- **`m = 16` for text_vector HNSW:** The text vector index is queried less frequently (only in SEMANTIC search mode, Section 9.6) and is accessed on a 3072-dimensional space where higher dimensionality inherently provides better separability. Lower `m` reduces index memory footprint.

- **`ef_construction`:** Controls build-time graph quality. Higher values produce better-connected graphs with higher recall, but increase index build time approximately linearly. `ef_construction=128` provides a good build-time/quality balance for the SCSE use case. Index build for 1M vectors at this setting takes approximately 15–20 minutes on a 16-core PostgreSQL instance.

- **`lists = 100` for IVFFlat:** The number of IVF cluster lists. PostgreSQL/pgvector recommends `lists ≈ rows / 1000` for datasets up to 1M rows, or `sqrt(rows)` for larger datasets. The `SET ivfflat.probes = N` session parameter controls how many lists are scanned at query time — the equivalent of FAISS's `nprobe`.

- **`vector_cosine_ops`:** The operator class specifies that similarity is measured using cosine distance (`<=>` operator in pgvector). This is appropriate for the SCSE because all vectors are L2-normalised before storage, making cosine distance equivalent to inner-product distance. The alternative `vector_ip_ops` (inner product) or `vector_l2_ops` (Euclidean distance) would produce incorrect results for normalised vectors.

**Index maintenance:** HNSW indexes in pgvector are append-only structures. PostgreSQL's MVCC mechanism means deleted rows leave dead tuples that the HNSW index continues to traverse until `VACUUM` is run. For tables with high write throughput (e.g., `quote_embeddings` which receives new records every time a quote is created), schedule `VACUUM ANALYZE scse.quote_embeddings` at least twice daily to maintain query performance.

---

### 7.4 pgvector Search Queries

The following SQL patterns are the canonical production queries used by the SCSE platform. They are parameterised stored procedures in the production database but are shown here as plain SQL for clarity. Each query demonstrates the key advantage of pgvector: combining vector similarity search with relational joins and business-logic filters in a single, consistent SQL statement.

**Finding similar materials with relational filters:**

```sql
-- Find similar materials (with relational filters in same query)
WITH query_vector AS (
    SELECT fused_vector 
    FROM scse.material_embeddings 
    WHERE material_id = $1 AND is_active = TRUE
)
SELECT 
    m.material_id,
    m.name,
    m.material_group,
    m.price_eur_per_kg,
    1 - (me.fused_vector <=> qv.fused_vector) AS cosine_similarity,
    me.fused_vector <=> qv.fused_vector AS cosine_distance
FROM scse.material_embeddings me
JOIN material_intelligence.materials m USING (material_id)
CROSS JOIN query_vector qv
WHERE 
    me.is_active = TRUE
    AND me.material_id != $1
    AND m.status = 'ACTIVE'
    AND m.material_group = $2  -- optional filter
    AND me.fused_vector <=> qv.fused_vector < 0.30  -- similarity > 0.70
ORDER BY cosine_distance ASC
LIMIT 20;

-- Find similar quotes with supplier constraints
WITH query_vector AS (
    SELECT fused_vector FROM scse.quote_embeddings WHERE quote_id = $1 AND is_active = TRUE
)
SELECT 
    q.quote_id,
    q.unit_price_eur,
    q.supplier_id,
    s.name AS supplier_name,
    s.scorecard_total,
    1 - (qe.fused_vector <=> qv.fused_vector) AS similarity,
    ROW_NUMBER() OVER (PARTITION BY q.supplier_id ORDER BY qe.fused_vector <=> qv.fused_vector) AS supplier_rank
FROM scse.quote_embeddings qe
JOIN cost_history.quote_records q USING (quote_id)
JOIN supplier_intelligence.suppliers s ON q.supplier_id = s.supplier_id
CROSS JOIN query_vector qv
WHERE 
    qe.is_active = TRUE
    AND q.quote_id != $1
    AND s.status = 'ACTIVE'
    AND s.scorecard_total >= $2  -- min score filter
    AND q.validity_date >= CURRENT_DATE
    AND qe.fused_vector <=> qv.fused_vector < 0.35
ORDER BY cosine_distance ASC
LIMIT 30;
```

**Query performance notes:**

- The cosine distance threshold (`< 0.30` for materials, `< 0.35` for quotes) corresponds to similarity thresholds of `> 0.70` and `> 0.65` respectively. These are the SCSE minimum similarity thresholds from the quality SLA. Queries that return zero results (no sufficiently similar entities found) are expected and valid — the application layer handles this with a "no similar items found" UI state rather than returning low-quality matches.

- The `CROSS JOIN query_vector qv` pattern is the standard pgvector idiom for embedding the query vector as a CTE. The alternative of passing the vector as a `$N::vector` parameter directly in the WHERE clause also works but makes the query harder to read and debug. Both approaches produce the same query plan.

- The `supplier_rank` window function in the quotes query provides deduplication: if multiple similar quotes exist from the same supplier, only the most similar one per supplier is surfaced at the application layer. The `ROW_NUMBER()` result is used in the application code to filter `WHERE supplier_rank = 1` after fetching the raw results.

- For `EXPLAIN ANALYZE` on these queries, look for `Index Scan using idx_material_emb_fused_hnsw` in the query plan. If a sequential scan appears instead, the HNSW index is not being used — typically because the relational `WHERE` clause filters down to a very small set that the planner deems better served by a sequential scan. This is correct planner behaviour for highly selective filters.

---

### 7.5 pgvector Repository

The `PgvectorRepository` class is the data-access layer for all pgvector operations within the SCSE. It is injected into the `SimilaritySearchService` via dependency injection and can be independently mocked for unit tests. All methods are `async` and use the `asyncpg` connection pool.

```python
class PgvectorRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def upsert_embedding(self, record: VectorRecord) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO scse.{table} 
                    (entity_id, text_vector, structured_vector, fused_vector, model_version, encoder_version)
                VALUES ($1, $2::vector, $3::vector, $4::vector, $5, $6)
                ON CONFLICT (entity_id) WHERE is_active = TRUE
                DO UPDATE SET
                    text_vector = EXCLUDED.text_vector,
                    structured_vector = EXCLUDED.structured_vector,
                    fused_vector = EXCLUDED.fused_vector,
                    model_version = EXCLUDED.model_version,
                    encoder_version = EXCLUDED.encoder_version,
                    updated_at = now()
            """.format(table=record.collection),
            str(record.entity_id), record.text_vector, record.structured_vector, 
            record.fused_vector, record.embedding_model, record.encoder_version)
    
    async def find_similar(
        self,
        entity_type: str,
        query_id: UUID,
        filters: dict,
        limit: int = 20,
        min_similarity: float = 0.70,
    ) -> list[SimilarityResult]:
        ...
```

**Implementation notes:**

The `upsert_embedding` method uses a partial-index conflict target (`ON CONFLICT (entity_id) WHERE is_active = TRUE`) that matches the partial unique constraint defined in Section 7.2. This ensures that only the currently active embedding for an entity is updated, leaving historical (inactive) embeddings untouched for audit purposes.

The `{table}` string substitution is intentional but requires caution: `record.collection` must be a validated enum member from a closed set of allowed table names (`product_embeddings`, `material_embeddings`, `quote_embeddings`, etc.) rather than a raw string from external input. The `VectorRecord` domain object enforces this at construction time via a `CollectionType` enum. This pattern is safe because the substitution happens before the parameterised query is executed and the value is never derived from user input.

The `find_similar` method (stub shown) constructs and executes the appropriate parameterised query from Section 7.4 based on `entity_type`. A query factory pattern is used internally to select the correct SQL template for each entity type, rather than a single query that branches with `IF` statements in SQL — this makes each query independently optimisable by the PostgreSQL query planner.

---

## Section 8: Ranking Algorithms

Retrieving candidate items from vector indexes is only the first half of the SCSE search pipeline. Raw vector similarity scores are not sufficient to rank results for procurement professionals: a semantically similar material that is on a watchlisted supplier, has an expired certification, or comes from a geopolitically high-risk region should rank below a slightly less semantically similar but commercially sound alternative. This section describes the SCSE ranking architecture: the two-stage retrieval pipeline, the re-ranker, the diversity algorithm, the business rules engine, and cross-entity ranking.

---

### 8.1 Two-Stage Retrieval Pipeline

The SCSE ranking pipeline follows the industry-standard two-stage retrieval architecture, optimised for the procurement domain:

**Stage 1: ANN Recall (Qdrant / FAISS)**
- Returns top-200 candidates with raw vector scores
- Optimised for recall: it is acceptable to return irrelevant items at this stage, as long as all truly relevant items are included
- Latency budget: < 10 ms
- No business rules applied at this stage

**Stage 2: Re-ranking (Python re-ranker)**
- Input: top-200 ANN candidates
- Processing steps:
  1. Cross-encoder score computation (semantic re-scoring using a fine-tuned cross-encoder model)
  2. Structured similarity score (feature vector cosine similarity)
  3. Business rules adjustment (boosts and penalties from procurement rules)
  4. Diversity penalty (MMR to prevent redundant results)
  5. Confidence score computation (Section 10)
- Output: final top-K results with confidence scores and explanation breakdowns
- Latency budget: < 50 ms (target: < 30 ms)

This two-stage design is motivated by a fundamental efficiency argument: applying a heavyweight re-ranker (cross-encoder) to 2M candidate items would be computationally prohibitive. By first using approximate nearest-neighbour search to reduce the candidate set from 2M to 200, and then applying the re-ranker to only 200 items, the system achieves the quality of the expensive cross-encoder at a fraction of the cost.

The choice of k=200 for the ANN stage is backed by offline evaluation: with a well-tuned HNSW index (recall@200 > 0.99), there is negligible probability that a relevant item ranks outside the top-200 by vector similarity alone. Increasing k beyond 200 yields diminishing returns on final result quality while linearly increasing re-ranker latency.

---

### 8.2 Re-Ranker Architecture

The `SimilarityReRanker` orchestrates all Stage 2 scoring and applies the weighted combination to produce the final ranked list. The weights in `RankingWeights` are configurable via the SCSE control plane API and can be adjusted per search context (e.g., different weights for strategic sourcing versus spot-buy search modes).

```python
class SimilarityReRanker:
    def __init__(self):
        self.cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
        self.weights = RankingWeights(
            semantic_similarity=0.40,
            structured_similarity=0.30,
            business_relevance=0.20,
            diversity_bonus=0.10,
        )
    
    def rerank(
        self,
        query: SimilarityQuery,
        candidates: list[SimilarityCandidate],
    ) -> list[RankedResult]:
        for candidate in candidates:
            candidate.semantic_score = self._semantic_score(query, candidate)
            candidate.structured_score = self._structured_score(query, candidate)
            candidate.business_score = self._business_score(query, candidate)
        
        # Apply diversity: penalize consecutive same-supplier or same-category results
        results = self._apply_mmr(candidates, lambda_=0.7)
        
        return [
            RankedResult(
                rank=i+1,
                candidate=r,
                final_score=self._weighted_score(r),
                confidence=self._confidence_score(r),
            )
            for i, r in enumerate(results[:query.top_k])
        ]
    
    def _structured_score(self, query: SimilarityQuery, candidate: SimilarityCandidate) -> float:
        """Cosine similarity between structured feature vectors."""
        return cosine_similarity(query.structured_vector, candidate.structured_vector)
    
    def _business_score(self, query: SimilarityQuery, candidate: SimilarityCandidate) -> float:
        """Business relevance: higher for preferred suppliers, active status, etc."""
        score = 0.0
        if candidate.payload.get("supplier_tier") == 1: score += 0.2
        if candidate.payload.get("status") == "ACTIVE": score += 0.3
        if candidate.payload.get("scorecard_total", 0) >= 85: score += 0.3
        # ... more rules
        return min(score, 1.0)
```

**Weight rationale:**

- **Semantic similarity (0.40):** The primary signal. A high fused-vector cosine similarity means the items are genuinely alike in their textual descriptions and technical characteristics. This is the foundational relevance signal.

- **Structured similarity (0.30):** Technical feature alignment (dimensions, tolerances, material grade, process parameters) is critical in manufacturing procurement. Two items can be semantically described similarly but differ critically on a key numerical specification. The structured vector captures this numerical similarity and contributes 30% of the final score.

- **Business relevance (0.20):** Procurement decisions are not made on technical similarity alone. Preferred suppliers, active contracts, and quality scorecards must influence ranking. This component ensures commercially sound results surface above technically similar but commercially problematic ones.

- **Diversity bonus (0.10):** A small diversity component prevents result sets from being dominated by near-duplicates. In a large material catalogue, multiple variants of the same base material may all score similarly — the MMR diversity mechanism (Section 8.3) distributes weight across categories.

**Cross-encoder model:** The `cross-encoder/ms-marco-MiniLM-L-12-v2` is a lightweight cross-encoder fine-tuned for passage relevance scoring. In the SCSE context, it re-scores (query_text, candidate_text) pairs to produce a relevance score that is more accurate than the bi-encoder fused similarity. The model runs in PyTorch on CPU; at 200 candidates per query, inference takes approximately 15–25 ms on a standard 8-core pod.

For procurement-critical deployments, a domain-fine-tuned cross-encoder (trained on procurement query / relevant-item pairs from historical sourcing decisions) is being developed and will replace the MS MARCO model in SCSE v3. The interface (`CrossEncoder.predict()`) is unchanged, enabling a drop-in model swap.

---

### 8.3 MMR (Maximal Marginal Relevance) for Diversity

Procurement professionals searching for similar materials or alternative suppliers need diverse results — not 20 variants of the same item. The Maximal Marginal Relevance (MMR) algorithm, originally proposed by Carbonell and Goldstein (1998), provides a principled way to balance relevance and diversity in the ranked result set.

```python
def maximal_marginal_relevance(
    candidates: list[SimilarityCandidate],
    lambda_: float = 0.7,
    k: int = 20,
) -> list[SimilarityCandidate]:
    """
    MMR score = lambda * relevance(c) - (1-lambda) * max_similarity_to_selected(c)
    lambda_=1.0 → pure relevance (no diversity)
    lambda_=0.0 → pure diversity
    """
    selected = []
    remaining = candidates.copy()
    
    while len(selected) < k and remaining:
        if not selected:
            best = max(remaining, key=lambda c: c.semantic_score)
        else:
            scores = []
            for c in remaining:
                sim_to_selected = max(
                    cosine_similarity(c.fused_vector, s.fused_vector) 
                    for s in selected
                )
                mmr_score = lambda_ * c.semantic_score - (1 - lambda_) * sim_to_selected
                scores.append((c, mmr_score))
            best = max(scores, key=lambda x: x[1])[0]
        
        selected.append(best)
        remaining.remove(best)
    
    return selected
```

**Algorithm walkthrough:**

1. The first selected item is always the highest-relevance candidate — no diversity penalty for position 1.
2. For each subsequent position, MMR computes a composite score for every remaining candidate: `lambda * relevance - (1-lambda) * max_similarity_to_already_selected`. The second term penalises candidates that are similar to anything already in the selected set.
3. `lambda_=0.7` (the SCSE default) weights relevance 2.3× more heavily than diversity. This is the empirically calibrated value from A/B testing with procurement teams — lower values (more diversity) were found to confuse users by presenting highly dissimilar items too early in the list.
4. The `lambda_` parameter is exposed through the search API as `diversity_factor` (0.5–1.0) for callers who need to tune this per-search. Strategic sourcing searches may want higher diversity (`lambda_=0.5`) to explore a wider supplier base; cost validation searches may prefer lower diversity (`lambda_=0.9`) to see the most tightly similar cost comparators.

**Performance note:** The MMR loop has O(k × N) iterations, where N is the candidate set size (200) and k is the output size (20). The inner loop computes pairwise cosine similarities. With NumPy vectorisation, this is approximately 0.5–1 ms on the 200-candidate set — negligible relative to the overall re-ranking budget.

---

### 8.4 Business Rules Ranker

The `BusinessRulesRanker` applies procurement-specific boosts and penalties to re-ranked results. This component encodes institutional procurement policy in software: preferred supplier agreements, quality grade requirements, geopolitical risk constraints, and lead-time thresholds all influence the final ranking in ways that vector similarity cannot capture.

```python
class BusinessRulesRanker:
    """Applies procurement-specific boosts and penalties."""
    
    BOOSTS = {
        "preferred_supplier": +0.15,
        "active_contract": +0.10,
        "quality_grade_A": +0.08,
        "local_supplier": +0.05,
        "validated_process": +0.05,
    }
    
    PENALTIES = {
        "supplier_on_watchlist": -0.30,
        "expired_certification": -0.20,
        "high_geopolitical_risk": -0.15,
        "low_scorecard": -0.10,     # scorecard < 55
        "long_lead_time": -0.05,    # lead_time > 90 days
    }
    
    def apply(self, results: list[RankedResult], context: SearchContext) -> list[RankedResult]:
        for result in results:
            boost = self._calculate_boost(result, context)
            result.final_score = min(result.final_score + boost, 1.0)
            result.boost_applied = boost
            result.boost_reasons = self._boost_reasons(result, context)
        return sorted(results, key=lambda r: r.final_score, reverse=True)
```

**Design principles for business rules:**

1. **Transparency:** Every boost or penalty applied is recorded in `result.boost_reasons` and surfaced in the API response (Section 10.4). Procurement professionals must be able to understand why a result ranked where it did. A black-box ranking that cannot explain itself will not be trusted.

2. **Additive, not multiplicative:** Boosts and penalties are added to (or subtracted from) the `final_score`, not multiplied. This ensures a catastrophic penalty (e.g., `supplier_on_watchlist: -0.30`) can override excellent vector similarity, while a good boost cannot exceed a ceiling of 1.0. The additive model is easier for procurement policy teams to reason about when tuning values.

3. **Score clamping:** `min(result.final_score + boost, 1.0)` prevents scores from exceeding 1.0, which would violate the confidence score interpretability contract (Section 10).

4. **Context-sensitivity:** The `context: SearchContext` parameter carries the requester's role, cost centre, preferred supplier list, and active procurement programme. Boosts are context-dependent: a supplier may be "preferred" in the context of one cost centre but "restricted" in another. The `_calculate_boost` method resolves this context-aware lookup against the `SearchContext`.

5. **Watchlist penalty magnitude:** The `-0.30` penalty for watchlisted suppliers is intentionally large enough to suppress them to the bottom of most result sets. A supplier with 0.90 vector similarity but `-0.30` penalty ranks below a clean supplier with 0.65 vector similarity. If the procurement team wishes to completely exclude watchlisted suppliers, the `FilterBuilder` (Section 9.5) should be used to exclude them at the ANN stage, which is more efficient than penalising them at re-ranking.

---

### 8.5 Cross-Entity Ranking

In advanced search scenarios — particularly strategic sourcing and cost optimisation workflows — users need results that span multiple entity types. A search for "high-strength aerospace fasteners" should surface not only matching products but also the materials they are made from, the manufacturing processes used to produce them, and the suppliers who can deliver them. The `CrossEntityRanker` assembles these heterogeneous results into a coherent, interleaved ranked list.

```python
class CrossEntityRanker:
    """When searching products, also surface related materials, processes, suppliers."""
    
    def rank_cross_entity(
        self,
        primary_results: list[RankedResult],  # products
        secondary_results: dict[str, list[RankedResult]],  # materials, processes, suppliers
        weights: dict[str, float] = {"products": 0.5, "materials": 0.25, "suppliers": 0.15, "processes": 0.10},
    ) -> CrossEntitySearchResult:
        # Normalize scores within each entity type
        # Apply entity-type weights
        # Return unified ranked list with entity_type labels
```

**Cross-entity ranking strategy:**

Scores from different entity types are not directly comparable. A material similarity score of 0.82 and a supplier similarity score of 0.82 are computed against different embedding spaces and cannot be interleaved naively. The normalisation step converts each entity type's scores to a within-type rank percentile before applying the entity-type weights.

The entity-type weights (`products: 0.5, materials: 0.25, suppliers: 0.15, processes: 0.10`) reflect the primary intent of a product search: the user primarily wants products, secondarily cares about constituent materials, then supplier availability, and finally applicable processes. These weights are configurable per search mode and per user role.

The output `CrossEntitySearchResult` carries a flat ranked list where each item is labelled with its `entity_type`, enabling the UI to render entity-type badges (e.g., "MATERIAL", "SUPPLIER") alongside each result. A separate `by_entity_type` grouping is also returned for UIs that prefer tabbed navigation over interleaved results.

---

## Section 9: Hybrid Search

Hybrid search — combining dense vector similarity with sparse keyword matching and structured attribute filters — is the default and highest-quality search mode in the SCSE. Pure semantic search (dense vectors only) excels at finding conceptually similar items but struggles with exact term matching: a query for "EN AW-6082-T6 aluminium" may not rank an exact match first if the embedding space places it near semantically similar alloys. Sparse BM25 search handles exact term matching precisely but cannot generalise across paraphrases or synonyms. Hybrid search combines both signals to deliver the strengths of both approaches.

---

### 9.1 Hybrid Search Architecture

The SCSE hybrid search architecture flows through three parallel signal paths that are fused before re-ranking:

```
Query → [Text Analysis]  → Sparse BM25 vector (TF-IDF / SPLADE)
      → [Embedding]      → Dense vector (fused 1024d)
      → [Structured]     → Numeric filters (price range, lead time, country)

Qdrant Hybrid:
  Dense ANN search  (fused vector, top-200)
  Sparse BM25 search (keyword match, top-200)
  RRF Fusion        → top-50 candidates

Re-ranker → Business Rules → Final top-K
```

**Path descriptions:**

1. **Sparse BM25 path (Text Analysis):** The raw query text is preprocessed by the `QueryExpander` (Section 9.3), then encoded into a sparse TF-IDF vector by the `SparseVectorEncoder` (Section 9.2). This sparse vector is submitted to Qdrant's sparse vector index, which returns the top-200 results by BM25 score. This path excels at exact part number matches, material grade codes (e.g., "S355J2"), and domain-specific acronyms.

2. **Dense ANN path (Embedding):** The query is embedded via the SCSE dual-encoder (text encoder + structured encoder + fusion projector) into a 1024-dimensional fused vector and submitted to Qdrant's HNSW dense index. This path returns the top-200 most semantically similar items. It excels at paraphrase queries, synonym handling, and multilingual queries.

3. **Structured filter path:** Numeric and categorical query parameters (price range, lead time constraints, country-of-origin, certifications) are translated into Qdrant `Filter` objects by the `FilterBuilder` (Section 9.5) and applied as pre-filters on both ANN paths. Pre-filtering in Qdrant is more efficient than post-filtering because it reduces the candidate set before ANN graph traversal.

4. **RRF Fusion:** The two ANN result lists (dense top-200 and sparse top-200) are fused using Reciprocal Rank Fusion (Section 9.4) to produce a unified top-50 candidate list. This fused list balances items that rank highly on either or both signals.

5. **Re-ranking and business rules:** The top-50 fused candidates proceed through the Stage 2 pipeline described in Section 8.

---

### 9.2 Sparse Vector Generation (BM25)

The `SparseVectorEncoder` generates sparse TF-IDF vectors for both the indexed corpus and incoming queries. These vectors are stored as Qdrant sparse vectors alongside the dense vectors in each collection, enabling Qdrant's native hybrid search API.

```python
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

class SparseVectorEncoder:
    def __init__(self):
        self.tfidf = TfidfVectorizer(
            max_features=30000,
            ngram_range=(1, 2),
            sublinear_tf=True,
            analyzer='word',
            token_pattern=r'\b[a-zA-Z0-9][a-zA-Z0-9_\-\.]+\b',
        )
        self.vocab_size = 30000
    
    def fit(self, corpus: list[str]) -> None:
        self.tfidf.fit(corpus)
    
    def encode(self, text: str) -> SparseVector:
        vec = self.tfidf.transform([text])
        indices = vec.indices.tolist()
        values = vec.data.tolist()
        return SparseVector(indices=indices, values=values)
    
    def encode_query(self, query: str) -> SparseVector:
        # Query expansion: add synonyms, related terms
        expanded = self.query_expander.expand(query)
        return self.encode(expanded)
```

**Parameter rationale:**

- **`max_features=30000`:** The vocabulary is capped at the 30,000 most frequent terms across the procurement corpus. This covers essentially all meaningful procurement terminology (material grades, part number prefixes, process terms, supplier capability descriptors) while keeping the sparse vector representation compact.

- **`ngram_range=(1, 2)`:** Unigrams plus bigrams. Bigrams capture compound terms critical in procurement: "carbon steel", "die casting", "on-time delivery", "lead time", "unit price". Without bigrams, these compound terms decompose into generic unigrams that match too broadly.

- **`sublinear_tf=True`:** Applies `log(1 + tf)` instead of raw term frequency. This prevents documents with hundreds of repetitions of a term from scoring disproportionately higher than documents with a few precise, meaningful occurrences.

- **Token pattern `\b[a-zA-Z0-9][a-zA-Z0-9_\-\.]+\b`:** Matches alphanumeric tokens with underscores, hyphens, and dots — essential for preserving material grades like `EN-AW-6082`, part numbers like `FAS-M8-25-A2`, and process codes like `ISO_9001.2015`. The standard TF-IDF token pattern `r"(?u)\b\w\w+\b"` strips hyphens and dots, breaking these domain-critical identifiers.

- **`SparseVector(indices, values)`:** The output is a Qdrant-compatible sparse vector object. Qdrant stores sparse vectors in a compressed inverted index similar to traditional text search engines, enabling O(|q|) lookup per query (proportional to the number of non-zero query terms) rather than O(N) sequential scan.

**Vocabulary drift management:** The TF-IDF vocabulary is fitted on the corpus at index build time and persisted alongside the FAISS indexes in S3. When new materials, products, or processes introduce novel terminology not in the original vocabulary (e.g., a new material grade specification), those terms receive no vocabulary index and are not represented in the sparse vector. The vocabulary is re-fitted quarterly during the scheduled corpus maintenance window (`scse_vocab_refit` Airflow DAG). Between re-fits, novel terms are handled by the dense vector path alone — a graceful degradation.

---

### 9.3 Query Expansion

Domain-specific query expansion is one of the most impactful features in the SCSE hybrid search pipeline. Procurement professionals use heterogeneous terminology: an engineer might query "aluminium" while a procurement specialist searches "AL-alloy" and a supplier indexes their product as "aluminium 6082". Without query expansion, these queries would fail to match despite describing the same material family.

```python
class QueryExpander:
    """Expand user queries with domain-specific synonyms."""
    
    SYNONYMS = {
        "steel": ["carbon steel", "stainless", "alloyed steel", "ferrous"],
        "aluminum": ["aluminium", "AL", "Al-alloy"],
        "machining": ["CNC", "milling", "turning", "boring", "drilling"],
        "casting": ["die casting", "sand casting", "investment casting"],
        # ... 500+ procurement domain synonyms
    }
    
    def expand(self, query: str, max_expansions: int = 3) -> str:
        tokens = query.lower().split()
        expanded = [query]
        for token in tokens:
            if token in self.SYNONYMS:
                expanded.extend(self.SYNONYMS[token][:max_expansions])
        return " ".join(expanded)
```

**Synonym dictionary management:**

The `SYNONYMS` dictionary is maintained in `config/query_expansion_synonyms.yaml` and loaded at service startup. It is not hardcoded in the class — the code above shows the structure but the actual synonyms are configuration-managed. The synonym file is version-controlled and changes require a PR review from a procurement domain expert.

The synonym dictionary is curated from three sources:
1. **Manual curation by procurement domain experts** — the primary source for high-confidence, high-impact synonyms (material grade families, process equivalences)
2. **Automatic extraction from corpus co-occurrence** — terms that frequently co-occur in the procurement corpus within a 5-token window are candidates for synonym relationships, reviewed by domain experts before inclusion
3. **Industry standard mappings** — ISO material grade equivalences, DIN/ASTM/EN material standard cross-references

The `max_expansions=3` limit prevents query expansion from generating excessively long queries that dilute the sparse vector signal. If a token has 10 synonyms, only the 3 highest-priority ones (listed first in the SYNONYMS value list) are added.

**Expansion scope:** Query expansion is applied to sparse (BM25) encoding only. Dense embedding already generalises across synonyms implicitly through the embedding model's training. Applying expansion to dense encoding as well would introduce noise.

---

### 9.4 Reciprocal Rank Fusion (RRF)

Reciprocal Rank Fusion (RRF) is the fusion algorithm that combines the dense ANN result list and the sparse BM25 result list into a single ranked list. RRF was proposed by Cormack, Clarke, and Buettcher (2009) and has become the de-facto standard for rank fusion in hybrid retrieval systems due to its simplicity, robustness to score scale differences, and strong empirical performance.

```python
def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """
    RRF score = sum_i weight_i / (k + rank_i(doc))
    Default k=60 from original RRF paper (Cormack et al. 2009)
    """
    if weights is None:
        weights = [1.0 / len(ranked_lists)] * len(ranked_lists)
    
    scores: dict[str, float] = defaultdict(float)
    
    for ranked_list, weight in zip(ranked_lists, weights):
        for rank, (doc_id, _score) in enumerate(ranked_list, start=1):
            scores[doc_id] += weight / (k + rank)
    
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

**Why RRF over score-level fusion:**

The key insight of RRF is that it uses only the *rank* of each document in each list, ignoring the raw scores. This is crucial because dense vector cosine similarities (range: 0.70–1.0 after threshold filtering) and BM25 scores (range: 0–25, unbounded) are on fundamentally different scales. Attempting to combine them via simple weighted average of raw scores (linear score fusion) requires careful normalisation that is brittle and dataset-dependent.

RRF's rank-based combination is naturally scale-invariant: an item ranked #1 in either list gets maximum credit regardless of whether its raw BM25 score was 2.1 or 18.7. The `k=60` parameter dampens the contribution of high-rank differences: the difference between rank 1 and rank 2 contributes `1/61 - 1/62 ≈ 0.00026` to the score, while the difference between rank 10 and rank 11 contributes `1/70 - 1/71 ≈ 0.0002`. This smoothing prevents the top-ranked item in one list from dominating the fusion when the other list disagrees.

**Weighted RRF for asymmetric signal strength:**

The `weights` parameter allows the fusion to favour one signal over another. In the SCSE default configuration:
- HYBRID mode: `weights=[0.6, 0.4]` (dense 60%, sparse 40%) — dense vectors carry more signal for general procurement queries
- KEYWORD mode: `weights=[0.2, 0.8]` (dense 20%, sparse 80%) — for exact part number lookups, sparse dominates
- FEATURE mode: `weights=[1.0, 0.0]` — structured vector only, no sparse signal

These weights are exposed as a configurable parameter in the SCSE search API under `fusion_weights`.

---

### 9.5 Filter Strategy

The `FilterBuilder` translates structured search parameters into Qdrant `Filter` objects that are applied as pre-filters during ANN search. Pre-filtering is dramatically more efficient than post-filtering: by reducing the candidate set before ANN traversal, Qdrant can traverse fewer graph nodes, reduce memory bandwidth, and return results faster.

The tradeoff is that aggressive pre-filtering can degrade ANN accuracy (known as the "low-recall under heavy filter" problem in ANN literature). Qdrant mitigates this with its "filtered HNSW" implementation, which falls back to sequential scan when the filtered candidate set is too small for reliable graph traversal. SCSE monitors the `filtered_scan_ratio` metric from Qdrant's telemetry and alerts when it exceeds 5%, indicating over-filtering.

```python
class FilterBuilder:
    """Build Qdrant filter objects from search parameters."""
    
    def build_product_filter(self, params: ProductSearchParams) -> Filter | None:
        conditions = []
        if params.categories:
            conditions.append(FieldCondition(key="category", match=MatchAny(any=params.categories)))
        if params.price_min or params.price_max:
            conditions.append(FieldCondition(
                key="price_eur",
                range=Range(gte=params.price_min, lte=params.price_max)
            ))
        if params.is_active is not None:
            conditions.append(FieldCondition(key="is_active", match=MatchValue(value=params.is_active)))
        # ... more filters
        return Filter(must=conditions) if conditions else None
    
    def build_supplier_filter(self, params: SupplierSearchParams) -> Filter | None:
        conditions = []
        if params.min_scorecard:
            conditions.append(FieldCondition(key="quality_score", range=Range(gte=params.min_scorecard)))
        if params.countries:
            conditions.append(FieldCondition(key="country_iso", match=MatchAny(any=params.countries)))
        if params.certifications:
            conditions.append(FieldCondition(key="certifications", match=MatchAny(any=params.certifications)))
        return Filter(must=conditions) if conditions else None
```

**Filter design conventions:**

1. **`must` vs `should` vs `must_not`:** All SCSE filters use `must` (AND semantics) by default. `should` (OR semantics) is used only for the multi-value `MatchAny` conditions within a single field (e.g., `category IN ('FASTENERS', 'BRACKETS')`). `must_not` is used exclusively for the watchlist exclusion filter, which the `FilterBuilder` applies automatically when the requester's `SearchContext` indicates an active watchlist exclusion policy.

2. **Null-safe filter building:** When a parameter is `None`, the corresponding filter condition is omitted. This ensures that unspecified parameters do not inadvertently filter out valid results. The `return Filter(must=conditions) if conditions else None` pattern returns `None` (no filter) when no parameters are specified, which Qdrant interprets as "match all".

3. **Payload field naming:** All Qdrant payload field names used in filter conditions are defined as constants in `scse.qdrant.payload_keys` to prevent typo-induced silent filtering failures. A misspelled payload key name in a filter condition causes Qdrant to skip the condition silently (it treats an unknown field as absent from all documents), which produces incorrect over-inclusive results rather than an error.

---

### 9.6 Search Modes

The SCSE exposes six search modes through the search API, each configuring a different combination of dense, sparse, and filter signal paths. The appropriate mode is selected by the API caller (or auto-detected from the query structure by the `SearchModeDetector` heuristic).

| Mode | Dense Signal | Sparse Signal | Filters | Primary Use Case |
|---|---|---|---|---|
| SEMANTIC | Yes (text vector) | No | Optional | Natural language description queries — "find stainless steel parts for automotive exterior trim" |
| FEATURE | Yes (fused vector) | No | No | Technical specification similarity — find items with the same dimensional and material profile as a reference item |
| KEYWORD | No | Yes | Optional | Exact term matching — part numbers, material grade codes, supplier names |
| HYBRID | Yes (fused vector) | Yes | Optional | Best overall quality — default mode for all procurement UI searches |
| FILTERED | Yes (fused vector) | No | Required | Constrained search within a predefined scope — e.g., "similar items from German suppliers only" |
| RECOMMEND | Collaborative (user history) | No | Optional | "More like these approved items" — uses historical approval signals to bias the dense search |

**Mode selection guidance:**

- **HYBRID** is the recommended default for all interactive procurement searches. It consistently outperforms pure SEMANTIC or pure KEYWORD modes in A/B testing on SCSE's internal evaluation benchmarks (MRR@10 improvement: +12% over SEMANTIC, +8% over KEYWORD).

- **SEMANTIC** is preferred for exploratory or discovery searches where the user is unfamiliar with exact terminology. It is also more robust to multilingual queries (English/German/French queries against a mixed-language corpus).

- **FEATURE** is used by the cost validation engine, which compares a new quote's specifications against historical quotes and materials. In this context, the textual description is irrelevant; only the structured technical parameters matter.

- **KEYWORD** is primarily accessed programmatically by integration partners (ERP systems, procurement platforms) that need to match specific part numbers or material codes with high precision.

- **FILTERED** is used when the search scope is contractually constrained (e.g., a procurement framework agreement restricts sourcing to a specific approved supplier list). The required filter ensures the constraint is enforced at the vector search level, not as a post-processing step.

- **RECOMMEND** uses implicit feedback signals (which similar items were approved, shortlisted, or quoted in the past) to personalise the dense search. The collaborative signal is encoded as a weighted blend of historical item embeddings representing the user's or cost-centre's approval history. This mode is under active development and currently available in beta to selected pilot users.

---

## Section 10: Confidence Score

The confidence score is the SCSE's mechanism for communicating result quality to downstream consumers in a structured, explainable way. Rather than exposing raw cosine similarities — which are difficult for non-technical procurement professionals to interpret — the SCSE computes a multi-component confidence score that incorporates semantic similarity, technical feature alignment, data completeness, data freshness, and business context alignment.

The confidence score is a first-class citizen in the SCSE API response: it is displayed in the procurement UI as a colour-coded badge (HIGH = green, MEDIUM = amber, LOW = red, UNCERTAIN = grey), surfaced in the cost validation dashboard as a quality indicator, and used by the recommendation engine as a feature for downstream ML models.

---

### 10.1 Confidence Score Model

The `ConfidenceScore` dataclass captures both the aggregate confidence and the component-level breakdown. The `ConfidenceGrade` enum provides a human-readable summary tier that maps naturally to procurement decision workflows: HIGH-confidence results can be used directly for benchmarking; MEDIUM results should be reviewed; LOW results require manual validation; UNCERTAIN results should not be used for cost decisions without additional research.

```python
@dataclass
class ConfidenceScore:
    overall: float          # 0.0-1.0 final confidence
    grade: ConfidenceGrade  # HIGH / MEDIUM / LOW / UNCERTAIN
    
    # Component scores
    vector_similarity: float    # raw cosine similarity (0.70-1.0)
    feature_alignment: float    # structured feature overlap (0.0-1.0)
    data_completeness: float    # how complete the entity's data is (0.0-1.0)
    temporal_relevance: float   # how recent the match is (0.0-1.0)
    business_relevance: float   # business rules alignment (0.0-1.0)
    
    # Explanations
    reasons: list[str]          # human-readable explanation bullets
    warnings: list[str]         # caveats (e.g., "price data is 90 days old")

class ConfidenceGrade(Enum):
    HIGH      = "HIGH"       # overall >= 0.85
    MEDIUM    = "MEDIUM"     # 0.70 <= overall < 0.85
    LOW       = "LOW"        # 0.55 <= overall < 0.70
    UNCERTAIN = "UNCERTAIN"  # overall < 0.55
```

**Grade boundary rationale:**

The grade boundaries were established through a combination of statistical calibration (Section 10.3) and procurement domain validation:

- **HIGH (≥ 0.85):** Results at this grade level have been validated to be suitable for direct use as cost benchmarks by procurement analysts, with a false positive rate < 5% in historical evaluation.
- **MEDIUM (0.70–0.85):** Results require a quick human sanity check but are generally useful. Approximately 70% of MEDIUM results in user studies were rated "relevant" by procurement experts.
- **LOW (0.55–0.70):** Potentially relevant but should be treated as a starting point for further investigation. Useful for market intelligence but not for binding cost decisions.
- **UNCERTAIN (< 0.55):** Results that fell below the minimum ANN similarity threshold (0.70 cosine similarity) but were returned due to a very sparse candidate set. These are rare and indicate that the system did not find meaningful matches for the query.

The `reasons` list provides positive evidence for the match (e.g., "Very high semantic similarity", "Strong feature alignment"). The `warnings` list surfaces data quality concerns that do not disqualify the match but should inform the user's judgment (e.g., "Price data is 47 days old", "Supplier scorecard below recommended threshold"). This bidirectional explanation is important for maintaining user trust: showing only positive reasons would create false confidence, while showing only warnings would unnecessarily discourage use of high-quality matches.

---

### 10.2 Confidence Calculator

The `ConfidenceCalculator` computes all five component scores and combines them via a weighted linear combination. The weights are configurable per entity type — the importance of data completeness differs between Suppliers (where missing certifications are critical) and Processes (where temporal relevance matters more).

```python
class ConfidenceCalculator:
    WEIGHTS = {
        "vector_similarity": 0.40,
        "feature_alignment": 0.25,
        "data_completeness": 0.15,
        "temporal_relevance": 0.10,
        "business_relevance": 0.10,
    }
    
    def calculate(self, query: SimilarityQuery, match: SimilarityCandidate) -> ConfidenceScore:
        vs = self._vector_similarity_score(match.cosine_similarity)
        fa = self._feature_alignment_score(query.feature_vector, match.feature_vector)
        dc = self._data_completeness_score(match.payload)
        tr = self._temporal_relevance_score(match.payload.get("updated_at"))
        br = self._business_relevance_score(match.payload, query.context)
        
        overall = (
            vs * self.WEIGHTS["vector_similarity"] +
            fa * self.WEIGHTS["feature_alignment"] +
            dc * self.WEIGHTS["data_completeness"] +
            tr * self.WEIGHTS["temporal_relevance"] +
            br * self.WEIGHTS["business_relevance"]
        )
        
        return ConfidenceScore(
            overall=round(overall, 4),
            grade=self._grade(overall),
            vector_similarity=vs,
            feature_alignment=fa,
            data_completeness=dc,
            temporal_relevance=tr,
            business_relevance=br,
            reasons=self._generate_reasons(vs, fa, dc, tr, br),
            warnings=self._generate_warnings(match),
        )
    
    def _vector_similarity_score(self, cosine: float) -> float:
        """Map cosine similarity [0.70, 1.0] to score [0.0, 1.0]."""
        return max(0.0, (cosine - 0.70) / 0.30)
    
    def _temporal_relevance_score(self, updated_at: str | None) -> float:
        """Decay function: score=1.0 if updated today, 0.5 at 180 days, 0.0 at 365 days."""
        if not updated_at:
            return 0.2
        age_days = (datetime.utcnow() - datetime.fromisoformat(updated_at)).days
        return max(0.0, 1.0 - (age_days / 365))
    
    def _data_completeness_score(self, payload: dict) -> float:
        """Score based on non-null required fields ratio."""
        required_fields = ["name", "category", "price_eur", "updated_at", "status"]
        filled = sum(1 for f in required_fields if payload.get(f) is not None)
        return filled / len(required_fields)
    
    def _generate_reasons(self, vs, fa, dc, tr, br) -> list[str]:
        reasons = []
        if vs >= 0.85: reasons.append("Very high semantic similarity")
        if fa >= 0.80: reasons.append("Strong feature alignment on technical specs")
        if dc >= 0.90: reasons.append("Complete and reliable entity data")
        if tr >= 0.85: reasons.append("Recently updated data")
        if br >= 0.80: reasons.append("Strong business context match")
        return reasons
    
    def _generate_warnings(self, match: SimilarityCandidate) -> list[str]:
        warnings = []
        age_days = ...
        if age_days > 90: warnings.append(f"Price data is {age_days} days old")
        if match.payload.get("scorecard_total", 100) < 70:
            warnings.append("Supplier scorecard below recommended threshold")
        return warnings
```

**Component score design notes:**

**`_vector_similarity_score`:** The linear mapping `(cosine - 0.70) / 0.30` converts cosine similarity from the `[0.70, 1.0]` range (the SCSE minimum similarity threshold at 0.70) to `[0.0, 1.0]`. A cosine similarity of exactly 0.70 scores 0.0, and exactly 1.0 scores 1.0. This rescaling ensures that the confidence score's vector_similarity component uses the full `[0.0, 1.0]` range rather than clustering artificially near 1.0. Items below 0.70 cosine similarity are filtered out before reaching the confidence calculator and would score 0.0 by the `max(0.0, ...)` guard.

**`_temporal_relevance_score`:** The linear decay function treats data age linearly — a 180-day-old record scores 0.51, a 365-day-old record scores 0.0. The `return 0.2` fallback for missing `updated_at` signals that stale/untracked data contributes modestly positive temporal relevance rather than zero, since the absence of an `updated_at` timestamp may indicate a legacy record rather than definitively outdated data. A Sigmoid or exponential decay function was considered but the linear decay is more intuitive for domain users reasoning about data freshness.

**`_data_completeness_score`:** The five required fields (`name`, `category`, `price_eur`, `updated_at`, `status`) represent the minimum viable information set for a procurement decision. An entity missing `price_eur` is significantly less useful for cost benchmarking; missing `status` makes it impossible to determine if the item is still available. Additional optional fields (dimensions, material grade, lead time) are tracked separately in the `warnings` generator: missing optional fields generate informational warnings but do not reduce the completeness score.

---

### 10.3 Confidence Calibration

A confidence score is meaningful only if it is *calibrated* — a result with `overall = 0.85` should be genuinely relevant approximately 85% of the time when validated by a domain expert. Without calibration, the raw weighted combination may be systematically over- or under-confident.

**Calibration methodology:**

1. **Labelled dataset collection:** The SCSE maintains a growing labelled dataset (`scse.confidence_labels`) populated from two sources:
   - Procurement analyst feedback: analysts can rate search results as "Relevant", "Partially Relevant", or "Irrelevant" via a thumbs-up/thumbs-down interface in the procurement UI
   - Expert review sessions: quarterly structured sessions where senior procurement engineers rate a stratified random sample of SCSE search results against each entity type

2. **Calibration measurement:** For each confidence grade threshold, compute the empirical precision (fraction of results rated "Relevant" by the labelling process). Plot the calibration curve: predicted confidence on the x-axis, empirical precision on the y-axis. A perfectly calibrated model follows the diagonal.

3. **Calibration correction:** Two calibration methods are supported:
   - **Platt scaling:** Fits a logistic regression `σ(a*f(x) + b)` where `f(x)` is the raw confidence score. Simple, interpretable, and robust to small label datasets.
   - **Isotonic regression:** Fits a non-decreasing step function to the calibration curve. More flexible than Platt scaling but requires a larger labelled dataset (> 1,000 samples per entity type) to avoid overfitting.

4. **Recalibration schedule:** A monthly Airflow DAG (`scse_confidence_recalibrate`) runs the calibration pipeline, updates the calibration parameters stored in `scse.calibration_params`, and publishes a calibration quality report to the SCSE Confluence space. If the Expected Calibration Error (ECE) exceeds 0.05, a P2 ticket is created for the SCSE team to investigate the confidence model.

5. **Grafana calibration dashboard:** The SCSE Grafana workspace includes a `Confidence Calibration` dashboard (UID: `scse-calibration-001`) that renders the calibration curve for each entity type and tracks ECE over time. Access: Grafana → Dashboards → SCSE → Confidence Calibration.

**Integration with online serving:** The calibration parameters are loaded at service startup as part of the `ConfidenceCalculator` initialisation. When calibration parameters are absent or stale (> 60 days old), the calculator falls back to uncalibrated scores and appends a `"Confidence scores may be uncalibrated — last calibration: {date}"` entry to the warnings list.

---

### 10.4 Explanation API Response

The confidence score and its component breakdown are serialised as part of every search result in the SCSE REST API response. The following JSON structure is the canonical format returned by the `GET /v1/similarity/search` and `GET /v1/similarity/{entity_type}/{entity_id}/similar` endpoints.

```json
{
  "match_id": "uuid",
  "rank": 1,
  "entity_type": "MATERIAL",
  "confidence": {
    "overall": 0.883,
    "grade": "HIGH",
    "breakdown": {
      "vector_similarity": 0.94,
      "feature_alignment": 0.87,
      "data_completeness": 0.95,
      "temporal_relevance": 0.82,
      "business_relevance": 0.78
    },
    "reasons": [
      "Very high semantic similarity",
      "Strong feature alignment on technical specs",
      "Complete and reliable entity data"
    ],
    "warnings": [
      "Price data is 47 days old"
    ]
  }
}
```

**API contract guarantees:**

- `overall` is always in `[0.0, 1.0]`, rounded to 4 decimal places
- `grade` is always one of `"HIGH"`, `"MEDIUM"`, `"LOW"`, `"UNCERTAIN"` — no other values will be returned
- All `breakdown` fields are present in every response (no optional fields in the breakdown object). A missing component score defaults to 0.0 rather than being omitted
- `reasons` may be an empty list (if no threshold for any positive signal is met), but the field is always present
- `warnings` may be an empty list, but the field is always present
- The `confidence` object is always present in search results. It is never `null`. If confidence computation fails due to a calculator error, a fallback `ConfidenceScore` with `overall=0.0`, `grade="UNCERTAIN"`, and `warnings=["Confidence computation unavailable"]` is returned rather than propagating an error

**Consuming the confidence score:**

Downstream services should use the `grade` field rather than `overall` for human-facing decisions. The `overall` numeric score is appropriate for programmatic use cases (ML model features, sorting, filtering) where fine-grained differentiation within a grade band matters. The `breakdown` object is intended for audit, debugging, and transparency dashboards rather than for direct user consumption.

The `reasons` and `warnings` lists are written in natural language suitable for display directly in the procurement UI. They are intentionally concise (< 60 characters per item) to fit in tooltip-sized UI components. Internationalisation (i18n) of these strings is managed via the SCSE i18n service, which maps the canonical English reason/warning keys to localised strings in the response language determined by the `Accept-Language` header.

---

## Appendix: Cross-Reference Index

The following table maps the key concepts in Sections 6–10 to their related architecture decision records, operational runbooks, and related documentation.

| Concept | Section | Related ADR | Runbook | Related Doc |
|---|---|---|---|---|
| FAISS index type selection | 6.2 | ADR-002 | OPS-SCSE-003 (FAISS index rebuild) | SCSE-DOC-001 §4 |
| FAISS index persistence | 6.6 | ADR-002 | OPS-SCSE-003 | OPS-SCSE-004 (S3 index management) |
| pgvector schema | 7.2 | ADR-003 | OPS-SCSE-005 (pgvector migrations) | DB-SCSE-001 |
| HNSW index tuning | 7.3 | ADR-003 | OPS-SCSE-006 (vector index maintenance) | PERF-SCSE-001 |
| Two-stage retrieval | 8.1 | ADR-004 | — | SCSE-DOC-001 §5 |
| MMR diversity | 8.3 | — | — | EVAL-SCSE-002 (diversity evaluation) |
| Business rules boosts | 8.4 | ADR-005 | OPS-SCSE-007 (business rules config) | PROC-SCSE-001 |
| Hybrid search RRF | 9.4 | ADR-006 | — | EVAL-SCSE-001 (hybrid eval results) |
| Confidence calibration | 10.3 | — | OPS-SCSE-008 (monthly recalibration) | EVAL-SCSE-003 (calibration report) |
| Explanation API | 10.4 | ADR-007 | — | API-SCSE-001 (REST API reference) |

---

*End of SCSE-DOC-002. For questions or corrections, contact the SCSE platform team via the `#scse-platform` Slack channel or open a ticket in the SCSE Jira project.*
