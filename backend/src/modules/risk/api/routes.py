from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from ....database import get_db
from ....dependencies import PaginationDep
from ....middleware.auth import CurrentUser, get_current_user
from ..application.services import AddMitigationCommand, CreateRiskCommand, RiskService
from ..domain.models import RiskCategory, RiskSeverity, RiskStatus
from ..infrastructure.repository import SqlAlchemyRiskRepository
from .schemas import (
    AcceptRiskRequest, AddMitigationRequest, CreateRiskRequest,
    PortfolioResponse, RiskListResponse, RiskResponse, UpdateScoreRequest,
)

router = APIRouter()

def get_service(session: AsyncSession = Depends(get_db)) -> RiskService:
    return RiskService(SqlAlchemyRiskRepository(session))

@router.post("", status_code=status.HTTP_201_CREATED, response_model=RiskResponse)
async def create(body: CreateRiskRequest, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    r = await svc.create(CreateRiskCommand(tenant_id=user.tenant_id, category=body.category, title=body.title, description=body.description, probability=body.probability, impact=body.impact, detectability=body.detectability, estimated_cost_eur=body.estimated_cost_eur, cost_variance_pct=body.cost_variance_pct, impact_area=body.impact_area, affected_material_id=body.affected_material_id, affected_supplier_id=body.affected_supplier_id, affected_rfq_id=body.affected_rfq_id, notes=body.notes))
    return RiskResponse.from_domain(r)

@router.get("", response_model=RiskListResponse)
async def list_risks(
    category: RiskCategory | None = Query(default=None),
    status: RiskStatus | None = Query(default=None),
    severity: RiskSeverity | None = Query(default=None),
    *,
    pg: PaginationDep, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskListResponse:
    items, total = await svc.list(user.tenant_id, category, status, severity, pg.offset, pg.limit)
    return RiskListResponse(total=total, page=pg.page, page_size=pg.page_size, items=[RiskResponse.from_domain(r) for r in items])

@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> PortfolioResponse:
    return PortfolioResponse.from_domain(await svc.get_portfolio(user.tenant_id))

@router.get("/{risk_id}", response_model=RiskResponse)
async def get(risk_id: UUID, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.get(risk_id, user.tenant_id))

@router.post("/{risk_id}/acknowledge", response_model=RiskResponse)
async def acknowledge(risk_id: UUID, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.acknowledge(risk_id, user.tenant_id))

@router.post("/{risk_id}/mitigate", response_model=RiskResponse)
async def start_mitigating(risk_id: UUID, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.start_mitigating(risk_id, user.tenant_id))

@router.post("/{risk_id}/resolve", response_model=RiskResponse)
async def resolve(risk_id: UUID, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.resolve(risk_id, user.tenant_id))

@router.post("/{risk_id}/accept", response_model=RiskResponse)
async def accept(risk_id: UUID, body: AcceptRiskRequest, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.accept(risk_id, user.tenant_id, body.reason))

@router.patch("/{risk_id}/score", response_model=RiskResponse)
async def update_score(risk_id: UUID, body: UpdateScoreRequest, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.update_score(risk_id, user.tenant_id, body.probability, body.impact, body.detectability))

@router.post("/{risk_id}/mitigations", response_model=RiskResponse, status_code=status.HTTP_201_CREATED)
async def add_mitigation(risk_id: UUID, body: AddMitigationRequest, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.add_mitigation(AddMitigationCommand(risk_id=risk_id, tenant_id=user.tenant_id, description=body.description, owner=body.owner, due_date=body.due_date)))

@router.post("/{risk_id}/mitigations/{action_id}/complete", response_model=RiskResponse)
async def complete_mitigation(risk_id: UUID, action_id: UUID, svc: RiskService = Depends(get_service), user: CurrentUser = Depends(get_current_user)) -> RiskResponse:
    return RiskResponse.from_domain(await svc.complete_mitigation(risk_id, action_id, user.tenant_id))
