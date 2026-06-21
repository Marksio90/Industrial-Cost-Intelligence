"""
Section 7 — API

FastAPI router dla cost digital twin:

  POST   /cost/models                      → utwórz model produktu
  GET    /cost/models/{model_id}           → pobierz model
  DELETE /cost/models/{model_id}           → usuń model

  POST   /cost/models/{model_id}/compute   → oblicz breakdown kosztu
  GET    /cost/models/{model_id}/graph     → zwróć causal graph
  GET    /cost/models/{model_id}/sensitivity → OAT sensitivity analysis

  POST   /cost/scenarios                   → utwórz scenariusz
  GET    /cost/scenarios/{scenario_id}     → pobierz scenariusz
  POST   /cost/scenarios/{scenario_id}/run → uruchom scenariusz
  GET    /cost/scenarios/{scenario_id}/result → wynik scenariusza
  DELETE /cost/scenarios/{scenario_id}     → usuń scenariusz

  POST   /cost/whatif/material             → co jeśli: zmiana materiału
  POST   /cost/whatif/supplier             → co jeśli: zmiana dostawcy
  POST   /cost/whatif/technology           → co jeśli: zmiana technologii
  POST   /cost/whatif/country              → co jeśli: zmiana kraju
  POST   /cost/whatif/volume               → co jeśli: zmiana wolumenu
  POST   /cost/whatif/compound             → co jeśli: kombinacja zmian

  POST   /cost/optimize                    → uruchom optymalizację
  GET    /cost/optimize/{task_id}          → wynik optymalizacji
  GET    /cost/countries                   → porównanie krajów

  POST   /cost/monte-carlo                 → uruchom MC
  GET    /cost/monte-carlo/{run_id}        → wynik MC

  GET    /cost/dashboard/{model_id}        → pełny dashboard
  GET    /cost/health                      → health check
  GET    /cost/metrics                     → Prometheus metrics
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from .models import (
    ProductCostModel, CostResult, CostDelta, CostChange,
    ScenarioType, OptimizationObjective,
    BomLine, RoutingStep, SupplierCostProfile, TechnologyProfile,
    ProductModelIn, CostChangeIn, ScenarioIn, OptimizationIn,
    CostResultOut, OptimizationTask,
)
from .simulation_engine import CostSimulationEngine, MCSimResult
from .scenario_planning import ScenarioPlanner, ScenarioLibrary, ScenarioResult, run_stress_test
from .optimization_engine import OptimizationEngine
from .causal_graph import CostGraphBuilder
from .dashboard import DashboardAssembler
from .monitoring import (
    record_computation, record_scenario, record_mc_run,
    record_optimization, record_whatif,
    cost_health_check, prometheus_response,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/cost", tags=["cost_twin"])

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stores (production: replace with Redis / PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────

_models:       dict[str, ProductCostModel]       = {}
_results:      dict[str, CostResult]             = {}
_mc_results:   dict[str, MCSimResult]            = {}
_opt_results:  dict[str, dict[str, Any]]         = {}
_scenario_lib  = ScenarioLibrary()
_engine        = CostSimulationEngine()
_planner       = ScenarioPlanner()
_optimizer     = OptimizationEngine()
_dashboard     = DashboardAssembler()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic request / response schemas
# ─────────────────────────────────────────────────────────────────────────────

class ComputeRequest(BaseModel):
    country_code:    str | None = None
    volume_override: int | None = None
    price_overrides: dict[str, float] = Field(default_factory=dict)

class MaterialWhatIfRequest(BaseModel):
    material_id:     str
    new_price:       float | None = None
    price_delta_pct: float | None = None
    new_scrap_rate:  float | None = None

class SupplierWhatIfRequest(BaseModel):
    old_supplier_id: str
    new_supplier_id: str
    new_margin_pct:  float = 10.0
    defect_rate:     float = 0.005
    tooling_amort:   float = 0.0
    qualification:   float = 0.0

class TechnologyWhatIfRequest(BaseModel):
    process_type:        str
    machine_rate_eur_h:  float = 30.0
    energy_kwh_per_h:    float = 5.0
    cycle_time_s:        float | None = None
    tooling_cost_eur:    float = 0.0
    tooling_life_units:  int   = 50000
    yield_pct:           float = 0.98
    automation_level:    float = 0.5
    capex_eur:           float = 0.0
    payback_years:       float = 5.0

class CountryWhatIfRequest(BaseModel):
    new_country_code: str

class VolumeWhatIfRequest(BaseModel):
    new_volume:    int
    learning_rate: float = 0.85

class CompoundWhatIfRequest(BaseModel):
    changes: list[CostChangeIn]

class MCRequest(BaseModel):
    country_code: str | None = None
    n_runs:       int   = 1000
    price_vol:    float = 0.10
    fx_vol:       float = 0.05
    labor_vol:    float = 0.03
    seed:         int | None = None

class OptimizeRequest(BaseModel):
    objective:      OptimizationObjective = OptimizationObjective.MIN_TOTAL_COST
    algorithm:      str = "greedy"
    volume_range:   tuple[int, int] | None = None

class ScenarioRunRequest(BaseModel):
    scenario_type: ScenarioType
    params:        dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Model CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/models", status_code=201)
async def create_model(payload: ProductModelIn) -> dict[str, str]:
    model_id = payload.model_id or str(uuid.uuid4())
    model = ProductCostModel(
        model_id           = model_id,
        product_name       = payload.product_name,
        production_country = payload.production_country or "DE",
        annual_volume      = payload.annual_volume or 10000,
        bom_lines          = [BomLine(**b.model_dump()) for b in (payload.bom_lines or [])],
        routing            = [RoutingStep(**r.model_dump()) for r in (payload.routing or [])],
        target_margin_pct  = payload.target_margin_pct or 15.0,
        product_weight_kg  = payload.product_weight_kg or 1.0,
    )
    _models[model_id] = model
    log.info("Created cost model %s", model_id)
    return {"model_id": model_id}


@router.get("/models/{model_id}")
async def get_model(model_id: str) -> dict[str, Any]:
    model = _models.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    return {
        "model_id":    model.model_id,
        "product_name": model.product_name,
        "country":     model.production_country,
        "volume":      model.annual_volume,
        "bom_lines":   len(model.bom_lines),
        "routing":     len(model.routing),
    }


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: str) -> None:
    if model_id not in _models:
        raise HTTPException(404, f"Model '{model_id}' not found")
    del _models[model_id]
    _results.pop(model_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# Computation
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/models/{model_id}/compute")
async def compute_cost(model_id: str, req: ComputeRequest) -> dict[str, Any]:
    model = _models.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")

    t0 = time.perf_counter()
    try:
        result = _engine.compute(
            model,
            country_code    = req.country_code or model.production_country,
            price_overrides = req.price_overrides or None,
            volume_override = req.volume_override,
        )
        duration = time.perf_counter() - t0
        _results[model_id] = result
        record_computation(model_id, req.country_code or model.production_country, duration, "ok")
        b = result.breakdown
        return {
            "model_id":    model_id,
            "country":     result.country_code,
            "volume":      result.annual_volume,
            "breakdown":   b.__dict__,
            "material_lines": result.material_lines,
            "duration_s":  round(duration, 3),
        }
    except Exception as exc:
        record_computation(model_id, req.country_code or model.production_country, time.perf_counter() - t0, "error")
        raise HTTPException(500, str(exc))


@router.get("/models/{model_id}/graph")
async def get_cost_graph(model_id: str) -> dict[str, Any]:
    model = _models.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    graph = CostGraphBuilder().build(model)
    return graph.to_dict()


@router.get("/models/{model_id}/sensitivity")
async def get_sensitivity(
    model_id:  str,
    delta_pct: float = Query(default=10.0, ge=1.0, le=50.0),
    country:   str   = Query(default=""),
) -> dict[str, Any]:
    model = _models.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")

    country_code = country or model.production_country
    entries = _engine.sensitivity_oat(model, country_code, delta_pct)
    return {
        "model_id":  model_id,
        "delta_pct": delta_pct,
        "drivers": [
            {
                "driver":     e.driver,
                "swing_eur":  round(e.swing_eur, 2),
                "elasticity": round(e.elasticity, 3),
            }
            for e in entries
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# What-if endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _model_or_404(model_id: str) -> ProductCostModel:
    model = _models.get(model_id)
    if not model:
        raise HTTPException(404, f"Model '{model_id}' not found")
    return model


def _scenario_to_dict(r: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_id":    r.scenario_id,
        "scenario_name":  r.scenario_name,
        "scenario_type":  r.scenario_type.value,
        "impact":         r.impact_label,
        "baseline_cost":  round(r.baseline.breakdown.total_cost_eur, 2),
        "scenario_cost":  round(r.scenario.breakdown.total_cost_eur, 2),
        "delta_eur":      round(r.delta.delta_eur, 2),
        "delta_pct":      round(r.delta.delta_pct, 2),
        "recommendations": r.recommendations,
        "run_time_s":     round(r.run_time_s, 3),
        "metadata":       r.metadata,
    }


@router.post("/whatif/material/{model_id}")
async def whatif_material(model_id: str, req: MaterialWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    result = _planner.run_material_change(
        model, req.material_id,
        new_price       = req.new_price,
        price_delta_pct = req.price_delta_pct,
        new_scrap_rate  = req.new_scrap_rate,
    )
    record_whatif("material", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


@router.post("/whatif/supplier/{model_id}")
async def whatif_supplier(model_id: str, req: SupplierWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    new_profile = SupplierCostProfile(
        supplier_id          = req.new_supplier_id,
        margin_pct           = req.new_margin_pct,
        defect_rate          = req.defect_rate,
        tooling_amort_per_unit = req.tooling_amort,
        qualification_eur    = req.qualification,
    )
    result = _planner.run_supplier_change(
        model, req.old_supplier_id, req.new_supplier_id, new_profile
    )
    record_whatif("supplier", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


@router.post("/whatif/technology/{model_id}")
async def whatif_technology(model_id: str, req: TechnologyWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    tech = TechnologyProfile(
        process_type        = req.process_type,
        machine_rate_eur_h  = req.machine_rate_eur_h,
        energy_kwh_per_h    = req.energy_kwh_per_h,
        tooling_cost_eur    = req.tooling_cost_eur,
        tooling_life_units  = req.tooling_life_units,
        cycle_time_s        = req.cycle_time_s,
        yield_pct           = req.yield_pct,
        automation_level    = req.automation_level,
        capex_eur           = req.capex_eur,
        payback_years       = req.payback_years,
    )
    result = _planner.run_technology_change(model, req.process_type, tech)
    record_whatif("technology", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


@router.post("/whatif/country/{model_id}")
async def whatif_country(model_id: str, req: CountryWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    result = _planner.run_country_change(model, req.new_country_code)
    record_whatif("country", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


@router.post("/whatif/volume/{model_id}")
async def whatif_volume(model_id: str, req: VolumeWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    result = _planner.run_volume_change(model, req.new_volume, req.learning_rate)
    record_whatif("volume", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


@router.post("/whatif/compound/{model_id}")
async def whatif_compound(model_id: str, req: CompoundWhatIfRequest) -> dict[str, Any]:
    model = _model_or_404(model_id)
    t0 = time.perf_counter()
    changes = [CostChange(**c.model_dump()) for c in req.changes]
    result = _planner.run_compound(model, changes)
    record_whatif("compound", model_id, time.perf_counter() - t0)
    return _scenario_to_dict(result)


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios (save/run/result)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scenarios/{model_id}", status_code=201)
async def create_scenario(model_id: str, req: ScenarioIn) -> dict[str, str]:
    from .models import CostScenario
    sid = str(uuid.uuid4())
    scenario = CostScenario(
        scenario_id   = sid,
        model_id      = model_id,
        name          = req.name,
        scenario_type = req.scenario_type,
        changes       = [CostChange(**c.model_dump()) for c in req.changes],
    )
    _scenario_lib.save(model_id, scenario)
    return {"scenario_id": sid}


@router.get("/scenarios/{model_id}/{scenario_id}/result")
async def get_scenario_result(model_id: str, scenario_id: str) -> dict[str, Any]:
    result = _scenario_lib.get_result(model_id, scenario_id)
    if not result:
        raise HTTPException(404, "Result not available — run the scenario first")
    return _scenario_to_dict(result)


@router.post("/scenarios/{model_id}/{scenario_id}/run")
async def run_scenario(model_id: str, scenario_id: str) -> dict[str, Any]:
    model = _model_or_404(model_id)
    scenario = _scenario_lib.get(model_id, scenario_id)
    if not scenario:
        raise HTTPException(404, f"Scenario '{scenario_id}' not found")

    result = _planner.run_compound(model, scenario.changes, scenario_name=scenario.name)
    result.scenario_id = scenario_id
    _scenario_lib.save_result(model_id, result)
    record_scenario(model_id, scenario_id, result.delta.delta_pct, result.impact_label)
    return _scenario_to_dict(result)


@router.delete("/scenarios/{model_id}/{scenario_id}", status_code=204)
async def delete_scenario(model_id: str, scenario_id: str) -> None:
    if not _scenario_lib.delete(model_id, scenario_id):
        raise HTTPException(404, f"Scenario '{scenario_id}' not found")


@router.get("/scenarios/{model_id}")
async def list_scenarios(model_id: str) -> list[dict[str, Any]]:
    return [
        {"scenario_id": s.scenario_id, "name": s.name, "type": s.scenario_type.value}
        for s in _scenario_lib.list_all(model_id)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Stress test
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/stress-test/{model_id}")
async def stress_test(model_id: str) -> dict[str, Any]:
    model = _model_or_404(model_id)
    summary = run_stress_test(model, _planner)
    return {
        "scenario_count": summary.scenario_count,
        "avg_delta_pct":  summary.avg_delta_pct,
        "critical_count": summary.critical_count,
        "worst_case":     _scenario_to_dict(summary.worst_case) if summary.worst_case else None,
        "best_case":      _scenario_to_dict(summary.best_case)  if summary.best_case  else None,
        "summary_table":  summary.summary_table,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/monte-carlo/{model_id}", status_code=202)
async def run_monte_carlo(
    model_id:         str,
    req:              MCRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    model = _model_or_404(model_id)
    run_id = str(uuid.uuid4())

    async def _run() -> None:
        t0 = time.perf_counter()
        mc = _engine.monte_carlo(
            model,
            country_code = req.country_code or model.production_country,
            n_runs       = req.n_runs,
            price_vol    = req.price_vol,
            fx_vol       = req.fx_vol,
            labor_vol    = req.labor_vol,
            seed         = req.seed,
        )
        _mc_results[run_id] = mc
        record_mc_run(model_id, run_id, req.n_runs, time.perf_counter() - t0, mc.var_95)
        log.info("MC run %s complete: mean=%.2f var95=%.2f", run_id, mc.mean_eur, mc.var_95)

    background_tasks.add_task(_run)
    return {"run_id": run_id, "status": "queued"}


@router.get("/monte-carlo/{run_id}")
async def get_mc_result(run_id: str) -> dict[str, Any]:
    mc = _mc_results.get(run_id)
    if not mc:
        raise HTTPException(404, "MC run not found or still running")
    return {
        "run_id":  run_id,
        "n_runs":  mc.n_runs,
        "mean":    round(mc.mean_eur, 2),
        "std":     round(mc.std_eur, 2),
        "p5":      round(mc.p5_eur, 2),
        "p50":     round(mc.p50_eur, 2),
        "p95":     round(mc.p95_eur, 2),
        "var_95":  round(mc.var_95, 2),
        "var_99":  round(mc.var_99, 2),
        "cvar_95": round(mc.cvar_95, 2),
        "histogram": mc.histogram(20),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Optimization
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/optimize/{model_id}", status_code=202)
async def optimize(
    model_id:         str,
    req:              OptimizeRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    model = _model_or_404(model_id)
    task_id = str(uuid.uuid4())

    async def _run() -> None:
        t0 = time.perf_counter()
        task = OptimizationTask(
            task_id       = task_id,
            objective     = req.objective,
            algorithm     = req.algorithm,
            volume_range  = req.volume_range,
        )
        result = _optimizer.optimize(task, model)
        _opt_results[task_id] = {
            "task_id":          task_id,
            "objective":        result.objective.value,
            "baseline_cost":    round(result.baseline_cost, 2),
            "optimized_cost":   round(result.optimized_cost, 2),
            "savings_eur":      round(result.savings_eur, 2),
            "savings_pct":      round(result.savings_pct, 2),
            "best_decisions":   result.best_decisions,
            "improvement_path": result.improvement_path,
            "pareto_frontier":  result.pareto_frontier,
            "run_time_s":       round(result.run_time_s, 3),
        }
        record_optimization(model_id, task_id, result.savings_pct, time.perf_counter() - t0)

    background_tasks.add_task(_run)
    return {"task_id": task_id, "status": "queued"}


@router.get("/optimize/{task_id}")
async def get_optimization_result(task_id: str) -> dict[str, Any]:
    result = _opt_results.get(task_id)
    if not result:
        raise HTTPException(404, "Optimization task not found or still running")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Countries
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/countries")
async def list_countries() -> list[dict[str, Any]]:
    from .dependency_model import COUNTRY_PROFILES
    return [
        {
            "code":         code,
            "currency":     c.currency,
            "wage_eur_h":   c.avg_wage_eur_h,
            "electricity":  c.electricity_eur_kwh,
            "import_duty":  c.import_duty_pct,
            "co2_tax":      c.co2_tax_eur_t,
            "risk":         c.risk_level.name,
        }
        for code, c in COUNTRY_PROFILES.items()
    ]


@router.get("/countries/compare/{model_id}")
async def compare_countries(model_id: str) -> list[dict[str, Any]]:
    model = _model_or_404(model_id)
    return _optimizer.quick_country_comparison(model)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{model_id}")
async def get_dashboard(model_id: str) -> dict[str, Any]:
    model  = _model_or_404(model_id)
    result = _results.get(model_id)

    if not result:
        # Compute on demand
        result = _engine.compute(model, model.production_country)
        _results[model_id] = result

    sensitivity = _engine.sensitivity_oat(model, model.production_country)
    country_cmp = _optimizer.quick_country_comparison(model)

    return _dashboard.build(
        baseline           = result,
        sensitivity        = sensitivity,
        country_comparison = country_cmp,
        base_volume        = model.annual_volume,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health + Metrics
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        **cost_health_check(),
        "models":    len(_models),
        "scenarios": sum(len(_scenario_lib.list_all(m)) for m in _models),
        "mc_runs":   len(_mc_results),
        "opt_tasks": len(_opt_results),
    }


@router.get("/metrics")
async def metrics() -> Any:
    from fastapi.responses import Response
    body, content_type = prometheus_response()
    return Response(content=body, media_type=content_type)
