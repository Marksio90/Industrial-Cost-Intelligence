"""
Section 8 — Monitoring — Prometheus metrics for Cost Optimizer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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

_NS = "ici_cost_optimizer"

OPT_RUNS        = Counter(f"{_NS}_runs_total",           "Optimization runs",         ["product_id", "algorithm", "status"]) if _PROM else Counter("","", [])
OPT_DURATION    = Histogram(f"{_NS}_duration_s",         "Run duration seconds",      buckets=[0.1, 0.5, 1, 5, 15, 60]) if _PROM else Histogram("","")
SAVING_GAUGE    = Gauge(f"{_NS}_saving_eur",             "Best saving EUR",           ["product_id"]) if _PROM else Gauge("","", [])
SAVING_PCT      = Gauge(f"{_NS}_saving_pct",             "Best saving %",             ["product_id"]) if _PROM else Gauge("","", [])
N_EVALUATED     = Histogram(f"{_NS}_candidates_evaluated","Candidates evaluated",     buckets=[10,50,100,500,1000,5000,10000]) if _PROM else Histogram("","")
N_FEASIBLE      = Gauge(f"{_NS}_feasible_ratio",         "Feasible ratio",            ["product_id"]) if _PROM else Gauge("","", [])
BEST_COST       = Gauge(f"{_NS}_best_cost_eur",          "Best cost EUR/unit",        ["product_id"]) if _PROM else Gauge("","", [])
BEST_QUALITY    = Gauge(f"{_NS}_best_quality_index",     "Best quality index",        ["product_id"]) if _PROM else Gauge("","", [])
BEST_RISK       = Gauge(f"{_NS}_best_risk_score",        "Best risk score",           ["product_id"]) if _PROM else Gauge("","", [])
CONSTRAINT_VIOL = Counter(f"{_NS}_constraint_violations","Constraint violations",     ["constraint_name"]) if _PROM else Counter("","", [])


def record_run(
    product_id: str,
    algorithm: str,
    status: str,
    duration_s: float,
    saving_eur: float = 0,
    saving_pct: float = 0,
    n_evaluated: int = 0,
    n_feasible: int = 0,
    best_cost: float = 0,
    best_quality: float = 0,
    best_risk: float = 0,
) -> None:
    OPT_RUNS.labels(product_id=product_id, algorithm=algorithm, status=status).inc()
    OPT_DURATION.observe(duration_s)
    N_EVALUATED.observe(n_evaluated)
    if status == "ok":
        SAVING_GAUGE.labels(product_id=product_id).set(saving_eur)
        SAVING_PCT.labels(product_id=product_id).set(saving_pct)
        BEST_COST.labels(product_id=product_id).set(best_cost)
        BEST_QUALITY.labels(product_id=product_id).set(best_quality)
        BEST_RISK.labels(product_id=product_id).set(best_risk)
        if n_evaluated > 0:
            N_FEASIBLE.labels(product_id=product_id).set(n_feasible / n_evaluated)


def record_violation(constraint_name: str) -> None:
    CONSTRAINT_VIOL.labels(constraint_name=constraint_name).inc()


def optimizer_health_check() -> dict[str, Any]:
    return {"status": "ok", "component": "cost_optimizer"}


def prometheus_response() -> tuple[bytes, str]:
    if not _PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


@dataclass
class OptimizerHealthSnapshot:
    total_runs:     int   = 0
    avg_saving_pct: float = 0.0
    avg_duration_s: float = 0.0
    best_product_id: str  = ""
    best_saving_eur: float = 0.0
    collected_at:   float = field(default_factory=time.time)
