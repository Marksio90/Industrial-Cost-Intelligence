"""
Entry point for the autonomous RFQ Agent.

Modes:
  1. CLI one-shot:  python -m rfq_agent.main run --goal "..."
  2. API server:    python -m rfq_agent.main serve  (FastAPI)
  3. Worker:        python -m rfq_agent.main worker  (queue-based)
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import structlog
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .agent import RFQAgent
from .config import AgentSettings, get_settings
from .database.models import Base

log = structlog.get_logger(__name__)


# ── Database setup ─────────────────────────────────────────────────────────

def _make_session_factory(settings: AgentSettings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        settings.database_url, echo=False, pool_size=5, max_overflow=10
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _create_tables(settings: AgentSettings) -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


# ── FastAPI app ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    goal: str
    tenant_id: str = "default"


class RunResponse(BaseModel):
    run_id: str
    status: str
    message: str


def create_api(settings: AgentSettings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    session_factory = _make_session_factory(settings)
    redis_client: Redis | None = None

    app = FastAPI(
        title="RFQ Agent API",
        version="1.0.0",
        default_response_class=ORJSONResponse,
    )

    @app.on_event("startup")
    async def startup() -> None:
        nonlocal redis_client
        try:
            redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
            await redis_client.ping()
            log.info("redis_connected")
        except Exception as exc:
            log.warning("redis_unavailable", error=str(exc))
            redis_client = None
        await _create_tables(settings)
        log.info("rfq_agent_api_ready")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if redis_client:
            await redis_client.aclose()

    async def get_session():
        async with session_factory() as session:
            yield session

    @app.post("/run", response_model=RunResponse)
    async def run_agent(
        body: RunRequest,
        background_tasks: BackgroundTasks,
        session: AsyncSession = Depends(get_session),
    ) -> RunResponse:
        agent = RFQAgent(settings, session, redis_client)

        async def _run() -> None:
            async with session_factory() as bg_session:
                bg_agent = RFQAgent(settings, bg_session, redis_client)
                try:
                    result = await bg_agent.run(body.goal, tenant_id=body.tenant_id)
                    log.info("background_run_done", result=result)
                except Exception as exc:
                    log.error("background_run_error", error=str(exc))

        background_tasks.add_task(_run)
        return RunResponse(
            run_id="queued",
            status="RUNNING",
            message=f"Agent started for: {body.goal[:100]}",
        )

    @app.post("/run/sync")
    async def run_agent_sync(
        body: RunRequest,
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        agent = RFQAgent(settings, session, redis_client)
        result = await agent.run(body.goal, tenant_id=body.tenant_id)
        return result

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


# ── CLI ────────────────────────────────────────────────────────────────────

async def _cli_run(goal: str, tenant_id: str = "default") -> None:
    settings = get_settings()
    session_factory = _make_session_factory(settings)

    redis_client: Redis | None = None
    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
    except Exception:
        log.warning("redis_unavailable_cli")

    await _create_tables(settings)

    async with session_factory() as session:
        agent = RFQAgent(settings, session, redis_client)
        result = await agent.run(goal, tenant_id=tenant_id)
        print(json.dumps(result, indent=2, default=str))

    if redis_client:
        await redis_client.aclose()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RFQ Autonomous Agent")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run agent for a single goal")
    run_p.add_argument("--goal", required=True)
    run_p.add_argument("--tenant", default="default")

    serve_p = sub.add_parser("serve", help="Start FastAPI server")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8001)

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_cli_run(args.goal, args.tenant))
    elif args.command == "serve":
        app = create_api()
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
