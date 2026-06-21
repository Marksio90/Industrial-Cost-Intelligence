"""
Section 5 — Optimization Layer

Optymalizacja parametrów fabryki:
  - ThroughputOptimizer: maksymalizacja przepustowości (buffer sizes, batch sizes)
  - SchedulingRuleOptimizer: porównanie reguł szeregowania przez symulację
  - MaintenanceOptimizer: optymalizacja harmonogramu PM (MTBF × cost)
  - EnergyOptimizer: przesunięcie procesów energochłonnych poza szczyty taryfowe
  - WorkerAssignmentOptimizer: przypisanie pracowników do maszyn (skill matching)
  - GeneticOptimizer: ogólny algorytm genetyczny dla przestrzeni dyskretnej
  - SimulatedAnnealingOptimizer: SA dla ciągłych parametrów
  - OptimizationEngine: unified entry point
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
    FactoryLayout, SimulationConfig, FactoryKPI,
    SchedulingRule, OptimizationGoal, MachineType, Worker,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Objective function
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate(
    layout: FactoryLayout,
    config: SimulationConfig,
) -> FactoryKPI:
    """Run a simulation and return KPI."""
    from .simulation_model import FactorySimulator
    sim = FactorySimulator(copy.deepcopy(layout), copy.deepcopy(config))
    result = sim.run()
    return result.factory_kpi


def _score(kpi: FactoryKPI, goal: OptimizationGoal) -> float:
    """Convert KPI to a scalar score (higher = better)."""
    if goal == OptimizationGoal.MAX_THROUGHPUT:
        return kpi.throughput_h
    elif goal == OptimizationGoal.MIN_MAKESPAN:
        return -kpi.makespan_h
    elif goal == OptimizationGoal.MIN_TARDINESS:
        return -kpi.late_orders
    elif goal == OptimizationGoal.MAX_OEE:
        return kpi.avg_oee
    elif goal == OptimizationGoal.MIN_WIP:
        return -kpi.avg_wip
    elif goal == OptimizationGoal.MIN_ENERGY:
        return -kpi.total_energy_kwh
    else:  # BALANCED
        return (kpi.avg_oee / 100) * 0.3 + (kpi.throughput_h / max(kpi.throughput_h, 1)) * 0.3 \
               + (kpi.on_time_delivery / 100) * 0.2 - (kpi.avg_wip / max(kpi.avg_wip, 1)) * 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling Rule Optimizer (simulation-based comparison)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleComparisonResult:
    rule:       SchedulingRule
    kpi:        FactoryKPI
    score:      float
    run_time_s: float


class SchedulingRuleOptimizer:
    """
    Runs simulation with each scheduling rule and ranks them by objective score.
    """

    def optimize(
        self,
        layout:    FactoryLayout,
        config:    SimulationConfig,
        goal:      OptimizationGoal = OptimizationGoal.MAX_THROUGHPUT,
        rules:     list[SchedulingRule] | None = None,
    ) -> list[RuleComparisonResult]:
        rules = rules or list(SchedulingRule)
        results: list[RuleComparisonResult] = []

        for rule in rules:
            cfg = copy.deepcopy(config)
            cfg.scheduling_rule = rule
            t0 = time.perf_counter()
            try:
                kpi   = _evaluate(layout, cfg)
                score = _score(kpi, goal)
                results.append(RuleComparisonResult(rule, kpi, round(score, 4), round(time.perf_counter() - t0, 2)))
            except Exception as exc:
                log.warning("Rule %s evaluation failed: %s", rule, exc)

        results.sort(key=lambda r: r.score, reverse=True)
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Buffer Size Optimizer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BufferOptResult:
    buffer_id:    str
    optimal_cap:  int
    baseline_cap: int
    baseline_kpi: FactoryKPI
    optimal_kpi:  FactoryKPI
    score_delta:  float


class BufferOptimizer:
    """
    Optimizes buffer capacities via golden-section search on each buffer.
    Objective: minimize WIP × lead_time product (Little's Law).
    """

    def optimize(
        self,
        layout:    FactoryLayout,
        config:    SimulationConfig,
        goal:      OptimizationGoal = OptimizationGoal.MIN_WIP,
        cap_range: tuple[int, int]  = (10, 500),
        steps:     int = 6,
    ) -> list[BufferOptResult]:
        results: list[BufferOptResult] = []
        baseline_kpi = _evaluate(layout, config)
        base_score   = _score(baseline_kpi, goal)

        for bid, buf in layout.buffers.items():
            lo, hi = cap_range
            orig_cap = buf.capacity
            best_cap = orig_cap
            best_score = base_score

            # Discrete sweep
            caps = [lo + int((hi - lo) * i / max(steps - 1, 1)) for i in range(steps)]
            caps = sorted(set(caps + [orig_cap]))

            for cap in caps:
                trial = copy.deepcopy(layout)
                trial.buffers[bid].capacity = cap
                try:
                    kpi   = _evaluate(trial, config)
                    score = _score(kpi, goal)
                    if score > best_score:
                        best_score = score
                        best_cap   = cap
                except Exception:
                    pass

            opt_layout = copy.deepcopy(layout)
            opt_layout.buffers[bid].capacity = best_cap
            opt_kpi = _evaluate(opt_layout, config)

            results.append(BufferOptResult(
                buffer_id    = bid,
                optimal_cap  = best_cap,
                baseline_cap = orig_cap,
                baseline_kpi = baseline_kpi,
                optimal_kpi  = opt_kpi,
                score_delta  = round(_score(opt_kpi, goal) - base_score, 4),
            ))

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance Optimizer (PM interval)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MaintenanceOptResult:
    machine_id:       str
    optimal_pm_frac:  float    # fraction of MTBF for PM interval
    baseline_oee:     float
    optimal_oee:      float
    breakdowns_saved: int


class MaintenanceOptimizer:
    """
    Finds optimal PM interval (as fraction of MTBF) per machine.
    Trades off PM downtime vs breakdown avoidance.
    OEE improvement = key metric.
    """

    def optimize(
        self,
        layout: FactoryLayout,
        config: SimulationConfig,
        pm_fractions: list[float] | None = None,
    ) -> list[MaintenanceOptResult]:
        pm_fractions = pm_fractions or [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        results: list[MaintenanceOptResult] = []
        baseline_kpi = _evaluate(layout, config)

        for mid, machine in layout.machines.items():
            baseline_oee = baseline_kpi.machine_kpis.get(mid, type("", (), {"oee": 0.0})()).oee
            best_oee  = baseline_oee
            best_frac = 0.8
            best_bd   = baseline_kpi.machine_kpis.get(mid, type("", (), {"breakdown_count": 0})()).breakdown_count

            for frac in pm_fractions:
                trial = copy.deepcopy(layout)
                trial.machines[mid].failure_model.mtbf_hours *= frac
                try:
                    kpi  = _evaluate(trial, config)
                    mkpi = kpi.machine_kpis.get(mid)
                    if mkpi and mkpi.oee > best_oee:
                        best_oee  = mkpi.oee
                        best_frac = frac
                        best_bd   = mkpi.breakdown_count
                except Exception:
                    pass

            results.append(MaintenanceOptResult(
                machine_id       = mid,
                optimal_pm_frac  = best_frac,
                baseline_oee     = round(baseline_oee, 2),
                optimal_oee      = round(best_oee, 2),
                breakdowns_saved = best_bd,
            ))

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Energy Optimizer (shift demand response)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EnergyShiftRecommendation:
    machine_id:     str
    current_peak_h: float    # hour when machine runs (0–23)
    suggested_h:    float    # suggested off-peak hour
    energy_saving_eur: float
    production_impact: str   # "none" | "minor" | "moderate"


class EnergyOptimizer:
    """
    Identifies high-power machines running during peak tariff hours
    and recommends shifting them to off-peak hours.
    """

    def optimize(
        self,
        layout: FactoryLayout,
        horizon_hours: float = 8.0,
    ) -> list[EnergyShiftRecommendation]:
        tariff = layout.energy_tariff or {h: 0.15 for h in range(24)}
        peak_hours   = [h for h, p in tariff.items() if p == max(tariff.values())]
        offpeak_hours = [h for h, p in tariff.items() if p == min(tariff.values())]

        recs: list[EnergyShiftRecommendation] = []
        for mid, machine in layout.machines.items():
            kw = machine.energy.processing_kw
            if kw < 10:
                continue   # low-power machines not worth shifting

            # Estimate peak energy cost
            peak_kwh = kw * horizon_hours
            peak_cost = peak_kwh * max(tariff.values())
            offpeak_cost = peak_kwh * min(tariff.values())
            saving = peak_cost - offpeak_cost

            recs.append(EnergyShiftRecommendation(
                machine_id      = mid,
                current_peak_h  = peak_hours[0] if peak_hours else 14,
                suggested_h     = offpeak_hours[0] if offpeak_hours else 22,
                energy_saving_eur = round(saving, 2),
                production_impact = "minor" if kw < 30 else "moderate",
            ))

        recs.sort(key=lambda r: r.energy_saving_eur, reverse=True)
        return recs


# ─────────────────────────────────────────────────────────────────────────────
# Worker Assignment Optimizer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkerAssignment:
    worker_id:  str
    machine_id: str
    skill_match: float    # 0–1
    utilization_benefit: float


class WorkerAssignmentOptimizer:
    """
    Assigns workers to machines maximizing skill match and coverage.
    Uses greedy bipartite matching.
    """

    def optimize(self, layout: FactoryLayout) -> list[WorkerAssignment]:
        assignments: list[WorkerAssignment] = []
        assigned_workers: set[str] = set()
        assigned_machines: set[str] = set()

        # Score matrix
        scores: list[tuple[float, str, str]] = []
        for wid, worker in layout.workers.items():
            for mid, machine in layout.machines.items():
                # Skill match
                match = 0.0
                for skill in worker.skills:
                    if machine.machine_type in skill.machine_types:
                        match = max(match, skill.skill_level)
                if match > 0:
                    scores.append((match, wid, mid))

        # Greedy: best match first
        scores.sort(reverse=True)
        for match, wid, mid in scores:
            if wid in assigned_workers or mid in assigned_machines:
                continue
            assignments.append(WorkerAssignment(
                worker_id            = wid,
                machine_id           = mid,
                skill_match          = round(match, 3),
                utilization_benefit  = round(match * 0.15, 3),  # up to +15% OEE
            ))
            assigned_workers.add(wid)
            assigned_machines.add(mid)

        return assignments


# ─────────────────────────────────────────────────────────────────────────────
# Genetic Optimizer (general purpose)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptimizationCandidate:
    params:     dict[str, Any]
    kpi:        FactoryKPI | None
    score:      float
    generation: int = 0


class GeneticOptimizer:
    """
    Genetic algorithm for factory parameter optimization.
    Decision space: scheduling rule, buffer sizes, PM fractions.

    Chromosome: dict[param_name → discrete_option_index]
    Fitness: simulation KPI score for given goal.
    """

    def __init__(
        self,
        population_size: int = 10,
        generations:     int = 5,
        mutation_rate:   float = 0.20,
        elite_k:         int = 2,
        seed:            int | None = None,
    ) -> None:
        self.pop_size      = population_size
        self.generations   = generations
        self.mutation_rate = mutation_rate
        self.elite_k       = elite_k
        self._rng          = random.Random(seed)

    def optimize(
        self,
        layout:  FactoryLayout,
        config:  SimulationConfig,
        goal:    OptimizationGoal = OptimizationGoal.MAX_OEE,
    ) -> list[OptimizationCandidate]:
        # Parameter space
        rules   = list(SchedulingRule)
        buf_ids = list(layout.buffers.keys())
        caps    = [20, 50, 100, 200, 500]

        def random_chrom() -> dict[str, Any]:
            chrom: dict[str, Any] = {
                "scheduling_rule": self._rng.choice(rules),
            }
            for bid in buf_ids:
                chrom[f"buf_{bid}"] = self._rng.choice(caps)
            return chrom

        def decode(chrom: dict[str, Any]) -> tuple[FactoryLayout, SimulationConfig]:
            trial_layout = copy.deepcopy(layout)
            trial_config = copy.deepcopy(config)
            trial_config.scheduling_rule = chrom["scheduling_rule"]
            for bid in buf_ids:
                if bid in trial_layout.buffers:
                    trial_layout.buffers[bid].capacity = chrom.get(f"buf_{bid}", 100)
            return trial_layout, trial_config

        def fitness(chrom: dict[str, Any]) -> tuple[FactoryKPI | None, float]:
            try:
                tl, tc = decode(chrom)
                kpi = _evaluate(tl, tc)
                return kpi, _score(kpi, goal)
            except Exception as exc:
                log.debug("Genetic eval failed: %s", exc)
                return None, float("-inf")

        population = [random_chrom() for _ in range(self.pop_size)]
        history:    list[OptimizationCandidate] = []

        for gen in range(self.generations):
            scored = []
            for chrom in population:
                kpi, sc = fitness(chrom)
                scored.append((sc, chrom, kpi))
            scored.sort(key=lambda x: x[0], reverse=True)

            if scored:
                sc, ch, kpi = scored[0]
                history.append(OptimizationCandidate(ch, kpi, round(sc, 4), gen))

            # Elites
            elites = [c for _, c, _ in scored[:self.elite_k]]

            # Crossover + mutation
            new_pop = list(elites)
            while len(new_pop) < self.pop_size:
                p1 = self._rng.choice(elites)
                p2 = self._rng.choice(elites)
                child: dict[str, Any] = {}
                for key in p1:
                    child[key] = p1[key] if self._rng.random() < 0.5 else p2[key]
                # Mutation
                for key in child:
                    if self._rng.random() < self.mutation_rate:
                        if key == "scheduling_rule":
                            child[key] = self._rng.choice(rules)
                        else:
                            child[key] = self._rng.choice(caps)
                new_pop.append(child)
            population = new_pop

        return history


# ─────────────────────────────────────────────────────────────────────────────
# Simulated Annealing (continuous parameters)
# ─────────────────────────────────────────────────────────────────────────────

class SimulatedAnnealingOptimizer:
    """
    SA for continuous factory parameters:
    - Machine cycle time targets (process improvement)
    - MTBF targets (investment in reliability)

    Perturbs one parameter at a time.
    """

    def __init__(
        self,
        T_start: float = 100.0,
        T_min:   float = 1.0,
        alpha:   float = 0.85,
        steps:   int   = 20,
        seed:    int | None = None,
    ) -> None:
        self.T_start = T_start
        self.T_min   = T_min
        self.alpha   = alpha
        self.steps   = steps
        self._rng    = random.Random(seed)

    def optimize(
        self,
        layout:  FactoryLayout,
        config:  SimulationConfig,
        goal:    OptimizationGoal = OptimizationGoal.MAX_OEE,
    ) -> list[OptimizationCandidate]:
        current = copy.deepcopy(layout)
        best    = copy.deepcopy(layout)
        kpi     = _evaluate(current, config)
        score   = _score(kpi, goal)
        best_sc = score
        T       = self.T_start
        history: list[OptimizationCandidate] = [OptimizationCandidate({}, kpi, round(score, 4), 0)]

        for step in range(self.steps):
            if T < self.T_min:
                break

            trial = copy.deepcopy(current)
            # Perturb: randomly adjust one machine's cycle time ±10%
            mid = self._rng.choice(list(trial.machines.keys()))
            m   = trial.machines[mid]
            m.cycle_time_s = max(10.0, m.cycle_time_s * (1 + self._rng.uniform(-0.10, 0.10)))

            try:
                trial_kpi   = _evaluate(trial, config)
                trial_score = _score(trial_kpi, goal)
                delta       = trial_score - score

                if delta > 0 or self._rng.random() < math.exp(delta / T):
                    current = trial
                    score   = trial_score
                    kpi     = trial_kpi
                    if trial_score > best_sc:
                        best    = copy.deepcopy(trial)
                        best_sc = trial_score

                history.append(OptimizationCandidate(
                    {"perturbed_machine": mid, "cycle_time_s": m.cycle_time_s},
                    trial_kpi, round(trial_score, 4), step
                ))
            except Exception as exc:
                log.debug("SA step %d failed: %s", step, exc)

            T *= self.alpha

        return history


# ─────────────────────────────────────────────────────────────────────────────
# Unified Optimization Engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptimizationReport:
    goal:           OptimizationGoal
    algorithm:      str
    baseline_score: float
    best_score:     float
    improvement_pct: float
    best_params:    dict[str, Any]
    best_kpi:       FactoryKPI | None
    history:        list[OptimizationCandidate]
    recommendations: list[str]
    run_time_s:     float


class OptimizationEngine:
    """
    Unified factory optimization engine.

    Algorithms:
      greedy  → SchedulingRuleOptimizer (fastest)
      genetic → GeneticOptimizer (exploration)
      sa      → SimulatedAnnealingOptimizer (continuous)
      full    → all above + BufferOptimizer + MaintenanceOptimizer
    """

    def optimize(
        self,
        layout:     FactoryLayout,
        config:     SimulationConfig,
        goal:       OptimizationGoal = OptimizationGoal.MAX_OEE,
        algorithm:  str = "greedy",
        max_iter:   int = 10,
        pop_size:   int = 10,
        seed:       int | None = None,
    ) -> OptimizationReport:
        t0 = time.perf_counter()

        baseline_kpi = _evaluate(layout, config)
        baseline_sc  = _score(baseline_kpi, goal)

        history: list[OptimizationCandidate] = [
            OptimizationCandidate({}, baseline_kpi, round(baseline_sc, 4), 0)
        ]
        best_kpi = baseline_kpi
        best_sc  = baseline_sc
        best_params: dict[str, Any] = {"scheduling_rule": config.scheduling_rule.value}

        if algorithm in {"greedy", "full"}:
            rule_opt = SchedulingRuleOptimizer()
            rule_results = rule_opt.optimize(layout, config, goal)
            if rule_results and rule_results[0].score > best_sc:
                best_sc     = rule_results[0].score
                best_kpi    = rule_results[0].kpi
                best_params["scheduling_rule"] = rule_results[0].rule.value
            for r in rule_results:
                history.append(OptimizationCandidate(
                    {"scheduling_rule": r.rule.value}, r.kpi, round(r.score, 4), 0
                ))

        if algorithm in {"genetic", "full"}:
            gen_opt = GeneticOptimizer(pop_size, max_iter, seed=seed)
            gen_history = gen_opt.optimize(layout, config, goal)
            for c in gen_history:
                history.append(c)
                if c.score > best_sc:
                    best_sc     = c.score
                    best_kpi    = c.kpi
                    best_params = dict(c.params)

        if algorithm in {"sa"}:
            sa_opt = SimulatedAnnealingOptimizer(steps=max_iter, seed=seed)
            sa_history = sa_opt.optimize(layout, config, goal)
            for c in sa_history:
                history.append(c)
                if c.score > best_sc:
                    best_sc     = c.score
                    best_kpi    = c.kpi
                    best_params = dict(c.params)

        improvement_pct = ((best_sc - baseline_sc) / abs(baseline_sc) * 100.0) if baseline_sc else 0.0

        recs = self._generate_recommendations(baseline_kpi, best_kpi, goal)

        return OptimizationReport(
            goal             = goal,
            algorithm        = algorithm,
            baseline_score   = round(baseline_sc, 4),
            best_score       = round(best_sc, 4),
            improvement_pct  = round(improvement_pct, 2),
            best_params      = best_params,
            best_kpi         = best_kpi,
            history          = history,
            recommendations  = recs,
            run_time_s       = round(time.perf_counter() - t0, 2),
        )

    def _generate_recommendations(
        self,
        baseline: FactoryKPI,
        best:     FactoryKPI | None,
        goal:     OptimizationGoal,
    ) -> list[str]:
        recs: list[str] = []
        if not best:
            return recs
        if best.avg_oee > baseline.avg_oee + 2:
            recs.append(f"Zmiana parametrów poprawia OEE o {best.avg_oee - baseline.avg_oee:.1f}% → wdróż zmiany")
        if baseline.bottleneck_id:
            recs.append(f"Wąskie gardło: {baseline.bottleneck_id} — rozważ równoległe maszyny lub redukcję czasu cyklu")
        if baseline.scrap_rate > 2.0:
            recs.append(f"Wskaźnik braków {baseline.scrap_rate:.1f}% — priorytet SPC i kalibracja maszyn")
        if baseline.avg_wip > 50:
            recs.append(f"Wysoki WIP {baseline.avg_wip:.0f} szt — zredukuj rozmiary buforów lub zmień regułę na EDD")
        if baseline.on_time_delivery < 90:
            recs.append(f"OTD {baseline.on_time_delivery:.1f}% — zmień regułę na SLACK lub CRITICAL_RATIO")
        if best.total_energy_kwh < baseline.total_energy_kwh * 0.95:
            saving = baseline.energy_cost_eur - best.energy_cost_eur
            recs.append(f"Optymalizacja energii oszczędza {saving:.2f} EUR/zmianę")
        return recs
