from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class ProcessType(StrEnum):
    TURNING      = "TURNING"
    MILLING      = "MILLING"
    GRINDING     = "GRINDING"
    DRILLING     = "DRILLING"
    WELDING      = "WELDING"
    PRESSING     = "PRESSING"
    FORGING      = "FORGING"
    CASTING      = "CASTING"
    HEAT_TREAT   = "HEAT_TREAT"
    COATING      = "COATING"
    ASSEMBLY     = "ASSEMBLY"
    INSPECTION   = "INSPECTION"
    OTHER        = "OTHER"


class ProcessStatus(StrEnum):
    ACTIVE     = "ACTIVE"
    DEPRECATED = "DEPRECATED"


@dataclass(frozen=True)
class MachineRate:
    machine_type: str
    rate_per_hour_eur: Decimal
    location: str = "DE"

    def __post_init__(self) -> None:
        if self.rate_per_hour_eur < 0:
            raise ValueError("Machine rate cannot be negative")


@dataclass(frozen=True)
class LaborRate:
    skill_level: str     # OPERATOR, TECHNICIAN, ENGINEER
    rate_per_hour_eur: Decimal
    location: str = "DE"


class ManufacturingProcess:
    def __init__(
        self,
        process_id: UUID,
        code: str,
        name: str,
        process_type: ProcessType,
        cycle_time_seconds: int,
        setup_time_seconds: int,
        machine_rate: MachineRate,
        labor_rate: LaborRate,
        status: ProcessStatus,
        tenant_id: str,
        scrap_rate: Decimal = Decimal("0.02"),
        description: str = "",
    ) -> None:
        self._id = process_id
        self._code = code
        self._name = name
        self._process_type = process_type
        self._cycle_time_seconds = cycle_time_seconds
        self._setup_time_seconds = setup_time_seconds
        self._machine_rate = machine_rate
        self._labor_rate = labor_rate
        self._status = status
        self._tenant_id = tenant_id
        self._scrap_rate = scrap_rate
        self._description = description

    @classmethod
    def create(
        cls,
        code: str,
        name: str,
        process_type: ProcessType,
        cycle_time_seconds: int,
        setup_time_seconds: int,
        machine_rate: MachineRate,
        labor_rate: LaborRate,
        tenant_id: str,
        scrap_rate: Decimal = Decimal("0.02"),
        description: str = "",
    ) -> "ManufacturingProcess":
        return cls(
            process_id=uuid4(), code=code, name=name,
            process_type=process_type, cycle_time_seconds=cycle_time_seconds,
            setup_time_seconds=setup_time_seconds, machine_rate=machine_rate,
            labor_rate=labor_rate, status=ProcessStatus.ACTIVE,
            tenant_id=tenant_id, scrap_rate=scrap_rate, description=description,
        )

    @property
    def id(self) -> UUID: return self._id
    @property
    def code(self) -> str: return self._code
    @property
    def name(self) -> str: return self._name
    @property
    def process_type(self) -> ProcessType: return self._process_type
    @property
    def cycle_time_seconds(self) -> int: return self._cycle_time_seconds
    @property
    def setup_time_seconds(self) -> int: return self._setup_time_seconds
    @property
    def machine_rate(self) -> MachineRate: return self._machine_rate
    @property
    def labor_rate(self) -> LaborRate: return self._labor_rate
    @property
    def status(self) -> ProcessStatus: return self._status
    @property
    def tenant_id(self) -> str: return self._tenant_id
    @property
    def scrap_rate(self) -> Decimal: return self._scrap_rate
    @property
    def description(self) -> str: return self._description

    def calculate_cost(self, quantity: int, batch_size: int = 1) -> Decimal:
        """Calculate total manufacturing cost for a quantity."""
        cycles = quantity
        setups = max(1, quantity // batch_size)
        machine_hours = (
            Decimal(cycles * self._cycle_time_seconds)
            + Decimal(setups * self._setup_time_seconds)
        ) / Decimal(3600)
        scrap_factor = Decimal(1) / (Decimal(1) - self._scrap_rate)
        return (
            machine_hours * (self._machine_rate.rate_per_hour_eur + self._labor_rate.rate_per_hour_eur)
            * scrap_factor
        ).quantize(Decimal("0.0001"))

    def deprecate(self) -> None:
        self._status = ProcessStatus.DEPRECATED
