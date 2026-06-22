"""
Section 1 — Causal Graph

DAG przyczynowo-skutkowy modelujący propagację szoków ekonomicznych
przez łańcuch: geopolityka → logistyka → surowce → energia → FX → inflacja → koszty.

Struktura grafu:
  Geopolitics → Supply Chain Stress → Logistics Cost → Commodity Prices
  Oil Price   → Energy Cost         → Inflation       → Labor Cost
  FX EUR/USD  → Import Cost         → Material Cost   → Production Cost
  Interest Rate → Demand Index      → Commodity Demand → Prices

Każda krawędź ma:
  • strength   — elastyczność (1% zmiany A → strength% zmiany B)
  • lag_months — z jakim opóźnieniem efekt się materializuje
  • relation   — kierunek i charakter związku
"""
from __future__ import annotations

import math
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .models import (
    CausalNode, CausalEdge, CausalRelationType, SignalType, WorldState,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Causal Graph Structure
# ─────────────────────────────────────────────────────────────────────────────

class CausalGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, CausalNode] = {}
        self._edges: list[CausalEdge] = []
        self._out: dict[str, list[CausalEdge]] = defaultdict(list)
        self._in:  dict[str, list[CausalEdge]] = defaultdict(list)

    def add_node(self, node: CausalNode) -> "CausalGraph":
        self._nodes[node.node_id] = node
        return self

    def add_edge(self, edge: CausalEdge) -> "CausalGraph":
        self._edges.append(edge)
        self._out[edge.from_id].append(edge)
        self._in[edge.to_id].append(edge)
        return self

    def node(self, node_id: str) -> CausalNode | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[CausalNode]:
        return list(self._nodes.values())

    def edges(self) -> list[CausalEdge]:
        return list(self._edges)

    def successors(self, node_id: str) -> list[str]:
        return [e.to_id for e in self._out.get(node_id, [])]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.from_id for e in self._in.get(node_id, [])]

    def topological_sort(self) -> list[str]:
        """Kahn's algorithm — returns nodes in causal order."""
        in_degree = {nid: len(self._in.get(nid, [])) for nid in self._nodes}
        queue = deque([n for n, d in in_degree.items() if d == 0])
        order: list[str] = []
        while queue:
            nid = queue.popleft()
            order.append(nid)
            for e in self._out.get(nid, []):
                in_degree[e.to_id] -= 1
                if in_degree[e.to_id] == 0:
                    queue.append(e.to_id)
        return order

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": n.node_id, "name": n.name, "type": n.signal_type.value,
                 "region": n.region, "current": n.current_val, "unit": n.unit}
                for n in self._nodes.values()
            ],
            "edges": [
                {"from": e.from_id, "to": e.to_id, "relation": e.relation.value,
                 "strength": e.strength, "lag_months": e.lag_months, "desc": e.description}
                for e in self._edges
            ],
            "stats": {
                "n_nodes": len(self._nodes),
                "n_edges": len(self._edges),
                "avg_degree": len(self._edges) / max(len(self._nodes), 1),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built World Causal Graph
# ─────────────────────────────────────────────────────────────────────────────

def _n(nid: str, name: str, stype: SignalType, unit: str = "",
       region: str = "global", mean_rev: float | None = None,
       vol: float = 0.05, drift: float = 0.0) -> CausalNode:
    return CausalNode(node_id=nid, name=name, signal_type=stype, region=region,
                      unit=unit, mean_reversion_level=mean_rev,
                      volatility=vol, drift=drift)


def _e(fid: str, tid: str, rel: CausalRelationType, strength: float,
       lag: int = 0, desc: str = "") -> CausalEdge:
    return CausalEdge(from_id=fid, to_id=tid, relation=rel,
                      strength=strength, lag_months=lag, description=desc)


def build_world_causal_graph() -> CausalGraph:
    """
    Builds the complete causal DAG for production cost forecasting.
    ~35 nodes, ~55 edges covering all major transmission channels.
    """
    g = CausalGraph()

    # ── Exogenous roots (no incoming edges)
    g.add_node(_n("geo_eu",   "EU Geopolitical Risk",         SignalType.GEOPOLITICAL_RISK, "0-1", "EU",     vol=0.15))
    g.add_node(_n("geo_asia", "Asia Geopolitical Risk",       SignalType.GEOPOLITICAL_RISK, "0-1", "Asia",   vol=0.12))
    g.add_node(_n("geo_me",   "Middle East Conflict Risk",    SignalType.GEOPOLITICAL_RISK, "0-1", "ME",     vol=0.18))
    g.add_node(_n("ir_ecb",   "ECB Interest Rate",            SignalType.INTEREST_RATE,     "%",   "EUR",    mean_rev=2.5, vol=0.03))
    g.add_node(_n("ir_fed",   "Fed Funds Rate",               SignalType.INTEREST_RATE,     "%",   "USD",    mean_rev=3.0, vol=0.04))
    g.add_node(_n("demand_gl","Global Manufacturing Demand",  SignalType.DEMAND_INDEX,      "idx", "global", mean_rev=100.0, vol=0.08))

    # ── Energy
    g.add_node(_n("oil",      "Brent Crude Oil",              SignalType.ENERGY_PRICE,      "USD/bbl", "global", mean_rev=80.0, vol=0.25))
    g.add_node(_n("gas_eu",   "EU Natural Gas (TTF)",         SignalType.ENERGY_PRICE,      "EUR/MWh", "EU",     mean_rev=40.0, vol=0.40))
    g.add_node(_n("elec_eu",  "EU Electricity",               SignalType.ENERGY_PRICE,      "EUR/MWh", "EU",     mean_rev=90.0, vol=0.30))
    g.add_node(_n("coal",     "Coal Price",                   SignalType.ENERGY_PRICE,      "USD/t",   "global", mean_rev=120.0, vol=0.20))

    # ── FX
    g.add_node(_n("fx_eurusd","EUR/USD",                      SignalType.FX_RATE,           "rate",    "EUR",    mean_rev=1.08, vol=0.08))
    g.add_node(_n("fx_eurcny","EUR/CNY",                      SignalType.FX_RATE,           "rate",    "EUR",    mean_rev=7.80, vol=0.06))
    g.add_node(_n("fx_eurpln","EUR/PLN",                      SignalType.FX_RATE,           "rate",    "EUR",    mean_rev=4.25, vol=0.07))

    # ── Inflation
    g.add_node(_n("cpi_eu",   "EU CPI",                       SignalType.INFLATION,         "% YoY", "EU",     mean_rev=2.5, vol=0.04, drift=-0.1))
    g.add_node(_n("ppi_eu",   "EU PPI",                       SignalType.INFLATION,         "% YoY", "EU",     mean_rev=3.0, vol=0.06))
    g.add_node(_n("wage_eu",  "EU Wage Growth",               SignalType.LABOR_COST,        "% YoY", "EU",     mean_rev=3.5, vol=0.03, drift=0.05))

    # ── Commodities
    g.add_node(_n("steel",    "Steel HRC",                    SignalType.COMMODITY_PRICE,   "EUR/t",   "EU",    mean_rev=620.0, vol=0.20))
    g.add_node(_n("alum",     "Aluminium LME",                SignalType.COMMODITY_PRICE,   "EUR/t",   "global",mean_rev=2300.0, vol=0.18))
    g.add_node(_n("copper",   "Copper LME",                   SignalType.COMMODITY_PRICE,   "EUR/t",   "global",mean_rev=8800.0, vol=0.22))
    g.add_node(_n("plastic",  "Plastic (PE/PP avg)",          SignalType.COMMODITY_PRICE,   "EUR/t",   "EU",    mean_rev=1075.0, vol=0.15))
    g.add_node(_n("rare_earth","Rare Earth Index",            SignalType.COMMODITY_PRICE,   "index",   "CN",    mean_rev=100.0, vol=0.25))

    # ── Logistics
    g.add_node(_n("freight_sh","Shanghai-Rotterdam Freight",  SignalType.LOGISTICS_COST,    "USD/FEU","global", mean_rev=2000.0, vol=0.35))
    g.add_node(_n("road_eu",   "EU Road Freight",             SignalType.LOGISTICS_COST,    "EUR/t",  "EU",     mean_rev=45.0, vol=0.10))
    g.add_node(_n("lead_time", "Supply Lead Time Index",      SignalType.SUPPLY_DISRUPTION, "idx",    "global", mean_rev=1.0, vol=0.12))

    # ── Labor & production cost
    g.add_node(_n("labor_cost","EU Industrial Labor Cost",    SignalType.LABOR_COST,        "EUR/h",  "EU",     mean_rev=32.0, vol=0.04))
    g.add_node(_n("prod_cost", "Industrial Production Cost",  SignalType.COMMODITY_PRICE,   "idx",    "EU",     mean_rev=100.0, vol=0.10))

    # ── Causal edges (transmission channels) ──────────────────────────────────

    # Geopolitics → logistics / supply
    g.add_edge(_e("geo_eu",   "freight_sh", CausalRelationType.DIRECT_POSITIVE, 0.15, lag=1, desc="EU tensions → rerouting, insurance"))
    g.add_edge(_e("geo_me",   "oil",        CausalRelationType.DIRECT_POSITIVE, 0.30, lag=0, desc="ME conflict → oil supply shock"))
    g.add_edge(_e("geo_me",   "freight_sh", CausalRelationType.DIRECT_POSITIVE, 0.20, lag=1, desc="ME conflict → Red Sea rerouting"))
    g.add_edge(_e("geo_asia", "rare_earth", CausalRelationType.DIRECT_POSITIVE, 0.35, lag=2, desc="Asia tensions → rare earth export restrictions"))
    g.add_edge(_e("geo_asia", "freight_sh", CausalRelationType.DIRECT_POSITIVE, 0.20, lag=0, desc="Asia tensions → port congestion"))
    g.add_edge(_e("geo_eu",   "gas_eu",     CausalRelationType.DIRECT_POSITIVE, 0.25, lag=0, desc="EU conflict → gas supply disruption"))

    # Oil → energy cascade
    g.add_edge(_e("oil",      "gas_eu",     CausalRelationType.DIRECT_POSITIVE, 0.40, lag=1, desc="Oil-gas price correlation"))
    g.add_edge(_e("oil",      "coal",       CausalRelationType.DIRECT_POSITIVE, 0.25, lag=1, desc="Energy substitution"))
    g.add_edge(_e("gas_eu",   "elec_eu",    CausalRelationType.DIRECT_POSITIVE, 0.55, lag=0, desc="Gas dominates marginal power cost"))
    g.add_edge(_e("coal",     "elec_eu",    CausalRelationType.DIRECT_POSITIVE, 0.20, lag=0, desc="Coal power plants"))
    g.add_edge(_e("oil",      "fx_eurusd",  CausalRelationType.DIRECT_NEGATIVE, 0.10, lag=1, desc="Oil↑ → USD demand → EUR weakens"))
    g.add_edge(_e("oil",      "freight_sh", CausalRelationType.DIRECT_POSITIVE, 0.30, lag=0, desc="Bunker fuel cost"))
    g.add_edge(_e("oil",      "plastic",    CausalRelationType.DIRECT_POSITIVE, 0.45, lag=2, desc="Naphtha feedstock"))

    # Energy → inflation / production cost
    g.add_edge(_e("elec_eu",  "ppi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.20, lag=1, desc="Energy intensive production"))
    g.add_edge(_e("elec_eu",  "steel",      CausalRelationType.DIRECT_POSITIVE, 0.35, lag=2, desc="Electric arc furnace costs"))
    g.add_edge(_e("elec_eu",  "alum",       CausalRelationType.DIRECT_POSITIVE, 0.50, lag=1, desc="Electrolysis energy intensity"))
    g.add_edge(_e("elec_eu",  "labor_cost", CausalRelationType.DIRECT_POSITIVE, 0.05, lag=3, desc="Cost of living → wage pressure"))
    g.add_edge(_e("gas_eu",   "steel",      CausalRelationType.DIRECT_POSITIVE, 0.20, lag=2, desc="Blast furnace natural gas"))
    g.add_edge(_e("gas_eu",   "plastic",    CausalRelationType.DIRECT_POSITIVE, 0.30, lag=2, desc="Steam cracker feedstock"))

    # FX → import costs
    g.add_edge(_e("fx_eurusd","oil",        CausalRelationType.DIRECT_NEGATIVE, 0.15, lag=0, desc="EUR/USD → oil cost in EUR"))
    g.add_edge(_e("fx_eurusd","copper",     CausalRelationType.DIRECT_NEGATIVE, 0.25, lag=0, desc="LME priced in USD"))
    g.add_edge(_e("fx_eurusd","alum",       CausalRelationType.DIRECT_NEGATIVE, 0.25, lag=0, desc="LME priced in USD"))
    g.add_edge(_e("fx_eurcny","rare_earth", CausalRelationType.DIRECT_NEGATIVE, 0.20, lag=1, desc="CNY appreciation → costlier imports"))
    g.add_edge(_e("fx_eurcny","freight_sh", CausalRelationType.DIRECT_NEGATIVE, 0.10, lag=0, desc="CNY costs"))

    # Interest rates → FX & demand
    g.add_edge(_e("ir_fed",   "fx_eurusd",  CausalRelationType.DIRECT_NEGATIVE, 0.35, lag=1, desc="Fed↑ → USD strengthens"))
    g.add_edge(_e("ir_ecb",   "fx_eurusd",  CausalRelationType.DIRECT_POSITIVE, 0.30, lag=1, desc="ECB↑ → EUR strengthens"))
    g.add_edge(_e("ir_ecb",   "demand_gl",  CausalRelationType.DIRECT_NEGATIVE, 0.20, lag=3, desc="Tighter credit → lower demand"))
    g.add_edge(_e("ir_fed",   "demand_gl",  CausalRelationType.DIRECT_NEGATIVE, 0.25, lag=3, desc="US demand contraction"))
    g.add_edge(_e("demand_gl","steel",      CausalRelationType.DIRECT_POSITIVE, 0.30, lag=2, desc="Industrial demand → steel"))
    g.add_edge(_e("demand_gl","copper",     CausalRelationType.DIRECT_POSITIVE, 0.35, lag=2, desc="Copper is demand barometer"))
    g.add_edge(_e("demand_gl","freight_sh", CausalRelationType.DIRECT_POSITIVE, 0.25, lag=1, desc="Trade volume → freight rates"))

    # Inflation → wages → production cost
    g.add_edge(_e("cpi_eu",   "wage_eu",    CausalRelationType.DIRECT_POSITIVE, 0.60, lag=3, desc="CPI → real wage negotiations"))
    g.add_edge(_e("ppi_eu",   "cpi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.35, lag=2, desc="PPI passes through to CPI"))
    g.add_edge(_e("wage_eu",  "labor_cost", CausalRelationType.DIRECT_POSITIVE, 0.85, lag=0, desc="Wage = majority of labor cost"))
    g.add_edge(_e("wage_eu",  "ppi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.25, lag=1, desc="Labor cost → producer prices"))

    # Commodities → PPI
    g.add_edge(_e("steel",    "ppi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.15, lag=1, desc="Steel weight in PPI basket"))
    g.add_edge(_e("copper",   "ppi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.08, lag=1, desc="Copper in PPI"))
    g.add_edge(_e("plastic",  "ppi_eu",     CausalRelationType.DIRECT_POSITIVE, 0.10, lag=1, desc="Plastics in PPI"))

    # Logistics → lead time → supply disruption
    g.add_edge(_e("freight_sh","lead_time", CausalRelationType.DIRECT_POSITIVE, 0.40, lag=1, desc="High freight → longer queues"))
    g.add_edge(_e("freight_sh","ppi_eu",    CausalRelationType.DIRECT_POSITIVE, 0.12, lag=2, desc="Freight cost pass-through"))
    g.add_edge(_e("lead_time", "prod_cost", CausalRelationType.DIRECT_POSITIVE, 0.15, lag=1, desc="Delays → safety stock → cost"))
    g.add_edge(_e("road_eu",  "prod_cost",  CausalRelationType.DIRECT_POSITIVE, 0.08, lag=0, desc="Domestic transport cost"))

    # Final aggregation → production cost
    g.add_edge(_e("labor_cost","prod_cost", CausalRelationType.DIRECT_POSITIVE,   0.30, lag=0))
    g.add_edge(_e("steel",    "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.20, lag=1))
    g.add_edge(_e("alum",     "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.10, lag=1))
    g.add_edge(_e("copper",   "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.08, lag=1))
    g.add_edge(_e("plastic",  "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.06, lag=1))
    g.add_edge(_e("elec_eu",  "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.12, lag=0))
    g.add_edge(_e("ppi_eu",   "prod_cost",  CausalRelationType.DIRECT_POSITIVE,   0.25, lag=1))

    return g


# ─────────────────────────────────────────────────────────────────────────────
# Shock Propagator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShockResult:
    source_node:  str
    shocked_val:  float      # original_value × (1 + shock_pct/100)
    propagated:   dict[str, float]   # node_id → cumulative % change
    causal_path:  list[str]


class ShockPropagator:
    """
    Propagates a % shock from a source node through the causal graph
    using BFS with multiplicative strength accumulation.

    At each hop: downstream_change = upstream_change × edge.strength
    Lag is tracked but not applied (caller handles timing).
    """

    def propagate(
        self,
        graph: CausalGraph,
        source_id: str,
        shock_pct: float,       # e.g. +50 = 50% increase in source
        max_hops: int = 5,
        min_strength: float = 0.005,
    ) -> ShockResult:
        propagated: dict[str, float] = {source_id: shock_pct}
        queue: deque = deque([(source_id, shock_pct, [source_id])])
        visited: set = {source_id}
        best_path: list[str] = [source_id]

        while queue:
            node_id, current_pct, path = queue.popleft()
            if len(path) > max_hops + 1:
                continue
            for edge in graph._out.get(node_id, []):
                downstream = edge.to_id
                # Signed propagation
                if edge.relation in (CausalRelationType.DIRECT_NEGATIVE,
                                     CausalRelationType.DAMPENING):
                    delta = -current_pct * edge.strength
                else:
                    delta = current_pct * edge.strength

                if abs(delta) < min_strength:
                    continue

                if downstream in propagated:
                    propagated[downstream] += delta
                else:
                    propagated[downstream] = delta
                    if downstream not in visited:
                        visited.add(downstream)
                        new_path = path + [downstream]
                        queue.append((downstream, delta, new_path))
                        if len(new_path) > len(best_path):
                            best_path = new_path

        return ShockResult(
            source_node=source_id,
            shocked_val=shock_pct,
            propagated=propagated,
            causal_path=best_path,
        )

    def multi_shock(
        self,
        graph: CausalGraph,
        shocks: dict[str, float],  # node_id → shock_pct
        max_hops: int = 5,
    ) -> dict[str, float]:
        """Superimposes multiple simultaneous shocks."""
        total: dict[str, float] = {}
        for source, pct in shocks.items():
            result = self.propagate(graph, source, pct, max_hops)
            for node, delta in result.propagated.items():
                total[node] = total.get(node, 0.0) + delta
        return total
