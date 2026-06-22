"""
Section 7 — Search Layer

Hybrid search pipeline:
  1. Fulltext search  (Neo4j fulltext index)
  2. Vector ANN       (Neo4j vector index, HNSW cosine)
  3. Structural       (graph-context re-ranking)
  4. Result fusion    (Reciprocal Rank Fusion)

Entry points:
  SearchEngine.search(query, tenant_id, ...)
  SearchEngine.search_by_vector(embedding, ...)
  SearchEngine.search_by_filter(filters, ...)
  SearchEngine.autocomplete(prefix, ...)
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .models import NodeType
from .embeddings import EmbeddingEncoder, build_node_prompt

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    node_id:   str
    node_type: str
    name:      str
    score:     float
    explain:   dict[str, Any] = field(default_factory=dict)
    props:     dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    hits:         list[SearchHit]
    total:        int
    took_ms:      float
    query:        str
    strategy:     str   # fulltext | vector | hybrid | filter


# ─────────────────────────────────────────────────────────────────────────────
# Cypher templates
# ─────────────────────────────────────────────────────────────────────────────

_FULLTEXT_GLOBAL = """
CALL db.index.fulltext.queryNodes('global_ft_idx', $query)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND ($node_types IS NULL OR any(lbl IN labels(node) WHERE lbl IN $node_types))
RETURN node.node_id AS node_id, labels(node)[0] AS node_type,
       node.name AS name, score, properties(node) AS props
ORDER BY score DESC
LIMIT $limit
"""

_FULLTEXT_MATERIAL = """
CALL db.index.fulltext.queryNodes('material_ft_idx', $query)
YIELD node, score
WHERE node.tenant_id = $tenant_id
RETURN node.node_id AS node_id, 'Material' AS node_type,
       node.name AS name, score, properties(node) AS props
ORDER BY score DESC
LIMIT $limit
"""

_FULLTEXT_SUPPLIER = """
CALL db.index.fulltext.queryNodes('supplier_ft_idx', $query)
YIELD node, score
WHERE node.tenant_id = $tenant_id
RETURN node.node_id AS node_id, 'Supplier' AS node_type,
       node.name AS name, score, properties(node) AS props
ORDER BY score DESC
LIMIT $limit
"""

_VECTOR_SEARCH = """
CALL db.index.vector.queryNodes($index_name, $limit, $embedding)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND ($node_types IS NULL OR any(lbl IN labels(node) WHERE lbl IN $node_types))
RETURN node.node_id AS node_id, labels(node)[0] AS node_type,
       node.name AS name, score, properties(node) AS props
"""

_FILTER_MATERIALS = """
MATCH (n:Material)
WHERE n.tenant_id = $tenant_id
  AND ($material_class IS NULL OR n.material_class = $material_class)
  AND ($grade IS NULL OR n.grade = $grade)
  AND ($is_critical IS NULL OR n.is_critical = $is_critical)
  AND ($is_hazmat IS NULL OR n.is_hazmat = $is_hazmat)
  AND ($reach_compliant IS NULL OR n.reach_compliant = $reach_compliant)
RETURN n.node_id AS node_id, 'Material' AS node_type,
       n.name AS name, 1.0 AS score, properties(n) AS props
ORDER BY n.name
SKIP $skip LIMIT $limit
"""

_FILTER_SUPPLIERS = """
MATCH (n:Supplier)
WHERE n.tenant_id = $tenant_id
  AND ($country IS NULL OR n.country = $country)
  AND ($approved IS NULL OR n.approved = $approved)
  AND ($preferred IS NULL OR n.preferred = $preferred)
  AND ($iso_9001 IS NULL OR n.iso_9001 = $iso_9001)
  AND ($risk_max IS NULL OR n.risk_score <= $risk_max)
RETURN n.node_id AS node_id, 'Supplier' AS node_type,
       n.name AS name, 1.0 AS score, properties(n) AS props
ORDER BY n.quality_score DESC
SKIP $skip LIMIT $limit
"""

_AUTOCOMPLETE = """
CALL db.index.fulltext.queryNodes('global_ft_idx', $prefix + '*')
YIELD node, score
WHERE node.tenant_id = $tenant_id
RETURN node.node_id AS node_id, labels(node)[0] AS node_type,
       node.name AS name, score
ORDER BY score DESC
LIMIT $limit
"""

_GRAPH_CONTEXT = """
MATCH (n {node_id: $node_id})-[r]-(neighbor)
WHERE neighbor.tenant_id = $tenant_id
RETURN neighbor.node_id AS node_id, type(r) AS relation, r.weight AS weight
LIMIT 20
"""

# ─────────────────────────────────────────────────────────────────────────────
# Reciprocal Rank Fusion
# ─────────────────────────────────────────────────────────────────────────────

def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchHit]],
    k: int = 60,
) -> list[SearchHit]:
    """
    Combine multiple ranked lists via RRF.
    score(d) = sum_i 1 / (k + rank_i(d))
    """
    scores: dict[str, float] = {}
    best_hit: dict[str, SearchHit] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            scores[hit.node_id] = scores.get(hit.node_id, 0.0) + 1.0 / (k + rank)
            if hit.node_id not in best_hit or hit.score > best_hit[hit.node_id].score:
                best_hit[hit.node_id] = hit

    fused = []
    for node_id, rrf_score in sorted(scores.items(), key=lambda x: -x[1]):
        hit = best_hit[node_id]
        hit.explain["rrf_score"] = round(rrf_score, 6)
        hit.score = rrf_score
        fused.append(hit)
    return fused


# ─────────────────────────────────────────────────────────────────────────────
# SearchEngine
# ─────────────────────────────────────────────────────────────────────────────

_VECTOR_INDEX: dict[str, str] = {
    NodeType.MATERIAL.value: "material_embedding_idx",
    NodeType.PRODUCT.value:  "product_embedding_idx",
    NodeType.SUPPLIER.value: "supplier_embedding_idx",
}


class SearchEngine:
    """
    Hybrid search over the knowledge graph.

    Parameters
    ----------
    driver   : Neo4j AsyncDriver
    encoder  : EmbeddingEncoder (optional — used for semantic search)
    """

    def __init__(self, driver: Any, encoder: EmbeddingEncoder | None = None) -> None:
        self._driver  = driver
        self._encoder = encoder or EmbeddingEncoder()

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        tenant_id: str,
        node_types: list[str] | None = None,
        limit: int = 20,
        strategy: str = "hybrid",   # fulltext | vector | hybrid
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        t0 = time.monotonic()
        hits: list[SearchHit] = []

        if strategy == "fulltext":
            hits = await self._fulltext(query, tenant_id, node_types, limit)
        elif strategy == "vector":
            hits = await self._vector(query, tenant_id, node_types, limit)
        elif strategy == "hybrid":
            ft_task  = asyncio.create_task(self._fulltext(query, tenant_id, node_types, limit))
            vec_task = asyncio.create_task(self._vector(query, tenant_id, node_types, limit))
            ft_hits, vec_hits = await asyncio.gather(ft_task, vec_task)
            hits = reciprocal_rank_fusion([ft_hits, vec_hits])[:limit]
        else:
            hits = await self._fulltext(query, tenant_id, node_types, limit)

        if filters:
            hits = self._apply_filters(hits, filters)

        took = (time.monotonic() - t0) * 1000
        return SearchResult(
            hits=hits[:limit],
            total=len(hits),
            took_ms=round(took, 2),
            query=query,
            strategy=strategy,
        )

    async def search_by_vector(
        self,
        embedding: list[float],
        tenant_id: str,
        node_types: list[str] | None = None,
        limit: int = 20,
    ) -> SearchResult:
        t0   = time.monotonic()
        hits = await self._vector_by_embedding(embedding, tenant_id, node_types, limit)
        took = (time.monotonic() - t0) * 1000
        return SearchResult(hits=hits, total=len(hits), took_ms=round(took, 2), query="<vector>", strategy="vector")

    async def search_by_filter(
        self,
        filters: dict[str, Any],
        tenant_id: str,
        node_type: str = "Material",
        skip: int = 0,
        limit: int = 50,
    ) -> SearchResult:
        t0   = time.monotonic()
        hits = await self._filter_query(filters, tenant_id, node_type, skip, limit)
        took = (time.monotonic() - t0) * 1000
        return SearchResult(hits=hits, total=len(hits), took_ms=round(took, 2), query="<filter>", strategy="filter")

    async def autocomplete(
        self,
        prefix: str,
        tenant_id: str,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        if len(prefix) < 2:
            return []
        async with self._driver.session() as session:
            result = await session.run(_AUTOCOMPLETE, prefix=prefix, tenant_id=tenant_id, limit=limit)
            rows   = await result.data()
        return [{"node_id": r["node_id"], "node_type": r["node_type"], "name": r["name"]} for r in rows]

    async def find_similar_nodes(
        self,
        node_id: str,
        tenant_id: str,
        limit: int = 10,
    ) -> list[SearchHit]:
        """Find similar nodes by fetching embedding and running ANN."""
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n {node_id: $node_id}) RETURN n.embedding AS emb, labels(n)[0] AS lbl",
                node_id=node_id,
            )
            row = await result.single()
        if not row or not row["emb"]:
            return []
        node_type = row["lbl"]
        index = _VECTOR_INDEX.get(node_type)
        if not index:
            return []
        hits = await self._vector_by_embedding(row["emb"], tenant_id, [node_type], limit + 1)
        return [h for h in hits if h.node_id != node_id][:limit]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fulltext(
        self,
        query: str,
        tenant_id: str,
        node_types: list[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        escaped = self._escape_lucene(query)
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    _FULLTEXT_GLOBAL,
                    query=escaped,
                    tenant_id=tenant_id,
                    node_types=node_types,
                    limit=limit,
                )
                rows = await result.data()
            return [
                SearchHit(
                    node_id=r["node_id"],
                    node_type=r["node_type"],
                    name=r["name"],
                    score=float(r["score"]),
                    explain={"fulltext_score": float(r["score"])},
                    props=dict(r.get("props", {})),
                )
                for r in rows
            ]
        except Exception as exc:
            log.warning("Fulltext search error: %s", exc)
            return []

    async def _vector(
        self,
        query: str,
        tenant_id: str,
        node_types: list[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        try:
            embedding = await self._encoder.encode_one(query)
        except Exception:
            return []
        return await self._vector_by_embedding(embedding, tenant_id, node_types, limit)

    async def _vector_by_embedding(
        self,
        embedding: list[float],
        tenant_id: str,
        node_types: list[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        targets = node_types or [NodeType.MATERIAL.value, NodeType.PRODUCT.value, NodeType.SUPPLIER.value]
        tasks = []
        for nt in targets:
            index = _VECTOR_INDEX.get(nt)
            if index:
                tasks.append(self._vector_one_index(index, embedding, tenant_id, node_types, limit))
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_hits: list[SearchHit] = []
        for r in results:
            if isinstance(r, list):
                all_hits.extend(r)
        # deduplicate and re-rank by score
        seen: set[str] = set()
        deduped: list[SearchHit] = []
        for h in sorted(all_hits, key=lambda x: -x.score):
            if h.node_id not in seen:
                seen.add(h.node_id)
                deduped.append(h)
        return deduped[:limit]

    async def _vector_one_index(
        self,
        index_name: str,
        embedding: list[float],
        tenant_id: str,
        node_types: list[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    _VECTOR_SEARCH,
                    index_name=index_name,
                    limit=limit,
                    embedding=embedding,
                    tenant_id=tenant_id,
                    node_types=node_types,
                )
                rows = await result.data()
            return [
                SearchHit(
                    node_id=r["node_id"],
                    node_type=r["node_type"],
                    name=r["name"],
                    score=float(r["score"]),
                    explain={"vector_score": float(r["score"]), "index": index_name},
                    props=dict(r.get("props", {})),
                )
                for r in rows
            ]
        except Exception as exc:
            log.warning("Vector search error on %s: %s", index_name, exc)
            return []

    async def _filter_query(
        self,
        filters: dict[str, Any],
        tenant_id: str,
        node_type: str,
        skip: int,
        limit: int,
    ) -> list[SearchHit]:
        if node_type == "Supplier":
            query = _FILTER_SUPPLIERS
            params = {
                "tenant_id": tenant_id,
                "country":   filters.get("country"),
                "approved":  filters.get("approved"),
                "preferred": filters.get("preferred"),
                "iso_9001":  filters.get("iso_9001"),
                "risk_max":  filters.get("risk_max"),
                "skip":      skip,
                "limit":     limit,
            }
        else:
            query = _FILTER_MATERIALS
            params = {
                "tenant_id":      tenant_id,
                "material_class": filters.get("material_class"),
                "grade":          filters.get("grade"),
                "is_critical":    filters.get("is_critical"),
                "is_hazmat":      filters.get("is_hazmat"),
                "reach_compliant": filters.get("reach_compliant"),
                "skip":           skip,
                "limit":          limit,
            }
        try:
            async with self._driver.session() as session:
                result = await session.run(query, **params)
                rows   = await result.data()
            return [
                SearchHit(node_id=r["node_id"], node_type=r["node_type"], name=r["name"],
                          score=float(r["score"]), props=dict(r.get("props", {})))
                for r in rows
            ]
        except Exception as exc:
            log.warning("Filter query error: %s", exc)
            return []

    @staticmethod
    def _apply_filters(hits: list[SearchHit], filters: dict[str, Any]) -> list[SearchHit]:
        """Post-filter hits by property values."""
        result = []
        for hit in hits:
            match = True
            for key, value in filters.items():
                if hit.props.get(key) != value:
                    match = False
                    break
            if match:
                result.append(hit)
        return result

    @staticmethod
    def _escape_lucene(text: str) -> str:
        """Escape Lucene special characters for Neo4j fulltext queries."""
        special = r'+-&|!(){}[]^"~*?:\/'
        escaped = ""
        for ch in text:
            if ch in special:
                escaped += "\\" + ch
            else:
                escaped += ch
        return escaped


# ─────────────────────────────────────────────────────────────────────────────
# GraphContextRanker — boost score by connectivity
# ─────────────────────────────────────────────────────────────────────────────

class GraphContextRanker:
    """
    Re-rank search hits by graph centrality / connectivity.
    Adds a small bonus for well-connected nodes.
    """

    def __init__(self, driver: Any, boost_weight: float = 0.10) -> None:
        self._driver = driver
        self._boost  = boost_weight

    async def rerank(self, hits: list[SearchHit], tenant_id: str) -> list[SearchHit]:
        if not hits:
            return hits
        node_ids = [h.node_id for h in hits]
        degrees  = await self._get_degrees(node_ids, tenant_id)
        max_deg  = max(degrees.values(), default=1) or 1

        for hit in hits:
            deg   = degrees.get(hit.node_id, 0)
            bonus = self._boost * (deg / max_deg)
            hit.explain["graph_degree"] = deg
            hit.explain["graph_bonus"]  = round(bonus, 4)
            hit.score                   = hit.score * (1 - self._boost) + bonus

        hits.sort(key=lambda h: -h.score)
        return hits

    async def _get_degrees(self, node_ids: list[str], tenant_id: str) -> dict[str, int]:
        query = """
        UNWIND $node_ids AS nid
        MATCH (n {node_id: nid})-[r]-()
        RETURN nid, count(r) AS deg
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, node_ids=node_ids)
                rows   = await result.data()
            return {r["nid"]: r["deg"] for r in rows}
        except Exception:
            return {}
