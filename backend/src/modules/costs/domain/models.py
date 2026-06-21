from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class CostStatus(StrEnum):
    DRAFT    = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class CostCategory(StrEnum):
    MATERIAL  = "MATERIAL"
    LABOR     = "LABOR"
    MACHINE   = "MACHINE"
    ENERGY    = "ENERGY"
    TOOLING   = "TOOLING"
    OVERHEAD  = "OVERHEAD"
    LOGISTICS = "LOGISTICS"
    MARGIN    = "MARGIN"


@dataclass(frozen=True)
class CostComponent:
    category: CostCategory
    amount_eur: Decimal
    description: str = ""
    share_pct: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.amount_eur < 0:
            raise ValueError(f"Cost component {self.category} cannot be negative")


@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class CostBreakdownCreated(DomainEvent):
    breakdown_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class CostBreakdownApproved(DomainEvent):
    breakdown_id: UUID = field(default_factory=uuid4)
    total_cost_eur: Decimal = Decimal(0)


class CostBreakdown:
    """Aggregate: cost breakdown for a BOM item or product."""

    def __init__(
        self,
        breakdown_id: UUID,
        bom_item_id: UUID,
        reference_number: str,
        quantity: int,
        components: list[CostComponent],
        status: CostStatus,
        tenant_id: str,
        location: str = "DE",
        currency: str = "EUR",
        notes: str = "",
        version: int = 1,
    ) -> None:
        self._id = breakdown_id
        self._bom_item_id = bom_item_id
        self._reference_number = reference_number
        self._quantity = quantity
        self._components = list(components)
        self._status = status
        self._tenant_id = tenant_id
        self._location = location
        self._currency = currency
        self._notes = notes
        self._version = version
        self._events: list[DomainEvent] = []
        self._recalculate_shares()

    @classmethod
    def create(
        cls,
        bom_item_id: UUID,
        reference_number: str,
        quantity: int,
        components: list[CostComponent],
        tenant_id: str,
        location: str = "DE",
        currency: str = "EUR",
        notes: str = "",
    ) -> "CostBreakdown":
        cb = cls(
            breakdown_id=uuid4(),
            bom_item_id=bom_item_id,
            reference_number=reference_number,
            quantity=quantity,
            components=components,
            status=CostStatus.DRAFT,
            tenant_id=tenant_id,
            location=location,
            currency=currency,
            notes=notes,
        )
        cb._events.append(CostBreakdownCreated(breakdown_id=cb._id))
        return cb

    def _recalculate_shares(self) -> None:
        total = self.total_cost_eur
        if total == 0:
            return
        self._components = [
            CostComponent(
                category=c.category,
                amount_eur=c.amount_eur,
                description=c.description,
                share_pct=(c.amount_eur / total * 100).quantize(Decimal("0.01")),
            )
            for c in self._components
        ]

    @property
    def id(self) -> UUID: return self._id
    @property
    def bom_item_id(self) -> UUID: return self._bom_item_id
    @property
    def reference_number(self) -> str: return self._reference_number
    @property
    def quantity(self) -> int: return self._quantity
    @property
    def components(self) -> list[CostComponent]: return list(self._components)
    @property
    def status(self) -> CostStatus: return self._status
    @property
    def tenant_id(self) -> str: return self._tenant_id
    @property
    def location(self) -> str: return self._location
    @property
    def currency(self) -> str: return self._currency
    @property
    def notes(self) -> str: return self._notes
    @property
    def version(self) -> int: return self._version

    @property
    def total_cost_eur(self) -> Decimal:
        return sum((c.amount_eur for c in self._components), Decimal(0))

    @property
    def unit_cost_eur(self) -> Decimal:
        if self._quantity == 0:
            return Decimal(0)
        return (self.total_cost_eur / self._quantity).quantize(Decimal("0.0001"))

    def get_component(self, category: CostCategory) -> CostComponent | None:
        return next((c for c in self._components if c.category == category), None)

    def approve(self, approver: str) -> None:
        if self._status != CostStatus.DRAFT:
            raise ValueError("Only DRAFT breakdowns can be approved")
        self._status = CostStatus.APPROVED
        self._events.append(
            CostBreakdownApproved(breakdown_id=self._id, total_cost_eur=self.total_cost_eur)
        )

    def reject(self, reason: str) -> None:
        if self._status != CostStatus.DRAFT:
            raise ValueError("Only DRAFT breakdowns can be rejected")
        self._status = CostStatus.REJECTED

    def revise(self, new_components: list[CostComponent], notes: str = "") -> "CostBreakdown":
        """Create a new version (immutable revision)."""
        return CostBreakdown(
            breakdown_id=uuid4(),
            bom_item_id=self._bom_item_id,
            reference_number=self._reference_number,
            quantity=self._quantity,
            components=new_components,
            status=CostStatus.DRAFT,
            tenant_id=self._tenant_id,
            location=self._location,
            currency=self._currency,
            notes=notes,
            version=self._version + 1,
        )

    def pop_events(self) -> list[DomainEvent]:
        ev, self._events = list(self._events), []
        return ev
