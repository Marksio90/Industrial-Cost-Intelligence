from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class RiskCategory(StrEnum):
    SUPPLY = "SUPPLY"
    PRICE = "PRICE"
    MATERIAL = "MATERIAL"
    PROCESS = "PROCESS"
    GEOPOLITICAL = "GEOPOLITICAL"
    FINANCIAL = "FINANCIAL"
    QUALITY = "QUALITY"
    COMPLIANCE = "COMPLIANCE"


class RiskSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"
    ACCEPTED = "ACCEPTED"


class ImpactArea(StrEnum):
    COST = "COST"
    DELIVERY = "DELIVERY"
    QUALITY = "QUALITY"
    COMPLIANCE = "COMPLIANCE"


@dataclass(frozen=True)
class RiskScore:
    probability: Decimal  # 0.0 - 1.0
    impact: Decimal       # 0.0 - 1.0
    detectability: Decimal  # 0.0 - 1.0, lower = harder to detect

    def __post_init__(self) -> None:
        for attr in ("probability", "impact", "detectability"):
            v = getattr(self, attr)
            if not Decimal("0") <= v <= Decimal("1"):
                raise ValueError(f"{attr} must be between 0 and 1")

    @property
    def rpn(self) -> Decimal:
        """Risk Priority Number = probability × impact × (1 - detectability)"""
        return (self.probability * self.impact * (Decimal("1") - self.detectability)).quantize(Decimal("0.0001"))

    @property
    def severity(self) -> RiskSeverity:
        rpn = self.rpn
        if rpn >= Decimal("0.5"): return RiskSeverity.CRITICAL
        if rpn >= Decimal("0.3"): return RiskSeverity.HIGH
        if rpn >= Decimal("0.1"): return RiskSeverity.MEDIUM
        return RiskSeverity.LOW


@dataclass(frozen=True)
class FinancialImpact:
    estimated_cost_eur: Decimal
    cost_variance_pct: Decimal  # how much cost could vary
    impact_area: ImpactArea

    @property
    def worst_case_eur(self) -> Decimal:
        return self.estimated_cost_eur * (Decimal("1") + self.cost_variance_pct / Decimal("100"))


@dataclass
class MitigationAction:
    action_id: UUID
    description: str
    owner: str
    due_date: datetime | None
    completed_at: datetime | None = None

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    def complete(self) -> None:
        self.completed_at = datetime.utcnow()


@dataclass
class RiskItem:
    risk_id: UUID
    tenant_id: str
    category: RiskCategory
    title: str
    description: str
    score: RiskScore
    financial_impact: FinancialImpact | None
    status: RiskStatus
    affected_material_id: UUID | None
    affected_supplier_id: UUID | None
    affected_rfq_id: UUID | None
    mitigation_actions: list[MitigationAction]
    notes: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    _events: list = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        tenant_id: str,
        category: RiskCategory,
        title: str,
        description: str,
        probability: Decimal,
        impact: Decimal,
        detectability: Decimal,
        financial_impact: FinancialImpact | None = None,
        affected_material_id: UUID | None = None,
        affected_supplier_id: UUID | None = None,
        affected_rfq_id: UUID | None = None,
        notes: str = "",
    ) -> "RiskItem":
        score = RiskScore(probability, impact, detectability)
        now = datetime.utcnow()
        r = cls(
            risk_id=uuid4(), tenant_id=tenant_id, category=category, title=title,
            description=description, score=score, financial_impact=financial_impact,
            status=RiskStatus.OPEN, affected_material_id=affected_material_id,
            affected_supplier_id=affected_supplier_id, affected_rfq_id=affected_rfq_id,
            mitigation_actions=[], notes=notes, created_at=now, updated_at=now, resolved_at=None,
        )
        r._events.append(RiskIdentified(risk_id=r.risk_id, category=category, severity=score.severity, tenant_id=tenant_id))
        return r

    def acknowledge(self) -> None:
        if self.status != RiskStatus.OPEN:
            raise ValueError(f"Cannot acknowledge risk in status {self.status}")
        self.status = RiskStatus.ACKNOWLEDGED
        self.updated_at = datetime.utcnow()

    def start_mitigating(self) -> None:
        if self.status not in (RiskStatus.OPEN, RiskStatus.ACKNOWLEDGED):
            raise ValueError(f"Cannot start mitigation in status {self.status}")
        self.status = RiskStatus.MITIGATING
        self.updated_at = datetime.utcnow()

    def resolve(self) -> None:
        self.status = RiskStatus.RESOLVED
        now = datetime.utcnow()
        self.updated_at = now
        self.resolved_at = now
        self._events.append(RiskResolved(risk_id=self.risk_id, tenant_id=self.tenant_id))

    def accept(self, reason: str) -> None:
        self.status = RiskStatus.ACCEPTED
        self.notes = reason
        self.updated_at = datetime.utcnow()

    def update_score(self, probability: Decimal, impact: Decimal, detectability: Decimal) -> None:
        self.score = RiskScore(probability, impact, detectability)
        self.updated_at = datetime.utcnow()

    def add_mitigation(self, description: str, owner: str, due_date: datetime | None = None) -> MitigationAction:
        action = MitigationAction(action_id=uuid4(), description=description, owner=owner, due_date=due_date)
        self.mitigation_actions.append(action)
        self.updated_at = datetime.utcnow()
        return action

    def pop_events(self) -> list:
        evs, self._events = self._events, []
        return evs


@dataclass(frozen=True)
class RiskIdentified:
    risk_id: UUID
    category: RiskCategory
    severity: RiskSeverity
    tenant_id: str


@dataclass(frozen=True)
class RiskResolved:
    risk_id: UUID
    tenant_id: str


@dataclass
class RiskPortfolio:
    """Read-model aggregate for portfolio-level risk analysis."""
    tenant_id: str
    risks: list[RiskItem]

    @property
    def open_count(self) -> int:
        return sum(1 for r in self.risks if r.status == RiskStatus.OPEN)

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.risks if r.score.severity == RiskSeverity.CRITICAL)

    @property
    def total_financial_exposure_eur(self) -> Decimal:
        return sum((r.financial_impact.worst_case_eur for r in self.risks if r.financial_impact and r.status not in (RiskStatus.RESOLVED, RiskStatus.ACCEPTED)), Decimal("0"))

    @property
    def by_category(self) -> dict[RiskCategory, list[RiskItem]]:
        result: dict[RiskCategory, list[RiskItem]] = {}
        for r in self.risks:
            result.setdefault(r.category, []).append(r)
        return result

    @property
    def average_rpn(self) -> Decimal:
        if not self.risks: return Decimal("0")
        return (sum(r.score.rpn for r in self.risks) / len(self.risks)).quantize(Decimal("0.0001"))
