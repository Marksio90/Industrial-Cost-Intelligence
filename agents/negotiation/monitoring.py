"""
Section 10 — Monitoring

Prometheus metrics for the negotiation system.
All metrics are labeled by tenant_id + strategy + material_class.

Key KPIs tracked:
  - negotiation_sessions_total          (by state=AGREEMENT|DEADLOCK|WITHDRAWN)
  - negotiation_rounds_per_session      (histogram)
  - negotiation_discount_pct            (histogram — value captured vs initial ask)
  - negotiation_value_saved_eur_total   (counter)
  - negotiation_duration_seconds        (histogram — time from open to close)
  - negotiation_acceptance_probability  (gauge — last prediction per active session)
  - negotiation_round_duration_seconds  (histogram — time supplier takes to respond)
  - negotiation_risk_controls_total     (counter by control_name + action)
  - negotiation_approval_requests_total (counter by risk_level + outcome)
  - negotiation_auto_accept_total       (counter — no human needed)
  - negotiation_strategy_distribution   (gauge — sessions per strategy)
  - supplier_behavior_profile_confidence (gauge per supplier)
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

try:
    from prometheus_client import Counter, Gauge, Histogram
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Metric definitions
# ─────────────────────────────────────────────────────────────────────────────

def _counter(name: str, doc: str, labels: list[str]) -> Any:
    if _HAS_PROMETHEUS:
        return Counter(name, doc, labels)
    return _NoopMetric()


def _histogram(name: str, doc: str, labels: list[str], buckets: list[float] | None = None) -> Any:
    if _HAS_PROMETHEUS:
        kwargs: dict[str, Any] = {}
        if buckets:
            kwargs["buckets"] = buckets
        return Histogram(name, doc, labels, **kwargs)
    return _NoopMetric()


def _gauge(name: str, doc: str, labels: list[str]) -> Any:
    if _HAS_PROMETHEUS:
        return Gauge(name, doc, labels)
    return _NoopMetric()


NEGOTIATION_SESSIONS_TOTAL = _counter(
    "ici_negotiation_sessions_total",
    "Total negotiation sessions by outcome",
    ["tenant_id", "strategy", "material_class", "state"],
)

NEGOTIATION_ROUNDS = _histogram(
    "ici_negotiation_rounds_per_session",
    "Number of rounds taken per session",
    ["tenant_id", "strategy", "material_class"],
    buckets=[1, 2, 3, 4, 5, 6, 8, 10, 15, 20],
)

NEGOTIATION_DISCOUNT_PCT = _histogram(
    "ici_negotiation_discount_pct",
    "Discount achieved vs initial ask (%)",
    ["tenant_id", "strategy", "material_class"],
    buckets=[0, 2, 5, 8, 10, 12, 15, 20, 25, 30, 40],
)

NEGOTIATION_VALUE_SAVED = _counter(
    "ici_negotiation_value_saved_eur_total",
    "Total EUR saved vs initial ask across all negotiations",
    ["tenant_id", "material_class"],
)

NEGOTIATION_DURATION = _histogram(
    "ici_negotiation_duration_seconds",
    "Total negotiation session duration (open to close)",
    ["tenant_id", "strategy", "result"],
    buckets=[300, 1800, 3600, 7200, 86400, 172800, 604800],
)

NEGOTIATION_ROUND_DURATION = _histogram(
    "ici_negotiation_round_response_seconds",
    "Time for supplier to respond to each round",
    ["tenant_id"],
    buckets=[300, 1800, 7200, 21600, 86400, 172800],
)

ACCEPTANCE_PROBABILITY = _gauge(
    "ici_negotiation_acceptance_probability",
    "Latest acceptance probability prediction per active session",
    ["session_id", "tenant_id"],
)

RISK_CONTROLS_TOTAL = _counter(
    "ici_negotiation_risk_controls_total",
    "Risk controls fired",
    ["control_name", "action", "tenant_id"],
)

APPROVAL_REQUESTS_TOTAL = _counter(
    "ici_negotiation_approval_requests_total",
    "Human approval requests",
    ["risk_level", "checkpoint_type", "outcome", "tenant_id"],
)

AUTO_ACCEPT_TOTAL = _counter(
    "ici_negotiation_auto_accept_total",
    "Auto-accepted supplier offers (no human needed)",
    ["tenant_id", "material_class"],
)

STRATEGY_ACTIVE = _gauge(
    "ici_negotiation_strategy_active_sessions",
    "Active sessions per strategy",
    ["strategy", "tenant_id"],
)

BEHAVIOR_PROFILE_CONFIDENCE = _gauge(
    "ici_supplier_behavior_profile_confidence",
    "Confidence score of supplier behavior model",
    ["supplier_id", "tenant_id"],
)

OFFER_PRICE_RATIO = _histogram(
    "ici_negotiation_offer_price_ratio",
    "Offer price as ratio of initial ask",
    ["tenant_id", "strategy", "round"],
    buckets=[0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.92, 0.95, 0.98, 1.0, 1.05],
)


# ─────────────────────────────────────────────────────────────────────────────
# Noop fallback when prometheus_client not installed
# ─────────────────────────────────────────────────────────────────────────────

class _NoopMetric:
    def labels(self, **_: Any) -> "_NoopMetric":
        return self
    def inc(self, *_: Any) -> None: ...
    def observe(self, *_: Any) -> None: ...
    def set(self, *_: Any) -> None: ...
    def time(self) -> Any:
        from contextlib import nullcontext
        return nullcontext()


# ─────────────────────────────────────────────────────────────────────────────
# Metric recording helpers
# ─────────────────────────────────────────────────────────────────────────────

def record_session_closed(
    tenant_id:      str,
    strategy:       str,
    material_class: str,
    state:          str,            # AGREEMENT | DEADLOCK | WITHDRAWN | ESCALATED
    rounds_taken:   int,
    discount_pct:   float,
    value_saved:    float,
    duration_s:     float,
) -> None:
    NEGOTIATION_SESSIONS_TOTAL.labels(
        tenant_id      = tenant_id,
        strategy       = strategy,
        material_class = material_class,
        state          = state,
    ).inc()

    NEGOTIATION_ROUNDS.labels(
        tenant_id=tenant_id, strategy=strategy, material_class=material_class
    ).observe(rounds_taken)

    NEGOTIATION_DISCOUNT_PCT.labels(
        tenant_id=tenant_id, strategy=strategy, material_class=material_class
    ).observe(discount_pct * 100)

    NEGOTIATION_VALUE_SAVED.labels(
        tenant_id=tenant_id, material_class=material_class
    ).inc(max(0.0, value_saved))

    NEGOTIATION_DURATION.labels(
        tenant_id=tenant_id, strategy=strategy, result=state
    ).observe(duration_s)


def record_round_response(tenant_id: str, response_time_s: float) -> None:
    NEGOTIATION_ROUND_DURATION.labels(tenant_id=tenant_id).observe(response_time_s)


def record_acceptance_probability(session_id: str, tenant_id: str, prob: float) -> None:
    ACCEPTANCE_PROBABILITY.labels(session_id=session_id, tenant_id=tenant_id).set(prob)


def record_risk_control(control_name: str, action: str, tenant_id: str) -> None:
    RISK_CONTROLS_TOTAL.labels(
        control_name=control_name, action=action, tenant_id=tenant_id
    ).inc()


def record_approval_outcome(
    risk_level:      str,
    checkpoint_type: str,
    outcome:         str,    # "approved" | "rejected" | "auto_approved" | "expired"
    tenant_id:       str,
) -> None:
    APPROVAL_REQUESTS_TOTAL.labels(
        risk_level      = risk_level,
        checkpoint_type = checkpoint_type,
        outcome         = outcome,
        tenant_id       = tenant_id,
    ).inc()


def record_auto_accept(tenant_id: str, material_class: str) -> None:
    AUTO_ACCEPT_TOTAL.labels(tenant_id=tenant_id, material_class=material_class).inc()


def record_offer_sent(
    tenant_id:    str,
    strategy:     str,
    round_number: int,
    offer_price:  float,
    initial_ask:  float,
) -> None:
    ratio = offer_price / initial_ask if initial_ask > 0 else 1.0
    OFFER_PRICE_RATIO.labels(
        tenant_id=tenant_id, strategy=strategy, round=str(round_number)
    ).observe(ratio)


def update_behavior_profile_confidence(
    supplier_id: str,
    tenant_id:   str,
    confidence:  float,
) -> None:
    BEHAVIOR_PROFILE_CONFIDENCE.labels(
        supplier_id=supplier_id, tenant_id=tenant_id
    ).set(confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Session timer context manager
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def negotiation_session_timer(
    tenant_id:     str,
    strategy:      str,
    result_holder: list[str],   # mutable list; caller sets result_holder[0] to result
) -> Generator[None, None, None]:
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        result  = result_holder[0] if result_holder else "UNKNOWN"
        NEGOTIATION_DURATION.labels(
            tenant_id=tenant_id, strategy=strategy, result=result
        ).observe(elapsed)


# ─────────────────────────────────────────────────────────────────────────────
# Negotiation KPI Summary (for Grafana / reporting API)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NegotiationKPISummary:
    """Snapshot of negotiation performance for a tenant."""
    tenant_id:              str
    period_days:            int
    total_sessions:         int
    agreement_rate:         float       # sessions that closed as AGREEMENT / total
    avg_discount_pct:       float       # average % below initial ask
    avg_rounds_per_session: float
    total_value_saved_eur:  float
    avg_session_duration_h: float
    top_strategy:           str         # most used strategy in AGREEMENT sessions
    deadlock_rate:          float
    escalation_rate:        float


def build_kpi_summary(rows: list[dict[str, Any]], period_days: int) -> NegotiationKPISummary:
    """Build summary from raw negotiation_outcomes rows."""
    if not rows:
        return NegotiationKPISummary(
            tenant_id="", period_days=period_days,
            total_sessions=0, agreement_rate=0, avg_discount_pct=0,
            avg_rounds_per_session=0, total_value_saved_eur=0,
            avg_session_duration_h=0, top_strategy="", deadlock_rate=0,
            escalation_rate=0,
        )

    total      = len(rows)
    agreements = [r for r in rows if r.get("result") == "agreement"]
    deadlocks  = [r for r in rows if r.get("result") == "deadlock"]
    escalated  = [r for r in rows if r.get("result") == "escalated"]

    discounts  = [r.get("discount_vs_initial_ask", 0) or 0 for r in agreements]
    rounds     = [r.get("rounds_taken", 0)             or 0 for r in rows]
    saved      = [r.get("total_value_saved_eur", 0)    or 0 for r in agreements]
    durations  = [r.get("days_elapsed", 0)             or 0 for r in rows]
    strategies = [r.get("strategy_used", "")               for r in agreements]

    strategy_counts: dict[str, int] = {}
    for s in strategies:
        if s:
            strategy_counts[s] = strategy_counts.get(s, 0) + 1
    top_strategy = max(strategy_counts, key=strategy_counts.get) if strategy_counts else ""

    return NegotiationKPISummary(
        tenant_id              = rows[0].get("tenant_id", ""),
        period_days            = period_days,
        total_sessions         = total,
        agreement_rate         = len(agreements) / total,
        avg_discount_pct       = sum(discounts) / len(discounts) if discounts else 0,
        avg_rounds_per_session = sum(rounds)    / len(rounds)    if rounds    else 0,
        total_value_saved_eur  = sum(saved),
        avg_session_duration_h = sum(durations) / len(durations) * 24 if durations else 0,
        top_strategy           = top_strategy,
        deadlock_rate          = len(deadlocks)  / total,
        escalation_rate        = len(escalated)  / total,
    )
