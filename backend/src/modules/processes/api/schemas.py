from __future__ import annotations
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from ..domain.models import ProcessStatus, ProcessType


class CreateProcessRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=256)
    process_type: ProcessType
    cycle_time_seconds: int = Field(..., ge=1)
    setup_time_seconds: int = Field(..., ge=0)
    machine_type: str = Field(..., min_length=1, max_length=64)
    machine_rate_eur: Decimal = Field(..., ge=0)
    skill_level: str = Field(..., min_length=1, max_length=16)
    labor_rate_eur: Decimal = Field(..., ge=0)
    location: str = Field(default="DE", max_length=4)
    scrap_rate: Decimal = Field(default=Decimal("0.02"), ge=0, lt=1)
    description: str = Field(default="", max_length=2000)


class ProcessResponse(BaseModel):
    id: UUID; code: str; name: str; process_type: ProcessType
    cycle_time_seconds: int; setup_time_seconds: int
    machine_type: str; machine_rate_eur: Decimal; location: str
    skill_level: str; labor_rate_eur: Decimal; scrap_rate: Decimal
    status: ProcessStatus; description: str; tenant_id: str

    @classmethod
    def from_domain(cls, p) -> "ProcessResponse":
        return cls(
            id=p.id, code=p.code, name=p.name, process_type=p.process_type,
            cycle_time_seconds=p.cycle_time_seconds, setup_time_seconds=p.setup_time_seconds,
            machine_type=p.machine_rate.machine_type,
            machine_rate_eur=p.machine_rate.rate_per_hour_eur,
            location=p.machine_rate.location, skill_level=p.labor_rate.skill_level,
            labor_rate_eur=p.labor_rate.rate_per_hour_eur, scrap_rate=p.scrap_rate,
            status=p.status, description=p.description, tenant_id=p.tenant_id,
        )


class ProcessListResponse(BaseModel):
    total: int; page: int; page_size: int; items: list[ProcessResponse]
