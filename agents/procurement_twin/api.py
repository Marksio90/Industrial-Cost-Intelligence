"""
Section 7 — API

FastAPI router dla cyfrowego bliźniaka:

  POST   /twin/suppliers               — dodaj profil dostawcy
  GET    /twin/suppliers               — lista dostawców
  GET    /twin/suppliers/{id}/risk     — szczegółowe ryzyko dostawcy
  GET    /twin/suppliers/{id}/impact   — ripple effect analysis

  POST   /twin/materials               — dodaj materiał
  GET    /twin/materials               — lista materiałów

  POST   /twin/scenarios               — utwórz scenariusz
  GET    /twin/scenarios               — lista scenariuszy
  DELETE /twin/scenarios/{id}          — usuń scenariusz
  POST   /twin/scenarios/from-template — z szablonu
  POST   /twin/scenarios/{id}/run      — uruchom symulację

  POST   /twin/monte-carlo             — MC analysis
  GET    /twin/monte-carlo/{id}        — wyniki MC

  POST   /twin/stress-test             — stress test portfolio
  GET    /twin/resilience              — resilience report
  GET    /twin/portfolio/var           — Portfolio VaR

  GET    /twin/dashboard               — pełny dashboard
  GET    /twin/dashboard/comparison    — baseline vs scenario
  GET    /twin/dashboard/events        — timeline zdarzeń

  POST   /twin/simulate/event          — inject single event
  GET    /twin/state                   — aktualny stan bliźniaka

  WS     /twin/stream                  — real-time KPI stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .models import (
    SupplierProfileIn, ScenarioCreateIn, MCRunIn, SimulationRunIn,
    SimulationResult, MonteCarloResult, ImpactLevel, ScenarioType,
    SCENARIO_TEMPLATES, ScenarioShock,
)
from .scenario_engine import ScenarioLibrary, ScenarioRunner, CompoundScenarioBuilder, run_stress_test
from .monte_carlo import MonteCarloEngine, MonteCarloConfig
from .risk_propagation import RiskPropagationEngine, ResilienceReport
from .dashboard import DashboardAssembler, build_supplier_scorecard, build_event_timeline
from .simulation_engine import SimulationStateBuilder, SimulationEngine
from .event_simulation import EventDrivenSimulator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/twin", tags=["procurement-twin"])

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state (replace with DB/Redis in production)
# ─────────────────────────────────────────────────────────────────────────────

_library    = ScenarioLibrary()
_mc_results: dict[str, MonteCarloResult] = {}
_sim_results: dict[str, SimulationResult] = {}
_baseline_results: dict[str, SimulationResult] = {}
_ws_clients: list[WebSocket] = []

# ─────────────────────────────────────────────────────────────────────────────
# Dependency stubs
# ─────────────────────────────────────────────────────────────────────────────

def _get_state(tenant_id: str = "tenant-demo"):
    """Inject or build SimulationState. Override in production."""
    raise HTTPException(503, "Inject simulation state via app startup")

def _get_library() -> ScenarioLibrary:
    return _library

# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class SupplierRiskOut(BaseModel):
    supplier_id:    str
    name:           str
    country:        str
    risk_score:     float
    bankruptcy_prob: float
    on_time_pct:    float
    geo_risk:       float
    impact_level:   str


class RippleEffectOut(BaseModel):
    origin_id:           str
    origin_name:         str
    total_impact_eur:    float
    direct_eur:          float
    indirect_eur:        float
    affected_materials:  int
    propagation_hops:    int
    impact_level:        str
    ripple_chain:        list[dict[str, Any]]


class StressTestIn(BaseModel):
    scenario_ids: list[str]
    tenant_id:    str = "tenant-demo"


class StressTestOut(BaseModel):
    scenarios_run:          int
    worst_case_delta_pct:   float
    worst_scenario:         str
    total_at_risk_eur:      float
    critical_count:         int
    summary_table:          list[dict[str, Any]]


class EventInjectIn(BaseModel):
    event_type:  str
    source_id:   str
    day_offset:  int = 0
    magnitude:   float = 10.0
    tenant_id:   str = "tenant-demo"


# ─────────────────────────────────────────────────────────────────────────────
# Suppliers
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/suppliers")
async def list_suppliers(tenant_id: str = Query(default="tenant-demo")) -> list[dict[str, Any]]:
    """List all registered supplier profiles."""
    # In production, query DB by tenant_id
    return []


@router.get("/suppliers/{supplier_id}/risk", response_model=SupplierRiskOut)
async def supplier_risk(supplier_id: str, tenant_id: str = Query(default="tenant-demo")) -> SupplierRiskOut:
    from .risk_propagation import _supplier_risk_score
    # Stub — real impl fetches supplier from state
    raise HTTPException(404, "Supplier not found — inject state first")


@router.get("/suppliers/{supplier_id}/impact", response_model=RippleEffectOut)
async def supplier_impact(
    supplier_id: str,
    tenant_id:   str = Query(default="tenant-demo"),
    state       = Depends(_get_state),
) -> RippleEffectOut:
    engine = RiskPropagationEngine(state)
    impact = engine.simulate_supplier_failure(supplier_id)
    return RippleEffectOut(
        origin_id=impact.origin_id,
        origin_name=impact.origin_name,
        total_impact_eur=impact.total_eur,
        direct_eur=impact.direct_eur,
        indirect_eur=impact.indirect_eur,
        affected_materials=len(impact.affected_materials),
        propagation_hops=impact.propagation_hops,
        impact_level=impact.impact_level.value,
        ripple_chain=impact.ripple_chain,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scenarios", status_code=201)
async def create_scenario(
    body:    ScenarioCreateIn,
    library: ScenarioLibrary = Depends(_get_library),
) -> dict[str, Any]:
    from .models import ScenarioDefinition
    shocks = [
        ScenarioShock(
            target_type=s.target_type,
            target_id=s.target_id,
            parameter=s.parameter,
            value=s.value,
            day_offset=s.day_offset,
            duration_days=s.duration_days,
            ramp_days=s.ramp_days,
        )
        for s in body.shocks
    ]
    scenario = ScenarioDefinition(
        name=body.name,
        description=body.description,
        scenario_type=ScenarioType(body.scenario_type),
        shocks=shocks,
        horizon_days=body.horizon_days,
        seed=body.seed,
        tenant_id=body.tenant_id,
    )
    library.create(scenario)
    return {"scenario_id": scenario.scenario_id, "name": scenario.name, "shocks": len(shocks)}


@router.get("/scenarios")
async def list_scenarios(
    tenant_id: str = Query(default="tenant-demo"),
    library:   ScenarioLibrary = Depends(_get_library),
) -> list[dict[str, Any]]:
    scenarios = library.list_all(tenant_id)
    return [
        {
            "scenario_id":  s.scenario_id,
            "name":         s.name,
            "type":         s.scenario_type.value,
            "horizon_days": s.horizon_days,
            "shocks":       len(s.shocks),
            "created_at":   s.created_at.isoformat(),
        }
        for s in scenarios
    ]


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(
    scenario_id: str,
    library:     ScenarioLibrary = Depends(_get_library),
) -> None:
    if not library.delete(scenario_id):
        raise HTTPException(404, f"Scenario {scenario_id!r} not found")


@router.post("/scenarios/from-template", status_code=201)
async def create_from_template(
    template:  str  = Query(..., description="Template key e.g. supplier_loss, steel_price_15pct"),
    target_id: str  = Query(..., description="supplier_id or material_id to target"),
    tenant_id: str  = Query(default="tenant-demo"),
    library:   ScenarioLibrary = Depends(_get_library),
) -> dict[str, Any]:
    scenario = library.create_from_template(template, target_id, tenant_id)
    if not scenario:
        raise HTTPException(404, f"Template {template!r} not found. Available: {list(SCENARIO_TEMPLATES.keys())}")
    return {"scenario_id": scenario.scenario_id, "name": scenario.name}


@router.get("/scenarios/templates")
async def list_templates() -> list[dict[str, str]]:
    return [
        {"key": k, "name": v["name"], "description": v.get("description", "")}
        for k, v in SCENARIO_TEMPLATES.items()
    ]


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(
    scenario_id:  str,
    background:   BackgroundTasks,
    library:      ScenarioLibrary = Depends(_get_library),
    state        = Depends(_get_state),
) -> dict[str, str]:
    scenario = library.get(scenario_id)
    if not scenario:
        raise HTTPException(404, f"Scenario {scenario_id!r} not found")

    async def _run() -> None:
        runner = ScenarioRunner(state)
        result = runner.run(scenario)
        library.store_result(result)
        _sim_results[scenario_id] = result
        await _broadcast_kpi(result)

    background.add_task(_run)
    return {"status": "accepted", "scenario_id": scenario_id}


@router.get("/scenarios/{scenario_id}/result")
async def get_scenario_result(
    scenario_id: str,
    library:     ScenarioLibrary = Depends(_get_library),
) -> dict[str, Any]:
    result = library.get_result(scenario_id) or _sim_results.get(scenario_id)
    if not result:
        raise HTTPException(404, "No result yet — run scenario first")
    return {
        "simulation_id":     result.simulation_id,
        "status":            result.status.value,
        "cost_delta_eur":    result.cost_delta_eur,
        "cost_delta_pct":    result.cost_delta_pct,
        "max_lead_time":     result.max_lead_time_days,
        "min_service":       result.min_service_level,
        "stockout_days":     result.stockout_days,
        "impact_level":      result.impact_level.value,
        "recommendations":   result.recommendations,
        "events_triggered":  len(result.events_triggered),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/monte-carlo", status_code=202)
async def run_monte_carlo(
    body:       MCRunIn,
    background: BackgroundTasks,
    library:    ScenarioLibrary = Depends(_get_library),
    state      = Depends(_get_state),
) -> dict[str, str]:
    scenario = library.get(body.scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")

    mc_id = body.scenario_id

    async def _run() -> None:
        config = MonteCarloConfig(n_runs=body.n_runs, horizon_days=body.horizon_days, seed=body.seed)
        engine = MonteCarloEngine(state, config)
        result = await engine.run_async(scenario)
        _mc_results[mc_id] = result
        log.info("MC completed: n=%d mean=%.0f var95=%.0f", result.n_runs, result.cost_mean, result.var_95)

    background.add_task(_run)
    return {"status": "accepted", "mc_id": mc_id}


@router.get("/monte-carlo/{mc_id}")
async def get_mc_result(mc_id: str) -> dict[str, Any]:
    mc = _mc_results.get(mc_id)
    if not mc:
        raise HTTPException(404, "MC result not found or still running")
    from .dashboard import build_mc_fan_chart, build_mc_stats_panel, build_tornado_chart
    return {
        "mc_id":        mc.mc_id,
        "n_runs":       mc.n_runs,
        "stats":        build_mc_stats_panel(mc),
        "fan_chart":    build_mc_fan_chart(mc),
        "tornado":      build_tornado_chart(mc),
        "duration_s":   mc.duration_s,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stress test & resilience
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/stress-test", response_model=StressTestOut)
async def stress_test(
    body:    StressTestIn,
    library: ScenarioLibrary = Depends(_get_library),
    state   = Depends(_get_state),
) -> StressTestOut:
    scenarios = [library.get(sid) for sid in body.scenario_ids if library.get(sid)]
    if not scenarios:
        raise HTTPException(400, "No valid scenarios found")
    result = run_stress_test(state, scenarios)
    return StressTestOut(
        scenarios_run=result.scenarios_run,
        worst_case_delta_pct=result.worst_case_cost_delta_pct,
        worst_scenario=result.worst_scenario_name,
        total_at_risk_eur=result.total_at_risk_eur,
        critical_count=len(result.critical_scenarios),
        summary_table=result.summary_table,
    )


@router.get("/resilience")
async def resilience_report(
    tenant_id: str = Query(default="tenant-demo"),
    state     = Depends(_get_state),
) -> dict[str, Any]:
    engine = RiskPropagationEngine(state)
    report = engine.compute_resilience()
    return {
        "overall_score":        report.overall_score,
        "single_source_count":  report.single_source_count,
        "geo_concentration":    report.geo_concentration,
        "high_risk_spend_pct":  report.high_risk_spend_pct,
        "hhi_supplier":         report.hhi_supplier,
        "critical_suppliers":   report.critical_suppliers,
        "mitigation_actions":   report.mitigation_actions,
    }


@router.get("/portfolio/var")
async def portfolio_var(
    confidence: float = Query(default=0.95, ge=0.80, le=0.99),
    tenant_id:  str   = Query(default="tenant-demo"),
    state      = Depends(_get_state),
) -> dict[str, float]:
    engine = RiskPropagationEngine(state)
    return engine.portfolio_var(confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def full_dashboard(
    scenario_id: str | None = Query(default=None),
    tenant_id:   str = Query(default="tenant-demo"),
    library:     ScenarioLibrary = Depends(_get_library),
    state       = Depends(_get_state),
) -> dict[str, Any]:
    baseline = _baseline_results.get(tenant_id)
    scenario_result = _sim_results.get(scenario_id) if scenario_id else None

    if not baseline or not scenario_result:
        raise HTTPException(404, "Run baseline and scenario first")

    mc         = _mc_results.get(scenario_id)
    risk_engine = RiskPropagationEngine(state)
    resilience  = risk_engine.compute_resilience()

    assembler = DashboardAssembler(baseline, scenario_result, mc, resilience)
    return assembler.build()


@router.get("/dashboard/scorecard")
async def supplier_scorecard(
    tenant_id: str = Query(default="tenant-demo"),
    state     = Depends(_get_state),
) -> list[dict[str, Any]]:
    return build_supplier_scorecard(state.suppliers)


@router.get("/dashboard/events")
async def event_timeline(
    scenario_id: str = Query(...),
) -> list[dict[str, Any]]:
    result = _sim_results.get(scenario_id)
    if not result:
        raise HTTPException(404, "Scenario result not found")
    return build_event_timeline(result.events_triggered)


# ─────────────────────────────────────────────────────────────────────────────
# Quick-fire what-if endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/whatif/supplier-loss")
async def whatif_supplier_loss(
    supplier_id: str = Query(...),
    tenant_id:   str = Query(default="tenant-demo"),
    state       = Depends(_get_state),
) -> dict[str, Any]:
    """Instant ripple-effect analysis for supplier bankruptcy."""
    engine = RiskPropagationEngine(state)
    impact = engine.simulate_supplier_failure(supplier_id)
    return {
        "supplier":          impact.origin_name,
        "total_impact_eur":  impact.total_eur,
        "affected_materials": len(impact.affected_materials),
        "impact_level":      impact.impact_level.value,
        "ripple_chain":      impact.ripple_chain[:10],
    }


@router.get("/whatif/price-shock")
async def whatif_price_shock(
    material_id: str   = Query(...),
    delta_pct:   float = Query(..., description="% price change, positive = increase"),
    tenant_id:   str   = Query(default="tenant-demo"),
    state       = Depends(_get_state),
) -> dict[str, Any]:
    engine = RiskPropagationEngine(state)
    return engine.simulate_price_shock(material_id, delta_pct)


@router.get("/whatif/fx-shock")
async def whatif_fx_shock(
    pair:      str   = Query(..., example="EUR/PLN"),
    delta_pct: float = Query(...),
    state     = Depends(_get_state),
) -> dict[str, Any]:
    engine = RiskPropagationEngine(state)
    return engine.simulate_fx_shock(pair, delta_pct)


@router.get("/whatif/correlation")
async def supplier_correlation_risk(
    tenant_id: str = Query(default="tenant-demo"),
    state     = Depends(_get_state),
) -> list[dict[str, Any]]:
    engine = RiskPropagationEngine(state)
    return engine.correlation_risk()


# ─────────────────────────────────────────────────────────────────────────────
# Event injection
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/simulate/event", status_code=202)
async def inject_event(
    body:       EventInjectIn,
    background: BackgroundTasks,
    state      = Depends(_get_state),
) -> dict[str, str]:
    from .event_simulation import ScheduledEvent, EventPriority
    from .models import RiskEventType
    from uuid import uuid4
    event = ScheduledEvent(
        fire_day=body.day_offset,
        priority=EventPriority.HIGH,
        event_id=str(uuid4()),
        event_type=RiskEventType(body.event_type),
        source_id=body.source_id,
        payload={"magnitude": body.magnitude},
    )
    async def _run_with_event() -> None:
        sim = EventDrivenSimulator(state, horizon_days=90, inject_events=[event])
        summary = sim.run()
        log.info("Event injection completed: %s", summary)

    background.add_task(_run_with_event)
    return {"status": "accepted"}


# ─────────────────────────────────────────────────────────────────────────────
# State snapshot
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/state")
async def get_state_snapshot(
    tenant_id: str = Query(default="tenant-demo"),
    state     = Depends(_get_state),
) -> dict[str, Any]:
    return {
        "supplier_count":  len(state.suppliers),
        "material_count":  len(state.materials),
        "order_count":     len(state.orders),
        "fx_pairs":        list(state.fx_rates.keys()),
        "active_events":   state.active_events,
        "total_spend":     state.total_spend,
        "service_level":   state.service_level,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — real-time KPI stream
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/stream")
async def kpi_stream(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            # Keep alive — real updates pushed via _broadcast_kpi()
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


async def _broadcast_kpi(result: SimulationResult) -> None:
    if not _ws_clients:
        return
    payload = {
        "type":           "simulation_complete",
        "scenario_id":    result.scenario_id,
        "cost_delta_pct": result.cost_delta_pct,
        "impact_level":   result.impact_level.value,
        "stockout_days":  result.stockout_days,
    }
    disconnected = []
    for client in _ws_clients:
        try:
            await client.send_json(payload)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        _ws_clients.remove(c)
