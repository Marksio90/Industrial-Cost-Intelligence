"""
ICI OpenTelemetry tracing setup.

Instruments:
  - FastAPI (auto-instrument via FastAPIInstrumentor)
  - SQLAlchemy async engine (SQLAlchemyInstrumentor)
  - Redis (RedisInstrumentor)
  - HTTPX outbound calls (HTTPXClientInstrumentor)
  - Anthropic SDK calls (manual span wrapper)
  - Playwright (manual span wrapper)

Exporters configured via settings:
  - OTLP gRPC → otel-collector (production)
  - Console (development when otel_endpoint is empty)

Usage:
    from src.observability.tracing import configure_tracing, get_tracer, traced

    configure_tracing()          # call once at startup

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("custom.key", "value")
        ...

    # Or as a decorator:
    @traced("compute_cost")
    async def compute_cost(part_id: int) -> float:
        ...
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def configure_tracing(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
    environment: str = "production",
    version: str = "1.0.0",
) -> None:
    """
    Bootstrap OpenTelemetry. Safe to call multiple times (idempotent).
    Silently skips if opentelemetry packages are not installed.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        logger.warning("opentelemetry-sdk not installed — tracing disabled")
        return

    from src.config import get_settings
    s = get_settings()

    svc_name   = service_name or s.otel_service_name
    otel_ep    = endpoint     or s.otel_endpoint
    env        = environment  or s.environment
    svc_ver    = version      or s.app_version

    resource = Resource.create({
        SERVICE_NAME:          svc_name,
        DEPLOYMENT_ENVIRONMENT: env,
        "service.version":     svc_ver,
        "service.namespace":   "ici",
    })

    provider = TracerProvider(resource=resource)

    if otel_ep:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otel_ep, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTLP tracing exporter configured", extra={"endpoint": otel_ep})
        except ImportError:
            logger.warning("opentelemetry-exporter-otlp not installed")
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    _instrument_libraries()


def _instrument_libraries() -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument(
            excluded_urls="/health,/health/ready,/metrics",
        )
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument(
            enable_commenter=True,
            commenter_options={"db_driver": True, "db_framework": False},
        )
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass


def get_tracer(name: str = "ici") -> Any:
    """Return an OTel tracer, or a no-op stub if OTel is not installed."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoopTracer()


def current_trace_id() -> str | None:
    """Return the hex trace ID of the active span, if any."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        ctx  = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except ImportError:
        pass
    return None


def set_span_attributes(**kwargs: Any) -> None:
    """Add key=value attributes to the active span without importing OTel at call site."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        for k, v in kwargs.items():
            span.set_attribute(k, v)
    except (ImportError, Exception):
        pass


def record_exception(exc: Exception, *, escaped: bool = True) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.trace import StatusCode
        span = trace.get_current_span()
        span.record_exception(exc)
        span.set_status(StatusCode.ERROR, str(exc))
    except (ImportError, Exception):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# @traced decorator
# ─────────────────────────────────────────────────────────────────────────────

def traced(
    operation_name: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """
    Async decorator that wraps a function in an OTel span.

        @traced("predict_cost", attributes={"model": "lgbm"})
        async def predict(features: dict) -> float: ...
    """
    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        span_name = operation_name or fn.__qualname__

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            tracer = get_tracer(fn.__module__)
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if record_exception:
                        try:
                            from opentelemetry.trace import StatusCode
                            span.record_exception(exc)
                            span.set_status(StatusCode.ERROR, str(exc))
                        except (ImportError, Exception):
                            pass
                    raise

        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic LLM span helper
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicSpan:
    """
    Context manager that wraps a single Anthropic messages.create() call
    with semantic OTel attributes aligned to the gen_ai spec.

    Usage:
        with AnthropicSpan(model="claude-sonnet-4-6", session_id=rfq_id) as span:
            response = await client.messages.create(...)
            span.record_response(response)
    """
    def __init__(self, model: str, session_id: str = "", **extra: Any) -> None:
        self._model      = model
        self._session_id = session_id
        self._extra      = extra
        self._span: Any  = None

    def __enter__(self) -> "AnthropicSpan":
        tracer = get_tracer("ici.llm")
        self._cm = tracer.start_as_current_span("llm.chat")
        self._span = self._cm.__enter__()
        try:
            self._span.set_attribute("gen_ai.system", "anthropic")
            self._span.set_attribute("gen_ai.request.model", self._model)
            if self._session_id:
                self._span.set_attribute("gen_ai.session_id", self._session_id)
            for k, v in self._extra.items():
                self._span.set_attribute(f"gen_ai.{k}", str(v))
        except Exception:
            pass
        return self

    def record_response(self, response: Any) -> None:
        if self._span is None:
            return
        try:
            usage = getattr(response, "usage", None)
            if usage:
                self._span.set_attribute("gen_ai.usage.input_tokens",  usage.input_tokens)
                self._span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
            self._span.set_attribute("gen_ai.response.stop_reason", str(response.stop_reason))
        except Exception:
            pass

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._span and exc_type:
            try:
                self._span.record_exception(exc_val)
            except Exception:
                pass
        if self._cm:
            return bool(self._cm.__exit__(exc_type, exc_val, exc_tb))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# No-op stub (used when OTel not installed)
# ─────────────────────────────────────────────────────────────────────────────

class _NoopSpan:
    def set_attribute(self, *_: Any, **__: Any) -> None: pass
    def record_exception(self, *_: Any, **__: Any) -> None: pass
    def set_status(self, *_: Any, **__: Any) -> None: pass
    def __enter__(self) -> "_NoopSpan": return self
    def __exit__(self, *_: Any) -> bool: return False


class _NoopTracer:
    def start_as_current_span(self, name: str, **_: Any) -> _NoopSpan:
        return _NoopSpan()
