"""
Section 4 — Simulation Layer

Monte Carlo symulacja pełnego modelu świata:
  • N ścieżek (domyślnie 500) dla 12 miesięcy
  • Korelacje między sygnałami (energy-commodity, FX-interest)
  • Skoki (jump diffusion) dla szoków geopolitycznych
  • Percentyle rozkładu kosztów P5/P25/P50/P75/P95
  • Value at Risk (VaR) i Conditional VaR (CVaR)
  • Probability of exceeding cost threshold
"""
from __future__ import annotations

import math
import random
import statistics
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    WorldState, ScenarioType, MonteCarloPath, SimulationResult,
    GeopoliticalState,
)

log = logging.getLogger(__name__)

# Correlation matrix (simplified Cholesky-like)
# Signals: [oil, gas, elec, steel, alum, copper, freight, fx_eurusd, cpi]
_CORR = [
#   oil   gas  elec  steel alum  copper freight  fx    cpi
    [1.00, 0.55, 0.40, 0.30, 0.25, 0.20, 0.30, -0.10, 0.25],  # oil
    [0.55, 1.00, 0.65, 0.20, 0.35, 0.15, 0.20, -0.05, 0.30],  # gas
    [0.40, 0.65, 1.00, 0.30, 0.50, 0.20, 0.15, -0.05, 0.25],  # elec
    [0.30, 0.20, 0.30, 1.00, 0.45, 0.55, 0.25, -0.15, 0.20],  # steel
    [0.25, 0.35, 0.50, 0.45, 1.00, 0.40, 0.20, -0.10, 0.15],  # alum
    [0.20, 0.15, 0.20, 0.55, 0.40, 1.00, 0.20, -0.20, 0.10],  # copper
    [0.30, 0.20, 0.15, 0.25, 0.20, 0.20, 1.00, -0.05, 0.15],  # freight
    [-0.10,-0.05,-0.05,-0.15,-0.10,-0.20,-0.05,  1.00,-0.10],  # fx_eurusd
    [0.25, 0.30, 0.25, 0.20, 0.15, 0.10, 0.15, -0.10, 1.00],  # cpi
]


def _cholesky(corr: list[list[float]]) -> list[list[float]]:
    """Simple Cholesky decomposition for correlation matrix."""
    n = len(corr)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                val = corr[i][i] - s
                L[i][j] = math.sqrt(max(val, 1e-10))
            else:
                L[i][j] = (corr[i][j] - s) / (L[j][j] + 1e-12)
    return L


_L_CHOL = _cholesky(_CORR)


def _correlated_normals(L: list[list[float]], rng: random.Random) -> list[float]:
    """Draw correlated standard normals using Cholesky factor."""
    n = len(L)
    z = [rng.gauss(0, 1) for _ in range(n)]
    return [sum(L[i][j] * z[j] for j in range(i + 1)) for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# Single path generator
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_path(
    world: WorldState,
    path_id: int,
    scenario: ScenarioType,
    cost_weights: dict[str, float],
    rng: random.Random,
) -> MonteCarloPath:
    """
    Simulate 12 monthly steps for all key signals.
    Returns cost path indexed [month 0..12].
    """
    dt = 1 / 12
    geo = world.geopolitics
    en  = world.energy
    cm  = world.commodities
    fx  = world.fx

    # Scenario shock multipliers
    shocks = _scenario_shocks(scenario, geo)

    # Initial values [oil, gas, elec, steel, alum, copper, freight, fx_eurusd, cpi]
    vals = [
        en.oil_usd_bbl * shocks.get("oil", 1.0),
        en.natural_gas_eur_mwh * shocks.get("gas", 1.0),
        en.electricity_eur_mwh * shocks.get("elec", 1.0),
        cm.steel_hrc_eur_t * shocks.get("steel", 1.0),
        cm.aluminum_eur_t * shocks.get("alum", 1.0),
        cm.copper_eur_t * shocks.get("copper", 1.0),
        world.logistics.shanghai_rotterdam_usd * shocks.get("freight", 1.0),
        fx.eur_usd * shocks.get("fx_eurusd", 1.0),
        world.inflation.cpi_yoy * shocks.get("cpi", 1.0),
    ]

    # Long-run means (mean reversion targets)
    mu   = [80.0, 40.0, 90.0, 620.0, 2300.0, 8800.0, 2000.0, 1.08, 2.5]
    sig  = [0.25,  0.40, 0.30, 0.20,  0.18,   0.22,   0.35,  0.08, 0.08]
    theta= [0.6,   0.5,  0.4,  0.5,   0.5,    0.4,    0.8,   1.0,  0.3]  # OU speed

    # Jump probabilities per month (geopolitical shocks)
    jump_prob = _jump_prob(scenario, geo)

    cost_path = [_cost_from_vals(vals, cost_weights, world)]
    fx_path   = [vals[7]]
    en_path   = [vals[0]]
    cm_path   = [vals[3]]

    for _ in range(12):
        corr_normals = _correlated_normals(_L_CHOL, rng)
        new_vals = []
        for i, (v, m, s, th) in enumerate(zip(vals, mu, sig, theta)):
            # OU + GBM blend
            dW = corr_normals[i] * math.sqrt(dt)
            ou_drift = th * (m - v) * dt
            v_new = v + ou_drift + s * v * dW
            # Jump event
            if rng.random() < jump_prob:
                jump_size = rng.gauss(0.15, 0.10)  # mean +15% jump
                v_new *= (1 + jump_size)
            new_vals.append(max(v_new, 0.001))
        vals = new_vals
        cost_path.append(_cost_from_vals(vals, cost_weights, world))
        fx_path.append(vals[7])
        en_path.append(vals[0])
        cm_path.append(vals[3])

    return MonteCarloPath(
        path_id=path_id,
        scenario_type=scenario,
        cost_path=cost_path,
        fx_path=fx_path,
        energy_path=en_path,
        commodity_path=cm_path,
    )


def _cost_from_vals(
    vals: list[float],
    weights: dict[str, float],
    world: WorldState,
) -> float:
    """Compute relative cost index (1.0 = base)."""
    oil, gas, elec, steel, alum, copper, freight, fx_eurusd, cpi = vals

    base_oil   = world.energy.oil_usd_bbl
    base_gas   = world.energy.natural_gas_eur_mwh
    base_elec  = world.energy.electricity_eur_mwh
    base_steel = world.commodities.steel_hrc_eur_t
    base_alum  = world.commodities.aluminum_eur_t
    base_copper= world.commodities.copper_eur_t
    base_fr    = world.logistics.shanghai_rotterdam_usd
    base_cpi   = world.inflation.cpi_yoy

    mp  = weights.get("material_pct", 0.40)
    ep  = weights.get("energy_pct",   0.15)
    lp  = weights.get("logistics_pct",0.10)
    op  = weights.get("overhead_pct", 0.10)
    lbp = weights.get("labor_pct",    0.25)
    ss  = weights.get("steel_share",  0.50)
    as_ = weights.get("alum_share",   0.20)
    cs  = weights.get("copper_share", 0.10)
    usd = weights.get("usd_exposure", 0.30)

    mat_idx = (steel/base_steel * ss + alum/base_alum * as_ +
               copper/base_copper * cs + (1-ss-as_-cs)) / max(ss+as_+cs, 0.01)
    en_idx  = (elec/base_elec * 0.6 + gas/base_gas * 0.25 + (oil/base_oil/base_oil * base_oil * world.fx.eur_usd) / base_elec * 0.15)
    fr_idx  = freight / base_fr
    cpi_idx = 1 + (cpi - base_cpi) / 100 * 0.5
    fx_adj  = 1 + (base_oil - oil * world.fx.eur_usd / fx_eurusd) / base_oil * usd * 0.3

    cost_idx = (mp * mat_idx + ep * en_idx + lp * fr_idx +
                op * cpi_idx + lbp * cpi_idx) * fx_adj
    return max(cost_idx, 0.5)


def _scenario_shocks(scenario: ScenarioType, geo: GeopoliticalState) -> dict[str, float]:
    if scenario == ScenarioType.OPTIMISTIC:
        return {"oil": 0.85, "gas": 0.80, "elec": 0.90, "freight": 0.80, "steel": 0.90}
    if scenario == ScenarioType.PESSIMISTIC:
        return {"oil": 1.25, "gas": 1.35, "elec": 1.20, "freight": 1.25, "steel": 1.15, "cpi": 1.30}
    if scenario == ScenarioType.SHOCK_ENERGY:
        return {"oil": 1.60, "gas": 2.50, "elec": 2.00, "freight": 1.30}
    if scenario == ScenarioType.SHOCK_WAR:
        return {"oil": 1.50, "gas": 2.00, "steel": 1.40, "freight": 1.80, "alum": 1.30}
    if scenario == ScenarioType.SHOCK_RECESSION:
        return {"oil": 0.70, "steel": 0.75, "copper": 0.80, "freight": 0.65, "cpi": 1.10}
    if scenario == ScenarioType.STAGFLATION:
        return {"oil": 1.30, "gas": 1.40, "cpi": 1.60, "steel": 1.10}
    return {}  # BASELINE — no shocks


def _jump_prob(scenario: ScenarioType, geo: GeopoliticalState) -> float:
    base = geo.supply_chain_stress * 0.02   # ~2% per month at stress=1
    extras = {
        ScenarioType.SHOCK_WAR:     0.08,
        ScenarioType.SHOCK_ENERGY:  0.06,
        ScenarioType.PESSIMISTIC:   0.03,
        ScenarioType.STAGFLATION:   0.04,
    }
    return base + extras.get(scenario, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo Engine
# ─────────────────────────────────────────────────────────────────────────────

def _dist(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    n = len(s)
    def p(pct): return s[max(0, min(int(n * pct / 100), n - 1))]
    return {"p5": p(5), "p25": p(25), "p50": p(50), "p75": p(75), "p95": p(95),
            "mean": statistics.mean(s), "std": statistics.stdev(s) if n > 1 else 0}


def _var(values: list[float], pct: float = 95.0) -> float:
    s = sorted(values)
    idx = int(len(s) * pct / 100)
    return s[min(idx, len(s) - 1)]


def _cvar(values: list[float], pct: float = 95.0) -> float:
    threshold = _var(values, pct)
    tail = [v for v in values if v >= threshold]
    return statistics.mean(tail) if tail else threshold


class MonteCarloEngine:
    def __init__(self, n_paths: int = 500, seed: int = 42) -> None:
        self.n_paths = n_paths
        self.rng = random.Random(seed)

    def simulate(
        self,
        world: WorldState,
        base_cost_eur: float,
        scenario: ScenarioType = ScenarioType.BASELINE,
        cost_weights: dict[str, float] | None = None,
        threshold_pct: float = 10.0,   # alert if cost +10%
    ) -> SimulationResult:
        w = cost_weights or {
            "material_pct": 0.40, "energy_pct": 0.15, "logistics_pct": 0.10,
            "overhead_pct": 0.10, "labor_pct": 0.25,
            "steel_share": 0.50, "alum_share": 0.20, "copper_share": 0.10,
            "usd_exposure": 0.30,
        }

        paths: list[MonteCarloPath] = [
            _simulate_path(world, i, scenario, w, self.rng)
            for i in range(self.n_paths)
        ]

        def cost_at(month: int) -> list[float]:
            return [base_cost_eur * p.cost_path[month] for p in paths]

        threshold = base_cost_eur * (1 + threshold_pct / 100)
        vals_12m = cost_at(12)

        prob_above: dict[str, float] = {}
        for label, mult in [("5pct", 1.05), ("10pct", 1.10), ("20pct", 1.20)]:
            thr = base_cost_eur * mult
            prob_above[f"above_{label}"] = sum(1 for v in vals_12m if v > thr) / max(self.n_paths, 1)

        return SimulationResult(
            n_paths=self.n_paths,
            base_cost=base_cost_eur,
            dist_1m=_dist(cost_at(1)),
            dist_3m=_dist(cost_at(3)),
            dist_6m=_dist(cost_at(6)),
            dist_12m=_dist(cost_at(12)),
            var_95_12m=round(_var(vals_12m, 95), 4),
            cvar_95_12m=round(_cvar(vals_12m, 95), 4),
            probability_above_threshold=prob_above,
        )
