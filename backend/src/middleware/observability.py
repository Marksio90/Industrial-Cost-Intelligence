from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

logger = structlog.get_logger(__name__)

# ── Prometheus Metrics ────────────────────────────────────────────────────────

http_requests_total = Counter(
    "ici_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "ici_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

http_requests_in_progress = Counter(
    "ici_http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method", "path"],
)


def _get_route_path(request: Request) -> str:
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        tenant_id  = request.headers.get("X-Tenant-ID", "unknown")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            tenant_id=tenant_id,
        )

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        route_path = _get_route_path(request)

        # Prometheus
        http_requests_total.labels(
            method=request.method,
            path=route_path,
            status_code=str(response.status_code),
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method,
            path=route_path,
        ).observe(duration)

        logger.info(
            "request",
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )

        response.headers["X-Request-ID"] = request_id
        return response


async def metrics_endpoint(request: Request) -> Response:
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
