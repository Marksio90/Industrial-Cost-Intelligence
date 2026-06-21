from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class SupplierStatus(StrEnum):
    ACTIVE      = "ACTIVE"
    QUALIFIED   = "QUALIFIED"
    SUSPENDED   = "SUSPENDED"
    BLACKLISTED = "BLACKLISTED"
    PROSPECT    = "PROSPECT"


class SupplierTier(StrEnum):
    TIER1 = "TIER1"
    TIER2 = "TIER2"
    TIER3 = "TIER3"


class PaymentTerms(StrEnum):
    NET30  = "NET30"
    NET45  = "NET45"
    NET60  = "NET60"
    NET90  = "NET90"
    PREPAY = "PREPAY"


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    postal_code: str
    country_code: str   # ISO 3166-1 alpha-2

    def __post_init__(self) -> None:
        if len(self.country_code) != 2:
            raise ValueError("country_code must be 2-letter ISO 3166-1")


@dataclass(frozen=True)
class SupplierScore:
    quality_ppm: int         # defects per million
    delivery_reliability: Decimal  # 0.0–1.0
    price_competitiveness: Decimal # 0.0–1.0
    financial_score: Decimal       # Altman Z or equivalent 0.0–10.0

    @property
    def overall(self) -> Decimal:
        return (
            (Decimal(1) - self.quality_ppm / Decimal(1_000_000)) * Decimal("0.35")
            + self.delivery_reliability * Decimal("0.35")
            + self.price_competitiveness * Decimal("0.20")
            + self.financial_score / Decimal(10) * Decimal("0.10")
        )


@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class SupplierCreated(DomainEvent):
    supplier_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class SupplierSuspended(DomainEvent):
    supplier_id: UUID = field(default_factory=uuid4)
    reason: str = ""


class Supplier:
    def __init__(
        self,
        supplier_id: UUID,
        code: str,
        name: str,
        address: Address,
        tier: SupplierTier,
        status: SupplierStatus,
        payment_terms: PaymentTerms,
        currency: str,
        lead_time_days: int,
        tenant_id: str,
        contact_email: str = "",
        contact_phone: str = "",
        tax_id: str = "",
        score: SupplierScore | None = None,
    ) -> None:
        self._id = supplier_id
        self._code = code
        self._name = name
        self._address = address
        self._tier = tier
        self._status = status
        self._payment_terms = payment_terms
        self._currency = currency
        self._lead_time_days = lead_time_days
        self._tenant_id = tenant_id
        self._contact_email = contact_email
        self._contact_phone = contact_phone
        self._tax_id = tax_id
        self._score = score
        self._events: list[DomainEvent] = []

    @classmethod
    def create(
        cls,
        code: str,
        name: str,
        address: Address,
        tier: SupplierTier,
        payment_terms: PaymentTerms,
        currency: str,
        lead_time_days: int,
        tenant_id: str,
        contact_email: str = "",
        contact_phone: str = "",
        tax_id: str = "",
    ) -> "Supplier":
        s = cls(
            supplier_id=uuid4(),
            code=code,
            name=name,
            address=address,
            tier=tier,
            status=SupplierStatus.PROSPECT,
            payment_terms=payment_terms,
            currency=currency,
            lead_time_days=lead_time_days,
            tenant_id=tenant_id,
            contact_email=contact_email,
            contact_phone=contact_phone,
            tax_id=tax_id,
        )
        s._events.append(SupplierCreated(supplier_id=s._id))
        return s

    @property
    def id(self) -> UUID: return self._id
    @property
    def code(self) -> str: return self._code
    @property
    def name(self) -> str: return self._name
    @property
    def address(self) -> Address: return self._address
    @property
    def tier(self) -> SupplierTier: return self._tier
    @property
    def status(self) -> SupplierStatus: return self._status
    @property
    def payment_terms(self) -> PaymentTerms: return self._payment_terms
    @property
    def currency(self) -> str: return self._currency
    @property
    def lead_time_days(self) -> int: return self._lead_time_days
    @property
    def tenant_id(self) -> str: return self._tenant_id
    @property
    def contact_email(self) -> str: return self._contact_email
    @property
    def score(self) -> SupplierScore | None: return self._score
    @property
    def tax_id(self) -> str: return self._tax_id
    @property
    def contact_phone(self) -> str: return self._contact_phone
    @property
    def is_active(self) -> bool:
        return self._status in (SupplierStatus.ACTIVE, SupplierStatus.QUALIFIED)

    def qualify(self) -> None:
        self._status = SupplierStatus.QUALIFIED

    def suspend(self, reason: str) -> None:
        if self._status == SupplierStatus.BLACKLISTED:
            raise ValueError("Cannot suspend a blacklisted supplier")
        self._status = SupplierStatus.SUSPENDED
        self._events.append(SupplierSuspended(supplier_id=self._id, reason=reason))

    def blacklist(self) -> None:
        self._status = SupplierStatus.BLACKLISTED

    def reactivate(self) -> None:
        if self._status == SupplierStatus.BLACKLISTED:
            raise ValueError("Cannot reactivate a blacklisted supplier")
        self._status = SupplierStatus.ACTIVE

    def update_score(self, score: SupplierScore) -> None:
        self._score = score

    def pop_events(self) -> list[DomainEvent]:
        ev, self._events = list(self._events), []
        return ev
