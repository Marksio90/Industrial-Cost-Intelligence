"""
ICI Prometheus metrics registry.

All metrics live here so they are registered exactly once and importable
from any module without circular-import risk.

Usage:
    from src.observability.metrics import record_cost_prediction
    record_cost_prediction(mape=0.12, model_version="v3", material="steel")
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    REGISTRY,
    CollectorRegistry,
)

# ─────────────────────────────────────────────────────────────────────────────
# HTTP  (extends existing middleware metrics for richer labelling)
# ─────────────────────────────────────────────────────────────────────────────

HTTP_REQUEST_DURATION = Histogram(
    "ici_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path", "status_code", "tenant_id"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

HTTP_REQUESTS_TOTAL = Counter(
    "ici_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code", "tenant_id"],
)

HTTP_REQUEST_SIZE_BYTES = Histogram(
    "ici_http_request_size_bytes",
    "HTTP request body size",
    ["method", "path"],
    buckets=[64, 256, 1024, 4096, 16384, 65536, 262144, 1048576],
)

HTTP_RESPONSE_SIZE_BYTES = Histogram(
    "ici_http_response_size_bytes",
    "HTTP response body size",
    ["path", "status_code"],
    buckets=[64, 256, 1024, 4096, 16384, 65536, 262144, 1048576],
)

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "ici_http_requests_in_flight",
    "Number of HTTP requests currently being processed",
    ["method", "path"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Cost Prediction ML
# ─────────────────────────────────────────────────────────────────────────────

ML_PREDICTION_DURATION = Histogram(
    "ici_ml_prediction_duration_seconds",
    "Cost prediction inference latency",
    ["model_version", "material_category"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

ML_PREDICTION_MAPE = Histogram(
    "ici_ml_prediction_mape",
    "Mean Absolute Percentage Error per prediction batch",
    ["model_version", "material_category"],
    buckets=[0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0],
)

ML_PREDICTION_MAPE_EWMA = Gauge(
    "ici_ml_mape_ewma",
    "Exponentially weighted moving average of prediction MAPE",
    ["model_version"],
)

ML_PREDICTION_REQUESTS_TOTAL = Counter(
    "ici_ml_prediction_requests_total",
    "Total cost prediction requests",
    ["model_version", "material_category", "status"],
)

ML_PREDICTION_VALUE_EUR = Histogram(
    "ici_ml_prediction_value_eur",
    "Distribution of predicted cost values in EUR",
    ["material_category"],
    buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 50000],
)

ML_DRIFT_SCORE = Gauge(
    "ici_ml_drift_psi",
    "Population Stability Index per feature (0=none, >0.2=high)",
    ["feature", "model_version"],
)

ML_DRIFT_ALERTS_TOTAL = Counter(
    "ici_ml_drift_alerts_total",
    "Total drift alerts fired",
    ["severity", "feature"],
)

ML_MODEL_VERSION = Gauge(
    "ici_ml_model_version_info",
    "Active model version (label only, value always 1)",
    ["model_name", "version", "stage"],
)

ML_CACHE_HIT_TOTAL = Counter(
    "ici_ml_cache_hits_total",
    "ML prediction cache hits",
    ["model_version"],
)

ML_CACHE_MISS_TOTAL = Counter(
    "ici_ml_cache_misses_total",
    "ML prediction cache misses",
    ["model_version"],
)

# ─────────────────────────────────────────────────────────────────────────────
# RFQ Agent
# ─────────────────────────────────────────────────────────────────────────────

RFQ_SESSIONS_TOTAL = Counter(
    "ici_rfq_sessions_total",
    "Total RFQ agent sessions started",
    ["tenant_id", "status"],  # status: success | failed | timeout
)

RFQ_SESSION_DURATION = Histogram(
    "ici_rfq_session_duration_seconds",
    "End-to-end duration of one RFQ agent run",
    ["status"],
    buckets=[30, 60, 120, 300, 600, 900, 1800, 3600],
)

RFQ_EMAILS_SENT_TOTAL = Counter(
    "ici_rfq_emails_sent_total",
    "Total RFQ emails dispatched",
    ["tenant_id", "backend"],  # backend: smtp | sendgrid
)

RFQ_EMAILS_FAILED_TOTAL = Counter(
    "ici_rfq_emails_failed_total",
    "RFQ emails that failed to send",
    ["tenant_id", "reason"],  # reason: rate_limit | compliance | smtp_error
)

RFQ_RESPONSES_PARSED_TOTAL = Counter(
    "ici_rfq_responses_parsed_total",
    "Supplier responses successfully parsed",
    ["parser"],  # parser: regex | llm
)

RFQ_PARSE_FAILURES_TOTAL = Counter(
    "ici_rfq_parse_failures_total",
    "Supplier responses that failed to parse",
    ["tenant_id"],
)

RFQ_SUCCESS_RATE = Gauge(
    "ici_rfq_success_rate",
    "Rolling 1-hour RFQ success rate (sessions_ok / sessions_total)",
    ["tenant_id"],
)

RFQ_SUPPLIERS_DISCOVERED = Histogram(
    "ici_rfq_suppliers_discovered",
    "Number of suppliers found per scraping run",
    ["tenant_id"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100],
)

RFQ_QUOTE_SCORE = Histogram(
    "ici_rfq_quote_score",
    "Composite quote score (0–1) after normalisation",
    ["tenant_id"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

RFQ_RATE_LIMIT_BLOCKS_TOTAL = Counter(
    "ici_rfq_rate_limit_blocks_total",
    "RFQ emails blocked by rate limiter",
    ["limiter"],  # limiter: global | domain | interval
)

RFQ_LLM_ITERATIONS = Histogram(
    "ici_rfq_llm_iterations",
    "Number of ReAct loop iterations per session",
    ["status"],
    buckets=[1, 2, 3, 5, 8, 10, 15, 20],
)

RFQ_LLM_TOKENS_USED = Counter(
    "ici_rfq_llm_tokens_total",
    "Total LLM tokens consumed by RFQ agent",
    ["type"],  # type: input | output
)

# ─────────────────────────────────────────────────────────────────────────────
# Background Workers
# ─────────────────────────────────────────────────────────────────────────────

WORKER_JOBS_TOTAL = Counter(
    "ici_worker_jobs_total",
    "Total background jobs processed",
    ["queue", "job_type", "status"],
)

WORKER_JOB_DURATION = Histogram(
    "ici_worker_job_duration_seconds",
    "Background job execution duration",
    ["queue", "job_type"],
    buckets=[0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0],
)

WORKER_QUEUE_DEPTH = Gauge(
    "ici_worker_queue_depth",
    "Current number of jobs waiting in queue",
    ["queue"],
)

WORKER_JOB_RETRIES_TOTAL = Counter(
    "ici_worker_job_retries_total",
    "Total job retry attempts",
    ["queue", "job_type"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

DB_QUERY_DURATION = Histogram(
    "ici_db_query_duration_seconds",
    "Database query execution time",
    ["operation", "table"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0],
)

DB_CONNECTIONS_ACTIVE = Gauge(
    "ici_db_connections_active",
    "Active database connections in pool",
)

DB_POOL_OVERFLOW = Counter(
    "ici_db_pool_overflow_total",
    "Times the connection pool limit was exceeded",
)

DB_ERRORS_TOTAL = Counter(
    "ici_db_errors_total",
    "Total database errors",
    ["operation", "error_type"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Cache (Redis)
# ─────────────────────────────────────────────────────────────────────────────

CACHE_OPERATIONS_TOTAL = Counter(
    "ici_cache_operations_total",
    "Redis cache operations",
    ["operation", "result"],  # result: hit | miss | error
)

CACHE_OPERATION_DURATION = Histogram(
    "ici_cache_operation_duration_seconds",
    "Redis operation latency",
    ["operation"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1],
)

# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_QUERY_DURATION = Histogram(
    "ici_search_query_duration_seconds",
    "Semantic search query latency",
    ["index", "mode"],  # mode: vector | keyword | hybrid
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

SEARCH_RESULTS_COUNT = Histogram(
    "ici_search_results_count",
    "Number of results returned per search",
    ["index"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100],
)

SEARCH_REQUESTS_TOTAL = Counter(
    "ici_search_requests_total",
    "Total search requests",
    ["index", "mode", "status"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Business KPIs
# ─────────────────────────────────────────────────────────────────────────────

BUSINESS_COST_SAVINGS_EUR = Counter(
    "ici_business_cost_savings_eur_total",
    "Cumulative cost savings identified via RFQ/ML optimisation",
    ["tenant_id"],
)

BUSINESS_QUOTES_ACCEPTED_TOTAL = Counter(
    "ici_business_quotes_accepted_total",
    "Quotes accepted by procurement team",
    ["tenant_id", "material_category"],
)

BUSINESS_ACTIVE_TENANTS = Gauge(
    "ici_business_active_tenants",
    "Number of tenants with activity in the last 24h",
)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience recording helpers
# ─────────────────────────────────────────────────────────────────────────────

class ICIMetrics:
    """Namespace shim — call class methods or use module-level helpers below."""

    # HTTP
    request_duration  = HTTP_REQUEST_DURATION
    requests_total    = HTTP_REQUESTS_TOTAL
    requests_in_flight = HTTP_REQUESTS_IN_FLIGHT

    # ML
    prediction_duration = ML_PREDICTION_DURATION
    prediction_mape     = ML_PREDICTION_MAPE
    prediction_mape_ewma = ML_PREDICTION_MAPE_EWMA
    drift_psi           = ML_DRIFT_SCORE

    # RFQ
    rfq_sessions      = RFQ_SESSIONS_TOTAL
    rfq_success_rate  = RFQ_SUCCESS_RATE
    rfq_emails_sent   = RFQ_EMAILS_SENT_TOTAL

    # Worker
    worker_jobs       = WORKER_JOBS_TOTAL
    worker_queue_depth = WORKER_QUEUE_DEPTH


def record_http_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_s: float,
    tenant_id: str = "unknown",
    request_bytes: int = 0,
    response_bytes: int = 0,
) -> None:
    labels = dict(method=method, path=path, status_code=str(status_code), tenant_id=tenant_id)
    HTTP_REQUEST_DURATION.labels(**labels).observe(duration_s)
    HTTP_REQUESTS_TOTAL.labels(**labels).inc()
    if request_bytes:
        HTTP_REQUEST_SIZE_BYTES.labels(method=method, path=path).observe(request_bytes)
    if response_bytes:
        HTTP_RESPONSE_SIZE_BYTES.labels(path=path, status_code=str(status_code)).observe(response_bytes)


def record_cost_prediction(
    *,
    duration_s: float,
    mape: float | None = None,
    predicted_eur: float | None = None,
    model_version: str = "unknown",
    material_category: str = "unknown",
    status: str = "success",
) -> None:
    ML_PREDICTION_DURATION.labels(
        model_version=model_version, material_category=material_category
    ).observe(duration_s)
    ML_PREDICTION_REQUESTS_TOTAL.labels(
        model_version=model_version, material_category=material_category, status=status
    ).inc()
    if mape is not None:
        ML_PREDICTION_MAPE.labels(
            model_version=model_version, material_category=material_category
        ).observe(mape)
    if predicted_eur is not None:
        ML_PREDICTION_VALUE_EUR.labels(material_category=material_category).observe(predicted_eur)


def record_rfq_event(
    *,
    event: str,
    tenant_id: str = "default",
    **kwargs: str | float | int,
) -> None:
    """
    event: 'session_start' | 'session_end' | 'email_sent' | 'email_failed' |
           'response_parsed' | 'parse_failed' | 'rate_limited'
    """
    match event:
        case "session_start":
            pass
        case "session_end":
            status  = str(kwargs.get("status", "success"))
            dur     = float(kwargs.get("duration_s", 0))
            RFQ_SESSIONS_TOTAL.labels(tenant_id=tenant_id, status=status).inc()
            RFQ_SESSION_DURATION.labels(status=status).observe(dur)
            iters = kwargs.get("iterations")
            if iters is not None:
                RFQ_LLM_ITERATIONS.labels(status=status).observe(float(iters))
        case "email_sent":
            RFQ_EMAILS_SENT_TOTAL.labels(
                tenant_id=tenant_id,
                backend=str(kwargs.get("backend", "smtp")),
            ).inc()
        case "email_failed":
            RFQ_EMAILS_FAILED_TOTAL.labels(
                tenant_id=tenant_id,
                reason=str(kwargs.get("reason", "unknown")),
            ).inc()
        case "response_parsed":
            RFQ_RESPONSES_PARSED_TOTAL.labels(
                parser=str(kwargs.get("parser", "regex"))
            ).inc()
        case "parse_failed":
            RFQ_PARSE_FAILURES_TOTAL.labels(tenant_id=tenant_id).inc()
        case "rate_limited":
            RFQ_RATE_LIMIT_BLOCKS_TOTAL.labels(
                limiter=str(kwargs.get("limiter", "global"))
            ).inc()
        case "tokens_used":
            for tok_type in ("input", "output"):
                val = kwargs.get(f"{tok_type}_tokens")
                if val is not None:
                    RFQ_LLM_TOKENS_USED.labels(type=tok_type).inc(float(val))


def record_worker_job(
    *,
    queue: str,
    job_type: str,
    status: str,
    duration_s: float,
    retried: bool = False,
) -> None:
    WORKER_JOBS_TOTAL.labels(queue=queue, job_type=job_type, status=status).inc()
    WORKER_JOB_DURATION.labels(queue=queue, job_type=job_type).observe(duration_s)
    if retried:
        WORKER_JOB_RETRIES_TOTAL.labels(queue=queue, job_type=job_type).inc()


@contextmanager
def record_db_query(
    *, operation: str, table: str
) -> Generator[None, None, None]:
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        DB_ERRORS_TOTAL.labels(
            operation=operation, error_type=type(exc).__name__
        ).inc()
        raise
    finally:
        DB_QUERY_DURATION.labels(operation=operation, table=table).observe(
            time.perf_counter() - start
        )


def record_cache_operation(
    *, operation: str, result: str, duration_s: float
) -> None:
    CACHE_OPERATIONS_TOTAL.labels(operation=operation, result=result).inc()
    CACHE_OPERATION_DURATION.labels(operation=operation).observe(duration_s)


def record_search_query(
    *,
    index: str,
    mode: str,
    duration_s: float,
    result_count: int,
    status: str = "success",
) -> None:
    SEARCH_QUERY_DURATION.labels(index=index, mode=mode).observe(duration_s)
    SEARCH_RESULTS_COUNT.labels(index=index).observe(result_count)
    SEARCH_REQUESTS_TOTAL.labels(index=index, mode=mode, status=status).inc()
