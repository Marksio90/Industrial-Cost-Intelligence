from __future__ import annotations
from uuid import UUID
from ....exceptions import EntityNotFound, BusinessRuleViolation


class ForecastNotFound(EntityNotFound):
    def __init__(self, forecast_id: UUID) -> None:
        super().__init__(f"Forecast {forecast_id} not found")


class InsufficientHistoricalData(BusinessRuleViolation):
    def __init__(self, material_id: UUID, required: int, available: int) -> None:
        super().__init__(f"Material {material_id} needs {required} data points, has {available}")


class ForecastAlreadyRunning(BusinessRuleViolation):
    def __init__(self, material_id: UUID) -> None:
        super().__init__(f"Forecast already running for material {material_id}")
