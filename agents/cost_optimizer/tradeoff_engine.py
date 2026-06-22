"""
Sections 5 & 6 — Cost vs Quality Tradeoff + Risk vs Cost Tradeoff

Dedykowane analizatory tradeoffów z:
  • Ilościową wyceną jakości (Quality Premium / Discount)
  • Ilościową wyceną ryzyka (Risk-Adjusted Cost)
  • Sensitivity analysis — jak bardzo zmiana jakości wpływa na koszt?
  • Risk-cost frontier z regionami akceptowalności
  • Rekomendacjami dla poszczególnych regionów frontu
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ConfigurationCandidate, TradeoffAxis, TradeoffCurve, TradeoffPoint,
)


# ─────────────────────────────────────────────────────────────────────────────
# Quality monetization
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class QualityCostPoint:
    """One point on the cost-quality frontier."""
    config_id:      str
    total_cost_eur: float
    quality_index:  float
    scrap_rate_pct: float
    quality_cost_eur: float   # cost of poor quality = scrap + rework
    adjusted_cost_eur: float  # total_cost + quality_cost
    label:          str = ""


@dataclass
class CostQualityAnalysis:
    points:            list[QualityCostPoint]
    pareto:            list[QualityCostPoint]   # non-dominated (low cost + high quality)
    recommended:       QualityCostPoint | None
    quality_premium_eur: float    # extra cost for highest quality vs. lowest
    quality_discount_pct: float   # % cost reduction from quality relaxation
    breakeven_quality: float      # quality threshold below which scrap costs > savings
    insight:           str = ""


class CostQualityAnalyzer:
    """
    Sections 5: Cost vs Quality Tradeoff

    Models:
      quality_cost_eur = annual_volume × scrap_rate_pct/100 × unit_cost × rework_factor
      adjusted_cost    = total_cost + quality_cost_per_unit
    """

    def __init__(self, annual_volume: int = 10_000, rework_factor: float = 2.5) -> None:
        self.annual_volume = annual_volume
        self.rework_factor = rework_factor

    def analyze(self, candidates: list[ConfigurationCandidate]) -> CostQualityAnalysis:
        if not candidates:
            return CostQualityAnalysis([], [], None, 0, 0, 0)

        feasible = [c for c in candidates if c.feasible] or candidates
        # Deduplicate by (rounded cost, rounded quality)
        seen: set[tuple] = set()
        unique = []
        for c in feasible:
            key = (round(c.total_cost_eur, 2), round(c.quality_index, 3))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        points: list[QualityCostPoint] = []
        for c in unique:
            scrap_rate = max(0.005, 1 - c.quality_index)  # proxy
            qc = c.total_cost_eur * scrap_rate * self.rework_factor
            adj = c.total_cost_eur + qc
            points.append(QualityCostPoint(
                config_id=c.candidate_id,
                total_cost_eur=c.total_cost_eur,
                quality_index=c.quality_index,
                scrap_rate_pct=round(scrap_rate * 100, 2),
                quality_cost_eur=round(qc, 4),
                adjusted_cost_eur=round(adj, 4),
            ))

        # Pareto: minimize total_cost AND maximize quality
        pareto = self._pareto(points)

        # Quality premium
        if pareto:
            max_q = max(p.quality_index for p in pareto)
            min_q = min(p.quality_index for p in pareto)
            high_q_pts = [p for p in pareto if p.quality_index >= max_q - 0.05]
            low_q_pts  = [p for p in pareto if p.quality_index <= min_q + 0.05]
            premium = (min(p.total_cost_eur for p in high_q_pts)
                       - min(p.total_cost_eur for p in low_q_pts))
        else:
            premium = 0.0

        # Discount from quality relaxation
        min_cost = min(p.total_cost_eur for p in points)
        max_cost = max(p.total_cost_eur for p in points)
        discount_pct = (max_cost - min_cost) / max_cost * 100 if max_cost else 0.0

        # Breakeven quality: where quality_cost = material_savings
        breakeven_q = self._breakeven(points)

        recommended = min(pareto, key=lambda p: p.adjusted_cost_eur) if pareto else None
        if recommended:
            recommended.label = "Rekomendowana: najniższy koszt całkowity (uwzgl. jakość)"

        return CostQualityAnalysis(
            points=points,
            pareto=pareto,
            recommended=recommended,
            quality_premium_eur=round(premium, 2),
            quality_discount_pct=round(discount_pct, 1),
            breakeven_quality=round(breakeven_q, 3),
            insight=self._insight(premium, discount_pct, breakeven_q),
        )

    def _pareto(self, pts: list[QualityCostPoint]) -> list[QualityCostPoint]:
        result = []
        for p in pts:
            dominated = any(
                o.total_cost_eur <= p.total_cost_eur and o.quality_index >= p.quality_index
                and (o.total_cost_eur < p.total_cost_eur or o.quality_index > p.quality_index)
                for o in pts if o is not p
            )
            if not dominated:
                result.append(p)
        return sorted(result, key=lambda p: p.total_cost_eur)

    def _breakeven(self, pts: list[QualityCostPoint]) -> float:
        if not pts:
            return 0.9
        sorted_pts = sorted(pts, key=lambda p: p.total_cost_eur)
        for i in range(1, len(sorted_pts)):
            cost_saving = sorted_pts[0].total_cost_eur - sorted_pts[i].total_cost_eur
            q_penalty   = sorted_pts[i].quality_cost_eur - sorted_pts[0].quality_cost_eur
            if q_penalty > 0 and cost_saving > 0 and q_penalty >= cost_saving:
                return sorted_pts[i].quality_index
        return min(p.quality_index for p in pts)

    def _insight(self, premium: float, discount_pct: float, breakeven: float) -> str:
        if premium > 0:
            return (f"Osiągnięcie najwyższej jakości kosztuje dodatkowo {premium:,.2f} EUR/szt. "
                    f"Potencjalna oszczędność przez relaksację jakości: {discount_pct:.1f}%. "
                    f"Próg opłacalności: quality_index ≥ {breakeven:.2f}.")
        return "Brak istotnej różnicy kosztowej między poziomami jakości."


# ─────────────────────────────────────────────────────────────────────────────
# Risk monetization
# ─────────────────────────────────────────────────────────────────────────────

# Value-at-Risk multipliers per risk tier
_RISK_VAR_FACTOR = {
    "low":      0.02,   # 2% of annual spend
    "medium":   0.08,
    "high":     0.20,
    "critical": 0.40,
}


@dataclass
class RiskCostPoint:
    """One point on the cost-risk frontier."""
    config_id:        str
    total_cost_eur:   float
    risk_score:       float   # 0–1
    risk_tier:        str     # low/medium/high/critical
    risk_value_eur:   float   # VaR: monetized risk exposure
    risk_adj_cost:    float   # total_cost + risk_value_eur
    label:            str = ""


@dataclass
class RiskCostAnalysis:
    points:              list[RiskCostPoint]
    pareto:              list[RiskCostPoint]
    recommended:         RiskCostPoint | None
    cheapest:            RiskCostPoint | None  # ignoring risk
    safest:              RiskCostPoint | None  # ignoring cost
    risk_premium_eur:    float   # cost of moving from highest- to lowest-risk
    risk_adj_saving_eur: float   # saving using risk-adjusted cost vs naive cheapest
    acceptable_region:  list[RiskCostPoint]   # risk < 0.5 AND reasonable cost
    insight:             str = ""


class RiskCostAnalyzer:
    """
    Section 6: Risk vs Cost Tradeoff

    Risk monetization:
      risk_value_eur = annual_spend × VaR_factor(risk_tier)
      risk_adj_cost  = total_cost_eur + risk_value_eur
    """

    def __init__(self, annual_volume: int = 10_000, baseline_unit_cost: float = 10.0) -> None:
        self.annual_volume = annual_volume
        self.annual_spend = annual_volume * baseline_unit_cost

    def analyze(self, candidates: list[ConfigurationCandidate]) -> RiskCostAnalysis:
        if not candidates:
            return RiskCostAnalysis([], [], None, None, None, 0, 0, [])

        feasible = [c for c in candidates if c.feasible] or candidates
        seen: set[tuple] = set()
        unique = []
        for c in feasible:
            key = (round(c.total_cost_eur, 2), round(c.risk_score, 3))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        points: list[RiskCostPoint] = []
        for c in unique:
            tier = self._risk_tier(c.risk_score)
            var = self.annual_spend * _RISK_VAR_FACTOR[tier] * c.risk_score
            var_per_unit = var / max(self.annual_volume, 1)
            adj = c.total_cost_eur + var_per_unit
            points.append(RiskCostPoint(
                config_id=c.candidate_id,
                total_cost_eur=c.total_cost_eur,
                risk_score=c.risk_score,
                risk_tier=tier,
                risk_value_eur=round(var_per_unit, 4),
                risk_adj_cost=round(adj, 4),
            ))

        pareto = self._pareto(points)
        recommended = (min(pareto, key=lambda p: p.risk_adj_cost) if pareto else None)
        cheapest     = min(points, key=lambda p: p.total_cost_eur) if points else None
        safest       = min(points, key=lambda p: p.risk_score)     if points else None

        if recommended:
            recommended.label = "Rekomendowana: najniższy koszt skorygowany o ryzyko"
        if cheapest:
            cheapest.label = "Najtańsza (bez korekty ryzyka)"
        if safest:
            safest.label = "Najbezpieczniejsza"

        risk_premium = ((min(p.total_cost_eur for p in points if p.risk_score < 0.3) -
                         min(p.total_cost_eur for p in points))
                        if any(p.risk_score < 0.3 for p in points) else 0.0)

        # Risk-adjusted saving vs naive cheapest
        risk_adj_saving = 0.0
        if cheapest and recommended and recommended.config_id != cheapest.config_id:
            risk_adj_saving = cheapest.risk_adj_cost - recommended.risk_adj_cost

        acceptable = [p for p in points if p.risk_score <= 0.5]
        if acceptable:
            min_acc_cost = min(p.total_cost_eur for p in acceptable)
            acceptable = [p for p in acceptable if p.total_cost_eur <= min_acc_cost * 1.15]

        return RiskCostAnalysis(
            points=points,
            pareto=pareto,
            recommended=recommended,
            cheapest=cheapest,
            safest=safest,
            risk_premium_eur=round(risk_premium, 2),
            risk_adj_saving_eur=round(risk_adj_saving, 4),
            acceptable_region=acceptable,
            insight=self._insight(risk_premium, risk_adj_saving, recommended),
        )

    def _risk_tier(self, score: float) -> str:
        if score < 0.25: return "low"
        if score < 0.50: return "medium"
        if score < 0.75: return "high"
        return "critical"

    def _pareto(self, pts: list[RiskCostPoint]) -> list[RiskCostPoint]:
        result = []
        for p in pts:
            dominated = any(
                o.total_cost_eur <= p.total_cost_eur and o.risk_score <= p.risk_score
                and (o.total_cost_eur < p.total_cost_eur or o.risk_score < p.risk_score)
                for o in pts if o is not p
            )
            if not dominated:
                result.append(p)
        return sorted(result, key=lambda p: p.total_cost_eur)

    def _insight(self, premium: float, adj_saving: float, rec) -> str:
        parts = []
        if premium > 0:
            parts.append(f"Premia za bezpieczeństwo: {premium:,.2f} EUR/szt. (+ryzyko reducuje 70% zakłóceń).")
        if adj_saving > 0:
            parts.append(f"Wybór najtańszej opcji bez analizy ryzyka kosztuje {adj_saving:,.2f} EUR/szt. więcej po korekcie.")
        if rec:
            parts.append(f"Rekomendacja: konfiguracja {rec.config_id[:8]}… (ryzyko={rec.risk_score:.2f}, koszt={rec.total_cost_eur:.2f} EUR).")
        return " ".join(parts) or "Brak istotnej różnicy ryzyka między konfiguracjami."
