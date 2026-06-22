"""
Section 5 — Risk Propagation

Propagacja ryzyka przez sieć dostawców:
  - Graf zależności: dostawca → materiał → produkt
  - Ripple effect: bankructwo dostawcy → brakujące materiały → zatrzymanie produkcji
  - Geo-konsentracja: % spend w jednym kraju / regionie
  - Single-source exposure: materiały krytyczne z jednym dostawcą
  - Korelacja ryzyka: gdy jeden dostawca pada, inne mogą podążyć (sektor/kraj)
  - Resilience score: odporność całego łańcucha dostaw
  - Mitigation actions: sugestie redukcji ryzyka
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .models import (
    SupplierProfile, SupplierStatus, MaterialSpec,
    ImpactLevel, RiskEventType, SimulationState,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Risk node / graph structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskNode:
    node_id:   str
    node_type: str     # "supplier" | "material" | "product"
    name:      str
    risk_score: float  # 0–1
    exposure:  float   # EUR at risk
    criticality: float  # 0–1 (impact if this node fails)


@dataclass
class RiskEdge:
    source_id: str
    target_id: str
    edge_type: str    # "supplies" | "uses" | "depends_on"
    weight:    float  # dependency strength 0–1
    spend_eur: float  # annual spend on this edge


@dataclass
class RiskGraph:
    nodes: dict[str, RiskNode] = field(default_factory=dict)
    edges: list[RiskEdge]      = field(default_factory=list)

    def adjacency_out(self) -> dict[str, list[RiskEdge]]:
        adj: dict[str, list[RiskEdge]] = defaultdict(list)
        for e in self.edges:
            adj[e.source_id].append(e)
        return dict(adj)

    def adjacency_in(self) -> dict[str, list[RiskEdge]]:
        adj: dict[str, list[RiskEdge]] = defaultdict(list)
        for e in self.edges:
            adj[e.target_id].append(e)
        return dict(adj)


# ─────────────────────────────────────────────────────────────────────────────
# Risk score components
# ─────────────────────────────────────────────────────────────────────────────

def _supplier_risk_score(sup: SupplierProfile) -> float:
    """Composite supplier risk score 0–1."""
    # Bankruptcy probability (annualised → normalised)
    bankrupt_risk = min(sup.bankruptcy_prob_annual * 5, 1.0)  # 20%+ → score 1
    # Geographic risk
    geo_risk  = sup.geo_risk_score
    # Delivery risk
    otd_risk  = 1.0 - sup.on_time_delivery_pct
    # Quality risk
    qual_risk = min(sup.quality_defect_rate * 10, 1.0)
    # Credit rating risk
    _RATING_RISK = {"AAA": 0.0, "AA": 0.05, "A": 0.10, "BBB": 0.20, "BB": 0.35, "B": 0.55, "CCC": 0.80, "D": 1.0}
    credit_risk = _RATING_RISK.get(sup.credit_rating.upper(), 0.30)

    weights = [0.30, 0.25, 0.15, 0.10, 0.20]
    scores  = [bankrupt_risk, geo_risk, otd_risk, qual_risk, credit_risk]
    return min(sum(w * s for w, s in zip(weights, scores)), 1.0)


def _material_criticality(mat: MaterialSpec, state: SimulationState) -> float:
    """How critical is this material to operations (0–1)."""
    base = 0.5 if mat.is_critical else 0.2
    # Fewer suppliers → higher criticality
    active_suppliers = sum(
        1 for sid in mat.supplier_ids
        if state.suppliers.get(sid) and state.suppliers[sid].status == SupplierStatus.ACTIVE
    )
    supplier_factor = 1.0 - min((active_suppliers - 1) * 0.15, 0.75)
    # No substitutes → higher criticality
    substitute_factor = 0.0 if mat.substitutes else 0.30

    return min(base + supplier_factor * 0.5 + substitute_factor, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# RiskPropagationEngine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PropagationImpact:
    origin_id:    str
    origin_name:  str
    event_type:   RiskEventType
    direct_eur:   float
    indirect_eur: float
    total_eur:    float
    affected_materials: list[str]
    affected_products:  list[str]
    propagation_hops: int
    impact_level: ImpactLevel
    ripple_chain: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResilienceReport:
    overall_score:        float    # 0–1 (1 = fully resilient)
    single_source_count:  int      # materials with only 1 supplier
    geo_concentration:    dict[str, float]   # country → spend%
    high_risk_spend_pct:  float
    critical_suppliers:   list[dict[str, Any]]
    mitigation_actions:   list[dict[str, Any]]
    hhi_supplier:         float    # Herfindahl–Hirschman Index for supplier concentration


class RiskPropagationEngine:
    """
    Analyses and simulates risk propagation through the supply network.
    """

    def __init__(self, state: SimulationState) -> None:
        self._state = state
        self._graph = self._build_risk_graph()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_risk_graph(self) -> RiskGraph:
        g = RiskGraph()

        for sup_id, sup in self._state.suppliers.items():
            risk = _supplier_risk_score(sup)
            annual_spend = self._estimate_supplier_spend(sup_id)
            g.nodes[sup_id] = RiskNode(
                node_id=sup_id,
                node_type="supplier",
                name=sup.name,
                risk_score=risk,
                exposure=annual_spend,
                criticality=0.0,    # set after material analysis
            )

        for mat_id, mat in self._state.materials.items():
            crit = _material_criticality(mat, self._state)
            annual_spend = mat.annual_usage * mat.price_model.base_price
            g.nodes[mat_id] = RiskNode(
                node_id=mat_id,
                node_type="material",
                name=mat.name,
                risk_score=0.0,
                exposure=annual_spend,
                criticality=crit,
            )
            # Supplier → Material edges
            for sup_id in mat.supplier_ids:
                if sup_id in self._state.suppliers:
                    annual_s = annual_spend / max(len(mat.supplier_ids), 1)
                    g.edges.append(RiskEdge(
                        source_id=sup_id,
                        target_id=mat_id,
                        edge_type="supplies",
                        weight=1.0 if sup_id == mat.primary_supplier_id else 0.5,
                        spend_eur=annual_s,
                    ))

        return g

    def _estimate_supplier_spend(self, supplier_id: str) -> float:
        total = 0.0
        for mat in self._state.materials.values():
            if supplier_id in mat.supplier_ids:
                share = 1.0 / max(len(mat.supplier_ids), 1)
                total += mat.annual_usage * mat.price_model.base_price * share
        return total

    # ── Ripple effect ─────────────────────────────────────────────────────────

    def simulate_supplier_failure(self, supplier_id: str) -> PropagationImpact:
        """
        Simulate what happens if supplier_id goes bankrupt.
        BFS through supply graph to find affected materials and cost impact.
        """
        sup = self._state.suppliers.get(supplier_id)
        if not sup:
            return PropagationImpact(
                origin_id=supplier_id, origin_name="unknown",
                event_type=RiskEventType.SUPPLIER_BANKRUPT,
                direct_eur=0, indirect_eur=0, total_eur=0,
                affected_materials=[], affected_products=[],
                propagation_hops=0, impact_level=ImpactLevel.LOW,
            )

        adj_in = self._graph.adjacency_in()
        visited: set[str] = {supplier_id}
        queue   = [(supplier_id, 0, [])]
        affected_materials: list[str] = []
        direct_eur   = 0.0
        indirect_eur = 0.0
        ripple_chain: list[dict[str, Any]] = []
        max_hops     = 0

        while queue:
            node_id, hops, path = queue.pop(0)
            max_hops = max(max_hops, hops)

            for edge in adj_in.get(node_id, []):
                # This edge: target = node_id (something supplies node_id) — wrong direction
                pass

            # Forward: what does this supplier supply?
            for edge in self._graph.edges:
                if edge.source_id != node_id:
                    continue
                target_node = self._graph.nodes.get(edge.target_id)
                if not target_node or edge.target_id in visited:
                    continue
                visited.add(edge.target_id)

                mat = self._state.materials.get(edge.target_id)
                if mat:
                    affected_materials.append(edge.target_id)
                    annual_spend = mat.annual_usage * mat.price_model.base_price
                    # Can we switch to alternative supplier?
                    alt_count = sum(
                        1 for sid in mat.supplier_ids
                        if sid != supplier_id
                        and self._state.suppliers.get(sid)
                        and self._state.suppliers[sid].status == SupplierStatus.ACTIVE
                    )
                    if alt_count == 0:
                        # No alternative — full exposure
                        premium_cost = annual_spend * 0.20   # emergency sourcing premium
                        direct_eur  += premium_cost
                        impact_note  = "no_alternative"
                    else:
                        # Switching cost (qualification, logistics)
                        switch_cost  = annual_spend * 0.05
                        indirect_eur += switch_cost
                        impact_note  = f"alt_suppliers={alt_count}"

                    ripple_chain.append({
                        "hop":      hops + 1,
                        "node_id":  edge.target_id,
                        "name":     mat.name,
                        "type":     "material",
                        "eur":      round(annual_spend, 2),
                        "impact":   impact_note,
                    })
                    queue.append((edge.target_id, hops + 1, path + [node_id]))

        total_eur    = direct_eur + indirect_eur
        impact_level = self._classify_supplier_impact(total_eur, len(affected_materials))

        return PropagationImpact(
            origin_id=supplier_id,
            origin_name=sup.name,
            event_type=RiskEventType.SUPPLIER_BANKRUPT,
            direct_eur=round(direct_eur, 2),
            indirect_eur=round(indirect_eur, 2),
            total_eur=round(total_eur, 2),
            affected_materials=affected_materials,
            affected_products=[],     # extend with BOM data if available
            propagation_hops=max_hops + 1,
            impact_level=impact_level,
            ripple_chain=ripple_chain,
        )

    def simulate_price_shock(self, material_id: str, delta_pct: float) -> dict[str, Any]:
        """Compute cost impact of a price shock on a specific material."""
        mat = self._state.materials.get(material_id)
        if not mat:
            return {}
        annual_spend    = mat.annual_usage * mat.price_model.base_price
        additional_cost = annual_spend * (delta_pct / 100.0)
        # Check if substitutes exist
        sub_count = len(mat.substitutes)
        mitigation = additional_cost * min(0.30 * sub_count, 0.70)   # substitution saves up to 70%
        net_impact  = additional_cost - mitigation
        return {
            "material_id":      material_id,
            "material_name":    mat.name,
            "base_spend_eur":   round(annual_spend, 2),
            "price_delta_pct":  delta_pct,
            "gross_impact_eur": round(additional_cost, 2),
            "mitigation_eur":   round(mitigation, 2),
            "net_impact_eur":   round(net_impact, 2),
            "substitutes_count": sub_count,
            "impact_level":     self._classify_cost_impact(net_impact, annual_spend).value,
        }

    def simulate_fx_shock(self, pair: str, delta_pct: float) -> dict[str, Any]:
        """Compute portfolio cost impact of FX rate change."""
        base_currency = pair.split("/")[1]   # EUR/PLN → PLN is the cost currency
        affected_spend = 0.0
        for mat in self._state.materials.values():
            if mat.price_model.currency != base_currency:
                continue
            affected_spend += mat.annual_usage * mat.price_model.base_price
        fx_impact  = affected_spend * (delta_pct / 100.0)
        hedged_pct = 0.30   # assume 30% hedged by default
        net_impact = fx_impact * (1 - hedged_pct)
        return {
            "pair":             pair,
            "delta_pct":        delta_pct,
            "exposed_spend_eur": round(affected_spend, 2),
            "gross_impact_eur": round(fx_impact, 2),
            "assumed_hedge_pct": hedged_pct,
            "net_impact_eur":   round(net_impact, 2),
        }

    # ── Resilience analysis ───────────────────────────────────────────────────

    def compute_resilience(self) -> ResilienceReport:
        single_source = [
            mat for mat in self._state.materials.values()
            if len(mat.supplier_ids) <= 1
        ]

        # Geo concentration
        spend_by_country: dict[str, float] = defaultdict(float)
        total_spend = 0.0
        for sup_id, sup in self._state.suppliers.items():
            spend = self._estimate_supplier_spend(sup_id)
            spend_by_country[sup.country] += spend
            total_spend += spend
        geo_pct = {
            country: round(spend / max(total_spend, 1) * 100, 1)
            for country, spend in spend_by_country.items()
        }

        # HHI (Herfindahl–Hirschman Index) for supplier concentration
        supplier_shares = [
            self._estimate_supplier_spend(sid) / max(total_spend, 1)
            for sid in self._state.suppliers
        ]
        hhi = sum(s ** 2 for s in supplier_shares) * 10_000   # 0–10000

        # High-risk spend
        high_risk_spend = sum(
            self._estimate_supplier_spend(sid)
            for sid, sup in self._state.suppliers.items()
            if _supplier_risk_score(sup) > 0.5
        )
        high_risk_pct = high_risk_spend / max(total_spend, 1)

        # Top risky suppliers
        critical_sups = sorted(
            [
                {
                    "supplier_id":  sid,
                    "name":         sup.name,
                    "country":      sup.country,
                    "risk_score":   round(_supplier_risk_score(sup), 3),
                    "annual_spend": round(self._estimate_supplier_spend(sid), 0),
                }
                for sid, sup in self._state.suppliers.items()
            ],
            key=lambda x: -x["risk_score"],
        )[:10]

        # Resilience score (inverse of risk concentration)
        geo_max_pct   = max(geo_pct.values(), default=0) / 100.0
        single_src_r  = len(single_source) / max(len(self._state.materials), 1)
        score = max(0.0, 1.0 - 0.40 * geo_max_pct - 0.35 * single_src_r - 0.25 * high_risk_pct)

        mitigation = self._generate_mitigation_actions(
            single_source, geo_pct, high_risk_pct, critical_sups
        )

        return ResilienceReport(
            overall_score=round(score, 3),
            single_source_count=len(single_source),
            geo_concentration=geo_pct,
            high_risk_spend_pct=round(high_risk_pct, 3),
            critical_suppliers=critical_sups,
            mitigation_actions=mitigation,
            hhi_supplier=round(hhi, 0),
        )

    def _generate_mitigation_actions(
        self,
        single_source:    list[MaterialSpec],
        geo_pct:          dict[str, float],
        high_risk_pct:    float,
        critical_sups:    list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        priority = 1

        for mat in sorted(single_source, key=lambda m: -m.annual_usage * m.price_model.base_price)[:5]:
            spend = mat.annual_usage * mat.price_model.base_price
            actions.append({
                "priority":   priority,
                "action":     "DUAL_SOURCE",
                "target":     mat.name,
                "description": f"Zakwalifikuj 2. dostawcę dla {mat.name} (spend: {spend:,.0f} EUR/rok)",
                "risk_reduction_pct": 60,
                "effort":     "MEDIUM",
            })
            priority += 1

        for country, pct in sorted(geo_pct.items(), key=lambda x: -x[1]):
            if pct > 40:
                actions.append({
                    "priority":   priority,
                    "action":     "GEO_DIVERSIFY",
                    "target":     country,
                    "description": f"Zmniejsz koncentrację {country}: {pct:.0f}% spend → cel <30%",
                    "risk_reduction_pct": 30,
                    "effort":     "HIGH",
                })
                priority += 1

        if high_risk_pct > 0.30:
            actions.append({
                "priority":   priority,
                "action":     "RISK_MONITORING",
                "target":     "high_risk_suppliers",
                "description": f"{high_risk_pct:.0%} spend w dostawcach wysokiego ryzyka — wdroż quarterly review",
                "risk_reduction_pct": 15,
                "effort":     "LOW",
            })
            priority += 1

        return actions

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_supplier_impact(total_eur: float, mat_count: int) -> ImpactLevel:
        if total_eur > 500_000 or mat_count > 10:
            return ImpactLevel.CRITICAL
        if total_eur > 100_000 or mat_count > 5:
            return ImpactLevel.HIGH
        if total_eur > 20_000 or mat_count > 2:
            return ImpactLevel.MEDIUM
        return ImpactLevel.LOW

    @staticmethod
    def _classify_cost_impact(net_impact: float, base_spend: float) -> ImpactLevel:
        pct = net_impact / max(base_spend, 1)
        if pct > 0.20:
            return ImpactLevel.CRITICAL
        if pct > 0.10:
            return ImpactLevel.HIGH
        if pct > 0.03:
            return ImpactLevel.MEDIUM
        return ImpactLevel.LOW

    # ── Portfolio risk ────────────────────────────────────────────────────────

    def portfolio_var(self, confidence: float = 0.95) -> dict[str, float]:
        """
        Simple parametric VaR for the procurement portfolio.
        Assumes log-normal price distribution and independence (conservative).
        """
        total_variance = 0.0
        total_spend    = 0.0

        for mat in self._state.materials.values():
            w     = mat.annual_usage * mat.price_model.base_price
            sigma = mat.price_model.volatility
            total_spend    += w
            total_variance += (w * sigma) ** 2

        portfolio_sigma = math.sqrt(total_variance) / max(total_spend, 1)

        # Z-score for confidence level
        _Z = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}
        z   = _Z.get(confidence, 1.645)
        var = total_spend * portfolio_sigma * z

        return {
            "total_spend_eur":    round(total_spend, 2),
            "portfolio_vol_pct":  round(portfolio_sigma * 100, 2),
            "var_eur":            round(var, 2),
            "var_pct":            round(portfolio_sigma * z * 100, 2),
            "confidence":         confidence,
        }

    def correlation_risk(self) -> list[dict[str, Any]]:
        """
        Flag supplier pairs in the same country/sector (correlated failures).
        """
        correlated_pairs: list[dict[str, Any]] = []
        sups = list(self._state.suppliers.values())
        for i in range(len(sups)):
            for j in range(i + 1, len(sups)):
                a, b = sups[i], sups[j]
                if a.country == b.country:
                    shared_mats = set(a.material_ids) & set(b.material_ids)
                    if shared_mats:
                        correlated_pairs.append({
                            "supplier_a":   a.name,
                            "supplier_b":   b.name,
                            "country":      a.country,
                            "shared_materials": list(shared_mats),
                            "correlation_risk": "HIGH — same country",
                        })
        return correlated_pairs
