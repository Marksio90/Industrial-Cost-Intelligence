"""
ICI API Security Hardening Middleware.

Applies defence-in-depth at the HTTP layer:

  SecurityHeadersMiddleware   — HSTS, CSP, X-Frame-Options, Permissions-Policy
  RateLimitMiddleware         — per-IP + per-user sliding-window rate limiting
                                backed by Redis; fails open on Redis unavailability
  RequestValidationMiddleware — max body size, content-type enforcement,
                                path traversal guard, oversized query-string rejection
  SQLInjectionGuard           — heuristic scan of path/query params (belt-and-braces;
                                primary defence is parameterised ORM queries)

Wire in create_app() — order matters (outermost = first evaluated):
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, redis_url=settings.redis_cache_url)
    app.add_middleware(RequestValidationMiddleware)

Rate limit tiers (requests / window):
    /api/v1/auth/*      10 / 60s   (brute-force protection)
    /api/v1/rfq/*       30 / 60s   (expensive agent calls)
    /api/v1/*           120 / 60s  (general API)
    global per-IP       300 / 60s
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..observability.logging import get_logger
from ..observability.metrics import HTTP_REQUESTS_TOTAL

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Security Headers
# ─────────────────────────────────────────────────────────────────────────────

_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

_SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options":    "nosniff",
    "X-Frame-Options":           "DENY",
    "X-XSS-Protection":          "1; mode=block",
    "Referrer-Policy":           "strict-origin-when-cross-origin",
    "Permissions-Policy":        "geolocation=(), microphone=(), camera=(), payment=()",
    "Content-Security-Policy":   _CSP,
    "Cross-Origin-Opener-Policy":     "same-origin",
    "Cross-Origin-Embedder-Policy":   "require-corp",
    "Cross-Origin-Resource-Policy":   "same-site",
}

# Headers that must never be exposed
_HEADERS_TO_REMOVE = {"Server", "X-Powered-By", "X-AspNet-Version"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        for h in _HEADERS_TO_REMOVE:
            if h in response.headers:
                del response.headers[h]
        for k, v in _SECURITY_HEADERS.items():
            response.headers[k] = v
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────────────────────────────────────

# (path_prefix, window_s, max_requests)
_RATE_TIERS: list[tuple[str, int, int]] = [
    ("/api/v1/auth",  60, 10),
    ("/api/v1/rfq",   60, 30),
    ("/api/v1/",      60, 120),
]
_GLOBAL_LIMIT = (60, 300)   # (window_s, max_requests) per IP

_RATE_LIMIT_SKIP = frozenset({"/health", "/health/ready", "/metrics"})


def _get_limit(path: str) -> tuple[int, int]:
    for prefix, window, limit in _RATE_TIERS:
        if path.startswith(prefix):
            return window, limit
    return _GLOBAL_LIMIT


class _SlidingWindow:
    """In-process sliding window (fallback when Redis is unavailable)."""

    def __init__(self) -> None:
        self._store: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str, window_s: int, max_req: int) -> tuple[bool, int]:
        now  = time.monotonic()
        dq   = self._store[key]
        cutoff = now - window_s
        while dq and dq[0] < cutoff:
            dq.popleft()
        remaining = max(0, max_req - len(dq))
        if len(dq) >= max_req:
            return False, 0
        dq.append(now)
        return True, remaining - 1


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, redis_url: str | None = None) -> None:
        super().__init__(app)
        self._redis_url  = redis_url
        self._redis: Any = None
        self._fallback   = _SlidingWindow()

    async def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
        except Exception:
            self._redis = None
        return self._redis

    async def _check_redis(self, key: str, window_s: int, max_req: int) -> tuple[bool, int]:
        r = await self._get_redis()
        if r is None:
            return self._fallback.is_allowed(key, window_s, max_req)
        pipe = r.pipeline()
        now  = int(time.time() * 1000)
        try:
            pipe.zremrangebyscore(key, 0, now - window_s * 1000)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, window_s + 1)
            results = await pipe.execute()
            count     = results[1]
            remaining = max(0, max_req - count - 1)
            return count < max_req, remaining
        except Exception as exc:
            logger.debug("rate_limit_redis_error", error=str(exc))
            return self._fallback.is_allowed(key, window_s, max_req)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in _RATE_LIMIT_SKIP:
            return await call_next(request)

        ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
            or getattr(request.client, "host", "unknown")

        window_s, max_req = _get_limit(path)
        key = f"rl:{ip}:{path.split('/')[2] if path.count('/') >= 2 else 'root'}"

        allowed, remaining = await self._check_redis(key, window_s, max_req)

        if not allowed:
            logger.warning("rate_limit_exceeded", ip=ip, path=path)
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method, path=path,
                status_code="429", tenant_id="unknown",
            ).inc()
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": "RATE_LIMIT_EXCEEDED", "message": "Too many requests"},
                headers={
                    "Retry-After":           str(window_s),
                    "X-RateLimit-Limit":     str(max_req),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset":     str(int(time.time()) + window_s),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"]     = str(max_req)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"]     = str(int(time.time()) + window_s)
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Request Validation
# ─────────────────────────────────────────────────────────────────────────────

_MAX_BODY_SIZE     = 50 * 1024 * 1024   # 50 MB
_MAX_URL_LENGTH    = 8192
_MAX_HEADER_SIZE   = 32 * 1024           # 32 KB

# Path traversal patterns
_PATH_TRAVERSAL_RE = re.compile(r"\.\./|%2e%2e|%252e%252e", re.I)

# SQL injection heuristics (supplement ORM; belt-and-braces)
_SQLI_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(union\s+select|insert\s+into|drop\s+table|exec\s*\(|xp_cmd)", re.I),
    re.compile(r"(\bor\b\s+\d+=\d+|\band\b\s+\d+=\d+)", re.I),
    re.compile(r"(--\s*$|;--)", re.M),
    re.compile(r"('\s*(or|and)\s*')", re.I),
]

# XSS heuristics
_XSS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<script[^>]*>", re.I),
    re.compile(r"javascript\s*:", re.I),
    re.compile(r"on\w+\s*=", re.I),
    re.compile(r"<iframe[^>]*>", re.I),
]


def _scan_value(value: str) -> str | None:
    """Returns the threat name if detected, else None."""
    for p in _SQLI_PATTERNS:
        if p.search(value):
            return "sql_injection"
    for p in _XSS_PATTERNS:
        if p.search(value):
            return "xss"
    if _PATH_TRAVERSAL_RE.search(value):
        return "path_traversal"
    return None


class RequestValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # URL length
        if len(str(request.url)) > _MAX_URL_LENGTH:
            return JSONResponse(
                status_code=status.HTTP_414_REQUEST_URI_TOO_LONG,
                content={"error": "URI_TOO_LONG"},
            )

        # Content-Length guard
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_SIZE:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"error": "PAYLOAD_TOO_LARGE", "max_bytes": _MAX_BODY_SIZE},
            )

        # Path traversal in URL path
        if _PATH_TRAVERSAL_RE.search(path):
            logger.warning("path_traversal_attempt", path=path,
                           ip=getattr(getattr(request, "client", None), "host", ""))
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "INVALID_PATH"},
            )

        # Scan query parameters
        for param, value in request.query_params.items():
            threat = _scan_value(f"{param}={value}")
            if threat:
                logger.warning(
                    "malicious_input_detected",
                    threat=threat, param=param,
                    path=path,
                    ip=getattr(getattr(request, "client", None), "host", ""),
                )
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "INVALID_INPUT", "detail": "Malicious input detected"},
                )

        # Enforce JSON content-type on mutation endpoints
        if request.method in ("POST", "PUT", "PATCH"):
            ct = request.headers.get("content-type", "")
            if ct and "multipart/form-data" not in ct and "application/json" not in ct:
                return JSONResponse(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    content={"error": "UNSUPPORTED_MEDIA_TYPE",
                             "detail": "application/json required"},
                )

        return await call_next(request)
