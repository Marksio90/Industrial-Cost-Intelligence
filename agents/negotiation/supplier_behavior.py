"""
Section 2 — Supplier Behavior Model

Builds a behavioral fingerprint for each supplier from historical negotiations.
Applies behavioral economics concepts:

  Anchoring          — how much does the supplier adjust from our first price?
  Loss Aversion      — do they fight harder to avoid a "loss" vs gain a "gain"?
  Reciprocity        — do they match our concession speed/size?
  Deadline Effect    — do they concede faster as deadline approaches?
  Concession Pattern — monotone decreasing? erratic? plateau?

The model outputs:
  - SupplierBehaviorProfile: quantified traits + elasticity scores
  - accept_probability(price, round, context) → float
  - concession_forecast(price_history) → next expected concession

ML backend: LightGBM classifier trained on historical round outcomes.
Falls back to heuristic rule engine when <10 negotiation samples exist.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Optional ML dependency — graceful degradation
try:
    import lightgbm as lgb
    import numpy as np
    HAS_LGB = True
except ImportError:
    HAS_LGB = False


# ─────────────────────────────────────────────────────────────────────────────
# Behavioral signals detected per round
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BehavioralSignal:
    signal:      str
    confidence:  float     # 0–1
    description: str
    evidence:    list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Supplier Behavior Profile (runtime, not ORM)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SupplierBehaviorProfile:
    supplier_id:   str

    # Concession dynamics
    avg_concession_rate:     float | None = None   # % per round
    concession_velocity:     float | None = None   # slope of concession curve
    concession_consistency:  float | None = None   # std dev of concession sizes

    # Behavioral traits (see SupplierTrait enum)
    traits: list[str] = field(default_factory=list)

    # Elasticity
    price_elasticity:  float | None = None   # < -1 = elastic, > -1 = inelastic
    volume_sensitivity: float | None = None

    # Anchoring
    anchor_resistance: float = 0.5   # 0=fully follows anchor, 1=ignores it

    # Deadline
    deadline_sensitivity: float = 0.5   # 0=ignores, 1=strong deadline effect

    # Reciprocity
    reciprocity_ratio: float | None = None   # their concession / our concession

    # Track record
    total_negotiations:    int   = 0
    agreement_rate:        float | None = None
    avg_discount_achieved: float | None = None   # % below initial ask
    avg_rounds_to_close:   float | None = None

    # Data confidence
    data_points:  int = 0
    confidence:   float = 0.0   # 0=heuristic only, 1=well-fitted model

    def is_high_confidence(self) -> bool:
        return self.data_points >= 10 and self.confidence >= 0.6

    def dominant_trait(self) -> str | None:
        return self.traits[0] if self.traits else None


# ─────────────────────────────────────────────────────────────────────────────
# Historical round datapoint
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HistoricalRound:
    round_number:    int
    our_price:       float
    supplier_price:  float
    initial_ask:     float
    target_price:    float
    outcome:         str           # ACCEPTED | REJECTED | COUNTER | WITHDRAWN
    days_since_start: int
    days_to_deadline: int | None


# ─────────────────────────────────────────────────────────────────────────────
# Behavior Analyzer
# ─────────────────────────────────────────────────────────────────────────────

class SupplierBehaviorAnalyzer:
    """
    Analyzes historical rounds to build / update a SupplierBehaviorProfile.
    Used by the Behavior Model Service on every completed negotiation.
    """

    def analyze(
        self,
        supplier_id: str,
        history: list[HistoricalRound],
        existing: SupplierBehaviorProfile | None = None,
    ) -> SupplierBehaviorProfile:
        profile = existing or SupplierBehaviorProfile(supplier_id=supplier_id)
        profile.data_points = len(history)

        if not history:
            return profile

        profile.total_negotiations += 1

        # ── Concession analysis ───────────────────────────────────────────────
        supplier_prices  = [r.supplier_price for r in history]
        our_prices       = [r.our_price      for r in history]
        concessions = []
        for i in range(1, len(supplier_prices)):
            delta = supplier_prices[i - 1] - supplier_prices[i]
            concessions.append(delta / supplier_prices[0] if supplier_prices[0] else 0)

        if concessions:
            profile.avg_concession_rate    = statistics.mean(concessions)
            profile.concession_consistency = statistics.stdev(concessions) if len(concessions) > 1 else 0.0
            # Velocity: linear regression slope of concession magnitudes
            profile.concession_velocity = _linear_slope(list(range(len(concessions))), concessions)

        # ── Anchoring resistance ──────────────────────────────────────────────
        if history:
            initial_gap  = history[0].initial_ask - history[0].our_price
            response_gap = history[0].supplier_price - history[0].our_price if len(history) > 1 else initial_gap
            profile.anchor_resistance = min(1.0, response_gap / initial_gap) if initial_gap != 0 else 0.5

        # ── Reciprocity ───────────────────────────────────────────────────────
        our_concessions = []
        for i in range(1, len(our_prices)):
            d = our_prices[i] - our_prices[i - 1]
            our_concessions.append(d / our_prices[0] if our_prices[0] else 0)

        if concessions and our_concessions:
            pairs = list(zip(our_concessions, concessions))
            profile.reciprocity_ratio = (
                statistics.mean(t[1] / t[0] for t in pairs if t[0] != 0) if pairs else None
            )

        # ── Agreement rate ────────────────────────────────────────────────────
        agreed = sum(1 for r in history if r.outcome == "ACCEPTED")
        profile.agreement_rate = agreed / len(history) if history else None

        # ── Discount achieved ─────────────────────────────────────────────────
        if history and history[-1].outcome == "ACCEPTED":
            discount = (history[0].initial_ask - history[-1].supplier_price) / history[0].initial_ask
            profile.avg_discount_achieved = (
                ((profile.avg_discount_achieved or discount) + discount) / 2
            )

        # ── Deadline sensitivity ──────────────────────────────────────────────
        deadline_rounds = [
            r for r in history
            if r.days_to_deadline is not None and r.days_to_deadline <= 7
        ]
        if deadline_rounds:
            # Check if concessions increase near deadline
            near_concessions = [supplier_prices[i - 1] - supplier_prices[i]
                                for i, r in enumerate(history[1:], 1)
                                if r in deadline_rounds]
            if near_concessions and concessions:
                profile.deadline_sensitivity = min(
                    1.0, statistics.mean(near_concessions) / (statistics.mean([abs(c) for c in concessions]) + 1e-9)
                )

        # ── Traits classification ──────────────────────────────────────────────
        profile.traits = self._classify_traits(profile, history)

        # ── Confidence ────────────────────────────────────────────────────────
        profile.confidence = min(1.0, profile.data_points / 20)
        return profile

    def _classify_traits(
        self,
        p: SupplierBehaviorProfile,
        history: list[HistoricalRound],
    ) -> list[str]:
        traits: list[str] = []

        if p.anchor_resistance is not None and p.anchor_resistance < 0.35:
            traits.append("ANCHORING_SUSCEPTIBLE")

        if p.avg_concession_rate is not None and p.avg_concession_rate > 0.04:
            traits.append("FAST_CONCEDER")
        elif p.avg_concession_rate is not None and p.avg_concession_rate < 0.01:
            traits.append("STUBBORN")

        if p.deadline_sensitivity is not None and p.deadline_sensitivity > 0.7:
            traits.append("DEADLINE_SENSITIVE")

        if p.reciprocity_ratio is not None and 0.7 < p.reciprocity_ratio < 1.3:
            traits.append("RECIPROCAL")

        # Volume sensitivity: check if higher volume proposals got better prices
        if any(r.supplier_price / r.initial_ask < 0.92 for r in history if r.outcome == "ACCEPTED"):
            traits.append("PRICE_SENSITIVE")

        if p.agreement_rate is not None and p.agreement_rate > 0.75 and p.avg_discount_achieved is not None and p.avg_discount_achieved < 0.03:
            traits.append("RELATIONSHIP_DRIVEN")

        return traits[:5]   # cap at 5 most relevant


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance Probability Estimator
# ─────────────────────────────────────────────────────────────────────────────

class AcceptanceProbabilityEstimator:
    """
    Predicts P(supplier accepts | our_price, context).

    Features:
      - relative_price:    (our_price - initial_ask) / initial_ask
      - round_number:      normalized to [0,1] vs max_rounds
      - concession_rate:   supplier's rate in this session
      - days_to_deadline:  normalized urgency
      - trait_anchoring:   binary
      - trait_deadline:    binary
      - market_position:   substitute_count (normalized)
      - total_gap_pct:     (initial_ask - our_target) / initial_ask

    ML model: LightGBM binary classifier.
    Heuristic fallback: logistic regression on relative_price only.
    """

    def __init__(self, model: Any = None) -> None:
        self._model = model   # LightGBM Booster or None

    def predict(
        self,
        our_price:        float,
        initial_ask:      float,
        target_price:     float,
        round_number:     int,
        max_rounds:       int,
        profile:          SupplierBehaviorProfile,
        days_to_deadline: int | None = None,
        current_supplier_price: float | None = None,
    ) -> float:
        """Returns probability ∈ [0, 1] that supplier accepts this price."""

        relative_price = (our_price - initial_ask) / initial_ask if initial_ask else 0
        round_pct      = round_number / max(max_rounds, 1)
        urgency        = 1.0 - min(1.0, (days_to_deadline or 30) / 30) if days_to_deadline else 0.5

        # Gap between current supplier price and our offer
        gap_pct = 0.0
        if current_supplier_price and current_supplier_price > 0:
            gap_pct = (current_supplier_price - our_price) / current_supplier_price

        if self._model is not None and HAS_LGB:
            features = np.array([[
                relative_price,
                round_pct,
                urgency,
                gap_pct,
                profile.avg_concession_rate or 0.02,
                profile.anchor_resistance,
                profile.deadline_sensitivity,
                float("ANCHORING_SUSCEPTIBLE" in profile.traits),
                float("DEADLINE_SENSITIVE"     in profile.traits),
                float("STUBBORN"               in profile.traits),
                float("FAST_CONCEDER"          in profile.traits),
                min(1.0, (profile.total_negotiations or 1) / 50),
            ]])
            return float(self._model.predict(features)[0])

        # ── Heuristic fallback ────────────────────────────────────────────────
        # Base: logistic function of price gap
        # P = σ(k * (gap - threshold))
        threshold = 0.02   # supplier typically accepts when gap < 2%
        k = 8.0            # steepness

        base_prob = _sigmoid(k * (threshold - gap_pct))

        # Adjust for urgency (deadline pressure increases acceptance)
        base_prob += urgency * profile.deadline_sensitivity * 0.1

        # Adjust for round (later rounds → more pressure on both sides)
        base_prob += round_pct * 0.05

        # Adjust for traits
        if "FAST_CONCEDER" in profile.traits:
            base_prob += 0.08
        if "STUBBORN" in profile.traits:
            base_prob -= 0.1

        return max(0.0, min(1.0, base_prob))


# ─────────────────────────────────────────────────────────────────────────────
# Real-time signal detector (used during active rounds)
# ─────────────────────────────────────────────────────────────────────────────

class RoundSignalDetector:
    """
    Detects behavioral signals from a supplier's latest response.
    Called after each incoming counter-offer.
    """

    def detect(
        self,
        round_history: list[dict[str, Any]],
        profile: SupplierBehaviorProfile,
    ) -> list[BehavioralSignal]:
        signals: list[BehavioralSignal] = []

        if len(round_history) < 2:
            return signals

        prices = [r["supplier_price"] for r in round_history if r.get("supplier_price")]
        if len(prices) < 2:
            return signals

        last_concession = prices[-2] - prices[-1]
        avg_concession  = (prices[0] - prices[-1]) / max(len(prices) - 1, 1)

        # Plateau detection — concession shrinking fast
        if last_concession < avg_concession * 0.3:
            signals.append(BehavioralSignal(
                signal="NEAR_RESISTANCE_POINT",
                confidence=0.75,
                description="Supplier concession rate dropped by >70% — approaching BATNA",
                evidence=[f"Last concession: {last_concession:.4f}, avg: {avg_concession:.4f}"],
            ))

        # Acceleration — supplier suddenly conceding more
        if last_concession > avg_concession * 2.0:
            signals.append(BehavioralSignal(
                signal="ACCELERATING_CONCESSION",
                confidence=0.80,
                description="Supplier concession rate accelerated — may be under time pressure",
                evidence=[f"Last concession: {last_concession:.4f} vs avg {avg_concession:.4f}"],
            ))

        # Suspiciously round numbers (psychological anchoring by supplier)
        lp = round_history[-1].get("supplier_price", 0)
        if lp % 0.05 < 0.001 or lp % 0.10 < 0.001:
            signals.append(BehavioralSignal(
                signal="ROUND_NUMBER_ANCHOR",
                confidence=0.60,
                description="Supplier is anchoring on a round price — psychological floor",
                evidence=[f"Price: {lp}"],
            ))

        # Message sentiment hints (simple keyword detection)
        msg = (round_history[-1].get("message") or "").lower()
        if any(w in msg for w in ("final offer", "best price", "cannot go lower", "last offer")):
            signals.append(BehavioralSignal(
                signal="FINAL_OFFER_CLAIM",
                confidence=0.65,
                description="Supplier claims this is final — but historical data shows they often concede further",
                evidence=[f"Message contains final-offer language"],
            ))

        if any(w in msg for w in ("urgent", "capacity", "other customers", "popular", "running out")):
            signals.append(BehavioralSignal(
                signal="SCARCITY_PRESSURE",
                confidence=0.55,
                description="Supplier applying artificial scarcity pressure",
                evidence=[f"Scarcity keywords in message"],
            ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _linear_slope(x: list[float], y: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    n   = len(x)
    sx  = sum(x); sy = sum(y)
    sxx = sum(xi * xi for xi in x)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    denom = n * sxx - sx * sx
    return (n * sxy - sx * sy) / denom if denom else 0.0
