"""Section 7: FastAPI router for the World Model."""
from __future__ import annotations
import time
import uuid
from typing import Any, Optional

try:
    from fastapi import APIRouter, BackgroundTasks, HTTPException
    from pydantic import BaseModel, Field
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    class BaseModel:  # type: ignore
        pass
    def Field(*a, **kw):  # type: ignore
        return None

from .models import ScenarioType, WorldState, ForecastRequestIn
from .causal_graph import build_world_causal_graph, ShockPropagator
from .knowledge_graph import build_world_knowledge_graph, KGQueryEngine
from .forecasting import ProductionCostForecaster
from .simulation import MonteCarloEngine
from .scenario_planning import ScenarioPlanningEngine
from .optimization import SourcingOptimizer
from .dashboard import WorldModelDashboard
from . import monitoring as mon


_DEFAULT_WORLD = WorldState()
_results_store: dict[str, Any] = {}


class ShockIn(BaseModel):
    shocks: dict[str, float] = Field(default_factory=dict)
    max_hops: int = 5


class SimulateIn(BaseModel):
    product_id: str
    base_cost_eur: float
    scenario: ScenarioType = ScenarioType.BASELINE
    n_paths: int = Field(default=500, ge=50, le=5000)
    threshold_pct: float = 0.10
    material_pct: float = 0.45
    labor_pct: float = 0.20
    energy_pct: float = 0.15
    logistics_pct: float = 0.10
    overhead_pct: float = 0.10


class ScenarioPlanIn(BaseModel):
    product_id: str
    base_cost_eur: float
    country: str = "DE"
    scenarios: Optional[list[ScenarioType]] = None
    n_mc_paths: int = Field(default=200, ge=50, le=2000)
    material_pct: float = 0.45
    labor_pct: float = 0.20
    energy_pct: float = 0.15
    logistics_pct: float = 0.10
    overhead_pct: float = 0.10
    steel_share: float = 0.60
    alum_share: float = 0.15
    copper_share: float = 0.10
    plastic_share: float = 0.15
    usd_exposure: float = 0.30
    cny_exposure: float = 0.20


class OptimizeIn(ScenarioPlanIn):
    annual_volume: int = Field(default=10_000, ge=1)
    budget_eur: Optional[float] = None


class WorldStateIn(BaseModel):
    cpi_yoy: float = 3.5
    ppi_yoy: float = 4.2
    eur_usd: float = 1.08
    eur_cny: float = 7.80
    oil_usd_bbl: float = 82.0
    gas_eur_mwh: float = 35.0
    electricity_eur_mwh: float = 85.0
    steel_eur_t: float = 620.0
    alum_eur_t: float = 2300.0
    copper_eur_t: float = 8800.0
    shanghai_rotterdam_usd: float = 1800.0
    conflict_risk_eu: float = 0.15
    supply_chain_stress: float = 0.25


def _world_from_in(w: WorldStateIn) -> WorldState:
    ws = WorldState()
    ws.inflation.cpi_yoy = w.cpi_yoy
    ws.inflation.ppi_yoy = w.ppi_yoy
    ws.fx.eur_usd = w.eur_usd
    ws.fx.eur_cny = w.eur_cny
    ws.energy.oil_usd_bbl = w.oil_usd_bbl
    ws.energy.gas_eur_mwh = w.gas_eur_mwh
    ws.energy.electricity_eur_mwh = w.electricity_eur_mwh
    ws.commodities.steel_eur_t = w.steel_eur_t
    ws.commodities.aluminum_eur_t = w.alum_eur_t
    ws.commodities.copper_eur_t = w.copper_eur_t
    ws.logistics.shanghai_rotterdam_usd = w.shanghai_rotterdam_usd
    ws.geopolitics.conflict_risk_eu = w.conflict_risk_eu
    ws.geopolitics.supply_chain_stress = w.supply_chain_stress
    return ws


if _FASTAPI_AVAILABLE:
    router = APIRouter(prefix="/world-model", tags=["world-model"])

    @router.post("/forecast")
    async def forecast(req: ForecastRequestIn):
        t0 = time.perf_counter()
        world = req.world or _DEFAULT_WORLD
        forecaster = ProductionCostForecaster(n_paths=req.n_mc_paths or 500)
        try:
            result = forecaster.forecast(
                product_id=req.product_id,
                world=world,
                base_cost_eur=req.base_cost_eur,
                country=req.country,
                material_pct=req.material_pct,
                labor_pct=req.labor_pct,
                energy_pct=req.energy_pct,
                logistics_pct=req.logistics_pct,
                overhead_pct=req.overhead_pct,
                steel_share=req.steel_share,
                alum_share=req.alum_share,
                copper_share=req.copper_share,
                plastic_share=req.plastic_share,
                usd_exposure=req.usd_exposure,
                cny_exposure=req.cny_exposure,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            mon.record_forecast(req.product_id, "ok", elapsed)
            return {
                "product_id": result.product_id,
                "base_cost_eur": result.base_cost_eur,
                "scenario": result.scenario.value,
                "run_time_ms": result.run_time_ms,
                "horizons": {
                    k: {
                        "horizon": v.horizon.value,
                        "total_cost_delta_pct": v.total_cost_delta_pct,
                        "total_cost_delta_eur": v.total_cost_delta_eur,
                        "confidence": v.confidence,
                        "dominant_driver": v.dominant_driver,
                        "cost_impacts": [
                            {"signal_id": c.signal_id, "impact_pct": c.impact_pct, "direction": c.direction}
                            for c in v.cost_impacts
                        ],
                    }
                    for k, v in result.horizons.items()
                },
            }
        except Exception as e:
            mon.record_forecast(req.product_id, "error", (time.perf_counter() - t0) * 1000)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/simulate")
    async def simulate(req: SimulateIn, background_tasks: BackgroundTasks):
        task_id = str(uuid.uuid4())
        _results_store[task_id] = {"status": "running"}

        def _run():
            t0 = time.perf_counter()
            world = _DEFAULT_WORLD
            engine = MonteCarloEngine(n_paths=req.n_paths)
            cost_weights = {
                "material_pct": req.material_pct, "labor_pct": req.labor_pct,
                "energy_pct": req.energy_pct, "logistics_pct": req.logistics_pct,
                "overhead_pct": req.overhead_pct,
            }
            sim = engine.simulate(world, req.base_cost_eur, req.scenario, cost_weights, req.threshold_pct)
            elapsed = (time.perf_counter() - t0) * 1000
            mon.record_simulation(req.scenario.value, elapsed)
            _results_store[task_id] = {
                "status": "done",
                "product_id": req.product_id,
                "scenario": req.scenario.value,
                "dist_12m": {"p5": sim.dist_12m.p5, "p25": sim.dist_12m.p25, "p50": sim.dist_12m.p50,
                             "p75": sim.dist_12m.p75, "p95": sim.dist_12m.p95, "mean": sim.dist_12m.mean},
                "var_95_12m": sim.var_95_12m,
                "cvar_95_12m": sim.cvar_95_12m,
                "probability_above_threshold": sim.probability_above_threshold,
                "n_paths": sim.n_paths,
                "run_time_ms": elapsed,
            }

        background_tasks.add_task(_run)
        return {"task_id": task_id, "status": "running"}

    @router.get("/simulate/{task_id}")
    async def get_simulation_result(task_id: str):
        r = _results_store.get(task_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return r

    @router.post("/scenarios")
    async def run_scenarios(req: ScenarioPlanIn, background_tasks: BackgroundTasks):
        task_id = str(uuid.uuid4())
        _results_store[task_id] = {"status": "running"}

        def _run():
            t0 = time.perf_counter()
            engine = ScenarioPlanningEngine(
                n_mc_paths=req.n_mc_paths,
                material_pct=req.material_pct, labor_pct=req.labor_pct,
                energy_pct=req.energy_pct, logistics_pct=req.logistics_pct,
                overhead_pct=req.overhead_pct, steel_share=req.steel_share,
                alum_share=req.alum_share, copper_share=req.copper_share,
                plastic_share=req.plastic_share, usd_exposure=req.usd_exposure,
                cny_exposure=req.cny_exposure,
            )
            comp = engine.run_all(req.product_id, req.base_cost_eur, _DEFAULT_WORLD,
                                  req.country, req.scenarios)
            elapsed = (time.perf_counter() - t0) * 1000
            mon.record_scenario_run("ok", elapsed)
            mon.record_spread(req.product_id, comp.spread_pct)
            _results_store[task_id] = {
                "status": "done",
                "product_id": comp.product_id,
                "base_cost_eur": comp.base_cost_eur,
                "worst_case_m12": comp.worst_case_m12,
                "best_case_m12": comp.best_case_m12,
                "baseline_m12": comp.baseline_m12,
                "spread_pct": comp.spread_pct,
                "run_time_ms": comp.run_time_ms,
                "scenarios": [
                    {
                        "scenario": r.scenario.value,
                        "cost_at_m1": r.cost_at_m1, "cost_at_m3": r.cost_at_m3,
                        "cost_at_m6": r.cost_at_m6, "cost_at_m12": r.cost_at_m12,
                        "delta_pct_m12": r.delta_pct_m12, "var_95_12m": r.var_95_12m,
                        "risk_flags": [{"horizon": f.horizon, "severity": f.severity, "message": f.message} for f in r.risk_flags],
                        "recommendations": [{"action": rc.action, "priority": rc.priority} for rc in r.recommendations],
                    }
                    for r in comp.scenarios
                ],
                "top_risks": [{"severity": f.severity, "message": f.message} for f in comp.top_risks],
            }

        background_tasks.add_task(_run)
        return {"task_id": task_id, "status": "running"}

    @router.get("/scenarios/{task_id}")
    async def get_scenario_result(task_id: str):
        r = _results_store.get(task_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return r

    @router.post("/optimize")
    async def optimize(req: OptimizeIn, background_tasks: BackgroundTasks):
        task_id = str(uuid.uuid4())
        _results_store[task_id] = {"status": "running"}

        def _run():
            t0 = time.perf_counter()
            optimizer = SourcingOptimizer(
                n_mc_paths=req.n_mc_paths, annual_volume=req.annual_volume,
                material_pct=req.material_pct, labor_pct=req.labor_pct,
                energy_pct=req.energy_pct, logistics_pct=req.logistics_pct,
                overhead_pct=req.overhead_pct, steel_share=req.steel_share,
                alum_share=req.alum_share, copper_share=req.copper_share,
                plastic_share=req.plastic_share, usd_exposure=req.usd_exposure,
                cny_exposure=req.cny_exposure,
            )
            opt = optimizer.optimize(req.product_id, req.base_cost_eur, _DEFAULT_WORLD,
                                     req.country, req.scenarios, req.budget_eur)
            elapsed = (time.perf_counter() - t0) * 1000
            rob = opt.robust_solution
            mon.record_optimization(req.product_id, "ok", rob.net_benefit_eur, elapsed)
            _results_store[task_id] = {
                "status": "done",
                "product_id": opt.product_id,
                "robust_solution": {
                    "selected_actions": [h.action.value for h in rob.selected_hedges],
                    "total_hedge_cost_eur": rob.total_hedge_cost_eur,
                    "expected_saving_eur": rob.expected_saving_eur,
                    "net_benefit_eur": rob.net_benefit_eur,
                    "worst_case_cost_m12": rob.worst_case_cost_m12,
                    "robustness_score": rob.robustness_score,
                    "regret": rob.regret,
                },
                "hedge_options": [
                    {"action": h.action.value, "description": h.description,
                     "cost_eur": h.cost_eur, "saving_eur_annual": h.saving_eur_annual,
                     "confidence": h.confidence}
                    for h in opt.hedge_options
                ],
                "run_time_ms": elapsed,
            }

        background_tasks.add_task(_run)
        return {"task_id": task_id, "status": "running"}

    @router.get("/optimize/{task_id}")
    async def get_optimization_result(task_id: str):
        r = _results_store.get(task_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return r

    @router.get("/causal-graph")
    async def get_causal_graph():
        g = build_world_causal_graph()
        return g.to_dict()

    @router.post("/causal-graph/shock")
    async def propagate_shock(req: ShockIn):
        g = build_world_causal_graph()
        propagator = ShockPropagator()
        result = propagator.multi_shock(g, req.shocks)
        return {"propagated_deltas": result, "n_affected": len(result)}

    @router.get("/knowledge-graph")
    async def get_knowledge_graph(node_id: Optional[str] = None, depth: int = 2):
        kg = build_world_knowledge_graph()
        if node_id:
            neighborhood = kg.neighborhood(node_id, depth=depth)
            return {
                "center": node_id,
                "nodes": {k: {"id": v.id, "type": v.node_type, "label": v.label} for k, v in neighborhood.items()},
            }
        return {
            "n_nodes": len(kg._nodes),
            "n_relations": len(kg._relations),
            "node_types": list({n.node_type for n in kg._nodes.values()}),
        }

    @router.get("/knowledge-graph/risk/{event_node_id}")
    async def get_risk_paths(event_node_id: str):
        kg = build_world_knowledge_graph()
        qe = KGQueryEngine(kg)
        exposure = qe.risk_exposure(event_node_id)
        return {"event_node_id": event_node_id, "risk_exposure": exposure}

    @router.post("/dashboard")
    async def build_dashboard(req: ScenarioPlanIn):
        engine = ScenarioPlanningEngine(
            n_mc_paths=req.n_mc_paths,
            material_pct=req.material_pct, labor_pct=req.labor_pct,
            energy_pct=req.energy_pct, logistics_pct=req.logistics_pct,
            overhead_pct=req.overhead_pct, steel_share=req.steel_share,
            alum_share=req.alum_share, copper_share=req.copper_share,
            plastic_share=req.plastic_share, usd_exposure=req.usd_exposure,
            cny_exposure=req.cny_exposure,
        )
        world = _DEFAULT_WORLD
        comparison = engine.run_all(req.product_id, req.base_cost_eur, world, req.country, req.scenarios)
        optimizer = SourcingOptimizer(n_mc_paths=req.n_mc_paths, material_pct=req.material_pct,
                                      labor_pct=req.labor_pct, energy_pct=req.energy_pct)
        opt_result = optimizer.optimize(req.product_id, req.base_cost_eur, world, req.country)
        mc = MonteCarloEngine(n_paths=req.n_mc_paths)
        baseline_sim = mc.simulate(world, req.base_cost_eur, ScenarioType.BASELINE,
                                   {"material_pct": req.material_pct, "labor_pct": req.labor_pct,
                                    "energy_pct": req.energy_pct, "logistics_pct": req.logistics_pct,
                                    "overhead_pct": req.overhead_pct})
        causal_dict = build_world_causal_graph().to_dict()
        dash = WorldModelDashboard()
        return dash.build(comparison, opt_result, world, baseline_sim, causal_dict)

    @router.get("/health")
    async def health():
        snap = mon.get_health()
        return {
            "status": "ok",
            "total_forecast_runs": snap.total_forecast_runs,
            "total_sim_runs": snap.total_sim_runs,
            "total_opt_runs": snap.total_opt_runs,
            "avg_forecast_ms": round(snap.avg_forecast_ms, 1),
            "last_product_id": snap.last_product_id,
        }

    @router.get("/metrics")
    async def metrics():
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from fastapi.responses import Response
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
        except ImportError:
            return {"message": "prometheus_client not installed"}

    world_model_router = router

else:
    world_model_router = None
