"""
ICI Autonomous Negotiation Agent

Architecture overview — see individual modules:
  models.py            § ORM + Pydantic schemas
  supplier_behavior.py § Behavioral fingerprinting + acceptance probability
  historical_model.py  § ZOPA estimation + similar-case retrieval
  price_elasticity.py  § Elasticity estimation + Monte Carlo price bands
  counter_proposal.py  § Offer generation + concession schedule + framing
  strategy.py          § Strategy selection + mid-session adaptation
  multi_agent.py       § Coordinator + Research + Analyst + Negotiation agents
  human_approval.py    § HITL workflow + approval lifecycle
  risk_controls.py     § Hard gates (price floor, sanctions, order value)
  monitoring.py        § Prometheus metrics + KPI summary
  events.py            § Domain events + Redis stream publisher
  api.py               § REST API + WebSocket stream
"""

from .api import router as negotiation_router
from .models import (
    NegotiationStrategy,
    SessionState,
    RoundOutcome,
    RiskLevel,
    ApprovalStatus,
    SupplierTrait,
)
from .multi_agent import NegotiationCoordinator, CoordinatorState
from .human_approval import HumanApprovalManager
from .events import NegotiationEventPublisher
from .risk_controls import RiskControlEngine

__all__ = [
    "negotiation_router",
    "NegotiationStrategy",
    "SessionState",
    "RoundOutcome",
    "RiskLevel",
    "ApprovalStatus",
    "SupplierTrait",
    "NegotiationCoordinator",
    "CoordinatorState",
    "HumanApprovalManager",
    "NegotiationEventPublisher",
    "RiskControlEngine",
]
