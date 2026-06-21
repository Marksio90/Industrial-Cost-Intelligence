"""
Unified observability middleware for FastAPI.

Replaces the older src/middleware/observability.py.
Wire in create_app():
    app.add_middleware(ObservabilityMiddleware)
    app.add_route("/metrics", prometheus_metrics_endpoint)
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from .logging import bind_request_context, get_logger
from .metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_IN_FLIGHT,
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_SIZE_BYTES,
    HTTP_RESPONSE_SIZE_BYTES,
)
from .tracing import current_trace_id, record_exception, set_span_attributes

logger = get_logger(__name__)

# Paths that should not be logged or metered (low-value noise)
_SKIP_PATHS = frozenset({"/health", "/health/ready", "/metrics"})


def _resolve_route_path(request: Request) -> str:
    """Return the matched route template (e.g. /api/v1/costs/{id}), not the raw URL."""
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Single middleware that wires together:
      - Request ID propagation
      - Tenant ID extraction
      - structlog context binding (request_id, tenant_id, trace_id)
      - Prometheus HTTP metrics
      - OTel span attributes
      - Response X-Request-ID / X-Trace-ID headers
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Cheap path: health/metrics endpoints need no instrumentation
        if path in _SKIP_PATHS:
            return await call_next(request)

        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        tenant_id  = request.headers.get("X-Tenant-ID", "unknown")
        trace_id   = current_trace_id()

        bind_request_context(
            request_id=request_id,
            tenant_id=tenant_id,
            correlation_id=trace_id,
        )

        route_path   = _resolve_route_path(request)
        method       = request.method
        content_len  = int(request.headers.get("content-length", 0))

        if content_len:
            HTTP_REQUEST_SIZE_BYTES.labels(method=method, path=route_path).observe(content_len)

        HTTP_REQUESTS_IN_FLIGHT.labels(method=method, path=route_path).inc()
        set_span_attributes(**{
            "http.method":      method,
            "http.route":       route_path,
            "http.tenant_id":   tenant_id,
            "http.request_id":  request_id,
        })

        t0 = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            record_exception(exc)
            logger.exception("unhandled_request_error", path=path, exc_info=exc)
            raise
        finally:
            HTTP_REQUESTS_IN_FLIGHT.labels(method=method, path=route_path).dec()

        duration = time.perf_counter() - t0
        status   = str(response.status_code)

        HTTP_REQUEST_DURATION.labels(
            method=method, path=route_path, status_code=status, tenant_id=tenant_id
        ).observe(duration)
        HTTP_REQUESTS_TOTAL.labels(
            method=method, path=route_path, status_code=status, tenant_id=tenant_id
        ).inc()

        resp_len = int(response.headers.get("content-length", 0))
        if resp_len:
            HTTP_RESPONSE_SIZE_BYTES.labels(path=route_path, status_code=status).observe(resp_len)

        set_span_attributes(**{
            "http.status_code":    response.status_code,
            "http.response_size":  resp_len,
            "http.duration_ms":    round(duration * 1000, 2),
        })

        log_fn = logger.warning if response.status_code >= 400 else logger.info
        log_fn(
            "http_request",
            method=method,
            path=path,
            route=route_path,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
            tenant_id=tenant_id,
        )

        response.headers["X-Request-ID"] = request_id
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id

        return response


async def prometheus_metrics_endpoint(request: Request) -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
