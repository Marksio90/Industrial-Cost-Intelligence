# Similarity Cost Search Engine

Semantyczny silnik wyszukiwania podobieństw dla platformy Industrial Cost Intelligence.
Znajduje podobne produkty, wyceny, materiały, procesy i dostawców używając wektorów
embedding, hybrydowego wyszukiwania (dense + sparse + RRF) i re-rankingu z regułami biznesowymi.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-strategy-features-embeddings-schema-qdrant.md](./01-strategy-features-embeddings-schema-qdrant.md) | Strategia podobieństwa (6 progów, 5 typów encji), inżynieria cech (200 cech dla 5 typów), architektura embeddingów (text-embedding-3-large 3072d + MLP 512d + FusionAttentionLayer 1024d), VectorRecord schema, konfiguracja Qdrant (multi-vector, sparse, HNSW m=32, INT8 quantization, 3-node cluster) |
| [02-faiss-pgvector-ranking-hybrid-confidence.md](./02-faiss-pgvector-ranking-hybrid-confidence.md) | FAISS (IVFFlat/IVFPQ/HNSWFlat, BatchSimilarityJob), pgvector (transactional fallback, HNSW indexes, SQL similarity queries), algorytmy rankingowe (MMR diversity, BusinessRulesRanker, CrossEntityRanker), hybrid search (dense + BM25 + RRF, QueryExpander 500+ synonimów), Confidence Score (5-komponentowy: vector_similarity + feature_alignment + data_completeness + temporal_relevance + business_relevance) |
| [03-sql-schema-api-events-monitoring.md](./03-sql-schema-api-events-monitoring.md) | Schemat PostgreSQL 16 (6 ENUMów, 9 tabel, 4 funkcje składowane, triggery, widoki), OpenAPI 3.1 (22 endpointy, 7 tagów), 8 tematów Kafka, 5 schematów Avro, SCEOutboxPublisher, mapa konsumentów, 20 metryk Prometheus, 8 reguł Alertmanager, 6 dashboardów Grafana, tabela SLA |
| [04-security-testing-scalability-risks-roadmap.md](./04-security-testing-scalability-risks-roadmap.md) | 7 ról RBAC, JWT middleware, SearchResultMasker, 10 kontroli bezpieczeństwa; macierz 8 testów, pytest unit/integration (Testcontainers: PG16+Qdrant), Pact contract, k6 load (P95<300ms przy 500 RPS), RecallEvaluationHarness; 4 poziomy skalowalności, SCSEServiceRouter, EmbeddingWorker, HPA Kubernetes; 14 ryzyk; roadmap 32 sprinty (4 fazy) |

## Architektura wyszukiwania

```
Query Input
    │
    ├─► Dense Vector (fused 1024d)   → Qdrant HNSW ANN → top-200 candidates
    ├─► Sparse BM25 (TF-IDF 30k)    → Qdrant sparse   → top-200 candidates
    └─► Filters (price, country...) → payload index   → pre-filter
             │
             ▼ RRF Fusion (Qdrant native)
             │
             ▼ Re-ranking: semantic×0.40 + structured×0.30 + business×0.20 + MMR×0.10
             │
             ▼ Confidence Score (HIGH/MEDIUM/LOW/UNCERTAIN) + reasons[] + warnings[]
             │
             ▼ Final top-K results
```

## Typy encji i progi podobieństwa

| Typ encji | Definicja podobieństwa | Wektory |
|-----------|----------------------|---------|
| PRODUCT | Specyfikacja techniczna + ekwiwalentność funkcjonalna + profil kosztowy | text 3072d + struct 512d → fused 1024d |
| QUOTE | Poziom cenowy + warunki handlowe + tier dostawcy + zakres | text 3072d + struct 512d → fused 1024d |
| MATERIAL | Właściwości fizyczne/chemiczne + normy + grupa surowcowa | text 3072d + struct 512d → fused 1024d |
| PROCESS | Sekwencja operacji + typ maszyny + tolerancje + czas cyklu | text 3072d + struct 512d → fused 1024d |
| SUPPLIER | Zdolności + geografia + certyfikaty + profil scorecard | text 3072d + struct 512d → fused 1024d |

| Etykieta | Zakres | Zastosowanie |
|----------|--------|-------------|
| EXACT | > 0.97 | Wykrywanie duplikatów |
| NEAR_DUPLICATE | 0.93–0.97 | Alert duplikatu, wersje |
| HIGHLY_SIMILAR | 0.85–0.92 | Alternatywne zaopatrzenie |
| SIMILAR | 0.70–0.84 | Benchmarking cen |
| RELATED | 0.50–0.69 | Eksploracja rynku |
| UNRELATED | < 0.50 | Filtrowane z wyników |

## Architektura embeddingów

| Warstwa | Model/Klasa | Wymiar | Cel |
|---------|------------|--------|-----|
| Text Encoder | OpenAI text-embedding-3-large | 3072d | Semantyczne znaczenie tekstu |
| Structured Encoder | StructuredFeatureEncoder (MLP PyTorch) | 512d | Podobieństwo cech numerycznych |
| Fusion Layer | FusionAttentionLayer (learned α) | 1024d | Połączenie text + structured |
| Sparse Vector | TF-IDF BM25 (30k vocab) | sparse | Dopasowanie słów kluczowych |

## Stack techniczny

- **Vector DB (primary):** Qdrant 3-node cluster (replication_factor=2, 4 shards, INT8 quantization)
- **Vector DB (fallback):** pgvector HNSW (PostgreSQL 16, transactional search)
- **Batch ANN:** FAISS (IVFFlat / IVF-PQ / GPU, nightly Airflow DAG)
- **Cache:** Redis 7+ (7 wzorców: similar 4h TTL, search 15min, recommend 1h)
- **ML:** PyTorch (MLP encoder + FusionAttention), scikit-learn (calibration), MLflow (model registry)
- **Text Embeddings:** OpenAI text-embedding-3-large (3072d), fallback: text-embedding-3-small (1536d)
- **Messaging:** Apache Kafka 3+ (8 tematów, Avro + Schema Registry)
- **Monitoring:** Prometheus (20 metryk) + Grafana (6 dashboardów) + Alertmanager (8 reguł)

## Role RBAC

| Rola | Dostęp |
|------|--------|
| SCSE_VIEWER | Wyszukiwanie (bez floor_price), rekomendacje, cache |
| SCSE_ANALYST | Pełne wyszukiwanie + analityka |
| SCSE_PROCUREMENT | SCSE_ANALYST + feedback kliknięć |
| SCSE_DATA_STEWARD | SCSE_ANALYST + etykiety treningowe |
| SCSE_OPS | Wszystko powyżej + zarządzanie indeksem |
| SCSE_ADMIN | Pełny dostęp + DELETE + rollback |
| SYSTEM_INTEGRATOR | POST /index/entity, POST /index/batch |

## SLA

| Metryka | Cel | Ostrzeżenie | Krytyczny |
|---------|-----|------------|----------|
| P95 latencja wyszukiwania | <300ms | 300–500ms | >500ms |
| P99 latencja wyszukiwania | <1000ms | 1–2s | >2s |
| Zero-result rate | <2% | 2–5% | >5% |
| Świeżość indeksu | <4h | 4–24h | >24h |
| Precision@1 | >0.80 | 0.70–0.80 | <0.70 |
| NDCG@10 | >0.75 | 0.65–0.75 | <0.65 |
| Pokrycie indeksu | >99% | 95–99% | <95% |
| Cache hit ratio | >70% | 50–70% | <50% |

## Integracje

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| Cost History Engine | ← | Kafka | che.quote.recorded → embed quote |
| Supplier Intelligence Engine | ← | Kafka | sie.supplier.updated → re-embed |
| Material Intelligence Engine | ← | Kafka | mie.material.updated → re-embed |
| Manufacturing Process Engine | ← | Kafka | mpe.process.updated → re-embed |
| Cost Calculation Engine | → | REST | /search/materials, /search/suppliers |
| RFQ Engine | → | REST | /search/quotes, /recommend/suppliers |
| Procurement Portal (UI) | → | REST | /search/*, /recommend/*, /cache/* |
| MLflow Model Registry | ↔ | REST | encoder versioning + artifact storage |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | Schema PG, Qdrant setup, EmbeddingPipeline, basic search API, JWT RBAC |
| Full Entity Coverage | S9–S16 | Wszystkie 5 typów encji, re-ranker, confidence, FAISS batch, Kafka consumers |
| Intelligence | S17–S24 | MLP encoder training, FusionLayer, recall evaluation, feedback API, MLflow |
| Scale & Production | S25–S32 | Kubernetes HPA, multi-region Qdrant, GPU FAISS, A/B testing, security hardening |
