"""
Monitoring — Prometheus metrics for Decision Intelligence Engine.
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

_NS = "ici_decision"

ANALYSIS_RUNS    = Counter(f"{_NS}_analysis_runs_total",      "Analysis runs",         ["tenant_id", "status"]) if _PROM else Counter("","", [])
ANALYSIS_DURATION = Histogram(f"{_NS}_analysis_duration_ms",  "Duration ms",           buckets=[5, 10, 50, 100, 500, 1000]) if _PROM else Histogram("","")
RECS_GENERATED   = Counter(f"{_NS}_recommendations_total",    "Recommendations",       ["rec_type"]) if _PROM else Counter("","", [])
RECS_ACCEPTED    = Counter(f"{_NS}_recommendations_accepted", "Accepted recs",         ["rec_type"]) if _PROM else Counter("","", [])
CONFIDENCE_GAUGE = Gauge(f"{_NS}_avg_confidence",             "Avg confidence",        ["tenant_id"]) if _PROM else Gauge("","", [])
SAVING_GAUGE     = Gauge(f"{_NS}_total_saving_eur",           "Total saving EUR",      ["tenant_id"]) if _PROM else Gauge("","", [])
OPT_RUNS         = Counter(f"{_NS}_optimization_runs_total",  "Portfolio opt runs",    ["tenant_id"]) if _PROM else Counter("","", [])
FEEDBACK_EVENTS  = Counter(f"{_NS}_feedback_events_total",    "Feedback events",       ["outcome"]) if _PROM else Counter("","", [])


def record_analysis(tenant_id: str, status: str, duration_ms: float, recs: list | None = None) -> None:
    ANALYSIS_RUNS.labels(tenant_id=tenant_id, status=status).inc()
    ANALYSIS_DURATION.observe(duration_ms)
    if recs:
        total_saving = 0.0
        conf_sum = 0.0
        for r in recs:
            RECS_GENERATED.labels(rec_type=r.rec_type.value).inc()
            total_saving += r.expected_saving_eur
            conf_sum += r.confidence.overall
        CONFIDENCE_GAUGE.labels(tenant_id=tenant_id).set(conf_sum / len(recs))
        SAVING_GAUGE.labels(tenant_id=tenant_id).set(total_saving)


def record_feedback(rec_type: str, outcome: str) -> None:
    RECS_ACCEPTED.labels(rec_type=rec_type).inc()
    FEEDBACK_EVENTS.labels(outcome=outcome).inc()


def record_optimization(tenant_id: str) -> None:
    OPT_RUNS.labels(tenant_id=tenant_id).inc()


def decision_health_check() -> dict[str, Any]:
    return {"status": "ok", "component": "decision_engine"}


def prometheus_response() -> tuple[bytes, str]:
    if not _PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


@dataclass
class DecisionHealthSnapshot:
    tenant_id:        str
    analyses_today:   int   = 0
    avg_confidence:   float = 0.0
    total_saving_eur: float = 0.0
    top_rec_type:     str   = ""
    collected_at:     float = field(default_factory=time.time)
