"""
Section 2 — Knowledge Graph

Graf wiedzy łączący encje gospodarcze: kraje, surowce, dostawców,
regulacje, wydarzenia rynkowe, procesy produkcyjne.

Funkcje:
  • Query: znajdź ścieżkę wpływu A→B
  • Inference: wyprowadź nowe relacje (transitivity, analogies)
  • Risk paths: wszystkie ścieżki od zdarzenia do kosztu
  • Conflict detector: wewnętrznie sprzeczne informacje
  • Context enrichment: dodaj wiedzę ekspertów jako fakty
"""
from __future__ import annotations

import math
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .models import KGNode, KGRelation, NodeEntity

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Graph
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, KGNode] = {}
        self._rels:  list[KGRelation]  = []
        self._out:   dict[str, list[KGRelation]] = defaultdict(list)
        self._in:    dict[str, list[KGRelation]] = defaultdict(list)
        self._type_idx: dict[str, list[str]] = defaultdict(list)   # entity → [node_ids]
        self._label_idx: dict[str, str] = {}                        # label → node_id

    def add_node(self, node: KGNode) -> "KnowledgeGraph":
        self._nodes[node.node_id] = node
        self._type_idx[node.entity.value].append(node.node_id)
        self._label_idx[node.label.lower()] = node.node_id
        return self

    def add_relation(self, rel: KGRelation) -> "KnowledgeGraph":
        self._rels.append(rel)
        self._out[rel.from_id].append(rel)
        self._in[rel.to_id].append(rel)
        return self

    def node(self, node_id: str) -> KGNode | None:
        return self._nodes.get(node_id)

    def find_by_label(self, label: str) -> KGNode | None:
        nid = self._label_idx.get(label.lower())
        return self._nodes.get(nid) if nid else None

    def find_by_type(self, entity: NodeEntity) -> list[KGNode]:
        return [self._nodes[nid] for nid in self._type_idx.get(entity.value, []) if nid in self._nodes]

    def relations_from(self, node_id: str, rel_type: str | None = None) -> list[KGRelation]:
        rels = self._out.get(node_id, [])
        if rel_type:
            rels = [r for r in rels if r.rel_type == rel_type]
        return rels

    def relations_to(self, node_id: str, rel_type: str | None = None) -> list[KGRelation]:
        rels = self._in.get(node_id, [])
        if rel_type:
            rels = [r for r in rels if r.rel_type == rel_type]
        return rels

    # ── Path finding ──────────────────────────────────────────────────────────

    def shortest_path(self, from_id: str, to_id: str) -> list[str] | None:
        """BFS shortest path."""
        if from_id == to_id:
            return [from_id]
        visited = {from_id}
        queue: deque = deque([(from_id, [from_id])])
        while queue:
            cur, path = queue.popleft()
            for rel in self._out.get(cur, []):
                nxt = rel.to_id
                if nxt == to_id:
                    return path + [nxt]
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return None

    def all_paths(self, from_id: str, to_id: str, max_depth: int = 5) -> list[list[str]]:
        """All simple paths up to max_depth."""
        results: list[list[str]] = []
        def dfs(cur: str, path: list[str], visited: set) -> None:
            if cur == to_id:
                results.append(list(path))
                return
            if len(path) > max_depth:
                return
            for rel in self._out.get(cur, []):
                nxt = rel.to_id
                if nxt not in visited:
                    visited.add(nxt)
                    path.append(nxt)
                    dfs(nxt, path, visited)
                    path.pop()
                    visited.discard(nxt)
        dfs(from_id, [from_id], {from_id})
        return results

    def risk_paths_to_cost(self, event_node_id: str) -> list[list[str]]:
        """All paths from an event to the production cost node."""
        cost_nodes = self._type_idx.get(NodeEntity.COST_COMPONENT.value, [])
        paths = []
        for cn in cost_nodes:
            paths.extend(self.all_paths(event_node_id, cn, max_depth=6))
        return paths

    def neighborhood(self, node_id: str, depth: int = 2) -> dict[str, KGNode]:
        """Return all nodes within depth hops."""
        visited = {node_id}
        queue: deque = deque([(node_id, 0)])
        result = {}
        while queue:
            cur, d = queue.popleft()
            result[cur] = self._nodes[cur]
            if d < depth:
                for rel in (self._out.get(cur, []) + self._in.get(cur, [])):
                    nxt = rel.to_id if rel.from_id == cur else rel.from_id
                    if nxt not in visited and nxt in self._nodes:
                        visited.add(nxt)
                        queue.append((nxt, d + 1))
        return result

    # ── Inference ─────────────────────────────────────────────────────────────

    def infer_transitive(self, rel_type: str) -> list[KGRelation]:
        """Infers transitive relations: if A→B and B→C then infer A→C."""
        existing = {(r.from_id, r.to_id) for r in self._rels if r.rel_type == rel_type}
        inferred = []
        for r1 in [r for r in self._rels if r.rel_type == rel_type]:
            for r2 in [r for r in self._out.get(r1.to_id, []) if r.rel_type == rel_type]:
                key = (r1.from_id, r2.to_id)
                if key not in existing and r1.from_id != r2.to_id:
                    inferred.append(KGRelation(
                        from_id=r1.from_id,
                        to_id=r2.to_id,
                        rel_type=f"{rel_type}_inferred",
                        weight=r1.weight * r2.weight * 0.7,
                        confidence=min(r1.confidence, r2.confidence) * 0.8,
                        properties={"inferred_via": r1.to_id},
                    ))
        return inferred

    def to_dict(self) -> dict:
        return {
            "nodes": [{"id": n.node_id, "entity": n.entity.value, "label": n.label,
                       "properties": n.properties} for n in self._nodes.values()],
            "relations": [{"id": r.rel_id, "from": r.from_id, "to": r.to_id,
                           "type": r.rel_type, "weight": r.weight} for r in self._rels],
            "stats": {
                "n_nodes": len(self._nodes), "n_relations": len(self._rels),
                "entities": {k: len(v) for k, v in self._type_idx.items()},
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built World Knowledge Graph
# ─────────────────────────────────────────────────────────────────────────────

def _kn(nid: str, entity: NodeEntity, label: str, **props) -> KGNode:
    return KGNode(node_id=nid, entity=entity, label=label, properties=props)

def _kr(fid: str, tid: str, rel_type: str, w: float = 1.0, conf: float = 0.9, **props) -> KGRelation:
    return KGRelation(from_id=fid, to_id=tid, rel_type=rel_type,
                      weight=w, confidence=conf, properties=props)


def build_world_knowledge_graph() -> KnowledgeGraph:
    kg = KnowledgeGraph()

    # ── Countries
    for cid, cname, region, risk in [
        ("c_de", "Germany",      "EU",     0.10),
        ("c_pl", "Poland",       "EU",     0.20),
        ("c_cn", "China",        "Asia",   0.45),
        ("c_in", "India",        "Asia",   0.35),
        ("c_mx", "Mexico",       "LATAM",  0.40),
        ("c_ru", "Russia",       "CIS",    0.90),
        ("c_us", "United States","NA",     0.15),
        ("c_ua", "Ukraine",      "EU-E",   0.85),
        ("c_ir", "Iran",         "ME",     0.80),
        ("c_sa", "Saudi Arabia", "ME",     0.35),
    ]:
        kg.add_node(_kn(cid, NodeEntity.COUNTRY, cname, region=region, geopolitical_risk=risk))

    # ── Commodities
    for cid, cname, unit, main_producer in [
        ("cm_steel",    "Steel HRC",       "EUR/t",   "c_cn"),
        ("cm_alum",     "Aluminium",       "EUR/t",   "c_cn"),
        ("cm_copper",   "Copper",          "EUR/t",   "c_cn"),
        ("cm_nickel",   "Nickel",          "EUR/t",   "c_ru"),
        ("cm_plastic",  "Plastics PE/PP",  "EUR/t",   "c_sa"),
        ("cm_rare_earth","Rare Earths",    "index",   "c_cn"),
        ("cm_oil",      "Crude Oil",       "USD/bbl", "c_sa"),
        ("cm_gas",      "Natural Gas",     "EUR/MWh", "c_ru"),
        ("cm_lithium",  "Lithium",         "USD/t",   "c_cn"),
    ]:
        kg.add_node(_kn(cid, NodeEntity.COMMODITY, cname, unit=unit, main_producer=main_producer))

    # ── Cost components
    for cid, cname, weight in [
        ("cc_material", "Material Cost",   0.40),
        ("cc_labor",    "Labor Cost",      0.25),
        ("cc_energy",   "Energy Cost",     0.15),
        ("cc_logistics","Logistics Cost",  0.10),
        ("cc_overhead", "Overhead",        0.10),
    ]:
        kg.add_node(_kn(cid, NodeEntity.COST_COMPONENT, cname, typical_weight=weight))

    # ── Markets
    for mid, mname, mtype in [
        ("m_lme",   "London Metal Exchange", "commodity"),
        ("m_ttf",   "TTF Gas Hub",           "energy"),
        ("m_ice",   "ICE Brent",             "energy"),
        ("m_ecx",   "EU ETS Carbon Market",  "carbon"),
        ("m_fx",    "FX Market",             "currency"),
    ]:
        kg.add_node(_kn(mid, NodeEntity.MARKET, mname, type=mtype))

    # ── Regulations
    for rid, rname, region in [
        ("reg_cbam",  "EU Carbon Border Adjustment Mechanism", "EU"),
        ("reg_reach", "REACH Chemical Regulation",             "EU"),
        ("reg_tariff_cn", "US/EU Tariffs on China",           "global"),
        ("reg_ets",   "EU Emissions Trading System",           "EU"),
        ("reg_supply_chain", "EU Supply Chain Due Diligence", "EU"),
    ]:
        kg.add_node(_kn(rid, NodeEntity.REGULATION, rname, region=region))

    # ── Events (known risk events)
    for eid, ename, prob, impact in [
        ("ev_war_eu",     "EU Military Escalation",           0.10, 0.9),
        ("ev_gas_cut",    "Russian Gas Cut-off",              0.20, 0.7),
        ("ev_tw_blockade","Taiwan Strait Blockade",           0.08, 0.95),
        ("ev_suez",       "Suez Canal Disruption",            0.15, 0.5),
        ("ev_recession",  "EU Recession",                     0.25, 0.6),
        ("ev_energy_crisis","Energy Price Spike >200 EUR/MWh",0.12, 0.8),
        ("ev_strike",     "Major Port Strike (EU)",           0.15, 0.4),
    ]:
        kg.add_node(_kn(eid, NodeEntity.EVENT, ename, probability=prob, impact_score=impact))

    # ── Relations ──────────────────────────────────────────────────────────────

    # Country produces / supplies commodity
    kg.add_relation(_kr("c_cn",  "cm_steel",      "major_producer", 0.55))
    kg.add_relation(_kr("c_cn",  "cm_alum",       "major_producer", 0.60))
    kg.add_relation(_kr("c_cn",  "cm_rare_earth", "dominant_supplier", 0.85))
    kg.add_relation(_kr("c_cn",  "cm_lithium",    "major_refiner",  0.70))
    kg.add_relation(_kr("c_ru",  "cm_nickel",     "major_producer", 0.40))
    kg.add_relation(_kr("c_ru",  "cm_gas",        "major_exporter", 0.35))
    kg.add_relation(_kr("c_sa",  "cm_oil",        "major_exporter", 0.12))
    kg.add_relation(_kr("c_in",  "cm_steel",      "growing_producer",0.10))

    # Commodity traded on market
    for cm, m in [("cm_steel","m_lme"),("cm_alum","m_lme"),("cm_copper","m_lme"),
                   ("cm_nickel","m_lme"),("cm_oil","m_ice"),("cm_gas","m_ttf")]:
        kg.add_relation(_kr(cm, m, "traded_on", 1.0))

    # Commodity → cost component
    kg.add_relation(_kr("cm_steel",    "cc_material",  "major_input",   0.25))
    kg.add_relation(_kr("cm_alum",     "cc_material",  "input",         0.12))
    kg.add_relation(_kr("cm_copper",   "cc_material",  "input",         0.10))
    kg.add_relation(_kr("cm_plastic",  "cc_material",  "input",         0.08))
    kg.add_relation(_kr("cm_oil",      "cc_energy",    "input",         0.40))
    kg.add_relation(_kr("cm_gas",      "cc_energy",    "major_input",   0.45))
    kg.add_relation(_kr("cm_gas",      "cc_logistics", "bunker_fuel",   0.20))

    # Events → commodities / countries
    kg.add_relation(_kr("ev_war_eu",     "cm_steel",    "supply_shock",  0.40, conf=0.7))
    kg.add_relation(_kr("ev_gas_cut",    "cm_gas",      "supply_shock",  0.80, conf=0.9))
    kg.add_relation(_kr("ev_gas_cut",    "cc_energy",   "major_impact",  0.70, conf=0.9))
    kg.add_relation(_kr("ev_tw_blockade","cm_rare_earth","supply_shock", 0.90, conf=0.8))
    kg.add_relation(_kr("ev_tw_blockade","cm_lithium",  "supply_shock",  0.60, conf=0.8))
    kg.add_relation(_kr("ev_suez",       "cc_logistics","cost_increase", 0.50, conf=0.85))
    kg.add_relation(_kr("ev_recession",  "cm_steel",    "demand_drop",   0.35, conf=0.75))
    kg.add_relation(_kr("ev_energy_crisis","cc_energy", "major_impact",  0.85, conf=0.90))
    kg.add_relation(_kr("ev_strike",     "cc_logistics","disruption",    0.40, conf=0.80))

    # Regulations → cost components
    kg.add_relation(_kr("reg_cbam",  "cc_material",  "cost_adder",    0.30, conf=0.95))
    kg.add_relation(_kr("reg_ets",   "cc_energy",    "cost_adder",    0.20, conf=0.95))
    kg.add_relation(_kr("reg_tariff_cn","cc_material","cost_adder",   0.25, conf=0.80))
    kg.add_relation(_kr("reg_supply_chain","cc_overhead","compliance_cost",0.10, conf=0.85))

    # Country trade dependencies
    kg.add_relation(_kr("c_de", "c_cn", "imports_from", 0.35, desc="Germany: significant CN imports"))
    kg.add_relation(_kr("c_de", "c_ru", "energy_dependency", 0.25, desc="Historical gas dependency"))
    kg.add_relation(_kr("c_pl", "c_de", "supply_chain_linked", 0.60, desc="German production chains"))
    kg.add_relation(_kr("c_de", "c_in", "growing_imports",  0.08))
    kg.add_relation(_kr("c_de", "c_mx", "nearshoring_target",0.05))

    return kg


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Query Engine
# ─────────────────────────────────────────────────────────────────────────────

class KGQueryEngine:
    """High-level query interface for the knowledge graph."""

    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    def risk_exposure(self, cost_component_id: str) -> list[dict]:
        """What events can impact this cost component? Via all paths."""
        events = self.kg.find_by_type(NodeEntity.EVENT)
        exposed = []
        for ev in events:
            paths = self.kg.all_paths(ev.node_id, cost_component_id, max_depth=5)
            if paths:
                prob = ev.properties.get("probability", 0.1)
                impact = ev.properties.get("impact_score", 0.5)
                # Path weight = product of relation weights
                min_path_weight = min(
                    math.prod(
                        (self.kg._out.get(p[i], [{}])[0].weight if self.kg._out.get(p[i]) else 0.5)
                        for i in range(len(p) - 1)
                    )
                    for p in paths
                )
                exposed.append({
                    "event_id":    ev.node_id,
                    "event_name":  ev.label,
                    "n_paths":     len(paths),
                    "probability": prob,
                    "impact":      impact,
                    "risk_score":  prob * impact * min_path_weight,
                    "shortest_path": paths[0] if paths else [],
                })
        return sorted(exposed, key=lambda x: x["risk_score"], reverse=True)

    def supply_chain_dependencies(self, country_id: str) -> list[dict]:
        """What commodities / countries does this country depend on?"""
        rels = self.kg.relations_from(country_id, "imports_from") + \
               self.kg.relations_from(country_id, "energy_dependency") + \
               self.kg.relations_from(country_id, "supply_chain_linked")
        return [
            {"target": r.to_id, "target_name": self.kg.node(r.to_id).label if self.kg.node(r.to_id) else r.to_id,
             "rel_type": r.rel_type, "weight": r.weight}
            for r in rels
        ]

    def commodity_exposure_chain(self, commodity_id: str) -> dict:
        """Full chain: producers → markets → cost components → risk events."""
        producers = [r.from_id for r in self.kg.relations_to(commodity_id, "major_producer")
                     + self.kg.relations_to(commodity_id, "dominant_supplier")]
        markets   = [r.to_id for r in self.kg.relations_from(commodity_id, "traded_on")]
        cost_comps= [r.to_id for r in self.kg.relations_from(commodity_id, "major_input")
                     + self.kg.relations_from(commodity_id, "input")]
        events    = [r.from_id for r in self.kg.relations_to(commodity_id, "supply_shock")]
        return {
            "commodity":   commodity_id,
            "producers":   producers,
            "markets":     markets,
            "cost_components": cost_comps,
            "risk_events": events,
        }
