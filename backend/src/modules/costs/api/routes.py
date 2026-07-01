from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import ComponentInput, CreateBreakdownCommand, CostService
from ..domain.models import CostStatus
from ..infrastructure.repository import SqlAlchemyCostRepository
from .schemas import (
    CostBreakdownResponse, CostListResponse,
    CreateBreakdownRequest, RejectRequest, ReviseRequest,
)

router = APIRouter()


def get_service(session: AsyncSession = Depends(get_db)) -> CostService:
    return CostService(SqlAlchemyCostRepository(session))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CostBreakdownResponse)
async def create_breakdown(
    body: CreateBreakdownRequest,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostBreakdownResponse:
    cmd = CreateBreakdownCommand(
        bom_item_id=body.bom_item_id,
        reference_number=body.reference_number,
        quantity=body.quantity,
        components=[ComponentInput(c.category, c.amount_eur, c.description) for c in body.components],
        tenant_id=user.tenant_id,
        location=body.location,
        currency=body.currency,
        notes=body.notes,
    )
    return CostBreakdownResponse.from_domain(await svc.create(cmd))


@router.get("", response_model=CostListResponse)
async def list_breakdowns(
    bom_item_id: UUID | None = Query(default=None),
    status: CostStatus | None = Query(default=None),
    *,
    pg: PaginationDep,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostListResponse:
    items, total = await svc.list(
        user.tenant_id, bom_item_id=bom_item_id, status=status,
        offset=pg.offset, limit=pg.limit,
    )
    return CostListResponse(
        total=total, page=pg.page, page_size=pg.page_size,
        items=[CostBreakdownResponse.from_domain(cb) for cb in items],
    )


@router.get("/{breakdown_id}", response_model=CostBreakdownResponse)
async def get_breakdown(
    breakdown_id: UUID,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostBreakdownResponse:
    return CostBreakdownResponse.from_domain(await svc.get(breakdown_id, user.tenant_id))


@router.post("/{breakdown_id}/approve", response_model=CostBreakdownResponse)
async def approve(
    breakdown_id: UUID,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostBreakdownResponse:
    return CostBreakdownResponse.from_domain(
        await svc.approve(breakdown_id, user.tenant_id, str(user.user_id))
    )


@router.post("/{breakdown_id}/reject", response_model=CostBreakdownResponse)
async def reject(
    breakdown_id: UUID,
    body: RejectRequest,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostBreakdownResponse:
    return CostBreakdownResponse.from_domain(
        await svc.reject(breakdown_id, user.tenant_id, body.reason)
    )


@router.post("/{breakdown_id}/revise", response_model=CostBreakdownResponse)
async def revise(
    breakdown_id: UUID,
    body: ReviseRequest,
    svc: CostService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> CostBreakdownResponse:
    return CostBreakdownResponse.from_domain(
        await svc.revise(
            breakdown_id, user.tenant_id,
            [ComponentInput(c.category, c.amount_eur, c.description) for c in body.components],
            body.notes,
        )
    )
