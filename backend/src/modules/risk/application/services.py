from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from ..domain.exceptions import MitigationActionNotFound
from ..domain.models import FinancialImpact, ImpactArea, RiskCategory, RiskItem, RiskPortfolio, RiskSeverity, RiskStatus
from ..infrastructure.repository import AbstractRiskRepository


@dataclass
class CreateRiskCommand:
    tenant_id: str
    category: RiskCategory
    title: str
    description: str
    probability: Decimal
    impact: Decimal
    detectability: Decimal
    estimated_cost_eur: Decimal | None = None
    cost_variance_pct: Decimal | None = None
    impact_area: ImpactArea | None = None
    affected_material_id: UUID | None = None
    affected_supplier_id: UUID | None = None
    affected_rfq_id: UUID | None = None
    notes: str = ""


@dataclass
class AddMitigationCommand:
    risk_id: UUID
    tenant_id: str
    description: str
    owner: str
    due_date: datetime | None = None


class RiskService:
    def __init__(self, repo: AbstractRiskRepository) -> None: self._repo = repo

    async def create(self, cmd: CreateRiskCommand) -> RiskItem:
        fi = FinancialImpact(cmd.estimated_cost_eur, cmd.cost_variance_pct, cmd.impact_area) if cmd.estimated_cost_eur is not None else None
        r = RiskItem.create(tenant_id=cmd.tenant_id, category=cmd.category, title=cmd.title, description=cmd.description, probability=cmd.probability, impact=cmd.impact, detectability=cmd.detectability, financial_impact=fi, affected_material_id=cmd.affected_material_id, affected_supplier_id=cmd.affected_supplier_id, affected_rfq_id=cmd.affected_rfq_id, notes=cmd.notes)
        return await self._repo.save(r)

    async def get(self, rid: UUID, tenant_id: str) -> RiskItem:
        return await self._repo.get_by_id(rid, tenant_id)

    async def list(self, tenant_id: str, category: RiskCategory | None = None, status: RiskStatus | None = None, severity: RiskSeverity | None = None, offset: int = 0, limit: int = 20) -> tuple[list[RiskItem], int]:
        return await self._repo.list(tenant_id, category, status, severity, offset, limit)

    async def get_portfolio(self, tenant_id: str) -> RiskPortfolio:
        return await self._repo.get_portfolio(tenant_id)

    async def acknowledge(self, rid: UUID, tenant_id: str) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id); r.acknowledge(); return await self._repo.save(r)

    async def start_mitigating(self, rid: UUID, tenant_id: str) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id); r.start_mitigating(); return await self._repo.save(r)

    async def resolve(self, rid: UUID, tenant_id: str) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id); r.resolve(); return await self._repo.save(r)

    async def accept(self, rid: UUID, tenant_id: str, reason: str) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id); r.accept(reason); return await self._repo.save(r)

    async def update_score(self, rid: UUID, tenant_id: str, probability: Decimal, impact: Decimal, detectability: Decimal) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id); r.update_score(probability, impact, detectability); return await self._repo.save(r)

    async def add_mitigation(self, cmd: AddMitigationCommand) -> RiskItem:
        r = await self._repo.get_by_id(cmd.risk_id, cmd.tenant_id); r.add_mitigation(cmd.description, cmd.owner, cmd.due_date); return await self._repo.save(r)

    async def complete_mitigation(self, rid: UUID, action_id: UUID, tenant_id: str) -> RiskItem:
        r = await self._repo.get_by_id(rid, tenant_id)
        action = next((a for a in r.mitigation_actions if a.action_id == action_id), None)
        if not action: raise MitigationActionNotFound(action_id)
        action.complete()
        return await self._repo.save(r)
