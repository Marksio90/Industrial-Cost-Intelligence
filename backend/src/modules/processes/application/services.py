from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID
from ..domain.models import LaborRate, MachineRate, ManufacturingProcess, ProcessType
from ..infrastructure.repository import AbstractProcessRepository


@dataclass
class CreateProcessCommand:
    code: str; name: str; process_type: ProcessType; cycle_time_seconds: int
    setup_time_seconds: int; machine_type: str; machine_rate_eur: Decimal
    skill_level: str; labor_rate_eur: Decimal; tenant_id: str
    scrap_rate: Decimal = Decimal("0.02"); description: str = ""
    location: str = "DE"


class ProcessService:
    def __init__(self, repo: AbstractProcessRepository) -> None:
        self._repo = repo

    async def create(self, cmd: CreateProcessCommand) -> ManufacturingProcess:
        p = ManufacturingProcess.create(
            code=cmd.code, name=cmd.name, process_type=cmd.process_type,
            cycle_time_seconds=cmd.cycle_time_seconds, setup_time_seconds=cmd.setup_time_seconds,
            machine_rate=MachineRate(cmd.machine_type, cmd.machine_rate_eur, cmd.location),
            labor_rate=LaborRate(cmd.skill_level, cmd.labor_rate_eur, cmd.location),
            tenant_id=cmd.tenant_id, scrap_rate=cmd.scrap_rate, description=cmd.description,
        )
        return await self._repo.save(p)

    async def get(self, pid: UUID, tenant_id: str) -> ManufacturingProcess:
        return await self._repo.get_by_id(pid, tenant_id)

    async def list(self, tenant_id: str, process_type: ProcessType | None = None, offset: int = 0, limit: int = 20) -> tuple[list[ManufacturingProcess], int]:
        return await self._repo.list(tenant_id, process_type, offset, limit)

    async def deprecate(self, pid: UUID, tenant_id: str) -> ManufacturingProcess:
        p = await self._repo.get_by_id(pid, tenant_id)
        p.deprecate()
        return await self._repo.save(p)
