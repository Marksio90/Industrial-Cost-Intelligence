"""
Hybrid search pipeline: Keyword + Vector → RRF fusion → Reranking.

Algorithm:
─────────────────────────────────────────────────────────────────────────────
  1. KEYWORD PASS  (SQL / pg_trgm + FTS)
       Returns top-K₁ candidates with normalised score ∈ [0, 1].

  2. VECTOR PASS  (HNSW ANN via pgvector or Qdrant)
       Query vector embedded with "query: " prefix (E5 asymmetric).
       Returns top-K₂ candidates with cosine similarity ∈ [0, 1].

  3. RECIPROCAL RANK FUSION  (RRF)
       Classic RRF formula with k=60:
         RRF(d) = Σ  α_i / (k + rank_i(d))
       α_keyword = 0.3, α_vector = 0.7 (tunable per domain)
       All three lists are pooled; missing rank → penalty rank = K + 1.

  4. CROSS-ENCODER RERANKER  (optional, triggered by rerank=True)
       Reranker model scores (query, passage) pairs.
       Applied to top-N_rerank candidates only (default 20) for latency.
       We use ms-marco-MiniLM-L-6-v2 (6-layer, ~5ms per pair on CPU).

  5. SCORE NORMALISATION
       Final scores linearly scaled to [0, 1] for consistent API output.

─────────────────────────────────────────────────────────────────────────────
Performance targets (single-node, p95):
  Keyword-only:   < 20ms
  Vector-only:    < 30ms
  Hybrid no rerank: < 50ms
  Hybrid + rerank:  < 150ms  (20 pairs × ~7ms per pair)
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import UUID

import numpy as np

from ..domain.models import (
    EmbeddingModel, SearchDomain, SearchHit, SearchMode, SearchQuery, SearchResult,
)
from ..infrastructure.cache import SearchCache, build_cache_key
from ..infrastructure.keyword_store import KeywordStore
from ..infrastructure.vector_store import VectorStoreProtocol
from .embedder import EmbedderProtocol, EmbedderRegistry, embed_search_query
from .reranker import CrossEncoderReranker

logger = logging.getLogger(__name__)

# RRF constant — lower k = higher influence of top ranks; 60 is standard
_RRF_K = 60

# Default model per domain
_DOMAIN_MODEL: dict[SearchDomain, EmbeddingModel] = {
    SearchDomain.MATERIALS: EmbeddingModel.MULTILINGUAL_E5_LARGE,
    SearchDomain.SUPPLIERS: EmbeddingModel.ALL_MPNET_BASE_V2,
    SearchDomain.QUOTES:    EmbeddingModel.MULTILINGUAL_E5_LARGE,
    SearchDomain.PROCESSES: EmbeddingModel.MULTILINGUAL_E5_LARGE,
}


# ---------------------------------------------------------------------------
# Similarity scoring formula (documented here, referenced in docstring)
# ---------------------------------------------------------------------------
#
# Cosine similarity (vector leg):
#   sim(q, d) = (q · d) / (‖q‖ · ‖d‖)
#   Since both are unit vectors (normalised at embed time): sim = q · d
#
# BM25-approximate (keyword leg, via pg_trgm + FTS):
#   score_k = 0.65 × word_similarity(q, text) + 0.35 × ts_rank_norm(q, text)
#
# RRF fusion:
#   α = vector_weight (default 0.7), β = keyword_weight (default 0.3)
#   RRF(d) = α / (k + rank_v(d)) + β / (k + rank_k(d))
#
# Cross-encoder rerank (optional):
#   score_ce = CrossEncoder.predict([(q, passage_d)])  ∈ (-∞, +∞)
#   normalised via softmax over the rerank candidate set.
#
# Final output score:
#   If reranked: linear scale softmax(score_ce) → [0, 1]
#   Else:        linear scale RRF scores          → [0, 1]


class HybridSearchService:

    # How many candidates to fetch per leg before fusion
    _VECTOR_FETCH_MULT  = 3  # fetch top_k × 3 from vector store
    _KEYWORD_FETCH_MULT = 3

    def __init__(
        self,
        keyword_store: KeywordStore,
        vector_store: VectorStoreProtocol,
        cache: SearchCache,
        reranker: CrossEncoderReranker | None = None,
        use_mock_embedder: bool = False,
    ) -> None:
        self._kw      = keyword_store
        self._vs      = vector_store
        self._cache   = cache
        self._reranker = reranker
        self._mock    = use_mock_embedder

    # ----------------------------------------------------------------
    # Public entry point
    # ----------------------------------------------------------------

    async def search(self, query: SearchQuery, rerank: bool = False) -> SearchResult:
        t0 = time.monotonic()

        # 1. Cache check
        key = build_cache_key(
            query.tenant_id, query.domain, query.query,
            query.mode, query.top_k, query.filters,
        )
        cached = await self._cache.get(key)
        if cached:
            return cached

        # 2. Dispatch to correct pipeline leg(s)
        kw_hits: list[dict] = []
        v_hits:  list[dict] = []

        if query.mode in (SearchMode.KEYWORD, SearchMode.HYBRID):
            kw_hits = await self._keyword_search(query)

        if query.mode in (SearchMode.VECTOR, SearchMode.HYBRID):
            v_hits = await self._vector_search(query)

        # 3. Fusion
        if query.mode == SearchMode.HYBRID:
            fused = self._rrf_fuse(kw_hits, v_hits, query)
        elif query.mode == SearchMode.KEYWORD:
            fused = [{"entity_id": h["entity_id"], "score": h["score"],
                      "vector_score": None, "keyword_score": h["score"],
                      "payload": h.get("payload", {})} for h in kw_hits]
        else:
            fused = [{"entity_id": h["entity_id"], "score": h["score"],
                      "vector_score": h["score"], "keyword_score": None,
                      "payload": h.get("payload", {})} for h in v_hits]

        total_candidates = len(fused)
        fused = fused[: query.top_k * 2]  # cap before optional rerank

        # 4. Optional cross-encoder rerank
        if rerank and self._reranker and fused:
            fused = await self._rerank(query.query, fused)

        # 5. Threshold filter + top-k clip
        fused = [h for h in fused if h["score"] >= query.score_threshold]
        fused = fused[: query.top_k]

        # 6. Build result
        hits = [
            SearchHit(
                entity_id=h["entity_id"],
                domain=query.domain,
                score=h["score"],
                vector_score=h.get("vector_score"),
                keyword_score=h.get("keyword_score"),
                rank=i + 1,
                payload=h.get("payload", {}),
            )
            for i, h in enumerate(fused)
        ]
        latency_ms = (time.monotonic() - t0) * 1000
        result = SearchResult(
            query=query.query, domain=query.domain, mode=query.mode,
            hits=hits, total_candidates=total_candidates, latency_ms=latency_ms,
        )

        # 7. Cache write (fire-and-forget)
        asyncio.create_task(self._cache.set(key, result, query.mode))

        return result

    # ----------------------------------------------------------------
    # Keyword leg
    # ----------------------------------------------------------------

    async def _keyword_search(self, query: SearchQuery) -> list[dict]:
        k = query.top_k * self._KEYWORD_FETCH_MULT
        return await self._kw.query(
            domain=query.domain,
            query_text=query.query,
            tenant_id=query.tenant_id,
            top_k=k,
            filters=query.filters,
        )

    # ----------------------------------------------------------------
    # Vector leg
    # ----------------------------------------------------------------

    async def _vector_search(self, query: SearchQuery) -> list[dict]:
        model    = _DOMAIN_MODEL[query.domain]
        embedder = EmbedderRegistry.get(model, use_mock=self._mock)
        q_vec    = await embed_search_query(query.query, embedder)
        k        = query.top_k * self._VECTOR_FETCH_MULT

        return await self._vs.query(
            domain=query.domain,
            query_vector=q_vec,
            tenant_id=query.tenant_id,
            top_k=k,
            score_threshold=0.0,  # threshold applied after fusion
            filters=query.filters,
        )

    # ----------------------------------------------------------------
    # RRF fusion
    # ----------------------------------------------------------------

    def _rrf_fuse(
        self,
        kw_hits: list[dict],
        v_hits:  list[dict],
        query: SearchQuery,
    ) -> list[dict]:
        """
        Reciprocal Rank Fusion.

        RRF(d) = α / (k + rank_v(d))  +  β / (k + rank_k(d))

        where α = vector_weight, β = keyword_weight, k = 60.
        Missing from a leg → rank = len(leg) + 1 (soft penalty, not zero).
        """
        α = query.vector_weight
        β = query.keyword_weight

        # Build rank maps  entity_id → rank (1-based)
        v_rank: dict[UUID, int]  = {h["entity_id"]: i + 1 for i, h in enumerate(v_hits)}
        kw_rank: dict[UUID, int] = {h["entity_id"]: i + 1 for i, h in enumerate(kw_hits)}

        # Build payload maps
        v_payload:  dict[UUID, dict] = {h["entity_id"]: h.get("payload", {}) for h in v_hits}
        kw_payload: dict[UUID, dict] = {h["entity_id"]: h.get("payload", {}) for h in kw_hits}
        v_score_map:  dict[UUID, float] = {h["entity_id"]: float(h["score"]) for h in v_hits}
        kw_score_map: dict[UUID, float] = {h["entity_id"]: float(h["score"]) for h in kw_hits}

        v_max_rank  = len(v_hits) + 1
        kw_max_rank = len(kw_hits) + 1

        all_ids = set(v_rank) | set(kw_rank)
        fused: list[dict] = []

        for eid in all_ids:
            rv   = v_rank.get(eid,  v_max_rank)
            rk   = kw_rank.get(eid, kw_max_rank)
            rrf  = α / (_RRF_K + rv) + β / (_RRF_K + rk)
            pl   = {**kw_payload.get(eid, {}), **v_payload.get(eid, {})}
            fused.append({
                "entity_id":    eid,
                "score":        rrf,
                "vector_score": v_score_map.get(eid),
                "keyword_score": kw_score_map.get(eid),
                "payload":      pl,
            })

        # Sort by RRF score desc
        fused.sort(key=lambda x: x["score"], reverse=True)

        # Normalise RRF scores to [0, 1]
        if fused:
            max_s = fused[0]["score"]
            min_s = fused[-1]["score"]
            rng   = max_s - min_s if max_s != min_s else 1.0
            for h in fused:
                h["score"] = (h["score"] - min_s) / rng

        return fused

    # ----------------------------------------------------------------
    # Cross-encoder reranking
    # ----------------------------------------------------------------

    async def _rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        if not self._reranker:
            return candidates

        # Extract passage text from payload (best-effort)
        pairs = [(query, h["payload"].get("embedded_text", h["payload"].get("name", ""))) for h in candidates]
        scores = await self._reranker.score_pairs(pairs)

        # Attach reranker score and re-sort
        for h, s in zip(candidates, scores):
            h["score"] = float(s)
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Normalise to [0, 1] using softmax
        raw = np.array([h["score"] for h in candidates], dtype=np.float32)
        exp = np.exp(raw - raw.max())
        norm = exp / exp.sum()
        for h, n in zip(candidates, norm.tolist()):
            h["score"] = n

        return candidates
