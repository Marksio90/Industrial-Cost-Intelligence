"""
Section 8 — Human Approval Workflow

Defines when autonomous agent must pause and seek human approval,
manages the approval lifecycle, and enforces timeouts.

Approval triggers (any one sufficient):
  ┌─────────────────────────────────────────────────────────┬───────────┐
  │ Condition                                               │ Risk Level│
  ├─────────────────────────────────────────────────────────┼───────────┤
  │ Offer price > walk_away * 1.05                          │ HIGH      │
  │ Total order value > €500k                               │ HIGH      │
  │ Round 1 anchor price (always approve before first send) │ MEDIUM    │
  │ Strategy switch (mid-session adaptation)                │ MEDIUM    │
  │ Walk-away decision                                      │ HIGH      │
  │ Settlement above target price                           │ MEDIUM    │
  │ Supplier flagged as strategic/sole-source               │ MEDIUM    │
  │ Behavioral signal: SCARCITY_PRESSURE (possible fraud)   │ MEDIUM    │
  │ Acceptance probability drop > 20% in one round          │ LOW       │
  └─────────────────────────────────────────────────────────┴───────────┘

Auto-approval tiers:
  LOW     → auto-approve after 2 hours (email notification)
  MEDIUM  → require human within 4 hours (Slack + email)
  HIGH    → block until human approves (no timeout)
  STOP    → permanent block, escalate to CPO

Approval API:
  POST /negotiations/{session_id}/approvals/{approval_id}/decide
  { "approved": true, "notes": "...", "modified_price": null }

Webhook on approval → coordinator resumes session.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

import structlog

from .models import ApprovalStatus, RiskLevel

logger = structlog.get_logger(__name__)

# Timeout configuration per risk level (hours)
_AUTO_APPROVE_AFTER_H: dict[RiskLevel, float | None] = {
    RiskLevel.LOW:    2.0,
    RiskLevel.MEDIUM: None,   # must be human-approved
    RiskLevel.HIGH:   None,
    RiskLevel.STOP:   None,
}

_NOTIFICATION_CHANNELS: dict[RiskLevel, list[str]] = {
    RiskLevel.LOW:    ["email"],
    RiskLevel.MEDIUM: ["slack", "email"],
    RiskLevel.HIGH:   ["slack", "email", "sms"],
    RiskLevel.STOP:   ["slack", "email", "sms", "pagerduty"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Approval Request
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    request_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id:       str = ""
    tenant_id:        str = ""
    checkpoint_type:  str = ""    # "first_offer" | "offer_send" | "walk_away" | "settlement" | "strategy_change"
    risk_level:       RiskLevel = RiskLevel.MEDIUM
    trigger_reason:   str = ""

    # What the agent proposes
    proposed_action:  dict[str, Any] = field(default_factory=dict)
    ai_recommendation: str = ""
    risk_summary:     str = ""

    # Context for approver
    session_context:  dict[str, Any] = field(default_factory=dict)
    round_history:    list[dict[str, Any]] = field(default_factory=list)

    # Lifecycle
    status:           ApprovalStatus = ApprovalStatus.PENDING
    approver_id:      str | None = None
    approver_notes:   str = ""
    created_at:       datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at:       datetime | None = None
    decided_at:       datetime | None = None

    # Modified price from approver (optional override)
    approved_price:   float | None = None


@dataclass
class ApprovalDecisionResult:
    approved:       bool
    modified_price: float | None
    notes:          str
    decided_by:     str
    decided_at:     datetime


# ─────────────────────────────────────────────────────────────────────────────
# Trigger Evaluator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApprovalTrigger:
    condition:      str
    risk_level:     RiskLevel
    checkpoint_type: str
    reason:         str


class ApprovalTriggerEvaluator:
    """
    Evaluates a proposed action against all trigger conditions.
    Returns the highest-risk trigger that fires, or None for auto-proceed.
    """

    def evaluate(
        self,
        offer_price:        float,
        walk_away:          float,
        initial_ask:        float,
        order_value:        float,
        round_number:       int,
        is_first_offer:     bool,
        is_sole_source:     bool,
        is_walk_away:       bool,
        is_settlement:      bool,
        target_price:       float,
        strategy_changed:   bool,
        signal_names:       list[str],
        prev_accept_prob:   float | None = None,
        curr_accept_prob:   float | None = None,
        require_first_offer_approval: bool = True,
    ) -> ApprovalTrigger | None:
        triggers: list[ApprovalTrigger] = []

        if is_walk_away:
            triggers.append(ApprovalTrigger(
                condition      = "walk_away_decision",
                risk_level     = RiskLevel.HIGH,
                checkpoint_type = "walk_away",
                reason         = "Agent proposing to walk away — requires human confirmation",
            ))

        if is_settlement and offer_price > target_price * 1.02:
            triggers.append(ApprovalTrigger(
                condition      = "settlement_above_target",
                risk_level     = RiskLevel.HIGH,
                checkpoint_type = "settlement",
                reason         = f"Settlement price €{offer_price:.4f} is {(offer_price/target_price-1)*100:.1f}% above target",
            ))

        if offer_price > walk_away * 1.05:
            triggers.append(ApprovalTrigger(
                condition      = "offer_above_walk_away",
                risk_level     = RiskLevel.HIGH,
                checkpoint_type = "offer_send",
                reason         = f"Offer €{offer_price:.4f} exceeds walk-away €{walk_away:.4f}",
            ))

        if order_value > 500_000:
            triggers.append(ApprovalTrigger(
                condition      = "high_order_value",
                risk_level     = RiskLevel.HIGH,
                checkpoint_type = "offer_send",
                reason         = f"Order value €{order_value:,.0f} exceeds €500k threshold",
            ))

        if is_first_offer and require_first_offer_approval:
            triggers.append(ApprovalTrigger(
                condition      = "first_offer",
                risk_level     = RiskLevel.MEDIUM,
                checkpoint_type = "first_offer",
                reason         = "First offer requires human review before sending",
            ))

        if is_sole_source:
            triggers.append(ApprovalTrigger(
                condition      = "sole_source_supplier",
                risk_level     = RiskLevel.MEDIUM,
                checkpoint_type = "offer_send",
                reason         = "Sole-source supplier — negotiation approach requires approval",
            ))

        if strategy_changed:
            triggers.append(ApprovalTrigger(
                condition      = "strategy_change",
                risk_level     = RiskLevel.MEDIUM,
                checkpoint_type = "strategy_change",
                reason         = "Agent adapting negotiation strategy mid-session",
            ))

        if "SCARCITY_PRESSURE" in signal_names:
            triggers.append(ApprovalTrigger(
                condition      = "scarcity_signal",
                risk_level     = RiskLevel.MEDIUM,
                checkpoint_type = "offer_send",
                reason         = "Supplier applying scarcity pressure — verify before responding",
            ))

        if (prev_accept_prob is not None and curr_accept_prob is not None
                and prev_accept_prob - curr_accept_prob > 0.20):
            triggers.append(ApprovalTrigger(
                condition      = "acceptance_prob_drop",
                risk_level     = RiskLevel.LOW,
                checkpoint_type = "offer_send",
                reason         = f"Acceptance probability dropped from {prev_accept_prob:.0%} to {curr_accept_prob:.0%}",
            ))

        if not triggers:
            return None

        # Return highest-risk trigger
        priority = {RiskLevel.STOP: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
        return max(triggers, key=lambda t: priority.get(t.risk_level, 0))


# ─────────────────────────────────────────────────────────────────────────────
# Approval Manager
# ─────────────────────────────────────────────────────────────────────────────

NotifyFn = Callable[[ApprovalRequest, list[str]], Awaitable[None]]
PersistFn = Callable[[ApprovalRequest], Awaitable[str]]
LoadFn    = Callable[[str], Awaitable[ApprovalRequest | None]]


class HumanApprovalManager:
    """
    Creates, tracks, and resolves approval requests.
    Integrates with notification channels and DB persistence.
    """

    def __init__(
        self,
        notify_fn:  NotifyFn | None = None,
        persist_fn: PersistFn | None = None,
        load_fn:    LoadFn    | None = None,
    ) -> None:
        self._notify   = notify_fn
        self._persist  = persist_fn
        self._load     = load_fn
        self._evaluator = ApprovalTriggerEvaluator()
        self._pending:  dict[str, asyncio.Future[ApprovalDecisionResult]] = {}

    async def request_approval(
        self,
        session_id:        str,
        tenant_id:         str,
        checkpoint_type:   str,
        risk_level:        RiskLevel,
        trigger_reason:    str,
        proposed_action:   dict[str, Any],
        ai_recommendation: str = "",
        risk_summary:      str = "",
        session_context:   dict[str, Any] | None = None,
        round_history:     list[dict[str, Any]] | None = None,
    ) -> ApprovalRequest:
        """Creates and persists an approval request. Returns immediately."""
        auto_approve_h = _AUTO_APPROVE_AFTER_H.get(risk_level)
        expires_at     = (
            datetime.now(timezone.utc) + timedelta(hours=auto_approve_h)
            if auto_approve_h else None
        )

        request = ApprovalRequest(
            session_id        = session_id,
            tenant_id         = tenant_id,
            checkpoint_type   = checkpoint_type,
            risk_level        = risk_level,
            trigger_reason    = trigger_reason,
            proposed_action   = proposed_action,
            ai_recommendation = ai_recommendation,
            risk_summary      = risk_summary,
            session_context   = session_context or {},
            round_history     = round_history   or [],
            expires_at        = expires_at,
        )

        if self._persist:
            request.request_id = await self._persist(request)

        # Notify channels
        channels = _NOTIFICATION_CHANNELS.get(risk_level, ["email"])
        if self._notify:
            asyncio.create_task(self._notify(request, channels))

        # Schedule auto-approval for LOW risk
        if auto_approve_h is not None:
            asyncio.create_task(self._auto_approve_after(request, auto_approve_h * 3600))

        logger.info(
            "approval_requested",
            request_id   = request.request_id,
            session_id   = session_id,
            risk_level   = risk_level.value,
            checkpoint   = checkpoint_type,
            auto_approve = auto_approve_h,
        )
        return request

    async def wait_for_decision(
        self,
        request_id: str,
        timeout_s:  float = 14400.0,    # 4 hours default
    ) -> ApprovalDecisionResult:
        """
        Suspends the negotiation coroutine until a human (or auto-approve) decides.
        """
        loop   = asyncio.get_event_loop()
        future: asyncio.Future[ApprovalDecisionResult] = loop.create_future()
        self._pending[request_id] = future

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("approval_timeout", request_id=request_id)
            future.cancel()
            del self._pending[request_id]
            raise

    async def record_decision(
        self,
        request_id:    str,
        approved:      bool,
        approver_id:   str,
        notes:         str = "",
        modified_price: float | None = None,
    ) -> ApprovalDecisionResult:
        """Called by the API layer when a human submits a decision."""
        result = ApprovalDecisionResult(
            approved       = approved,
            modified_price = modified_price,
            notes          = notes,
            decided_by     = approver_id,
            decided_at     = datetime.now(timezone.utc),
        )

        logger.info(
            "approval_decided",
            request_id  = request_id,
            approved    = approved,
            approver_id = approver_id,
        )

        if request_id in self._pending:
            future = self._pending.pop(request_id)
            if not future.done():
                future.set_result(result)

        return result

    async def _auto_approve_after(self, request: ApprovalRequest, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        if request.request_id in self._pending:
            logger.info("auto_approving", request_id=request.request_id, risk=request.risk_level.value)
            await self.record_decision(
                request_id     = request.request_id,
                approved       = True,
                approver_id    = "system:auto-approve",
                notes          = f"Auto-approved after {delay_s/3600:.1f}h (LOW risk level)",
            )

    def check_trigger(
        self,
        **kwargs: Any,
    ) -> ApprovalTrigger | None:
        return self._evaluator.evaluate(**kwargs)
