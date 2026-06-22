"""
Section 3 — Monte Carlo Layer

Stochastyczna analiza ryzyka zakupowego:
  - N równoległych przebiegów symulacji z różnymi seedami
  - Rozkłady: koszt, lead time, poziom obsługi
  - VaR 95% / CVaR 95% (Expected Shortfall)
  - Analiza czułości (tornado chart) — Sobol / OAT
  - Histogramy wyników
  - Convergence check (running mean stabilisation)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import logging
import math
import random
import statistics
import time
from dataclasses import dataclass
from typing import Any

from .models import (
    MonteCarloConfig, MonteCarloResult, Percentile,
    ScenarioDefinition, SimulationResult, SimulationStatus,
)
from .simulation_engine import SimulationEngine, SimulationState

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Single MC run (pure function — picklable for multiprocessing)
# ─────────────────────────────────────────────────────────────────────────────

def _run_one(args: tuple[SimulationState, ScenarioDefinition, int]) -> dict[str, float]:
    """Run one MC trial; returns scalar KPI dict."""
    state, scenario, seed = args
    state_copy = copy.deepcopy(state)
    engine     = SimulationEngine(state_copy, seed=seed)

    from .scenario_engine import ScenarioRunner
    shock_map: dict[int, list] = {}
    for shock in scenario.shocks:
        shock_map.setdefault(shock.day_offset, []).append(shock)

    for day in range(1, scenario.horizon_days + 1):
        for shock in shock_map.get(day - 1, []):
            engine.apply_shock(
                target_type=shock.target_type,
                target_id=shock.target_id,
                parameter=shock.parameter,
                value=shock.value,
            )
        engine.step()

    total_cost   = sum(o.total_cost for o in engine.state.orders if o.status == "delivered")
    lead_times   = [k.avg_lead_time_days for k in engine.daily_kpis if k.avg_lead_time_days > 0]
    service_lvls = [k.service_level for k in engine.daily_kpis]
    stockout     = sum(1 for k in engine.daily_kpis if k.stockout_materials > 0)

    return {
        "total_cost":      total_cost,
        "avg_lead_time":   statistics.mean(lead_times)  if lead_times  else 0.0,
        "max_lead_time":   max(lead_times)               if lead_times  else 0.0,
        "min_service":     min(service_lvls)             if service_lvls else 0.0,
        "avg_service":     statistics.mean(service_lvls) if service_lvls else 0.0,
        "stockout_days":   stockout,
        "emergency_orders": engine._emergency_orders,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stat helpers
# ─────────────────────────────────────────────────────────────────────────────

def _percentile(data: list[float], q: float) -> float:
    if not data:
        return 0.0
    sorted_d = sorted(data)
    idx = max(0, min(len(sorted_d) - 1, int(math.floor(q * len(sorted_d)))))
    return sorted_d[idx]


def _var(data: list[float], confidence: float = 0.95) -> float:
    """Value at Risk at confidence level (loss tail)."""
    return _percentile(data, confidence)


def _cvar(data: list[float], confidence: float = 0.95) -> float:
    """Conditional VaR (Expected Shortfall) — mean of tail losses."""
    sorted_d = sorted(data)
    cutoff_idx = int(math.floor(confidence * len(sorted_d)))
    tail = sorted_d[cutoff_idx:]
    if not tail:
        return sorted_d[-1]
    return sum(tail) / len(tail)


def _histogram(data: list[float], bins: int = 20) -> list[dict[str, float]]:
    if not data:
        return []
    lo, hi = min(data), max(data)
    if lo == hi:
        return [{"min": lo, "max": hi, "count": len(data), "freq": 1.0}]
    width = (hi - lo) / bins
    buckets: list[dict[str, float]] = []
    for i in range(bins):
        b_lo   = lo + i * width
        b_hi   = b_lo + width
        count  = sum(1 for v in data if b_lo <= v < b_hi)
        buckets.append({"min": round(b_lo, 2), "max": round(b_hi, 2), "count": count, "freq": count / len(data)})
    return buckets


def _coefficient_of_variation(data: list[float]) -> float:
    if not data:
        return 0.0
    mean = statistics.mean(data)
    if mean == 0:
        return 0.0
    std = statistics.stdev(data) if len(data) > 1 else 0.0
    return std / mean


def _check_convergence(runs: list[float], window: int = 50) -> bool:
    """Running mean converged if last `window` runs have <1% relative change."""
    if len(runs) < window * 2:
        return False
    prev_mean = statistics.mean(runs[-2 * window : -window])
    curr_mean = statistics.mean(runs[-window:])
    if prev_mean == 0:
        return False
    return abs(curr_mean - prev_mean) / abs(prev_mean) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# MonteCarloEngine
# ─────────────────────────────────────────────────────────────────────────────

class MonteCarloEngine:
    """
    Parallel Monte Carlo simulation engine.

    Parameters
    ----------
    baseline : SimulationState
    config   : MonteCarloConfig
    """

    def __init__(
        self,
        baseline: SimulationState,
        config:   MonteCarloConfig | None = None,
    ) -> None:
        self._baseline = baseline
        self._config   = config or MonteCarloConfig()

    def run(self, scenario: ScenarioDefinition) -> MonteCarloResult:
        """Run synchronous MC (blocking). Returns MonteCarloResult."""
        result = MonteCarloResult(scenario_id=scenario.scenario_id)
        t0     = time.monotonic()

        rng    = random.Random(self._config.seed)
        seeds  = [rng.randint(1, 2**31) for _ in range(self._config.n_runs)]
        args   = [(self._baseline, scenario, s) for s in seeds]

        trial_data: list[dict[str, float]] = []
        workers = min(self._config.parallel_workers, 4)

        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
                for data in pool.map(_run_one, args, chunksize=max(1, len(args) // workers)):
                    trial_data.append(data)
        except Exception:
            # Fallback to single-threaded (e.g., inside subprocess)
            trial_data = [_run_one(a) for a in args]

        result = self._aggregate(trial_data, scenario, t0)
        return result

    async def run_async(self, scenario: ScenarioDefinition) -> MonteCarloResult:
        """Async wrapper — runs MC in executor thread."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run, scenario)

    def _aggregate(
        self,
        trials: list[dict[str, float]],
        scenario: ScenarioDefinition,
        t0: float,
    ) -> MonteCarloResult:
        costs      = [t["total_cost"]   for t in trials]
        lead_times = [t["avg_lead_time"] for t in trials]
        services   = [t["avg_service"]  for t in trials]
        stockouts  = [t["stockout_days"] for t in trials]

        result = MonteCarloResult(
            scenario_id=scenario.scenario_id,
            n_runs=len(trials),
            horizon_days=scenario.horizon_days,
        )

        # Cost stats
        result.cost_mean = round(statistics.mean(costs), 2)
        result.cost_std  = round(statistics.stdev(costs) if len(costs) > 1 else 0.0, 2)
        result.cost_var  = round(_coefficient_of_variation(costs), 4)
        result.var_95    = round(_var(costs, 0.95), 2)
        result.cvar_95   = round(_cvar(costs, 0.95), 2)

        # Percentiles
        result.cost_percentiles = [
            Percentile(level=q, value=round(_percentile(costs, q), 2))
            for q in self._config.confidence_levels
        ]

        # Lead time
        result.lead_mean = round(statistics.mean(lead_times), 2)
        result.lead_std  = round(statistics.stdev(lead_times) if len(lead_times) > 1 else 0.0, 2)
        result.lead_p95  = round(_percentile(lead_times, 0.95), 2)

        # Service level
        result.service_mean = round(statistics.mean(services), 4)
        result.service_p5   = round(_percentile(services, 0.05), 4)

        # Stockout probability
        result.stockout_prob = round(sum(1 for s in stockouts if s > 0) / len(stockouts), 4)

        # Histograms
        result.cost_histogram = _histogram(costs, bins=25)
        result.lead_histogram = _histogram(lead_times, bins=20)

        # Sensitivity (OAT — one-at-a-time)
        result.sensitivity = self._sensitivity(scenario, len(trials))

        result.duration_s    = round(time.monotonic() - t0, 2)
        result.completed_at  = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        return result

    def _sensitivity(
        self,
        scenario: ScenarioDefinition,
        n_base: int,
    ) -> list[dict[str, Any]]:
        """
        OAT sensitivity: perturb each shock parameter ±10%
        and measure relative cost change. Returns tornado chart data.
        """
        sensitivity: list[dict[str, Any]] = []
        n_oat = min(200, n_base // 5)   # smaller OAT runs for speed

        rng_s = random.Random(self._config.seed + 999)

        for shock in scenario.shocks:
            if not isinstance(shock.value, (int, float)):
                continue
            base_val = float(shock.value)
            results_hi, results_lo = [], []

            for direction, deltas in [("hi", [base_val * 1.10]), ("lo", [base_val * 0.90])]:
                for delta_val in deltas:
                    modified = copy.deepcopy(scenario)
                    for s in modified.shocks:
                        if s.parameter == shock.parameter and s.target_id == shock.target_id:
                            s.value = delta_val
                    seeds = [rng_s.randint(1, 2**31) for _ in range(n_oat)]
                    trial_costs = []
                    for seed in seeds:
                        try:
                            r = _run_one((self._baseline, modified, seed))
                            trial_costs.append(r["total_cost"])
                        except Exception:
                            pass
                    mean_cost = statistics.mean(trial_costs) if trial_costs else 0.0
                    if direction == "hi":
                        results_hi.append(mean_cost)
                    else:
                        results_lo.append(mean_cost)

            base_cost_oat = statistics.mean([
                _run_one((self._baseline, scenario, rng_s.randint(1, 2**31)))["total_cost"]
                for _ in range(n_oat)
            ]) if n_oat > 0 else 1.0

            hi_mean = statistics.mean(results_hi) if results_hi else base_cost_oat
            lo_mean = statistics.mean(results_lo) if results_lo else base_cost_oat

            sensitivity.append({
                "parameter":     shock.parameter,
                "target_id":     shock.target_id,
                "base_value":    base_val,
                "hi_cost_delta": round((hi_mean - base_cost_oat) / max(base_cost_oat, 1) * 100, 2),
                "lo_cost_delta": round((lo_mean - base_cost_oat) / max(base_cost_oat, 1) * 100, 2),
                "swing":         round(abs(hi_mean - lo_mean) / max(base_cost_oat, 1) * 100, 2),
            })

        # Sort by swing (descending) for tornado chart
        sensitivity.sort(key=lambda x: -x["swing"])
        return sensitivity


# ─────────────────────────────────────────────────────────────────────────────
# Convergence analyser
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConvergenceReport:
    n_runs:           int
    converged:        bool
    stable_after:     int | None       # run number when mean stabilised
    running_means:    list[float]      # cost running mean per run
    running_stdevs:   list[float]
    relative_error_pct: float          # final relative error


def analyse_convergence(costs: list[float]) -> ConvergenceReport:
    running_means:  list[float] = []
    running_stdevs: list[float] = []

    for i in range(1, len(costs) + 1):
        subset = costs[:i]
        running_means.append(statistics.mean(subset))
        running_stdevs.append(statistics.stdev(subset) if i > 1 else 0.0)

    stable_after = None
    for i in range(50, len(running_means)):
        prev = running_means[i - 50]
        curr = running_means[i]
        if prev and abs(curr - prev) / abs(prev) < 0.005:
            stable_after = i
            break

    final_mean = running_means[-1] if running_means else 0.0
    final_std  = running_stdevs[-1] if running_stdevs else 0.0
    rel_err    = (final_std / math.sqrt(len(costs))) / max(abs(final_mean), 1) * 100

    return ConvergenceReport(
        n_runs=len(costs),
        converged=stable_after is not None,
        stable_after=stable_after,
        running_means=running_means,
        running_stdevs=running_stdevs,
        relative_error_pct=round(rel_err, 3),
    )
