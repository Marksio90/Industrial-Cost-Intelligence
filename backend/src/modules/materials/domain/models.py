from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


# ── Value Objects ─────────────────────────────────────────────────────────────

class MaterialClass(StrEnum):
    STEEL       = "STEEL"
    ALUMINUM    = "ALUMINUM"
    COPPER      = "COPPER"
    PLASTIC     = "PLASTIC"
    COMPOSITE   = "COMPOSITE"
    RUBBER      = "RUBBER"
    GLASS       = "GLASS"
    CERAMIC     = "CERAMIC"
    OTHER       = "OTHER"


class MaterialStatus(StrEnum):
    ACTIVE      = "ACTIVE"
    DEPRECATED  = "DEPRECATED"
    OBSOLETE    = "OBSOLETE"
    PROTOTYPE   = "PROTOTYPE"


class UnitOfMeasure(StrEnum):
    KG   = "KG"
    G    = "G"
    T    = "T"
    M    = "M"
    MM   = "MM"
    M2   = "M2"
    M3   = "M3"
    PCS  = "PCS"
    L    = "L"


@dataclass(frozen=True)
class MaterialNumber:
    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 64:
            raise ValueError("MaterialNumber must be 1–64 characters")
        if not self.value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("MaterialNumber must be alphanumeric with - and _ allowed")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Density:
    value: Decimal   # g/cm³
    unit: str = "g/cm³"

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError("Density must be positive")


@dataclass(frozen=True)
class PricePerUnit:
    amount: Decimal
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Price cannot be negative")
        if len(self.currency) != 3:
            raise ValueError("Currency must be ISO 4217 (3 chars)")

    def __add__(self, other: "PricePerUnit") -> "PricePerUnit":
        if self.currency != other.currency:
            raise ValueError("Cannot add prices in different currencies")
        return PricePerUnit(self.amount + other.amount, self.currency)

    def multiply(self, factor: Decimal) -> "PricePerUnit":
        return PricePerUnit(self.amount * factor, self.currency)


# ── Domain Events ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class MaterialCreated(DomainEvent):
    material_id: UUID = field(default_factory=uuid4)
    material_number: str = ""


@dataclass(frozen=True)
class MaterialPriceUpdated(DomainEvent):
    material_id: UUID = field(default_factory=uuid4)
    old_price_eur: Decimal = Decimal(0)
    new_price_eur: Decimal = Decimal(0)


@dataclass(frozen=True)
class MaterialDeprecated(DomainEvent):
    material_id: UUID = field(default_factory=uuid4)
    reason: str = ""


# ── Aggregate Root ────────────────────────────────────────────────────────────

class Material:
    """Aggregate root for the Material bounded context."""

    def __init__(
        self,
        material_id: UUID,
        material_number: MaterialNumber,
        name: str,
        material_class: MaterialClass,
        unit_of_measure: UnitOfMeasure,
        base_price: PricePerUnit,
        status: MaterialStatus = MaterialStatus.ACTIVE,
        description: str = "",
        alloy: str | None = None,
        density: Density | None = None,
        min_order_qty: Decimal = Decimal("1"),
        lead_time_days: int = 0,
        tenant_id: str = "default",
    ) -> None:
        self._id = material_id
        self._material_number = material_number
        self._name = name
        self._material_class = material_class
        self._unit_of_measure = unit_of_measure
        self._base_price = base_price
        self._status = status
        self._description = description
        self._alloy = alloy
        self._density = density
        self._min_order_qty = min_order_qty
        self._lead_time_days = lead_time_days
        self._tenant_id = tenant_id
        self._events: list[DomainEvent] = []

        self._validate()

    def _validate(self) -> None:
        from ..domain.exceptions import MaterialInvariantViolation
        if not self._name or len(self._name) > 256:
            raise MaterialInvariantViolation("Name must be 1–256 characters")
        if self._lead_time_days < 0:
            raise MaterialInvariantViolation("Lead time cannot be negative")
        if self._min_order_qty <= 0:
            raise MaterialInvariantViolation("Minimum order quantity must be positive")

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        material_number: str,
        name: str,
        material_class: MaterialClass,
        unit_of_measure: UnitOfMeasure,
        base_price_eur: Decimal,
        description: str = "",
        alloy: str | None = None,
        density_g_cm3: Decimal | None = None,
        min_order_qty: Decimal = Decimal("1"),
        lead_time_days: int = 0,
        tenant_id: str = "default",
    ) -> "Material":
        mat = cls(
            material_id=uuid4(),
            material_number=MaterialNumber(material_number),
            name=name,
            material_class=material_class,
            unit_of_measure=unit_of_measure,
            base_price=PricePerUnit(base_price_eur),
            description=description,
            alloy=alloy,
            density=Density(density_g_cm3) if density_g_cm3 else None,
            min_order_qty=min_order_qty,
            lead_time_days=lead_time_days,
            tenant_id=tenant_id,
        )
        mat._events.append(
            MaterialCreated(material_id=mat._id, material_number=material_number)
        )
        return mat

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def id(self) -> UUID:
        return self._id

    @property
    def material_number(self) -> MaterialNumber:
        return self._material_number

    @property
    def name(self) -> str:
        return self._name

    @property
    def material_class(self) -> MaterialClass:
        return self._material_class

    @property
    def unit_of_measure(self) -> UnitOfMeasure:
        return self._unit_of_measure

    @property
    def base_price(self) -> PricePerUnit:
        return self._base_price

    @property
    def base_price_eur(self) -> Decimal:
        return self._base_price.amount

    @property
    def status(self) -> MaterialStatus:
        return self._status

    @property
    def description(self) -> str:
        return self._description

    @property
    def alloy(self) -> str | None:
        return self._alloy

    @property
    def density(self) -> Density | None:
        return self._density

    @property
    def min_order_qty(self) -> Decimal:
        return self._min_order_qty

    @property
    def lead_time_days(self) -> int:
        return self._lead_time_days

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def is_active(self) -> bool:
        return self._status == MaterialStatus.ACTIVE

    # ── Commands ──────────────────────────────────────────────────────────────

    def update_price(self, new_price_eur: Decimal) -> None:
        from ..domain.exceptions import MaterialBusinessRuleViolation
        if not self.is_active:
            raise MaterialBusinessRuleViolation(
                "Cannot update price of non-active material"
            )
        if new_price_eur < 0:
            raise MaterialBusinessRuleViolation("Price cannot be negative")

        old_amount = self._base_price.amount
        self._base_price = PricePerUnit(new_price_eur)
        self._events.append(
            MaterialPriceUpdated(
                material_id=self._id,
                old_price_eur=old_amount,
                new_price_eur=new_price_eur,
            )
        )

    def update_details(
        self,
        name: str | None = None,
        description: str | None = None,
        lead_time_days: int | None = None,
        min_order_qty: Decimal | None = None,
    ) -> None:
        if name is not None:
            self._name = name
        if description is not None:
            self._description = description
        if lead_time_days is not None:
            self._lead_time_days = lead_time_days
        if min_order_qty is not None:
            self._min_order_qty = min_order_qty
        self._validate()

    def deprecate(self, reason: str) -> None:
        from ..domain.exceptions import MaterialBusinessRuleViolation
        if self._status == MaterialStatus.OBSOLETE:
            raise MaterialBusinessRuleViolation("Cannot deprecate an obsolete material")
        self._status = MaterialStatus.DEPRECATED
        self._events.append(MaterialDeprecated(material_id=self._id, reason=reason))

    def reactivate(self) -> None:
        from ..domain.exceptions import MaterialBusinessRuleViolation
        if self._status == MaterialStatus.OBSOLETE:
            raise MaterialBusinessRuleViolation("Cannot reactivate an obsolete material")
        self._status = MaterialStatus.ACTIVE

    def calculate_cost(self, quantity: Decimal, weight_kg: Decimal | None = None) -> Decimal:
        """Calculate total cost for a given quantity."""
        if quantity < self._min_order_qty:
            from ..domain.exceptions import MaterialBusinessRuleViolation
            raise MaterialBusinessRuleViolation(
                f"Quantity {quantity} below minimum order quantity {self._min_order_qty}"
            )
        if self._unit_of_measure == UnitOfMeasure.KG and weight_kg is not None:
            return self._base_price.amount * weight_kg
        return self._base_price.amount * quantity

    def pop_events(self) -> list[DomainEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Material):
            return NotImplemented
        return self._id == other._id

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Material(id={self._id}, number={self._material_number}, name={self._name!r})"
