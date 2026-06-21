"""
Section 6 — Strategy Selection Engine

Chooses the negotiation strategy for a new session and adapts it mid-session.

Selection factors (weighted scoring matrix):
  ┌────────────────────────┬────────┬────────────────────────────────────────┐
  │ Factor                 │ Weight │ Signal                                 │
  ├────────────────────────┼────────┼────────────────────────────────────────┤
  │ Urgency score          │  0.20  │ deadline < 14 days → DEADLINE          │
  │ Supplier alternatives  │  0.20  │ substitutes ≥ 3 → COMPETITIVE         │
  │ Relationship value     │  0.15  │ > 5 prior negotiations → COLLAB        │
  │ Price gap size         │  0.15  │ gap > 20% → HARDBALL; < 5% → COMPROM  │
  │ Supplier traits        │  0.15  │ STUBBORN → DEADLINE; FAST → COMPETITIVE│
  │ Market position        │  0.10  │ sole source → ACCOMMODATING            │
  │ Volume importance      │  0.05  │ strategic supplier → COLLABORATIVE     │
  └────────────────────────┴────────┴────────────────────────────────────────┘

Mid-session adaptation rules:
  - ACCELERATION signal → switch to COMPETITIVE (apply pressure)
  - PLATEAU signal      → switch to DEADLINE (create urgency)
  - FINAL_OFFER_CLAIM   → switch to HARDBALL (call their bluff or accept)
  - Rounds > 60% used  → DEADLINE if not already
  - Acceptance prob > 0.85 → lock current price, trigger approval

Each strategy is associated with a set of behavioral economics techniques
applied by the CounterProposalEngine when framing messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from .models import NegotiationStrategy, SessionState
from .supplier_behavior import BehavioralSignal, SupplierBehaviorProfile

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy metadata
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategyProfile:
    name:            NegotiationStrategy
    description:     str
    best_when:       list[str]
    avoid_when:      list[str]
    be_techniques:   list[str]    # behavioral economics techniques to apply
    expected_rounds: int
    expected_discount_pct: float  # typical % below initial ask


_STRATEGY_PROFILES: dict[NegotiationStrategy, StrategyProfile] = {
    NegotiationStrategy.COMPETITIVE: StrategyProfile(
        name            = NegotiationStrategy.COMPETITIVE,
        description     = "Anchor aggressively, concede slowly (Ackermann)",
        best_when       = ["multiple alternatives", "price gap > 10%", "non-strategic supplier"],
        avoid_when      = ["sole source", "tight deadline", "relationship critical"],
        be_techniques   = ["anchoring", "contrast_effect", "scarcity_counter"],
        expected_rounds = 6,
        expected_discount_pct = 0.12,
    ),
    NegotiationStrategy.COLLABORATIVE: StrategyProfile(
        name            = NegotiationStrategy.COLLABORATIVE,
        description     = "Expand the pie — trade non-price terms for price",
        best_when       = ["strategic long-term supplier", "relationship valued", "complex specs"],
        avoid_when      = ["commodity purchase", "budget under pressure", "many alternatives"],
        be_techniques   = ["reciprocity", "package_deal", "loss_aversion_framing"],
        expected_rounds = 4,
        expected_discount_pct = 0.08,
    ),
    NegotiationStrategy.COMPROMISING: StrategyProfile(
        name            = NegotiationStrategy.COMPROMISING,
        description     = "Split the difference — fair but leaves value on table",
        best_when       = ["time pressure", "small gap", "previous relationship"],
        avoid_when      = ["large gap > 15%", "supplier known to anchor high"],
        be_techniques   = ["fairness_appeal", "anchoring"],
        expected_rounds = 3,
        expected_discount_pct = 0.06,
    ),
    NegotiationStrategy.ACCOMMODATING: StrategyProfile(
        name            = NegotiationStrategy.ACCOMMODATING,
        description     = "Preserve relationship over price optimization",
        best_when       = ["sole source", "strategic supplier", "post-crisis repair"],
        avoid_when      = ["standard commodity", "many substitutes", "high price gap"],
        be_techniques   = ["relationship_appeal", "reciprocity"],
        expected_rounds = 2,
        expected_discount_pct = 0.03,
    ),
    NegotiationStrategy.HARDBALL: StrategyProfile(
        name            = NegotiationStrategy.HARDBALL,
        description     = "Extreme anchor, minimal concessions, long silences",
        best_when       = ["very large gap > 25%", "supplier known to pad heavily", "commodity"],
        avoid_when      = ["sole source", "urgent delivery", "relationship critical"],
        be_techniques   = ["anchoring", "silence", "deadline_pressure", "walkaway_threat"],
        expected_rounds = 8,
        expected_discount_pct = 0.18,
    ),
    NegotiationStrategy.DEADLINE: StrategyProfile(
        name            = NegotiationStrategy.DEADLINE,
        description     = "Create urgency — hold position then concede near deadline",
        best_when       = ["supplier deadline-sensitive", "rounds > 60% used", "stalled"],
        avoid_when      = ["supplier has no deadline pressure", "relationship critical"],
        be_techniques   = ["deadline_pressure", "scarcity", "loss_aversion_framing"],
        expected_rounds = 5,
        expected_discount_pct = 0.10,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Selection
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategySelectionResult:
    strategy:         NegotiationStrategy
    confidence:       float
    reasoning:        str
    alternatives:     list[tuple[NegotiationStrategy, float]]   # (strategy, score)
    be_techniques:    list[str]
    expected_rounds:  int
    expected_discount: float


class StrategySelectionEngine:
    """
    Scores all strategies against the current negotiation context
    and returns the highest-scoring one with reasoning.
    """

    def select(
        self,
        price_gap_pct:     float,       # (initial_ask - target) / initial_ask
        urgency_score:     float,       # 0=not urgent, 1=critical
        substitute_count:  int,         # alternative suppliers available
        relationship_score: float,      # 0=new, 1=strategic partner
        supplier_profile:  SupplierBehaviorProfile,
        is_sole_source:    bool = False,
        volume_strategic:  bool = False,
        days_to_deadline:  int | None = None,
    ) -> StrategySelectionResult:
        scores: dict[NegotiationStrategy, float] = {}
        reasons: dict[NegotiationStrategy, list[str]] = {s: [] for s in NegotiationStrategy}

        # ── COMPETITIVE ───────────────────────────────────────────────────────
        s = NegotiationStrategy.COMPETITIVE
        sc = 0.0
        if substitute_count >= 3:
            sc += 0.25; reasons[s].append(f"{substitute_count} alternatives available")
        if price_gap_pct > 0.10:
            sc += 0.20; reasons[s].append(f"price gap {price_gap_pct:.0%} > 10%")
        if relationship_score < 0.4:
            sc += 0.15; reasons[s].append("new/transactional supplier")
        if "ANCHORING_SUSCEPTIBLE" in supplier_profile.traits:
            sc += 0.15; reasons[s].append("supplier susceptible to anchoring")
        if is_sole_source:
            sc -= 0.30; reasons[s].append("PENALTY: sole source")
        scores[s] = sc

        # ── COLLABORATIVE ─────────────────────────────────────────────────────
        s = NegotiationStrategy.COLLABORATIVE
        sc = 0.0
        if relationship_score > 0.6:
            sc += 0.25; reasons[s].append("strategic relationship")
        if volume_strategic:
            sc += 0.15; reasons[s].append("high volume / strategic materials")
        if "RELATIONSHIP_DRIVEN" in supplier_profile.traits:
            sc += 0.20; reasons[s].append("supplier relationship-driven")
        if substitute_count < 2:
            sc += 0.10; reasons[s].append("limited alternatives")
        if urgency_score > 0.7:
            sc -= 0.15; reasons[s].append("PENALTY: urgency disfavors collaborative approach")
        scores[s] = sc

        # ── COMPROMISING ──────────────────────────────────────────────────────
        s = NegotiationStrategy.COMPROMISING
        sc = 0.0
        if price_gap_pct < 0.07:
            sc += 0.30; reasons[s].append(f"small gap {price_gap_pct:.0%}")
        if urgency_score > 0.5:
            sc += 0.15; reasons[s].append("time pressure favors quick resolution")
        if relationship_score > 0.3:
            sc += 0.10; reasons[s].append("existing relationship")
        scores[s] = sc

        # ── ACCOMMODATING ─────────────────────────────────────────────────────
        s = NegotiationStrategy.ACCOMMODATING
        sc = 0.0
        if is_sole_source:
            sc += 0.40; reasons[s].append("sole source — relationship preservation critical")
        if relationship_score > 0.8:
            sc += 0.20; reasons[s].append("top-tier strategic partner")
        if price_gap_pct < 0.05:
            sc += 0.10; reasons[s].append("small gap — not worth risking relationship")
        scores[s] = sc

        # ── HARDBALL ──────────────────────────────────────────────────────────
        s = NegotiationStrategy.HARDBALL
        sc = 0.0
        if price_gap_pct > 0.20:
            sc += 0.30; reasons[s].append(f"large gap {price_gap_pct:.0%} — suspect padding")
        if substitute_count >= 5:
            sc += 0.20; reasons[s].append("many alternatives — strong leverage")
        if "STUBBORN" in supplier_profile.traits:
            sc += 0.10; reasons[s].append("stubborn supplier — must match energy")
        if is_sole_source:
            sc -= 0.40; reasons[s].append("PENALTY: sole source")
        if urgency_score > 0.7:
            sc -= 0.25; reasons[s].append("PENALTY: urgency incompatible with hardball")
        scores[s] = sc

        # ── DEADLINE ──────────────────────────────────────────────────────────
        s = NegotiationStrategy.DEADLINE
        sc = 0.0
        if days_to_deadline is not None and days_to_deadline <= 14:
            sc += 0.35; reasons[s].append(f"deadline in {days_to_deadline} days")
        if "DEADLINE_SENSITIVE" in supplier_profile.traits:
            sc += 0.25; reasons[s].append("supplier deadline-sensitive")
        if urgency_score > 0.6:
            sc += 0.15; reasons[s].append("high urgency score")
        scores[s] = sc

        # ── Select ────────────────────────────────────────────────────────────
        best = max(scores, key=scores.get)
        best_score = scores[best]
        sorted_alts = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        profile_info = _STRATEGY_PROFILES[best]
        reasoning = (
            f"Selected {best.value} (score: {best_score:.2f}). "
            + " ".join(reasons[best])
        )

        logger.info(
            "strategy_selected",
            strategy=best.value,
            score=best_score,
            price_gap_pct=price_gap_pct,
            urgency=urgency_score,
            substitutes=substitute_count,
        )

        return StrategySelectionResult(
            strategy          = best,
            confidence        = min(1.0, best_score),
            reasoning         = reasoning,
            alternatives      = sorted_alts[1:4],
            be_techniques     = profile_info.be_techniques,
            expected_rounds   = profile_info.expected_rounds,
            expected_discount = profile_info.expected_discount_pct,
        )

    def should_adapt(
        self,
        current_strategy: NegotiationStrategy,
        round_number:     int,
        max_rounds:       int,
        signals:          list[BehavioralSignal],
        acceptance_prob:  float,
    ) -> tuple[NegotiationStrategy, str] | None:
        """
        Returns (new_strategy, reason) if mid-session adaptation is warranted,
        else None to keep current strategy.
        """
        signal_names = {s.signal for s in signals}

        # Plateau → switch to DEADLINE to create urgency
        if "NEAR_RESISTANCE_POINT" in signal_names and current_strategy != NegotiationStrategy.DEADLINE:
            return NegotiationStrategy.DEADLINE, "Supplier approaching resistance — switching to deadline pressure"

        # Supplier accelerating → we can be more COMPETITIVE
        if "ACCELERATING_CONCESSION" in signal_names and current_strategy == NegotiationStrategy.COLLABORATIVE:
            return NegotiationStrategy.COMPETITIVE, "Supplier conceding fast — extracting more value with competitive stance"

        # Final offer claim + high acceptance prob → call bluff with HARDBALL hold
        if "FINAL_OFFER_CLAIM" in signal_names and acceptance_prob > 0.65:
            if current_strategy not in (NegotiationStrategy.HARDBALL, NegotiationStrategy.DEADLINE):
                return NegotiationStrategy.HARDBALL, "Supplier claims final offer but acceptance probability is high — holding position"

        # Round budget running low
        round_pct = round_number / max_rounds
        if round_pct >= 0.65 and current_strategy not in (NegotiationStrategy.DEADLINE, NegotiationStrategy.COMPROMISING):
            return NegotiationStrategy.DEADLINE, f"Round {round_number}/{max_rounds} — switching to deadline pressure"

        return None   # no change
