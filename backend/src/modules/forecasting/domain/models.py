from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class ForecastHorizon(StrEnum):
    SHORT = "SHORT"    # 1-3 months
    MEDIUM = "MEDIUM"  # 3-12 months
    LONG = "LONG"      # 12-36 months


class ForecastMethod(StrEnum):
    SARIMA = "SARIMA"
    LSTM = "LSTM"
    PROPHET = "PROPHET"
    ENSEMBLE = "ENSEMBLE"
    MOVING_AVERAGE = "MOVING_AVERAGE"


class ForecastStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DataPointType(StrEnum):
    HISTORICAL = "HISTORICAL"
    PREDICTED = "PREDICTED"
    ACTUAL = "ACTUAL"


@dataclass(frozen=True)
class ConfidenceInterval:
    lower: Decimal
    upper: Decimal
    confidence_level: float  # 0.8, 0.9, 0.95

    def __post_init__(self) -> None:
        if self.lower > self.upper:
            raise ValueError("lower must be <= upper")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level must be between 0 and 1")

    def contains(self, value: Decimal) -> bool:
        return self.lower <= value <= self.upper

    def width(self) -> Decimal:
        return self.upper - self.lower


@dataclass
class ForecastDataPoint:
    point_id: UUID
    period_date: date
    value: Decimal
    point_type: DataPointType
    confidence_interval: ConfidenceInterval | None = None

    @classmethod
    def historical(cls, period_date: date, value: Decimal) -> "ForecastDataPoint":
        return cls(uuid4(), period_date, value, DataPointType.HISTORICAL)

    @classmethod
    def predicted(cls, period_date: date, value: Decimal, ci: ConfidenceInterval) -> "ForecastDataPoint":
        return cls(uuid4(), period_date, value, DataPointType.PREDICTED, ci)


@dataclass
class ForecastAccuracy:
    mae: Decimal   # Mean Absolute Error
    mape: Decimal  # Mean Absolute Percentage Error
    rmse: Decimal  # Root Mean Square Error
    r2: Decimal    # R-squared

    def is_acceptable(self, mape_threshold: Decimal = Decimal("0.15")) -> bool:
        return self.mape <= mape_threshold


@dataclass
class PriceForecast:
    forecast_id: UUID
    material_id: UUID
    tenant_id: str
    horizon: ForecastHorizon
    method: ForecastMethod
    status: ForecastStatus
    data_points: list[ForecastDataPoint]
    accuracy: ForecastAccuracy | None
    model_version: str
    notes: str
    created_at: datetime
    completed_at: datetime | None
    _events: list = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        material_id: UUID,
        tenant_id: str,
        horizon: ForecastHorizon,
        method: ForecastMethod,
        model_version: str = "1.0",
        notes: str = "",
    ) -> "PriceForecast":
        return cls(
            forecast_id=uuid4(),
            material_id=material_id,
            tenant_id=tenant_id,
            horizon=horizon,
            method=method,
            status=ForecastStatus.PENDING,
            data_points=[],
            accuracy=None,
            model_version=model_version,
            notes=notes,
            created_at=datetime.utcnow(),
            completed_at=None,
        )

    def start(self) -> None:
        if self.status != ForecastStatus.PENDING:
            raise ValueError(f"Cannot start forecast in status {self.status}")
        self.status = ForecastStatus.RUNNING

    def complete(self, data_points: list[ForecastDataPoint], accuracy: ForecastAccuracy) -> None:
        if self.status != ForecastStatus.RUNNING:
            raise ValueError(f"Cannot complete forecast in status {self.status}")
        self.status = ForecastStatus.COMPLETED
        self.data_points = data_points
        self.accuracy = accuracy
        self.completed_at = datetime.utcnow()
        self._events.append(ForecastCompleted(forecast_id=self.forecast_id, material_id=self.material_id, mape=accuracy.mape))

    def fail(self, reason: str) -> None:
        self.status = ForecastStatus.FAILED
        self.notes = reason
        self.completed_at = datetime.utcnow()

    def get_predictions(self) -> list[ForecastDataPoint]:
        return [dp for dp in self.data_points if dp.point_type == DataPointType.PREDICTED]

    def pop_events(self) -> list:
        evs, self._events = self._events, []
        return evs


@dataclass(frozen=True)
class ForecastCompleted:
    forecast_id: UUID
    material_id: UUID
    mape: Decimal


@dataclass
class DemandForecast:
    forecast_id: UUID
    material_id: UUID
    tenant_id: str
    horizon: ForecastHorizon
    method: ForecastMethod
    status: ForecastStatus
    data_points: list[ForecastDataPoint]
    accuracy: ForecastAccuracy | None
    model_version: str
    notes: str
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def create(
        cls,
        material_id: UUID,
        tenant_id: str,
        horizon: ForecastHorizon,
        method: ForecastMethod,
        model_version: str = "1.0",
        notes: str = "",
    ) -> "DemandForecast":
        return cls(
            forecast_id=uuid4(),
            material_id=material_id,
            tenant_id=tenant_id,
            horizon=horizon,
            method=method,
            status=ForecastStatus.PENDING,
            data_points=[],
            accuracy=None,
            model_version=model_version,
            notes=notes,
            created_at=datetime.utcnow(),
            completed_at=None,
        )

    def complete(self, data_points: list[ForecastDataPoint], accuracy: ForecastAccuracy) -> None:
        if self.status != ForecastStatus.RUNNING:
            raise ValueError(f"Cannot complete forecast in status {self.status}")
        self.status = ForecastStatus.COMPLETED
        self.data_points = data_points
        self.accuracy = accuracy
        self.completed_at = datetime.utcnow()

    def fail(self, reason: str) -> None:
        self.status = ForecastStatus.FAILED
        self.notes = reason
        self.completed_at = datetime.utcnow()
