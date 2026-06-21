"""
Section 8 — Monitoring

Prometheus metryki cyfrowego bliźniaka kosztów:
  - Liczba obliczeń kosztu i czas trwania
  - Wyniki scenariuszy (delta %, impact level)
  - Monte Carlo: n_runs, VaR 95%, czas
  - Optymalizacja: savings %, czas
  - What-if query count per type
  - Model count / scenario count gauges
"""
from __future__ import annotations

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
    log.info("prometheus_client not installed — cost_twin metrics disabled")

    class _Stub:
        def labels(self, **_): return self
        def inc(self, _=1): pass
        def set(self, _): pass
        def observe(self, _): pass

    Counter = Gauge = Histogram = Summary = lambda *a, **k: _Stub()

_NS = "ici_cost"

# ── Computations ──────────────────────────────────────────────────────────────
COST_COMPUTATIONS = Counter(
    f"{_NS}_computations_total",
    "Number of cost breakdown computations",
    ["model_id", "country", "status"],
) if _PROM else Counter("", "", [])

COST_DURATION = Histogram(
    f"{_NS}_computation_duration_seconds",
    "Cost computation duration",
    ["country"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
) if _PROM else Histogram("", "", [])

# ── Scenario ──────────────────────────────────────────────────────────────────
SCENARIO_RUNS = Counter(
    f"{_NS}_scenario_runs_total",
    "Scenario run count",
    ["model_id", "impact_level"],
) if _PROM else Counter("", "", [])

SCENARIO_DELTA_PCT = Histogram(
    f"{_NS}_scenario_delta_pct",
    "Scenario cost delta %",
    ["model_id"],
    buckets=[-50, -20, -10, -5, 0, 5, 10, 20, 50, 100],
) if _PROM else Histogram("", "", [])

# ── Monte Carlo ───────────────────────────────────────────────────────────────
MC_RUNS_TOTAL = Counter(
    f"{_NS}_mc_runs_total",
    "Total Monte Carlo trial runs",
    ["model_id"],
) if _PROM else Counter("", "", [])

MC_DURATION = Histogram(
    f"{_NS}_mc_duration_seconds",
    "Monte Carlo run duration",
    ["model_id"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
) if _PROM else Histogram("", "", [])

MC_VAR_95 = Gauge(
    f"{_NS}_mc_var_95_eur",
    "VaR 95% from latest MC run",
    ["model_id", "run_id"],
) if _PROM else Gauge("", "", [])

# ── Optimization ──────────────────────────────────────────────────────────────
OPT_RUNS = Counter(
    f"{_NS}_optimization_runs_total",
    "Optimization run count",
    ["model_id"],
) if _PROM else Counter("", "", [])

OPT_SAVINGS_PCT = Histogram(
    f"{_NS}_optimization_savings_pct",
    "Optimization savings %",
    ["model_id"],
    buckets=[0, 2, 5, 10, 15, 20, 30, 50],
) if _PROM else Histogram("", "", [])

OPT_DURATION = Histogram(
    f"{_NS}_optimization_duration_seconds",
    "Optimization run duration",
    ["model_id"],
    buckets=[0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0],
) if _PROM else Histogram("", "", [])

# ── What-if ───────────────────────────────────────────────────────────────────
WHATIF_QUERIES = Counter(
    f"{_NS}_whatif_queries_total",
    "What-if query count",
    ["query_type", "model_id"],
) if _PROM else Counter("", "", [])

WHATIF_LATENCY = Histogram(
    f"{_NS}_whatif_latency_seconds",
    "What-if query latency",
    ["query_type"],
    buckets=[0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0],
) if _PROM else Histogram("", "", [])

# ── Model / scenario gauges ───────────────────────────────────────────────────
MODEL_COUNT = Gauge(
    f"{_NS}_models_total",
    "Number of cost models loaded",
) if _PROM else Gauge("", "")

SCENARIO_COUNT = Gauge(
    f"{_NS}_scenarios_total",
    "Number of saved scenarios",
) if _PROM else Gauge("", "")


# ─────────────────────────────────────────────────────────────────────────────
# Metric helpers
# ─────────────────────────────────────────────────────────────────────────────

def record_computation(model_id: str, country: str, duration_s: float, status: str) -> None:
    COST_COMPUTATIONS.labels(model_id=model_id, country=country, status=status).inc()
    COST_DURATION.labels(country=country).observe(duration_s)


def record_scenario(model_id: str, scenario_id: str, delta_pct: float, impact_level: str) -> None:
    SCENARIO_RUNS.labels(model_id=model_id, impact_level=impact_level).inc()
    SCENARIO_DELTA_PCT.labels(model_id=model_id).observe(delta_pct)


def record_mc_run(model_id: str, run_id: str, n_runs: int, duration_s: float, var_95: float) -> None:
    MC_RUNS_TOTAL.labels(model_id=model_id).inc(n_runs)
    MC_DURATION.labels(model_id=model_id).observe(duration_s)
    MC_VAR_95.labels(model_id=model_id, run_id=run_id).set(var_95)


def record_optimization(model_id: str, task_id: str, savings_pct: float, duration_s: float) -> None:
    OPT_RUNS.labels(model_id=model_id).inc()
    OPT_SAVINGS_PCT.labels(model_id=model_id).observe(savings_pct)
    OPT_DURATION.labels(model_id=model_id).observe(duration_s)


def record_whatif(query_type: str, model_id: str, duration_s: float) -> None:
    WHATIF_QUERIES.labels(query_type=query_type, model_id=model_id).inc()
    WHATIF_LATENCY.labels(query_type=query_type).observe(duration_s)


def update_model_count(count: int) -> None:
    MODEL_COUNT.set(count)


def update_scenario_count(count: int) -> None:
    SCENARIO_COUNT.set(count)


# ─────────────────────────────────────────────────────────────────────────────
# Health snapshot
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostTwinHealthSnapshot:
    model_count:         int   = 0
    scenario_count:      int   = 0
    mc_runs_queued:      int   = 0
    optimization_queued: int   = 0
    last_computation_s:  float = 0.0
    collected_at:        float = field(default_factory=time.time)


def cost_health_check() -> dict[str, Any]:
    return {"status": "ok", "component": "cost_twin"}


def prometheus_response() -> tuple[bytes, str]:
    if not _PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
