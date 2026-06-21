"""
Section 4 — Multi-Objective Optimization

Implementacja NSGA-II inspired Pareto dominance:
  • Pareto dominance check (o1 dominates o2 if better on all objectives)
  • Non-dominated sorting (Rank 1 = Pareto front)
  • Crowding distance (diversity preservation)
  • Knee point detection (balanced optimal choice)
  • Tradeoff curve generation for 2D slices
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ConfigurationCandidate, ObjectiveType,
    TradeoffAxis, TradeoffCurve, TradeoffPoint,
)


# ─────────────────────────────────────────────────────────────────────────────
# Objective extraction
# ─────────────────────────────────────────────────────────────────────────────

def _obj_values(c: ConfigurationCandidate, objectives: list[ObjectiveType]) -> list[float]:
    """Extract objective values. All are to-be-minimized (negate maximize objectives)."""
    vals = []
    for obj in objectives:
        if obj == ObjectiveType.MINIMIZE_COST:
            vals.append(c.total_cost_eur)
        elif obj == ObjectiveType.MINIMIZE_RISK:
            vals.append(c.risk_score)
        elif obj == ObjectiveType.MAXIMIZE_QUALITY:
            vals.append(-c.quality_index)   # negate: maximize = minimize negative
        elif obj == ObjectiveType.MAXIMIZE_DELIVERY:
            vals.append(-1.0 / (c.delivery_days + 1))  # negate
        elif obj == ObjectiveType.MINIMIZE_CO2:
            vals.append(c.co2_kg)
        else:
            vals.append(0.0)
    return vals


def _dominates(a_vals: list[float], b_vals: list[float]) -> bool:
    """Returns True if a dominates b (a ≤ b on all, a < b on at least one)."""
    at_least_one_better = False
    for av, bv in zip(a_vals, b_vals):
        if av > bv:
            return False
        if av < bv:
            at_least_one_better = True
    return at_least_one_better


# ─────────────────────────────────────────────────────────────────────────────
# Non-dominated Sorting (NSGA-II)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NSGAResult:
    fronts: list[list[ConfigurationCandidate]]   # front[0] = Pareto front
    ranks:  dict[str, int]                        # candidate_id → rank


def non_dominated_sort(
    candidates: list[ConfigurationCandidate],
    objectives: list[ObjectiveType],
) -> NSGAResult:
    if not candidates:
        return NSGAResult(fronts=[], ranks={})

    n = len(candidates)
    obj_vals = [_obj_values(c, objectives) for c in candidates]
    domination_count = [0] * n       # how many candidates dominate i
    dominated_set    = [[] for _ in range(n)]  # which candidates i dominates

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if _dominates(obj_vals[i], obj_vals[j]):
                dominated_set[i].append(j)
            elif _dominates(obj_vals[j], obj_vals[i]):
                domination_count[i] += 1

    fronts: list[list[int]] = []
    current_front = [i for i in range(n) if domination_count[i] == 0]
    fronts.append(current_front)

    while fronts[-1]:
        next_front = []
        for i in fronts[-1]:
            for j in dominated_set[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    next_front.append(j)
        if not next_front:
            break
        fronts.append(next_front)

    # Assign ranks
    ranks: dict[str, int] = {}
    result_fronts: list[list[ConfigurationCandidate]] = []
    for rank, front in enumerate(fronts):
        cands_in_front = []
        for idx in front:
            candidates[idx].pareto_rank = rank + 1
            ranks[candidates[idx].candidate_id] = rank + 1
            cands_in_front.append(candidates[idx])
        result_fronts.append(cands_in_front)

    return NSGAResult(fronts=result_fronts, ranks=ranks)


# ─────────────────────────────────────────────────────────────────────────────
# Crowding Distance
# ─────────────────────────────────────────────────────────────────────────────

def compute_crowding_distance(
    candidates: list[ConfigurationCandidate],
    objectives: list[ObjectiveType],
) -> None:
    if len(candidates) <= 2:
        for c in candidates:
            c.crowding_distance = math.inf
        return

    n = len(candidates)
    obj_vals = [_obj_values(c, objectives) for c in candidates]
    distances = [0.0] * n

    for m in range(len(objectives)):
        sorted_idx = sorted(range(n), key=lambda i: obj_vals[i][m])
        distances[sorted_idx[0]] = math.inf
        distances[sorted_idx[-1]] = math.inf
        f_min = obj_vals[sorted_idx[0]][m]
        f_max = obj_vals[sorted_idx[-1]][m]
        if abs(f_max - f_min) < 1e-12:
            continue
        for k in range(1, n - 1):
            distances[sorted_idx[k]] += (
                (obj_vals[sorted_idx[k + 1]][m] - obj_vals[sorted_idx[k - 1]][m])
                / (f_max - f_min)
            )

    for i, c in enumerate(candidates):
        c.crowding_distance = distances[i]


# ─────────────────────────────────────────────────────────────────────────────
# Knee Point Detection
# ─────────────────────────────────────────────────────────────────────────────

def find_knee_point(pareto: list[ConfigurationCandidate]) -> ConfigurationCandidate | None:
    """
    Finds the knee point on the Pareto front using the maximum curvature method
    (normalized distance from line connecting endpoints).
    For 2 objectives: point farthest from the line connecting extreme points.
    """
    if not pareto:
        return None
    if len(pareto) == 1:
        return pareto[0]

    costs  = [c.total_cost_eur for c in pareto]
    risks  = [c.risk_score for c in pareto]
    min_c, max_c = min(costs), max(costs)
    min_r, max_r = min(risks), max(risks)
    r_c = max_c - min_c or 1e-9
    r_r = max_r - min_r or 1e-9

    # Normalize to [0,1]
    norm = [(((c.total_cost_eur - min_c) / r_c),
             ((c.risk_score - min_r) / r_r)) for c in pareto]

    # Line from (0,1) to (1,0) (trade-off diagonal)
    best_dist = -math.inf
    knee = pareto[0]
    for i, (nc, nr) in enumerate(norm):
        # Distance from line x + y = 1 → |nc + nr - 1| / sqrt(2)
        dist = abs(nc + nr - 1) / math.sqrt(2)
        if dist > best_dist:
            best_dist = dist
            knee = pareto[i]
    return knee


# ─────────────────────────────────────────────────────────────────────────────
# Tradeoff Curve Builder
# ─────────────────────────────────────────────────────────────────────────────

class TradeoffCurveBuilder:
    """Builds 2D tradeoff curves from all evaluated candidates."""

    def build(
        self,
        candidates: list[ConfigurationCandidate],
        axis: TradeoffAxis = TradeoffAxis.COST_VS_RISK,
    ) -> TradeoffCurve:
        feasible = [c for c in candidates if c.feasible]
        if not feasible:
            feasible = candidates

        points = [self._to_point(c, axis) for c in feasible]
        pareto_pts = self._compute_pareto_2d(points)
        knee = self._knee_2d(pareto_pts)

        return TradeoffCurve(axis=axis, points=points, pareto_points=pareto_pts, knee_point=knee)

    def _to_point(self, c: ConfigurationCandidate, axis: TradeoffAxis) -> TradeoffPoint:
        x_label, y_label = axis.value.replace("_", " ").upper().split(" VS ")
        if axis == TradeoffAxis.COST_VS_RISK:
            x, y = c.total_cost_eur, c.risk_score
        elif axis == TradeoffAxis.COST_VS_QUALITY:
            x, y = c.total_cost_eur, c.quality_index
        elif axis == TradeoffAxis.COST_VS_DELIVERY:
            x, y = c.total_cost_eur, c.delivery_days
        elif axis == TradeoffAxis.COST_VS_CO2:
            x, y = c.total_cost_eur, c.co2_kg
        else:
            x, y = c.total_cost_eur, c.risk_score
        return TradeoffPoint(
            config_id=c.candidate_id,
            x_label=x_label,
            y_label=y_label,
            x_value=x,
            y_value=y,
            choices=c.choices,
        )

    def _compute_pareto_2d(self, points: list[TradeoffPoint]) -> list[TradeoffPoint]:
        """For cost-vs-X where lower cost AND lower X = better."""
        pareto = []
        for p in points:
            dominated = any(
                o.x_value <= p.x_value and o.y_value <= p.y_value
                and (o.x_value < p.x_value or o.y_value < p.y_value)
                for o in points if o is not p
            )
            p.dominated = dominated
            if not dominated:
                pareto.append(p)
        return sorted(pareto, key=lambda p: p.x_value)

    def _knee_2d(self, pareto: list[TradeoffPoint]) -> TradeoffPoint | None:
        if len(pareto) < 2:
            return pareto[0] if pareto else None
        xs = [p.x_value for p in pareto]
        ys = [p.y_value for p in pareto]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        rx = max_x - min_x or 1e-9
        ry = max_y - min_y or 1e-9
        best_dist = -math.inf
        knee = pareto[0]
        for p in pareto:
            nx = (p.x_value - min_x) / rx
            ny = (p.y_value - min_y) / ry
            dist = abs(nx + ny - 1) / math.sqrt(2)
            if dist > best_dist:
                best_dist = dist
                knee = p
        return knee


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Objective Optimizer (orchestrates NSGA + tradeoffs)
# ─────────────────────────────────────────────────────────────────────────────

class MultiObjectiveOptimizer:
    def __init__(self, objectives: list[ObjectiveType]) -> None:
        self.objectives = objectives
        self.builder = TradeoffCurveBuilder()

    def process(
        self,
        all_candidates: list[ConfigurationCandidate],
    ) -> tuple[list[ConfigurationCandidate], dict[str, TradeoffCurve]]:
        """
        Runs NSGA-II sorting + crowding distance on all candidates.
        Returns (pareto_front, tradeoff_curves).
        """
        feasible = [c for c in all_candidates if c.feasible]
        if not feasible:
            feasible = all_candidates[:100]

        # Limit to unique candidates by choices fingerprint
        seen: set[str] = set()
        unique: list[ConfigurationCandidate] = []
        for c in feasible:
            fp = str(sorted(c.choices.items()))
            if fp not in seen:
                seen.add(fp)
                unique.append(c)

        nsga = non_dominated_sort(unique, self.objectives)
        pareto_front = nsga.fronts[0] if nsga.fronts else []
        compute_crowding_distance(pareto_front, self.objectives)

        # Find knee point
        knee = find_knee_point(pareto_front)
        if knee:
            knee.cost_drivers = ["Knee point: balanced cost/risk optimum"]

        # Build tradeoff curves
        tradeoffs: dict[str, TradeoffCurve] = {}
        for axis in [TradeoffAxis.COST_VS_RISK, TradeoffAxis.COST_VS_QUALITY,
                     TradeoffAxis.COST_VS_DELIVERY, TradeoffAxis.COST_VS_CO2]:
            tradeoffs[axis.value] = self.builder.build(unique, axis)

        return pareto_front, tradeoffs
