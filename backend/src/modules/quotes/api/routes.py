from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import CreateQuoteCommand, LineItemInput, QuoteService
from ..domain.models import QuoteStatus
from ..infrastructure.repository import SqlAlchemyQuoteRepository
from .schemas import CreateQuoteRequest, QuoteListResponse, QuoteResponse, RejectQuoteRequest

router = APIRouter()

def get_service(session: AsyncSession = Depends(get_db)) -> QuoteService:
    return QuoteService(SqlAlchemyQuoteRepository(session))

@router.post("", status_code=status.HTTP_201_CREATED, response_model=QuoteResponse)
async def create(body: CreateQuoteRequest, svc: QuoteService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> QuoteResponse:
    return QuoteResponse.from_domain(await svc.create(CreateQuoteCommand(
        rfq_id=body.rfq_id, supplier_id=body.supplier_id, quote_number=body.quote_number,
        validity_date=body.validity_date, tenant_id=user.tenant_id,
        line_items=[LineItemInput(li.material_id, li.description, li.quantity, li.unit_of_measure, li.unit_price_eur, li.lead_time_days, li.notes) for li in body.line_items],
        currency=body.currency, delivery_terms=body.delivery_terms, payment_terms=body.payment_terms, notes=body.notes,
    )))

@router.get("", response_model=QuoteListResponse)
async def list_quotes(rfq_id: UUID | None = Query(default=None), supplier_id: UUID | None = Query(default=None), status: QuoteStatus | None = Query(default=None), pg: PaginationDep = Depends(), svc: QuoteService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> QuoteListResponse:
    items, total = await svc.list(user.tenant_id, rfq_id, supplier_id, status, pg.offset, pg.limit)
    return QuoteListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[QuoteResponse.from_domain(q) for q in items])

@router.get("/{quote_id}", response_model=QuoteResponse)
async def get(quote_id: UUID, svc: QuoteService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> QuoteResponse:
    return QuoteResponse.from_domain(await svc.get(quote_id, user.tenant_id))

@router.post("/{quote_id}/accept", response_model=QuoteResponse)
async def accept(quote_id: UUID, svc: QuoteService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> QuoteResponse:
    return QuoteResponse.from_domain(await svc.accept(quote_id, user.tenant_id))

@router.post("/{quote_id}/reject", response_model=QuoteResponse)
async def reject(quote_id: UUID, body: RejectQuoteRequest, svc: QuoteService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> QuoteResponse:
    return QuoteResponse.from_domain(await svc.reject(quote_id, user.tenant_id, body.reason))
