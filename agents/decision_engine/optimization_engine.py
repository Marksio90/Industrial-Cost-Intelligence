"""
Optimization Engine — multi-objective portfolio optimizer for recommendations.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .models import Recommendation, UrgencyLevel


@dataclass
class ParetoPoint:
    saving_eur: float
    risk_score: float
    recs: list[str] = field(default_factory=list)  # rec_ids


@dataclass
class PortfolioResult:
    selected: list[Recommendation]
    total_saving_eur: float
    total_impl_cost_eur: float
    net_benefit_eur: float
    avg_confidence: float
    pareto_frontier: list[ParetoPoint]
    run_time_ms: float


_URGENCY_SCORE = {
    UrgencyLevel.CRITICAL: 1.0,
    UrgencyLevel.HIGH:     0.8,
    UrgencyLevel.MEDIUM:   0.6,
    UrgencyLevel.LOW:      0.4,
    UrgencyLevel.WATCH:    0.2,
}

_RISK_NUM = {"NISKIE": 0.1, "ŚREDNIE": 0.3, "WYSOKIE": 0.6, "KRYTYCZNE": 0.9}


def _risk_score(rec: Recommendation) -> float:
    base = _RISK_NUM.get(rec.risk_level, 0.3)
    return base * (1 - rec.confidence.overall * 0.3)


class MultiObjectiveOptimizer:
    def optimize(
        self,
        recommendations: list[Recommendation],
        budget_eur: float = 1_000_000,
        max_recs: int = 5,
        risk_budget: float = 2.0,
        weights: dict[str, float] | None = None,
    ) -> PortfolioResult:
        t0 = time.time()
        w = weights or {"saving": 0.5, "confidence": 0.3, "urgency": 0.2}

        feasible = [r for r in recommendations if r.implementation_cost_eur <= budget_eur]
        if not feasible:
            return PortfolioResult([], 0, 0, 0, 0, [], 0)

        max_saving = max((r.expected_saving_eur for r in feasible), default=1) or 1

        def score(r: Recommendation) -> float:
            norm_saving = r.expected_saving_eur / max_saving
            return (w["saving"] * norm_saving
                    + w["confidence"] * r.confidence.overall
                    + w["urgency"] * _URGENCY_SCORE.get(r.urgency, 0.5))

        scored = sorted(feasible, key=score, reverse=True)

        selected, total_cost, total_risk = [], 0.0, 0.0
        for r in scored:
            if len(selected) >= max_recs:
                break
            cost = r.implementation_cost_eur
            risk = _risk_score(r)
            if total_cost + cost <= budget_eur and total_risk + risk <= risk_budget:
                selected.append(r)
                total_cost += cost
                total_risk += risk

        total_saving = sum(r.expected_saving_eur for r in selected)
        avg_conf = sum(r.confidence.overall for r in selected) / max(len(selected), 1)
        pareto = self._build_pareto(feasible)

        return PortfolioResult(
            selected=selected,
            total_saving_eur=round(total_saving, 2),
            total_impl_cost_eur=round(total_cost, 2),
            net_benefit_eur=round(total_saving - total_cost, 2),
            avg_confidence=round(avg_conf, 4),
            pareto_frontier=pareto,
            run_time_ms=round((time.time() - t0) * 1000, 2),
        )

    def _build_pareto(self, recs: list[Recommendation], max_points: int = 20) -> list[ParetoPoint]:
        if not recs:
            return []
        step = max(1, len(recs) // max_points)
        points = [
            ParetoPoint(saving_eur=recs[i].expected_saving_eur,
                        risk_score=_risk_score(recs[i]),
                        recs=[recs[i].rec_id])
            for i in range(0, len(recs), step)
        ]
        # Remove dominated points (higher risk AND lower saving)
        dominated = set()
        for i, a in enumerate(points):
            for j, b in enumerate(points):
                if i == j:
                    continue
                if (b.saving_eur >= a.saving_eur and b.risk_score <= a.risk_score
                        and (b.saving_eur > a.saving_eur or b.risk_score < a.risk_score)):
                    dominated.add(i)
        return [p for i, p in enumerate(points) if i not in dominated]
