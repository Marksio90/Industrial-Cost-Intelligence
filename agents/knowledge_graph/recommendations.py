"""
Section 8 — Recommendation Engine

Three strategies combined via ensemble:
  1. Graph traversal   — SIMILAR_TO / BOM / co-occurrence paths
  2. Embedding cosine  — ANN vector search (Neo4j HNSW)
  3. Collaborative     — "users who used X also used Y" (co_occurrence counts)

Entry point:
  RecommendationEngine.recommend(node_id, tenant_id, strategy, limit)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .models import RelationType, NodeType
from .embeddings import cosine_similarity

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Recommendation:
    node_id:    str
    node_type:  str
    name:       str
    score:      float
    reason:     str
    path:       list[str] = field(default_factory=list)
    props:      dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Cypher queries
# ─────────────────────────────────────────────────────────────────────────────

_GRAPH_SUBSTITUTES = """
MATCH (seed {node_id: $node_id})-[r1:SIMILAR_TO]-(candidate)
WHERE candidate.tenant_id = $tenant_id
  AND candidate.node_id <> $node_id
RETURN candidate.node_id AS node_id, labels(candidate)[0] AS node_type,
       candidate.name AS name, r1.weight AS score,
       ['SIMILAR_TO'] AS path, properties(candidate) AS props
ORDER BY score DESC LIMIT $limit
"""

_GRAPH_CO_USED = """
MATCH (seed {node_id: $node_id})-[:USED_IN]->(prod)<-[:USED_IN]-(candidate)
WHERE candidate.tenant_id = $tenant_id
  AND candidate.node_id <> $node_id
  AND labels(candidate) = labels(seed)
WITH candidate, count(prod) AS co_count
RETURN candidate.node_id AS node_id, labels(candidate)[0] AS node_type,
       candidate.name AS name,
       toFloat(co_count) / (1.0 + toFloat(co_count)) AS score,
       ['USED_IN','<product>','USED_IN'] AS path,
       properties(candidate) AS props
ORDER BY co_count DESC LIMIT $limit
"""

_GRAPH_SUPPLIER_RECS = """
MATCH (seed {node_id: $node_id})<-[:SUPPLIED_BY]-(sup:Supplier)
MATCH (sup)-[:SUPPLIED_BY]->(candidate)
WHERE candidate.tenant_id = $tenant_id
  AND candidate.node_id <> $node_id
  AND labels(candidate) = labels(seed)
WITH candidate, count(sup) AS shared_suppliers
RETURN candidate.node_id AS node_id, labels(candidate)[0] AS node_type,
       candidate.name AS name,
       toFloat(shared_suppliers) / 5.0 AS score,
       ['SUPPLIED_BY','<supplier>','SUPPLIED_BY'] AS path,
       properties(candidate) AS props
ORDER BY shared_suppliers DESC LIMIT $limit
"""

_GRAPH_STANDARD_RECS = """
MATCH (seed {node_id: $node_id})-[:CONFORMS_TO]->(std:Standard)<-[:CONFORMS_TO]-(candidate)
WHERE candidate.tenant_id = $tenant_id
  AND candidate.node_id <> $node_id
  AND labels(candidate) = labels(seed)
WITH candidate, count(std) AS shared_standards
RETURN candidate.node_id AS node_id, labels(candidate)[0] AS node_type,
       candidate.name AS name,
       toFloat(shared_standards) / 3.0 AS score,
       ['CONFORMS_TO','<standard>','CONFORMS_TO'] AS path,
       properties(candidate) AS props
ORDER BY shared_standards DESC LIMIT $limit
"""

_VECTOR_REC = """
MATCH (seed {node_id: $node_id})
WITH seed, seed.embedding AS emb, labels(seed)[0] AS lbl
CALL db.index.vector.queryNodes($index_name, $limit + 1, emb)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND node.node_id <> $node_id
RETURN node.node_id AS node_id, labels(node)[0] AS node_type,
       node.name AS name, score, [] AS path, properties(node) AS props
ORDER BY score DESC LIMIT $limit
"""

_COLLAB_FILTER = """
MATCH (seed {node_id: $node_id})<-[:USED_IN]-(prod:Product)-[:USED_IN]->(peer)
WHERE peer.node_id <> $node_id AND peer.tenant_id = $tenant_id
WITH peer, count(prod) AS weight
MATCH (peer)-[:USED_IN]->(prod2:Product)-[:USED_IN]->(candidate)
WHERE candidate.node_id <> $node_id
  AND candidate.tenant_id = $tenant_id
  AND labels(candidate) = labels(seed)
WITH candidate, sum(weight) AS collab_score
RETURN candidate.node_id AS node_id, labels(candidate)[0] AS node_type,
       candidate.name AS name,
       toFloat(collab_score) / 10.0 AS score,
       ['<collab>'] AS path,
       properties(candidate) AS props
ORDER BY collab_score DESC LIMIT $limit
"""

_FETCH_NODE_META = """
MATCH (n {node_id: $node_id})
RETURN labels(n)[0] AS node_type, n.name AS name, n.embedding AS embedding
"""

_VECTOR_INDEX_MAP: dict[str, str] = {
    "Material": "material_embedding_idx",
    "Product":  "product_embedding_idx",
    "Supplier": "supplier_embedding_idx",
}


# ─────────────────────────────────────────────────────────────────────────────
# RecommendationEngine
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationEngine:
    """
    Ensemble recommendation engine for knowledge graph nodes.

    Strategies
    ----------
    graph      — purely structural (SIMILAR_TO, co-usage, shared standards)
    embedding  — vector ANN only
    collab     — collaborative filtering via co-occurrence
    hybrid     — weighted blend of graph + embedding + collab
    """

    # Ensemble weights (must sum to 1.0)
    _WEIGHTS = {"graph": 0.40, "embedding": 0.35, "collab": 0.25}

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    # ── Public ────────────────────────────────────────────────────────────────

    async def recommend(
        self,
        node_id:   str,
        tenant_id: str,
        strategy:  str = "hybrid",
        limit:     int = 10,
        filters:   dict[str, Any] | None = None,
    ) -> list[Recommendation]:
        meta = await self._fetch_meta(node_id)
        if not meta:
            return []

        node_type = meta["node_type"]
        index     = _VECTOR_INDEX_MAP.get(node_type)

        if strategy == "graph":
            recs = await self._graph_recs(node_id, tenant_id, node_type, limit * 2)
        elif strategy == "embedding":
            recs = await self._vector_recs(node_id, tenant_id, index, limit * 2) if index else []
        elif strategy == "collab":
            recs = await self._collab_recs(node_id, tenant_id, limit * 2)
        else:  # hybrid
            recs = await self._hybrid_recs(node_id, tenant_id, node_type, index, limit * 2)

        if filters:
            recs = [r for r in recs if self._match_filters(r, filters)]

        return recs[:limit]

    async def explain(self, node_id: str, candidate_id: str, tenant_id: str) -> dict[str, Any]:
        """Explain why candidate was recommended for node_id."""
        query = """
        MATCH p = shortestPath((a {node_id: $src})-[*1..5]-(b {node_id: $tgt}))
        WHERE all(n IN nodes(p) WHERE n.tenant_id = $tenant_id OR n.tenant_id IS NULL)
        RETURN [n IN nodes(p) | n.name] AS names,
               [r IN relationships(p) | type(r)] AS rels,
               length(p) AS hops
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, src=node_id, tgt=candidate_id, tenant_id=tenant_id)
                row = await result.single()
            if row:
                return {"path_names": row["names"], "relations": row["rels"], "hops": row["hops"]}
        except Exception as exc:
            log.warning("Explain query failed: %s", exc)
        return {"path_names": [], "relations": [], "hops": -1}

    # ── Strategy implementations ──────────────────────────────────────────────

    async def _graph_recs(
        self, node_id: str, tenant_id: str, node_type: str, limit: int
    ) -> list[Recommendation]:
        import asyncio
        tasks = [
            self._run_query(_GRAPH_SUBSTITUTES, {"node_id": node_id, "tenant_id": tenant_id, "limit": limit}, "material_substitute"),
            self._run_query(_GRAPH_CO_USED,     {"node_id": node_id, "tenant_id": tenant_id, "limit": limit}, "co_used_in_product"),
            self._run_query(_GRAPH_SUPPLIER_RECS, {"node_id": node_id, "tenant_id": tenant_id, "limit": limit}, "shared_supplier"),
            self._run_query(_GRAPH_STANDARD_RECS, {"node_id": node_id, "tenant_id": tenant_id, "limit": limit}, "shared_standard"),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_recs: list[Recommendation] = []
        for r in results:
            if isinstance(r, list):
                all_recs.extend(r)
        return self._deduplicate_merge(all_recs, limit)

    async def _vector_recs(
        self, node_id: str, tenant_id: str, index: str | None, limit: int
    ) -> list[Recommendation]:
        if not index:
            return []
        return await self._run_query(
            _VECTOR_REC,
            {"node_id": node_id, "tenant_id": tenant_id, "index_name": index, "limit": limit},
            "vector_similarity",
        )

    async def _collab_recs(
        self, node_id: str, tenant_id: str, limit: int
    ) -> list[Recommendation]:
        return await self._run_query(
            _COLLAB_FILTER,
            {"node_id": node_id, "tenant_id": tenant_id, "limit": limit},
            "collaborative_filter",
        )

    async def _hybrid_recs(
        self,
        node_id:   str,
        tenant_id: str,
        node_type: str,
        index:     str | None,
        limit:     int,
    ) -> list[Recommendation]:
        import asyncio
        graph_task   = asyncio.create_task(self._graph_recs(node_id, tenant_id, node_type, limit))
        vector_task  = asyncio.create_task(self._vector_recs(node_id, tenant_id, index, limit))
        collab_task  = asyncio.create_task(self._collab_recs(node_id, tenant_id, limit))
        g, v, c = await asyncio.gather(graph_task, vector_task, collab_task, return_exceptions=True)

        # Build score map per strategy
        combined: dict[str, dict[str, Any]] = {}

        def _add(recs: list[Recommendation] | Exception, strategy_key: str) -> None:
            if isinstance(recs, Exception):
                return
            for rank, rec in enumerate(recs, start=1):
                if rec.node_id not in combined:
                    combined[rec.node_id] = {"rec": rec, "score": 0.0}
                w = self._WEIGHTS[strategy_key]
                combined[rec.node_id]["score"] += w / (60 + rank)  # RRF

        _add(g, "graph")
        _add(v, "embedding")
        _add(c, "collab")

        merged = sorted(combined.values(), key=lambda x: -x["score"])
        result = []
        for entry in merged[:limit]:
            rec = entry["rec"]
            rec.score = round(entry["score"] * 1000, 4)  # scale for readability
            result.append(rec)
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_query(
        self,
        query:  str,
        params: dict[str, Any],
        reason: str,
    ) -> list[Recommendation]:
        try:
            async with self._driver.session() as session:
                result = await session.run(query, **params)
                rows   = await result.data()
            return [
                Recommendation(
                    node_id=r["node_id"],
                    node_type=r["node_type"],
                    name=r["name"],
                    score=min(float(r.get("score", 0.0)), 1.0),
                    reason=reason,
                    path=list(r.get("path", [])),
                    props=dict(r.get("props", {})),
                )
                for r in rows
            ]
        except Exception as exc:
            log.warning("Recommendation query '%s' failed: %s", reason, exc)
            return []

    async def _fetch_meta(self, node_id: str) -> dict[str, Any] | None:
        try:
            async with self._driver.session() as session:
                result = await session.run(_FETCH_NODE_META, node_id=node_id)
                row    = await result.single()
            return dict(row) if row else None
        except Exception:
            return None

    @staticmethod
    def _deduplicate_merge(recs: list[Recommendation], limit: int) -> list[Recommendation]:
        seen:  dict[str, Recommendation] = {}
        for rec in recs:
            if rec.node_id not in seen or rec.score > seen[rec.node_id].score:
                seen[rec.node_id] = rec
        return sorted(seen.values(), key=lambda r: -r.score)[:limit]

    @staticmethod
    def _match_filters(rec: Recommendation, filters: dict[str, Any]) -> bool:
        for k, v in filters.items():
            if rec.props.get(k) != v:
                return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Similarity graph builder — batch compute SIMILAR_TO edges
# ─────────────────────────────────────────────────────────────────────────────

_UPSERT_SIMILAR = """
UNWIND $rows AS row
MATCH (a {node_id: row.source_id}), (b {node_id: row.target_id})
MERGE (a)-[r:SIMILAR_TO]-(b)
SET r.weight      = row.weight,
    r.method      = row.method,
    r.grade_compat = row.grade_compat,
    r.updated_at  = datetime()
"""

_FETCH_MATERIAL_EMBEDDINGS = """
MATCH (n:Material)
WHERE n.tenant_id = $tenant_id
  AND n.embedding IS NOT NULL
RETURN n.node_id AS node_id, n.embedding AS embedding,
       n.material_class AS material_class, n.grade AS grade
LIMIT $limit
"""


async def build_similarity_graph(
    driver:    Any,
    tenant_id: str,
    threshold: float = 0.75,
    limit:     int   = 5_000,
) -> int:
    """
    Compute pairwise cosine similarity for all Material nodes with embeddings
    and upsert SIMILAR_TO edges above threshold.
    Returns number of edges created/updated.
    """
    async with driver.session() as session:
        result = await session.run(
            _FETCH_MATERIAL_EMBEDDINGS, tenant_id=tenant_id, limit=limit
        )
        rows = await result.data()

    if len(rows) < 2:
        return 0

    edges: list[dict[str, Any]] = []
    n = len(rows)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rows[i], rows[j]
            if a["material_class"] != b["material_class"]:
                continue
            sim = cosine_similarity(a["embedding"], b["embedding"])
            if sim >= threshold:
                edges.append({
                    "source_id":    a["node_id"],
                    "target_id":    b["node_id"],
                    "weight":       round(sim, 4),
                    "method":       "embedding_cosine",
                    "grade_compat": a.get("grade") == b.get("grade"),
                })

    if not edges:
        return 0

    batch_size = 200
    total = 0
    async with driver.session() as session:
        for i in range(0, len(edges), batch_size):
            chunk = edges[i : i + batch_size]
            await session.run(_UPSERT_SIMILAR, rows=chunk)
            total += len(chunk)
    return total
