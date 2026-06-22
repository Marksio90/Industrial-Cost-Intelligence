"""
ICI structured logging — built on structlog.

Features:
  - JSON output in production, coloured dev console in development
  - Automatic request_id / tenant_id context via contextvars
  - Correlation ID passed through to downstream spans
  - Sensitive field redaction (password, token, api_key, secret)
  - Async-safe (no thread-local state)

Usage:
    from src.observability.logging import get_logger, bind_request_context

    logger = get_logger(__name__)

    # In request handler / middleware:
    bind_request_context(request_id="abc", tenant_id="acme")

    logger.info("order_created", order_id=42, amount_eur=150.0)
"""
from __future__ import annotations

import logging
import logging.config
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# ─────────────────────────────────────────────────────────────────────────────
# Redaction
# ─────────────────────────────────────────────────────────────────────────────

_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential", "private_key", "access_key",
    "secret_key", "refresh_token", "id_token", "bearer",
})


def _redact_sensitive(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


# ─────────────────────────────────────────────────────────────────────────────
# Processors
# ─────────────────────────────────────────────────────────────────────────────

def _add_service_context(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    from src.config import get_settings
    s = get_settings()
    event_dict.setdefault("service", s.otel_service_name)
    event_dict.setdefault("version", s.app_version)
    event_dict.setdefault("env", s.environment)
    return event_dict


def _add_log_level(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    event_dict["level"] = method.upper()
    return event_dict


# Shared processor chain used by both JSON and console renderers
_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    _add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
    _redact_sensitive,
    _add_service_context,
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """
    Call once at application startup (in lifespan or main).
    Idempotent — safe to call multiple times.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    if json_logs:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Quiet noisy libraries
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Propagate uvicorn errors through structlog
    logging.getLogger("uvicorn.error").setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(
    *,
    request_id: str,
    tenant_id: str = "unknown",
    user_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """
    Bind per-request keys to structlog contextvars.
    Must be called before any logging in the same async context.
    correlation_id should match the OpenTelemetry trace ID when both are used.
    """
    ctx: dict[str, str] = {
        "request_id": request_id,
        "tenant_id":  tenant_id,
    }
    if user_id:
        ctx["user_id"] = user_id
    if correlation_id:
        ctx["correlation_id"] = correlation_id

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**ctx)


def bind_job_context(
    *,
    job_id: str,
    job_type: str,
    queue: str = "default",
    tenant_id: str = "unknown",
) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        job_id=job_id,
        job_type=job_type,
        queue=queue,
        tenant_id=tenant_id,
    )
