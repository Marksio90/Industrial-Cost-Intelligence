from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MaterialInput(BaseModel):
    material_class: str | None = None
    density_kg_m3: float | None = None
    hardness_hv: float | None = None
    ultimate_tensile_strength_mpa: float | None = None
    yield_strength_mpa: float | None = None
    elongation_pct: float | None = None
    thermal_conductivity_w_mk: float | None = None
    price_per_kg_eur: float | None = None
    machinability_index: float | None = Field(default=None, ge=0.0, le=1.0)


class ProcessInput(BaseModel):
    process_type: str | None = None
    setup_time_h: float | None = None
    cycle_time_min: float | None = None
    machine_hourly_rate_eur: float | None = None
    operator_count: int | None = None
    scrap_rate_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    tooling_cost_eur: float | None = None
    num_operations: int | None = None
    surface_finish_ra: float | None = None
    tolerance_grade: str | None = None
    requires_heat_treatment: bool = False
    requires_inspection: bool = False
    batch_size: int = Field(default=1, ge=1)


class GeometryInput(BaseModel):
    length_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    volume_cm3: float | None = None
    surface_area_cm2: float | None = None
    wall_thickness_mm: float | None = None
    num_holes: int = 0
    num_threads: int = 0
    num_pockets: int = 0


class SupplierInput(BaseModel):
    overall_score: float | None = Field(default=None, ge=0.0, le=1.0)
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    delivery_score: float | None = Field(default=None, ge=0.0, le=1.0)
    price_score: float | None = Field(default=None, ge=0.0, le=1.0)
    financial_score: float | None = Field(default=None, ge=0.0, le=1.0)
    years_active: int | None = None
    capacity_utilisation: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_lead_time_days: float | None = None
    quote_win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_price_deviation_pct: float | None = None
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    supplier_tier: str | None = None
    certifications: str | None = None


class CostPredictionRequest(BaseModel):
    material: MaterialInput = Field(default_factory=MaterialInput)
    process: ProcessInput = Field(default_factory=ProcessInput)
    geometry: GeometryInput = Field(default_factory=GeometryInput)
    supplier: SupplierInput = Field(default_factory=SupplierInput)
    quantity: float = Field(default=1.0, gt=0)
    request_id: str | None = None

    def to_flat_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        d.update(self.material.model_dump())
        d.update(self.process.model_dump())
        d.update(self.geometry.model_dump())
        d.update(self.supplier.model_dump())
        d["quantity"] = self.quantity
        return d


class ConfidenceInterval(BaseModel):
    lower: float
    upper: float
    confidence_level: float = 0.9


class CostPredictionResponse(BaseModel):
    request_id: str | None
    predicted_cost_eur: float
    confidence_interval: ConfidenceInterval | None = None
    model_version: str
    feature_contributions: dict[str, float] | None = None
    latency_ms: float
    cached: bool = False


class BatchPredictionRequest(BaseModel):
    items: list[CostPredictionRequest] = Field(..., min_length=1, max_length=500)


class BatchPredictionResponse(BaseModel):
    predictions: list[CostPredictionResponse]
    n_items: int
    total_latency_ms: float


class ModelInfoResponse(BaseModel):
    model_name: str
    version: str
    stage: str
    mape: float | None
    rmse: float | None
    n_features: int
