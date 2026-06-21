from __future__ import annotations
from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from ..domain.models import RFQStatus


class LineItemRequest(BaseModel):
    material_id: UUID; description: str = Field(..., min_length=1, max_length=512)
    quantity: Decimal = Field(..., gt=0); unit_of_measure: str = Field(..., max_length=8)
    target_price_eur: Decimal | None = None; notes: str = ""

class CreateRFQRequest(BaseModel):
    rfq_number: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=256)
    supplier_ids: list[UUID] = Field(..., min_length=1)
    due_date: date
    line_items: list[LineItemRequest] = Field(..., min_length=1)
    notes: str = ""

class AwardRequest(BaseModel):
    supplier_id: UUID

class LineItemResponse(BaseModel):
    line_id: UUID; material_id: UUID; description: str
    quantity: Decimal; unit_of_measure: str; target_price_eur: Decimal | None; notes: str

class RFQResponse(BaseModel):
    id: UUID; rfq_number: str; title: str; status: RFQStatus
    due_date: date; buyer_id: UUID; supplier_ids: list[UUID]
    winning_supplier_id: UUID | None; notes: str; tenant_id: str
    line_items: list[LineItemResponse]

    @classmethod
    def from_domain(cls, r) -> "RFQResponse":
        return cls(
            id=r.id, rfq_number=r.rfq_number, title=r.title, status=r.status,
            due_date=r.due_date, buyer_id=r.buyer_id, supplier_ids=r.supplier_ids,
            winning_supplier_id=r.winning_supplier_id, notes=r.notes, tenant_id=r.tenant_id,
            line_items=[LineItemResponse(line_id=li.line_id, material_id=li.material_id, description=li.description, quantity=li.quantity, unit_of_measure=li.unit_of_measure, target_price_eur=li.target_price_eur, notes=li.notes) for li in r.line_items],
        )

class RFQListResponse(BaseModel):
    total: int; page: int; page_size: int; items: list[RFQResponse]
