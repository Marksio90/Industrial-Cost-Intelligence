from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from ..domain.models import ImpactArea, RiskCategory, RiskSeverity, RiskStatus


class CreateRiskRequest(BaseModel):
    category: RiskCategory
    title: str = Field(..., min_length=1, max_length=256)
    description: str = ""
    probability: Decimal = Field(..., ge=0, le=1)
    impact: Decimal = Field(..., ge=0, le=1)
    detectability: Decimal = Field(..., ge=0, le=1)
    estimated_cost_eur: Decimal | None = Field(default=None, ge=0)
    cost_variance_pct: Decimal | None = Field(default=None, ge=0)
    impact_area: ImpactArea | None = None
    affected_material_id: UUID | None = None
    affected_supplier_id: UUID | None = None
    affected_rfq_id: UUID | None = None
    notes: str = ""


class UpdateScoreRequest(BaseModel):
    probability: Decimal = Field(..., ge=0, le=1)
    impact: Decimal = Field(..., ge=0, le=1)
    detectability: Decimal = Field(..., ge=0, le=1)


class AcceptRiskRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


class AddMitigationRequest(BaseModel):
    description: str = Field(..., min_length=1)
    owner: str = Field(..., min_length=1, max_length=128)
    due_date: datetime | None = None


class RiskScoreResponse(BaseModel):
    probability: Decimal; impact: Decimal; detectability: Decimal; rpn: Decimal; severity: RiskSeverity


class FinancialImpactResponse(BaseModel):
    estimated_cost_eur: Decimal; cost_variance_pct: Decimal; impact_area: ImpactArea; worst_case_eur: Decimal


class MitigationActionResponse(BaseModel):
    action_id: UUID; description: str; owner: str; due_date: datetime | None; completed_at: datetime | None; is_completed: bool


class RiskResponse(BaseModel):
    risk_id: UUID; tenant_id: str; category: RiskCategory; title: str; description: str
    score: RiskScoreResponse; financial_impact: FinancialImpactResponse | None; status: RiskStatus
    affected_material_id: UUID | None; affected_supplier_id: UUID | None; affected_rfq_id: UUID | None
    mitigation_actions: list[MitigationActionResponse]; notes: str
    created_at: datetime; updated_at: datetime; resolved_at: datetime | None

    @classmethod
    def from_domain(cls, r) -> "RiskResponse":
        return cls(
            risk_id=r.risk_id, tenant_id=r.tenant_id, category=r.category, title=r.title, description=r.description,
            score=RiskScoreResponse(probability=r.score.probability, impact=r.score.impact, detectability=r.score.detectability, rpn=r.score.rpn, severity=r.score.severity),
            financial_impact=FinancialImpactResponse(estimated_cost_eur=r.financial_impact.estimated_cost_eur, cost_variance_pct=r.financial_impact.cost_variance_pct, impact_area=r.financial_impact.impact_area, worst_case_eur=r.financial_impact.worst_case_eur) if r.financial_impact else None,
            status=r.status, affected_material_id=r.affected_material_id, affected_supplier_id=r.affected_supplier_id, affected_rfq_id=r.affected_rfq_id,
            mitigation_actions=[MitigationActionResponse(action_id=ma.action_id, description=ma.description, owner=ma.owner, due_date=ma.due_date, completed_at=ma.completed_at, is_completed=ma.is_completed) for ma in r.mitigation_actions],
            notes=r.notes, created_at=r.created_at, updated_at=r.updated_at, resolved_at=r.resolved_at,
        )


class RiskListResponse(BaseModel):
    total: int; page: int; page_size: int; items: list[RiskResponse]


class PortfolioResponse(BaseModel):
    tenant_id: str; open_count: int; critical_count: int
    total_financial_exposure_eur: Decimal; average_rpn: Decimal
    by_category: dict[str, int]

    @classmethod
    def from_domain(cls, p) -> "PortfolioResponse":
        return cls(
            tenant_id=p.tenant_id, open_count=p.open_count, critical_count=p.critical_count,
            total_financial_exposure_eur=p.total_financial_exposure_eur, average_rpn=p.average_rpn,
            by_category={k.value: len(v) for k, v in p.by_category.items()},
        )
