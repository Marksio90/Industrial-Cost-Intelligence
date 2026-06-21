"""
Section 3 — Model grafowy (in-memory representation)

Uzupełnienie Neo4j o lekki graf in-memory:
  - Adjacency list (dict-of-dicts)
  - NetworkX integration (opcjonalnie)
  - BFS / DFS / Dijkstra path algorithms
  - Subgraph extraction
  - Graph statistics
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Any, Generator, Iterator

from .models import NodeType, RelationType, GraphEdge, GraphNode


# ─────────────────────────────────────────────────────────────────────────────
# In-memory graph structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InMemoryNode:
    node_id:   str
    node_type: NodeType
    name:      str
    tenant_id: str
    props:     dict[str, Any] = field(default_factory=dict)


@dataclass
class InMemoryEdge:
    source_id:     str
    target_id:     str
    relation_type: RelationType
    weight:        float = 1.0
    props:         dict[str, Any] = field(default_factory=dict)


class InMemoryGraph:
    """
    Lightweight in-memory graph over a tenant's subgraph.

    Stores:
      _nodes  : node_id → InMemoryNode
      _out    : node_id → list[InMemoryEdge]   (adjacency out)
      _in     : node_id → list[InMemoryEdge]   (adjacency in)
      _by_type: NodeType → set[node_id]
    """

    def __init__(self) -> None:
        self._nodes:   dict[str, InMemoryNode]         = {}
        self._out:     dict[str, list[InMemoryEdge]]   = defaultdict(list)
        self._in:      dict[str, list[InMemoryEdge]]   = defaultdict(list)
        self._by_type: dict[NodeType, set[str]]        = defaultdict(set)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def add_node(self, node: InMemoryNode) -> None:
        self._nodes[node.node_id] = node
        self._by_type[node.node_type].add(node.node_id)

    def add_edge(self, edge: InMemoryEdge) -> None:
        self._out[edge.source_id].append(edge)
        self._in[edge.target_id].append(edge)

    def remove_node(self, node_id: str) -> None:
        node = self._nodes.pop(node_id, None)
        if node:
            self._by_type[node.node_type].discard(node_id)
        # remove edges
        for e in self._out.pop(node_id, []):
            self._in[e.target_id] = [x for x in self._in[e.target_id] if x.source_id != node_id]
        for e in self._in.pop(node_id, []):
            self._out[e.source_id] = [x for x in self._out[e.source_id] if x.target_id != node_id]

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> InMemoryNode | None:
        return self._nodes.get(node_id)

    def nodes_by_type(self, node_type: NodeType) -> list[InMemoryNode]:
        return [self._nodes[nid] for nid in self._by_type.get(node_type, set()) if nid in self._nodes]

    def out_edges(self, node_id: str, rel_type: RelationType | None = None) -> list[InMemoryEdge]:
        edges = self._out.get(node_id, [])
        if rel_type:
            edges = [e for e in edges if e.relation_type == rel_type]
        return edges

    def in_edges(self, node_id: str, rel_type: RelationType | None = None) -> list[InMemoryEdge]:
        edges = self._in.get(node_id, [])
        if rel_type:
            edges = [e for e in edges if e.relation_type == rel_type]
        return edges

    def neighbors(self, node_id: str, rel_types: list[RelationType] | None = None) -> list[str]:
        edges = self._out.get(node_id, [])
        if rel_types:
            edges = [e for e in edges if e.relation_type in rel_types]
        return [e.target_id for e in edges]

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    # ── Statistics ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        edge_count = sum(len(v) for v in self._out.values())
        type_counts = {t.value: len(ids) for t, ids in self._by_type.items() if ids}
        return {
            "nodes": len(self._nodes),
            "edges": edge_count,
            "by_type": type_counts,
            "density": edge_count / max(len(self._nodes) ** 2, 1),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Path algorithms
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PathResult:
    nodes:       list[str]
    edges:       list[InMemoryEdge]
    total_weight: float
    hops:        int


def bfs_shortest_path(
    graph: InMemoryGraph,
    source: str,
    target: str,
    rel_types: list[RelationType] | None = None,
    max_depth: int = 10,
) -> PathResult | None:
    """BFS — unweighted shortest path."""
    if source not in graph or target not in graph:
        return None
    if source == target:
        return PathResult([source], [], 0.0, 0)

    visited: set[str] = {source}
    queue: deque[tuple[str, list[str], list[InMemoryEdge]]] = deque([(source, [source], [])])

    while queue:
        current, path_nodes, path_edges = queue.popleft()
        if len(path_nodes) > max_depth + 1:
            continue
        for edge in graph.out_edges(current, None):
            if rel_types and edge.relation_type not in rel_types:
                continue
            nxt = edge.target_id
            new_nodes = path_nodes + [nxt]
            new_edges = path_edges + [edge]
            if nxt == target:
                return PathResult(
                    nodes=new_nodes,
                    edges=new_edges,
                    total_weight=sum(e.weight for e in new_edges),
                    hops=len(new_edges),
                )
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, new_nodes, new_edges))
    return None


def dijkstra_path(
    graph: InMemoryGraph,
    source: str,
    target: str,
    weight_fn: Any = None,
    rel_types: list[RelationType] | None = None,
    max_depth: int = 10,
) -> PathResult | None:
    """Dijkstra — minimum-weight path. weight_fn(edge) → float cost."""
    if weight_fn is None:
        weight_fn = lambda e: 1.0 - e.weight + 1e-9

    dist: dict[str, float] = defaultdict(lambda: float("inf"))
    dist[source] = 0.0
    prev_edge: dict[str, InMemoryEdge | None] = {source: None}
    prev_node: dict[str, str | None]          = {source: None}
    heap: list[tuple[float, str]] = [(0.0, source)]

    while heap:
        d, u = heappop(heap)
        if d > dist[u]:
            continue
        if u == target:
            # reconstruct
            path_nodes: list[str] = []
            path_edges: list[InMemoryEdge] = []
            cur = target
            while cur is not None:
                path_nodes.append(cur)
                e = prev_edge.get(cur)
                if e:
                    path_edges.append(e)
                cur = prev_node.get(cur)
            path_nodes.reverse()
            path_edges.reverse()
            return PathResult(
                nodes=path_nodes,
                edges=path_edges,
                total_weight=dist[target],
                hops=len(path_edges),
            )
        if len(path_nodes if 'path_nodes' in dir() else []) > max_depth:
            continue
        for edge in graph.out_edges(u):
            if rel_types and edge.relation_type not in rel_types:
                continue
            v   = edge.target_id
            alt = d + weight_fn(edge)
            if alt < dist[v]:
                dist[v]      = alt
                prev_edge[v] = edge
                prev_node[v] = u
                heappush(heap, (alt, v))
    return None


def dfs_all_paths(
    graph: InMemoryGraph,
    source: str,
    target: str,
    max_depth: int = 5,
    rel_types: list[RelationType] | None = None,
) -> Generator[PathResult, None, None]:
    """DFS — yield all paths (up to max_depth hops)."""

    def _dfs(
        current: str,
        path_nodes: list[str],
        path_edges: list[InMemoryEdge],
        visited: set[str],
    ) -> Iterator[PathResult]:
        if len(path_edges) > max_depth:
            return
        if current == target and len(path_edges) > 0:
            yield PathResult(
                nodes=list(path_nodes),
                edges=list(path_edges),
                total_weight=sum(e.weight for e in path_edges),
                hops=len(path_edges),
            )
            return
        for edge in graph.out_edges(current):
            if rel_types and edge.relation_type not in rel_types:
                continue
            nxt = edge.target_id
            if nxt not in visited:
                visited.add(nxt)
                path_nodes.append(nxt)
                path_edges.append(edge)
                yield from _dfs(nxt, path_nodes, path_edges, visited)
                path_nodes.pop()
                path_edges.pop()
                visited.discard(nxt)

    yield from _dfs(source, [source], [], {source})


# ─────────────────────────────────────────────────────────────────────────────
# Subgraph extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_ego_graph(
    graph: InMemoryGraph,
    center: str,
    radius: int = 2,
    rel_types: list[RelationType] | None = None,
) -> InMemoryGraph:
    """Extract k-hop ego subgraph around center."""
    sub = InMemoryGraph()
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(center, 0)])

    while queue:
        node_id, depth = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = graph.get_node(node_id)
        if node:
            sub.add_node(node)
        if depth < radius:
            for edge in graph.out_edges(node_id):
                if rel_types and edge.relation_type not in rel_types:
                    continue
                sub.add_edge(edge)
                queue.append((edge.target_id, depth + 1))
            for edge in graph.in_edges(node_id):
                if rel_types and edge.relation_type not in rel_types:
                    continue
                sub.add_edge(edge)
                queue.append((edge.source_id, depth + 1))
    return sub


def extract_connected_component(graph: InMemoryGraph, start: str) -> InMemoryGraph:
    """BFS connected component (undirected view)."""
    sub = InMemoryGraph()
    visited: set[str] = set()
    queue: deque[str] = deque([start])

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = graph.get_node(node_id)
        if node:
            sub.add_node(node)
        for edge in graph.out_edges(node_id):
            sub.add_edge(edge)
            if edge.target_id not in visited:
                queue.append(edge.target_id)
        for edge in graph.in_edges(node_id):
            sub.add_edge(edge)
            if edge.source_id not in visited:
                queue.append(edge.source_id)
    return sub


# ─────────────────────────────────────────────────────────────────────────────
# Graph analytics (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

def degree_centrality(graph: InMemoryGraph) -> dict[str, float]:
    n = max(len(graph) - 1, 1)
    result = {}
    for node_id in graph._nodes:
        deg = len(graph.out_edges(node_id)) + len(graph.in_edges(node_id))
        result[node_id] = deg / n
    return result


def pagerank(
    graph: InMemoryGraph,
    damping: float = 0.85,
    iterations: int = 50,
    tol: float = 1e-6,
) -> dict[str, float]:
    nodes = list(graph._nodes.keys())
    n = len(nodes)
    if n == 0:
        return {}
    rank = {nid: 1.0 / n for nid in nodes}

    for _ in range(iterations):
        new_rank: dict[str, float] = {}
        for nid in nodes:
            in_sum = sum(
                rank[e.source_id] / max(len(graph.out_edges(e.source_id)), 1)
                for e in graph.in_edges(nid)
            )
            new_rank[nid] = (1 - damping) / n + damping * in_sum
        # check convergence
        delta = sum(abs(new_rank[nid] - rank[nid]) for nid in nodes)
        rank = new_rank
        if delta < tol:
            break
    return rank


def find_cycles(graph: InMemoryGraph, max_length: int = 6) -> list[list[str]]:
    """DFS-based cycle detection (returns unique cycles up to max_length)."""
    cycles: list[list[str]] = []
    visited: set[str] = set()

    def _dfs(start: str, current: str, path: list[str], path_set: set[str]) -> None:
        if len(path) > max_length:
            return
        for edge in graph.out_edges(current):
            nxt = edge.target_id
            if nxt == start and len(path) > 1:
                cycles.append(list(path) + [start])
            elif nxt not in path_set:
                path_set.add(nxt)
                path.append(nxt)
                _dfs(start, nxt, path, path_set)
                path.pop()
                path_set.discard(nxt)

    for node_id in graph._nodes:
        if node_id not in visited:
            _dfs(node_id, node_id, [node_id], {node_id})
            visited.add(node_id)

    return cycles


# ─────────────────────────────────────────────────────────────────────────────
# GraphLoader — hydrate from Neo4j result rows
# ─────────────────────────────────────────────────────────────────────────────

class GraphLoader:
    """
    Loads an InMemoryGraph from Neo4j result dicts.

    Expected row format (from Cypher):
      {node_id, node_type, name, tenant_id, ...props}
    or edge dict:
      {source_id, target_id, relation_type, weight, ...props}
    """

    @staticmethod
    def from_node_rows(rows: list[dict[str, Any]]) -> InMemoryGraph:
        g = InMemoryGraph()
        for row in rows:
            try:
                node = InMemoryNode(
                    node_id=row["node_id"],
                    node_type=NodeType(row.get("node_type", "Material")),
                    name=row.get("name", ""),
                    tenant_id=row.get("tenant_id", ""),
                    props={k: v for k, v in row.items() if k not in ("node_id", "node_type", "name", "tenant_id")},
                )
                g.add_node(node)
            except Exception:
                pass
        return g

    @staticmethod
    def add_edge_rows(graph: InMemoryGraph, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            try:
                edge = InMemoryEdge(
                    source_id=row["source_id"],
                    target_id=row["target_id"],
                    relation_type=RelationType(row.get("relation_type", "SIMILAR_TO")),
                    weight=float(row.get("weight", 1.0)),
                    props={k: v for k, v in row.items() if k not in ("source_id", "target_id", "relation_type", "weight")},
                )
                graph.add_edge(edge)
            except Exception:
                pass

    @staticmethod
    def from_neo4j_path(path_record: Any) -> list[dict[str, Any]]:
        """Convert Neo4j Path object to list of dicts."""
        result = []
        try:
            for node in path_record.nodes:
                result.append({"type": "node", "id": node.element_id, "labels": list(node.labels), "props": dict(node)})
            for rel in path_record.relationships:
                result.append({"type": "rel", "type_name": rel.type, "props": dict(rel)})
        except Exception:
            pass
        return result


# ─────────────────────────────────────────────────────────────────────────────
# NetworkX bridge (optional)
# ─────────────────────────────────────────────────────────────────────────────

def to_networkx(graph: InMemoryGraph, directed: bool = True) -> Any:
    """Convert InMemoryGraph to networkx.DiGraph / Graph (if installed)."""
    try:
        import networkx as nx
        G = nx.DiGraph() if directed else nx.Graph()
        for node_id, node in graph._nodes.items():
            G.add_node(node_id, label=node.node_type.value, name=node.name, **node.props)
        for node_id in graph._nodes:
            for edge in graph.out_edges(node_id):
                G.add_edge(
                    edge.source_id, edge.target_id,
                    relation=edge.relation_type.value,
                    weight=edge.weight,
                    **edge.props,
                )
        return G
    except ImportError:
        raise RuntimeError("networkx not installed — pip install networkx")


def from_networkx(G: Any, tenant_id: str = "") -> InMemoryGraph:
    """Import networkx graph into InMemoryGraph."""
    graph = InMemoryGraph()
    for node_id, data in G.nodes(data=True):
        node = InMemoryNode(
            node_id=str(node_id),
            node_type=NodeType(data.get("label", "Material")),
            name=data.get("name", str(node_id)),
            tenant_id=tenant_id,
            props={k: v for k, v in data.items() if k not in ("label", "name")},
        )
        graph.add_node(node)
    for src, tgt, data in G.edges(data=True):
        edge = InMemoryEdge(
            source_id=str(src),
            target_id=str(tgt),
            relation_type=RelationType(data.get("relation", "SIMILAR_TO")),
            weight=float(data.get("weight", 1.0)),
            props={k: v for k, v in data.items() if k not in ("relation", "weight")},
        )
        graph.add_edge(edge)
    return graph


# ─────────────────────────────────────────────────────────────────────────────
# GraphCache — tenant-keyed graph cache with TTL
# ─────────────────────────────────────────────────────────────────────────────

import time


class GraphCache:
    """Simple in-memory cache of tenant subgraphs with TTL (seconds)."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[InMemoryGraph, float]] = {}
        self._ttl   = ttl_seconds
        self._lock  = asyncio.Lock()

    async def get(self, tenant_id: str) -> InMemoryGraph | None:
        async with self._lock:
            entry = self._cache.get(tenant_id)
            if entry and (time.monotonic() - entry[1]) < self._ttl:
                return entry[0]
            return None

    async def set(self, tenant_id: str, graph: InMemoryGraph) -> None:
        async with self._lock:
            self._cache[tenant_id] = (graph, time.monotonic())

    async def invalidate(self, tenant_id: str) -> None:
        async with self._lock:
            self._cache.pop(tenant_id, None)

    async def invalidate_all(self) -> None:
        async with self._lock:
            self._cache.clear()

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "cached_tenants": len(self._cache),
            "entries": [
                {"tenant_id": tid, "nodes": g.stats()["nodes"], "age_s": round(now - ts, 1)}
                for tid, (g, ts) in self._cache.items()
            ],
        }
