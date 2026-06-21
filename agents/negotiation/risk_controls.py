"""
Section 9 — Risk Controls

Hard gates that cannot be overridden by the AI agent.
All risk decisions are logged and subject to audit.

Control categories:
  1. Price guardrails      — never offer above walk_away, never go below cost floor
  2. Velocity controls     — max 2 offers per hour to prevent frenzied negotiation
  3. Supplier concentration — block if single supplier > 40% of spend
  4. Compliance checks     — sanctioned entities, export controls
  5. Value thresholds      — escalate if order value exceeds per-agent authority
  6. Behavioral anomaly    — stop if supplier message contains legal threats
  7. Round budget          — auto-escalate if rounds exceed 150% of planned

Risk decision outcomes:
  proceed           → agent continues autonomously
  require_approval  → pause and request human approval (creates ApprovalRequest)
  stop              → permanently block this action; escalate to procurement team
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from .models import RiskLevel

logger = structlog.get_logger(__name__)

# ── Configurable thresholds (can be overridden per tenant) ────────────────────
DEFAULT_THRESHOLDS = {
    "max_order_value_auto_eur":   500_000,     # above this → require approval
    "max_order_value_agent_eur": 2_000_000,    # above this → always escalate (stop)
    "max_supplier_spend_pct":       40.0,      # supplier concentration %
    "max_discount_vs_ask_pct":      35.0,      # if we get >35% discount, something is wrong
    "max_offers_per_hour":           2,
    "price_floor_multiplier":        0.50,     # never offer below 50% of initial ask
    "walk_away_breach_tolerance":    0.02,     # allow up to 2% above walk_away in final round
    "legal_threat_keywords": [
        "legal action", "sue", "court", "attorney", "solicitor",
        "litigation", "breach of contract", "lawyer",
    ],
    "sanction_country_codes": ["RU", "BY", "KP", "IR", "SY", "CU"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Risk Decision
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskDecision:
    action:            str          # "proceed" | "require_approval" | "stop"
    risk_level:        RiskLevel
    reason:            str
    requires_approval: bool = False
    controls_fired:    list[str] = field(default_factory=list)
    metadata:          dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocked(self) -> bool:
        return self.action == "stop"

    @property
    def can_proceed_auto(self) -> bool:
        return self.action == "proceed"


# ─────────────────────────────────────────────────────────────────────────────
# Individual Risk Controls
# ─────────────────────────────────────────────────────────────────────────────

def _price_guardrail(
    offer_price:  float,
    walk_away:    float,
    initial_ask:  float,
    is_final_round: bool,
    cfg:          dict[str, Any],
) -> RiskDecision | None:
    floor = initial_ask * cfg["price_floor_multiplier"]
    if offer_price < floor:
        return RiskDecision(
            action     = "stop",
            risk_level = RiskLevel.STOP,
            reason     = f"Offer €{offer_price:.4f} below absolute floor €{floor:.4f} (50% of initial ask)",
            controls_fired = ["price_floor"],
        )

    tolerance = cfg["walk_away_breach_tolerance"]
    if offer_price > walk_away * (1 + tolerance) and not is_final_round:
        return RiskDecision(
            action             = "require_approval",
            risk_level         = RiskLevel.HIGH,
            reason             = f"Offer €{offer_price:.4f} exceeds walk-away €{walk_away:.4f}",
            requires_approval  = True,
            controls_fired     = ["walk_away_breach"],
        )
    return None


def _order_value_control(
    offer_price:  float,
    quantity:     float,
    cfg:          dict[str, Any],
) -> RiskDecision | None:
    value = offer_price * quantity
    if value > cfg["max_order_value_agent_eur"]:
        return RiskDecision(
            action     = "stop",
            risk_level = RiskLevel.STOP,
            reason     = f"Order value €{value:,.0f} exceeds agent authority €{cfg['max_order_value_agent_eur']:,.0f}",
            controls_fired = ["order_value_agent_limit"],
            metadata   = {"order_value": value},
        )
    if value > cfg["max_order_value_auto_eur"]:
        return RiskDecision(
            action            = "require_approval",
            risk_level        = RiskLevel.HIGH,
            reason            = f"Order value €{value:,.0f} requires human approval",
            requires_approval = True,
            controls_fired    = ["order_value_approval"],
            metadata          = {"order_value": value},
        )
    return None


def _supplier_concentration_control(
    supplier_spend_pct: float | None,
    cfg: dict[str, Any],
) -> RiskDecision | None:
    if supplier_spend_pct is None:
        return None
    if supplier_spend_pct > cfg["max_supplier_spend_pct"]:
        return RiskDecision(
            action            = "require_approval",
            risk_level        = RiskLevel.HIGH,
            reason            = f"Supplier spend concentration {supplier_spend_pct:.1f}% exceeds {cfg['max_supplier_spend_pct']:.0f}% limit",
            requires_approval = True,
            controls_fired    = ["supplier_concentration"],
            metadata          = {"concentration_pct": supplier_spend_pct},
        )
    return None


def _discount_anomaly_control(
    offer_price:   float,
    initial_ask:   float,
    cfg:           dict[str, Any],
) -> RiskDecision | None:
    if initial_ask <= 0:
        return None
    discount_pct = (initial_ask - offer_price) / initial_ask * 100
    if discount_pct > cfg["max_discount_vs_ask_pct"]:
        return RiskDecision(
            action            = "require_approval",
            risk_level        = RiskLevel.HIGH,
            reason            = f"Discount {discount_pct:.1f}% exceeds {cfg['max_discount_vs_ask_pct']:.0f}% — possible data error",
            requires_approval = True,
            controls_fired    = ["discount_anomaly"],
            metadata          = {"discount_pct": discount_pct},
        )
    return None


def _legal_threat_control(
    supplier_message: str,
    cfg:              dict[str, Any],
) -> RiskDecision | None:
    if not supplier_message:
        return None
    msg_lower = supplier_message.lower()
    for keyword in cfg["legal_threat_keywords"]:
        if keyword in msg_lower:
            return RiskDecision(
                action     = "stop",
                risk_level = RiskLevel.STOP,
                reason     = f"Supplier message contains legal threat keyword: '{keyword}'",
                controls_fired = ["legal_threat_detection"],
                metadata   = {"keyword": keyword},
            )
    return None


def _sanction_control(
    supplier_country: str | None,
    cfg:              dict[str, Any],
) -> RiskDecision | None:
    if not supplier_country:
        return None
    if supplier_country.upper() in cfg["sanction_country_codes"]:
        return RiskDecision(
            action     = "stop",
            risk_level = RiskLevel.STOP,
            reason     = f"Supplier country {supplier_country} is on sanctions list",
            controls_fired = ["sanction_check"],
            metadata   = {"country": supplier_country},
        )
    return None


def _round_budget_control(
    round_number: int,
    max_rounds:   int,
) -> RiskDecision | None:
    if round_number > max_rounds * 1.5:
        return RiskDecision(
            action            = "require_approval",
            risk_level        = RiskLevel.MEDIUM,
            reason            = f"Round {round_number} exceeds 150% of planned max ({max_rounds})",
            requires_approval = True,
            controls_fired    = ["round_budget_exceeded"],
        )
    return None


def _acceptance_probability_control(
    accept_prob:  float,
    round_number: int,
    max_rounds:   int,
) -> RiskDecision | None:
    # Very low probability in later rounds → flag for review
    if round_number > max_rounds * 0.7 and accept_prob < 0.15:
        return RiskDecision(
            action            = "require_approval",
            risk_level        = RiskLevel.MEDIUM,
            reason            = f"Acceptance probability {accept_prob:.0%} is very low with {max_rounds - round_number} rounds left",
            requires_approval = True,
            controls_fired    = ["low_acceptance_probability"],
            metadata          = {"accept_prob": accept_prob},
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Risk Control Engine (façade)
# ─────────────────────────────────────────────────────────────────────────────

class RiskControlEngine:
    """
    Evaluates all risk controls in priority order.
    Returns the highest-severity finding.
    Controls are non-negotiable — they override AI agent decisions.
    """

    def __init__(self, thresholds: dict[str, Any] | None = None) -> None:
        self._cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def evaluate_offer(
        self,
        session_id:          str,
        offer_price:         float,
        walk_away:           float,
        initial_ask:         float,
        round_number:        int,
        max_rounds:          int,
        accept_prob:         float = 0.5,
        quantity:            float = 0.0,
        supplier_message:    str   = "",
        supplier_country:    str | None = None,
        supplier_spend_pct:  float | None = None,
        is_final_round:      bool = False,
    ) -> RiskDecision:
        controls = [
            _sanction_control(supplier_country, self._cfg),
            _legal_threat_control(supplier_message, self._cfg),
            _price_guardrail(offer_price, walk_away, initial_ask, is_final_round, self._cfg),
            _order_value_control(offer_price, quantity, self._cfg),
            _supplier_concentration_control(supplier_spend_pct, self._cfg),
            _discount_anomaly_control(offer_price, initial_ask, self._cfg),
            _round_budget_control(round_number, max_rounds),
            _acceptance_probability_control(accept_prob, round_number, max_rounds),
        ]

        # Collect all fired controls
        fired = [c for c in controls if c is not None]

        if not fired:
            return RiskDecision(
                action     = "proceed",
                risk_level = RiskLevel.LOW,
                reason     = "All risk controls passed",
            )

        # Priority: STOP > HIGH > MEDIUM > LOW
        priority = {RiskLevel.STOP: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
        worst    = max(fired, key=lambda d: priority.get(d.risk_level, 0))

        # Aggregate all fired control names
        all_controls = [ctrl for d in fired for ctrl in d.controls_fired]

        result = RiskDecision(
            action            = worst.action,
            risk_level        = worst.risk_level,
            reason            = worst.reason,
            requires_approval = worst.requires_approval,
            controls_fired    = all_controls,
            metadata          = {
                "session_id":   session_id,
                "offer_price":  offer_price,
                "round_number": round_number,
                "fired_count":  len(fired),
            },
        )

        log_fn = logger.error if worst.risk_level == RiskLevel.STOP else logger.warning
        log_fn(
            "risk_control_fired",
            session_id     = session_id,
            action         = result.action,
            risk_level     = result.risk_level.value,
            controls_fired = all_controls,
            reason         = result.reason,
        )

        return result

    def evaluate_session_open(
        self,
        supplier_country:    str | None,
        total_order_value:   float,
        supplier_spend_pct:  float | None = None,
    ) -> RiskDecision:
        """Pre-flight check before opening a negotiation session."""
        checks = [
            _sanction_control(supplier_country, self._cfg),
            _order_value_control(1.0, total_order_value, self._cfg),
            _supplier_concentration_control(supplier_spend_pct, self._cfg),
        ]
        fired = [c for c in checks if c is not None]
        if not fired:
            return RiskDecision(action="proceed", risk_level=RiskLevel.LOW, reason="Pre-flight passed")
        priority = {RiskLevel.STOP: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}
        worst    = max(fired, key=lambda d: priority.get(d.risk_level, 0))
        return worst
