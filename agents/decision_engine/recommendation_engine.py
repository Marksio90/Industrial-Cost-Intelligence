"""
Recommendation Engine — traverses decision graph, produces ranked Recommendation objects.
"""
from __future__ import annotations
import uuid, time, logging
from dataclasses import dataclass, field
from typing import Any
from .models import (
    DecisionContext, DecisionNode, Recommendation, RecommendationType,
    UrgencyLevel, ConfidenceScore, ConfidenceLevel, ImpactEstimate,
    ImpactDimension, ActionParameter, Explanation, Evidence, EvidenceType,
    DataQuality,
)
from .decision_graph import DecisionGraph, GraphTraverser, SignalExtractor, TraversalResult

log = logging.getLogger(__name__)

_URGENCY_FACTOR = {
    UrgencyLevel.CRITICAL: 1.4,
    UrgencyLevel.HIGH: 1.2,
    UrgencyLevel.MEDIUM: 1.0,
    UrgencyLevel.LOW: 0.85,
    UrgencyLevel.WATCH: 0.70,
}

_REC_TITLES = {
    RecommendationType.CHANGE_SUPPLIER: "Zmień dostawcę",
    RecommendationType.INCREASE_MOQ: "Zwiększ MOQ",
    RecommendationType.DECREASE_MOQ: "Zmniejsz MOQ",
    RecommendationType.CHANGE_MATERIAL: "Zmień materiał",
    RecommendationType.CHANGE_PROCESS: "Zmień proces produkcji",
    RecommendationType.BUY_NOW: "Kup teraz",
    RecommendationType.WAIT: "Poczekaj z zakupem",
    RecommendationType.HEDGE_FX: "Zabezpiecz ryzyko walutowe",
    RecommendationType.DUAL_SOURCE: "Wprowadź dual-sourcing",
    RecommendationType.RENEGOTIATE: "Renegocjuj kontrakt",
    RecommendationType.INCREASE_STOCK: "Zwiększ zapasy",
    RecommendationType.REDUCE_STOCK: "Zmniejsz zapasy",
    RecommendationType.QUALIFY_ALTERNATIVE: "Kwalifikuj alternatywnego dostawcę",
    RecommendationType.ESCALATE: "Eskaluj do zarządu",
    RecommendationType.NO_ACTION: "Brak działania — monitoruj",
}

def _estimate_saving(rec_type: RecommendationType, signals: dict[str, Any], ctx: DecisionContext) -> tuple[float, float]:
    """Returns (saving_eur, saving_pct)."""
    annual_spend = 0.0
    if ctx.cost and ctx.cost.current_unit_cost:
        vol = ctx.production.annual_volume if ctx.production else 1000
        annual_spend = ctx.cost.current_unit_cost * vol

    if rec_type == RecommendationType.CHANGE_SUPPLIER:
        pct = min(signals.get("supplier.price_delta_pct", 0.05), 0.30)
        return annual_spend * pct, pct * 100
    if rec_type == RecommendationType.INCREASE_MOQ:
        return annual_spend * 0.04, 4.0
    if rec_type == RecommendationType.DECREASE_MOQ:
        return annual_spend * 0.02, 2.0
    if rec_type == RecommendationType.CHANGE_MATERIAL:
        pct = max(0, signals.get("cost.vs_benchmark_pct", 10) / 100 * 0.6)
        return annual_spend * pct, pct * 100
    if rec_type == RecommendationType.CHANGE_PROCESS:
        return annual_spend * 0.06, 6.0
    if rec_type == RecommendationType.BUY_NOW:
        volatility = signals.get("market.volatility_30d", 0.1)
        return annual_spend * volatility * 0.5, volatility * 50
    if rec_type == RecommendationType.WAIT:
        trend = abs(signals.get("market.trend_strength", 0.2))
        return annual_spend * trend * 0.3, trend * 30
    if rec_type == RecommendationType.HEDGE_FX:
        return annual_spend * 0.03, 3.0
    if rec_type == RecommendationType.RENEGOTIATE:
        return annual_spend * 0.05, 5.0
    if rec_type == RecommendationType.DUAL_SOURCE:
        return annual_spend * 0.02, 2.0
    return 0.0, 0.0

def _build_parameters(rec_type: RecommendationType, signals: dict[str, Any], ctx: DecisionContext) -> list[ActionParameter]:
    params = []
    if rec_type == RecommendationType.CHANGE_SUPPLIER and ctx.supplier:
        params.append(ActionParameter(key="current_supplier", value=ctx.supplier.supplier_id, unit="id", description="Obecny dostawca"))
        params.append(ActionParameter(key="alternative_count", value=ctx.supplier.alternative_count, unit="szt", description="Dostępne alternatywy"))
    if rec_type == RecommendationType.BUY_NOW and ctx.market:
        params.append(ActionParameter(key="spot_price", value=ctx.market.spot_price_eur, unit="EUR", description="Cena spot"))
        params.append(ActionParameter(key="futures_price", value=ctx.market.futures_price_eur, unit="EUR", description="Cena futures"))
    if rec_type in (RecommendationType.INCREASE_MOQ, RecommendationType.DECREASE_MOQ) and ctx.supplier:
        params.append(ActionParameter(key="current_moq", value=ctx.supplier.moq, unit="szt", description="Obecne MOQ"))
        ratio = signals.get("supplier.volume_moq_ratio", 1.0)
        params.append(ActionParameter(key="volume_moq_ratio", value=round(ratio, 2), unit="x", description="Stosunek wolumenu do MOQ"))
    return params

def _node_to_recommendation(node: DecisionNode, ctx: DecisionContext, signals: dict[str, Any], conf: ConfidenceScore) -> Recommendation:
    saving_eur, saving_pct = _estimate_saving(node.rec_type, signals, ctx)
    impl_cost = saving_eur * 0.05
    payback = (impl_cost / saving_eur * 12) if saving_eur > 0 else 0.0
    roi = ((saving_eur - impl_cost) / impl_cost * 100) if impl_cost > 0 else 0.0

    risk_factors = []
    if node.urgency in (UrgencyLevel.CRITICAL, UrgencyLevel.HIGH):
        risk_factors.append("Wymaga szybkiej decyzji — opóźnienie zwiększa ekspozycję")
    if signals.get("supplier.geo_risk", 0) > 0.5:
        risk_factors.append("Wysokie ryzyko geopolityczne w regionie dostawcy")
    if signals.get("market.supply_disruption_risk", 0) > 0.4:
        risk_factors.append("Ryzyko zakłócenia łańcucha dostaw")

    risk_level = "NISKIE"
    if node.urgency == UrgencyLevel.CRITICAL:
        risk_level = "KRYTYCZNE"
    elif node.urgency == UrgencyLevel.HIGH:
        risk_level = "WYSOKIE"
    elif node.urgency == UrgencyLevel.MEDIUM:
        risk_level = "ŚREDNIE"

    return Recommendation(
        rec_id=str(uuid.uuid4()),
        context_id=ctx.context_id,
        tenant_id=ctx.tenant_id,
        created_at=time.time(),
        rec_type=node.rec_type,
        title=_REC_TITLES.get(node.rec_type, str(node.rec_type)),
        urgency=node.urgency,
        confidence=conf,
        explanation=None,
        parameters=_build_parameters(node.rec_type, signals, ctx),
        target_id=ctx.supplier.supplier_id if ctx.supplier else ctx.cost.material_id if ctx.cost else "",
        expected_saving_eur=round(saving_eur, 2),
        expected_saving_pct=round(saving_pct, 2),
        implementation_cost_eur=round(impl_cost, 2),
        payback_months=round(payback, 1),
        roi_pct=round(roi, 1),
        risk_level=risk_level,
        risk_factors=risk_factors,
        score=0.0,
        rank=0,
        tags=ctx.tags,
    )

@dataclass
class EngineResult:
    context_id: str
    recommendations: list[Recommendation]
    traversal: TraversalResult
    signals: dict[str, Any]
    run_time_ms: float
    n_nodes_activated: int
    n_recs_raw: int
    n_recs_final: int

class RecommendationEngine:
    def __init__(self, graph: DecisionGraph | None = None):
        from .decision_graph import DecisionTreeBuilder
        self._graph = graph or DecisionTreeBuilder().build()
        self._traverser = GraphTraverser()
        self._extractor = SignalExtractor()

    def analyze(self, ctx: DecisionContext, top_n: int = 5, min_confidence: float = 0.3) -> EngineResult:
        t0 = time.time()
        signals = self._extractor.extract(ctx)
        traversal = self._traverser.evaluate(self._graph, signals)

        raw: list[Recommendation] = []
        for node in traversal.action_nodes:
            if node.rec_type is None:
                continue
            conf = self._compute_confidence(node, signals, ctx, traversal)
            rec = _node_to_recommendation(node, ctx, signals, conf)
            uf = _URGENCY_FACTOR.get(node.urgency, 1.0)
            rec.score = round(node.base_score * conf.overall * uf, 4)
            raw.append(rec)

        # dedup: per rec_type keep highest score
        best: dict[RecommendationType, Recommendation] = {}
        for r in raw:
            if r.rec_type not in best or r.score > best[r.rec_type].score:
                best[r.rec_type] = r

        ranked = sorted(best.values(), key=lambda r: r.score, reverse=True)
        filtered = [r for r in ranked if r.confidence.overall >= min_confidence]

        if not filtered:
            filtered = [self._no_action_rec(ctx)]

        for i, r in enumerate(filtered[:top_n], 1):
            r.rank = i

        return EngineResult(
            context_id=ctx.context_id,
            recommendations=filtered[:top_n],
            traversal=traversal,
            signals=signals,
            run_time_ms=round((time.time() - t0) * 1000, 2),
            n_nodes_activated=len(traversal.activated_nodes),
            n_recs_raw=len(raw),
            n_recs_final=len(filtered[:top_n]),
        )

    def _compute_confidence(self, node: DecisionNode, signals: dict[str, Any], ctx: DecisionContext, traversal: TraversalResult) -> ConfidenceScore:
        from .confidence_engine import ConfidenceEngine
        return ConfidenceEngine().compute(ctx, signals, node, traversal)

    def _no_action_rec(self, ctx: DecisionContext) -> Recommendation:
        conf = ConfidenceScore(
            overall=0.9, level=ConfidenceLevel.HIGH,
            data_quality=0.9, signal_strength=0.5,
            consensus=0.9, historical_accuracy=0.8,
            n_signals=0, n_conflicts=0, missing_data=[],
        )
        return Recommendation(
            rec_id=str(uuid.uuid4()),
            context_id=ctx.context_id,
            tenant_id=ctx.tenant_id,
            created_at=time.time(),
            rec_type=RecommendationType.NO_ACTION,
            title=_REC_TITLES[RecommendationType.NO_ACTION],
            urgency=UrgencyLevel.WATCH,
            confidence=conf,
            explanation=None,
            parameters=[],
            target_id="",
            expected_saving_eur=0.0,
            expected_saving_pct=0.0,
            implementation_cost_eur=0.0,
            payback_months=0.0,
            roi_pct=0.0,
            risk_level="NISKIE",
            risk_factors=[],
            score=0.5,
            rank=1,
            tags=ctx.tags,
        )
