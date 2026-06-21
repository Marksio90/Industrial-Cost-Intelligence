"""
Section 3 — Optimization Engine

Algorytmy przeszukiwania przestrzeni konfiguracji:
  • Greedy        — zachłanny, deterministyczny, szybki baseline
  • Beam Search   — szerokościowy z wiązką K najlepszych
  • Simulated Annealing — metaheurystyka, ucieczka z lokalnych minimów
  • Genetic       — ewolucja populacji (crossover + mutation + elity)
"""
from __future__ import annotations

import math
import random
import time
import logging
from copy import deepcopy
from typing import Callable

from .models import (
    BOM, ProcessRouting, SupplierProfile,
    Constraint, OptimizationAlgorithm, ObjectiveType,
    ConfigurationCandidate, OptimizationRun,
)
from .search_space import SearchSpace, ConfigurationEncoder, CostEvaluator
from .constraint_engine import ConstraintEngine

log = logging.getLogger(__name__)

ObjectiveFn = Callable[[ConfigurationCandidate], float]


# ─────────────────────────────────────────────────────────────────────────────
# Objective function builder
# ─────────────────────────────────────────────────────────────────────────────

def build_objective_fn(
    objectives: list[ObjectiveType],
    weights: list[float],
    penalty_weight: float = 10.0,
) -> ObjectiveFn:
    """Returns a function: candidate → scalar (lower = better)."""
    w = [w / (sum(weights) or 1) for w in weights]

    def fn(c: ConfigurationCandidate) -> float:
        score = 0.0
        for obj, wi in zip(objectives, w):
            if obj == ObjectiveType.MINIMIZE_COST:
                score += wi * c.total_cost_eur
            elif obj == ObjectiveType.MINIMIZE_RISK:
                score += wi * c.risk_score * 100   # scale to EUR range
            elif obj == ObjectiveType.MAXIMIZE_QUALITY:
                score -= wi * c.quality_index * 50  # negative = minimize
            elif obj == ObjectiveType.MAXIMIZE_DELIVERY:
                score += wi * c.delivery_days * 0.5
            elif obj == ObjectiveType.MINIMIZE_CO2:
                score += wi * c.co2_kg * 0.5
        # Constraint penalty
        penalty = sum(v.penalty for v in c.violations)
        return score + penalty * penalty_weight

    return fn


# ─────────────────────────────────────────────────────────────────────────────
# Shared evaluation helper
# ─────────────────────────────────────────────────────────────────────────────

def _make_candidate(choices: dict[str, str], evaluator: CostEvaluator,
                    constraint_engine: ConstraintEngine) -> ConfigurationCandidate:
    c = ConfigurationCandidate(choices=choices)
    evaluator.evaluate(c)
    constraint_engine.check(c)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# 1. Greedy Optimizer
# ─────────────────────────────────────────────────────────────────────────────

class GreedyOptimizer:
    """
    Fixes dimensions one by one, each time picking the best option.
    Runs in O(∑ N_i) evaluations — very fast, may miss global optimum.
    """

    def optimize(
        self,
        space: SearchSpace,
        encoder: ConfigurationEncoder,
        evaluator: CostEvaluator,
        constraint_engine: ConstraintEngine,
        objective_fn: ObjectiveFn,
    ) -> list[ConfigurationCandidate]:
        # Start from first option in each dimension
        choices: dict[str, str] = {
            dim.dim_id: dim.option_ids[0] for dim in space.dimensions
        }
        history: list[ConfigurationCandidate] = []

        for dim in space.dimensions:
            best_score = math.inf
            best_opt   = choices[dim.dim_id]
            for opt_id in dim.option_ids:
                choices[dim.dim_id] = opt_id
                c = _make_candidate(dict(choices), evaluator, constraint_engine)
                s = objective_fn(c)
                if s < best_score:
                    best_score = s
                    best_opt   = opt_id
                history.append(c)
            choices[dim.dim_id] = best_opt

        # Final candidate
        final = _make_candidate(choices, evaluator, constraint_engine)
        final.score = objective_fn(final)
        history.append(final)
        return history


# ─────────────────────────────────────────────────────────────────────────────
# 2. Beam Search
# ─────────────────────────────────────────────────────────────────────────────

class BeamSearchOptimizer:
    """
    Expands K best partial solutions at each dimension step.
    Better than greedy, still polynomial: O(K × ∑ N_i).
    """

    def __init__(self, beam_width: int = 10) -> None:
        self.beam_width = beam_width

    def optimize(
        self,
        space: SearchSpace,
        encoder: ConfigurationEncoder,
        evaluator: CostEvaluator,
        constraint_engine: ConstraintEngine,
        objective_fn: ObjectiveFn,
    ) -> list[ConfigurationCandidate]:
        dims = space.dimensions
        if not dims:
            return []

        # Beam = list of partial choice dicts
        beam: list[dict[str, str]] = [{}]
        history: list[ConfigurationCandidate] = []

        for dim in dims:
            candidates_here: list[tuple[float, dict[str, str]]] = []
            for partial in beam:
                for opt_id in dim.option_ids:
                    full = dict(partial)
                    full[dim.dim_id] = opt_id
                    # Fill remaining dims with first option for evaluation
                    for d2 in dims:
                        if d2.dim_id not in full:
                            full[d2.dim_id] = d2.option_ids[0]
                    c = _make_candidate(full, evaluator, constraint_engine)
                    s = objective_fn(c)
                    candidates_here.append((s, {k: v for k, v in full.items() if k <= dim.dim_id}))
                    history.append(c)
            # Keep top-K
            candidates_here.sort(key=lambda x: x[0])
            beam = [ch for _, ch in candidates_here[:self.beam_width]]

        # Evaluate complete beam
        best_candidates = []
        for ch in beam:
            # Fill any missing dims
            for d in dims:
                if d.dim_id not in ch:
                    ch[d.dim_id] = d.option_ids[0]
            c = _make_candidate(ch, evaluator, constraint_engine)
            c.score = objective_fn(c)
            best_candidates.append(c)
            history.append(c)

        return history


# ─────────────────────────────────────────────────────────────────────────────
# 3. Simulated Annealing
# ─────────────────────────────────────────────────────────────────────────────

class SimulatedAnnealingOptimizer:
    def __init__(
        self,
        T_start: float = 50.0,
        T_min:   float = 0.1,
        alpha:   float = 0.92,
        steps_per_temp: int = 15,
        seed: int | None = None,
    ) -> None:
        self.T_start = T_start
        self.T_min   = T_min
        self.alpha   = alpha
        self.steps_per_temp = steps_per_temp
        self.rng = random.Random(seed)

    def optimize(
        self,
        space: SearchSpace,
        encoder: ConfigurationEncoder,
        evaluator: CostEvaluator,
        constraint_engine: ConstraintEngine,
        objective_fn: ObjectiveFn,
        max_iter: int = 500,
    ) -> list[ConfigurationCandidate]:
        dims = space.dimensions
        if not dims:
            return []

        history: list[ConfigurationCandidate] = []

        # Start from random solution
        choices = {d.dim_id: self.rng.choice(d.option_ids) for d in dims}
        current = _make_candidate(choices, evaluator, constraint_engine)
        current_score = objective_fn(current)
        best = deepcopy(current)
        best_score = current_score
        history.append(current)

        T = self.T_start
        iteration = 0

        while T > self.T_min and iteration < max_iter:
            for _ in range(self.steps_per_temp):
                # Perturb: flip one random dimension
                dim = self.rng.choice(dims)
                new_choices = dict(current.choices)
                new_choices[dim.dim_id] = self.rng.choice(dim.option_ids)
                neighbor = _make_candidate(new_choices, evaluator, constraint_engine)
                n_score = objective_fn(neighbor)
                delta = n_score - current_score
                if delta < 0 or self.rng.random() < math.exp(-delta / T):
                    current = neighbor
                    current_score = n_score
                    if current_score < best_score:
                        best = deepcopy(current)
                        best_score = current_score
                history.append(neighbor)
                iteration += 1
                if iteration >= max_iter:
                    break
            T *= self.alpha

        best.score = best_score
        history.append(best)
        return history


# ─────────────────────────────────────────────────────────────────────────────
# 4. Genetic Algorithm
# ─────────────────────────────────────────────────────────────────────────────

class GeneticOptimizer:
    def __init__(
        self,
        population_size: int = 50,
        generations:     int = 30,
        mutation_rate:   float = 0.15,
        elite_pct:       float = 0.10,
        tournament_k:    int   = 3,
        seed: int | None = None,
    ) -> None:
        self.population_size = population_size
        self.generations     = generations
        self.mutation_rate   = mutation_rate
        self.elite_pct       = elite_pct
        self.tournament_k    = tournament_k
        self.rng = random.Random(seed)

    def optimize(
        self,
        space: SearchSpace,
        encoder: ConfigurationEncoder,
        evaluator: CostEvaluator,
        constraint_engine: ConstraintEngine,
        objective_fn: ObjectiveFn,
    ) -> list[ConfigurationCandidate]:
        dims = space.dimensions
        if not dims:
            return []

        history: list[ConfigurationCandidate] = []
        n_elites = max(1, int(self.population_size * self.elite_pct))

        def make(chrom: list[int], gen: int) -> ConfigurationCandidate:
            choices = encoder.decode(chrom)
            c = _make_candidate(choices, evaluator, constraint_engine)
            c.generation = gen
            c.score = objective_fn(c)
            return c

        # Initial population
        pop_chroms = [encoder.random_chromosome(self.rng) for _ in range(self.population_size)]
        pop = [make(ch, 0) for ch in pop_chroms]
        history.extend(pop)

        convergence: list[float] = []

        for gen in range(1, self.generations + 1):
            pop.sort(key=lambda c: c.score)
            convergence.append(pop[0].score)
            elites = pop[:n_elites]
            new_pop = list(elites)

            while len(new_pop) < self.population_size:
                parent_a = self._tournament(pop)
                parent_b = self._tournament(pop)
                ch_a = encoder.encode(parent_a)
                ch_b = encoder.encode(parent_b)
                child_ch = self._crossover(ch_a, ch_b)
                child_ch = self._mutate(child_ch, dims)
                child = make(child_ch, gen)
                new_pop.append(child)
                history.append(child)

            pop = new_pop

        pop.sort(key=lambda c: c.score)
        pop[0].score = objective_fn(pop[0])
        return history

    def _tournament(self, pop: list[ConfigurationCandidate]) -> ConfigurationCandidate:
        contestants = self.rng.sample(pop, min(self.tournament_k, len(pop)))
        return min(contestants, key=lambda c: c.score)

    def _crossover(self, a: list[int], b: list[int]) -> list[int]:
        if len(a) <= 1:
            return list(a)
        pt = self.rng.randint(1, len(a) - 1)
        return a[:pt] + b[pt:]

    def _mutate(self, chrom: list[int], dims) -> list[int]:
        result = list(chrom)
        for i, dim in enumerate(dims):
            if self.rng.random() < self.mutation_rate and dim.n_options > 1:
                result[i] = self.rng.randrange(dim.n_options)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Unified Optimization Engine
# ─────────────────────────────────────────────────────────────────────────────

class OptimizationEngine:
    def __init__(
        self,
        bom: BOM,
        routing: ProcessRouting | None,
        suppliers: dict[str, SupplierProfile],
        constraints: list[Constraint],
        objectives: list[ObjectiveType],
        objective_weights: list[float],
        annual_volume: int = 10_000,
    ) -> None:
        from .search_space import SearchSpaceBuilder, BaselineExtractor
        self.bom = bom
        self.routing = routing
        self.evaluator = CostEvaluator(bom, routing, suppliers, annual_volume)
        self.constraint_engine = ConstraintEngine(constraints, bom, suppliers)
        self.objective_fn = build_objective_fn(objectives, objective_weights)
        self.objectives = objectives

        builder = SearchSpaceBuilder()
        self.space = builder.build(bom, routing, list(suppliers.values()))
        self.encoder = ConfigurationEncoder(self.space)
        self.baseline = BaselineExtractor().extract(bom, routing, self.space)
        self.evaluator.evaluate(self.baseline)
        self.constraint_engine.check(self.baseline)
        self.baseline.score = self.objective_fn(self.baseline)

    def run(
        self,
        algorithm: OptimizationAlgorithm = OptimizationAlgorithm.GENETIC,
        population_size: int = 50,
        generations: int = 30,
        mutation_rate: float = 0.15,
        seed: int | None = 42,
    ) -> OptimizationRun:
        t0 = time.time()
        log.info("OptimizationEngine.run algorithm=%s", algorithm)

        if algorithm == OptimizationAlgorithm.GREEDY:
            opt = GreedyOptimizer()
            history = opt.optimize(self.space, self.encoder, self.evaluator,
                                   self.constraint_engine, self.objective_fn)
        elif algorithm == OptimizationAlgorithm.BEAM_SEARCH:
            opt = BeamSearchOptimizer(beam_width=10)
            history = opt.optimize(self.space, self.encoder, self.evaluator,
                                   self.constraint_engine, self.objective_fn)
        elif algorithm == OptimizationAlgorithm.SIMULATED_ANNEALING:
            opt = SimulatedAnnealingOptimizer(seed=seed)
            history = opt.optimize(self.space, self.encoder, self.evaluator,
                                   self.constraint_engine, self.objective_fn)
        elif algorithm == OptimizationAlgorithm.GENETIC:
            opt = GeneticOptimizer(population_size=population_size,
                                   generations=generations,
                                   mutation_rate=mutation_rate, seed=seed)
            history = opt.optimize(self.space, self.encoder, self.evaluator,
                                   self.constraint_engine, self.objective_fn)
        else:
            # Exhaustive (only safe for small spaces)
            history = self._exhaustive()

        feasible = [c for c in history if c.feasible]
        if not feasible:
            feasible = sorted(history, key=lambda c: sum(v.penalty for v in c.violations))[:1]

        feasible.sort(key=lambda c: c.score)

        # Convergence: best score per generation bucket
        n_gen = max(g + 1 for c in history for g in [c.generation]) if history else 1
        convergence = []
        for g in range(n_gen + 1):
            gen_group = [c for c in history if c.generation == g]
            if gen_group:
                convergence.append(min(c.score for c in gen_group))

        run = OptimizationRun(
            algorithm=algorithm,
            objectives=self.objectives,
            n_evaluated=len(history),
            n_feasible=len(feasible),
            n_generations=n_gen,
            run_time_s=round(time.time() - t0, 3),
            convergence_history=convergence,
            best_candidate=feasible[0] if feasible else None,
            pareto_front=[],  # filled by multi_objective engine
            all_candidates=history,
        )
        return run

    def _exhaustive(self) -> list[ConfigurationCandidate]:
        dims = self.space.dimensions
        n = self.space.total_combinations
        if n > 10_000:
            log.warning("Exhaustive search on %d combinations — capped at 10,000", n)
        history = []
        indices = [0] * len(dims)
        for _ in range(min(n, 10_000)):
            choices = {d.dim_id: d.option_ids[indices[i]] for i, d in enumerate(dims)}
            c = _make_candidate(choices, self.evaluator, self.constraint_engine)
            c.score = self.objective_fn(c)
            history.append(c)
            # Increment indices
            for k in range(len(indices) - 1, -1, -1):
                indices[k] += 1
                if indices[k] < dims[k].n_options:
                    break
                indices[k] = 0
        return history
