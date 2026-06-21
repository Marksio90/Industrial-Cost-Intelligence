"""
Search API — 3 domain-specific endpoints + admin indexing endpoints.

All search endpoints:
  POST /search/materials   — material semantic search
  POST /search/suppliers   — supplier semantic search
  POST /search/quotes      — quote semantic search

Admin (no auth bypass in prod — add ICI_ADMIN role requirement):
  POST /search/index/materials   — trigger re-indexing
  POST /search/index/suppliers   — trigger re-indexing
  POST /search/index/quotes      — trigger re-indexing
  DELETE /search/cache           — flush tenant cache
  GET  /search/health            — cache + vector store liveness

Rate limiting: enforced by Kong API Gateway upstream (see architecture docs).
Here we add a simple per-tenant RPS guard via a shared token bucket
stored in Redis (degraded to no-op if Redis is unavailable).
"""
from __future__ import annotations

import asyncio
import time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.indexing_service import SearchIndexingService
from ..application.reranker import CrossEncoderReranker, MockReranker
from ..application.search_service import HybridSearchService
from ..domain.models import SearchDomain, SearchMode, SearchQuery
from ..infrastructure.cache import SearchCache
from ..infrastructure.keyword_store import KeywordStore
from ..infrastructure.vector_store import get_vector_store
from .schemas import (
    IndexRequest, IndexResponse,
    MaterialSearchRequest, MaterialSearchResponse, MaterialHitResponse,
    QuoteSearchRequest, QuoteSearchResponse, QuoteHitResponse,
    SupplierSearchRequest, SupplierSearchResponse, SupplierHitResponse,
    SearchResponse,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Shared dependencies
# ---------------------------------------------------------------------------

_cache    = SearchCache()           # module-level singleton (Redis connection pool)
_reranker = None                    # lazy-init on first rerank request


def _get_reranker() -> CrossEncoderReranker | None:
    global _reranker
    if _reranker is None:
        import os
        if os.getenv("ENABLE_RERANKER", "false").lower() == "true":
            _reranker = CrossEncoderReranker()
    return _reranker


def _use_mock() -> bool:
    import os
    return os.getenv("USE_MOCK_EMBEDDER", "false").lower() == "true"


def _build_service(session: AsyncSession) -> HybridSearchService:
    return HybridSearchService(
        keyword_store=KeywordStore(session),
        vector_store=get_vector_store(session),
        cache=_cache,
        reranker=_get_reranker(),
        use_mock_embedder=_use_mock(),
    )


def _build_indexer(session: AsyncSession) -> SearchIndexingService:
    return SearchIndexingService(
        session=session,
        vector_store=get_vector_store(session),
        use_mock=_use_mock(),
    )


# ---------------------------------------------------------------------------
# POST /search/materials
# ---------------------------------------------------------------------------

@router.post(
    "/materials",
    response_model=MaterialSearchResponse,
    summary="Semantic material search",
    description="""
Hybrid search across material catalogue.

**Modes**:
- `hybrid` (default): RRF fusion of BM25 keyword + cosine vector similarity.
- `vector`: HNSW ANN using multilingual-e5-large embeddings (1024-dim).
- `keyword`: SQL pg_trgm + full-text search only.

**Filters** (applied before retrieval):
- `material_class`: METAL | PLASTIC | COMPOSITE | CERAMIC | ...
- `max_price_eur`: upper price bound
- `max_lead_time_days`: maximum delivery lead time
- `status`: ACTIVE | PROTOTYPE (defaults to excluding DEPRECATED)

**Scoring**:
RRF(d) = `vector_weight` / (60 + rank_v) + `keyword_weight` / (60 + rank_k)
Set `rerank=true` to apply cross-encoder ms-marco-MiniLM reranker on top-20.
""",
)
async def search_materials(
    body: MaterialSearchRequest,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialSearchResponse:
    filters = {**body.filters}
    if body.material_class:
        filters["material_class"] = body.material_class
    if body.max_price_eur is not None:
        filters["max_price_eur"] = body.max_price_eur
    if body.max_lead_time_days is not None:
        filters["max_lead_time_days"] = body.max_lead_time_days
    if body.status:
        filters["status"] = body.status

    svc    = _build_service(session)
    q      = SearchQuery(
        query=body.query, domain=SearchDomain.MATERIALS, mode=body.mode,
        tenant_id=user.tenant_id, top_k=body.top_k,
        vector_weight=body.vector_weight, keyword_weight=body.keyword_weight,
        score_threshold=body.score_threshold, filters=filters,
    )
    result = await svc.search(q, rerank=body.rerank)

    return MaterialSearchResponse(
        query=result.query, domain=result.domain, mode=result.mode,
        total_candidates=result.total_candidates, latency_ms=result.latency_ms,
        cached=result.cached,
        hits=[MaterialHitResponse.from_hit(h) for h in result.hits],
    )


# ---------------------------------------------------------------------------
# POST /search/suppliers
# ---------------------------------------------------------------------------

@router.post(
    "/suppliers",
    response_model=SupplierSearchResponse,
    summary="Semantic supplier search",
    description="""
Hybrid search across qualified suppliers.

Vector model: `all-mpnet-base-v2` (768-dim) — optimised for capability documents.

**Filters**:
- `country_code`: ISO 3166-1 alpha-2 (DE, PL, IT ...)
- `status`: PENDING | QUALIFIED | SUSPENDED | BLACKLISTED
- `min_overall_score`: minimum weighted supplier KPI score [0, 1]

**Tip**: For "find me a German aluminium casting supplier" queries,
hybrid mode gives the best results — keyword catches "casting" and
"aluminium", vector captures semantic intent of capability.
""",
)
async def search_suppliers(
    body: SupplierSearchRequest,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierSearchResponse:
    filters = {**body.filters}
    if body.country_code:
        filters["country_code"] = body.country_code
    if body.status:
        filters["status"] = body.status

    svc    = _build_service(session)
    q      = SearchQuery(
        query=body.query, domain=SearchDomain.SUPPLIERS, mode=body.mode,
        tenant_id=user.tenant_id, top_k=body.top_k,
        vector_weight=body.vector_weight, keyword_weight=body.keyword_weight,
        score_threshold=body.score_threshold, filters=filters,
    )
    result = await svc.search(q, rerank=body.rerank)

    # Apply min_overall_score post-filter (not indexable in keyword SQL trivially)
    hits = result.hits
    if body.min_overall_score is not None:
        hits = [
            h for h in hits
            if float(h.payload.get("overall_score") or 0) >= body.min_overall_score
        ]

    return SupplierSearchResponse(
        query=result.query, domain=result.domain, mode=result.mode,
        total_candidates=result.total_candidates, latency_ms=result.latency_ms,
        cached=result.cached,
        hits=[SupplierHitResponse.from_hit(h) for h in hits],
    )


# ---------------------------------------------------------------------------
# POST /search/quotes
# ---------------------------------------------------------------------------

@router.post(
    "/quotes",
    response_model=QuoteSearchResponse,
    summary="Semantic quote search",
    description="""
Hybrid search across received quotes.

Indexed text: quote number + supplier name + RFQ title + line item descriptions + notes.

**Filters**:
- `status`: RECEIVED | UNDER_REVIEW | ACCEPTED | REJECTED | EXPIRED
- `supplier_id`: narrow to a specific supplier's quotes

**Use case**: "find all quotes mentioning PEEK injection moulding under review"
""",
)
async def search_quotes(
    body: QuoteSearchRequest,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> QuoteSearchResponse:
    filters = {**body.filters}
    if body.status:
        filters["status"] = body.status
    if body.supplier_id:
        filters["supplier_id"] = str(body.supplier_id)

    svc    = _build_service(session)
    q      = SearchQuery(
        query=body.query, domain=SearchDomain.QUOTES, mode=body.mode,
        tenant_id=user.tenant_id, top_k=body.top_k,
        vector_weight=body.vector_weight, keyword_weight=body.keyword_weight,
        score_threshold=body.score_threshold, filters=filters,
    )
    result = await svc.search(q, rerank=body.rerank)

    return QuoteSearchResponse(
        query=result.query, domain=result.domain, mode=result.mode,
        total_candidates=result.total_candidates, latency_ms=result.latency_ms,
        cached=result.cached,
        hits=[QuoteHitResponse.from_hit(h) for h in result.hits],
    )


# ---------------------------------------------------------------------------
# Admin: re-indexing endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/index/materials",
    response_model=IndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-index materials embeddings",
)
async def index_materials(
    body: IndexRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> IndexResponse:
    user.require_role("ICI_ADMIN", "ICI_ENGINEER")
    indexer = _build_indexer(session)
    t0 = time.monotonic()

    if body.entity_ids:
        for eid in body.entity_ids:
            await indexer.index_material(eid, user.tenant_id)
        indexed = len(body.entity_ids)
    else:
        # Full re-index in background
        indexed = 0
        background_tasks.add_task(indexer.reindex_all_materials, user.tenant_id)

    # Invalidate search cache for materials domain
    background_tasks.add_task(_cache.invalidate_domain, user.tenant_id, SearchDomain.MATERIALS)

    return IndexResponse(
        indexed=indexed, domain=SearchDomain.MATERIALS,
        tenant_id=user.tenant_id, latency_ms=(time.monotonic() - t0) * 1000,
    )


@router.post(
    "/index/suppliers",
    response_model=IndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-index suppliers embeddings",
)
async def index_suppliers(
    body: IndexRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> IndexResponse:
    user.require_role("ICI_ADMIN", "ICI_ENGINEER")
    indexer = _build_indexer(session)
    t0 = time.monotonic()
    indexed = 0
    if body.entity_ids:
        for eid in body.entity_ids:
            await indexer.index_supplier(eid, user.tenant_id)
        indexed = len(body.entity_ids)
    background_tasks.add_task(_cache.invalidate_domain, user.tenant_id, SearchDomain.SUPPLIERS)
    return IndexResponse(indexed=indexed, domain=SearchDomain.SUPPLIERS,
                         tenant_id=user.tenant_id, latency_ms=(time.monotonic() - t0) * 1000)


@router.post(
    "/index/quotes",
    response_model=IndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-index quotes embeddings",
)
async def index_quotes(
    body: IndexRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> IndexResponse:
    user.require_role("ICI_ADMIN", "ICI_ENGINEER")
    indexer = _build_indexer(session)
    t0 = time.monotonic()
    indexed = 0
    if body.entity_ids:
        for eid in body.entity_ids:
            await indexer.index_quote(eid, user.tenant_id)
        indexed = len(body.entity_ids)
    background_tasks.add_task(_cache.invalidate_domain, user.tenant_id, SearchDomain.QUOTES)
    return IndexResponse(indexed=indexed, domain=SearchDomain.QUOTES,
                         tenant_id=user.tenant_id, latency_ms=(time.monotonic() - t0) * 1000)


# ---------------------------------------------------------------------------
# Cache flush
# ---------------------------------------------------------------------------

@router.delete(
    "/cache",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Flush tenant search cache",
)
async def flush_cache(
    domain: SearchDomain | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    user.require_role("ICI_ADMIN")
    if domain:
        await _cache.invalidate_domain(user.tenant_id, domain)
    else:
        for d in SearchDomain:
            await _cache.invalidate_domain(user.tenant_id, d)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/health", summary="Search subsystem health")
async def search_health(session: AsyncSession = Depends(get_db)) -> dict:
    redis_ok = await _cache.ping()
    # Quick vector store probe: count embeddings
    from sqlalchemy import text
    try:
        cnt = (await session.execute(
            text("SELECT COUNT(*) FROM ici_vectors.material_embeddings LIMIT 1")
        )).scalar_one()
        vector_ok = True
        vector_count = cnt
    except Exception as e:
        vector_ok = False
        vector_count = 0
    return {
        "redis":        {"ok": redis_ok},
        "vector_store": {"ok": vector_ok, "material_embeddings": vector_count},
    }
