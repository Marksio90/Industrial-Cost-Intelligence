"""
Section 1 — Cost Causal Graph

Skierowany acykliczny graf (DAG) relacji przyczynowych kosztu.

Węzły: każda kategoria kosztu (materiał, praca, energia, logistyka…)
Krawędzie: "A drives B" z elastycznością / mnożnikiem

Funkcje:
  - Budowa grafu z ProductCostModel
  - Topologiczne sortowanie (kolejność propagacji)
  - Analiza wpływu (które węzły mają największy koszt skumulowany)
  - Wykrywanie cykli (walidacja DAG)
  - Export do dict / JSON (dla frontendu)
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .models import (
    CostNode, CostEdge, CostEdgeType, CostNodeType, CostCategory,
    ProductCostModel, BomLine, RoutingStep,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CostGraph
# ─────────────────────────────────────────────────────────────────────────────

class CostGraph:
    """
    DAG reprezentujący relacje przyczynowe kosztów produktu.

    - _nodes : node_id → CostNode
    - _edges : list[CostEdge]
    - _out   : node_id → [CostEdge]   (adjacency out)
    - _in    : node_id → [CostEdge]   (adjacency in)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, CostNode]       = {}
        self._edges: list[CostEdge]            = []
        self._out:   dict[str, list[CostEdge]] = defaultdict(list)
        self._in:    dict[str, list[CostEdge]] = defaultdict(list)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def add_node(self, node: CostNode) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: CostEdge) -> None:
        self._edges.append(edge)
        self._out[edge.source_id].append(edge)
        self._in[edge.target_id].append(edge)

    def get_node(self, node_id: str) -> CostNode | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[CostNode]:
        return list(self._nodes.values())

    def edges(self) -> list[CostEdge]:
        return list(self._edges)

    def out_edges(self, node_id: str) -> list[CostEdge]:
        return self._out.get(node_id, [])

    def in_edges(self, node_id: str) -> list[CostEdge]:
        return self._in.get(node_id, [])

    # ── Topological sort (Kahn's algorithm) ──────────────────────────────────

    def topological_sort(self) -> list[str]:
        """Returns node_ids in topological order. Raises if cycle detected."""
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        for edge in self._edges:
            if edge.target_id in in_degree:
                in_degree[edge.target_id] += 1

        queue: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
        order: list[str]  = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for edge in self._out.get(nid, []):
                in_degree[edge.target_id] -= 1
                if in_degree[edge.target_id] == 0:
                    queue.append(edge.target_id)

        if len(order) != len(self._nodes):
            raise ValueError("Cost graph contains a cycle — not a valid DAG")
        return order

    def has_cycle(self) -> bool:
        try:
            self.topological_sort()
            return False
        except ValueError:
            return True

    # ── Influence analysis ────────────────────────────────────────────────────

    def compute_cumulative_costs(self) -> dict[str, float]:
        """
        Propagate costs forward through the DAG.
        A node's cumulative cost = own cost + weighted sum of upstream nodes.
        Returns node_id → cumulative_cost_eur.
        """
        order = self.topological_sort()
        cumulative: dict[str, float] = {}

        for nid in order:
            node  = self._nodes.get(nid)
            own   = (node.base_cost * node.quantity) if node else 0.0
            upstream = sum(
                cumulative.get(edge.source_id, 0.0) * edge.multiplier * edge.weight
                for edge in self._in.get(nid, [])
                if edge.edge_type == CostEdgeType.DRIVES
            )
            cumulative[nid] = own + upstream

        return cumulative

    def impact_ranking(self) -> list[dict[str, Any]]:
        """Rank nodes by their total cost impact on downstream nodes."""
        cumulative = self.compute_cumulative_costs()
        total      = sum(cumulative.values()) or 1.0
        ranking    = []

        for nid, cost in sorted(cumulative.items(), key=lambda x: -x[1]):
            node = self._nodes.get(nid)
            ranking.append({
                "node_id":   nid,
                "name":      node.name if node else nid,
                "type":      node.node_type.value if node else "",
                "cost_eur":  round(cost, 4),
                "share_pct": round(cost / total * 100, 2),
            })
        return ranking

    def sensitivity_per_driver(self, delta_pct: float = 1.0) -> list[dict[str, Any]]:
        """
        Finite-difference sensitivity: perturb each root node by delta_pct,
        measure change in total downstream cost.
        Returns sorted list of (node, elasticity) — tornado data.
        """
        base_total = sum(self.compute_cumulative_costs().values())
        results    = []

        for nid, node in self._nodes.items():
            if self._in.get(nid):    # skip non-root nodes
                continue
            orig = node.base_cost
            node.base_cost = orig * (1 + delta_pct / 100)
            perturbed_total = sum(self.compute_cumulative_costs().values())
            node.base_cost = orig

            elasticity = (perturbed_total - base_total) / max(base_total, 1e-9) / (delta_pct / 100)
            results.append({
                "node_id":    nid,
                "name":       node.name,
                "type":       node.node_type.value,
                "elasticity": round(elasticity, 4),
                "delta_eur":  round(perturbed_total - base_total, 4),
            })

        results.sort(key=lambda x: -abs(x["elasticity"]))
        return results

    # ── Reachability ──────────────────────────────────────────────────────────

    def reachable_from(self, node_id: str) -> set[str]:
        """All nodes reachable from node_id (BFS forward)."""
        visited: set[str] = set()
        queue   = deque([node_id])
        while queue:
            cur = queue.popleft()
            for edge in self._out.get(cur, []):
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    queue.append(edge.target_id)
        return visited

    def ancestors_of(self, node_id: str) -> set[str]:
        """All nodes that can reach node_id (BFS backward)."""
        visited: set[str] = set()
        queue   = deque([node_id])
        while queue:
            cur = queue.popleft()
            for edge in self._in.get(cur, []):
                if edge.source_id not in visited:
                    visited.add(edge.source_id)
                    queue.append(edge.source_id)
        return visited

    def cost_path(self, source_id: str, target_id: str) -> list[str] | None:
        """Shortest path from source to target (BFS). Returns node_id list."""
        if source_id not in self._nodes or target_id not in self._nodes:
            return None
        parent: dict[str, str | None] = {source_id: None}
        queue  = deque([source_id])
        while queue:
            cur = queue.popleft()
            if cur == target_id:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = parent.get(cur)
                return list(reversed(path))
            for edge in self._out.get(cur, []):
                if edge.target_id not in parent:
                    parent[edge.target_id] = cur
                    queue.append(edge.target_id)
        return None

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        cumulative = self.compute_cumulative_costs()
        total_cost = sum(cumulative.values()) or 1.0
        return {
            "nodes": [
                {
                    "node_id":      n.node_id,
                    "name":         n.name,
                    "type":         n.node_type.value,
                    "category":     n.category.value,
                    "base_cost":    round(n.base_cost, 4),
                    "quantity":     n.quantity,
                    "cumulative":   round(cumulative.get(n.node_id, 0.0), 4),
                    "share_pct":    round(cumulative.get(n.node_id, 0.0) / total_cost * 100, 2),
                    "is_fixed":     n.is_fixed,
                    "scrap_rate":   n.scrap_rate,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "edge_id":    e.edge_id,
                    "source":     e.source_id,
                    "target":     e.target_id,
                    "type":       e.edge_type.value,
                    "weight":     e.weight,
                    "multiplier": e.multiplier,
                    "elasticity": e.elasticity,
                }
                for e in self._edges
            ],
            "total_cost_eur": round(total_cost, 4),
            "node_count":     len(self._nodes),
            "edge_count":     len(self._edges),
        }

    def stats(self) -> dict[str, Any]:
        try:
            order = self.topological_sort()
            is_dag = True
        except ValueError:
            order  = []
            is_dag = False
        return {
            "nodes":   len(self._nodes),
            "edges":   len(self._edges),
            "is_dag":  is_dag,
            "depth":   len(order),
            "roots":   sum(1 for nid in self._nodes if not self._in.get(nid)),
            "leaves":  sum(1 for nid in self._nodes if not self._out.get(nid)),
        }


# ─────────────────────────────────────────────────────────────────────────────
# CostGraphBuilder — builds CostGraph from ProductCostModel
# ─────────────────────────────────────────────────────────────────────────────

class CostGraphBuilder:
    """
    Translates a ProductCostModel into a CostGraph DAG.

    Layers:
      1. Material nodes (one per BOM line)
      2. Process nodes  (one per routing step)
      3. Overhead node  (single, absorbs labor + machine)
      4. Logistics node
      5. Quality node
      6. Tax/Duty node
      7. Total cost node (sink)
    """

    def build(self, model: ProductCostModel) -> CostGraph:
        g = CostGraph()

        # Sink node — total cost
        sink = CostNode(
            node_id=f"{model.product_id}_total",
            name=f"{model.product_name} — Total Cost",
            node_type=CostNodeType.OVERHEAD,
            category=CostCategory.MANUFACTURING_OH,
            base_cost=0.0,
        )
        g.add_node(sink)

        # ── Material nodes ────────────────────────────────────────────────────
        mat_total = CostNode(
            node_id=f"{model.product_id}_mat_total",
            name="Direct Materials",
            node_type=CostNodeType.MATERIAL,
            category=CostCategory.DIRECT_MATERIAL,
            base_cost=0.0,
        )
        g.add_node(mat_total)

        for i, line in enumerate(model.bom_lines):
            node = CostNode(
                node_id=f"{model.product_id}_mat_{i}_{line.material_id}",
                name=line.material_name,
                node_type=CostNodeType.MATERIAL,
                category=CostCategory.DIRECT_MATERIAL,
                base_cost=line.unit_price,
                quantity=line.quantity * (1 + line.scrap_rate),
                unit=f"EUR/{line.unit}",
                scrap_rate=line.scrap_rate,
            )
            g.add_node(node)
            g.add_edge(CostEdge(
                source_id=node.node_id,
                target_id=mat_total.node_id,
                edge_type=CostEdgeType.DRIVES,
                weight=1.0,
                multiplier=1.0,
                elasticity=node.base_cost * node.quantity / max(model.direct_material_eur, 0.001),
            ))

        g.add_edge(CostEdge(
            source_id=mat_total.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        # ── Process / routing nodes ───────────────────────────────────────────
        proc_total = CostNode(
            node_id=f"{model.product_id}_proc_total",
            name="Manufacturing (Labor + Machine)",
            node_type=CostNodeType.LABOR,
            category=CostCategory.DIRECT_LABOR,
            base_cost=0.0,
        )
        g.add_node(proc_total)

        for step in model.routing:
            # Labor
            labor_h  = step.cycle_time_s / 3600.0 + step.setup_time_min / 60.0
            labor_c  = labor_h * step.labor_rate
            machine_c = labor_h * step.machine_rate

            labor_node = CostNode(
                node_id=f"{model.product_id}_labor_{step.step_id}",
                name=f"Labor: {step.operation_name}",
                node_type=CostNodeType.LABOR,
                category=CostCategory.DIRECT_LABOR,
                base_cost=labor_c,
                quantity=1.0,
            )
            machine_node = CostNode(
                node_id=f"{model.product_id}_mach_{step.step_id}",
                name=f"Machine: {step.operation_name}",
                node_type=CostNodeType.MACHINE,
                category=CostCategory.MANUFACTURING_OH,
                base_cost=machine_c,
                quantity=1.0,
            )
            g.add_node(labor_node)
            g.add_node(machine_node)

            for n in (labor_node, machine_node):
                g.add_edge(CostEdge(
                    source_id=n.node_id,
                    target_id=proc_total.node_id,
                    edge_type=CostEdgeType.DRIVES,
                    weight=1.0, multiplier=1.0, elasticity=1.0,
                ))

        g.add_edge(CostEdge(
            source_id=proc_total.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        # ── Logistics node ────────────────────────────────────────────────────
        logistics_node = CostNode(
            node_id=f"{model.product_id}_logistics",
            name="Logistics & Packaging",
            node_type=CostNodeType.LOGISTICS,
            category=CostCategory.LOGISTICS,
            base_cost=model.logistics_eur,
            quantity=1.0,
        )
        g.add_node(logistics_node)
        g.add_edge(CostEdge(
            source_id=logistics_node.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        # ── Overhead node ─────────────────────────────────────────────────────
        oh_node = CostNode(
            node_id=f"{model.product_id}_overhead",
            name="Factory + Admin Overhead",
            node_type=CostNodeType.OVERHEAD,
            category=CostCategory.MANUFACTURING_OH,
            base_cost=model.overhead_eur,
            quantity=1.0,
        )
        g.add_node(oh_node)
        # Overhead scales with labor
        g.add_edge(CostEdge(
            source_id=proc_total.node_id,
            target_id=oh_node.node_id,
            edge_type=CostEdgeType.SCALES_WITH,
            weight=0.25,    # 25% of labor
            multiplier=0.25,
            elasticity=1.0,
        ))
        g.add_edge(CostEdge(
            source_id=oh_node.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        # ── Quality node ──────────────────────────────────────────────────────
        qual_node = CostNode(
            node_id=f"{model.product_id}_quality",
            name="Quality & Inspection",
            node_type=CostNodeType.QUALITY,
            category=CostCategory.QUALITY,
            base_cost=model.quality_cost_eur,
            quantity=1.0,
        )
        g.add_node(qual_node)
        g.add_edge(CostEdge(
            source_id=qual_node.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        # ── Tax / Duty node ───────────────────────────────────────────────────
        tax_node = CostNode(
            node_id=f"{model.product_id}_tax",
            name="Duties & Taxes",
            node_type=CostNodeType.TAX_DUTY,
            category=CostCategory.TAX_COMPLIANCE,
            base_cost=model.duty_tax_eur,
            quantity=1.0,
        )
        g.add_node(tax_node)
        # Duty scales with material cost
        g.add_edge(CostEdge(
            source_id=mat_total.node_id,
            target_id=tax_node.node_id,
            edge_type=CostEdgeType.SCALES_WITH,
            weight=0.05,    # approx 5% duty on materials
            multiplier=0.05,
            elasticity=1.0,
        ))
        g.add_edge(CostEdge(
            source_id=tax_node.node_id,
            target_id=sink.node_id,
            edge_type=CostEdgeType.DRIVES,
            weight=1.0, multiplier=1.0, elasticity=1.0,
        ))

        return g


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built standard causal graphs for common cost drivers
# ─────────────────────────────────────────────────────────────────────────────

def build_steel_cost_graph() -> CostGraph:
    """
    Standard causal graph for steel part cost.

    Steel price → direct material cost
    Energy price → machine rate → conversion cost
    Labor rate  → direct labor
    Scrap rate  → waste cost
    FX rate     → import duty (if imported)
    CO2 price   → energy surcharge
    """
    g = CostGraph()

    steel_price  = CostNode(node_id="steel_price",  name="Steel Price (EUR/t)",  node_type=CostNodeType.MATERIAL,  category=CostCategory.DIRECT_MATERIAL, base_cost=800.0,  quantity=1.0)
    energy_price = CostNode(node_id="energy_price", name="Energy Price (EUR/MWh)",node_type=CostNodeType.ENERGY,   category=CostCategory.MANUFACTURING_OH, base_cost=120.0, quantity=1.0)
    labor_rate   = CostNode(node_id="labor_rate",   name="Labor Rate (EUR/h)",   node_type=CostNodeType.LABOR,    category=CostCategory.DIRECT_LABOR,     base_cost=28.0,  quantity=1.0)
    co2_price    = CostNode(node_id="co2_price",    name="CO2 Price (EUR/t)",    node_type=CostNodeType.ENERGY,   category=CostCategory.TAX_COMPLIANCE,   base_cost=80.0,  quantity=1.0)
    fx_eur_pln   = CostNode(node_id="fx_eur_pln",  name="EUR/PLN FX Rate",      node_type=CostNodeType.FINANCE,  category=CostCategory.FINANCE,          base_cost=4.25,  quantity=1.0)

    material_cost  = CostNode(node_id="material_cost",  name="Direct Material",  node_type=CostNodeType.MATERIAL, category=CostCategory.DIRECT_MATERIAL,  base_cost=0.0, quantity=1.0)
    machine_cost   = CostNode(node_id="machine_cost",   name="Machine Rate",     node_type=CostNodeType.MACHINE,  category=CostCategory.MANUFACTURING_OH,  base_cost=0.0, quantity=1.0)
    direct_labor   = CostNode(node_id="direct_labor",   name="Direct Labor",     node_type=CostNodeType.LABOR,    category=CostCategory.DIRECT_LABOR,      base_cost=0.0, quantity=1.0)
    energy_surcharge = CostNode(node_id="energy_surcharge", name="CO2 Surcharge",node_type=CostNodeType.TAX_DUTY, category=CostCategory.TAX_COMPLIANCE,   base_cost=0.0, quantity=1.0)
    total_cost     = CostNode(node_id="total_cost",     name="Total Unit Cost",  node_type=CostNodeType.OVERHEAD, category=CostCategory.MANUFACTURING_OH, base_cost=0.0, quantity=1.0)

    for n in [steel_price, energy_price, labor_rate, co2_price, fx_eur_pln,
              material_cost, machine_cost, direct_labor, energy_surcharge, total_cost]:
        g.add_node(n)

    # Causal edges
    g.add_edge(CostEdge("e1", "steel_price",   "material_cost",    CostEdgeType.DRIVES,      1.0, 0.0015,  1.0))  # 1.5 kg/part
    g.add_edge(CostEdge("e2", "energy_price",  "machine_cost",     CostEdgeType.DRIVES,      0.8, 0.002,   1.0))  # energy drives machine rate
    g.add_edge(CostEdge("e3", "labor_rate",    "direct_labor",     CostEdgeType.DRIVES,      1.0, 0.5/60,  1.0))  # 0.5h per part
    g.add_edge(CostEdge("e4", "co2_price",     "energy_surcharge", CostEdgeType.AMPLIFIES,   0.5, 0.0005,  0.6))  # CO2 → energy surcharge
    g.add_edge(CostEdge("e5", "fx_eur_pln",   "material_cost",    CostEdgeType.SCALES_WITH, 0.3, 0.0,    -0.3))  # FX affects import cost

    g.add_edge(CostEdge("e6", "material_cost",    "total_cost", CostEdgeType.DRIVES, 1.0, 1.0, 1.0))
    g.add_edge(CostEdge("e7", "machine_cost",     "total_cost", CostEdgeType.DRIVES, 1.0, 1.0, 1.0))
    g.add_edge(CostEdge("e8", "direct_labor",     "total_cost", CostEdgeType.DRIVES, 1.0, 1.0, 1.0))
    g.add_edge(CostEdge("e9", "energy_surcharge", "total_cost", CostEdgeType.DRIVES, 1.0, 1.0, 1.0))

    return g
