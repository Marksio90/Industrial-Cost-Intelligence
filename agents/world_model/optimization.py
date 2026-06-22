"""Section 6: World-aware sourcing optimization under macroeconomic scenarios."""
from __future__ import annotations
import time
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .models import ScenarioType, WorldState
from .scenario_planning import ScenarioPlanningEngine, ScenarioComparison


class HedgeAction(str, Enum):
    HEDGE_FX = "hedge_fx"
    BUY_FORWARD = "buy_forward"
    SWITCH_SUPPLIER = "switch_supplier"
    STOCKPILE = "stockpile"
    LOCK_ENERGY = "lock_energy"
    DIVERSIFY_SUPPLY = "diversify_supply"
    NEARSHORE = "nearshore"
    RENEGOTIATE = "renegotiate"


@dataclass
class HedgeOption:
    action: HedgeAction
    description: str
    cost_eur: float          # implementation / premium cost
    saving_eur_annual: float # expected annual saving
    risk_reduction_pct: float
    horizon_months: int
    applicable_scenarios: list[ScenarioType]
    confidence: float        # 0-1


@dataclass
class RobustSolution:
    selected_hedges: list[HedgeOption]
    total_hedge_cost_eur: float
    expected_saving_eur: float
    net_benefit_eur: float
    worst_case_cost_m12: float
    baseline_cost_m12: float
    regret: float            # worst_case - baseline
    robustness_score: float  # 0-1 (lower regret → higher score)


@dataclass
class OptimizationResult:
    product_id: str
    base_cost_eur: float
    comparison: ScenarioComparison
    hedge_options: list[HedgeOption]
    robust_solution: RobustSolution
    budget_constrained_solution: Optional[RobustSolution]
    run_time_ms: float


def _build_hedge_options(world: WorldState, base_cost: float, annual_volume: int = 10_000) -> list[HedgeOption]:
    annual_spend = base_cost * annual_volume
    opts: list[HedgeOption] = []

    # FX hedge
    fx_exposure = annual_spend * 0.30
    opts.append(HedgeOption(
        action=HedgeAction.HEDGE_FX,
        description="Kontrakt forward EUR/USD na 12 miesięcy (30% ekspozycji walutowej)",
        cost_eur=fx_exposure * 0.005,
        saving_eur_annual=fx_exposure * 0.03,
        risk_reduction_pct=15.0,
        horizon_months=12,
        applicable_scenarios=[ScenarioType.SHOCK_WAR, ScenarioType.PESSIMISTIC, ScenarioType.STAGFLATION],
        confidence=0.80,
    ))

    # Energy forward
    energy_spend = annual_spend * 0.15
    opts.append(HedgeOption(
        action=HedgeAction.LOCK_ENERGY,
        description="Kontrakt na energię elektryczną po stałej cenie na 12 miesięcy",
        cost_eur=energy_spend * 0.02,
        saving_eur_annual=energy_spend * 0.12,
        risk_reduction_pct=30.0,
        horizon_months=12,
        applicable_scenarios=[ScenarioType.SHOCK_ENERGY, ScenarioType.SHOCK_WAR, ScenarioType.STAGFLATION],
        confidence=0.85,
    ))

    # Steel forward
    steel_spend = annual_spend * 0.45 * 0.60
    opts.append(HedgeOption(
        action=HedgeAction.BUY_FORWARD,
        description="Zakup stali forward na 6 miesięcy (60% udziału w materiale)",
        cost_eur=steel_spend * 0.01,
        saving_eur_annual=steel_spend * 0.08,
        risk_reduction_pct=20.0,
        horizon_months=6,
        applicable_scenarios=[ScenarioType.SHOCK_WAR, ScenarioType.PESSIMISTIC],
        confidence=0.70,
    ))

    # Stockpile
    material_spend = annual_spend * 0.45
    opts.append(HedgeOption(
        action=HedgeAction.STOCKPILE,
        description="Zwiększenie zapasów surowców do 3 miesięcy",
        cost_eur=material_spend * 0.25 * 0.03,  # carrying cost
        saving_eur_annual=material_spend * 0.06,
        risk_reduction_pct=25.0,
        horizon_months=3,
        applicable_scenarios=[ScenarioType.SHOCK_WAR, ScenarioType.SHOCK_ENERGY],
        confidence=0.75,
    ))

    # Supplier diversification
    opts.append(HedgeOption(
        action=HedgeAction.DIVERSIFY_SUPPLY,
        description="Dodaj drugiego dostawcę w innym regionie geograficznym",
        cost_eur=15_000.0,
        saving_eur_annual=annual_spend * 0.04,
        risk_reduction_pct=35.0,
        horizon_months=6,
        applicable_scenarios=[ScenarioType.SHOCK_WAR, ScenarioType.PESSIMISTIC, ScenarioType.SHOCK_ENERGY],
        confidence=0.80,
    ))

    # Nearshoring
    logistics_spend = annual_spend * 0.10
    opts.append(HedgeOption(
        action=HedgeAction.NEARSHORE,
        description="Przeniesienie części zakupów do Europy Środkowej",
        cost_eur=25_000.0,
        saving_eur_annual=logistics_spend * 0.20,
        risk_reduction_pct=20.0,
        horizon_months=12,
        applicable_scenarios=[ScenarioType.SHOCK_WAR, ScenarioType.PESSIMISTIC],
        confidence=0.65,
    ))

    # Renegotiate
    opts.append(HedgeOption(
        action=HedgeAction.RENEGOTIATE,
        description="Renegocjacja kontraktów z kluczowymi dostawcami",
        cost_eur=5_000.0,
        saving_eur_annual=annual_spend * 0.03,
        risk_reduction_pct=10.0,
        horizon_months=3,
        applicable_scenarios=[ScenarioType.SHOCK_RECESSION, ScenarioType.OPTIMISTIC],
        confidence=0.60,
    ))

    return opts


def _compute_robust_solution(
    hedge_options: list[HedgeOption],
    comparison: ScenarioComparison,
    budget_eur: Optional[float] = None,
) -> RobustSolution:
    """Minimax regret: select hedges minimizing worst-case cost."""
    rng = random.Random(42)

    # Greedy selection: rank by (saving × confidence - cost) subject to budget
    ranked = sorted(
        hedge_options,
        key=lambda h: (h.saving_eur_annual * h.confidence - h.cost_eur),
        reverse=True,
    )

    selected: list[HedgeOption] = []
    spent = 0.0
    for h in ranked:
        if budget_eur is not None and spent + h.cost_eur > budget_eur:
            continue
        selected.append(h)
        spent += h.cost_eur

    total_cost = sum(h.cost_eur for h in selected)
    total_saving = sum(h.saving_eur_annual * h.confidence for h in selected)
    net_benefit = total_saving - total_cost

    # Risk reduction on worst-case
    worst_raw = comparison.worst_case_m12
    reduction_factor = 1.0
    for h in selected:
        reduction_factor *= (1.0 - h.risk_reduction_pct / 100.0)
    worst_hedged = comparison.base_cost_eur + (worst_raw - comparison.base_cost_eur) * reduction_factor
    baseline_m12 = comparison.baseline_m12
    regret = max(0.0, worst_hedged - baseline_m12)
    base = comparison.base_cost_eur
    regret_pct = regret / base if base else 0.0
    robustness = max(0.0, 1.0 - regret_pct / 0.30)  # 0 regret → 1.0, 30%+ regret → 0

    return RobustSolution(
        selected_hedges=selected,
        total_hedge_cost_eur=total_cost,
        expected_saving_eur=total_saving,
        net_benefit_eur=net_benefit,
        worst_case_cost_m12=worst_hedged,
        baseline_cost_m12=baseline_m12,
        regret=regret,
        robustness_score=robustness,
    )


class SourcingOptimizer:
    def __init__(
        self,
        n_mc_paths: int = 200,
        annual_volume: int = 10_000,
        material_pct: float = 0.45,
        labor_pct: float = 0.20,
        energy_pct: float = 0.15,
        logistics_pct: float = 0.10,
        overhead_pct: float = 0.10,
        steel_share: float = 0.60,
        alum_share: float = 0.15,
        copper_share: float = 0.10,
        plastic_share: float = 0.15,
        usd_exposure: float = 0.30,
        cny_exposure: float = 0.20,
    ):
        self._planner = ScenarioPlanningEngine(
            n_mc_paths=n_mc_paths,
            material_pct=material_pct,
            labor_pct=labor_pct,
            energy_pct=energy_pct,
            logistics_pct=logistics_pct,
            overhead_pct=overhead_pct,
            steel_share=steel_share,
            alum_share=alum_share,
            copper_share=copper_share,
            plastic_share=plastic_share,
            usd_exposure=usd_exposure,
            cny_exposure=cny_exposure,
        )
        self._annual_volume = annual_volume

    def optimize(
        self,
        product_id: str,
        base_cost_eur: float,
        world: WorldState,
        country: str = "DE",
        scenarios: Optional[list[ScenarioType]] = None,
        budget_eur: Optional[float] = None,
    ) -> OptimizationResult:
        t0 = time.perf_counter()

        comparison = self._planner.run_all(
            product_id=product_id,
            base_cost_eur=base_cost_eur,
            world=world,
            country=country,
            scenarios=scenarios,
        )

        hedge_options = _build_hedge_options(world, base_cost_eur, self._annual_volume)
        robust = _compute_robust_solution(hedge_options, comparison, budget_eur=None)
        budget_sol = None
        if budget_eur is not None:
            budget_sol = _compute_robust_solution(hedge_options, comparison, budget_eur=budget_eur)

        elapsed = (time.perf_counter() - t0) * 1000
        return OptimizationResult(
            product_id=product_id,
            base_cost_eur=base_cost_eur,
            comparison=comparison,
            hedge_options=hedge_options,
            robust_solution=robust,
            budget_constrained_solution=budget_sol,
            run_time_ms=elapsed,
        )
