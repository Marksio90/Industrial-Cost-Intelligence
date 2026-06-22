"""
Section 7 — API

FastAPI router dla factory digital twin:

  POST   /factory/layouts                          → utwórz layout fabryki
  GET    /factory/layouts/{factory_id}             → pobierz layout
  DELETE /factory/layouts/{factory_id}             → usuń layout

  POST   /factory/layouts/{factory_id}/machines    → dodaj maszynę
  POST   /factory/layouts/{factory_id}/workers     → dodaj pracownika
  POST   /factory/layouts/{factory_id}/buffers     → dodaj bufor
  POST   /factory/layouts/{factory_id}/orders      → dodaj zlecenie produkcyjne
  POST   /factory/layouts/{factory_id}/products    → zdefiniuj produkt

  POST   /factory/simulate/{factory_id}            → uruchom symulację (async)
  GET    /factory/simulate/{sim_id}/result         → wynik symulacji
  GET    /factory/simulate/{sim_id}/kpi            → KPI symulacji

  GET    /factory/graph/{factory_id}               → factory graph (nodes + edges)
  GET    /factory/schedule/{factory_id}            → wygeneruj plan produkcji
  GET    /factory/capacity/{factory_id}            → CRP (Capacity Requirements Planning)

  POST   /factory/optimize/{factory_id}            → optymalizacja (async)
  GET    /factory/optimize/{task_id}               → wynik optymalizacji

  GET    /factory/dashboard/{sim_id}               → pełny dashboard
  GET    /factory/dashboard/{sim_id}/map           → mapa fabryki
  GET    /factory/dashboard/{sim_id}/gantt         → Gantt chart
  GET    /factory/dashboard/{sim_id}/events        → event timeline

  WS     /factory/stream/{factory_id}              → live KPI stream

  GET    /factory/health                           → health check
  GET    /factory/metrics                          → Prometheus metrics
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from .models import (
    FactoryLayout, Machine, Worker, Buffer, TransportUnit,
    ProductDefinition, ProductionOrder, RoutingStep,
    ManufacturingCell, FailureModel, EnergyProfile, SetupMatrix,
    SimulationConfig, SimulationResult,
    MachineType, WorkerStatus, BufferType, TransportType, ShiftType,
    SchedulingRule, OptimizationGoal,
    MachineIn, WorkerIn, BufferIn, OrderIn, SimConfigIn, OptimizeIn,
    WorkerSkill,
)
from .factory_graph import FactoryGraphBuilder, LayoutAutoplacer
from .simulation_model import FactorySimulator
from .scheduling import StaticScheduler, CapacityPlanner
from .optimization import OptimizationEngine, OptimizationReport
from .dashboard import DashboardAssembler
from .monitoring import record_simulation, record_optimization, factory_health_check, prometheus_response

log = logging.getLogger(__name__)
router = APIRouter(prefix="/factory", tags=["factory_twin"])

# ─────────────────────────────────────────────────────────────────────────────
# In-memory stores
# ─────────────────────────────────────────────────────────────────────────────

_layouts:    dict[str, FactoryLayout]     = {}
_results:    dict[str, SimulationResult]  = {}
_opt_reports: dict[str, dict[str, Any]]  = {}
_ws_clients: dict[str, list[WebSocket]]  = {}

_graph_builder = FactoryGraphBuilder()
_autoplacer    = LayoutAutoplacer()
_scheduler     = StaticScheduler()
_cap_planner   = CapacityPlanner()
_opt_engine    = OptimizationEngine()
_dashboard     = DashboardAssembler()


# ─────────────────────────────────────────────────────────────────────────────
# Additional request models
# ─────────────────────────────────────────────────────────────────────────────

class LayoutCreateIn(BaseModel):
    factory_id:   str | None = None
    name:         str = "Factory"
    energy_tariff: dict[str, float] = {}  # hour_str → EUR/kWh


class ProductIn(BaseModel):
    product_id:    str
    name:          str
    family:        str = "default"
    priority:      int = 5
    weight_kg:     float = 1.0
    raw_material_kg: float = 1.0
    routing:       list[dict[str, Any]] = []


class EdgeIn(BaseModel):
    from_id:    str
    to_id:      str
    distance_m: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Layout CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/layouts", status_code=201)
async def create_layout(payload: LayoutCreateIn) -> dict[str, str]:
    fid = payload.factory_id or str(uuid.uuid4())
    tariff = {int(k): v for k, v in payload.energy_tariff.items()} if payload.energy_tariff else {h: 0.15 for h in range(24)}
    _layouts[fid] = FactoryLayout(
        factory_id    = fid,
        name          = payload.name,
        energy_tariff = tariff,
    )
    return {"factory_id": fid}


@router.get("/layouts/{factory_id}")
async def get_layout(factory_id: str) -> dict[str, Any]:
    layout = _get_layout(factory_id)
    return {
        "factory_id":    layout.factory_id,
        "name":          layout.name,
        "machine_count": len(layout.machines),
        "worker_count":  len(layout.workers),
        "buffer_count":  len(layout.buffers),
        "order_count":   len(layout.orders),
        "product_count": len(layout.products),
    }


@router.delete("/layouts/{factory_id}", status_code=204)
async def delete_layout(factory_id: str) -> None:
    if factory_id not in _layouts:
        raise HTTPException(404, f"Layout '{factory_id}' not found")
    del _layouts[factory_id]


# ─────────────────────────────────────────────────────────────────────────────
# Add entities
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/layouts/{factory_id}/machines", status_code=201)
async def add_machine(factory_id: str, payload: MachineIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    machine = Machine(
        machine_id   = payload.machine_id,
        name         = payload.name,
        machine_type = payload.machine_type,
        cell_id      = payload.cell_id,
        cycle_time_s = payload.cycle_time_s,
        failure_model= FailureModel(mtbf_hours=payload.mtbf_hours, mttr_hours=payload.mttr_hours),
        energy       = EnergyProfile(idle_kw=payload.idle_kw, processing_kw=payload.processing_kw),
        defect_rate  = payload.defect_rate,
        x            = payload.x,
        y            = payload.y,
    )
    layout.machines[payload.machine_id] = machine
    return {"machine_id": payload.machine_id}


@router.post("/layouts/{factory_id}/workers", status_code=201)
async def add_worker(factory_id: str, payload: WorkerIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    worker = Worker(
        worker_id = payload.worker_id,
        name      = payload.name,
        cell_id   = payload.cell_id,
        shift     = payload.shift,
        skills    = [WorkerSkill(machine_types=payload.skill_types, skill_level=payload.skill_level)],
    )
    layout.workers[payload.worker_id] = worker
    return {"worker_id": payload.worker_id}


@router.post("/layouts/{factory_id}/buffers", status_code=201)
async def add_buffer(factory_id: str, payload: BufferIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    buf = Buffer(
        buffer_id   = payload.buffer_id,
        name        = payload.name,
        buffer_type = payload.buffer_type,
        cell_id     = payload.cell_id,
        capacity    = payload.capacity,
        x           = payload.x,
        y           = payload.y,
    )
    layout.buffers[payload.buffer_id] = buf
    return {"buffer_id": payload.buffer_id}


@router.post("/layouts/{factory_id}/orders", status_code=201)
async def add_order(factory_id: str, payload: OrderIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    order = ProductionOrder(
        order_id   = payload.order_id,
        product_id = payload.product_id,
        quantity   = payload.quantity,
        due_date   = payload.due_date,
        priority   = payload.priority,
    )
    layout.orders[payload.order_id] = order
    return {"order_id": payload.order_id}


@router.post("/layouts/{factory_id}/products", status_code=201)
async def add_product(factory_id: str, payload: ProductIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    routing = [
        RoutingStep(
            step_id      = r.get("step_id", str(i)),
            sequence     = r.get("sequence", i),
            machine_id   = r["machine_id"],
            process_type = r.get("process_type", "generic"),
            cycle_time_s = float(r.get("cycle_time_s", 60)),
            setup_time_s = float(r.get("setup_time_s", 0)),
            yield_pct    = float(r.get("yield_pct", 1.0)),
        )
        for i, r in enumerate(payload.routing)
    ]
    prod = ProductDefinition(
        product_id = payload.product_id,
        name       = payload.name,
        family     = payload.family,
        routing    = routing,
        priority   = payload.priority,
        weight_kg  = payload.weight_kg,
    )
    layout.products[payload.product_id] = prod
    return {"product_id": payload.product_id}


@router.post("/layouts/{factory_id}/edges", status_code=201)
async def add_edge(factory_id: str, payload: EdgeIn) -> dict[str, str]:
    layout = _get_layout(factory_id)
    layout.edges[(payload.from_id, payload.to_id)] = payload.distance_m
    return {"edge": f"{payload.from_id}→{payload.to_id}"}


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/simulate/{factory_id}", status_code=202)
async def run_simulation(
    factory_id:       str,
    cfg:              SimConfigIn,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    layout = _get_layout(factory_id)
    sim_id = str(uuid.uuid4())

    async def _run() -> None:
        t0 = time.perf_counter()
        try:
            config = SimulationConfig(
                sim_id           = sim_id,
                horizon_hours    = cfg.horizon_hours,
                time_step_s      = cfg.time_step_s,
                warm_up_hours    = cfg.warm_up_hours,
                scheduling_rule  = cfg.scheduling_rule,
                seed             = cfg.seed,
                simulate_failures= cfg.simulate_failures,
                simulate_energy  = cfg.simulate_energy,
            )
            sim    = FactorySimulator(layout, config)
            result = sim.run()
            _results[sim_id] = result
            record_simulation(factory_id, "ok", time.perf_counter() - t0, result.factory_kpi)
            # Broadcast KPI to WebSocket clients
            await _broadcast(factory_id, {
                "event":       "sim_complete",
                "sim_id":      sim_id,
                "throughput":  result.factory_kpi.throughput_total,
                "avg_oee":     result.factory_kpi.avg_oee,
            })
        except Exception as exc:
            record_simulation(factory_id, "error", time.perf_counter() - t0)
            log.error("Simulation %s failed: %s", sim_id, exc)

    background_tasks.add_task(_run)
    return {"sim_id": sim_id, "status": "queued"}


@router.get("/simulate/{sim_id}/result")
async def get_sim_result(sim_id: str) -> dict[str, Any]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found or still running")
    kpi = result.factory_kpi
    return {
        "sim_id":         sim_id,
        "horizon_h":      result.config.horizon_hours,
        "scheduling_rule": result.config.scheduling_rule.value,
        "throughput":     kpi.throughput_total,
        "throughput_h":   kpi.throughput_h,
        "avg_oee":        kpi.avg_oee,
        "avg_wip":        kpi.avg_wip,
        "lead_time_h":    kpi.avg_lead_time_h,
        "on_time_pct":    kpi.on_time_delivery,
        "scrap_rate":     kpi.scrap_rate,
        "energy_kwh":     kpi.total_energy_kwh,
        "energy_eur":     kpi.energy_cost_eur,
        "bottleneck":     kpi.bottleneck_id,
        "late_orders":    kpi.late_orders,
        "run_time_s":     result.run_time_s,
    }


@router.get("/simulate/{sim_id}/kpi")
async def get_sim_kpi(sim_id: str) -> dict[str, Any]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found")
    return {
        mid: {
            "oee":          kpi.oee,
            "utilization":  kpi.utilization_pct,
            "availability": kpi.availability_pct,
            "performance":  kpi.performance_pct,
            "quality":      kpi.quality_pct,
            "throughput_h": kpi.throughput_h,
            "breakdowns":   kpi.breakdown_count,
            "energy_kwh":   kpi.energy_kwh,
        }
        for mid, kpi in result.factory_kpi.machine_kpis.items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graph, Schedule, Capacity
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/graph/{factory_id}")
async def get_factory_graph(factory_id: str) -> dict[str, Any]:
    layout = _get_layout(factory_id)
    graph  = _graph_builder.build(layout)
    _autoplacer.place(graph)
    return graph.to_dict()


@router.get("/schedule/{factory_id}")
async def get_schedule(
    factory_id:   str,
    rule:         SchedulingRule = Query(default=SchedulingRule.EDD),
    horizon_h:    float          = Query(default=8.0),
) -> dict[str, Any]:
    layout = _get_layout(factory_id)
    plan   = _scheduler.schedule(layout, rule, horizon_h * 3600)
    return {
        "factory_id":    factory_id,
        "rule":          rule.value,
        "makespan_h":    plan.makespan_h,
        "total_setup_s": plan.total_setup_s,
        "tardiness_s":   plan.tardiness_s,
        "machine_util":  plan.machine_util,
        "gantt":         plan.gantt[:500],
    }


@router.get("/capacity/{factory_id}")
async def get_capacity(
    factory_id: str,
    horizon_h:  float = Query(default=8.0),
) -> dict[str, Any]:
    layout = _get_layout(factory_id)
    loads  = _cap_planner.plan(layout, horizon_h)
    return {
        "factory_id":  factory_id,
        "horizon_h":   horizon_h,
        "overloaded":  [l.machine_id for l in loads if l.overloaded],
        "loads": [
            {
                "machine_id":  l.machine_id,
                "required_h":  l.required_h,
                "available_h": l.available_h,
                "load_pct":    l.load_pct,
                "overloaded":  l.overloaded,
            }
            for l in loads
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Optimization
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/optimize/{factory_id}", status_code=202)
async def optimize_factory(
    factory_id:       str,
    payload:          OptimizeIn,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    layout  = _get_layout(factory_id)
    task_id = str(uuid.uuid4())

    async def _run() -> None:
        t0 = time.perf_counter()
        config = SimulationConfig(horizon_hours=4.0, simulate_failures=True)
        report = _opt_engine.optimize(
            layout,
            config,
            goal       = payload.goal,
            algorithm  = payload.algorithm,
            max_iter   = payload.max_iterations,
            pop_size   = payload.population_size,
        )
        _opt_reports[task_id] = {
            "task_id":        task_id,
            "factory_id":     factory_id,
            "goal":           report.goal.value,
            "algorithm":      report.algorithm,
            "baseline_score": report.baseline_score,
            "best_score":     report.best_score,
            "improvement_pct": report.improvement_pct,
            "best_params":    report.best_params,
            "recommendations": report.recommendations,
            "run_time_s":     report.run_time_s,
            "history": [
                {"step": i, "score": c.score, "params": c.params, "gen": c.generation}
                for i, c in enumerate(report.history)
            ],
        }
        record_optimization(factory_id, report.algorithm, report.improvement_pct)

    background_tasks.add_task(_run)
    return {"task_id": task_id, "status": "queued"}


@router.get("/optimize/{task_id}")
async def get_optimization_result(task_id: str) -> dict[str, Any]:
    report = _opt_reports.get(task_id)
    if not report:
        raise HTTPException(404, "Optimization task not found or still running")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{sim_id}")
async def get_dashboard(sim_id: str) -> dict[str, Any]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found")
    return _dashboard.build(result)


@router.get("/dashboard/{sim_id}/map")
async def get_factory_map(sim_id: str) -> dict[str, Any]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found")
    # Try to find factory_id from config
    from .dashboard import build_factory_map
    # We'd need to store factory_id in result — use sim_id as factory fallback
    for fid, layout in _layouts.items():
        graph = _graph_builder.build(layout)
        # Update graph with latest KPI
        for mid, mkpi in result.factory_kpi.machine_kpis.items():
            graph.update_node_state(mid, mkpi.utilization_pct / 100, 0, "ok")
        _autoplacer.place(graph)
        return build_factory_map(graph)
    raise HTTPException(404, "No layout found")


@router.get("/dashboard/{sim_id}/gantt")
async def get_gantt(
    sim_id:   str,
    max_rows: int = Query(default=500, le=2000),
) -> dict[str, Any]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found")
    from .dashboard import build_gantt_chart
    return build_gantt_chart(result.gantt_data, max_rows)


@router.get("/dashboard/{sim_id}/events")
async def get_events(
    sim_id:    str,
    event_type: str | None = Query(default=None),
    limit:      int        = Query(default=200, le=5000),
) -> list[dict[str, Any]]:
    result = _results.get(sim_id)
    if not result:
        raise HTTPException(404, "Simulation not found")
    log_data = result.event_log
    if event_type:
        log_data = [e for e in log_data if e.get("type") == event_type]
    return log_data[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket live stream
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/stream/{factory_id}")
async def websocket_stream(websocket: WebSocket, factory_id: str) -> None:
    await websocket.accept()
    _ws_clients.setdefault(factory_id, []).append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Echo heartbeat
            await websocket.send_json({"type": "pong", "factory_id": factory_id})
    except WebSocketDisconnect:
        clients = _ws_clients.get(factory_id, [])
        if websocket in clients:
            clients.remove(websocket)


async def _broadcast(factory_id: str, payload: dict) -> None:
    clients = _ws_clients.get(factory_id, [])
    dead: list[WebSocket] = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.remove(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Health + Metrics
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        **factory_health_check(),
        "layouts":     len(_layouts),
        "simulations": len(_results),
        "opt_tasks":   len(_opt_reports),
        "ws_clients":  sum(len(v) for v in _ws_clients.values()),
    }


@router.get("/metrics")
async def metrics():
    from fastapi.responses import Response
    body, ct = prometheus_response()
    return Response(content=body, media_type=ct)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_layout(factory_id: str) -> FactoryLayout:
    layout = _layouts.get(factory_id)
    if not layout:
        raise HTTPException(404, f"Factory layout '{factory_id}' not found")
    return layout
