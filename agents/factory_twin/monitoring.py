"""
Monitoring — Prometheus metrics dla factory digital twin.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram
    _PROM = True
except ImportError:
    _PROM = False

    class _Stub:
        def labels(self, **_): return self
        def inc(self, _=1): pass
        def set(self, _): pass
        def observe(self, _): pass

    Counter = Gauge = Histogram = lambda *a, **k: _Stub()

_NS = "ici_factory"

SIM_RUNS = Counter(f"{_NS}_simulation_runs_total", "Simulation runs", ["factory_id", "status"]) if _PROM else Counter("","", [])
SIM_DURATION = Histogram(f"{_NS}_simulation_duration_s", "Sim duration", buckets=[0.1, 0.5, 1, 5, 15, 60]) if _PROM else Histogram("","")
MACHINE_OEE = Gauge(f"{_NS}_machine_oee_pct", "Machine OEE", ["factory_id", "machine_id"]) if _PROM else Gauge("","", [])
FACTORY_THROUGHPUT = Gauge(f"{_NS}_throughput_h", "Throughput per hour", ["factory_id"]) if _PROM else Gauge("","", [])
FACTORY_WIP = Gauge(f"{_NS}_avg_wip", "Average WIP", ["factory_id"]) if _PROM else Gauge("","", [])
FACTORY_ENERGY = Gauge(f"{_NS}_energy_kwh", "Total energy kWh", ["factory_id"]) if _PROM else Gauge("","", [])
BREAKDOWN_EVENTS = Counter(f"{_NS}_breakdowns_total", "Machine breakdowns", ["factory_id", "machine_id"]) if _PROM else Counter("","", [])
OPT_RUNS = Counter(f"{_NS}_optimization_runs_total", "Optimization runs", ["factory_id", "algorithm"]) if _PROM else Counter("","", [])
OPT_IMPROVEMENT = Histogram(f"{_NS}_optimization_improvement_pct", "Optimization improvement %", buckets=[0,2,5,10,20,50]) if _PROM else Histogram("","")


def record_simulation(factory_id: str, status: str, duration_s: float, kpi: Any | None = None) -> None:
    SIM_RUNS.labels(factory_id=factory_id, status=status).inc()
    SIM_DURATION.observe(duration_s)
    if kpi:
        FACTORY_THROUGHPUT.labels(factory_id=factory_id).set(kpi.throughput_h)
        FACTORY_WIP.labels(factory_id=factory_id).set(kpi.avg_wip)
        FACTORY_ENERGY.labels(factory_id=factory_id).set(kpi.total_energy_kwh)
        for mid, mkpi in kpi.machine_kpis.items():
            MACHINE_OEE.labels(factory_id=factory_id, machine_id=mid).set(mkpi.oee)
            if mkpi.breakdown_count > 0:
                BREAKDOWN_EVENTS.labels(factory_id=factory_id, machine_id=mid).inc(mkpi.breakdown_count)


def record_optimization(factory_id: str, algorithm: str, improvement_pct: float) -> None:
    OPT_RUNS.labels(factory_id=factory_id, algorithm=algorithm).inc()
    OPT_IMPROVEMENT.observe(improvement_pct)


def factory_health_check() -> dict[str, Any]:
    return {"status": "ok", "component": "factory_twin"}


def prometheus_response() -> tuple[bytes, str]:
    if not _PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


@dataclass
class FactoryHealthSnapshot:
    factory_id:       str
    machine_count:    int   = 0
    worker_count:     int   = 0
    active_orders:    int   = 0
    avg_oee:          float = 0.0
    last_throughput:  float = 0.0
    collected_at:     float = field(default_factory=time.time)
