"""
Section 10 — Monitoring

Prometheus metrics for the Knowledge Graph agent:
  - Graph size (nodes, edges, by label)
  - Query latency histograms (cypher, search, recommend)
  - Embedding coverage & freshness
  - Search quality (hits, zero-result rate)
  - Cache hit rate
  - Schema health checks
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus registration (optional — graceful if not installed)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    log.info("prometheus_client not installed — metrics disabled")

    # Stubs
    class _Stub:
        def labels(self, **_): return self
        def inc(self, _=1): pass
        def set(self, _): pass
        def observe(self, _): pass
        def time(self): return _NullCtx()

    class _NullCtx:
        def __enter__(self): return self
        def __exit__(self, *_): pass

    Counter = Gauge = Histogram = lambda *a, **k: _Stub()

_NAMESPACE = "ici_kg"

# ── Node & Edge gauges ────────────────────────────────────────────────────────
GRAPH_NODES = Gauge(
    f"{_NAMESPACE}_nodes_total",
    "Total graph nodes by label",
    ["tenant_id", "label"],
) if _PROM_AVAILABLE else Gauge("", "", [])

GRAPH_EDGES = Gauge(
    f"{_NAMESPACE}_edges_total",
    "Total graph edges by relation type",
    ["tenant_id", "relation_type"],
) if _PROM_AVAILABLE else Gauge("", "", [])

# ── Query latency ─────────────────────────────────────────────────────────────
CYPHER_LATENCY = Histogram(
    f"{_NAMESPACE}_cypher_duration_seconds",
    "Cypher query latency",
    ["query_type", "tenant_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
) if _PROM_AVAILABLE else Histogram("", "", [])

SEARCH_LATENCY = Histogram(
    f"{_NAMESPACE}_search_duration_seconds",
    "Search query latency",
    ["strategy"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
) if _PROM_AVAILABLE else Histogram("", "", [])

RECOMMEND_LATENCY = Histogram(
    f"{_NAMESPACE}_recommend_duration_seconds",
    "Recommendation latency",
    ["strategy"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
) if _PROM_AVAILABLE else Histogram("", "", [])

# ── Search quality ────────────────────────────────────────────────────────────
SEARCH_REQUESTS = Counter(
    f"{_NAMESPACE}_search_requests_total",
    "Search requests",
    ["strategy", "tenant_id"],
) if _PROM_AVAILABLE else Counter("", "", [])

SEARCH_ZERO_RESULTS = Counter(
    f"{_NAMESPACE}_search_zero_results_total",
    "Searches returning no results",
    ["strategy", "tenant_id"],
) if _PROM_AVAILABLE else Counter("", "", [])

SEARCH_HITS = Histogram(
    f"{_NAMESPACE}_search_hits",
    "Number of hits per search",
    ["strategy"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100],
) if _PROM_AVAILABLE else Histogram("", "", [])

# ── Embedding coverage ────────────────────────────────────────────────────────
EMBEDDING_COVERAGE = Gauge(
    f"{_NAMESPACE}_embedding_coverage_pct",
    "Fraction of nodes with valid embeddings",
    ["tenant_id", "label"],
) if _PROM_AVAILABLE else Gauge("", "", [])

EMBEDDING_RUN_DURATION = Histogram(
    f"{_NAMESPACE}_embedding_run_seconds",
    "Embedding pipeline run duration",
    ["tenant_id"],
    buckets=[1, 5, 15, 30, 60, 120, 300],
) if _PROM_AVAILABLE else Histogram("", "", [])

EMBEDDING_NODES_PROCESSED = Counter(
    f"{_NAMESPACE}_embedding_nodes_processed_total",
    "Nodes successfully embedded",
    ["tenant_id"],
) if _PROM_AVAILABLE else Counter("", "", [])

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_HITS = Counter(
    f"{_NAMESPACE}_cache_hits_total",
    "Graph cache hits",
    ["tenant_id"],
) if _PROM_AVAILABLE else Counter("", "", [])

CACHE_MISSES = Counter(
    f"{_NAMESPACE}_cache_misses_total",
    "Graph cache misses",
    ["tenant_id"],
) if _PROM_AVAILABLE else Counter("", "", [])

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA_CONSTRAINT_COUNT = Gauge(
    f"{_NAMESPACE}_schema_constraints",
    "Number of active Neo4j constraints",
) if _PROM_AVAILABLE else Gauge("", "")

SCHEMA_INDEX_COUNT = Gauge(
    f"{_NAMESPACE}_schema_indexes",
    "Number of active Neo4j indexes",
) if _PROM_AVAILABLE else Gauge("", "")

# ─────────────────────────────────────────────────────────────────────────────
# Metric helper functions
# ─────────────────────────────────────────────────────────────────────────────

def record_search(
    strategy:  str,
    tenant_id: str,
    hit_count: int,
    took_s:    float,
) -> None:
    SEARCH_REQUESTS.labels(strategy=strategy, tenant_id=tenant_id).inc()
    SEARCH_LATENCY.labels(strategy=strategy).observe(took_s)
    SEARCH_HITS.labels(strategy=strategy).observe(hit_count)
    if hit_count == 0:
        SEARCH_ZERO_RESULTS.labels(strategy=strategy, tenant_id=tenant_id).inc()


def record_cypher(query_type: str, tenant_id: str, took_s: float) -> None:
    CYPHER_LATENCY.labels(query_type=query_type, tenant_id=tenant_id).observe(took_s)


def record_recommendation(strategy: str, took_s: float) -> None:
    RECOMMEND_LATENCY.labels(strategy=strategy).observe(took_s)


def record_embedding_run(tenant_id: str, processed: int, duration_s: float) -> None:
    EMBEDDING_RUN_DURATION.labels(tenant_id=tenant_id).observe(duration_s)
    EMBEDDING_NODES_PROCESSED.labels(tenant_id=tenant_id).inc(processed)


def record_cache_hit(tenant_id: str) -> None:
    CACHE_HITS.labels(tenant_id=tenant_id).inc()


def record_cache_miss(tenant_id: str) -> None:
    CACHE_MISSES.labels(tenant_id=tenant_id).inc()


# ─────────────────────────────────────────────────────────────────────────────
# Cypher queries for metric collection
# ─────────────────────────────────────────────────────────────────────────────

_NODE_COUNTS = """
CALL apoc.meta.stats()
YIELD labels
RETURN labels
"""

_EDGE_COUNTS = """
CALL apoc.meta.stats()
YIELD relTypesCount
RETURN relTypesCount
"""

_NODE_COUNTS_SIMPLE = """
MATCH (n)
WHERE n.tenant_id = $tenant_id
RETURN labels(n)[0] AS lbl, count(n) AS cnt
"""

_EDGE_COUNTS_SIMPLE = """
MATCH ()-[r]->()
RETURN type(r) AS rel_type, count(r) AS cnt
"""

_EMBEDDING_COVERAGE = """
MATCH (n)
WHERE n.tenant_id = $tenant_id
  AND $label IN labels(n)
WITH count(n) AS total,
     count(n.embedding) AS with_emb
RETURN
  CASE WHEN total = 0 THEN 0.0
       ELSE toFloat(with_emb) / toFloat(total) * 100.0
  END AS coverage_pct
"""

_SCHEMA_COUNTS = """
SHOW CONSTRAINTS YIELD name RETURN count(*) AS cnt
"""
_INDEX_COUNTS = """
SHOW INDEXES YIELD name RETURN count(*) AS cnt
"""

# ─────────────────────────────────────────────────────────────────────────────
# MetricsCollector — periodic scrape job
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GraphHealthSnapshot:
    tenant_id:          str
    node_counts:        dict[str, int] = field(default_factory=dict)
    edge_counts:        dict[str, int] = field(default_factory=dict)
    embedding_coverage: dict[str, float] = field(default_factory=dict)
    constraint_count:   int = 0
    index_count:        int = 0
    collected_at:       float = field(default_factory=time.time)


class MetricsCollector:
    """
    Periodic Prometheus metric collector.
    Call `start()` in app lifespan to run every `interval_s` seconds.
    """

    def __init__(
        self,
        driver:      Any,
        tenant_ids:  list[str],
        interval_s:  int = 60,
    ) -> None:
        self._driver     = driver
        self._tenant_ids = tenant_ids
        self._interval   = interval_s
        self._task:      asyncio.Task | None = None
        self._labels     = ["Material", "Supplier", "Product", "Process", "Machine", "Standard", "Offer"]

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("MetricsCollector started (interval=%ds)", self._interval)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self._collect_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Metrics collection error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _collect_all(self) -> None:
        tasks = [self._collect_tenant(tid) for tid in self._tenant_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._collect_schema()

    async def _collect_tenant(self, tenant_id: str) -> GraphHealthSnapshot:
        snap = GraphHealthSnapshot(tenant_id=tenant_id)

        async with self._driver.session() as session:
            # node counts
            result = await session.run(_NODE_COUNTS_SIMPLE, tenant_id=tenant_id)
            rows   = await result.data()
            for row in rows:
                lbl = row["lbl"] or "unknown"
                cnt = row["cnt"]
                snap.node_counts[lbl] = cnt
                GRAPH_NODES.labels(tenant_id=tenant_id, label=lbl).set(cnt)

            # edge counts (global, not filtered by tenant)
            result = await session.run(_EDGE_COUNTS_SIMPLE)
            rows   = await result.data()
            for row in rows:
                snap.edge_counts[row["rel_type"]] = row["cnt"]
                GRAPH_EDGES.labels(tenant_id=tenant_id, relation_type=row["rel_type"]).set(row["cnt"])

            # embedding coverage per label
            for lbl in self._labels:
                result = await session.run(_EMBEDDING_COVERAGE, tenant_id=tenant_id, label=lbl)
                row    = await result.single()
                if row:
                    cov = float(row["coverage_pct"])
                    snap.embedding_coverage[lbl] = cov
                    EMBEDDING_COVERAGE.labels(tenant_id=tenant_id, label=lbl).set(cov)

        return snap

    async def _collect_schema(self) -> None:
        try:
            async with self._driver.session() as session:
                r1 = await (await session.run(_SCHEMA_COUNTS)).single()
                r2 = await (await session.run(_INDEX_COUNTS)).single()
                if r1:
                    SCHEMA_CONSTRAINT_COUNT.set(r1["cnt"])
                if r2:
                    SCHEMA_INDEX_COUNT.set(r2["cnt"])
        except Exception:
            pass

    async def snapshot(self, tenant_id: str) -> GraphHealthSnapshot:
        return await self._collect_tenant(tenant_id)


# ─────────────────────────────────────────────────────────────────────────────
# Health check endpoint helpers
# ─────────────────────────────────────────────────────────────────────────────

async def neo4j_health_check(driver: Any) -> dict[str, Any]:
    """Returns Neo4j connectivity status + basic stats."""
    try:
        t0 = time.monotonic()
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS ok")
            await result.single()
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def prometheus_metrics_response() -> tuple[bytes, str]:
    """For use in a /metrics endpoint."""
    if not _PROM_AVAILABLE:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
