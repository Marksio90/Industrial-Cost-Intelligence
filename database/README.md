# ICI Database Schema

PostgreSQL 15+ · pgvector 0.7+ · 7 migration files

## Execution order

```
psql -U ici_migrator -d ici -f migrations/001_extensions_and_schemas.sql
psql -U ici_migrator -d ici -f migrations/002_core_schema.sql
psql -U ici_migrator -d ici -f migrations/003_vector_tables.sql
psql -U ici_migrator -d ici -f migrations/004_indexes.sql
psql -U ici_migrator -d ici -f migrations/005_audit_log.sql
psql -U ici_migrator -d ici -f policies/006_retention_policies.sql
psql -U ici_migrator -d ici -f seeds/007_seed_data.sql   # dev/staging only
```

## Schema map

| Schema | Purpose |
|--------|---------|
| `ici` | Core business tables (partitioned) |
| `ici_vectors` | pgvector embedding tables |
| `ici_audit` | Immutable append-only audit log |
| `ici_archive` | Cold-storage detached partitions |

## Partition strategy

| Table | Method | Granularity | Rationale |
|-------|--------|-------------|-----------|
| `materials`, `suppliers`, `processes` | HASH(tenant_id) | 8 partitions | Multi-tenant write distribution |
| `rfqs` | RANGE(created_at) | Monthly | Time-range query pattern, easy archival |
| `quotes` | RANGE(created_at) | Annual | Lower write volume than RFQs |
| `cost_snapshots` | RANGE(snapshot_date) | Quarterly | Regulatory retention, bulk analytics |
| `forecasts` | RANGE(created_at) | Annual | ML artifact lifecycle |
| `risk_scores` | RANGE(created_at) | Annual | Risk register retention |
| `ici_audit.events` | RANGE(occurred_at) | Monthly | Fast pruning, compliance archival |

## Vector collections

| Table | Dimensions | Model | Index | Distance |
|-------|-----------|-------|-------|----------|
| `material_embeddings` | 1024 | multilingual-e5-large | HNSW m=16 | cosine |
| `cad_embeddings` | 512 | ResNet-50 | IVFFlat lists=100 | L2 |
| `supplier_embeddings` | 768 | all-mpnet-base-v2 | HNSW m=16 | cosine |
| `cost_context_embeddings` | 1024 | multilingual-e5-large | HNSW m=32 | cosine |
| `rfq_embeddings` | 1024 | multilingual-e5-large | HNSW m=16 | cosine |

## Retention (summary)

| Table | HOT | Archive | Drop |
|-------|-----|---------|------|
| rfqs / quotes | 24 mo | 36 mo | 60 mo |
| cost_snapshots | 36 mo | never | never |
| forecasts | 12 mo | — | 24 mo |
| risk_scores | 36 mo | 84 mo | — |
| audit events | 12 mo | 84 mo | 84 mo |

Run monthly: `CALL ici.run_retention_maintenance();`
