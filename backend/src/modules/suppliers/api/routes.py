from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import CreateSupplierCommand, SupplierService, UpdateScoreCommand
from ..domain.models import SupplierStatus
from ..infrastructure.repository import SqlAlchemySupplierRepository
from .schemas import (
    CreateSupplierRequest, SupplierListResponse,
    SupplierResponse, SupplierScoreSchema, SuspendRequest,
)

router = APIRouter()


def get_service(session: AsyncSession = Depends(get_db)) -> SupplierService:
    return SupplierService(SqlAlchemySupplierRepository(session))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SupplierResponse)
async def create_supplier(
    body: CreateSupplierRequest,
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierResponse:
    cmd = CreateSupplierCommand(
        code=body.code, name=body.name,
        street=body.address.street, city=body.address.city,
        postal_code=body.address.postal_code, country_code=body.address.country_code,
        tier=body.tier, payment_terms=body.payment_terms,
        currency=body.currency, lead_time_days=body.lead_time_days,
        tenant_id=user.tenant_id, contact_email=body.contact_email,
        contact_phone=body.contact_phone, tax_id=body.tax_id,
    )
    return SupplierResponse.from_domain(await svc.create(cmd))


@router.get("", response_model=SupplierListResponse)
async def list_suppliers(
    status: SupplierStatus | None = Query(default=None),
    search: str | None = Query(default=None),
    pg: PaginationDep = Depends(),
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierListResponse:
    items, total = await svc.list(
        user.tenant_id, status=status, search=search, offset=pg.offset, limit=pg.limit
    )
    return SupplierListResponse(
        total=total, page=pg.page, page_size=pg.page_size,
        items=[SupplierResponse.from_domain(s) for s in items],
    )


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: UUID,
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierResponse:
    return SupplierResponse.from_domain(await svc.get(supplier_id, user.tenant_id))


@router.post("/{supplier_id}/qualify", response_model=SupplierResponse)
async def qualify_supplier(
    supplier_id: UUID,
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierResponse:
    return SupplierResponse.from_domain(await svc.qualify(supplier_id, user.tenant_id))


@router.post("/{supplier_id}/suspend", response_model=SupplierResponse)
async def suspend_supplier(
    supplier_id: UUID,
    body: SuspendRequest,
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierResponse:
    return SupplierResponse.from_domain(
        await svc.suspend(supplier_id, user.tenant_id, body.reason)
    )


@router.put("/{supplier_id}/score", response_model=SupplierResponse)
async def update_score(
    supplier_id: UUID,
    body: SupplierScoreSchema,
    svc: SupplierService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> SupplierResponse:
    cmd = UpdateScoreCommand(
        supplier_id=supplier_id, tenant_id=user.tenant_id,
        quality_ppm=body.quality_ppm,
        delivery_reliability=body.delivery_reliability,
        price_competitiveness=body.price_competitiveness,
        financial_score=body.financial_score,
    )
    return SupplierResponse.from_domain(await svc.update_score(cmd))
