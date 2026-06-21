from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from ..domain.models import DataPointType, ForecastHorizon, ForecastMethod, ForecastStatus


class CreateForecastRequest(BaseModel):
    material_id: UUID
    horizon: ForecastHorizon
    method: ForecastMethod
    model_version: str = "1.0"
    notes: str = ""


class DataPointInput(BaseModel):
    period_date: date
    value: Decimal
    point_type: DataPointType
    ci_lower: Decimal | None = None
    ci_upper: Decimal | None = None
    ci_level: float | None = Field(default=None, ge=0, le=1)


class CompleteForecastRequest(BaseModel):
    data_points: list[DataPointInput] = Field(..., min_length=1)
    mae: Decimal = Field(..., ge=0)
    mape: Decimal = Field(..., ge=0)
    rmse: Decimal = Field(..., ge=0)
    r2: Decimal


class ConfidenceIntervalResponse(BaseModel):
    lower: Decimal; upper: Decimal; confidence_level: float


class DataPointResponse(BaseModel):
    point_id: UUID; period_date: date; value: Decimal; point_type: DataPointType
    confidence_interval: ConfidenceIntervalResponse | None


class AccuracyResponse(BaseModel):
    mae: Decimal; mape: Decimal; rmse: Decimal; r2: Decimal


class ForecastResponse(BaseModel):
    forecast_id: UUID; material_id: UUID; tenant_id: str
    horizon: ForecastHorizon; method: ForecastMethod; status: ForecastStatus
    model_version: str; notes: str
    accuracy: AccuracyResponse | None
    data_points: list[DataPointResponse]
    created_at: datetime; completed_at: datetime | None

    @classmethod
    def from_domain(cls, f) -> "ForecastResponse":
        return cls(
            forecast_id=f.forecast_id, material_id=f.material_id, tenant_id=f.tenant_id,
            horizon=f.horizon, method=f.method, status=f.status,
            model_version=f.model_version, notes=f.notes,
            accuracy=AccuracyResponse(mae=f.accuracy.mae, mape=f.accuracy.mape, rmse=f.accuracy.rmse, r2=f.accuracy.r2) if f.accuracy else None,
            data_points=[DataPointResponse(point_id=dp.point_id, period_date=dp.period_date, value=dp.value, point_type=dp.point_type, confidence_interval=ConfidenceIntervalResponse(lower=dp.confidence_interval.lower, upper=dp.confidence_interval.upper, confidence_level=dp.confidence_interval.confidence_level) if dp.confidence_interval else None) for dp in f.data_points],
            created_at=f.created_at, completed_at=f.completed_at,
        )


class ForecastListResponse(BaseModel):
    total: int; page: int; page_size: int; items: list[ForecastResponse]
