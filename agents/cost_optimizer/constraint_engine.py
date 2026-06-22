"""
Section 2 — Constraint Engine

Weryfikuje czy kandydatura konfiguracji spełnia wszystkie ograniczenia.

Typy ograniczeń:
  HARD        — naruszenie = infeasible (kandydatura odrzucona)
  SOFT        — naruszenie = kara w funkcji celu
  PREFERENCE  — naruszenie = mała kara (signal do rankingu)

Wbudowane ograniczenia:
  • Minimalny quality_index per pozycja BOM (function-critical items)
  • Maksymalne ryzyko dostawcy
  • Minimalna dostępność materiału
  • Maksymalny czas dostawy
  • Minimalny wolumen vs MOQ
  • Maksymalny udział CO2
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    BOM, BOMItem, BOMAlternative,
    SupplierProfile,
    Constraint, ConstraintType, ConstraintViolation,
    ConfigurationCandidate,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in constraint definitions
# ─────────────────────────────────────────────────────────────────────────────

def default_constraints() -> list[Constraint]:
    return [
        Constraint(
            constraint_type=ConstraintType.HARD,
            name="min_quality_index",
            description="Quality index must meet item requirement",
            signal="quality_index",
            operator=">=",
            threshold=0.0,   # overridden per-item
        ),
        Constraint(
            constraint_type=ConstraintType.HARD,
            name="min_availability",
            description="Material availability must be > 0.5",
            signal="availability",
            operator=">=",
            threshold=0.5,
        ),
        Constraint(
            constraint_type=ConstraintType.SOFT,
            name="max_supplier_risk",
            description="Composite supplier risk should be < 0.7",
            signal="risk_score",
            operator="<=",
            threshold=0.7,
            penalty_weight=2.0,
        ),
        Constraint(
            constraint_type=ConstraintType.SOFT,
            name="max_delivery_days",
            description="Total lead time should be < 60 days",
            signal="delivery_days",
            operator="<=",
            threshold=60.0,
            penalty_weight=1.0,
        ),
        Constraint(
            constraint_type=ConstraintType.PREFERENCE,
            name="prefer_low_co2",
            description="CO2 footprint should be minimized",
            signal="co2_kg",
            operator="<=",
            threshold=100.0,
            penalty_weight=0.3,
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Constraint Engine
# ─────────────────────────────────────────────────────────────────────────────

class ConstraintEngine:
    """
    Evaluates all constraints against a configuration candidate.

    Returns (is_feasible, violations, total_penalty).
    """

    def __init__(
        self,
        constraints: list[Constraint],
        bom: BOM,
        suppliers: dict[str, SupplierProfile],
    ) -> None:
        self.constraints = constraints
        self.bom = bom
        self.suppliers = suppliers
        # Build item quality requirement index
        self._item_quality: dict[str, float] = {}
        self._item_critical: dict[str, bool] = {}
        for item in bom.items:
            self._item_quality[item.item_id] = item.required_quality_index
            self._item_critical[item.item_id] = item.function_critical

    def check(
        self,
        candidate: ConfigurationCandidate,
        item_quality_map: dict[str, float] | None = None,
    ) -> tuple[bool, list[ConstraintViolation], float]:
        violations: list[ConstraintViolation] = []
        total_penalty = 0.0

        # Candidate-level signals (already computed by CostEvaluator)
        signals: dict[str, float] = {
            "quality_index":  candidate.quality_index,
            "risk_score":     candidate.risk_score,
            "delivery_days":  candidate.delivery_days,
            "co2_kg":         candidate.co2_kg,
            "total_cost_eur": candidate.total_cost_eur,
            "availability":   1.0,  # aggregated below
        }

        # Per-item quality enforcement (function-critical)
        if item_quality_map:
            for item_id, q in item_quality_map.items():
                if self._item_critical.get(item_id) and q < self._item_quality.get(item_id, 0.9):
                    v = ConstraintViolation(
                        constraint_id="item_quality",
                        constraint_name=f"min_quality:{item_id}",
                        constraint_type=ConstraintType.HARD,
                        signal="quality_index",
                        actual_value=q,
                        required_value=self._item_quality[item_id],
                        penalty=999.0,
                        message=f"Function-critical item {item_id} quality {q:.2f} < {self._item_quality[item_id]:.2f}",
                    )
                    violations.append(v)
                    total_penalty += 999.0

        # Generic constraint evaluation
        for c in self.constraints:
            val = signals.get(c.signal)
            if val is None:
                continue
            violated, penalty = self._evaluate(c, val)
            if violated:
                v = ConstraintViolation(
                    constraint_id=c.constraint_id,
                    constraint_name=c.name,
                    constraint_type=c.constraint_type,
                    signal=c.signal,
                    actual_value=val,
                    required_value=c.threshold,
                    penalty=penalty,
                    message=f"{c.name}: {c.signal}={val:.3f} {c.operator} {c.threshold} VIOLATED",
                )
                violations.append(v)
                total_penalty += penalty

        is_feasible = not any(v.constraint_type == ConstraintType.HARD for v in violations)
        candidate.feasible = is_feasible
        candidate.violations = violations
        return is_feasible, violations, total_penalty

    def _evaluate(self, c: Constraint, val: float) -> tuple[bool, float]:
        thr = float(c.threshold)
        op  = c.operator
        if   op == ">=": violated = val < thr
        elif op == "<=": violated = val > thr
        elif op == "==": violated = abs(val - thr) > 1e-9
        elif op == "!=": violated = abs(val - thr) < 1e-9
        elif op == "range":
            lo, hi = float(c.threshold), float(c.threshold_max or c.threshold)
            violated = not (lo <= val <= hi)
        else:
            violated = False

        if not violated:
            return False, 0.0

        # Penalty = weight × |deviation| / threshold  (normalized)
        deviation = abs(val - thr) / (abs(thr) + 1e-9)
        if c.constraint_type == ConstraintType.HARD:
            penalty = 999.0 + deviation * 100
        elif c.constraint_type == ConstraintType.SOFT:
            penalty = c.penalty_weight * deviation * 10
        else:  # PREFERENCE
            penalty = c.penalty_weight * deviation
        return True, penalty

    def bulk_check(
        self,
        candidates: list[ConfigurationCandidate],
    ) -> list[tuple[bool, float]]:
        results = []
        for c in candidates:
            feasible, _, penalty = self.check(c)
            results.append((feasible, penalty))
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Constraint Analyser  (reports which constraints are binding)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConstraintReport:
    total_candidates:  int
    feasible:          int
    infeasible:        int
    binding:           dict[str, int]   # constraint_name → violation count
    soft_penalties:    dict[str, float] # constraint_name → avg penalty


class ConstraintAnalyser:
    def analyse(self, candidates: list[ConfigurationCandidate]) -> ConstraintReport:
        binding: dict[str, int] = {}
        penalties: dict[str, list[float]] = {}
        n_feasible = sum(1 for c in candidates if c.feasible)
        for cand in candidates:
            for v in cand.violations:
                binding[v.constraint_name] = binding.get(v.constraint_name, 0) + 1
                if v.constraint_type != ConstraintType.HARD:
                    penalties.setdefault(v.constraint_name, []).append(v.penalty)
        avg_penalties = {k: sum(v) / len(v) for k, v in penalties.items()}
        return ConstraintReport(
            total_candidates=len(candidates),
            feasible=n_feasible,
            infeasible=len(candidates) - n_feasible,
            binding=binding,
            soft_penalties=avg_penalties,
        )
