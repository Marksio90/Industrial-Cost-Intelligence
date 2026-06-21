"""
Section 9 — Knowledge Graph API

FastAPI router exposing:
  POST   /graph/nodes
  GET    /graph/nodes/{node_id}
  PUT    /graph/nodes/{node_id}
  DELETE /graph/nodes/{node_id}
  POST   /graph/edges
  DELETE /graph/edges/{source_id}/{relation_type}/{target_id}

  GET    /graph/search
  GET    /graph/autocomplete
  GET    /graph/similar/{node_id}

  GET    /graph/path
  GET    /graph/bom/{product_id}
  GET    /graph/routing/{material_id}

  GET    /graph/recommend/{node_id}
  GET    /graph/recommend/{node_id}/explain/{candidate_id}

  GET    /graph/taxonomy
  GET    /graph/taxonomy/{code}

  GET    /graph/analytics/pagerank
  GET    /graph/analytics/spof
  GET    /graph/analytics/risk
  GET    /graph/analytics/stats

  POST   /graph/embeddings/run
  GET    /graph/schema
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from .models import (
    NodeCreate, EdgeCreate, GraphSearchRequest,
    PathRequest, RecommendRequest,
    NodeOut, PathOut, RecommendationOut,
    NodeType, RelationType,
)
from .cypher_queries import (
    CRUDQueries, TraversalQueries, AnalysisQueries,
    SearchQueries, RiskQueries, ReportingQueries,
    CypherRunner,
)
from .taxonomy import MATERIAL_TAXONOMY, PROCESS_TAXONOMY, taxonomy_to_dict
from .embeddings import EmbeddingPipeline, EmbeddingEncoder, EmbeddingStats
from .search import SearchEngine, SearchResult
from .recommendations import RecommendationEngine, Recommendation
from .neo4j_schema import schema_summary, apply_schema

log = logging.getLogger(__name__)

router = APIRouter(prefix="/graph", tags=["knowledge-graph"])

# ─────────────────────────────────────────────────────────────────────────────
# Dependency injection stubs
# (In production, replace with FastAPI lifespan / DI container)
# ─────────────────────────────────────────────────────────────────────────────

def _get_driver() -> Any:
    raise NotImplementedError("Inject Neo4j driver via app state or DI")

def _get_runner(driver: Any = Depends(_get_driver)) -> CypherRunner:
    return CypherRunner(driver)

def _get_search(driver: Any = Depends(_get_driver)) -> SearchEngine:
    return SearchEngine(driver)

def _get_recommend(driver: Any = Depends(_get_driver)) -> RecommendationEngine:
    return RecommendationEngine(driver)

def _get_pipeline(driver: Any = Depends(_get_driver)) -> EmbeddingPipeline:
    return EmbeddingPipeline(driver)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic response/request models
# ─────────────────────────────────────────────────────────────────────────────

class NodeResponse(BaseModel):
    node_id:   str
    node_type: str
    name:      str
    props:     dict[str, Any] = Field(default_factory=dict)


class EdgeResponse(BaseModel):
    source_id:     str
    target_id:     str
    relation_type: str
    weight:        float = 1.0
    props:         dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    hits:     list[dict[str, Any]]
    total:    int
    took_ms:  float
    strategy: str


class BomRow(BaseModel):
    level:       int
    node_id:     str
    name:        str
    quantity:    float | None = None
    unit:        str | None   = None
    scrap_pct:   float | None = None


class EmbeddingRunRequest(BaseModel):
    tenant_id:   str
    node_type:   str | None = None
    incremental: bool       = True
    since_hours: int        = 24


class EmbeddingRunResponse(BaseModel):
    processed:     int
    skipped:       int
    errors:        int
    duration_s:    float
    nodes_per_sec: float


class AnalyticsResponse(BaseModel):
    data:    list[dict[str, Any]]
    took_ms: float


# ─────────────────────────────────────────────────────────────────────────────
# Node CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/nodes", response_model=NodeResponse, status_code=201)
async def create_node(
    body:    NodeCreate,
    runner:  CypherRunner   = Depends(_get_runner),
    pipeline: EmbeddingPipeline = Depends(_get_pipeline),
    background: BackgroundTasks = BackgroundTasks(),
) -> NodeResponse:
    node_type = body.node_type
    props     = {**body.properties, "name": body.name, "tenant_id": body.tenant_id}

    query_map: dict[str, str] = {
        "Material": CRUDQueries.UPSERT_MATERIAL,
        "Supplier": CRUDQueries.UPSERT_SUPPLIER,
        "Product":  CRUDQueries.UPSERT_PRODUCT,
        "Process":  CRUDQueries.UPSERT_PROCESS,
    }
    cypher = query_map.get(node_type)
    if not cypher:
        raise HTTPException(400, f"Unsupported node type: {node_type}")

    rows = await runner.run_write(cypher, {"props": props})
    if not rows:
        raise HTTPException(500, "Node creation returned no result")

    node_id = rows[0].get("node_id")

    # async embedding in background
    async def _embed() -> None:
        try:
            vec = await pipeline.encode_node(node_type, props)
            await runner.run_write(
                CRUDQueries.SET_EMBEDDING,
                {"node_id": node_id, "embedding": vec},
            )
        except Exception as exc:
            log.warning("Background embedding failed: %s", exc)

    background.add_task(_embed)
    return NodeResponse(node_id=node_id, node_type=node_type, name=body.name, props=props)


@router.get("/nodes/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str, runner: CypherRunner = Depends(_get_runner)) -> NodeResponse:
    rows = await runner.run(
        "MATCH (n {node_id: $node_id}) RETURN n.node_id AS node_id, labels(n)[0] AS node_type, n.name AS name, properties(n) AS props",
        {"node_id": node_id},
    )
    if not rows:
        raise HTTPException(404, f"Node {node_id!r} not found")
    r = rows[0]
    return NodeResponse(node_id=r["node_id"], node_type=r["node_type"], name=r["name"], props=dict(r.get("props", {})))


@router.put("/nodes/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: str,
    body:    dict[str, Any],
    runner:  CypherRunner = Depends(_get_runner),
) -> NodeResponse:
    rows = await runner.run_write(
        "MATCH (n {node_id: $node_id}) SET n += $props RETURN n.node_id AS node_id, labels(n)[0] AS node_type, n.name AS name, properties(n) AS props",
        {"node_id": node_id, "props": body},
    )
    if not rows:
        raise HTTPException(404, f"Node {node_id!r} not found")
    r = rows[0]
    return NodeResponse(node_id=r["node_id"], node_type=r["node_type"], name=r["name"], props=dict(r.get("props", {})))


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(node_id: str, runner: CypherRunner = Depends(_get_runner)) -> None:
    await runner.run_write(CRUDQueries.DELETE_NODE, {"node_id": node_id})


# ─────────────────────────────────────────────────────────────────────────────
# Edge CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/edges", response_model=EdgeResponse, status_code=201)
async def create_edge(
    body:   EdgeCreate,
    runner: CypherRunner = Depends(_get_runner),
) -> EdgeResponse:
    rows = await runner.run_write(
        CRUDQueries.MERGE_RELATION,
        {
            "source_id":     body.source_id,
            "target_id":     body.target_id,
            "relation_type": body.relation_type,
            "weight":        body.weight,
            "props":         body.properties,
        },
    )
    return EdgeResponse(
        source_id=body.source_id,
        target_id=body.target_id,
        relation_type=body.relation_type,
        weight=body.weight,
        props=body.properties,
    )


@router.delete("/edges/{source_id}/{relation_type}/{target_id}", status_code=204)
async def delete_edge(
    source_id: str, relation_type: str, target_id: str,
    runner: CypherRunner = Depends(_get_runner),
) -> None:
    await runner.run_write(
        f"MATCH (a {{node_id: $src}})-[r:{relation_type}]->(b {{node_id: $tgt}}) DELETE r",
        {"src": source_id, "tgt": target_id},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=SearchResponse)
async def search_graph(
    q:          str            = Query(..., min_length=2, description="Search query"),
    tenant_id:  str            = Query(...),
    node_types: list[str]      = Query(default=[]),
    limit:      int            = Query(default=20, ge=1, le=100),
    strategy:   str            = Query(default="hybrid"),
    engine:     SearchEngine   = Depends(_get_search),
) -> SearchResponse:
    result = await engine.search(
        query=q,
        tenant_id=tenant_id,
        node_types=node_types or None,
        limit=limit,
        strategy=strategy,
    )
    return SearchResponse(
        hits=[{"node_id": h.node_id, "node_type": h.node_type, "name": h.name, "score": h.score, "explain": h.explain} for h in result.hits],
        total=result.total,
        took_ms=result.took_ms,
        strategy=result.strategy,
    )


@router.get("/autocomplete")
async def autocomplete(
    prefix:    str          = Query(..., min_length=2),
    tenant_id: str          = Query(...),
    limit:     int          = Query(default=10),
    engine:    SearchEngine = Depends(_get_search),
) -> list[dict[str, str]]:
    return await engine.autocomplete(prefix, tenant_id, limit)


@router.get("/similar/{node_id}")
async def similar_nodes(
    node_id:   str,
    tenant_id: str        = Query(...),
    limit:     int        = Query(default=10),
    engine:    SearchEngine = Depends(_get_search),
) -> list[dict[str, Any]]:
    hits = await engine.find_similar_nodes(node_id, tenant_id, limit)
    return [{"node_id": h.node_id, "node_type": h.node_type, "name": h.name, "score": h.score} for h in hits]


# ─────────────────────────────────────────────────────────────────────────────
# Graph traversal
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/path")
async def shortest_path(
    from_id:   str          = Query(...),
    to_id:     str          = Query(...),
    tenant_id: str          = Query(...),
    max_depth: int          = Query(default=5, ge=1, le=10),
    runner:    CypherRunner = Depends(_get_runner),
) -> dict[str, Any]:
    rows = await runner.run(
        TraversalQueries.SHORTEST_PATH,
        {"from_id": from_id, "to_id": to_id, "tenant_id": tenant_id, "max_depth": max_depth},
    )
    if not rows:
        raise HTTPException(404, "No path found")
    return rows[0]


@router.get("/bom/{product_id}")
async def bom_explosion(
    product_id: str,
    tenant_id:  str         = Query(...),
    max_depth:  int         = Query(default=5, ge=1, le=8),
    runner:     CypherRunner = Depends(_get_runner),
) -> list[dict[str, Any]]:
    rows = await runner.run(
        TraversalQueries.BOM_EXPLOSION,
        {"product_id": product_id, "tenant_id": tenant_id, "max_depth": max_depth},
    )
    return rows


@router.get("/routing/{material_id}")
async def process_routing(
    material_id: str,
    tenant_id:   str         = Query(...),
    runner:      CypherRunner = Depends(_get_runner),
) -> list[dict[str, Any]]:
    return await runner.run(
        TraversalQueries.PROCESS_ROUTING,
        {"material_id": material_id, "tenant_id": tenant_id},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recommendations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/recommend/{node_id}")
async def recommend(
    node_id:   str,
    tenant_id: str                  = Query(...),
    strategy:  str                  = Query(default="hybrid"),
    limit:     int                  = Query(default=10, ge=1, le=50),
    engine:    RecommendationEngine = Depends(_get_recommend),
) -> list[dict[str, Any]]:
    recs = await engine.recommend(node_id, tenant_id, strategy, limit)
    return [
        {"node_id": r.node_id, "node_type": r.node_type, "name": r.name,
         "score": r.score, "reason": r.reason, "path": r.path}
        for r in recs
    ]


@router.get("/recommend/{node_id}/explain/{candidate_id}")
async def explain_recommendation(
    node_id:      str,
    candidate_id: str,
    tenant_id:    str                  = Query(...),
    engine:       RecommendationEngine = Depends(_get_recommend),
) -> dict[str, Any]:
    return await engine.explain(node_id, candidate_id, tenant_id)


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomy
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/taxonomy")
async def get_taxonomy() -> dict[str, Any]:
    return {
        "material": taxonomy_to_dict(MATERIAL_TAXONOMY),
        "process":  taxonomy_to_dict(PROCESS_TAXONOMY),
    }


@router.get("/taxonomy/{code}")
async def get_taxonomy_node(code: str) -> dict[str, Any]:
    from .taxonomy import get_taxonomy_path, get_siblings
    path     = get_taxonomy_path(MATERIAL_TAXONOMY, code)
    siblings = get_siblings(MATERIAL_TAXONOMY, code)
    if not path:
        raise HTTPException(404, f"Taxonomy code {code!r} not found")
    return {"code": code, "path": path, "siblings": siblings}


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analytics/stats")
async def graph_stats(
    tenant_id: str          = Query(...),
    runner:    CypherRunner = Depends(_get_runner),
) -> dict[str, Any]:
    import time
    t0   = time.monotonic()
    rows = await runner.run(ReportingQueries.GRAPH_KPI, {"tenant_id": tenant_id})
    return {"data": rows[0] if rows else {}, "took_ms": round((time.monotonic() - t0) * 1000, 2)}


@router.get("/analytics/pagerank")
async def material_pagerank(
    tenant_id: str          = Query(...),
    limit:     int          = Query(default=20),
    runner:    CypherRunner = Depends(_get_runner),
) -> AnalyticsResponse:
    import time
    t0   = time.monotonic()
    rows = await runner.run(AnalysisQueries.MATERIAL_PAGERANK, {"tenant_id": tenant_id, "limit": limit})
    return AnalyticsResponse(data=rows, took_ms=round((time.monotonic() - t0) * 1000, 2))


@router.get("/analytics/spof")
async def single_source_materials(
    tenant_id: str          = Query(...),
    runner:    CypherRunner = Depends(_get_runner),
) -> AnalyticsResponse:
    import time
    t0   = time.monotonic()
    rows = await runner.run(RiskQueries.CRITICAL_SINGLE_SOURCE, {"tenant_id": tenant_id})
    return AnalyticsResponse(data=rows, took_ms=round((time.monotonic() - t0) * 1000, 2))


@router.get("/analytics/risk")
async def high_risk_suppliers(
    tenant_id:  str          = Query(...),
    risk_max:   float        = Query(default=0.7),
    runner:     CypherRunner = Depends(_get_runner),
) -> AnalyticsResponse:
    import time
    t0   = time.monotonic()
    rows = await runner.run(RiskQueries.HIGH_RISK_SUPPLIERS, {"tenant_id": tenant_id, "risk_max": risk_max})
    return AnalyticsResponse(data=rows, took_ms=round((time.monotonic() - t0) * 1000, 2))


@router.get("/analytics/geo-concentration")
async def geo_concentration(
    tenant_id: str          = Query(...),
    runner:    CypherRunner = Depends(_get_runner),
) -> AnalyticsResponse:
    import time
    t0   = time.monotonic()
    rows = await runner.run(AnalysisQueries.GEO_CONCENTRATION, {"tenant_id": tenant_id})
    return AnalyticsResponse(data=rows, took_ms=round((time.monotonic() - t0) * 1000, 2))


@router.get("/analytics/compliance-gaps")
async def compliance_gaps(
    tenant_id: str          = Query(...),
    runner:    CypherRunner = Depends(_get_runner),
) -> AnalyticsResponse:
    import time
    t0   = time.monotonic()
    rows = await runner.run(AnalysisQueries.COMPLIANCE_GAPS, {"tenant_id": tenant_id})
    return AnalyticsResponse(data=rows, took_ms=round((time.monotonic() - t0) * 1000, 2))


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/embeddings/run", response_model=EmbeddingRunResponse)
async def run_embeddings(
    body:     EmbeddingRunRequest,
    pipeline: EmbeddingPipeline = Depends(_get_pipeline),
) -> EmbeddingRunResponse:
    if body.incremental:
        stats = await pipeline.run_incremental(
            tenant_id=body.tenant_id,
            since_hours=body.since_hours,
            node_type=body.node_type,
        )
    else:
        stats = await pipeline.run_full(
            tenant_id=body.tenant_id,
            node_type=body.node_type,
        )
    return EmbeddingRunResponse(
        processed=stats.processed,
        skipped=stats.skipped,
        errors=stats.errors,
        duration_s=stats.duration_s,
        nodes_per_sec=stats.nodes_per_sec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/schema")
async def get_schema() -> dict[str, Any]:
    return schema_summary()


@router.post("/schema/apply", status_code=202)
async def apply_db_schema(
    runner: CypherRunner = Depends(_get_runner),
    background: BackgroundTasks = BackgroundTasks(),
) -> dict[str, str]:
    background.add_task(apply_schema, runner._driver)
    return {"status": "accepted", "message": "Schema application started in background"}
