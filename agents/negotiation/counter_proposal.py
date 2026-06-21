"""
Section 5 — Offer & Counter-Proposal Engine

Generates each round's offer, combining:

  Concession Schedule:
    - Ackermann pattern  (e.g. 50%→25%→13%→6%→3%→2%→1% of total concession space)
    - Decreasing returns (each concession smaller than last)
    - Minimum floor = walk_away_price (never crossed)

  Package Construction:
    Price is never the only lever. This engine also negotiates:
      - Payment terms    (net30 → net60 raises acceptance ~8%)
      - Delivery window  (relaxing by 1 week raises acceptance ~5%)
      - Warranty length  (adding 6 months raises acceptance ~4%)
      - Annual volume commitment (higher commitment justifies lower price)
      - Price validity period

    Total value is computed in EUR to enable fair comparison.

  Concession Framing (Behavioral Economics):
    - Frame every offer as movement from our side ("We've improved our offer by X")
    - Reference anchor ("Given the market rate of Y…")
    - Loss aversion framing for price-sensitive suppliers
    - Reciprocity trigger ("We made a significant concession — we expect similar movement")
    - Scarcity counter ("We have 3 other suppliers being evaluated")

  Rationale Generator:
    LLM-generated negotiation message using Anthropic claude-haiku-4-5-20251001
    Falls back to template if LLM unavailable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from .models import NegotiationOffer, NegotiationStrategy, TermsBundle
from .supplier_behavior import SupplierBehaviorProfile

logger = structlog.get_logger(__name__)

# Ackermann concession fractions (normalized to sum to 1)
_ACKERMANN_FRACTIONS = [0.50, 0.25, 0.125, 0.0625, 0.03125, 0.015, 0.01]


# ─────────────────────────────────────────────────────────────────────────────
# Concession Schedule
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConcessionSchedule:
    """Pre-computed concession amounts for each round."""
    anchor_price:    float
    target_price:    float
    walk_away:       float
    total_space:     float           # anchor - walk_away
    round_prices:    list[float]     # one per planned round
    strategy:        NegotiationStrategy


class ConcessionScheduler:
    """
    Builds the full price path from anchor → target → walk_away.
    Different patterns for different strategies.
    """

    def build(
        self,
        anchor_price:  float,
        target_price:  float,
        walk_away:     float,
        max_rounds:    int,
        strategy:      NegotiationStrategy,
        urgency_score: float = 0.5,
    ) -> ConcessionSchedule:
        total_space = anchor_price - walk_away
        if total_space <= 0:
            return ConcessionSchedule(
                anchor_price  = anchor_price,
                target_price  = target_price,
                walk_away     = walk_away,
                total_space   = 0,
                round_prices  = [anchor_price] * max_rounds,
                strategy      = strategy,
            )

        fractions = self._fractions_for(strategy, max_rounds, urgency_score)
        prices: list[float] = []
        current = anchor_price

        for i, frac in enumerate(fractions[:max_rounds]):
            concession = total_space * frac
            current   -= concession
            current    = max(current, walk_away)
            prices.append(round(current, 6))

        # Pad if fewer fractions than max_rounds
        while len(prices) < max_rounds:
            prices.append(max(walk_away, prices[-1]))

        return ConcessionSchedule(
            anchor_price  = anchor_price,
            target_price  = target_price,
            walk_away     = walk_away,
            total_space   = total_space,
            round_prices  = prices,
            strategy      = strategy,
        )

    def _fractions_for(
        self,
        strategy:      NegotiationStrategy,
        max_rounds:    int,
        urgency_score: float,
    ) -> list[float]:
        if strategy == NegotiationStrategy.HARDBALL:
            # Very slow — early rounds tiny, no concession until round 4+
            base = [0.05, 0.03, 0.02, 0.20, 0.15, 0.10, 0.10, 0.15]
        elif strategy == NegotiationStrategy.COMPETITIVE:
            # Ackermann pattern — signals there is NOT much room
            base = _ACKERMANN_FRACTIONS
        elif strategy == NegotiationStrategy.COLLABORATIVE:
            # Larger early concessions — signals good faith
            base = [0.30, 0.20, 0.18, 0.12, 0.08, 0.06, 0.04, 0.02]
        elif strategy == NegotiationStrategy.COMPROMISING:
            # Equal concessions each round
            n = max(max_rounds, 4)
            base = [0.8 / n] * n
        elif strategy == NegotiationStrategy.ACCOMMODATING:
            # Front-loaded — give most of the concession early
            base = [0.45, 0.25, 0.15, 0.08, 0.04, 0.02, 0.01]
        elif strategy == NegotiationStrategy.DEADLINE:
            # Hold firm, then large concession near deadline
            n = max(max_rounds, 6)
            base = [0.02] * (n - 2) + [0.30, 0.20]
        else:
            base = _ACKERMANN_FRACTIONS

        # Urgency modifier: high urgency → concede faster
        if urgency_score > 0.75:
            base = [f * 1.3 for f in base]

        # Normalize to sum ≤ 1.0 (we keep 5% as final-offer buffer)
        total = sum(base[:max_rounds])
        if total > 0.95:
            base = [f * 0.95 / total for f in base]

        return base[:max_rounds]


# ─────────────────────────────────────────────────────────────────────────────
# Terms Package Builder
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TermsPackage:
    price:       float
    terms:       TermsBundle
    total_value: float    # net present value of terms in EUR
    eur_savings: float    # vs baseline terms
    summary:     str


class TermsPackageBuilder:
    """
    Constructs a package offer that includes non-price terms.
    Uses EUR value equivalents to keep offers comparable.
    """

    # EUR value of each 30-day extension in payment terms per €1M order value
    PAYMENT_DAYS_VALUE_PER_30D = 2_000.0     # ~0.2% financing benefit
    DELIVERY_WEEK_VALUE        = 500.0        # per week relaxation
    WARRANTY_MONTH_VALUE       = 200.0        # per month extension

    def build(
        self,
        price:          float,
        quantity:       int,
        round_number:   int,
        strategy:       NegotiationStrategy,
        profile:        SupplierBehaviorProfile,
        baseline_terms: TermsBundle | None = None,
    ) -> TermsPackage:
        base = baseline_terms or TermsBundle()
        terms = TermsBundle(**base.model_dump())

        order_value = price * quantity
        eur_value   = 0.0
        changes: list[str] = []

        # In later rounds or for relationship-driven suppliers, offer better terms
        if round_number >= 3 or "RELATIONSHIP_DRIVEN" in profile.traits:
            # Extend payment terms
            if terms.payment_days < 60:
                terms.payment_days = 60
                delta = (60 - base.payment_days) / 30
                eur_value += delta * self.PAYMENT_DAYS_VALUE_PER_30D * (order_value / 1_000_000)
                changes.append(f"net{terms.payment_days} payment")

        if round_number >= 4 or strategy in (NegotiationStrategy.COLLABORATIVE, NegotiationStrategy.ACCOMMODATING):
            # Relax delivery window
            if terms.delivery_weeks < base.delivery_weeks + 2:
                terms.delivery_weeks = base.delivery_weeks + 2
                eur_value += 2 * self.DELIVERY_WEEK_VALUE
                changes.append(f"{terms.delivery_weeks}w delivery")

        if round_number >= 5 and "RELATIONSHIP_DRIVEN" in profile.traits:
            # Add warranty months
            if terms.warranty_months < 24:
                terms.warranty_months = 24
                eur_value += (24 - base.warranty_months) * self.WARRANTY_MONTH_VALUE
                changes.append("24mo warranty")

        summary = f"Offer: €{price:.4f}/unit"
        if changes:
            summary += f" + {', '.join(changes)}"

        return TermsPackage(
            price       = price,
            terms       = terms,
            total_value = order_value + eur_value,
            eur_savings = eur_value,
            summary     = summary,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Message Framer (Behavioral Economics)
# ─────────────────────────────────────────────────────────────────────────────

class OfferMessageFramer:
    """
    Generates negotiation message text using behavioral economics principles.
    Uses Anthropic API if available; falls back to templates.
    """

    def __init__(self, anthropic_client: Any = None) -> None:
        self._client = anthropic_client

    async def frame(
        self,
        round_number:        int,
        our_price:           float,
        prev_price:          float | None,
        supplier_last_price: float | None,
        market_price:        float | None,
        strategy:            NegotiationStrategy,
        profile:             SupplierBehaviorProfile,
        terms_package:       TermsPackage,
        session_context:     dict[str, Any],
    ) -> str:
        if self._client is not None:
            return await self._llm_frame(
                round_number, our_price, prev_price, supplier_last_price,
                market_price, strategy, profile, terms_package, session_context,
            )
        return self._template_frame(
            round_number, our_price, prev_price, supplier_last_price,
            market_price, strategy, profile, terms_package,
        )

    async def _llm_frame(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        (round_number, our_price, prev_price, supplier_last_price,
         market_price, strategy, profile, terms_package, session_context) = args

        concession_str = ""
        if prev_price:
            delta = abs(our_price - prev_price)
            pct   = delta / prev_price * 100
            concession_str = f"Our previous offer was €{prev_price:.4f}. We are improving it by €{delta:.4f} ({pct:.1f}%)."

        prompt = f"""You are a procurement negotiation specialist writing a professional supplier communication.

Context:
- Round: {round_number}
- Our offer: €{our_price:.4f}/unit
- Supplier's last price: €{supplier_last_price or 'N/A'}
- Market reference price: €{market_price or 'N/A'}
- Strategy: {strategy.value}
- Supplier profile: traits={profile.traits}, reciprocity_ratio={profile.reciprocity_ratio}
- {concession_str}
- Terms: {terms_package.summary}

Write a concise (3-4 sentences), professional negotiation message that:
1. States our revised offer clearly
2. References market context or our concession (not both)
3. Applies {strategy.value} strategy framing
4. Ends with a clear call to action

Use professional but direct language. No fluff."""

        response = await self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def _template_frame(
        self,
        round_number:        int,
        our_price:           float,
        prev_price:          float | None,
        supplier_last_price: float | None,
        market_price:        float | None,
        strategy:            NegotiationStrategy,
        profile:             SupplierBehaviorProfile,
        terms_package:       TermsPackage,
    ) -> str:
        lines: list[str] = []

        # Opening
        if round_number == 1:
            lines.append(f"Thank you for your quotation. Following a thorough market analysis, we are pleased to present our offer.")
        else:
            concession = abs(our_price - prev_price) if prev_price else 0
            pct = concession / prev_price * 100 if prev_price else 0
            lines.append(
                f"We have reviewed your position and, in the spirit of reaching an agreement, "
                f"we are improving our offer by €{concession:.4f}/unit ({pct:.1f}%)."
            )

        # Price statement
        lines.append(f"Our revised offer is €{our_price:.4f}/unit ({terms_package.terms.incoterm}).")

        # Market reference (if available)
        if market_price:
            lines.append(
                f"This aligns with current market benchmarks of approximately €{market_price:.4f}/unit "
                f"and reflects our long-term partnership intent."
            )

        # Terms highlight
        if terms_package.eur_savings > 0:
            lines.append(
                f"We are also offering improved commercial terms: {terms_package.summary.split('+')[-1].strip()}, "
                f"which represents an additional €{terms_package.eur_savings:,.0f} in value."
            )

        # Strategy-specific framing
        if strategy == NegotiationStrategy.COMPETITIVE:
            lines.append("We are evaluating multiple suppliers. Your prompt confirmation would be appreciated.")
        elif strategy == NegotiationStrategy.COLLABORATIVE:
            lines.append("We are committed to a long-term partnership and would welcome your counter-proposal.")
        elif strategy == NegotiationStrategy.DEADLINE:
            lines.append("We require confirmation by end of week to proceed with this procurement cycle.")
        elif strategy == NegotiationStrategy.HARDBALL:
            lines.append("This offer reflects our firm budget ceiling. We look forward to your response.")

        # Reciprocity trigger
        if "RECIPROCAL" in profile.traits and round_number > 1:
            lines.append("We have made a meaningful concession from our previous position and expect reciprocal movement.")

        return " ".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Counter-Proposal Engine (main facade)
# ─────────────────────────────────────────────────────────────────────────────

class CounterProposalEngine:
    """
    Generates the next offer for a negotiation round.

    Inputs:
      - session state (round number, walk-away, strategy)
      - supplier behavior profile
      - concession schedule (pre-built)
      - supplier's last response

    Outputs:
      - NegotiationOffer ready to send
      - Rationale for human review
    """

    def __init__(self, anthropic_client: Any = None) -> None:
        self._scheduler = ConcessionScheduler()
        self._terms     = TermsPackageBuilder()
        self._framer    = OfferMessageFramer(anthropic_client)

    def build_schedule(
        self,
        anchor_price:  float,
        target_price:  float,
        walk_away:     float,
        max_rounds:    int,
        strategy:      NegotiationStrategy,
        urgency_score: float = 0.5,
    ) -> ConcessionSchedule:
        return self._scheduler.build(
            anchor_price, target_price, walk_away, max_rounds, strategy, urgency_score
        )

    async def generate_offer(
        self,
        schedule:       ConcessionSchedule,
        round_number:   int,
        quantity:       int,
        profile:        SupplierBehaviorProfile,
        strategy:       NegotiationStrategy,
        supplier_response: dict[str, Any] | None = None,
        market_price:   float | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> NegotiationOffer:
        """Generate the offer for round N (1-indexed)."""
        idx     = max(0, round_number - 1)
        price   = schedule.round_prices[min(idx, len(schedule.round_prices) - 1)]

        prev_price           = schedule.round_prices[idx - 1] if idx > 0 else None
        supplier_last_price  = (supplier_response or {}).get("price_per_unit")

        # Adaptive adjustment: if supplier is converging faster than expected,
        # hold our concession to capture more value
        if supplier_last_price and prev_price:
            gap  = supplier_last_price - price
            prev_gap = supplier_last_price - (prev_price or price)
            if prev_gap > 0 and gap / prev_gap < 0.4:
                # Supplier converging fast → hold current price, don't offer pre-planned concession
                price = prev_price or price
                logger.debug("counter_proposal.holding_price", reason="supplier_converging_fast", price=price)

        # Build terms package
        terms_pkg = self._terms.build(
            price       = price,
            quantity    = quantity,
            round_number = round_number,
            strategy    = strategy,
            profile     = profile,
        )

        # Generate message
        message = await self._framer.frame(
            round_number        = round_number,
            our_price           = price,
            prev_price          = prev_price,
            supplier_last_price = supplier_last_price,
            market_price        = market_price,
            strategy            = strategy,
            profile             = profile,
            terms_package       = terms_pkg,
            session_context     = session_context or {},
        )

        # Rationale for audit / human review
        rationale = (
            f"Round {round_number}: Offering €{price:.4f} "
            f"(anchor: €{schedule.anchor_price:.4f}, "
            f"target: €{schedule.target_price:.4f}, "
            f"walk-away: €{schedule.walk_away:.4f}). "
            f"Strategy: {strategy.value}. "
            f"Concession from last round: €{abs(price - (prev_price or price)):.4f}."
        )

        return NegotiationOffer(
            price_per_unit    = Decimal(str(round(price, 6))),
            currency          = "EUR",
            terms             = terms_pkg.terms,
            message           = message,
            rationale         = rationale,
            valid_until_hours = 48 if strategy != NegotiationStrategy.DEADLINE else 24,
        )

    def should_accept_supplier_counter(
        self,
        supplier_price:   float,
        walk_away:        float,
        target_price:     float,
        acceptance_prob:  float,
        round_number:     int,
        max_rounds:       int,
    ) -> tuple[bool, str]:
        """
        Decides whether to auto-accept a supplier counter-offer.
        Returns (should_accept, reason).
        """
        if supplier_price < walk_away:
            return False, "supplier price is below walk-away"
        if supplier_price <= target_price:
            return True, f"supplier met our target price of €{target_price:.4f}"
        if acceptance_prob >= 0.85 and round_number >= max_rounds - 1:
            return True, f"high acceptance probability ({acceptance_prob:.0%}) on final round"
        return False, "continue negotiating"
