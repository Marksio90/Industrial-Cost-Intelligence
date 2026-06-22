"""
Section 5 — Optimization Engine

Silnik optymalizacji kosztu produktu:
  - GreedyOptimizer: zachłanna optymalizacja (OAT + najlepszy krok)
  - GeneticOptimizer: algorytm genetyczny dla przestrzeni dyskretnej
  - LinearRelaxationOptimizer: uproszczony LP (kraj + materiał + wolumen)
  - OptimizationEngine: unified entry point z wyborem algorytmu
  - ParetoFrontier: wielokryterialna optymalizacja koszt vs ryzyko

Przestrzeń decyzyjna:
  - Wybór kraju produkcji (z COUNTRY_PROFILES)
  - Wybór dostawcy materiału (z dostępnych dostawców per materiał)
  - Wybór technologii (z dostępnych profili tech)
  - Wolumen produkcji (z przedziału)
  - Zmiana skrap rate (redukcja odpadów)
"""
from __future__ import annotations

import copy
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ProductCostModel, OptimizationTask, OptimizationResult, OptimizationObjective,
    CostResult, CostBreakdown, SupplierCostProfile, TechnologyProfile,
)
from .dependency_model import COUNTRY_PROFILES
from .simulation_engine import CostSimulationEngine

log = logging.getLogger(__name__)

_ENGINE = CostSimulationEngine()


# ─────────────────────────────────────────────────────────────────────────────
# Decision space
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DecisionVariable:
    name:           str
    variable_type:  str        # "country", "supplier", "technology", "volume", "scrap"
    current_value:  Any
    options:        list[Any]  # discrete choices
    description:    str = ""


@dataclass
class OptimizationCandidate:
    decisions:    dict[str, Any]   # variable_name → value
    cost_eur:     float
    risk_score:   float = 0.0      # 0 (best) – 1 (worst)
    feasible:     bool = True
    metadata:     dict[str, Any] = field(default_factory=dict)

    @property
    def objective(self) -> float:
        return self.cost_eur


@dataclass
class ParetoPoint:
    cost_eur:   float
    risk_score: float
    decisions:  dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Objective function
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate(
    model:             ProductCostModel,
    decisions:         dict[str, Any],
    supplier_profiles: dict[str, SupplierCostProfile] | None,
    tech_profiles:     dict[str, TechnologyProfile]   | None,
    objective:         OptimizationObjective,
) -> tuple[float, float]:
    """
    Apply decisions to model and compute (cost_eur, risk_score).
    Returns (inf, 1.0) if evaluation fails.
    """
    try:
        m = copy.deepcopy(model)
        sp = dict(supplier_profiles or {})
        tp = dict(tech_profiles or {})
        country_code = m.production_country

        for var, val in decisions.items():
            if var == "country":
                country_code = val
                m.production_country = val
            elif var == "volume":
                m.annual_volume = int(val)
            elif var.startswith("scrap:"):
                mat_id = var[6:]
                for line in m.bom_lines:
                    if line.material_id == mat_id:
                        line.scrap_rate = float(val)
            elif var.startswith("supplier:"):
                mat_id = var[9:]
                for line in m.bom_lines:
                    if line.material_id == mat_id and val in sp:
                        line.supplier_id = val

        result = _ENGINE.compute(m, country_code, sp, tp)
        b = result.breakdown

        if objective == OptimizationObjective.MIN_TOTAL_COST:
            cost = b.total_cost_eur
        elif objective == OptimizationObjective.MIN_MATERIAL_COST:
            cost = b.direct_material_eur
        elif objective == OptimizationObjective.MIN_LOGISTICS:
            cost = b.logistics_eur
        elif objective == OptimizationObjective.MAX_MARGIN:
            cost = -b.margin_eur  # negate (maximize)
        else:
            cost = b.total_cost_eur

        # Risk score: country risk level
        c = COUNTRY_PROFILES.get(country_code, COUNTRY_PROFILES["DE"])
        risk = c.risk_level.value / 5.0  # normalize to 0–1

        return cost, risk

    except Exception as exc:
        log.debug("Evaluation failed: %s", exc)
        return float("inf"), 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Greedy optimizer
# ─────────────────────────────────────────────────────────────────────────────

class GreedyOptimizer:
    """
    One-at-a-time greedy optimizer.
    For each decision variable, try all options and keep the best.
    Repeats until no improvement.
    """

    def optimize(
        self,
        model:             ProductCostModel,
        variables:         list[DecisionVariable],
        objective:         OptimizationObjective = OptimizationObjective.MIN_TOTAL_COST,
        max_iterations:    int = 5,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> list[OptimizationCandidate]:
        # Initialize with current values
        current_decisions = {v.name: v.current_value for v in variables}
        current_cost, current_risk = _evaluate(model, current_decisions, supplier_profiles, tech_profiles, objective)

        history: list[OptimizationCandidate] = [
            OptimizationCandidate(dict(current_decisions), current_cost, current_risk)
        ]

        for iteration in range(max_iterations):
            improved = False

            for var in variables:
                best_cost   = current_cost
                best_risk   = current_risk
                best_val    = current_decisions[var.name]

                for option in var.options:
                    if option == current_decisions[var.name]:
                        continue
                    trial = dict(current_decisions)
                    trial[var.name] = option
                    cost, risk = _evaluate(model, trial, supplier_profiles, tech_profiles, objective)
                    if cost < best_cost:
                        best_cost = cost
                        best_risk = risk
                        best_val  = option

                if best_val != current_decisions[var.name]:
                    current_decisions[var.name] = best_val
                    current_cost = best_cost
                    current_risk = best_risk
                    improved = True
                    history.append(OptimizationCandidate(
                        dict(current_decisions), current_cost, current_risk,
                        metadata={"iteration": iteration, "improved_var": var.name}
                    ))

            if not improved:
                break

        return history


# ─────────────────────────────────────────────────────────────────────────────
# Genetic optimizer
# ─────────────────────────────────────────────────────────────────────────────

class GeneticOptimizer:
    """
    Simple genetic algorithm for discrete decision spaces.
    Population = list of candidates (chromosomes).
    Each chromosome = dict[variable_name → option_index].
    """

    def __init__(
        self,
        population_size: int = 20,
        generations:     int = 30,
        mutation_rate:   float = 0.15,
        elite_fraction:  float = 0.20,
        seed:            int | None = None,
    ) -> None:
        self.population_size = population_size
        self.generations     = generations
        self.mutation_rate   = mutation_rate
        self.elite_k         = max(1, int(population_size * elite_fraction))
        self.rng             = random.Random(seed)

    def optimize(
        self,
        model:             ProductCostModel,
        variables:         list[DecisionVariable],
        objective:         OptimizationObjective = OptimizationObjective.MIN_TOTAL_COST,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> list[OptimizationCandidate]:
        if not variables:
            return []

        def random_chromosome() -> dict[str, int]:
            return {v.name: self.rng.randint(0, len(v.options) - 1) for v in variables}

        def decode(chrom: dict[str, int]) -> dict[str, Any]:
            return {v.name: v.options[chrom[v.name]] for v in variables}

        def fitness(chrom: dict[str, int]) -> float:
            decisions = decode(chrom)
            cost, _ = _evaluate(model, decisions, supplier_profiles, tech_profiles, objective)
            return cost

        # Initialize population
        population = [random_chromosome() for _ in range(self.population_size)]
        # Include current solution
        population[0] = {v.name: v.options.index(v.current_value) if v.current_value in v.options else 0 for v in variables}

        history: list[OptimizationCandidate] = []
        best_cost = float("inf")
        best_chrom: dict[str, int] = {}

        for gen in range(self.generations):
            # Evaluate
            scored = [(fitness(c), c) for c in population]
            scored.sort(key=lambda x: x[0])

            if scored[0][0] < best_cost:
                best_cost  = scored[0][0]
                best_chrom = dict(scored[0][1])
                decisions  = decode(best_chrom)
                _, risk    = _evaluate(model, decisions, supplier_profiles, tech_profiles, objective)
                history.append(OptimizationCandidate(
                    decisions, best_cost, risk,
                    metadata={"generation": gen}
                ))

            # Elites survive
            elites = [c for _, c in scored[:self.elite_k]]

            # Crossover + mutation
            new_pop = list(elites)
            while len(new_pop) < self.population_size:
                p1 = self.rng.choice(elites)
                p2 = self.rng.choice(elites)
                child: dict[str, int] = {}
                for v in variables:
                    child[v.name] = p1[v.name] if self.rng.random() < 0.5 else p2[v.name]
                    if self.rng.random() < self.mutation_rate:
                        child[v.name] = self.rng.randint(0, len(v.options) - 1)
                new_pop.append(child)

            population = new_pop

        return history


# ─────────────────────────────────────────────────────────────────────────────
# Pareto frontier (cost vs risk)
# ─────────────────────────────────────────────────────────────────────────────

class ParetoFrontierBuilder:
    """
    Generates Pareto-optimal solutions on the cost–risk frontier
    by sampling all combinations of country and key parameters.
    """

    def build(
        self,
        model:             ProductCostModel,
        variables:         list[DecisionVariable],
        objective:         OptimizationObjective = OptimizationObjective.MIN_TOTAL_COST,
        max_samples:       int = 200,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
        seed:              int | None = None,
    ) -> list[ParetoPoint]:
        rng = random.Random(seed)
        points: list[ParetoPoint] = []

        # Enumerate (sample if too many)
        from itertools import product as iproduct
        all_options = [v.options for v in variables]
        total = 1
        for opts in all_options:
            total *= len(opts)

        if total <= max_samples:
            combos = list(iproduct(*all_options))
        else:
            combos_set = set()
            while len(combos_set) < max_samples:
                combo = tuple(rng.choice(opts) for opts in all_options)
                combos_set.add(combo)
            combos = list(combos_set)

        for combo in combos:
            decisions = {v.name: combo[i] for i, v in enumerate(variables)}
            cost, risk = _evaluate(model, decisions, supplier_profiles, tech_profiles, objective)
            if cost < float("inf"):
                points.append(ParetoPoint(cost, risk, decisions))

        # Filter Pareto-optimal
        pareto: list[ParetoPoint] = []
        for p in points:
            dominated = any(
                q.cost_eur <= p.cost_eur and q.risk_score <= p.risk_score
                and (q.cost_eur < p.cost_eur or q.risk_score < p.risk_score)
                for q in points
            )
            if not dominated:
                pareto.append(p)

        pareto.sort(key=lambda p: p.cost_eur)
        return pareto


# ─────────────────────────────────────────────────────────────────────────────
# Unified entry point
# ─────────────────────────────────────────────────────────────────────────────

class OptimizationEngine:
    """
    Unified cost optimization engine.

    Usage:
        engine = OptimizationEngine()
        result = engine.optimize(task, model, supplier_profiles, tech_profiles)
    """

    def __init__(self) -> None:
        self._greedy  = GreedyOptimizer()
        self._genetic = GeneticOptimizer()
        self._pareto  = ParetoFrontierBuilder()

    def optimize(
        self,
        task:              OptimizationTask,
        model:             ProductCostModel,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> OptimizationResult:
        t0 = time.perf_counter()

        # Build decision variables from task constraints
        variables = self._build_variables(task, model, supplier_profiles, tech_profiles)

        # Baseline
        baseline = _ENGINE.compute(model, model.production_country, supplier_profiles, tech_profiles)
        base_cost = baseline.breakdown.total_cost_eur

        # Run optimizer
        algorithm = getattr(task, "algorithm", "greedy")
        if algorithm == "genetic":
            history = self._genetic.optimize(model, variables, task.objective, supplier_profiles, tech_profiles)
        else:
            history = self._greedy.optimize(model, variables, task.objective, supplier_profiles=supplier_profiles, tech_profiles=tech_profiles)

        if not history:
            return OptimizationResult(
                task_id          = task.task_id,
                objective        = task.objective,
                baseline_cost    = base_cost,
                optimized_cost   = base_cost,
                savings_eur      = 0.0,
                savings_pct      = 0.0,
                best_decisions   = {},
                improvement_path = [],
                pareto_frontier  = [],
                run_time_s       = time.perf_counter() - t0,
            )

        best = min(history, key=lambda c: c.cost_eur)

        # Pareto frontier
        pareto = self._pareto.build(model, variables, task.objective, 100, supplier_profiles, tech_profiles)
        pareto_dicts = [
            {"cost_eur": p.cost_eur, "risk_score": p.risk_score, "decisions": p.decisions}
            for p in pareto
        ]

        savings = base_cost - best.cost_eur
        savings_pct = (savings / base_cost * 100.0) if base_cost else 0.0

        return OptimizationResult(
            task_id          = task.task_id,
            objective        = task.objective,
            baseline_cost    = base_cost,
            optimized_cost   = best.cost_eur,
            savings_eur      = savings,
            savings_pct      = savings_pct,
            best_decisions   = best.decisions,
            improvement_path = [
                {"step": i, "cost_eur": c.cost_eur, "decisions": c.decisions, "metadata": c.metadata}
                for i, c in enumerate(history)
            ],
            pareto_frontier  = pareto_dicts,
            run_time_s       = time.perf_counter() - t0,
        )

    def _build_variables(
        self,
        task:              OptimizationTask,
        model:             ProductCostModel,
        supplier_profiles: dict[str, SupplierCostProfile] | None,
        tech_profiles:     dict[str, TechnologyProfile]   | None,
    ) -> list[DecisionVariable]:
        variables: list[DecisionVariable] = []

        # Country variable
        country_opts = list(COUNTRY_PROFILES.keys())
        variables.append(DecisionVariable(
            name          = "country",
            variable_type = "country",
            current_value = model.production_country,
            options       = country_opts,
            description   = "Kraj produkcji",
        ))

        # Volume variable (if bounds specified)
        if hasattr(task, "volume_range") and task.volume_range:
            lo, hi = task.volume_range
            step   = max(1, (hi - lo) // 8)
            vol_opts = list(range(lo, hi + 1, step))
            variables.append(DecisionVariable(
                name          = "volume",
                variable_type = "volume",
                current_value = model.annual_volume,
                options       = vol_opts,
                description   = "Wolumen produkcji roczny",
            ))

        # Scrap rate variables per material
        for line in model.bom_lines:
            scrap_opts = [round(x, 3) for x in [
                max(0.0, line.scrap_rate - 0.03),
                max(0.0, line.scrap_rate - 0.01),
                line.scrap_rate,
                line.scrap_rate + 0.01,
            ]]
            variables.append(DecisionVariable(
                name          = f"scrap:{line.material_id}",
                variable_type = "scrap",
                current_value = line.scrap_rate,
                options       = list(set(scrap_opts)),
                description   = f"Scrap rate materiału {line.material_id}",
            ))

        return variables

    def quick_country_comparison(
        self,
        model:             ProductCostModel,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> list[dict[str, Any]]:
        """Compute cost for all countries, return sorted list."""
        rows = []
        for code, country in COUNTRY_PROFILES.items():
            try:
                m = copy.copy(model)
                m.production_country = code
                result = _ENGINE.compute(m, code, supplier_profiles, tech_profiles)
                b = result.breakdown
                rows.append({
                    "country":          code,
                    "total_cost_eur":   round(b.total_cost_eur, 2),
                    "labor_eur":        round(b.direct_labor_eur, 2),
                    "material_eur":     round(b.direct_material_eur, 2),
                    "logistics_eur":    round(b.logistics_eur, 2),
                    "risk_level":       country.risk_level.name,
                    "wage_eur_h":       country.avg_wage_eur_h,
                })
            except Exception as exc:
                log.debug("Country %s eval failed: %s", code, exc)
        rows.sort(key=lambda r: r["total_cost_eur"])
        return rows
