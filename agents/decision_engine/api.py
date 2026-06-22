"""
Decision Engine API — FastAPI router for decision intelligence endpoints.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from .models import (
    CostContext, DataQuality, DecisionContext, DecisionRequestIn,
    MarketContext, OptimizeRequestIn, OutcomeFeedbackIn,
    ProductionContext, SupplierContext,
)
from .recommendation_engine import EngineResult, RecommendationEngine
from .explainability import ExplainabilityEngine
from .optimization_engine import MultiObjectiveOptimizer
from .dashboard import DashboardAssembler
from .monitoring import (
    decision_health_check, prometheus_response,
    record_analysis, record_feedback, record_optimization,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/decisions", tags=["Decision Engine"])

_results:    dict[str, EngineResult] = {}
_portfolios: dict[str, Any]          = {}
_feedback:   list[dict]              = []

_engine    = RecommendationEngine()
_explainer = ExplainabilityEngine()
_optimizer = MultiObjectiveOptimizer()
_dashboard = DashboardAssembler()


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_ctx(req: DecisionRequestIn) -> DecisionContext:
    cost = None
    if req.cost:
        c = req.cost
        cost = CostContext(
            material_id=c.material_id,
            current_unit_cost=c.current_unit_cost,
            target_unit_cost=c.target_unit_cost,
            cost_trend_pct=c.cost_trend_pct,
            benchmark_cost=c.benchmark_cost,
            currency=c.currency,
        )

    supplier = None
    if req.supplier:
        s = req.supplier
        supplier = SupplierContext(
            supplier_id=s.supplier_id,
            name=s.name,
            current_price_eur=s.current_price_eur,
            lead_time_days=s.lead_time_days,
            otd_pct=s.otd_pct,
            defect_rate_pct=s.defect_rate_pct,
            financial_risk=s.financial_risk,
            geo_risk=s.geo_risk,
            single_source=s.single_source,
            moq=s.moq,
            annual_volume=s.annual_volume,
            price_delta_pct=s.price_delta_pct,
            contract_expiry_days=s.contract_expiry_days,
            alternative_count=s.alternative_count,
        )

    market = None
    if req.market:
        m = req.market
        market = MarketContext(
            material_id=m.material_id,
            spot_price_eur=m.spot_price_eur,
            futures_price_eur=m.futures_price_eur,
            price_volatility_30d=m.price_volatility_30d,
            trend_direction=m.trend_direction,
            trend_strength=m.trend_strength,
            supply_disruption_risk=m.supply_disruption_risk,
            demand_index=m.demand_index,
            alternative_materials=m.alternative_materials or [],
            fx_eur_usd=m.fx_eur_usd,
            fx_trend=m.fx_trend,
        )

    production = None
    if req.production:
        p = req.production
        production = ProductionContext(
            product_id=p.product_id,
            annual_volume=p.annual_volume,
            current_inventory_days=p.current_inventory_days,
            safety_stock_days=p.safety_stock_days,
            production_rate_pct=p.production_rate_pct,
            scrap_rate_pct=p.scrap_rate_pct,
            demand_forecast_units=p.demand_forecast_units,
            demand_confidence=p.demand_confidence,
        )

    return DecisionContext(
        context_id=str(uuid.uuid4()),
        tenant_id=req.tenant_id or "default",
        cost=cost,
        supplier=supplier,
        market=market,
        production=production,
        risk_appetite=req.risk_appetite,
        cost_weight=req.cost_weight,
        risk_weight=req.risk_weight,
        quality_weight=req.quality_weight,
        delivery_weight=req.delivery_weight,
        tags=list(req.tags),
    )


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze(req: DecisionRequestIn):
    """Run decision analysis and return ranked recommendations."""
    ctx = _build_ctx(req)
    t0 = time.time()
    try:
        result = _engine.analyze(ctx, top_n=5, min_confidence=0.3)
        _results[result.context_id] = result
        record_analysis(ctx.tenant_id, "ok", result.run_time_ms, result.recommendations)
        return {
            "context_id": result.context_id,
            "recommendations": [
                {
                    "rec_id":              r.rec_id,
                    "rank":                r.rank,
                    "rec_type":            r.rec_type.value,
                    "title":               r.title,
                    "urgency":             r.urgency.value,
                    "score":               r.score,
                    "confidence":          r.confidence.overall,
                    "confidence_level":    r.confidence.level.value,
                    "expected_saving_eur": r.expected_saving_eur,
                    "expected_saving_pct": r.expected_saving_pct,
                    "roi_pct":             r.roi_pct,
                    "payback_months":      r.payback_months,
                    "risk_level":          r.risk_level,
                    "parameters": [
                        {"key": p.key, "value": p.value, "unit": p.unit}
                        for p in r.parameters
                    ],
                }
                for r in result.recommendations
            ],
            "meta": {
                "n_activated":  result.n_nodes_activated,
                "n_recs_raw":   result.n_recs_raw,
                "n_recs_final": result.n_recs_final,
                "run_time_ms":  result.run_time_ms,
            },
        }
    except Exception as e:
        record_analysis(ctx.tenant_id, "error", (time.time() - t0) * 1000)
        log.exception("analyze error")
        raise HTTPException(500, str(e))


@router.get("/result/{context_id}")
async def get_result(context_id: str):
    result = _results.get(context_id)
    if not result:
        raise HTTPException(404, "Context not found")
    return {
        "context_id":   context_id,
        "n_recs":       result.n_recs_final,
        "run_time_ms":  result.run_time_ms,
        "n_activated":  result.n_nodes_activated,
    }


@router.get("/explain/{context_id}/{rec_id}")
async def explain(context_id: str, rec_id: str):
    result = _results.get(context_id)
    if not result:
        raise HTTPException(404, "Context not found")
    rec = next((r for r in result.recommendations if r.rec_id == rec_id), None)
    if not rec:
        raise HTTPException(404, "Recommendation not found")

    # Build a minimal stub context for explainability (signals already extracted)
    class _StubCtx:
        def __init__(self):
            self.context_id = context_id
            self.tenant_id = "default"
            self.cost = self.supplier = self.market = self.production = None
            self.tags: list = []

    expl = _explainer.explain(rec, _StubCtx(), result.signals, result.traversal)
    rec.explanation = expl
    return {
        "rec_id":         rec_id,
        "summary":        expl.summary,
        "rationale":      expl.rationale,
        "impacts":        [
            {"dimension": i.dimension.value, "delta_eur": i.delta_eur,
             "delta_pct": i.delta_pct, "description": i.description}
            for i in expl.impacts
        ],
        "counterfactuals": [
            {"scenario": c.scenario, "cost_delta_eur": c.cost_delta_eur,
             "risk_delta": c.risk_delta, "probability": c.probability,
             "time_horizon_days": c.time_horizon_days}
            for c in expl.counterfactuals
        ],
        "assumptions": expl.assumptions,
        "caveats":     expl.caveats,
        "evidence":    [e.to_sentence() for e in expl.evidence],
        "similar_cases": expl.similar_cases,
    }


@router.post("/optimize/{context_id}")
async def optimize_portfolio(context_id: str, req: OptimizeRequestIn, bg: BackgroundTasks):
    result = _results.get(context_id)
    if not result:
        raise HTTPException(404, "Context not found")
    task_id = str(uuid.uuid4())

    def _run():
        portfolio = _optimizer.optimize(result.recommendations, budget_eur=500_000, max_recs=5)
        _portfolios[task_id] = portfolio
        record_optimization(context_id)

    bg.add_task(_run)
    return {"task_id": task_id, "context_id": context_id}


@router.get("/optimize/{task_id}/result")
async def get_portfolio(task_id: str):
    portfolio = _portfolios.get(task_id)
    if not portfolio:
        raise HTTPException(404, "Task not found or not ready")
    return {
        "total_saving_eur":     portfolio.total_saving_eur,
        "total_impl_cost_eur":  portfolio.total_impl_cost_eur,
        "net_benefit_eur":      portfolio.net_benefit_eur,
        "avg_confidence":       portfolio.avg_confidence,
        "selected": [
            {"rec_id": r.rec_id, "title": r.title, "saving_eur": r.expected_saving_eur}
            for r in portfolio.selected
        ],
        "pareto_frontier": [
            {"saving_eur": p.saving_eur, "risk_score": p.risk_score}
            for p in portfolio.pareto_frontier
        ],
        "run_time_ms": portfolio.run_time_ms,
    }


@router.post("/feedback")
async def submit_feedback(fb: OutcomeFeedbackIn):
    entry = {
        "rec_id":            fb.rec_id,
        "actual_saving_eur": fb.actual_saving_eur,
        "was_correct":       fb.was_correct,
        "feedback_notes":    fb.feedback_notes,
        "ts":                time.time(),
    }
    _feedback.append(entry)
    record_feedback("unknown", "accepted" if fb.was_correct else "rejected")
    return {"status": "ok", "feedback_count": len(_feedback)}


@router.get("/feedback/history")
async def feedback_history(limit: int = Query(50, le=500)):
    return {"items": _feedback[-limit:], "total": len(_feedback)}


@router.get("/dashboard/{context_id}")
async def dashboard(context_id: str):
    result = _results.get(context_id)
    if not result:
        raise HTTPException(404, "Context not found")
    portfolio = next(
        (p for p in _portfolios.values()
         if any(r.context_id == context_id for r in p.selected)),
        None,
    )
    return _dashboard.build(result, portfolio)


@router.get("/signals/{context_id}")
async def get_signals(context_id: str):
    result = _results.get(context_id)
    if not result:
        raise HTTPException(404, "Context not found")
    return {"context_id": context_id, "signals": result.signals}


@router.get("/graph")
async def get_decision_graph():
    from .decision_graph import DecisionTreeBuilder
    g = DecisionTreeBuilder().build()
    return g.to_dict()


@router.get("/health")
async def health():
    return decision_health_check()


@router.get("/metrics")
async def metrics():
    body, ct = prometheus_response()
    return Response(content=body, media_type=ct)
