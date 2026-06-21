from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..domain.exceptions import ProcessCodeExists, ProcessNotFound
from ..domain.models import LaborRate, MachineRate, ManufacturingProcess, ProcessStatus, ProcessType
from .orm import ProcessORM


class AbstractProcessRepository(ABC):
    @abstractmethod
    async def get_by_id(self, pid: UUID, tenant_id: str) -> ManufacturingProcess: ...
    @abstractmethod
    async def save(self, p: ManufacturingProcess) -> ManufacturingProcess: ...
    @abstractmethod
    async def list(self, tenant_id: str, process_type: ProcessType | None, offset: int, limit: int) -> tuple[list[ManufacturingProcess], int]: ...


class SqlAlchemyProcessRepository(AbstractProcessRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, pid: UUID, tenant_id: str) -> ManufacturingProcess:
        row = await self._s.get(ProcessORM, pid)
        if not row or row.tenant_id != tenant_id or row.is_deleted:
            raise ProcessNotFound(pid)
        return self._map(row)

    async def save(self, p: ManufacturingProcess) -> ManufacturingProcess:
        row = (await self._s.execute(select(ProcessORM).where(ProcessORM.id == p.id))).scalar_one_or_none()
        if row is None:
            exists = (await self._s.execute(
                select(func.count()).select_from(ProcessORM).where(
                    ProcessORM.code == p.code, ProcessORM.tenant_id == p.tenant_id, ProcessORM.is_deleted.is_(False)
                )
            )).scalar_one()
            if exists: raise ProcessCodeExists(p.code)
            row = ProcessORM(
                id=p.id, tenant_id=p.tenant_id, code=p.code, name=p.name,
                process_type=p.process_type.value, cycle_time_seconds=p.cycle_time_seconds,
                setup_time_seconds=p.setup_time_seconds, machine_type=p.machine_rate.machine_type,
                machine_rate_eur=p.machine_rate.rate_per_hour_eur, machine_location=p.machine_rate.location,
                skill_level=p.labor_rate.skill_level, labor_rate_eur=p.labor_rate.rate_per_hour_eur,
                labor_location=p.labor_rate.location, scrap_rate=p.scrap_rate, status=p.status.value,
                description=p.description,
            )
            self._s.add(row)
        else:
            row.name=p.name; row.status=p.status.value; row.scrap_rate=p.scrap_rate
            row.cycle_time_seconds=p.cycle_time_seconds; row.setup_time_seconds=p.setup_time_seconds
            row.machine_rate_eur=p.machine_rate.rate_per_hour_eur; row.labor_rate_eur=p.labor_rate.rate_per_hour_eur
        await self._s.flush()
        return p

    async def list(self, tenant_id: str, process_type: ProcessType | None, offset: int, limit: int) -> tuple[list[ManufacturingProcess], int]:
        base = select(ProcessORM).where(ProcessORM.tenant_id == tenant_id, ProcessORM.is_deleted.is_(False))
        if process_type: base = base.where(ProcessORM.process_type == process_type.value)
        total = (await self._s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(ProcessORM.code).offset(offset).limit(limit))).scalars().all()
        return [self._map(r) for r in rows], total

    @staticmethod
    def _map(r: ProcessORM) -> ManufacturingProcess:
        return ManufacturingProcess(
            process_id=r.id, code=r.code, name=r.name, process_type=ProcessType(r.process_type),
            cycle_time_seconds=r.cycle_time_seconds, setup_time_seconds=r.setup_time_seconds,
            machine_rate=MachineRate(r.machine_type, r.machine_rate_eur, r.machine_location),
            labor_rate=LaborRate(r.skill_level, r.labor_rate_eur, r.labor_location),
            status=ProcessStatus(r.status), tenant_id=r.tenant_id,
            scrap_rate=r.scrap_rate, description=r.description,
        )
