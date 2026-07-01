from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import CreateRFQCommand, LineItemInput, RFQService
from ..domain.models import RFQStatus
from ..infrastructure.repository import SqlAlchemyRFQRepository
from .schemas import AwardRequest, CreateRFQRequest, RFQListResponse, RFQResponse

router = APIRouter()

def get_service(session: AsyncSession = Depends(get_db)) -> RFQService:
    return RFQService(SqlAlchemyRFQRepository(session))

@router.post("", status_code=status.HTTP_201_CREATED, response_model=RFQResponse)
async def create(body: CreateRFQRequest, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQResponse:
    return RFQResponse.from_domain(await svc.create(CreateRFQCommand(
        rfq_number=body.rfq_number, title=body.title, supplier_ids=body.supplier_ids,
        due_date=body.due_date, tenant_id=user.tenant_id, buyer_id=user.user_id,
        line_items=[LineItemInput(li.material_id, li.description, li.quantity, li.unit_of_measure, li.target_price_eur, li.notes) for li in body.line_items],
        notes=body.notes,
    )))

@router.get("", response_model=RFQListResponse)
async def list_rfqs(
    status: RFQStatus | None = Query(default=None),
    *,
    pg: PaginationDep, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQListResponse:
    items, total = await svc.list(user.tenant_id, status, pg.offset, pg.limit)
    return RFQListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[RFQResponse.from_domain(r) for r in items])

@router.get("/{rfq_id}", response_model=RFQResponse)
async def get(rfq_id: UUID, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQResponse:
    return RFQResponse.from_domain(await svc.get(rfq_id, user.tenant_id))

@router.post("/{rfq_id}/send", response_model=RFQResponse)
async def send(rfq_id: UUID, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQResponse:
    return RFQResponse.from_domain(await svc.send(rfq_id, user.tenant_id))

@router.post("/{rfq_id}/award", response_model=RFQResponse)
async def award(rfq_id: UUID, body: AwardRequest, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQResponse:
    return RFQResponse.from_domain(await svc.award(rfq_id, user.tenant_id, body.supplier_id))

@router.post("/{rfq_id}/cancel", response_model=RFQResponse)
async def cancel(rfq_id: UUID, svc: RFQService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RFQResponse:
    return RFQResponse.from_domain(await svc.cancel(rfq_id, user.tenant_id))
