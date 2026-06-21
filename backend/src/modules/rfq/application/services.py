from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4
from ..domain.models import RFQ, RFQLineItem, RFQStatus
from ..infrastructure.repository import AbstractRFQRepository


@dataclass
class LineItemInput:
    material_id: UUID; description: str; quantity: Decimal; unit_of_measure: str
    target_price_eur: Decimal | None = None; notes: str = ""


@dataclass
class CreateRFQCommand:
    rfq_number: str; title: str; supplier_ids: list[UUID]
    due_date: date; tenant_id: str; buyer_id: UUID
    line_items: list[LineItemInput]; notes: str = ""


class RFQService:
    def __init__(self, repo: AbstractRFQRepository) -> None: self._repo = repo

    async def create(self, cmd: CreateRFQCommand) -> RFQ:
        rfq = RFQ.create(
            rfq_number=cmd.rfq_number, title=cmd.title,
            line_items=[RFQLineItem(uuid4(), li.material_id, li.description, li.quantity, li.unit_of_measure, li.target_price_eur, li.notes) for li in cmd.line_items],
            supplier_ids=cmd.supplier_ids, due_date=cmd.due_date,
            tenant_id=cmd.tenant_id, buyer_id=cmd.buyer_id, notes=cmd.notes,
        )
        return await self._repo.save(rfq)

    async def get(self, rfq_id: UUID, tenant_id: str) -> RFQ: return await self._repo.get_by_id(rfq_id, tenant_id)
    async def list(self, tenant_id: str, status: RFQStatus | None = None, offset: int = 0, limit: int = 20) -> tuple[list[RFQ], int]:
        return await self._repo.list(tenant_id, status, offset, limit)

    async def send(self, rfq_id: UUID, tenant_id: str) -> RFQ:
        rfq = await self._repo.get_by_id(rfq_id, tenant_id); rfq.send(); return await self._repo.save(rfq)

    async def award(self, rfq_id: UUID, tenant_id: str, supplier_id: UUID) -> RFQ:
        rfq = await self._repo.get_by_id(rfq_id, tenant_id); rfq.award(supplier_id); return await self._repo.save(rfq)

    async def cancel(self, rfq_id: UUID, tenant_id: str) -> RFQ:
        rfq = await self._repo.get_by_id(rfq_id, tenant_id); rfq.cancel(); return await self._repo.save(rfq)
