from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class RFQStatus(StrEnum):
    DRAFT     = "DRAFT"
    SENT      = "SENT"
    RECEIVED  = "RECEIVED"
    EVALUATED = "EVALUATED"
    AWARDED   = "AWARDED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RFQLineItem:
    line_id: UUID
    material_id: UUID
    description: str
    quantity: Decimal
    unit_of_measure: str
    target_price_eur: Decimal | None = None
    notes: str = ""


@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class RFQSent(DomainEvent):
    rfq_id: UUID = field(default_factory=uuid4)
    supplier_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RFQAwarded(DomainEvent):
    rfq_id: UUID = field(default_factory=uuid4)
    winning_supplier_id: UUID = field(default_factory=uuid4)


class RFQ:
    def __init__(
        self,
        rfq_id: UUID,
        rfq_number: str,
        title: str,
        line_items: list[RFQLineItem],
        supplier_ids: list[UUID],
        due_date: date,
        status: RFQStatus,
        tenant_id: str,
        buyer_id: UUID,
        notes: str = "",
        winning_supplier_id: UUID | None = None,
    ) -> None:
        self._id = rfq_id
        self._rfq_number = rfq_number
        self._title = title
        self._line_items = list(line_items)
        self._supplier_ids = list(supplier_ids)
        self._due_date = due_date
        self._status = status
        self._tenant_id = tenant_id
        self._buyer_id = buyer_id
        self._notes = notes
        self._winning_supplier_id = winning_supplier_id
        self._events: list[DomainEvent] = []

    @classmethod
    def create(
        cls,
        rfq_number: str,
        title: str,
        line_items: list[RFQLineItem],
        supplier_ids: list[UUID],
        due_date: date,
        tenant_id: str,
        buyer_id: UUID,
        notes: str = "",
    ) -> "RFQ":
        r = cls(
            rfq_id=uuid4(), rfq_number=rfq_number, title=title,
            line_items=line_items, supplier_ids=supplier_ids, due_date=due_date,
            status=RFQStatus.DRAFT, tenant_id=tenant_id, buyer_id=buyer_id, notes=notes,
        )
        return r

    @property
    def id(self) -> UUID: return self._id
    @property
    def rfq_number(self) -> str: return self._rfq_number
    @property
    def title(self) -> str: return self._title
    @property
    def line_items(self) -> list[RFQLineItem]: return list(self._line_items)
    @property
    def supplier_ids(self) -> list[UUID]: return list(self._supplier_ids)
    @property
    def due_date(self) -> date: return self._due_date
    @property
    def status(self) -> RFQStatus: return self._status
    @property
    def tenant_id(self) -> str: return self._tenant_id
    @property
    def buyer_id(self) -> UUID: return self._buyer_id
    @property
    def notes(self) -> str: return self._notes
    @property
    def winning_supplier_id(self) -> UUID | None: return self._winning_supplier_id

    def send(self) -> None:
        if self._status != RFQStatus.DRAFT:
            raise ValueError("Only DRAFT RFQs can be sent")
        if not self._supplier_ids:
            raise ValueError("No suppliers assigned to RFQ")
        if not self._line_items:
            raise ValueError("No line items in RFQ")
        self._status = RFQStatus.SENT
        self._events.append(RFQSent(
            rfq_id=self._id,
            supplier_ids=tuple(str(s) for s in self._supplier_ids),
        ))

    def mark_received(self) -> None:
        if self._status != RFQStatus.SENT:
            raise ValueError("RFQ must be SENT to mark as RECEIVED")
        self._status = RFQStatus.RECEIVED

    def award(self, supplier_id: UUID) -> None:
        if self._status not in (RFQStatus.RECEIVED, RFQStatus.EVALUATED):
            raise ValueError("Can only award RECEIVED or EVALUATED RFQ")
        if supplier_id not in self._supplier_ids:
            raise ValueError("Supplier not part of this RFQ")
        self._status = RFQStatus.AWARDED
        self._winning_supplier_id = supplier_id
        self._events.append(RFQAwarded(rfq_id=self._id, winning_supplier_id=supplier_id))

    def cancel(self) -> None:
        if self._status == RFQStatus.AWARDED:
            raise ValueError("Cannot cancel awarded RFQ")
        self._status = RFQStatus.CANCELLED

    def pop_events(self) -> list[DomainEvent]:
        ev, self._events = list(self._events), []
        return ev
