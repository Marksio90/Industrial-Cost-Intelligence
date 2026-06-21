from __future__ import annotations
from abc import ABC, abstractmethod
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ..domain.exceptions import RiskNotFound
from ..domain.models import (
    FinancialImpact, ImpactArea, MitigationAction, RiskCategory, RiskItem,
    RiskPortfolio, RiskScore, RiskSeverity, RiskStatus,
)
from .orm import MitigationActionORM, RiskItemORM


class AbstractRiskRepository(ABC):
    @abstractmethod
    async def get_by_id(self, rid: UUID, tenant_id: str) -> RiskItem: ...
    @abstractmethod
    async def save(self, r: RiskItem) -> RiskItem: ...
    @abstractmethod
    async def list(self, tenant_id: str, category: RiskCategory | None, status: RiskStatus | None, severity: RiskSeverity | None, offset: int, limit: int) -> tuple[list[RiskItem], int]: ...
    @abstractmethod
    async def get_portfolio(self, tenant_id: str) -> RiskPortfolio: ...


class SqlAlchemyRiskRepository(AbstractRiskRepository):
    def __init__(self, session: AsyncSession) -> None: self._s = session

    def _q(self): return select(RiskItemORM).options(selectinload(RiskItemORM.mitigation_actions))

    async def get_by_id(self, rid: UUID, tenant_id: str) -> RiskItem:
        row = (await self._s.execute(self._q().where(RiskItemORM.id == rid, RiskItemORM.tenant_id == tenant_id, RiskItemORM.is_deleted.is_(False)))).scalar_one_or_none()
        if not row: raise RiskNotFound(rid)
        return self._map(row)

    async def save(self, r: RiskItem) -> RiskItem:
        row = (await self._s.execute(self._q().where(RiskItemORM.id == r.risk_id))).scalar_one_or_none()
        fi = r.financial_impact
        if row is None:
            row = RiskItemORM(
                id=r.risk_id, tenant_id=r.tenant_id, category=r.category.value, title=r.title,
                description=r.description, probability=r.score.probability, impact=r.score.impact,
                detectability=r.score.detectability, status=r.status.value,
                estimated_cost_eur=fi.estimated_cost_eur if fi else None,
                cost_variance_pct=fi.cost_variance_pct if fi else None,
                impact_area=fi.impact_area.value if fi else None,
                affected_material_id=r.affected_material_id, affected_supplier_id=r.affected_supplier_id,
                affected_rfq_id=r.affected_rfq_id, notes=r.notes, resolved_at=r.resolved_at,
            )
            self._s.add(row)
            await self._s.flush()
        else:
            row.status = r.status.value; row.notes = r.notes; row.resolved_at = r.resolved_at
            row.probability = r.score.probability; row.impact = r.score.impact; row.detectability = r.score.detectability
            if fi:
                row.estimated_cost_eur = fi.estimated_cost_eur; row.cost_variance_pct = fi.cost_variance_pct; row.impact_area = fi.impact_area.value
            for ma in row.mitigation_actions: await self._s.delete(ma)
            await self._s.flush()
        for ma in r.mitigation_actions:
            row.mitigation_actions.append(MitigationActionORM(id=ma.action_id, risk_id=r.risk_id, description=ma.description, owner=ma.owner, due_date=ma.due_date, completed_at=ma.completed_at))
        await self._s.flush()
        return r

    async def list(self, tenant_id: str, category: RiskCategory | None, status: RiskStatus | None, severity: RiskSeverity | None, offset: int, limit: int) -> tuple[list[RiskItem], int]:
        base = self._q().where(RiskItemORM.tenant_id == tenant_id, RiskItemORM.is_deleted.is_(False))
        if category: base = base.where(RiskItemORM.category == category.value)
        if status: base = base.where(RiskItemORM.status == status.value)
        total = (await self._s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (await self._s.execute(base.order_by(RiskItemORM.created_at.desc()).offset(offset).limit(limit))).scalars().all()
        items = [self._map(r) for r in rows]
        if severity:
            items = [i for i in items if i.score.severity == severity]
        return items, total

    async def get_portfolio(self, tenant_id: str) -> RiskPortfolio:
        rows = (await self._s.execute(self._q().where(RiskItemORM.tenant_id == tenant_id, RiskItemORM.is_deleted.is_(False), RiskItemORM.status.notin_(["RESOLVED", "ACCEPTED"])))).scalars().all()
        return RiskPortfolio(tenant_id=tenant_id, risks=[self._map(r) for r in rows])

    @staticmethod
    def _map(r: RiskItemORM) -> RiskItem:
        fi = FinancialImpact(r.estimated_cost_eur, r.cost_variance_pct, ImpactArea(r.impact_area)) if r.estimated_cost_eur is not None else None
        mas = [MitigationAction(action_id=ma.id, description=ma.description, owner=ma.owner, due_date=ma.due_date, completed_at=ma.completed_at) for ma in r.mitigation_actions]
        return RiskItem(risk_id=r.id, tenant_id=r.tenant_id, category=RiskCategory(r.category), title=r.title, description=r.description, score=RiskScore(r.probability, r.impact, r.detectability), financial_impact=fi, status=RiskStatus(r.status), affected_material_id=r.affected_material_id, affected_supplier_id=r.affected_supplier_id, affected_rfq_id=r.affected_rfq_id, mitigation_actions=mas, notes=r.notes, created_at=r.created_at, updated_at=r.updated_at, resolved_at=r.resolved_at)
