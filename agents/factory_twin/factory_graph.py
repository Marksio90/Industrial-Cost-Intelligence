"""
Section 1 — Factory Graph

Graf fabryki jako sieć węzłów i krawędzi:
  - FactoryNode: maszyna / bufor / stanowisko / magazyn
  - FactoryEdge: przepływ materiału (+ odległość, pojemność, transport)
  - FactoryGraph: graf skierowany z operacjami topologicznymi
  - Algorytmy: BFS/DFS, wykrywanie wąskich gardeł, Dijkstra (czas przejścia),
               PageRank przepływu, identyfikacja SPF (Single Points of Failure),
               analiza ścieżki krytycznej (CPM)
  - LayoutBuilder: automatyczne rozmieszczenie węzłów (layered layout)
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .models import (
    FactoryLayout, Machine, Buffer, ManufacturingCell,
    MachineStatus, BufferType,
)


# ─────────────────────────────────────────────────────────────────────────────
# Graph primitives
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FactoryNode:
    node_id:    str
    node_type:  str          # "machine" | "buffer" | "cell" | "depot" | "sink"
    label:      str
    cell_id:    str = ""
    x:          float = 0.0
    y:          float = 0.0
    capacity:   int = 1
    # Runtime state (updated by simulation)
    utilization:  float = 0.0
    queue_length: int   = 0
    status:       str   = "idle"
    # Metadata for rendering
    color:        str   = "#90CAF9"
    shape:        str   = "rect"   # rect | circle | diamond


@dataclass
class FactoryEdge:
    from_id:      str
    to_id:        str
    edge_type:    str    # "material_flow" | "transport" | "control"
    distance_m:   float = 5.0
    capacity_u_h: float = 1000.0   # units per hour
    transport_id: str | None = None
    # Runtime
    flow_rate:    float = 0.0
    utilization:  float = 0.0


class FactoryGraph:
    """
    Directed graph of the factory floor.
    Nodes: machines, buffers, cells, raw material depot, finished goods sink.
    Edges: material flow, transport paths.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, FactoryNode] = {}
        self._out:   dict[str, list[FactoryEdge]] = defaultdict(list)
        self._in:    dict[str, list[FactoryEdge]]  = defaultdict(list)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def add_node(self, node: FactoryNode) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: FactoryEdge) -> None:
        self._out[edge.from_id].append(edge)
        self._in[edge.to_id].append(edge)

    def get_node(self, node_id: str) -> FactoryNode | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[FactoryNode]:
        return list(self._nodes.values())

    def edges(self) -> list[FactoryEdge]:
        result = []
        seen = set()
        for edges in self._out.values():
            for e in edges:
                key = (e.from_id, e.to_id)
                if key not in seen:
                    seen.add(key)
                    result.append(e)
        return result

    def out_edges(self, node_id: str) -> list[FactoryEdge]:
        return self._out.get(node_id, [])

    def in_edges(self, node_id: str) -> list[FactoryEdge]:
        return self._in.get(node_id, [])

    def successors(self, node_id: str) -> list[str]:
        return [e.to_id for e in self._out.get(node_id, [])]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.from_id for e in self._in.get(node_id, [])]

    def update_node_state(self, node_id: str, utilization: float, queue: int, status: str) -> None:
        node = self._nodes.get(node_id)
        if node:
            node.utilization  = utilization
            node.queue_length = queue
            node.status       = status

    # ── Traversal ─────────────────────────────────────────────────────────────

    def bfs(self, start: str) -> list[str]:
        visited: list[str] = []
        seen: set[str]     = set()
        q = deque([start])
        while q:
            nid = q.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            visited.append(nid)
            for s in self.successors(nid):
                if s not in seen:
                    q.append(s)
        return visited

    def topological_sort(self) -> list[str]:
        """Kahn's algorithm. Returns [] if cycle detected."""
        in_deg: dict[str, int] = {n.node_id: 0 for n in self.nodes()}
        for n in self.nodes():
            for s in self.successors(n.node_id):
                in_deg[s] = in_deg.get(s, 0) + 1
        q = deque(nid for nid, d in in_deg.items() if d == 0)
        order: list[str] = []
        while q:
            nid = q.popleft()
            order.append(nid)
            for s in self.successors(nid):
                in_deg[s] -= 1
                if in_deg[s] == 0:
                    q.append(s)
        return order if len(order) == len(self._nodes) else []

    def dijkstra(self, start: str, weight_fn=None) -> dict[str, float]:
        """
        Shortest path (by distance_m or custom weight) from start.
        Returns {node_id → distance}.
        """
        import heapq
        if weight_fn is None:
            weight_fn = lambda e: e.distance_m

        dist: dict[str, float] = {nid: float("inf") for nid in self._nodes}
        dist[start] = 0.0
        heap = [(0.0, start)]
        while heap:
            d, nid = heapq.heappop(heap)
            if d > dist[nid]:
                continue
            for edge in self.out_edges(nid):
                nd = d + weight_fn(edge)
                if nd < dist[edge.to_id]:
                    dist[edge.to_id] = nd
                    heapq.heappush(heap, (nd, edge.to_id))
        return dist

    # ── Analysis ─────────────────────────────────────────────────────────────

    def find_bottleneck(self) -> str | None:
        """Node with highest utilization among machines."""
        best: str | None   = None
        best_util: float   = -1.0
        for node in self.nodes():
            if node.node_type == "machine" and node.utilization > best_util:
                best_util = node.utilization
                best = node.node_id
        return best

    def find_starved_machines(self, threshold: float = 0.3) -> list[str]:
        """Machines with utilization < threshold (likely starved)."""
        return [
            n.node_id for n in self.nodes()
            if n.node_type == "machine" and n.utilization < threshold
        ]

    def find_blocked_buffers(self, fill_threshold: float = 0.8) -> list[str]:
        """Buffers filled above threshold."""
        return [
            n.node_id for n in self.nodes()
            if n.node_type == "buffer" and n.utilization > fill_threshold
        ]

    def critical_path(self, source: str, sink: str) -> tuple[list[str], float]:
        """
        Longest path (CPM) from source to sink by cycle_time.
        Returns (path, total_time_s).
        """
        order = self.topological_sort()
        if not order:
            return [], 0.0

        dist: dict[str, float] = {nid: 0.0 for nid in self._nodes}
        prev: dict[str, str | None] = {nid: None for nid in self._nodes}

        for nid in order:
            node = self._nodes.get(nid)
            if node:
                for edge in self.out_edges(nid):
                    new_d = dist[nid] + edge.distance_m
                    if new_d > dist.get(edge.to_id, 0.0):
                        dist[edge.to_id] = new_d
                        prev[edge.to_id] = nid

        # Reconstruct path
        path = []
        cur: str | None = sink
        while cur is not None:
            path.append(cur)
            cur = prev.get(cur)
        path.reverse()
        return path, dist.get(sink, 0.0)

    def single_points_of_failure(self) -> list[str]:
        """
        Nodes whose removal disconnects the graph (articulation points — BFS approximation).
        For factory graphs: machines with no parallel alternative.
        """
        spf: list[str] = []
        machine_nodes = [n.node_id for n in self.nodes() if n.node_type == "machine"]

        for nid in machine_nodes:
            # Check if predecessors can reach successors without this node
            preds = self.predecessors(nid)
            succs = self.successors(nid)
            if not preds or not succs:
                continue
            # If no alternative path exists, it's a SPF
            alt_path = False
            for p in preds:
                for s in succs:
                    # BFS from p to s avoiding nid
                    reached = self._bfs_avoid(p, s, avoid=nid)
                    if reached:
                        alt_path = True
                        break
            if not alt_path:
                spf.append(nid)
        return spf

    def _bfs_avoid(self, start: str, target: str, avoid: str) -> bool:
        seen: set[str] = {avoid}
        q = deque([start])
        while q:
            nid = q.popleft()
            if nid == target:
                return True
            if nid in seen:
                continue
            seen.add(nid)
            for s in self.successors(nid):
                if s not in seen:
                    q.append(s)
        return False

    def flow_pagerank(self, damping: float = 0.85, iterations: int = 50) -> dict[str, float]:
        """PageRank variant measuring importance of each node in material flow."""
        n = len(self._nodes)
        if n == 0:
            return {}
        ranks: dict[str, float] = {nid: 1.0 / n for nid in self._nodes}
        for _ in range(iterations):
            new_ranks: dict[str, float] = {}
            for nid in self._nodes:
                in_sum = sum(
                    ranks[e.from_id] / max(len(self.out_edges(e.from_id)), 1)
                    for e in self.in_edges(nid)
                )
                new_ranks[nid] = (1 - damping) / n + damping * in_sum
            ranks = new_ranks
        return ranks

    def cell_utilization(self) -> dict[str, float]:
        """Average utilization per manufacturing cell."""
        cell_nodes: dict[str, list[float]] = defaultdict(list)
        for node in self.nodes():
            if node.cell_id and node.node_type == "machine":
                cell_nodes[node.cell_id].append(node.utilization)
        return {
            cell: sum(vals) / len(vals)
            for cell, vals in cell_nodes.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id":          n.node_id,
                    "type":        n.node_type,
                    "label":       n.label,
                    "cell":        n.cell_id,
                    "x":           n.x,
                    "y":           n.y,
                    "utilization": round(n.utilization, 3),
                    "queue":       n.queue_length,
                    "status":      n.status,
                    "color":       n.color,
                    "shape":       n.shape,
                }
                for n in self.nodes()
            ],
            "edges": [
                {
                    "from":       e.from_id,
                    "to":         e.to_id,
                    "type":       e.edge_type,
                    "distance_m": e.distance_m,
                    "flow_rate":  round(e.flow_rate, 2),
                    "utilization": round(e.utilization, 3),
                }
                for e in self.edges()
            ],
            "stats": {
                "node_count": len(self._nodes),
                "edge_count": len(self.edges()),
                "bottleneck": self.find_bottleneck(),
            },
        }

    def stats(self) -> dict[str, Any]:
        return {
            "nodes":        len(self._nodes),
            "edges":        len(self.edges()),
            "machine_count": sum(1 for n in self.nodes() if n.node_type == "machine"),
            "buffer_count":  sum(1 for n in self.nodes() if n.node_type == "buffer"),
            "bottleneck":    self.find_bottleneck(),
            "spf":           self.single_points_of_failure(),
            "cell_util":     self.cell_utilization(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder from FactoryLayout
# ─────────────────────────────────────────────────────────────────────────────

_MACHINE_COLORS: dict[str, str] = {
    "cnc_milling":    "#1565C0",
    "cnc_turning":    "#1976D2",
    "press":          "#7B1FA2",
    "welding_robot":  "#E65100",
    "assembly_robot": "#2E7D32",
    "inspection_cmm": "#00838F",
    "conveyor":       "#546E7A",
    "furnace":        "#B71C1C",
    "painting_booth": "#F57F17",
    "laser_cutter":   "#880E4F",
    "injection_mold": "#4527A0",
    "packaging":      "#33691E",
    "generic":        "#455A64",
}

_BUFFER_COLORS: dict[str, str] = {
    "raw_material": "#A5D6A7",
    "wip":          "#FFF176",
    "finished":     "#80CBC4",
    "scrap":        "#EF9A9A",
    "quarantine":   "#FFCC80",
}


class FactoryGraphBuilder:
    """Builds a FactoryGraph from a FactoryLayout."""

    def build(self, layout: FactoryLayout) -> FactoryGraph:
        g = FactoryGraph()

        # ── Depot and sink ────────────────────────────────────────────────────
        g.add_node(FactoryNode("depot", "depot", "Magazyn WE", color="#81D4FA", shape="diamond"))
        g.add_node(FactoryNode("sink",  "sink",  "Magazyn WY", color="#80CBC4", shape="diamond"))

        # ── Buffers ───────────────────────────────────────────────────────────
        for buf in layout.buffers.values():
            color = _BUFFER_COLORS.get(buf.buffer_type.value, "#FFF176")
            g.add_node(FactoryNode(
                node_id   = buf.buffer_id,
                node_type = "buffer",
                label     = buf.name,
                cell_id   = buf.cell_id,
                x         = buf.x,
                y         = buf.y,
                capacity  = buf.capacity,
                color     = color,
                shape     = "rect",
            ))

        # ── Machines ──────────────────────────────────────────────────────────
        for m in layout.machines.values():
            color = _MACHINE_COLORS.get(m.machine_type.value, "#90CAF9")
            g.add_node(FactoryNode(
                node_id   = m.machine_id,
                node_type = "machine",
                label     = m.name,
                cell_id   = m.cell_id,
                x         = m.x,
                y         = m.y,
                capacity  = m.parallel_slots,
                color     = color,
                shape     = "circle" if "robot" in m.machine_type.value else "rect",
            ))

        # ── Edges from layout ─────────────────────────────────────────────────
        for (fid, tid), dist in layout.edges.items():
            # Determine edge type
            fnode = g.get_node(fid)
            etype = "transport" if (fnode and fnode.node_type in {"depot", "buffer"}) else "material_flow"
            g.add_edge(FactoryEdge(fid, tid, etype, distance_m=dist))

        # ── Auto-connect depot → first buffer → first machine if no edges ─────
        if not layout.edges:
            self._auto_connect(g, layout)

        return g

    def _auto_connect(self, g: FactoryGraph, layout: FactoryLayout) -> None:
        """Fallback: connect depot → machines in sequence based on product routing."""
        machines = list(layout.machines.values())
        buffers  = [b for b in layout.buffers.values() if b.buffer_type.value == "raw_material"]

        if buffers:
            buf = buffers[0]
            g.add_edge(FactoryEdge("depot", buf.buffer_id, "transport", 10.0))
            if machines:
                g.add_edge(FactoryEdge(buf.buffer_id, machines[0].machine_id, "material_flow", 5.0))

        for i in range(len(machines) - 1):
            g.add_edge(FactoryEdge(machines[i].machine_id, machines[i+1].machine_id, "material_flow", 5.0))

        if machines:
            g.add_edge(FactoryEdge(machines[-1].machine_id, "sink", "material_flow", 5.0))


# ─────────────────────────────────────────────────────────────────────────────
# Layout auto-placement (layered / grid)
# ─────────────────────────────────────────────────────────────────────────────

class LayoutAutoplacer:
    """
    Assigns (x, y) coordinates to nodes for visualization
    using a simple layered layout based on topological order.
    """

    def place(self, graph: FactoryGraph, cell_gap: float = 30.0, node_gap: float = 15.0) -> None:
        order = graph.topological_sort()
        if not order:
            order = [n.node_id for n in graph.nodes()]

        # Group by layer (BFS depth from depot/source)
        depth: dict[str, int] = {}
        q = deque()
        for nid in order:
            if not graph.predecessors(nid):
                depth[nid] = 0
                q.append(nid)

        while q:
            nid = q.popleft()
            for s in graph.successors(nid):
                new_d = depth.get(nid, 0) + 1
                if new_d > depth.get(s, -1):
                    depth[s] = new_d
                    q.append(s)

        # Group by depth layer
        layers: dict[int, list[str]] = defaultdict(list)
        for nid, d in depth.items():
            layers[d].append(nid)

        for layer_idx in sorted(layers):
            nodes_in_layer = layers[layer_idx]
            for row, nid in enumerate(nodes_in_layer):
                node = graph.get_node(nid)
                if node and node.x == 0.0 and node.y == 0.0:
                    node.x = layer_idx * cell_gap
                    node.y = row * node_gap
