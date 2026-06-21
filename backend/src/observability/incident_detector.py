"""
ICI Incident Detector — real-time anomaly detection running inside the process.

Implements three independent detection strategies:

  1. StatisticalDetector
     Uses a 3-sigma (z-score) rule on a rolling window of metric samples.
     Suitable for latency, error rates, prediction MAPE.

  2. RateOfChangeDetector
     Fires when a metric's gradient (Δ per second) exceeds a threshold.
     Catches sudden spikes before they persist long enough for 3-sigma.

  3. ThresholdDetector
     Simple absolute ceiling / floor with debounce (must be breached for
     `min_duration_s` consecutive seconds before firing).

Incidents are emitted to:
  - structlog (JSON event with severity, detector, metric, value)
  - Prometheus counter (ici_incidents_total)
  - Redis pub/sub channel "ici:incidents" (for real-time dashboard push)
  - Optional async callback list (register via add_handler())

The IncidentDetector is instantiated once and started as a background task
in the FastAPI lifespan.

Usage:
    detector = IncidentDetector(redis_url="redis://:pw@localhost:6379/0")
    detector.register_metric(
        "ici_http_request_duration_seconds_p99",
        ThresholdRule(ceiling=2.0, min_duration_s=60, severity="warning"),
    )
    asyncio.create_task(detector.run())
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Protocol

from prometheus_client import Counter

from .logging import get_logger
from .metrics import (
    ML_PREDICTION_MAPE_EWMA,
    RFQ_SUCCESS_RATE,
    HTTP_REQUEST_DURATION,
)

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Severity & Incident model
# ─────────────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Incident:
    metric:      str
    detector:    str
    severity:    Severity
    value:       float
    threshold:   float
    message:     str
    ts:          float = field(default_factory=time.time)
    labels:      dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric":    self.metric,
            "detector":  self.detector,
            "severity":  self.severity.value,
            "value":     round(self.value, 6),
            "threshold": round(self.threshold, 6),
            "message":   self.message,
            "ts":        self.ts,
            "labels":    self.labels,
        }


# Prometheus counter for incident volume
INCIDENTS_TOTAL = Counter(
    "ici_incidents_total",
    "Total incidents fired by IncidentDetector",
    ["metric", "detector", "severity"],
)


IncidentHandler = Callable[[Incident], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────────────
# Detection rules
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ThresholdRule:
    ceiling:         float | None = None
    floor:           float | None = None
    min_duration_s:  float        = 30.0
    severity:        Severity     = Severity.WARNING
    cooldown_s:      float        = 300.0


@dataclass
class StatisticalRule:
    window:      int    = 120        # samples in rolling window
    z_threshold: float  = 3.0       # standard deviations
    min_samples: int    = 20        # don't fire until window is primed
    severity:    Severity = Severity.WARNING
    cooldown_s:  float  = 300.0


@dataclass
class RateOfChangeRule:
    max_delta_per_s: float           # max allowed change per second
    window_s:        float  = 60.0  # look-back for derivative
    severity:        Severity = Severity.WARNING
    cooldown_s:      float  = 300.0


# ─────────────────────────────────────────────────────────────────────────────
# Internal detector per metric
# ─────────────────────────────────────────────────────────────────────────────

class _MetricState:
    def __init__(self) -> None:
        self.samples: deque[tuple[float, float]] = deque(maxlen=500)  # (ts, value)
        self.breach_since: float | None = None
        self.last_fired: float = 0.0

    def push(self, value: float) -> None:
        self.samples.append((time.time(), value))

    @property
    def latest(self) -> float | None:
        return self.samples[-1][1] if self.samples else None

    def rolling_stats(self, n: int) -> tuple[float, float]:
        vals = [v for _, v in list(self.samples)[-n:]]
        if len(vals) < 2:
            return (vals[0] if vals else 0.0), 0.0
        mean = sum(vals) / len(vals)
        variance = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
        return mean, math.sqrt(variance)

    def derivative(self, window_s: float) -> float | None:
        now   = time.time()
        cutoff = now - window_s
        old = [(ts, v) for ts, v in self.samples if ts >= cutoff]
        if len(old) < 2:
            return None
        ts0, v0 = old[0]
        ts1, v1 = old[-1]
        dt = ts1 - ts0
        return (v1 - v0) / dt if dt > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Metric reader protocol (pluggable — default uses prometheus_client internals)
# ─────────────────────────────────────────────────────────────────────────────

class MetricReader(Protocol):
    async def read(self, metric_name: str, labels: dict[str, str]) -> float | None: ...


class PrometheusMetricReader:
    """Reads the current value of a Prometheus metric from the in-process registry."""

    async def read(self, metric_name: str, labels: dict[str, str]) -> float | None:
        from prometheus_client import REGISTRY
        try:
            for metric in REGISTRY.collect():
                if metric.name == metric_name:
                    for sample in metric.samples:
                        if all(sample.labels.get(k) == v for k, v in labels.items()):
                            return sample.value
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# IncidentDetector
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _WatchedMetric:
    metric_name: str
    labels:      dict[str, str]
    rules:       list[ThresholdRule | StatisticalRule | RateOfChangeRule]
    state:       _MetricState = field(default_factory=_MetricState)


class IncidentDetector:
    """
    Background coroutine that polls metrics and fires incidents.

    detector = IncidentDetector(redis_url=settings.redis_url)
    detector.register(
        "ici_http_request_duration_seconds",
        labels={"quantile": "0.99"},
        rules=[ThresholdRule(ceiling=2.0, min_duration_s=60)],
    )
    asyncio.create_task(detector.run())
    """

    def __init__(
        self,
        redis_url: str | None = None,
        poll_interval_s: float = 15.0,
        reader: MetricReader | None = None,
    ) -> None:
        self._redis_url       = redis_url
        self._poll_interval   = poll_interval_s
        self._watched:        list[_WatchedMetric] = []
        self._handlers:       list[IncidentHandler] = []
        self._reader          = reader or PrometheusMetricReader()
        self._redis: Any      = None
        self._running         = False

    # ── Registration ─────────────────────────────────────────────────────────

    def register(
        self,
        metric_name: str,
        rules: list[ThresholdRule | StatisticalRule | RateOfChangeRule],
        labels: dict[str, str] | None = None,
    ) -> None:
        self._watched.append(_WatchedMetric(
            metric_name=metric_name,
            labels=labels or {},
            rules=rules,
        ))

    def add_handler(self, handler: IncidentHandler) -> None:
        self._handlers.append(handler)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._running = True
        if self._redis_url:
            await self._connect_redis()

        logger.info("incident_detector_started", metrics=len(self._watched))
        while self._running:
            await asyncio.sleep(self._poll_interval)
            await self._poll_all()

    async def stop(self) -> None:
        self._running = False
        if self._redis:
            await self._redis.aclose()

    async def _connect_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
        except Exception as exc:
            logger.warning("incident_detector_redis_unavailable", error=str(exc))
            self._redis = None

    async def _poll_all(self) -> None:
        for watched in self._watched:
            value = await self._reader.read(watched.metric_name, watched.labels)
            if value is None:
                continue
            watched.state.push(value)
            for rule in watched.rules:
                incident = self._evaluate(watched, rule, value)
                if incident:
                    await self._emit(incident)

    # ── Rule evaluation ───────────────────────────────────────────────────────

    def _evaluate(
        self,
        w: _WatchedMetric,
        rule: ThresholdRule | StatisticalRule | RateOfChangeRule,
        value: float,
    ) -> Incident | None:
        now = time.time()

        if isinstance(rule, ThresholdRule):
            return self._eval_threshold(w, rule, value, now)
        elif isinstance(rule, StatisticalRule):
            return self._eval_statistical(w, rule, value, now)
        elif isinstance(rule, RateOfChangeRule):
            return self._eval_rate_of_change(w, rule, value, now)
        return None

    def _eval_threshold(
        self, w: _WatchedMetric, rule: ThresholdRule, value: float, now: float
    ) -> Incident | None:
        breached = (
            (rule.ceiling is not None and value > rule.ceiling) or
            (rule.floor   is not None and value < rule.floor)
        )

        if breached:
            if w.state.breach_since is None:
                w.state.breach_since = now
            elif (now - w.state.breach_since >= rule.min_duration_s
                  and now - w.state.last_fired >= rule.cooldown_s):
                w.state.last_fired = now
                threshold = rule.ceiling if rule.ceiling is not None else (rule.floor or 0.0)
                return Incident(
                    metric=w.metric_name,
                    detector="threshold",
                    severity=rule.severity,
                    value=value,
                    threshold=threshold,
                    message=(
                        f"{w.metric_name} has been {'above' if rule.ceiling else 'below'} "
                        f"{threshold:.4f} for {rule.min_duration_s:.0f}s (current={value:.4f})"
                    ),
                    labels=w.labels,
                )
        else:
            w.state.breach_since = None
        return None

    def _eval_statistical(
        self, w: _WatchedMetric, rule: StatisticalRule, value: float, now: float
    ) -> Incident | None:
        if len(w.state.samples) < rule.min_samples:
            return None
        mean, std = w.state.rolling_stats(rule.window)
        if std == 0:
            return None
        z = abs(value - mean) / std
        if z >= rule.z_threshold and now - w.state.last_fired >= rule.cooldown_s:
            w.state.last_fired = now
            return Incident(
                metric=w.metric_name,
                detector="statistical",
                severity=rule.severity,
                value=value,
                threshold=mean + rule.z_threshold * std,
                message=(
                    f"{w.metric_name} z-score={z:.2f} (>{rule.z_threshold}σ); "
                    f"value={value:.4f}, mean={mean:.4f}, std={std:.4f}"
                ),
                labels=w.labels,
            )
        return None

    def _eval_rate_of_change(
        self, w: _WatchedMetric, rule: RateOfChangeRule, value: float, now: float
    ) -> Incident | None:
        deriv = w.state.derivative(rule.window_s)
        if deriv is None:
            return None
        if abs(deriv) > rule.max_delta_per_s and now - w.state.last_fired >= rule.cooldown_s:
            w.state.last_fired = now
            return Incident(
                metric=w.metric_name,
                detector="rate_of_change",
                severity=rule.severity,
                value=value,
                threshold=rule.max_delta_per_s,
                message=(
                    f"{w.metric_name} changing at {deriv:.4f}/s "
                    f"(limit={rule.max_delta_per_s}/s)"
                ),
                labels=w.labels,
            )
        return None

    # ── Emission ──────────────────────────────────────────────────────────────

    async def _emit(self, incident: Incident) -> None:
        INCIDENTS_TOTAL.labels(
            metric=incident.metric,
            detector=incident.detector,
            severity=incident.severity.value,
        ).inc()

        log_fn = logger.critical if incident.severity == Severity.CRITICAL else logger.warning
        log_fn(
            "incident_detected",
            **incident.to_dict(),
        )

        if self._redis:
            try:
                await self._redis.publish(
                    "ici:incidents",
                    json.dumps(incident.to_dict()),
                )
            except Exception as exc:
                logger.debug("incident_redis_publish_failed", error=str(exc))

        for handler in self._handlers:
            try:
                await handler(incident)
            except Exception as exc:
                logger.warning("incident_handler_error", error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Default ruleset — wired in lifespan
# ─────────────────────────────────────────────────────────────────────────────

def build_default_detector(redis_url: str | None = None) -> IncidentDetector:
    """
    Factory that returns a pre-configured IncidentDetector with the ICI
    default ruleset. Call in lifespan and store on app.state.
    """
    d = IncidentDetector(redis_url=redis_url, poll_interval_s=15.0)

    # ── HTTP P99 latency ──────────────────────────────────────────────────────
    d.register(
        "ici_http_request_duration_seconds",
        labels={"quantile": "0.99"},
        rules=[
            ThresholdRule(ceiling=2.0, min_duration_s=60, severity=Severity.WARNING),
            ThresholdRule(ceiling=5.0, min_duration_s=30, severity=Severity.CRITICAL),
            RateOfChangeRule(max_delta_per_s=0.5, window_s=60, severity=Severity.WARNING),
        ],
    )

    # ── HTTP error rate (5xx) ─────────────────────────────────────────────────
    d.register(
        "ici_http_requests_total",
        labels={"status_code": "500"},
        rules=[
            StatisticalRule(window=60, z_threshold=3.0, severity=Severity.WARNING),
            ThresholdRule(ceiling=50.0, min_duration_s=30, severity=Severity.CRITICAL),
        ],
    )

    # ── ML prediction MAPE EWMA ───────────────────────────────────────────────
    d.register(
        "ici_ml_mape_ewma",
        labels={},
        rules=[
            ThresholdRule(ceiling=0.20, min_duration_s=120, severity=Severity.WARNING,
                          cooldown_s=600),
            ThresholdRule(ceiling=0.35, min_duration_s=60,  severity=Severity.CRITICAL,
                          cooldown_s=300),
            RateOfChangeRule(max_delta_per_s=0.002, window_s=300, severity=Severity.WARNING),
        ],
    )

    # ── ML drift PSI ──────────────────────────────────────────────────────────
    d.register(
        "ici_ml_drift_psi",
        labels={},
        rules=[
            ThresholdRule(ceiling=0.1, min_duration_s=0, severity=Severity.WARNING),
            ThresholdRule(ceiling=0.2, min_duration_s=0, severity=Severity.CRITICAL),
        ],
    )

    # ── RFQ success rate ──────────────────────────────────────────────────────
    d.register(
        "ici_rfq_success_rate",
        labels={},
        rules=[
            ThresholdRule(floor=0.70, min_duration_s=300, severity=Severity.WARNING,
                          cooldown_s=600),
            ThresholdRule(floor=0.50, min_duration_s=120, severity=Severity.CRITICAL,
                          cooldown_s=300),
        ],
    )

    # ── Worker queue backlog ──────────────────────────────────────────────────
    d.register(
        "ici_worker_queue_depth",
        labels={"queue": "default"},
        rules=[
            ThresholdRule(ceiling=100, min_duration_s=120, severity=Severity.WARNING),
            ThresholdRule(ceiling=500, min_duration_s=60,  severity=Severity.CRITICAL),
        ],
    )

    # ── DB query latency ─────────────────────────────────────────────────────
    d.register(
        "ici_db_query_duration_seconds",
        labels={"quantile": "0.95"},
        rules=[
            ThresholdRule(ceiling=0.5, min_duration_s=60, severity=Severity.WARNING),
            ThresholdRule(ceiling=2.0, min_duration_s=30, severity=Severity.CRITICAL),
        ],
    )

    return d
