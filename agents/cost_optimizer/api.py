"""
Cost Optimizer API — FastAPI router for autonomous cost structure optimization.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from .models import (
    BOM, BOMItem, BOMAlternative, ProcessRouting, ProcessStep,
    ProcessAlternative, SupplierProfile, Constraint, ConstraintType,
    OptimizationAlgorithm, ObjectiveType, OptimizationResult,
    CostBreakdown, OptimizeRequestIn, MaterialClass, ProcessType, SupplierTier,
)
from .constraint_engine import default_constraints
from .dashboard import DashboardAssembler
from .monitoring import optimizer_health_check, prometheus_response, record_run

log = logging.getLogger(__name__)
router = APIRouter(prefix="/cost-optimizer", tags=["Cost Optimizer"])

_results:   dict[str, OptimizationResult]  = {}
_dashboard  = DashboardAssembler()


# ── converters ────────────────────────────────────────────────────────────────

def _bom_from_req(req: OptimizeRequestIn) -> BOM:
    bom = BOM(product_id=req.bom.product_id, product_name=req.bom.product_name,
              annual_volume=req.annual_volume)
    for it in req.bom.items:
        item = BOMItem(
            item_id=it.item_id, description=it.description,
            quantity=it.quantity, unit=it.unit,
            required_quality_index=it.required_quality_index,
            function_critical=it.function_critical,
        )
        for a in it.alternatives:
            item.alternatives.append(BOMAlternative(
                alt_id=a.alt_id, material_name=a.material_name,
                material_class=MaterialClass(a.material_class),
                unit_cost_eur=a.unit_cost_eur,
                quality_index=a.quality_index,
                co2_kg_per_kg=a.co2_kg_per_kg,
                availability=a.availability,
                lead_time_days=a.lead_time_days,
                moq=a.moq,
                supplier_ids=list(a.supplier_ids),
            ))
        if item.alternatives:
            item.current_alt_id = item.alternatives[0].alt_id
        bom.items.append(item)
    return bom


def _routing_from_req(req: OptimizeRequestIn) -> ProcessRouting | None:
    if not req.routing:
        return None
    routing = ProcessRouting(product_id=req.routing.product_id)
    for s in req.routing.steps:
        step = ProcessStep(step_id=s.step_id, description=s.description)
        for a in s.alternatives:
            step.alternatives.append(ProcessAlternative(
                proc_id=a.proc_id,
                process_type=ProcessType(a.process_type),
                description=a.description,
                cycle_time_s=a.cycle_time_s,
                labor_cost_eur_h=a.labor_cost_eur_h,
                machine_cost_eur_h=a.machine_cost_eur_h,
                scrap_rate_pct=a.scrap_rate_pct,
                quality_index=a.quality_index,
                min_volume=a.min_volume,
                max_volume=a.max_volume,
            ))
        if step.alternatives:
            step.current_proc_id = step.alternatives[0].proc_id
        routing.steps.append(step)
    return routing


def _suppliers_from_req(req: OptimizeRequestIn) -> dict[str, SupplierProfile]:
    sups = {}
    for s in req.suppliers:
        sups[s.supplier_id] = SupplierProfile(
            supplier_id=s.supplier_id, name=s.name, country=s.country,
            tier=SupplierTier(s.tier),
            otd_pct=s.otd_pct, defect_rate_pct=s.defect_rate_pct,
            lead_time_days=s.lead_time_days,
            financial_risk=s.financial_risk, geo_risk=s.geo_risk,
            price_stability=s.price_stability,
        )
    return sups


def _constraints_from_req(req: OptimizeRequestIn) -> list[Constraint]:
    base = default_constraints()
    for c in req.constraints:
        base.append(Constraint(
            constraint_type=ConstraintType(c.constraint_type),
            name=c.name,
            signal=c.signal,
            operator=c.operator,
            threshold=c.threshold,
            penalty_weight=c.penalty_weight,
        ))
    return base


def _run_optimization(req: OptimizeRequestIn, result_id: str) -> None:
    t0 = time.time()
    try:
        from .optimization_engine import OptimizationEngine
        from .multi_objective import MultiObjectiveOptimizer as MOO
        from .tradeoff_engine import CostQualityAnalyzer, RiskCostAnalyzer
        from .explainability import ExplainabilityEngine

        bom       = _bom_from_req(req)
        routing   = _routing_from_req(req)
        suppliers = _suppliers_from_req(req)
        constraints = _constraints_from_req(req)
        objectives = list(req.objectives)
        obj_weights = list(req.objective_weights)
        if len(obj_weights) < len(objectives):
            obj_weights += [1.0] * (len(objectives) - len(obj_weights))

        engine = OptimizationEngine(
            bom=bom, routing=routing, suppliers=suppliers,
            constraints=constraints, objectives=objectives,
            objective_weights=obj_weights, annual_volume=req.annual_volume,
        )
        run = engine.run(
            algorithm=OptimizationAlgorithm(req.algorithm),
            population_size=req.population_size,
            generations=req.generations,
            mutation_rate=req.mutation_rate,
        )

        # Multi-objective post-processing
        moo = MOO(objectives=objectives)
        pareto_front, tradeoffs = moo.process(run.all_candidates)
        run.pareto_front = pareto_front

        best = run.best_candidate
        baseline = engine.baseline
        saving = baseline.total_cost_eur - (best.total_cost_eur if best else 0)
        saving_pct = saving / baseline.total_cost_eur * 100 if baseline.total_cost_eur else 0

        # Breakdown
        breakdown = engine.evaluator.cost_breakdown(best) if best else None

        # Explanation
        explainer = ExplainabilityEngine()
        explanation = explainer.explain(
            baseline, best, engine.space, engine.evaluator,
            bom, routing, annual_volume=req.annual_volume,
        ) if best else None

        result = OptimizationResult(
            result_id=result_id,
            product_id=req.product_id,
            run=run,
            best=best,
            baseline_cost_eur=round(baseline.total_cost_eur, 4),
            best_cost_eur=round(best.total_cost_eur if best else 0, 4),
            saving_eur=round(saving, 4),
            saving_pct=round(saving_pct, 2),
            tradeoffs=tradeoffs,
            cost_breakdown=breakdown,
            explanation=explanation.summary if explanation else [],
            recommendations=_generate_recs(best, baseline, explanation),
        )
        _results[result_id] = result

        record_run(
            product_id=req.product_id,
            algorithm=req.algorithm.value if hasattr(req.algorithm, "value") else str(req.algorithm),
            status="ok",
            duration_s=time.time() - t0,
            saving_eur=saving,
            saving_pct=saving_pct,
            n_evaluated=run.n_evaluated,
            n_feasible=run.n_feasible,
            best_cost=best.total_cost_eur if best else 0,
            best_quality=best.quality_index if best else 0,
            best_risk=best.risk_score if best else 0,
        )

    except Exception as e:
        log.exception("Optimization run %s failed", result_id)
        _results[result_id] = OptimizationResult(
            result_id=result_id, product_id=req.product_id,
            explanation=[f"ERROR: {e}"],
        )
        record_run(req.product_id, str(req.algorithm), "error", time.time() - t0)


def _generate_recs(best, baseline, explanation) -> list[str]:
    recs = []
    if best and baseline:
        if best.total_cost_eur < baseline.total_cost_eur:
            recs.append(f"Wdrożyć optymalną konfigurację — oszczędność {baseline.total_cost_eur - best.total_cost_eur:.4f} EUR/szt.")
        if best.risk_score > 0.5:
            recs.append("Zidentyfikować alternatywnych dostawców — ryzyko kompozytowe > 0.5.")
        if best.delivery_days > 45:
            recs.append("Rozważyć dostawców z krótszym czasem dostawy (>45 dni ryzyko braków).")
    if explanation and explanation.sensitivity:
        top = explanation.sensitivity[0]
        recs.append(f"Priorytetyzować negocjacje w obszarze '{top.label}' (wpływ {top.pct_impact:.1f}% kosztu).")
    return recs


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/optimize")
async def optimize(req: OptimizeRequestIn, bg: BackgroundTasks):
    """Submit optimization task (runs async in background)."""
    result_id = str(uuid.uuid4())
    _results[result_id] = OptimizationResult(result_id=result_id, product_id=req.product_id)
    bg.add_task(_run_optimization, req, result_id)
    return {"result_id": result_id, "status": "running", "product_id": req.product_id}


@router.get("/optimize/{result_id}")
async def get_result(result_id: str):
    result = _results.get(result_id)
    if not result:
        raise HTTPException(404, "Result not found")
    if not result.run:
        return {"result_id": result_id, "status": "running"}
    best = result.best
    return {
        "result_id":      result_id,
        "status":         "done",
        "product_id":     result.product_id,
        "baseline_cost":  result.baseline_cost_eur,
        "best_cost":      result.best_cost_eur,
        "saving_eur":     result.saving_eur,
        "saving_pct":     result.saving_pct,
        "best": {
            "choices":       best.choices if best else {},
            "quality_index": best.quality_index if best else 0,
            "risk_score":    best.risk_score if best else 0,
            "co2_kg":        best.co2_kg if best else 0,
            "delivery_days": best.delivery_days if best else 0,
            "feasible":      best.feasible if best else False,
            "violations":    len(best.violations) if best else 0,
        } if best else None,
        "run": {
            "algorithm":     result.run.algorithm.value,
            "n_evaluated":   result.run.n_evaluated,
            "n_feasible":    result.run.n_feasible,
            "n_generations": result.run.n_generations,
            "run_time_s":    result.run.run_time_s,
        } if result.run else None,
        "explanation":    result.explanation,
        "recommendations": result.recommendations,
    }


@router.get("/optimize/{result_id}/pareto")
async def get_pareto(result_id: str, top_n: int = Query(20, le=100)):
    result = _results.get(result_id)
    if not result or not result.run:
        raise HTTPException(404, "Result not found or not ready")
    pareto = sorted(result.run.pareto_front, key=lambda c: c.total_cost_eur)[:top_n]
    return {
        "pareto_front": [
            {
                "id":           c.candidate_id[:12],
                "cost_eur":     c.total_cost_eur,
                "risk_score":   c.risk_score,
                "quality_index": c.quality_index,
                "co2_kg":       c.co2_kg,
                "choices":      c.choices,
                "pareto_rank":  c.pareto_rank,
            }
            for c in pareto
        ],
        "n_total": len(result.run.pareto_front),
    }


@router.get("/optimize/{result_id}/tradeoffs")
async def get_tradeoffs(result_id: str):
    result = _results.get(result_id)
    if not result or not result.run:
        raise HTTPException(404, "Result not found or not ready")
    out = {}
    for axis, curve in (result.tradeoffs or {}).items():
        out[axis] = {
            "pareto_points": [{"x": p.x_value, "y": p.y_value, "id": p.config_id[:8]} for p in curve.pareto_points],
            "knee_point":    {"x": curve.knee_point.x_value, "y": curve.knee_point.y_value} if curve.knee_point else None,
            "n_total":       len(curve.points),
        }
    return out


@router.get("/optimize/{result_id}/breakdown")
async def get_breakdown(result_id: str):
    result = _results.get(result_id)
    if not result or not result.cost_breakdown:
        raise HTTPException(404, "Result not found or not ready")
    bd = result.cost_breakdown
    return {
        "material_eur":   bd.material_eur,
        "process_eur":    bd.process_eur,
        "logistics_eur":  bd.logistics_eur,
        "overhead_eur":   bd.overhead_eur,
        "quality_eur":    bd.quality_eur,
        "tooling_eur":    bd.tooling_eur,
        "total_eur":      bd.total_eur,
    }


@router.get("/optimize/{result_id}/dashboard")
async def get_dashboard(result_id: str):
    result = _results.get(result_id)
    if not result or not result.run:
        raise HTTPException(404, "Result not found or not ready")
    return _dashboard.build(result)


@router.post("/optimize/{result_id}/sync")
async def optimize_sync(req: OptimizeRequestIn):
    """Synchronous optimization — runs in-process (for small search spaces)."""
    result_id = str(uuid.uuid4())
    _run_optimization(req, result_id)
    return await get_result(result_id)


@router.get("/results")
async def list_results(limit: int = Query(20, le=100)):
    items = list(_results.values())[-limit:]
    return {
        "results": [
            {
                "result_id":  r.result_id,
                "product_id": r.product_id,
                "saving_eur": r.saving_eur,
                "saving_pct": r.saving_pct,
                "status":     "done" if r.run else "running",
            }
            for r in items
        ],
        "total": len(_results),
    }


@router.delete("/optimize/{result_id}")
async def delete_result(result_id: str):
    if result_id not in _results:
        raise HTTPException(404, "Result not found")
    del _results[result_id]
    return {"status": "deleted", "result_id": result_id}


@router.get("/health")
async def health():
    return optimizer_health_check()


@router.get("/metrics")
async def metrics():
    body, ct = prometheus_response()
    return Response(content=body, media_type=ct)
