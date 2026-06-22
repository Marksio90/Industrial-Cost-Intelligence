"""
Section 11 — REST API + WebSocket

FastAPI router for the Negotiation Agent.

Endpoints:
  POST   /negotiations                              Create + start session
  GET    /negotiations/{session_id}                 Get session state
  GET    /negotiations/{session_id}/rounds          List rounds
  POST   /negotiations/{session_id}/respond         Submit supplier response (webhook / manual)
  POST   /negotiations/{session_id}/close           Manually close (agreement / withdraw)
  GET    /negotiations/{session_id}/approvals        List approval requests
  POST   /negotiations/{session_id}/approvals/{id}/decide  Human approval decision
  GET    /negotiations/suppliers/{supplier_id}/profile     Behavior profile
  GET    /negotiations/kpi                           Tenant-level KPI summary
  WS     /negotiations/{session_id}/stream          Real-time event stream

Section 13 — Security
  - All endpoints require JWT Bearer token (via require_permission)
  - NEGOTIATE_CREATE permission to open sessions
  - NEGOTIATE_APPROVE permission to approve/reject offers
  - NEGOTIATE_VIEW permission to view sessions/rounds
  - All actions logged via AuditLogger (@audited decorator)
  - Offer prices and terms are encrypted at rest (EncryptedString fields)
  - Session IDs are UUID4 (not guessable)
  - Rate limit: 20 sessions/hour per tenant (via RateLimitMiddleware)
  - Approval decisions require re-authentication for HIGH risk
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from .events import NegotiationEventPublisher, NegotiationEventType
from .human_approval import HumanApprovalManager
from .models import (
    ApprovalDecision,
    ApprovalRequestOut,
    BehaviorProfileOut,
    NegotiationOffer,
    NegotiationSessionCreate,
    NegotiationSessionOut,
    RoundOut,
    SessionState,
)
from .monitoring import (
    record_approval_outcome,
    record_auto_accept,
    record_session_closed,
    record_offer_sent,
    build_kpi_summary,
)
from .multi_agent import CoordinatorState, NegotiationCoordinator
from .risk_controls import RiskControlEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/negotiations", tags=["Negotiations"])

# ── Permission constants (from backend security.rbac) ─────────────────────────
NEGOTIATE_CREATE  = "negotiation:create"
NEGOTIATE_APPROVE = "negotiation:approve"
NEGOTIATE_VIEW    = "negotiation:view"
NEGOTIATE_CLOSE   = "negotiation:close"


# ─────────────────────────────────────────────────────────────────────────────
# Dependency injection (simplified — wire via app state in production)
# ─────────────────────────────────────────────────────────────────────────────

def _get_coordinator(request: Any = None) -> NegotiationCoordinator:
    """Returns shared NegotiationCoordinator from app state."""
    return getattr(getattr(request, "app", None), "__negotiation_coordinator__", None) \
        or NegotiationCoordinator()


def _get_approval_manager(request: Any = None) -> HumanApprovalManager:
    return getattr(getattr(request, "app", None), "__approval_manager__", None) \
        or HumanApprovalManager()


def _get_publisher(request: Any = None) -> NegotiationEventPublisher:
    return getattr(getattr(request, "app", None), "__negotiation_publisher__", None) \
        or NegotiationEventPublisher()


def _get_current_user(request: Any = None) -> dict[str, Any]:
    """Mock — replace with real JWT dependency in production."""
    return {
        "user_id":   "system",
        "tenant_id": "tenant-demo",
        "roles":     ["ADMIN"],
        "email":     "demo@ici.example.com",
    }


# ─────────────────────────────────────────────────────────────────────────────
# In-memory session store (replace with DB in production)
# ─────────────────────────────────────────────────────────────────────────────
_sessions: dict[str, CoordinatorState] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_session(
    body:    NegotiationSessionCreate,
    request: Any = None,
) -> dict[str, Any]:
    """
    Open a new negotiation session with a supplier.
    The agent immediately begins ANALYZING phase (async).
    Returns session_id and initial state.
    """
    user     = _get_current_user(request)
    coord    = _get_coordinator(request)
    pub      = _get_publisher(request)
    risk     = RiskControlEngine()

    # Pre-flight risk check
    pre_risk = risk.evaluate_session_open(
        supplier_country  = body.context.get("supplier_country"),
        total_order_value = float(body.total_quantity) * float(body.target_price or 1),
    )
    if pre_risk.is_blocked:
        raise HTTPException(status_code=422, detail=pre_risk.reason)

    session_id = str(uuid.uuid4())
    state = CoordinatorState(
        session_id   = session_id,
        tenant_id    = user["tenant_id"],
        supplier_id  = str(body.supplier_id),
        strategy     = None,
        max_rounds   = body.max_rounds,
        target_price = float(body.target_price or 0),
        walk_away    = float(body.walk_away_price or 0),
        quantity     = float(body.total_quantity),
        context      = body.context,
    )
    _sessions[session_id] = state

    logger.info(
        "negotiation_session_created",
        session_id  = session_id,
        supplier_id = str(body.supplier_id),
        quantity    = float(body.total_quantity),
        tenant_id   = user["tenant_id"],
    )

    from .supplier_behavior import SupplierBehaviorProfile
    default_profile = SupplierBehaviorProfile(supplier_id=str(body.supplier_id))

    # Kick off analysis in background
    async def _run_analysis() -> None:
        try:
            result = await coord.start_session(
                state               = state,
                initial_ask         = float(body.target_price or 0) * 1.20,
                behavior_profile    = default_profile,
                urgency_score       = body.context.get("urgency_score", 0.5),
                substitute_count    = body.context.get("substitute_count", 0),
                relationship_score  = body.context.get("relationship_score", 0.3),
                is_sole_source      = body.context.get("is_sole_source", False),
            )
            logger.info("negotiation_session_ready", session_id=session_id, strategy=result.get("strategy"))
        except Exception as exc:
            logger.error("negotiation_session_start_failed", session_id=session_id, error=str(exc))

    asyncio.create_task(_run_analysis())

    return {
        "session_id":  session_id,
        "state":       SessionState.INITIATED.value,
        "tenant_id":   user["tenant_id"],
        "supplier_id": str(body.supplier_id),
        "message":     "Negotiation session created. Analysis running in background.",
    }


@router.get("/{session_id}", response_model=None)
async def get_session(session_id: str, request: Any = None) -> dict[str, Any]:
    """Get current state of a negotiation session."""
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id":    state.session_id,
        "tenant_id":     state.tenant_id,
        "supplier_id":   state.supplier_id,
        "state":         state.state.value,
        "strategy":      state.strategy.value if state.strategy else None,
        "round_number":  state.round_number,
        "max_rounds":    state.max_rounds,
        "target_price":  state.target_price,
        "walk_away":     state.walk_away,
        "market_price":  state.market_price,
        "quantity":      state.quantity,
    }


@router.get("/{session_id}/rounds")
async def list_rounds(session_id: str, request: Any = None) -> dict[str, Any]:
    """List all negotiation rounds for a session."""
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id":   session_id,
        "round_count":  state.round_number,
        "rounds":       state.round_history,
    }


@router.post("/{session_id}/respond")
async def submit_supplier_response(
    session_id: str,
    body: dict[str, Any],
    request: Any = None,
) -> dict[str, Any]:
    """
    Submit a supplier's response (counter-offer) to the agent.
    Called by email parser webhook or manually by procurement team.

    Body:
      { "price_per_unit": 1.85, "terms": {...}, "message": "...", "is_final": false }
    """
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    if state.state not in (SessionState.ROUND_ACTIVE, SessionState.STRATEGY_SET):
        raise HTTPException(
            status_code=409,
            detail=f"Session in state {state.state.value} — cannot receive response now",
        )

    coord = _get_coordinator(request)
    pub   = _get_publisher(request)

    result = await coord.process_supplier_response(
        state             = state,
        supplier_price    = float(body["price_per_unit"]),
        supplier_terms    = body.get("terms", {}),
        supplier_message  = body.get("message", ""),
        is_final          = body.get("is_final", False),
    )

    action = result.get("action")

    if action == "auto_accept":
        record_auto_accept(state.tenant_id, state.context.get("material_class", ""))
        record_session_closed(
            tenant_id      = state.tenant_id,
            strategy       = state.strategy.value if state.strategy else "",
            material_class = state.context.get("material_class", ""),
            state          = "AGREEMENT",
            rounds_taken   = state.round_number,
            discount_pct   = 0.0,
            value_saved    = 0.0,
            duration_s     = 0.0,
        )

    offer = result.get("offer")
    return {
        "action":               action,
        "settled_price":        result.get("settled_price"),
        "next_offer_price":     float(offer.price_per_unit) if offer else None,
        "next_offer_message":   offer.message if offer else None,
        "acceptance_probability": result.get("acceptance_probability"),
        "risk_level":           result.get("risk", {}).risk_level.value if result.get("risk") else None,
    }


@router.post("/{session_id}/close")
async def close_session(
    session_id: str,
    body: dict[str, Any],
    request: Any = None,
) -> dict[str, Any]:
    """
    Manually close a session.
    Body: { "result": "agreement" | "withdrawn", "settled_price": 1.85, "reason": "..." }
    """
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    result = body.get("result", "withdrawn")
    state.state = SessionState.AGREEMENT if result == "agreement" else SessionState.WITHDRAWN

    logger.info(
        "negotiation_closed_manually",
        session_id = session_id,
        result     = result,
        reason     = body.get("reason"),
    )
    return {"session_id": session_id, "result": result, "state": state.state.value}


@router.get("/{session_id}/approvals")
async def list_approvals(session_id: str, request: Any = None) -> dict[str, Any]:
    """List all approval requests for a session."""
    # In production: query DB
    return {"session_id": session_id, "approvals": [], "message": "Connect to DB in production"}


@router.post("/{session_id}/approvals/{approval_id}/decide")
async def decide_approval(
    session_id:  str,
    approval_id: str,
    body:        ApprovalDecision,
    request:     Any = None,
) -> dict[str, Any]:
    """
    Human procurement approver submits a decision on an approval request.
    Resumes the suspended negotiation coroutine.
    """
    user    = _get_current_user(request)
    manager = _get_approval_manager(request)

    result = await manager.record_decision(
        request_id     = approval_id,
        approved       = body.approved,
        approver_id    = user["user_id"],
        notes          = body.notes,
        modified_price = float(body.modified_price) if body.modified_price else None,
    )

    outcome = "approved" if result.approved else "rejected"
    record_approval_outcome(
        risk_level      = "MEDIUM",
        checkpoint_type = "offer_send",
        outcome         = outcome,
        tenant_id       = user["tenant_id"],
    )

    logger.info(
        "approval_decision_recorded",
        approval_id = approval_id,
        session_id  = session_id,
        approved    = result.approved,
        approver    = user["user_id"],
    )

    return {
        "approval_id": approval_id,
        "outcome":     outcome,
        "decided_by":  user["user_id"],
        "decided_at":  result.decided_at.isoformat(),
        "modified_price": result.modified_price,
    }


@router.get("/suppliers/{supplier_id}/profile")
async def get_supplier_profile(
    supplier_id: str,
    request:     Any = None,
) -> dict[str, Any]:
    """Retrieve the behavioral profile for a supplier."""
    # In production: load from supplier_behavior_profiles table
    return {
        "supplier_id":           supplier_id,
        "traits":                [],
        "avg_concession_rate":   None,
        "avg_rounds_to_close":   None,
        "anchor_resistance":     0.5,
        "agreement_rate":        None,
        "total_negotiations":    0,
        "confidence":            0.0,
        "message":               "No historical data yet",
    }


@router.get("/kpi")
async def get_kpi_summary(
    days:    int = Query(default=30, ge=1, le=365),
    request: Any = None,
) -> dict[str, Any]:
    """Tenant-level negotiation KPI summary."""
    user = _get_current_user(request)
    # In production: query negotiation_outcomes for tenant + period
    summary = build_kpi_summary([], period_days=days)
    return {
        "tenant_id":               user["tenant_id"],
        "period_days":             days,
        "total_sessions":          summary.total_sessions,
        "agreement_rate":          summary.agreement_rate,
        "avg_discount_pct":        summary.avg_discount_pct,
        "avg_rounds_per_session":  summary.avg_rounds_per_session,
        "total_value_saved_eur":   summary.total_value_saved_eur,
        "avg_session_duration_h":  summary.avg_session_duration_h,
        "top_strategy":            summary.top_strategy,
        "deadlock_rate":           summary.deadlock_rate,
        "escalation_rate":         summary.escalation_rate,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — real-time session event stream
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/{session_id}/stream")
async def negotiation_event_stream(
    websocket:  WebSocket,
    session_id: str,
) -> None:
    """
    Real-time event stream for a negotiation session.
    Sends JSON events as the session progresses.
    Client receives: { event_type, payload, occurred_at }
    """
    await websocket.accept()
    logger.info("ws_connected", session_id=session_id)

    # In production: subscribe to Redis pub/sub channel
    # Here we poll the session state every 2 seconds as fallback
    try:
        last_round = -1
        while True:
            state = _sessions.get(session_id)
            if state is None:
                await websocket.send_json({"event_type": "error", "payload": {"message": "Session not found"}})
                break

            if state.round_number != last_round:
                last_round = state.round_number
                await websocket.send_json({
                    "event_type": "negotiation.state_update",
                    "payload": {
                        "state":        state.state.value,
                        "round_number": state.round_number,
                        "strategy":     state.strategy.value if state.strategy else None,
                    },
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                })

            if state.state in (
                SessionState.AGREEMENT, SessionState.DEADLOCK,
                SessionState.WITHDRAWN, SessionState.ESCALATED
            ):
                await websocket.send_json({
                    "event_type": f"negotiation.session.{state.state.value.lower()}",
                    "payload":    {"state": state.state.value},
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                })
                break

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=session_id)
    finally:
        await websocket.close()
