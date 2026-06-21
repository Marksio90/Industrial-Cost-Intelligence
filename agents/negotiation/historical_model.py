"""
Section 3 — Historical Negotiation Model

Mines past negotiation outcomes to:
  1. Estimate ZOPA (Zone of Possible Agreement): [supplier_BATNA, our_BATNA]
  2. Find similar past negotiations via feature similarity
  3. Predict target price and walk-away price for new sessions
  4. Estimate round count and discount potential

Similarity features used for retrieval:
  - material_class + sub_class + grade
  - order_quantity (log-scaled)
  - supplier_country
  - season/month of negotiation
  - urgency_score
  - number of available substitutes

ZOPA Estimation:
  supplier_BATNA ≈ percentile-10 of historical settled prices for this material
  our_BATNA      ≈ market spot price + logistics + 5% margin
  ZOPA exists    ↔ supplier_BATNA < our_max_willingness_to_pay
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HistoricalCase:
    """One completed negotiation used as a training / retrieval example."""
    case_id:           str
    supplier_id:       str
    material_class:    str
    sub_class:         str
    grade:             str
    quantity:          float
    supplier_country:  str
    month:             int             # 1–12 for seasonality
    initial_ask:       float           # supplier's opening price
    settled_price:     float           # final agreed price
    our_target:        float
    walk_away:         float
    rounds_taken:      int
    strategy_used:     str
    result:            str             # "agreement" | "deadlock" | "withdrawn"
    discount_achieved: float           # (initial_ask - settled) / initial_ask


@dataclass
class ZOPAEstimate:
    """Zone of Possible Agreement."""
    supplier_batna:    float   # our estimate of their minimum acceptable price
    our_batna:         float   # maximum we're willing to pay (walk-away)
    zopa_exists:       bool
    zopa_lower:        float | None   # max(supplier_batna, market_floor)
    zopa_upper:        float | None   # min(our_batna, supplier_ceiling)
    confidence:        float
    basis:             str             # "historical" | "market" | "heuristic"


@dataclass
class SimilarCase:
    case:       HistoricalCase
    similarity: float   # 0–1, higher = more similar
    distance:   float


@dataclass
class HistoricalInsight:
    """Aggregated intelligence for a new negotiation."""
    # Price benchmarks
    median_settled_price:   float | None
    p10_settled_price:      float | None   # supplier floor estimate
    p90_settled_price:      float | None   # ceiling
    avg_discount_pct:       float | None
    avg_rounds:             float | None

    # Recommended targets
    recommended_target:     float | None
    recommended_walk_away:  float | None
    recommended_anchor:     float | None

    # ZOPA
    zopa:                   ZOPAEstimate | None

    # Strategy hint
    suggested_strategy:     str | None
    similar_cases:          list[SimilarCase] = field(default_factory=list)
    confidence:             float = 0.0

    # Narrative summary for the agent
    summary:                str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Historical Model
# ─────────────────────────────────────────────────────────────────────────────

class HistoricalNegotiationModel:
    """
    Retrieves and analyzes past negotiations to guide new sessions.

    In production: backed by the negotiation_outcomes PostgreSQL table.
    Accepts a list of HistoricalCase objects to allow testing without DB.
    """

    def __init__(self, cases: list[HistoricalCase] | None = None) -> None:
        self._cases: list[HistoricalCase] = cases or []

    def add_case(self, case: HistoricalCase) -> None:
        self._cases.append(case)

    def load_from_rows(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self._cases.append(HistoricalCase(**r))

    # ── Public interface ──────────────────────────────────────────────────────

    def analyze(
        self,
        material_class:   str,
        sub_class:        str,
        grade:            str,
        quantity:         float,
        supplier_country: str,
        market_price:     float | None = None,
        urgency_score:    float = 0.5,
        month:            int = 1,
    ) -> HistoricalInsight:
        similar = self._find_similar(
            material_class, sub_class, grade, quantity, supplier_country, month
        )

        if not similar:
            return self._heuristic_insight(market_price, urgency_score)

        # Weighted stats using similarity as weight
        settled  = [s.case.settled_price for s in similar]
        discounts = [s.case.discount_achieved for s in similar]
        rounds   = [s.case.rounds_taken    for s in similar]
        weights  = [s.similarity           for s in similar]

        w_median_price   = _weighted_percentile(settled, weights, 0.50)
        w_p10_price      = _weighted_percentile(settled, weights, 0.10)
        w_p90_price      = _weighted_percentile(settled, weights, 0.90)
        avg_discount     = _weighted_mean(discounts, weights)
        avg_rounds       = _weighted_mean(rounds, weights)

        # ZOPA estimation
        supplier_batna = w_p10_price or w_median_price or (market_price or 0) * 0.85
        our_batna      = (market_price or w_p90_price or supplier_batna * 1.25) * 1.10

        zopa = ZOPAEstimate(
            supplier_batna = supplier_batna,
            our_batna      = our_batna,
            zopa_exists    = supplier_batna < our_batna,
            zopa_lower     = supplier_batna if supplier_batna < our_batna else None,
            zopa_upper     = our_batna      if supplier_batna < our_batna else None,
            confidence     = min(1.0, len(similar) / 10),
            basis          = "historical",
        )

        # Anchor price: historically effective anchors are ~15-25% below initial ask
        # We target ~10% below our actual target to leave negotiation room
        target    = w_median_price or supplier_batna * 1.05
        walk_away = our_batna
        anchor    = target * 0.85   # 15% below target for anchor

        # Strategy hint from most common successful strategy in similar cases
        agreement_cases = [s.case for s in similar if s.case.result == "agreement"]
        strategy_counts: dict[str, int] = {}
        for c in agreement_cases:
            strategy_counts[c.strategy_used] = strategy_counts.get(c.strategy_used, 0) + 1
        suggested = max(strategy_counts, key=strategy_counts.get) if strategy_counts else "COLLABORATIVE"

        confidence = min(1.0, len(similar) / 5)
        summary    = self._build_summary(similar, avg_discount, avg_rounds, zopa, suggested)

        return HistoricalInsight(
            median_settled_price   = w_median_price,
            p10_settled_price      = w_p10_price,
            p90_settled_price      = w_p90_price,
            avg_discount_pct       = avg_discount,
            avg_rounds             = avg_rounds,
            recommended_target     = target,
            recommended_walk_away  = walk_away,
            recommended_anchor     = anchor,
            zopa                   = zopa,
            suggested_strategy     = suggested,
            similar_cases          = similar[:5],
            confidence             = confidence,
            summary                = summary,
        )

    def estimate_zopa(
        self,
        material_class: str,
        sub_class: str,
        grade: str,
        quantity: float,
        supplier_country: str,
        market_price: float | None = None,
    ) -> ZOPAEstimate:
        insight = self.analyze(material_class, sub_class, grade, quantity, supplier_country, market_price)
        if insight.zopa:
            return insight.zopa
        # Fallback: market-based ZOPA
        floor   = (market_price or 1.0) * 0.80
        ceiling = (market_price or 1.0) * 1.20
        return ZOPAEstimate(
            supplier_batna = floor,
            our_batna      = ceiling,
            zopa_exists    = True,
            zopa_lower     = floor,
            zopa_upper     = ceiling,
            confidence     = 0.2,
            basis          = "market",
        )

    # ── Similarity retrieval ──────────────────────────────────────────────────

    def _find_similar(
        self,
        material_class:   str,
        sub_class:        str,
        grade:            str,
        quantity:         float,
        supplier_country: str,
        month:            int,
        top_k:            int = 20,
    ) -> list[SimilarCase]:
        scored: list[SimilarCase] = []

        for c in self._cases:
            sim = self._similarity(c, material_class, sub_class, grade, quantity, supplier_country, month)
            if sim > 0.3:
                scored.append(SimilarCase(case=c, similarity=sim, distance=1.0 - sim))

        scored.sort(key=lambda x: x.similarity, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _similarity(
        case:            HistoricalCase,
        material_class:  str,
        sub_class:       str,
        grade:           str,
        quantity:        float,
        country:         str,
        month:           int,
    ) -> float:
        score = 0.0

        # Material match (most important)
        if case.material_class == material_class:
            score += 0.35
            if case.sub_class == sub_class:
                score += 0.20
                if case.grade == grade:
                    score += 0.15

        # Country match
        if case.supplier_country == country:
            score += 0.10

        # Quantity similarity (log scale)
        if case.quantity > 0 and quantity > 0:
            log_diff = abs(math.log(case.quantity) - math.log(quantity))
            qty_sim  = max(0.0, 1.0 - log_diff / 4)   # 4 = ~55x difference → 0
            score   += qty_sim * 0.12

        # Seasonal proximity
        month_diff = min(abs(case.month - month), 12 - abs(case.month - month))
        score += max(0.0, 1.0 - month_diff / 6) * 0.08

        return min(1.0, score)

    # ── Heuristic fallback ────────────────────────────────────────────────────

    @staticmethod
    def _heuristic_insight(
        market_price:  float | None,
        urgency_score: float,
    ) -> HistoricalInsight:
        mp = market_price or 1.0
        zopa = ZOPAEstimate(
            supplier_batna = mp * 0.82,
            our_batna      = mp * 1.15,
            zopa_exists    = True,
            zopa_lower     = mp * 0.82,
            zopa_upper     = mp * 1.15,
            confidence     = 0.15,
            basis          = "heuristic",
        )
        return HistoricalInsight(
            median_settled_price  = mp * 0.93,
            p10_settled_price     = mp * 0.82,
            p90_settled_price     = mp * 1.05,
            avg_discount_pct      = 0.07,
            avg_rounds            = 4.0,
            recommended_target    = mp * 0.92,
            recommended_walk_away = mp * 1.10,
            recommended_anchor    = mp * 0.78,
            zopa                  = zopa,
            suggested_strategy    = "COLLABORATIVE" if urgency_score < 0.6 else "COMPETITIVE",
            similar_cases         = [],
            confidence            = 0.15,
            summary               = "No historical data for this material/supplier combination. Using market-based heuristics.",
        )

    @staticmethod
    def _build_summary(
        similar:       list[SimilarCase],
        avg_discount:  float | None,
        avg_rounds:    float | None,
        zopa:          ZOPAEstimate,
        strategy:      str,
    ) -> str:
        n = len(similar)
        disc = f"{(avg_discount or 0) * 100:.1f}%"
        rnd  = f"{avg_rounds:.1f}" if avg_rounds else "?"
        zopa_str = (
            f"ZOPA exists between {zopa.zopa_lower:.4f} and {zopa.zopa_upper:.4f}"
            if zopa.zopa_exists and zopa.zopa_lower and zopa.zopa_upper
            else "No historical ZOPA identified"
        )
        return (
            f"Found {n} similar past negotiations. "
            f"Average discount achieved: {disc} in {rnd} rounds. "
            f"{zopa_str}. Recommended strategy: {strategy}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Weighted statistics helpers
# ─────────────────────────────────────────────────────────────────────────────

def _weighted_mean(values: list[float], weights: list[float]) -> float | None:
    if not values or not weights:
        return None
    total_w = sum(weights)
    if total_w == 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _weighted_percentile(
    values:  list[float],
    weights: list[float],
    pct:     float,
) -> float | None:
    if not values:
        return None
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return None
    cumulative = 0.0
    target     = pct * total_w
    for v, w in pairs:
        cumulative += w
        if cumulative >= target:
            return v
    return pairs[-1][0]
