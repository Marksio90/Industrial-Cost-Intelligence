"""
Search module tests.

Unit tests run without DB or Redis — using MockEmbedder + in-memory stubs.
Integration tests (marked @pytest.mark.integration) require a running PG.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
import pytest

from src.modules.search.application.embedder import MockEmbedder, EmbedderRegistry
from src.modules.search.application.reranker import MockReranker
from src.modules.search.application.search_service import HybridSearchService, _RRF_K
from src.modules.search.domain.models import (
    EmbeddingModel, MaterialFeatures, ProcessFeatures, QuoteFeatures,
    SearchDomain, SearchMode, SearchQuery, SupplierFeatures,
)
from src.modules.search.infrastructure.cache import SearchCache, build_cache_key, _LRUCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_embedder_registry():
    EmbedderRegistry.clear()
    yield
    EmbedderRegistry.clear()


def _mock_service(kw_hits=None, v_hits=None):
    kw_store = AsyncMock()
    kw_store.query.return_value = kw_hits or []
    vs = AsyncMock()
    vs.query.return_value = v_hits or []
    cache = SearchCache(redis_url=None)  # no Redis — LRU fallback
    return HybridSearchService(
        keyword_store=kw_store,
        vector_store=vs,
        cache=cache,
        reranker=None,
        use_mock_embedder=True,
    )


def _make_kw_hits(n: int) -> list[dict]:
    return [{"entity_id": uuid4(), "score": 1 - i * 0.05, "payload": {"name": f"mat-{i}"}} for i in range(n)]


def _make_v_hits(n: int) -> list[dict]:
    return [{"entity_id": uuid4(), "score": 1 - i * 0.04, "payload": {"name": f"mat-{i}"}} for i in range(n)]


# ---------------------------------------------------------------------------
# EmbedderProtocol / MockEmbedder
# ---------------------------------------------------------------------------

class TestMockEmbedder:
    def test_embed_query_returns_unit_vector(self):
        emb = MockEmbedder(dimensions=64)
        vec = emb.embed_query("steel sheet")
        assert vec.shape == (64,)
        assert abs(np.linalg.norm(vec) - 1.0) < 1e-5

    def test_same_text_same_vector(self):
        emb = MockEmbedder(dimensions=64)
        v1  = emb.embed_query("steel")
        v2  = emb.embed_query("steel")
        np.testing.assert_array_equal(v1, v2)

    def test_different_text_different_vector(self):
        emb = MockEmbedder(dimensions=64)
        v1  = emb.embed_query("steel")
        v2  = emb.embed_query("aluminium")
        assert not np.allclose(v1, v2)

    def test_embed_passages_batch(self):
        emb = MockEmbedder(dimensions=32)
        vecs = emb.embed_passages(["a", "b", "c"])
        assert vecs.shape == (3, 32)

    @pytest.mark.asyncio
    async def test_async_embed_query(self):
        emb = MockEmbedder(dimensions=16)
        vec = await emb.aembed_query("test")
        assert vec.shape == (16,)


# ---------------------------------------------------------------------------
# Feature passage generation
# ---------------------------------------------------------------------------

class TestFeaturePassages:
    def test_material_passage_contains_key_fields(self):
        f = MaterialFeatures(
            material_number="MAT-ST-001", name="Steel S235JR",
            description="Hot-rolled structural steel", material_class="METAL",
            base_unit="KG", price_eur=Decimal("0.72"), lead_time_days=7,
            density_g_cm3=Decimal("7.85"), supplier_name="Schulz Metallwerk",
        )
        p = f.to_passage()
        assert "MAT-ST-001" in p
        assert "Steel S235JR" in p
        assert "0.72" in p
        assert "7 days" in p
        assert "Schulz Metallwerk" in p
        assert p.startswith("passage: ")

    def test_supplier_passage_has_score(self):
        f = SupplierFeatures(
            code="SUP-DE-001", name="Schulz Metallwerk", country_code="DE",
            city="Stuttgart", status="QUALIFIED",
            quality_score=Decimal("0.91"), delivery_score=Decimal("0.88"),
            overall_score=Decimal("0.87"), capability_text="CNC machining, casting",
        )
        p = f.to_passage()
        assert "Stuttgart" in p
        assert "0.87" in p
        assert "CNC machining" in p

    def test_process_passage(self):
        f = ProcessFeatures(
            process_code="PROC-CNC-001", name="5-Axis CNC Milling",
            process_type="MACHINING", description="DMG MORI DMU 50",
            machine_rate_eur_hr=Decimal("95"), labor_rate_eur_hr=Decimal("28"),
            cycle_time_seconds=Decimal("180"), scrap_rate=Decimal("0.02"),
        )
        p = f.to_passage()
        assert "MACHINING" in p
        assert "95" in p
        assert "2.0%" in p

    def test_sparse_tokens(self):
        f = MaterialFeatures(
            material_number="MAT-AL-001", name="Aluminium 6061", description="",
            material_class="METAL", base_unit="KG", price_eur=Decimal("3.2"),
            lead_time_days=14, density_g_cm3=None, supplier_name=None,
        )
        tokens = f.to_sparse_tokens()
        assert any("MAT" in t for t in tokens)
        assert "Aluminium 6061" in tokens


# ---------------------------------------------------------------------------
# EmbedderRegistry
# ---------------------------------------------------------------------------

class TestEmbedderRegistry:
    def test_get_mock_returns_mock(self):
        emb = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=True)
        assert isinstance(emb, MockEmbedder)

    def test_get_returns_same_instance(self):
        e1 = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=True)
        e2 = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=True)
        assert e1 is e2

    def test_register_custom(self):
        custom = MockEmbedder("custom", 128)
        EmbedderRegistry.register(EmbeddingModel.MULTILINGUAL_E5_LARGE, custom)
        got = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE)
        assert got is custom


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

class TestRRFFusion:
    def _svc(self):
        return _mock_service()

    def _q(self, vw=0.7, kw=0.3):
        return SearchQuery(
            query="steel", domain=SearchDomain.MATERIALS,
            mode=SearchMode.HYBRID, tenant_id="t1",
            vector_weight=vw, keyword_weight=kw,
        )

    def test_rrf_formula(self):
        svc = self._svc()
        q   = self._q()
        eid = uuid4()
        v_hits  = [{"entity_id": eid, "score": 0.9, "payload": {}}]
        kw_hits = [{"entity_id": eid, "score": 0.8, "payload": {}}]
        fused = svc._rrf_fuse(kw_hits, v_hits, q)
        # Both rank 1 → RRF = 0.7/(60+1) + 0.3/(60+1) = 1.0/(61) ≈ 0.01639
        assert len(fused) == 1
        # After normalisation of a single item: score = 1.0
        assert abs(fused[0]["score"] - 1.0) < 1e-6

    def test_rrf_union_of_hits(self):
        svc = self._svc()
        q   = self._q()
        e1, e2, e3 = uuid4(), uuid4(), uuid4()
        v_hits  = [{"entity_id": e1, "score": 0.9, "payload": {}},
                   {"entity_id": e2, "score": 0.8, "payload": {}}]
        kw_hits = [{"entity_id": e2, "score": 0.85, "payload": {}},
                   {"entity_id": e3, "score": 0.7,  "payload": {}}]
        fused = svc._rrf_fuse(kw_hits, v_hits, q)
        entity_ids = {h["entity_id"] for h in fused}
        assert {e1, e2, e3} == entity_ids

    def test_rrf_vector_dominant(self):
        """With α=0.9, entity that ranks #1 in vector should win overall."""
        svc = self._svc()
        q   = self._q(vw=0.9, kw=0.1)
        e1, e2 = uuid4(), uuid4()
        v_hits  = [{"entity_id": e1, "score": 0.95, "payload": {}},
                   {"entity_id": e2, "score": 0.60, "payload": {}}]
        kw_hits = [{"entity_id": e2, "score": 0.95, "payload": {}},
                   {"entity_id": e1, "score": 0.60, "payload": {}}]
        fused = svc._rrf_fuse(kw_hits, v_hits, q)
        assert fused[0]["entity_id"] == e1

    def test_scores_normalised_to_unit_range(self):
        svc = self._svc()
        q   = self._q()
        hits = [{"entity_id": uuid4(), "score": 0.9 - i * 0.1, "payload": {}} for i in range(5)]
        fused = svc._rrf_fuse(hits, hits, q)
        scores = [h["score"] for h in fused]
        assert max(scores) <= 1.0 + 1e-9
        assert min(scores) >= 0.0 - 1e-9


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------

class TestMockReranker:
    @pytest.mark.asyncio
    async def test_higher_overlap_higher_score(self):
        r = MockReranker()
        pairs = [
            ("steel sheet metal", "steel aluminium sheet cold rolled"),
            ("steel sheet metal", "plastic injection moulding"),
        ]
        scores = await r.score_pairs(pairs)
        assert scores[0] > scores[1]

    @pytest.mark.asyncio
    async def test_empty_pairs(self):
        r = MockReranker()
        assert await r.score_pairs([]) == []


# ---------------------------------------------------------------------------
# HybridSearchService — end-to-end with mocks
# ---------------------------------------------------------------------------

class TestHybridSearchService:
    @pytest.mark.asyncio
    async def test_keyword_only_mode(self):
        kw = _make_kw_hits(5)
        svc = _mock_service(kw_hits=kw)
        q = SearchQuery(query="steel", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.KEYWORD, tenant_id="t1", top_k=3)
        result = await svc.search(q)
        assert result.mode == SearchMode.KEYWORD
        assert len(result.hits) == 3
        assert all(h.vector_score is None for h in result.hits)

    @pytest.mark.asyncio
    async def test_vector_only_mode(self):
        v = _make_v_hits(5)
        svc = _mock_service(v_hits=v)
        q = SearchQuery(query="aluminium alloy lightweight",
                        domain=SearchDomain.MATERIALS,
                        mode=SearchMode.VECTOR, tenant_id="t1", top_k=3)
        result = await svc.search(q)
        assert result.mode == SearchMode.VECTOR
        assert len(result.hits) <= 3
        assert all(h.keyword_score is None for h in result.hits)

    @pytest.mark.asyncio
    async def test_hybrid_mode_returns_union(self):
        kw = _make_kw_hits(5)
        v  = _make_v_hits(5)
        svc = _mock_service(kw_hits=kw, v_hits=v)
        q = SearchQuery(query="metal sheet", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.HYBRID, tenant_id="t1", top_k=10)
        result = await svc.search(q)
        # Union of 5+5 (all unique) = 10 entities
        assert len(result.hits) == 10

    @pytest.mark.asyncio
    async def test_top_k_respected(self):
        kw = _make_kw_hits(20)
        v  = _make_v_hits(20)
        svc = _mock_service(kw_hits=kw, v_hits=v)
        q = SearchQuery(query="x", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.HYBRID, tenant_id="t1", top_k=5)
        result = await svc.search(q)
        assert len(result.hits) <= 5

    @pytest.mark.asyncio
    async def test_score_threshold_filters(self):
        kw = [{"entity_id": uuid4(), "score": 0.05, "payload": {}}]
        svc = _mock_service(kw_hits=kw)
        q = SearchQuery(query="x", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.KEYWORD, tenant_id="t1",
                        top_k=10, score_threshold=0.5)
        result = await svc.search(q)
        # After normalisation of single hit: score=1.0 ≥ 0.5
        assert all(h.score >= 0.5 for h in result.hits)

    @pytest.mark.asyncio
    async def test_result_ranks_are_sequential(self):
        kw = _make_kw_hits(5)
        svc = _mock_service(kw_hits=kw)
        q = SearchQuery(query="x", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.KEYWORD, tenant_id="t1", top_k=5)
        result = await svc.search(q)
        assert [h.rank for h in result.hits] == list(range(1, len(result.hits) + 1))

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self):
        kw = _make_kw_hits(3)
        svc = _mock_service(kw_hits=kw)
        q = SearchQuery(query="cached query", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.KEYWORD, tenant_id="t1", top_k=3)
        r1 = await svc.search(q)
        assert not r1.cached
        # Small sleep to let cache write task complete
        await asyncio.sleep(0.01)
        r2 = await svc.search(q)
        assert r2.cached

    @pytest.mark.asyncio
    async def test_latency_ms_is_positive(self):
        svc = _mock_service(kw_hits=_make_kw_hits(2))
        q = SearchQuery(query="x", domain=SearchDomain.MATERIALS,
                        mode=SearchMode.KEYWORD, tenant_id="t1")
        r = await svc.search(q)
        assert r.latency_ms > 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestLRUCache:
    def test_set_and_get(self):
        c = _LRUCache(10)
        c.set("k1", "v1", ttl=60)
        assert c.get("k1") == "v1"

    def test_ttl_expiry(self):
        import time
        c = _LRUCache(10)
        c.set("k", "v", ttl=0)   # TTL=0 → expires immediately
        import time as t
        t.sleep(0.001)
        assert c.get("k") is None

    def test_lru_eviction(self):
        c = _LRUCache(3)
        c.set("a", 1, 60); c.set("b", 2, 60); c.set("c", 3, 60)
        c.set("d", 4, 60)  # evicts 'a' (LRU)
        assert c.get("a") is None
        assert c.get("d") == 4

    def test_delete_pattern(self):
        c = _LRUCache(10)
        c.set("search:t1:materials:abc", 1, 60)
        c.set("search:t1:materials:def", 2, 60)
        c.set("search:t1:suppliers:xyz", 3, 60)
        deleted = c.delete_pattern("search:t1:materials:")
        assert deleted == 2
        assert c.get("search:t1:suppliers:xyz") == 3


class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = build_cache_key("t", SearchDomain.MATERIALS, "steel", SearchMode.HYBRID, 10, {})
        k2 = build_cache_key("t", SearchDomain.MATERIALS, "steel", SearchMode.HYBRID, 10, {})
        assert k1 == k2

    def test_different_query_different_key(self):
        k1 = build_cache_key("t", SearchDomain.MATERIALS, "steel", SearchMode.HYBRID, 10, {})
        k2 = build_cache_key("t", SearchDomain.MATERIALS, "aluminium", SearchMode.HYBRID, 10, {})
        assert k1 != k2

    def test_key_prefix(self):
        k = build_cache_key("tenant-demo", SearchDomain.SUPPLIERS, "q", SearchMode.VECTOR, 5, {})
        assert k.startswith("search:tenant-demo:suppliers:")


# ---------------------------------------------------------------------------
# API integration tests (require live DB + USE_MOCK_EMBEDDER=true)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSearchAPI:
    @pytest.mark.asyncio
    async def test_search_materials_returns_200(self, client):
        resp = await client.post("/api/v1/search/materials", json={"query": "steel sheet"})
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data
        assert "latency_ms" in data
        assert data["domain"] == "materials"

    @pytest.mark.asyncio
    async def test_search_materials_keyword_mode(self, client):
        resp = await client.post("/api/v1/search/materials",
                                 json={"query": "aluminium", "mode": "keyword"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_suppliers_country_filter(self, client):
        resp = await client.post("/api/v1/search/suppliers",
                                 json={"query": "casting", "country_code": "DE"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_quotes(self, client):
        resp = await client.post("/api/v1/search/quotes",
                                 json={"query": "aluminium housing"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_health(self, client):
        resp = await client.get("/api/v1/search/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "redis" in data
        assert "vector_store" in data

    @pytest.mark.asyncio
    async def test_invalid_query_too_short(self, client):
        resp = await client.post("/api/v1/search/materials", json={"query": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_top_k_capped_at_100(self, client):
        resp = await client.post("/api/v1/search/materials",
                                 json={"query": "steel", "top_k": 200})
        assert resp.status_code == 422
