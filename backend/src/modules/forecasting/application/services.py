from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID
from ..domain.models import (
    ConfidenceInterval, DataPointType, DemandForecast, ForecastAccuracy,
    ForecastDataPoint, ForecastHorizon, ForecastMethod, ForecastStatus, PriceForecast,
)
from ..infrastructure.repository import AbstractForecastRepository


@dataclass
class CreateForecastCommand:
    material_id: UUID
    tenant_id: str
    horizon: ForecastHorizon
    method: ForecastMethod
    model_version: str = "1.0"
    notes: str = ""


@dataclass
class DataPointInput:
    period_date: object
    value: Decimal
    point_type: DataPointType
    ci_lower: Decimal | None = None
    ci_upper: Decimal | None = None
    ci_level: float | None = None


@dataclass
class CompleteForecastCommand:
    forecast_id: UUID
    tenant_id: str
    data_points: list[DataPointInput]
    mae: Decimal
    mape: Decimal
    rmse: Decimal
    r2: Decimal


class ForecastingService:
    def __init__(self, repo: AbstractForecastRepository) -> None: self._repo = repo

    async def create_price_forecast(self, cmd: CreateForecastCommand) -> PriceForecast:
        f = PriceForecast.create(material_id=cmd.material_id, tenant_id=cmd.tenant_id, horizon=cmd.horizon, method=cmd.method, model_version=cmd.model_version, notes=cmd.notes)
        f.start()
        return await self._repo.save_price_forecast(f)

    async def complete_price_forecast(self, cmd: CompleteForecastCommand) -> PriceForecast:
        f = await self._repo.get_price_forecast(cmd.forecast_id, cmd.tenant_id)
        dps = [self._build_dp(dp) for dp in cmd.data_points]
        acc = ForecastAccuracy(cmd.mae, cmd.mape, cmd.rmse, cmd.r2)
        f.complete(dps, acc)
        return await self._repo.save_price_forecast(f)

    async def fail_price_forecast(self, forecast_id: UUID, tenant_id: str, reason: str) -> PriceForecast:
        f = await self._repo.get_price_forecast(forecast_id, tenant_id)
        f.fail(reason)
        return await self._repo.save_price_forecast(f)

    async def get_price_forecast(self, fid: UUID, tenant_id: str) -> PriceForecast:
        return await self._repo.get_price_forecast(fid, tenant_id)

    async def list_price_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[PriceForecast], int]:
        return await self._repo.list_price_forecasts(tenant_id, material_id, offset, limit)

    async def create_demand_forecast(self, cmd: CreateForecastCommand) -> DemandForecast:
        f = DemandForecast.create(material_id=cmd.material_id, tenant_id=cmd.tenant_id, horizon=cmd.horizon, method=cmd.method, model_version=cmd.model_version, notes=cmd.notes)
        return await self._repo.save_demand_forecast(f)

    async def complete_demand_forecast(self, cmd: CompleteForecastCommand) -> DemandForecast:
        f = await self._repo.get_demand_forecast(cmd.forecast_id, cmd.tenant_id)
        dps = [self._build_dp(dp) for dp in cmd.data_points]
        acc = ForecastAccuracy(cmd.mae, cmd.mape, cmd.rmse, cmd.r2)
        f.complete(dps, acc)
        return await self._repo.save_demand_forecast(f)

    async def get_demand_forecast(self, fid: UUID, tenant_id: str) -> DemandForecast:
        return await self._repo.get_demand_forecast(fid, tenant_id)

    async def list_demand_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[DemandForecast], int]:
        return await self._repo.list_demand_forecasts(tenant_id, material_id, offset, limit)

    @staticmethod
    def _build_dp(inp: DataPointInput) -> ForecastDataPoint:
        ci = ConfidenceInterval(inp.ci_lower, inp.ci_upper, inp.ci_level) if inp.ci_lower is not None else None
        return ForecastDataPoint.historical(inp.period_date, inp.value) if inp.point_type == DataPointType.HISTORICAL else ForecastDataPoint.predicted(inp.period_date, inp.value, ci)
