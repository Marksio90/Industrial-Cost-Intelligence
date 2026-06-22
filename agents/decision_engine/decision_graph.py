"""
Section 1 — Decision Graph

Hierarchiczny graf decyzyjny (DAG reguł biznesowych):
  - DecisionGraph: węzły warunkowe, progi, bramy AND/OR, liście akcji
  - GraphTraverser: ewaluacja kontekstu przez graf → lista aktywowanych węzłów
  - DecisionTreeBuilder: buduje predefiniowany graf dla scenariuszy ICI
  - SignalExtractor: wyciąga sygnały numeryczne z DecisionContext
  - RuleEngine: zestaw reguł biznesowych (parametryzowane, wersjonowane)
  - GraphSerializer: eksport do JSON + wizualizacja (nodes + edges)

Predefiniowane poddrzewa:
  supplier_risk_tree   → CHANGE_SUPPLIER | DUAL_SOURCE | QUALIFY_ALTERNATIVE | RENEGOTIATE
  price_signal_tree    → BUY_NOW | WAIT | HEDGE_FX | INCREASE_STOCK | REDUCE_STOCK
  material_cost_tree   → CHANGE_MATERIAL | CHANGE_PROCESS | RENEGOTIATE | NO_ACTION
  moq_optimization_tree→ INCREASE_MOQ | DECREASE_MOQ | RENEGOTIATE
  inventory_tree       → BUY_NOW | WAIT | INCREASE_STOCK | REDUCE_STOCK
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .models import (
    DecisionContext, DecisionNode, NodeType, RecommendationType, UrgencyLevel,
    CostContext, SupplierContext, MarketContext, ProductionContext,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Signal extractor — flattens DecisionContext into a dict[str, float|str]
# ─────────────────────────────────────────────────────────────────────────────

class SignalExtractor:
    """
    Extracts named signals from a DecisionContext.
    Signal keys follow the convention: domain.attribute
    e.g. "supplier.financial_risk", "market.trend_direction"
    """

    def extract(self, ctx: DecisionContext) -> dict[str, Any]:
        signals: dict[str, Any] = {
            "risk_appetite": ctx.risk_appetite,
            "cost_weight":   ctx.cost_weight,
            "risk_weight":   ctx.risk_weight,
        }

        if ctx.cost:
            c = ctx.cost
            signals.update({
                "cost.current_unit_cost":  c.current_unit_cost,
                "cost.cost_trend_pct":     c.cost_trend_pct,
                "cost.material_pct":       c.material_pct,
                "cost.benchmark_cost":     c.benchmark_cost or 0.0,
                "cost.above_benchmark":    (c.current_unit_cost > (c.benchmark_cost or c.current_unit_cost)),
                "cost.above_target":       (c.current_unit_cost > (c.target_unit_cost or c.current_unit_cost)),
                "cost.vs_target_pct":      (
                    (c.current_unit_cost - (c.target_unit_cost or c.current_unit_cost))
                    / max(c.target_unit_cost or c.current_unit_cost, 0.001) * 100
                ),
                "cost.vs_benchmark_pct":   (
                    (c.current_unit_cost - (c.benchmark_cost or c.current_unit_cost))
                    / max(c.benchmark_cost or c.current_unit_cost, 0.001) * 100
                ),
            })

        if ctx.supplier:
            s = ctx.supplier
            signals.update({
                "supplier.financial_risk":       s.financial_risk,
                "supplier.geo_risk":             s.geo_risk,
                "supplier.composite_risk":       (s.financial_risk * 0.4 + s.geo_risk * 0.35 + (1 - s.otd_pct / 100) * 0.25),
                "supplier.otd_pct":              s.otd_pct,
                "supplier.defect_rate_pct":      s.defect_rate_pct,
                "supplier.single_source":        float(s.single_source),
                "supplier.moq":                  float(s.moq),
                "supplier.price_delta_pct":      s.price_delta_pct,
                "supplier.contract_expiry_days": float(s.contract_expiry_days),
                "supplier.alternative_count":    float(s.alternative_count),
                "supplier.volume_moq_ratio":     float(s.annual_volume) / max(s.moq, 1),
                "supplier.lead_time_days":       s.lead_time_days,
            })

        if ctx.market:
            m = ctx.market
            is_rising  = 1.0 if m.trend_direction == "rising"  else 0.0
            is_falling = 1.0 if m.trend_direction == "falling" else 0.0
            signals.update({
                "market.spot_price_eur":            m.spot_price_eur,
                "market.price_volatility_30d":      m.price_volatility_30d,
                "market.trend_rising":              is_rising,
                "market.trend_falling":             is_falling,
                "market.trend_strength":            m.trend_strength,
                "market.supply_disruption_risk":    m.supply_disruption_risk,
                "market.demand_index":              m.demand_index,
                "market.fx_weakening":              float(m.fx_trend == "weakening"),
                "market.futures_premium_pct":       (
                    ((m.futures_price_eur or m.spot_price_eur) - m.spot_price_eur)
                    / max(m.spot_price_eur, 0.001) * 100
                ),
                "market.alternative_count":         float(len(m.alternative_materials)),
            })

        if ctx.production:
            p = ctx.production
            signals.update({
                "production.inventory_days":         p.current_inventory_days,
                "production.safety_stock_days":      p.safety_stock_days,
                "production.days_of_cover":          p.current_inventory_days - p.safety_stock_days,
                "production.below_safety_stock":     float(p.current_inventory_days < p.safety_stock_days),
                "production.near_reorder":           float(p.current_inventory_days < p.reorder_point_days),
                "production.scrap_rate_pct":         p.scrap_rate_pct,
                "production.production_rate_pct":    p.production_rate_pct,
                "production.demand_confidence":      p.demand_confidence,
                "production.annual_volume":          float(p.annual_volume),
            })

        # Custom signals
        signals.update(ctx.custom_signals)
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Graph data structure
# ─────────────────────────────────────────────────────────────────────────────

class DecisionGraph:
    """
    Directed acyclic graph of DecisionNodes.
    Evaluation: DFS from root nodes → collect all activated leaf (action) nodes.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, DecisionNode] = {}
        self._out:   dict[str, list[str]] = defaultdict(list)
        self._in:    dict[str, list[str]]  = defaultdict(list)
        self._roots: list[str] = []

    def add_node(self, node: DecisionNode) -> "DecisionGraph":
        self._nodes[node.node_id] = node
        return self

    def add_edge(self, from_id: str, to_id: str) -> "DecisionGraph":
        self._out[from_id].append(to_id)
        self._in[to_id].append(from_id)
        # Update node children / parent lists
        if from_id in self._nodes and to_id not in self._nodes[from_id].children_ids:
            self._nodes[from_id].children_ids.append(to_id)
        if to_id in self._nodes and from_id not in self._nodes[to_id].parent_ids:
            self._nodes[to_id].parent_ids.append(from_id)
        return self

    def set_roots(self, *node_ids: str) -> "DecisionGraph":
        self._roots = list(node_ids)
        return self

    def get_node(self, node_id: str) -> DecisionNode | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[DecisionNode]:
        return list(self._nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id":       n.node_id,
                    "type":     n.node_type.value,
                    "label":    n.label,
                    "signal":   n.signal_key,
                    "operator": n.operator,
                    "threshold": n.threshold,
                    "rec_type": n.rec_type.value if n.rec_type else None,
                    "urgency":  n.urgency.value,
                    "children": n.children_ids,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {"from": fid, "to": tid}
                for fid, tos in self._out.items()
                for tid in tos
            ],
            "roots": self._roots,
            "stats": {
                "nodes":   len(self._nodes),
                "edges":   sum(len(v) for v in self._out.values()),
                "actions": sum(1 for n in self._nodes.values() if n.node_type == NodeType.ACTION),
                "roots":   len(self._roots),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Graph traverser (evaluator)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TraversalResult:
    activated_nodes:  list[str]          # node_ids that evaluated True
    action_nodes:     list[DecisionNode] # leaf action nodes reached
    path_traces:      dict[str, list[str]] = field(default_factory=dict)  # action_id → path
    signal_snapshot:  dict[str, Any]     = field(default_factory=dict)
    evaluation_log:   list[dict[str, Any]] = field(default_factory=list)


class GraphTraverser:
    """
    Evaluates a DecisionGraph against a set of signals.
    Returns all activated action nodes with their traversal paths.
    """

    def evaluate(
        self,
        graph:   DecisionGraph,
        signals: dict[str, Any],
    ) -> TraversalResult:
        result = TraversalResult([], [], signal_snapshot=signals)
        activated: set[str] = set()

        for root_id in graph._roots:
            self._dfs(graph, root_id, signals, activated, result, path=[root_id])

        result.activated_nodes = list(activated)
        return result

    def _dfs(
        self,
        graph:     DecisionGraph,
        node_id:   str,
        signals:   dict[str, Any],
        activated: set[str],
        result:    TraversalResult,
        path:      list[str],
    ) -> bool:
        node = graph.get_node(node_id)
        if not node:
            return False

        passed = self._evaluate_node(node, signals)
        log_entry = {
            "node_id": node_id,
            "label":   node.label,
            "type":    node.node_type.value,
            "passed":  passed,
        }
        result.evaluation_log.append(log_entry)

        if not passed:
            return False

        activated.add(node_id)

        if node.node_type == NodeType.ACTION and node.rec_type:
            result.action_nodes.append(node)
            result.path_traces[node_id] = list(path)
            return True

        # Traverse children
        child_results: list[bool] = []
        for child_id in node.children_ids:
            cr = self._dfs(graph, child_id, signals, activated, result, path + [child_id])
            child_results.append(cr)

        if node.node_type == NodeType.GATE_AND:
            return all(child_results)
        elif node.node_type == NodeType.GATE_OR:
            return any(child_results)
        return any(child_results)

    def _evaluate_node(self, node: DecisionNode, signals: dict[str, Any]) -> bool:
        if node.node_type in {NodeType.GATE_AND, NodeType.GATE_OR, NodeType.AGGREGATION}:
            return True  # gates always pass (children determine outcome)

        if node.node_type == NodeType.ACTION:
            return True   # always activate when reached

        if not node.signal_key:
            return True   # no condition → always pass

        value = self._resolve(node.signal_key, signals)
        if value is None:
            return False

        return self._compare(value, node.operator, node.threshold)

    def _resolve(self, key: str, signals: dict[str, Any]) -> Any:
        # Support dotted paths
        if "." in key:
            return signals.get(key)
        return signals.get(key)

    def _compare(self, value: Any, op: str, threshold: Any) -> bool:
        try:
            if op == ">":   return float(value) > float(threshold)
            if op == ">=":  return float(value) >= float(threshold)
            if op == "<":   return float(value) < float(threshold)
            if op == "<=":  return float(value) <= float(threshold)
            if op == "==":  return value == threshold
            if op == "!=":  return value != threshold
            if op == "in":  return value in threshold
            if op == "not_in": return value not in threshold
            if op == "bool":   return bool(value)
        except (TypeError, ValueError):
            pass
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Prebuilt decision trees
# ─────────────────────────────────────────────────────────────────────────────

def _n(node_id: str, node_type: NodeType, label: str, **kwargs) -> DecisionNode:
    return DecisionNode(node_id=node_id, node_type=node_type, label=label, **kwargs)


def _action(node_id: str, label: str, rec_type: RecommendationType,
             urgency: UrgencyLevel = UrgencyLevel.MEDIUM, score: float = 0.6) -> DecisionNode:
    return DecisionNode(node_id=node_id, node_type=NodeType.ACTION, label=label,
                        rec_type=rec_type, urgency=urgency, base_score=score)


class DecisionTreeBuilder:
    """
    Builds the full ICI Decision Graph by assembling sub-trees:
      1. Supplier Risk Tree
      2. Price Signal Tree
      3. Material Cost Tree
      4. MOQ Optimization Tree
      5. Inventory / Timing Tree
    """

    def build(self) -> DecisionGraph:
        g = DecisionGraph()

        # ── Root gate ────────────────────────────────────────────────────────
        g.add_node(_n("root", NodeType.GATE_OR, "ICI Decision Root"))

        # ── Supplier risk sub-tree ────────────────────────────────────────────
        self._build_supplier_tree(g)
        # ── Price / market sub-tree ───────────────────────────────────────────
        self._build_price_tree(g)
        # ── Material cost sub-tree ────────────────────────────────────────────
        self._build_material_cost_tree(g)
        # ── MOQ sub-tree ─────────────────────────────────────────────────────
        self._build_moq_tree(g)
        # ── Inventory / timing sub-tree ───────────────────────────────────────
        self._build_inventory_tree(g)

        # Connect sub-trees to root
        g.set_roots("root")
        for sub in ["sup_root", "price_root", "matcost_root", "moq_root", "inv_root"]:
            g.add_edge("root", sub)

        return g

    # ── Supplier risk ─────────────────────────────────────────────────────────

    def _build_supplier_tree(self, g: DecisionGraph) -> None:
        g.add_node(_n("sup_root", NodeType.GATE_OR, "Supplier Risk Gate"))

        # High composite risk → change supplier
        g.add_node(_n("sup_high_risk", NodeType.THRESHOLD, "Composite Risk > 0.6",
                       signal_key="supplier.composite_risk", operator=">", threshold=0.6))
        g.add_node(_action("act_change_supplier", "Zmień dostawcę",
                            RecommendationType.CHANGE_SUPPLIER, UrgencyLevel.HIGH, 0.85))
        g.add_edge("sup_root", "sup_high_risk")
        g.add_edge("sup_high_risk", "act_change_supplier")

        # Single source → dual source
        g.add_node(_n("sup_single", NodeType.THRESHOLD, "Single Source = True",
                       signal_key="supplier.single_source", operator=">=", threshold=1.0))
        g.add_node(_action("act_dual_source", "Wdroż dual-sourcing",
                            RecommendationType.DUAL_SOURCE, UrgencyLevel.MEDIUM, 0.75))
        g.add_edge("sup_root", "sup_single")
        g.add_edge("sup_single", "act_dual_source")

        # No alternatives + medium risk → qualify
        g.add_node(_n("sup_no_alt", NodeType.GATE_AND, "No Alternatives Gate"))
        g.add_node(_n("sup_no_alt_chk", NodeType.THRESHOLD, "Alternatives == 0",
                       signal_key="supplier.alternative_count", operator="<=", threshold=0.0))
        g.add_node(_n("sup_med_risk", NodeType.THRESHOLD, "Risk 0.3–0.6",
                       signal_key="supplier.composite_risk", operator=">=", threshold=0.3))
        g.add_node(_action("act_qualify", "Kwalifikuj nowego dostawcę",
                            RecommendationType.QUALIFY_ALTERNATIVE, UrgencyLevel.MEDIUM, 0.70))
        g.add_edge("sup_root",       "sup_no_alt")
        g.add_edge("sup_no_alt",     "sup_no_alt_chk")
        g.add_edge("sup_no_alt",     "sup_med_risk")
        g.add_edge("sup_no_alt",     "act_qualify")

        # OTD < 90% → renegotiate SLA
        g.add_node(_n("sup_otd_low", NodeType.THRESHOLD, "OTD < 90%",
                       signal_key="supplier.otd_pct", operator="<", threshold=90.0))
        g.add_node(_action("act_renegotiate", "Renegocjuj umowę SLA",
                            RecommendationType.RENEGOTIATE, UrgencyLevel.MEDIUM, 0.65))
        g.add_edge("sup_root", "sup_otd_low")
        g.add_edge("sup_otd_low", "act_renegotiate")

        # Contract expiry < 90 days → renegotiate
        g.add_node(_n("sup_contract_exp", NodeType.THRESHOLD, "Contract < 90 days",
                       signal_key="supplier.contract_expiry_days", operator="<", threshold=90.0))
        g.add_node(_action("act_renegotiate_contract", "Renegocjuj kontrakt",
                            RecommendationType.RENEGOTIATE, UrgencyLevel.HIGH, 0.72))
        g.add_edge("sup_root", "sup_contract_exp")
        g.add_edge("sup_contract_exp", "act_renegotiate_contract")

        # Price increase > 10% → escalate
        g.add_node(_n("sup_price_spike", NodeType.THRESHOLD, "Price Delta > 10%",
                       signal_key="supplier.price_delta_pct", operator=">", threshold=10.0))
        g.add_node(_action("act_escalate", "Eskaluj do zarządu",
                            RecommendationType.ESCALATE, UrgencyLevel.CRITICAL, 0.90))
        g.add_edge("sup_root", "sup_price_spike")
        g.add_edge("sup_price_spike", "act_escalate")

    # ── Price signal ──────────────────────────────────────────────────────────

    def _build_price_tree(self, g: DecisionGraph) -> None:
        g.add_node(_n("price_root", NodeType.GATE_OR, "Price Signal Gate"))

        # Rising trend + high disruption → buy now
        g.add_node(_n("price_buy_gate", NodeType.GATE_AND, "Buy Now Gate"))
        g.add_node(_n("price_rising", NodeType.THRESHOLD, "Trend Rising",
                       signal_key="market.trend_rising", operator=">=", threshold=1.0))
        g.add_node(_n("price_disruption", NodeType.THRESHOLD, "Disruption Risk > 0.3",
                       signal_key="market.supply_disruption_risk", operator=">", threshold=0.3))
        g.add_node(_action("act_buy_now", "Kup teraz (forward buy)",
                            RecommendationType.BUY_NOW, UrgencyLevel.HIGH, 0.80))
        g.add_edge("price_root",       "price_buy_gate")
        g.add_edge("price_buy_gate",   "price_rising")
        g.add_edge("price_buy_gate",   "price_disruption")
        g.add_edge("price_buy_gate",   "act_buy_now")

        # Falling trend → wait
        g.add_node(_n("price_falling", NodeType.THRESHOLD, "Trend Falling",
                       signal_key="market.trend_falling", operator=">=", threshold=1.0))
        g.add_node(_n("price_falling_str", NodeType.THRESHOLD, "Strength > 0.4",
                       signal_key="market.trend_strength", operator=">", threshold=0.4))
        g.add_node(_action("act_wait", "Poczekaj na lepszą cenę",
                            RecommendationType.WAIT, UrgencyLevel.LOW, 0.70))
        g.add_edge("price_root",         "price_falling")
        g.add_edge("price_falling",      "price_falling_str")
        g.add_edge("price_falling_str",  "act_wait")

        # FX weakening → hedge
        g.add_node(_n("price_fx_weak", NodeType.THRESHOLD, "FX Weakening",
                       signal_key="market.fx_weakening", operator=">=", threshold=1.0))
        g.add_node(_action("act_hedge_fx", "Zabezpiecz kurs FX",
                            RecommendationType.HEDGE_FX, UrgencyLevel.MEDIUM, 0.75))
        g.add_edge("price_root",  "price_fx_weak")
        g.add_edge("price_fx_weak", "act_hedge_fx")

        # High volatility → increase stock
        g.add_node(_n("price_vol_high", NodeType.THRESHOLD, "Volatility > 0.15",
                       signal_key="market.price_volatility_30d", operator=">", threshold=0.15))
        g.add_node(_action("act_increase_stock", "Zwiększ zapasy buforowe",
                            RecommendationType.INCREASE_STOCK, UrgencyLevel.MEDIUM, 0.65))
        g.add_edge("price_root",     "price_vol_high")
        g.add_edge("price_vol_high", "act_increase_stock")

        # Low volatility + falling → reduce stock
        g.add_node(_n("price_reduce_gate", NodeType.GATE_AND, "Reduce Stock Gate"))
        g.add_node(_n("price_low_vol", NodeType.THRESHOLD, "Volatility < 0.05",
                       signal_key="market.price_volatility_30d", operator="<", threshold=0.05))
        g.add_node(_n("price_falling2", NodeType.THRESHOLD, "Trend Falling (reduce)",
                       signal_key="market.trend_falling", operator=">=", threshold=1.0))
        g.add_node(_action("act_reduce_stock", "Zredukuj zapasy",
                            RecommendationType.REDUCE_STOCK, UrgencyLevel.LOW, 0.55))
        g.add_edge("price_root",        "price_reduce_gate")
        g.add_edge("price_reduce_gate", "price_low_vol")
        g.add_edge("price_reduce_gate", "price_falling2")
        g.add_edge("price_reduce_gate", "act_reduce_stock")

    # ── Material cost ─────────────────────────────────────────────────────────

    def _build_material_cost_tree(self, g: DecisionGraph) -> None:
        g.add_node(_n("matcost_root", NodeType.GATE_OR, "Material Cost Gate"))

        # Above benchmark → change material
        g.add_node(_n("mat_above_bench", NodeType.THRESHOLD, "Cost > Benchmark 10%",
                       signal_key="cost.vs_benchmark_pct", operator=">", threshold=10.0))
        g.add_node(_action("act_change_material", "Zmień materiał",
                            RecommendationType.CHANGE_MATERIAL, UrgencyLevel.MEDIUM, 0.72))
        g.add_edge("matcost_root",    "mat_above_bench")
        g.add_edge("mat_above_bench", "act_change_material")

        # Above target → change process
        g.add_node(_n("mat_above_target", NodeType.THRESHOLD, "Cost > Target 5%",
                       signal_key="cost.vs_target_pct", operator=">", threshold=5.0))
        g.add_node(_action("act_change_process", "Optymalizuj proces",
                            RecommendationType.CHANGE_PROCESS, UrgencyLevel.MEDIUM, 0.68))
        g.add_edge("matcost_root",      "mat_above_target")
        g.add_edge("mat_above_target",  "act_change_process")

        # Rising cost trend > 5% → renegotiate
        g.add_node(_n("mat_cost_rising", NodeType.THRESHOLD, "Cost Trend > 5%",
                       signal_key="cost.cost_trend_pct", operator=">", threshold=5.0))
        g.add_node(_action("act_renegotiate_price", "Renegocjuj cenę z dostawcą",
                            RecommendationType.RENEGOTIATE, UrgencyLevel.HIGH, 0.78))
        g.add_edge("matcost_root",   "mat_cost_rising")
        g.add_edge("mat_cost_rising","act_renegotiate_price")

    # ── MOQ optimization ──────────────────────────────────────────────────────

    def _build_moq_tree(self, g: DecisionGraph) -> None:
        g.add_node(_n("moq_root", NodeType.GATE_OR, "MOQ Gate"))

        # Volume >> MOQ (ratio > 5) → increase MOQ for discount
        g.add_node(_n("moq_high_vol", NodeType.THRESHOLD, "Volume/MOQ > 5",
                       signal_key="supplier.volume_moq_ratio", operator=">", threshold=5.0))
        g.add_node(_action("act_increase_moq", "Zwiększ MOQ dla rabatu wolumenowego",
                            RecommendationType.INCREASE_MOQ, UrgencyLevel.LOW, 0.60))
        g.add_edge("moq_root",   "moq_high_vol")
        g.add_edge("moq_high_vol","act_increase_moq")

        # Volume ≈ MOQ (ratio < 1.5) → decrease MOQ to reduce cash lock-up
        g.add_node(_n("moq_low_vol", NodeType.THRESHOLD, "Volume/MOQ < 1.5",
                       signal_key="supplier.volume_moq_ratio", operator="<", threshold=1.5))
        g.add_node(_action("act_decrease_moq", "Zmniejsz MOQ / negocjuj elastyczność",
                            RecommendationType.DECREASE_MOQ, UrgencyLevel.MEDIUM, 0.65))
        g.add_edge("moq_root",   "moq_low_vol")
        g.add_edge("moq_low_vol","act_decrease_moq")

    # ── Inventory / timing ────────────────────────────────────────────────────

    def _build_inventory_tree(self, g: DecisionGraph) -> None:
        g.add_node(_n("inv_root", NodeType.GATE_OR, "Inventory Gate"))

        # Below safety stock → buy now (urgent)
        g.add_node(_n("inv_below_ss", NodeType.THRESHOLD, "Below Safety Stock",
                       signal_key="production.below_safety_stock", operator=">=", threshold=1.0))
        g.add_node(_action("act_buy_now_urgent", "Kup NATYCHMIAST (poniżej safety stock)",
                            RecommendationType.BUY_NOW, UrgencyLevel.CRITICAL, 0.95))
        g.add_edge("inv_root",      "inv_below_ss")
        g.add_edge("inv_below_ss",  "act_buy_now_urgent")

        # Near reorder + rising market → buy now
        g.add_node(_n("inv_reorder_gate", NodeType.GATE_AND, "Reorder + Rising Gate"))
        g.add_node(_n("inv_near_reorder", NodeType.THRESHOLD, "Near Reorder Point",
                       signal_key="production.near_reorder", operator=">=", threshold=1.0))
        g.add_node(_n("inv_market_rising", NodeType.THRESHOLD, "Market Rising (inv)",
                       signal_key="market.trend_rising", operator=">=", threshold=1.0))
        g.add_node(_action("act_buy_now_reorder", "Kup teraz (punkt reorder + rosnące ceny)",
                            RecommendationType.BUY_NOW, UrgencyLevel.HIGH, 0.82))
        g.add_edge("inv_root",         "inv_reorder_gate")
        g.add_edge("inv_reorder_gate", "inv_near_reorder")
        g.add_edge("inv_reorder_gate", "inv_market_rising")
        g.add_edge("inv_reorder_gate", "act_buy_now_reorder")

        # High inventory + falling market → reduce stock / wait
        g.add_node(_n("inv_high_stock", NodeType.GATE_AND, "High Stock + Falling Gate"))
        g.add_node(_n("inv_days_high", NodeType.THRESHOLD, "Inventory > 60 days",
                       signal_key="production.inventory_days", operator=">", threshold=60.0))
        g.add_node(_n("inv_falling", NodeType.THRESHOLD, "Market Falling (inv)",
                       signal_key="market.trend_falling", operator=">=", threshold=1.0))
        g.add_node(_action("act_reduce_stock2", "Zredukuj zapasy (ceny spadają)",
                            RecommendationType.REDUCE_STOCK, UrgencyLevel.LOW, 0.60))
        g.add_edge("inv_root",      "inv_high_stock")
        g.add_edge("inv_high_stock","inv_days_high")
        g.add_edge("inv_high_stock","inv_falling")
        g.add_edge("inv_high_stock","act_reduce_stock2")
