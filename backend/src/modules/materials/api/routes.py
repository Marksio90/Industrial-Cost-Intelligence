from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....database import get_db
from ....dependencies import Pagination, PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import (
    CreateMaterialCommand,
    ListMaterialsQuery,
    MaterialService,
    UpdateMaterialCommand,
    UpdatePriceCommand,
)
from ..domain.models import MaterialClass, MaterialStatus
from ..infrastructure.repository import SqlAlchemyMaterialRepository
from .schemas import (
    CreateMaterialRequest,
    DeprecateRequest,
    MaterialListResponse,
    MaterialResponse,
    UpdateMaterialRequest,
    UpdatePriceRequest,
)

router = APIRouter()


def get_service(session: AsyncSession = Depends(get_db)) -> MaterialService:
    return MaterialService(SqlAlchemyMaterialRepository(session))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MaterialResponse)
async def create_material(
    body: CreateMaterialRequest,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    cmd = CreateMaterialCommand(
        material_number=body.material_number,
        name=body.name,
        material_class=body.material_class,
        unit_of_measure=body.unit_of_measure,
        base_price_eur=body.base_price_eur,
        description=body.description,
        alloy=body.alloy,
        density_g_cm3=body.density_g_cm3,
        min_order_qty=body.min_order_qty,
        lead_time_days=body.lead_time_days,
        tenant_id=user.tenant_id,
        created_by=str(user.user_id),
    )
    material = await service.create(cmd)
    return MaterialResponse.from_domain(material)


@router.get("", response_model=MaterialListResponse)
async def list_materials(
    material_class: MaterialClass | None = Query(default=None),
    status: MaterialStatus | None = Query(default=None),
    search: str | None = Query(default=None, max_length=128),
    pagination: PaginationDep = Depends(),
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialListResponse:
    result = await service.list(
        ListMaterialsQuery(
            tenant_id=user.tenant_id,
            material_class=material_class,
            status=status,
            search=search,
            offset=pagination.offset,
            limit=pagination.limit,
        )
    )
    return MaterialListResponse(
        total=result.total,
        page=pagination.page,
        page_size=pagination.page_size,
        items=[MaterialResponse.from_domain(m) for m in result.items],
    )


@router.get("/{material_id}", response_model=MaterialResponse)
async def get_material(
    material_id: UUID,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    material = await service.get_by_id(material_id, user.tenant_id)
    return MaterialResponse.from_domain(material)


@router.patch("/{material_id}", response_model=MaterialResponse)
async def update_material(
    material_id: UUID,
    body: UpdateMaterialRequest,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    cmd = UpdateMaterialCommand(
        material_id=material_id,
        tenant_id=user.tenant_id,
        name=body.name,
        description=body.description,
        lead_time_days=body.lead_time_days,
        min_order_qty=body.min_order_qty,
        updated_by=str(user.user_id),
    )
    material = await service.update(cmd)
    return MaterialResponse.from_domain(material)


@router.put("/{material_id}/price", response_model=MaterialResponse)
async def update_price(
    material_id: UUID,
    body: UpdatePriceRequest,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    cmd = UpdatePriceCommand(
        material_id=material_id,
        tenant_id=user.tenant_id,
        new_price_eur=body.base_price_eur,
        updated_by=str(user.user_id),
    )
    material = await service.update_price(cmd)
    return MaterialResponse.from_domain(material)


@router.post("/{material_id}/deprecate", response_model=MaterialResponse)
async def deprecate_material(
    material_id: UUID,
    body: DeprecateRequest,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    material = await service.deprecate(material_id, user.tenant_id, body.reason)
    return MaterialResponse.from_domain(material)


@router.post("/{material_id}/reactivate", response_model=MaterialResponse)
async def reactivate_material(
    material_id: UUID,
    service: MaterialService = Depends(get_service),
    user: CurrentUser = Depends(get_current_user),
) -> MaterialResponse:
    material = await service.reactivate(material_id, user.tenant_id)
    return MaterialResponse.from_domain(material)
