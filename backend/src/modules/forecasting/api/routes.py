from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import CompleteForecastCommand, CreateForecastCommand, DataPointInput, ForecastingService
from ..infrastructure.repository import SqlAlchemyForecastRepository
from .schemas import CompleteForecastRequest, CreateForecastRequest, ForecastListResponse, ForecastResponse

router = APIRouter()

def get_service(session: AsyncSession = Depends(get_db)) -> ForecastingService:
    return ForecastingService(SqlAlchemyForecastRepository(session))

@router.post("/price", status_code=status.HTTP_201_CREATED, response_model=ForecastResponse)
async def create_price_forecast(body: CreateForecastRequest, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    f = await svc.create_price_forecast(CreateForecastCommand(material_id=body.material_id, tenant_id=user.tenant_id, horizon=body.horizon, method=body.method, model_version=body.model_version, notes=body.notes))
    return ForecastResponse.from_domain(f)

@router.get("/price", response_model=ForecastListResponse)
async def list_price_forecasts(material_id: UUID | None = Query(default=None), pg: PaginationDep = Depends(), svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastListResponse:
    items, total = await svc.list_price_forecasts(user.tenant_id, material_id, pg.offset, pg.limit)
    return ForecastListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[ForecastResponse.from_domain(f) for f in items])

@router.get("/price/{forecast_id}", response_model=ForecastResponse)
async def get_price_forecast(forecast_id: UUID, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    return ForecastResponse.from_domain(await svc.get_price_forecast(forecast_id, user.tenant_id))

@router.post("/price/{forecast_id}/complete", response_model=ForecastResponse)
async def complete_price_forecast(forecast_id: UUID, body: CompleteForecastRequest, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    cmd = CompleteForecastCommand(forecast_id=forecast_id, tenant_id=user.tenant_id, data_points=[DataPointInput(period_date=dp.period_date, value=dp.value, point_type=dp.point_type, ci_lower=dp.ci_lower, ci_upper=dp.ci_upper, ci_level=dp.ci_level) for dp in body.data_points], mae=body.mae, mape=body.mape, rmse=body.rmse, r2=body.r2)
    return ForecastResponse.from_domain(await svc.complete_price_forecast(cmd))

@router.post("/demand", status_code=status.HTTP_201_CREATED, response_model=ForecastResponse)
async def create_demand_forecast(body: CreateForecastRequest, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    f = await svc.create_demand_forecast(CreateForecastCommand(material_id=body.material_id, tenant_id=user.tenant_id, horizon=body.horizon, method=body.method, model_version=body.model_version, notes=body.notes))
    return ForecastResponse.from_domain(f)

@router.get("/demand", response_model=ForecastListResponse)
async def list_demand_forecasts(material_id: UUID | None = Query(default=None), pg: PaginationDep = Depends(), svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastListResponse:
    items, total = await svc.list_demand_forecasts(user.tenant_id, material_id, pg.offset, pg.limit)
    return ForecastListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[ForecastResponse.from_domain(f) for f in items])

@router.get("/demand/{forecast_id}", response_model=ForecastResponse)
async def get_demand_forecast(forecast_id: UUID, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    return ForecastResponse.from_domain(await svc.get_demand_forecast(forecast_id, user.tenant_id))

@router.post("/demand/{forecast_id}/complete", response_model=ForecastResponse)
async def complete_demand_forecast(forecast_id: UUID, body: CompleteForecastRequest, svc: ForecastingService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> ForecastResponse:
    cmd = CompleteForecastCommand(forecast_id=forecast_id, tenant_id=user.tenant_id, data_points=[DataPointInput(period_date=dp.period_date, value=dp.value, point_type=dp.point_type, ci_lower=dp.ci_lower, ci_upper=dp.ci_upper, ci_level=dp.ci_level) for dp in body.data_points], mae=body.mae, mape=body.mape, rmse=body.rmse, r2=body.r2)
    return ForecastResponse.from_domain(await svc.complete_demand_forecast(cmd))
