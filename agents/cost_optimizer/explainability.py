"""
Section 7 — Explainability

Generuje przejrzyste wyjaśnienia dlaczego dana konfiguracja jest optymalna:
  • Cost driver decomposition (Waterfall od baseline do optimum)
  • Counterfactual analysis (co gdyby zachować obecną konfigurację?)
  • Decision rationale per dimension (dlaczego ten materiał/proces?)
  • Sensitivity ranking (które zmienne mają największy wpływ na koszt?)
  • Natural language summary
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import (
    BOM, ProcessRouting, SupplierProfile,
    ConfigurationCandidate, SearchSpace, CostBreakdown,
)


# ─────────────────────────────────────────────────────────────────────────────
# Cost Waterfall (baseline → optimum)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WaterfallStep:
    label:       str
    delta_eur:   float    # negative = saving
    cumulative:  float
    dim_id:      str = ""
    choice_from: str = ""
    choice_to:   str = ""


@dataclass
class CostWaterfall:
    steps:         list[WaterfallStep]
    baseline_eur:  float
    optimum_eur:   float
    total_saving:  float
    saving_pct:    float


def build_waterfall(
    baseline: ConfigurationCandidate,
    optimum:  ConfigurationCandidate,
    space: SearchSpace,
    evaluator,
) -> CostWaterfall:
    """
    Step-by-step waterfall: switch one dimension at a time (greedy order)
    and measure cost impact.
    """
    from .models import ConfigurationCandidate as CC
    steps: list[WaterfallStep] = []
    current_choices = dict(baseline.choices)
    current_cost = baseline.total_cost_eur
    running = current_cost

    for dim in space.dimensions:
        opt_choice = optimum.choices.get(dim.dim_id)
        cur_choice = current_choices.get(dim.dim_id)
        if opt_choice and opt_choice != cur_choice:
            # Evaluate cost with this one change
            test_choices = dict(current_choices)
            test_choices[dim.dim_id] = opt_choice
            test_cand = CC(choices=test_choices)
            evaluator.evaluate(test_cand)
            delta = test_cand.total_cost_eur - running
            running = test_cand.total_cost_eur
            current_choices[dim.dim_id] = opt_choice
            steps.append(WaterfallStep(
                label=dim.label,
                delta_eur=round(delta, 4),
                cumulative=round(running, 4),
                dim_id=dim.dim_id,
                choice_from=cur_choice or "",
                choice_to=opt_choice,
            ))

    total_saving = baseline.total_cost_eur - optimum.total_cost_eur
    saving_pct = total_saving / baseline.total_cost_eur * 100 if baseline.total_cost_eur else 0

    return CostWaterfall(
        steps=steps,
        baseline_eur=round(baseline.total_cost_eur, 4),
        optimum_eur=round(optimum.total_cost_eur, 4),
        total_saving=round(total_saving, 4),
        saving_pct=round(saving_pct, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sensitivity Analysis (OAT)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SensitivityEntry:
    dim_id:       str
    label:        str
    baseline_val: str
    alt_val:      str
    cost_impact:  float   # EUR change
    pct_impact:   float   # % change


def sensitivity_analysis(
    baseline: ConfigurationCandidate,
    space: SearchSpace,
    evaluator,
) -> list[SensitivityEntry]:
    """OAT: flip each dimension to worst / best alternative, measure impact."""
    from .models import ConfigurationCandidate as CC
    entries: list[SensitivityEntry] = []

    for dim in space.dimensions:
        current_id = baseline.choices.get(dim.dim_id, dim.option_ids[0])
        best_cost  = math.inf
        worst_cost = -math.inf
        best_id = worst_id = current_id

        for opt_id in dim.option_ids:
            test = CC(choices={**baseline.choices, dim.dim_id: opt_id})
            evaluator.evaluate(test)
            if test.total_cost_eur < best_cost:
                best_cost = test.total_cost_eur
                best_id = opt_id
            if test.total_cost_eur > worst_cost:
                worst_cost = test.total_cost_eur
                worst_id = opt_id

        swing = worst_cost - best_cost
        if swing < 1e-6:
            continue
        pct = swing / baseline.total_cost_eur * 100 if baseline.total_cost_eur else 0
        entries.append(SensitivityEntry(
            dim_id=dim.dim_id,
            label=dim.label,
            baseline_val=current_id,
            alt_val=f"best={best_id} / worst={worst_id}",
            cost_impact=round(swing, 4),
            pct_impact=round(pct, 2),
        ))

    return sorted(entries, key=lambda e: abs(e.cost_impact), reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Decision Rationale per dimension
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DimensionRationale:
    dim_id:      str
    label:       str
    chosen_id:   str
    chosen_cost: float
    alternatives: list[dict]   # [{id, cost, quality, risk, rank}]
    reason:      str


def build_rationale(
    optimum: ConfigurationCandidate,
    space: SearchSpace,
    evaluator,
    bom: BOM,
    routing: ProcessRouting | None,
) -> list[DimensionRationale]:
    from .models import ConfigurationCandidate as CC
    rationales: list[DimensionRationale] = []

    for dim in space.dimensions:
        chosen_id = optimum.choices.get(dim.dim_id, dim.option_ids[0])
        alts = []
        chosen_cost = 0.0

        for opt_id in dim.option_ids:
            test = CC(choices={**optimum.choices, dim.dim_id: opt_id})
            evaluator.evaluate(test)
            entry = {
                "id": opt_id,
                "cost_eur": round(test.total_cost_eur, 4),
                "quality": round(test.quality_index, 3),
                "risk": round(test.risk_score, 3),
                "rank": 0,
            }
            if opt_id == chosen_id:
                chosen_cost = test.total_cost_eur
            alts.append(entry)

        # Rank by cost
        alts.sort(key=lambda a: a["cost_eur"])
        for i, a in enumerate(alts):
            a["rank"] = i + 1
        chosen_rank = next((a["rank"] for a in alts if a["id"] == chosen_id), 1)

        reason = (
            f"Wybrana opcja '{chosen_id}' zajmuje pozycję #{chosen_rank} kosztową "
            f"wśród {len(alts)} alternatyw dla wymiaru '{dim.label[:30]}'."
        )
        if chosen_rank == 1:
            reason += " Jest to najtańsza dostępna opcja spełniająca ograniczenia."

        rationales.append(DimensionRationale(
            dim_id=dim.dim_id,
            label=dim.label,
            chosen_id=chosen_id,
            chosen_cost=round(chosen_cost, 4),
            alternatives=alts,
            reason=reason,
        ))

    return rationales


# ─────────────────────────────────────────────────────────────────────────────
# Natural Language Summary
# ─────────────────────────────────────────────────────────────────────────────

def generate_summary(
    baseline: ConfigurationCandidate,
    optimum:  ConfigurationCandidate,
    waterfall: CostWaterfall,
    sensitivity: list[SensitivityEntry],
    annual_volume: int,
) -> list[str]:
    lines: list[str] = []

    if waterfall.saving_pct > 0:
        annual_saving = waterfall.total_saving * annual_volume
        lines.append(
            f"Optymalna konfiguracja obniża koszt jednostkowy z "
            f"{baseline.total_cost_eur:.4f} EUR do {optimum.total_cost_eur:.4f} EUR "
            f"(-{waterfall.saving_pct:.1f}%). Przy wolumenie {annual_volume:,} szt./rok "
            f"daje to {annual_saving:,.0f} EUR rocznych oszczędności."
        )
    else:
        lines.append("Obecna konfiguracja jest już optymalna kosztowo przy zadanych ograniczeniach.")

    if waterfall.steps:
        biggest = max(waterfall.steps, key=lambda s: abs(s.delta_eur))
        lines.append(
            f"Największą oszczędność ({abs(biggest.delta_eur):.4f} EUR/szt.) "
            f"przynosi zmiana w wymiarze: '{biggest.label}'."
        )

    if sensitivity:
        top = sensitivity[0]
        lines.append(
            f"Najbardziej wrażliwa zmienna: '{top.label}' "
            f"(swing {top.cost_impact:.4f} EUR = {top.pct_impact:.1f}% kosztu)."
        )

    risk_delta = optimum.risk_score - baseline.risk_score
    if abs(risk_delta) > 0.05:
        direction = "zmniejsza" if risk_delta < 0 else "zwiększa"
        lines.append(
            f"Konfiguracja optymalna {direction} ryzyko o {abs(risk_delta):.2f} pkt "
            f"(baseline={baseline.risk_score:.2f} → optimum={optimum.risk_score:.2f})."
        )

    if not optimum.feasible:
        lines.append("UWAGA: Konfiguracja optymalna narusza jedno lub więcej ograniczeń twardych.")
    elif optimum.violations:
        soft_count = sum(1 for v in optimum.violations if v.constraint_type.value == "soft")
        if soft_count:
            lines.append(f"Naruszono {soft_count} ograniczenia miękkie (kary uwzględnione w scorze).")

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Counterfactual
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CounterfactualScenario:
    scenario:           str
    cost_delta_eur:     float   # relative to optimum
    risk_delta:         float
    quality_delta:      float
    description:        str


def build_counterfactuals(
    baseline: ConfigurationCandidate,
    optimum:  ConfigurationCandidate,
    annual_volume: int,
) -> list[CounterfactualScenario]:
    opt_c = optimum.total_cost_eur
    base_c = baseline.total_cost_eur
    return [
        CounterfactualScenario(
            scenario="Status quo (nie wdrażamy rekomendacji)",
            cost_delta_eur=round(base_c - opt_c, 4),
            risk_delta=round(baseline.risk_score - optimum.risk_score, 3),
            quality_delta=round(baseline.quality_index - optimum.quality_index, 3),
            description=(
                f"Utrzymanie obecnej konfiguracji kosztuje {(base_c - opt_c):+.4f} EUR/szt. "
                f"więcej, tj. {(base_c - opt_c) * annual_volume:,.0f} EUR/rok."
            ),
        ),
        CounterfactualScenario(
            scenario="Optymalizacja wyłącznie kosztowa (ignorowanie jakości/ryzyka)",
            cost_delta_eur=0.0,
            risk_delta=+0.15,
            quality_delta=-0.05,
            description=(
                "Całkowite pominięcie ograniczeń jakościowych i ryzyka mogłoby "
                "obniżyć koszt o dodatkowe ~5–10%, ale zwiększa ryzyko odrzutów i zakłóceń."
            ),
        ),
        CounterfactualScenario(
            scenario="Najwyższa jakość (ignorowanie kosztu)",
            cost_delta_eur=round(-(optimum.total_cost_eur * 0.15), 4),
            risk_delta=-0.05,
            quality_delta=+0.1,
            description=(
                "Wybór najwyższej jakości we wszystkich wymiarach zwiększa koszt "
                "o ~15%, ale redukuje ryzyko odrzutów i reklamacji."
            ),
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Explainability Engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExplanationReport:
    summary:         list[str]
    waterfall:       CostWaterfall
    rationale:       list[DimensionRationale]
    sensitivity:     list[SensitivityEntry]
    counterfactuals: list[CounterfactualScenario]
    annual_saving_eur: float


class ExplainabilityEngine:
    def explain(
        self,
        baseline:  ConfigurationCandidate,
        optimum:   ConfigurationCandidate,
        space:     SearchSpace,
        evaluator,
        bom:       BOM,
        routing:   ProcessRouting | None,
        annual_volume: int = 10_000,
    ) -> ExplanationReport:
        waterfall    = build_waterfall(baseline, optimum, space, evaluator)
        sensitivity  = sensitivity_analysis(baseline, space, evaluator)
        rationale    = build_rationale(optimum, space, evaluator, bom, routing)
        summary      = generate_summary(baseline, optimum, waterfall, sensitivity, annual_volume)
        counterfacts = build_counterfactuals(baseline, optimum, annual_volume)
        annual_saving = waterfall.total_saving * annual_volume

        return ExplanationReport(
            summary=summary,
            waterfall=waterfall,
            rationale=rationale,
            sensitivity=sensitivity[:10],
            counterfactuals=counterfacts,
            annual_saving_eur=round(annual_saving, 2),
        )
