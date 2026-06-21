from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ..domain.exceptions import ForecastNotFound
from ..domain.models import (
    ConfidenceInterval, DataPointType, DemandForecast, ForecastAccuracy,
    ForecastDataPoint, ForecastHorizon, ForecastMethod, ForecastStatus, PriceForecast,
)
from .orm import DemandForecastORM, ForecastDataPointORM, PriceForecastORM


class AbstractForecastRepository(ABC):
    @abstractmethod
    async def get_price_forecast(self, fid: UUID, tenant_id: str) -> PriceForecast: ...
    @abstractmethod
    async def save_price_forecast(self, f: PriceForecast) -> PriceForecast: ...
    @abstractmethod
    async def list_price_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[PriceForecast], int]: ...
    @abstractmethod
    async def get_demand_forecast(self, fid: UUID, tenant_id: str) -> DemandForecast: ...
    @abstractmethod
    async def save_demand_forecast(self, f: DemandForecast) -> DemandForecast: ...
    @abstractmethod
    async def list_demand_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[DemandForecast], int]: ...


class SqlAlchemyForecastRepository(AbstractForecastRepository):
    def __init__(self, session: AsyncSession) -> None: self._s = session

    def _pq(self): return select(PriceForecastORM).options(selectinload(PriceForecastORM.data_points))
    def _dq(self): return select(DemandForecastORM).options(selectinload(DemandForecastORM.data_points))

    async def get_price_forecast(self, fid: UUID, tenant_id: str) -> PriceForecast:
        row = (await self._s.execute(self._pq().where(PriceForecastORM.id == fid, PriceForecastORM.tenant_id == tenant_id, PriceForecastORM.is_deleted.is_(False)))).scalar_one_or_none()
        if not row: raise ForecastNotFound(fid)
        return self._map_price(row)

    async def save_price_forecast(self, f: PriceForecast) -> PriceForecast:
        row = (await self._s.execute(self._pq().where(PriceForecastORM.id == f.forecast_id))).scalar_one_or_none()
        if row is None:
            row = PriceForecastORM(id=f.forecast_id, tenant_id=f.tenant_id, material_id=f.material_id, horizon=f.horizon.value, method=f.method.value, status=f.status.value, model_version=f.model_version, notes=f.notes)
            self._s.add(row)
            await self._s.flush()
        else:
            row.status = f.status.value; row.notes = f.notes; row.completed_at = f.completed_at
            if f.accuracy:
                row.mae = f.accuracy.mae; row.mape = f.accuracy.mape; row.rmse = f.accuracy.rmse; row.r2 = f.accuracy.r2
            for dp in row.data_points: await self._s.delete(dp)
            await self._s.flush()
        for dp in f.data_points:
            ci = dp.confidence_interval
            row.data_points.append(ForecastDataPointORM(id=dp.point_id, price_forecast_id=f.forecast_id, period_date=dp.period_date, value=dp.value, point_type=dp.point_type.value, ci_lower=ci.lower if ci else None, ci_upper=ci.upper if ci else None, ci_level=ci.confidence_level if ci else None))
        await self._s.flush()
        return f

    async def list_price_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[PriceForecast], int]:
        from sqlalchemy import func
        base = self._pq().where(PriceForecastORM.tenant_id == tenant_id, PriceForecastORM.is_deleted.is_(False))
        if material_id: base = base.where(PriceForecastORM.material_id == material_id)
        total = (await self._s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(PriceForecastORM.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        return [self._map_price(r) for r in rows], total

    async def get_demand_forecast(self, fid: UUID, tenant_id: str) -> DemandForecast:
        row = (await self._s.execute(self._dq().where(DemandForecastORM.id == fid, DemandForecastORM.tenant_id == tenant_id, DemandForecastORM.is_deleted.is_(False)))).scalar_one_or_none()
        if not row: raise ForecastNotFound(fid)
        return self._map_demand(row)

    async def save_demand_forecast(self, f: DemandForecast) -> DemandForecast:
        row = (await self._s.execute(self._dq().where(DemandForecastORM.id == f.forecast_id))).scalar_one_or_none()
        if row is None:
            row = DemandForecastORM(id=f.forecast_id, tenant_id=f.tenant_id, material_id=f.material_id, horizon=f.horizon.value, method=f.method.value, status=f.status.value, model_version=f.model_version, notes=f.notes)
            self._s.add(row)
            await self._s.flush()
        else:
            row.status = f.status.value; row.notes = f.notes; row.completed_at = f.completed_at
            if f.accuracy:
                row.mae = f.accuracy.mae; row.mape = f.accuracy.mape; row.rmse = f.accuracy.rmse; row.r2 = f.accuracy.r2
            for dp in row.data_points: await self._s.delete(dp)
            await self._s.flush()
        for dp in f.data_points:
            ci = dp.confidence_interval
            row.data_points.append(ForecastDataPointORM(id=dp.point_id, demand_forecast_id=f.forecast_id, period_date=dp.period_date, value=dp.value, point_type=dp.point_type.value, ci_lower=ci.lower if ci else None, ci_upper=ci.upper if ci else None, ci_level=ci.confidence_level if ci else None))
        await self._s.flush()
        return f

    async def list_demand_forecasts(self, tenant_id: str, material_id: UUID | None, offset: int, limit: int) -> tuple[list[DemandForecast], int]:
        from sqlalchemy import func
        base = self._dq().where(DemandForecastORM.tenant_id == tenant_id, DemandForecastORM.is_deleted.is_(False))
        if material_id: base = base.where(DemandForecastORM.material_id == material_id)
        total = (await self._s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(DemandForecastORM.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        return [self._map_demand(r) for r in rows], total

    @staticmethod
    def _map_dps(rows) -> list[ForecastDataPoint]:
        result = []
        for r in rows:
            ci = ConfidenceInterval(r.ci_lower, r.ci_upper, float(r.ci_level)) if r.ci_lower is not None else None
            result.append(ForecastDataPoint(r.id, r.period_date, r.value, DataPointType(r.point_type), ci))
        return result

    @classmethod
    def _map_price(cls, r: PriceForecastORM) -> PriceForecast:
        acc = ForecastAccuracy(r.mae, r.mape, r.rmse, r.r2) if r.mae is not None else None
        return PriceForecast(forecast_id=r.id, material_id=r.material_id, tenant_id=r.tenant_id, horizon=ForecastHorizon(r.horizon), method=ForecastMethod(r.method), status=ForecastStatus(r.status), data_points=cls._map_dps(r.data_points), accuracy=acc, model_version=r.model_version, notes=r.notes, created_at=r.created_at, completed_at=r.completed_at)

    @classmethod
    def _map_demand(cls, r: DemandForecastORM) -> DemandForecast:
        acc = ForecastAccuracy(r.mae, r.mape, r.rmse, r.r2) if r.mae is not None else None
        return DemandForecast(forecast_id=r.id, material_id=r.material_id, tenant_id=r.tenant_id, horizon=ForecastHorizon(r.horizon), method=ForecastMethod(r.method), status=ForecastStatus(r.status), data_points=cls._map_dps(r.data_points), accuracy=acc, model_version=r.model_version, notes=r.notes, created_at=r.created_at, completed_at=r.completed_at)
