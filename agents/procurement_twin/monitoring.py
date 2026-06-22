"""
Section 8 — Monitoring

Prometheus metryki cyfrowego bliźniaka:
  - Liczba uruchomionych symulacji i czas trwania
  - Wyniki scenariuszy (koszt Δ, service level, lead time)
  - Monte Carlo: czas obliczeń, n_runs, VaR
  - Risk events count per type
  - Resilience score per tenant
  - WebSocket client count
  - Queue depth (pending events)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus (graceful degradation)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from prometheus_client import Counter, Gauge, Histogram, Summary
    _PROM = True
except ImportError:
    _PROM = False
    log.info("prometheus_client not installed — twin metrics disabled")

    class _Stub:
        def labels(self, **_): return self
        def inc(self, _=1): pass
        def set(self, _): pass
        def observe(self, _): pass
        def time(self): return _NullCtx()

    class _NullCtx:
        def __enter__(self): return self
        def __exit__(self, *_): pass

    Counter = Gauge = Histogram = Summary = lambda *a, **k: _Stub()

_NS = "ici_twin"

# ── Simulation counters ───────────────────────────────────────────────────────
SIM_RUNS_TOTAL = Counter(
    f"{_NS}_simulation_runs_total",
    "Number of simulation runs",
    ["tenant_id", "scenario_type", "status"],
) if _PROM else Counter("", "", [])

SIM_DURATION = Histogram(
    f"{_NS}_simulation_duration_seconds",
    "Simulation run duration",
    ["scenario_type"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
) if _PROM else Histogram("", "", [])

# ── Scenario KPIs ─────────────────────────────────────────────────────────────
SCENARIO_COST_DELTA = Histogram(
    f"{_NS}_scenario_cost_delta_pct",
    "Cost delta percentage per scenario",
    ["tenant_id", "impact_level"],
    buckets=[-50, -20, -10, -5, 0, 5, 10, 20, 50, 100],
) if _PROM else Histogram("", "", [])

SCENARIO_SERVICE_LEVEL = Gauge(
    f"{_NS}_scenario_min_service_level",
    "Minimum service level achieved in scenario",
    ["tenant_id", "scenario_id"],
) if _PROM else Gauge("", "", [])

SCENARIO_STOCKOUT_DAYS = Gauge(
    f"{_NS}_scenario_stockout_days",
    "Stockout days in scenario",
    ["tenant_id", "scenario_id"],
) if _PROM else Gauge("", "", [])

# ── Monte Carlo ───────────────────────────────────────────────────────────────
MC_RUNS_TOTAL = Counter(
    f"{_NS}_mc_runs_total",
    "Total Monte Carlo trial runs",
    ["tenant_id"],
) if _PROM else Counter("", "", [])

MC_DURATION = Histogram(
    f"{_NS}_mc_duration_seconds",
    "Monte Carlo batch duration",
    ["tenant_id"],
    buckets=[1, 5, 15, 30, 60, 120, 300, 600],
) if _PROM else Histogram("", "", [])

MC_VAR_95 = Gauge(
    f"{_NS}_mc_var_95_eur",
    "VaR 95% from latest MC run",
    ["tenant_id", "scenario_id"],
) if _PROM else Gauge("", "", [])

MC_STOCKOUT_PROB = Gauge(
    f"{_NS}_mc_stockout_probability",
    "Stockout probability from MC",
    ["tenant_id", "scenario_id"],
) if _PROM else Gauge("", "", [])

# ── Risk events ───────────────────────────────────────────────────────────────
RISK_EVENTS = Counter(
    f"{_NS}_risk_events_total",
    "Risk events triggered",
    ["tenant_id", "event_type", "impact_level"],
) if _PROM else Counter("", "", [])

# ── Resilience ────────────────────────────────────────────────────────────────
RESILIENCE_SCORE = Gauge(
    f"{_NS}_resilience_score",
    "Overall supply chain resilience score (0–1)",
    ["tenant_id"],
) if _PROM else Gauge("", "")

SINGLE_SOURCE_COUNT = Gauge(
    f"{_NS}_single_source_materials",
    "Materials with single supplier",
    ["tenant_id"],
) if _PROM else Gauge("", "", [])

HIGH_RISK_SPEND_PCT = Gauge(
    f"{_NS}_high_risk_spend_pct",
    "Fraction of spend with high-risk suppliers",
    ["tenant_id"],
) if _PROM else Gauge("", "", [])

# ── WebSocket ─────────────────────────────────────────────────────────────────
WS_CLIENTS = Gauge(
    f"{_NS}_websocket_clients",
    "Active WebSocket clients",
) if _PROM else Gauge("", "")

# ── What-if queries ───────────────────────────────────────────────────────────
WHATIF_QUERIES = Counter(
    f"{_NS}_whatif_queries_total",
    "What-if query calls",
    ["query_type", "tenant_id"],
) if _PROM else Counter("", "", [])

WHATIF_LATENCY = Histogram(
    f"{_NS}_whatif_latency_seconds",
    "What-if query response time",
    ["query_type"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
) if _PROM else Histogram("", "", [])


# ─────────────────────────────────────────────────────────────────────────────
# Metric helpers
# ─────────────────────────────────────────────────────────────────────────────

def record_simulation(
    tenant_id:     str,
    scenario_type: str,
    status:        str,
    duration_s:    float,
) -> None:
    SIM_RUNS_TOTAL.labels(tenant_id=tenant_id, scenario_type=scenario_type, status=status).inc()
    SIM_DURATION.labels(scenario_type=scenario_type).observe(duration_s)


def record_scenario_result(
    tenant_id:    str,
    scenario_id:  str,
    cost_delta:   float,
    service_lvl:  float,
    stockouts:    int,
    impact_level: str,
) -> None:
    SCENARIO_COST_DELTA.labels(tenant_id=tenant_id, impact_level=impact_level).observe(cost_delta)
    SCENARIO_SERVICE_LEVEL.labels(tenant_id=tenant_id, scenario_id=scenario_id).set(service_lvl)
    SCENARIO_STOCKOUT_DAYS.labels(tenant_id=tenant_id, scenario_id=scenario_id).set(stockouts)


def record_mc_result(
    tenant_id:    str,
    scenario_id:  str,
    n_runs:       int,
    duration_s:   float,
    var_95:       float,
    stockout_prob: float,
) -> None:
    MC_RUNS_TOTAL.labels(tenant_id=tenant_id).inc(n_runs)
    MC_DURATION.labels(tenant_id=tenant_id).observe(duration_s)
    MC_VAR_95.labels(tenant_id=tenant_id, scenario_id=scenario_id).set(var_95)
    MC_STOCKOUT_PROB.labels(tenant_id=tenant_id, scenario_id=scenario_id).set(stockout_prob)


def record_risk_event(tenant_id: str, event_type: str, impact_level: str) -> None:
    RISK_EVENTS.labels(tenant_id=tenant_id, event_type=event_type, impact_level=impact_level).inc()


def record_resilience(
    tenant_id:        str,
    score:            float,
    single_source:    int,
    high_risk_pct:    float,
) -> None:
    RESILIENCE_SCORE.labels(tenant_id=tenant_id).set(score)
    SINGLE_SOURCE_COUNT.labels(tenant_id=tenant_id).set(single_source)
    HIGH_RISK_SPEND_PCT.labels(tenant_id=tenant_id).set(high_risk_pct)


def record_whatif(query_type: str, tenant_id: str, duration_s: float) -> None:
    WHATIF_QUERIES.labels(query_type=query_type, tenant_id=tenant_id).inc()
    WHATIF_LATENCY.labels(query_type=query_type).observe(duration_s)


# ─────────────────────────────────────────────────────────────────────────────
# Health snapshot
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TwinHealthSnapshot:
    tenant_id:          str
    supplier_count:     int = 0
    material_count:     int = 0
    active_scenarios:   int = 0
    resilience_score:   float = 0.0
    single_source_mats: int = 0
    high_risk_pct:      float = 0.0
    last_sim_duration:  float = 0.0
    pending_mc_runs:    int = 0
    ws_clients:         int = 0
    collected_at:       float = field(default_factory=time.time)


class TwinMonitor:
    """
    Periodic metrics collector for the procurement twin.
    Reads state and writes Prometheus gauges every `interval_s` seconds.
    """

    def __init__(
        self,
        state_provider: Any,     # callable() → SimulationState
        library:        Any,     # ScenarioLibrary
        tenant_ids:     list[str],
        interval_s:     int = 60,
    ) -> None:
        self._state_fn  = state_provider
        self._library   = library
        self._tenants   = tenant_ids
        self._interval  = interval_s
        self._task:     asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("TwinMonitor started (interval=%ds, tenants=%s)", self._interval, self._tenants)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self._collect_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("TwinMonitor error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _collect_all(self) -> None:
        for tid in self._tenants:
            await self._collect_tenant(tid)

    async def _collect_tenant(self, tenant_id: str) -> TwinHealthSnapshot:
        snap = TwinHealthSnapshot(tenant_id=tenant_id)
        try:
            state = self._state_fn(tenant_id)
            snap.supplier_count = len(state.suppliers)
            snap.material_count = len(state.materials)

            from .risk_propagation import RiskPropagationEngine
            engine   = RiskPropagationEngine(state)
            report   = engine.compute_resilience()
            snap.resilience_score   = report.overall_score
            snap.single_source_mats = report.single_source_count
            snap.high_risk_pct      = report.high_risk_spend_pct

            record_resilience(tenant_id, report.overall_score, report.single_source_count, report.high_risk_spend_pct)
        except Exception as exc:
            log.warning("Tenant %s collect failed: %s", tenant_id, exc)

        snap.active_scenarios = len(self._library.list_all(tenant_id))
        return snap

    async def snapshot(self, tenant_id: str) -> TwinHealthSnapshot:
        return await self._collect_tenant(tenant_id)


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint helpers
# ─────────────────────────────────────────────────────────────────────────────

def twin_health_check() -> dict[str, Any]:
    return {"status": "ok", "component": "procurement_twin"}


def prometheus_response() -> tuple[bytes, str]:
    if not _PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
