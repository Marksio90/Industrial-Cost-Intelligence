from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import CreateProcessCommand, ProcessService
from ..domain.models import ProcessType
from ..infrastructure.repository import SqlAlchemyProcessRepository
from .schemas import CreateProcessRequest, ProcessListResponse, ProcessResponse

router = APIRouter()

def get_service(session: AsyncSession = Depends(get_db)) -> ProcessService:
    return ProcessService(SqlAlchemyProcessRepository(session))

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProcessResponse)
async def create(body: CreateProcessRequest, svc: ProcessService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ProcessResponse:
    return ProcessResponse.from_domain(await svc.create(CreateProcessCommand(
        code=body.code, name=body.name, process_type=body.process_type,
        cycle_time_seconds=body.cycle_time_seconds, setup_time_seconds=body.setup_time_seconds,
        machine_type=body.machine_type, machine_rate_eur=body.machine_rate_eur,
        skill_level=body.skill_level, labor_rate_eur=body.labor_rate_eur,
        tenant_id=user.tenant_id, location=body.location, scrap_rate=body.scrap_rate, description=body.description,
    )))

@router.get("", response_model=ProcessListResponse)
async def list_processes(
    process_type: ProcessType | None = Query(default=None),
    *,
    pg: PaginationDep, svc: ProcessService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ProcessListResponse:
    items, total = await svc.list(user.tenant_id, process_type, pg.offset, pg.limit)
    return ProcessListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[ProcessResponse.from_domain(p) for p in items])

@router.get("/{process_id}", response_model=ProcessResponse)
async def get(process_id: UUID, svc: ProcessService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ProcessResponse:
    return ProcessResponse.from_domain(await svc.get(process_id, user.tenant_id))

@router.post("/{process_id}/deprecate", response_model=ProcessResponse)
async def deprecate(process_id: UUID, svc: ProcessService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ProcessResponse:
    return ProcessResponse.from_domain(await svc.deprecate(process_id, user.tenant_id))
