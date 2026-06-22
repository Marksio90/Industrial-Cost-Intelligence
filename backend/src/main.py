from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse

from .config import get_settings
from .database import engine, Base
from .exceptions import DomainError, ApplicationError
from .logging_config import configure_logging
from .observability import configure_logging as obs_configure_logging, configure_tracing
from .observability.middleware import ObservabilityMiddleware, prometheus_metrics_endpoint
from .observability.incident_detector import build_default_detector
from .security.api_hardening import (
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
    RequestValidationMiddleware,
)

# Module routers
from .modules.materials.api.routes import router as materials_router
from .modules.processes.api.routes import router as processes_router
from .modules.suppliers.api.routes import router as suppliers_router
from .modules.costs.api.routes import router as costs_router
from .modules.rfq.api.routes import router as rfq_router
from .modules.quotes.api.routes import router as quotes_router
from .modules.forecasting.api.routes import router as forecasting_router
from .modules.risk.api.routes import router as risk_router
from .modules.search.api.routes import router as search_router

settings = get_settings()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    obs_configure_logging(level=settings.log_level, json_logs=settings.log_json)
    logger.info("startup", version=settings.app_version, env=settings.environment)

    if settings.otel_enabled:
        configure_tracing(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_endpoint,
            environment=settings.environment,
            version=settings.app_version,
        )

    # Start incident detector as background task
    redis_url = getattr(settings, "redis_cache_url", None)
    detector = build_default_detector(redis_url=redis_url)
    import asyncio
    detector_task = asyncio.create_task(detector.run())
    app.state.incident_detector = detector

    yield

    detector_task.cancel()
    await engine.dispose()
    logger.info("shutdown")



def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        default_response_class=ORJSONResponse,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    # Outermost first — evaluated in reverse registration order by Starlette.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        redis_url=getattr(settings, "redis_cache_url", None),
    )
    app.add_middleware(RequestValidationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ObservabilityMiddleware)

    # ── Exception Handlers ────────────────────────────────────────────────────
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        logger.warning("domain_error", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(ApplicationError)
    async def app_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        logger.error("application_error", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
        )

    # ── Routes ────────────────────────────────────────────────────────────────
    API_PREFIX = "/api/v1"

    app.add_route("/metrics", prometheus_metrics_endpoint)

    @app.get("/health", tags=["infra"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/health/ready", tags=["infra"])
    async def readiness() -> dict[str, Any]:
        from sqlalchemy import text
        from .database import AsyncSessionFactory
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(materials_router,   prefix=f"{API_PREFIX}/materials",   tags=["Materials"])
    app.include_router(processes_router,   prefix=f"{API_PREFIX}/processes",   tags=["Processes"])
    app.include_router(suppliers_router,   prefix=f"{API_PREFIX}/suppliers",   tags=["Suppliers"])
    app.include_router(costs_router,       prefix=f"{API_PREFIX}/costs",       tags=["Costs"])
    app.include_router(rfq_router,         prefix=f"{API_PREFIX}/rfq",         tags=["RFQ"])
    app.include_router(quotes_router,      prefix=f"{API_PREFIX}/quotes",      tags=["Quotes"])
    app.include_router(forecasting_router, prefix=f"{API_PREFIX}/forecasting", tags=["Forecasting"])
    app.include_router(risk_router,        prefix=f"{API_PREFIX}/risk",        tags=["Risk"])
    app.include_router(search_router,     prefix=f"{API_PREFIX}/search",      tags=["Search"])

    return app


app = create_app()
