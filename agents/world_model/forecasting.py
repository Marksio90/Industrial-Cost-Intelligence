"""
Section 3 — Forecasting Layer

Prognozuje każdy sygnał ekonomiczny na horyzonty 1m/3m/6m/12m.

Metody:
  GBM          — Geometric Brownian Motion (dS = μS dt + σS dW)
  Mean Revert  — Ornstein-Uhlenbeck (dX = θ(μ-X)dt + σ dW)
  Trend Proj   — Regresja liniowa na trendzie
  Ensemble     — Ważona kombinacja GBM + MR + Trend

Każda prognoza zawiera:
  • point forecast (median)
  • confidence interval (P5/P95)
  • monthly path
  • uncertainty (szerokość CI)
"""
from __future__ import annotations

import math
import random
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    WorldState, ForecastHorizon, ForecastMethod, SignalForecast,
    CostImpact, HorizonForecast, ProductionCostForecast, ScenarioType,
)

log = logging.getLogger(__name__)

# Horizon → months ahead
_HORIZON_MONTHS = {
    ForecastHorizon.M1:  1,
    ForecastHorizon.M3:  3,
    ForecastHorizon.M6:  6,
    ForecastHorizon.M12: 12,
}


# ─────────────────────────────────────────────────────────────────────────────
# Stochastic processes
# ─────────────────────────────────────────────────────────────────────────────

def _gbm_path(
    S0: float, mu: float, sigma: float, T: int, n_paths: int, rng: random.Random
) -> list[list[float]]:
    """Geometric Brownian Motion — monthly steps."""
    dt = 1 / 12  # monthly
    paths = []
    for _ in range(n_paths):
        S = S0
        path = [S0]
        for _ in range(T):
            dW = rng.gauss(0, 1) * math.sqrt(dt)
            S = S * math.exp((mu - 0.5 * sigma**2) * dt + sigma * dW)
            path.append(max(S, 0.001))
        paths.append(path)
    return paths


def _ou_path(
    X0: float, theta: float, mu: float, sigma: float, T: int, n_paths: int, rng: random.Random
) -> list[list[float]]:
    """Ornstein-Uhlenbeck mean reversion."""
    dt = 1 / 12
    paths = []
    for _ in range(n_paths):
        X = X0
        path = [X0]
        for _ in range(T):
            dW = rng.gauss(0, 1) * math.sqrt(dt)
            X = X + theta * (mu - X) * dt + sigma * dW
            path.append(X)
        paths.append(path)
    return paths


def _trend_path(X0: float, trend_pct_month: float, T: int) -> list[float]:
    """Simple linear trend projection."""
    return [X0 * (1 + trend_pct_month) ** t for t in range(T + 1)]


def _percentile(data: list[float], pct: float) -> float:
    sorted_d = sorted(data)
    idx = (len(sorted_d) - 1) * pct / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_d) - 1)
    return sorted_d[lo] + (sorted_d[hi] - sorted_d[lo]) * (idx - lo)


# ─────────────────────────────────────────────────────────────────────────────
# Signal Forecaster
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalSpec:
    signal_id:    str
    name:         str
    current:      float
    mu:           float = 0.01    # annual drift (GBM) or long-run mean (OU)
    sigma:        float = 0.10    # annual volatility
    theta:        float = 2.0     # mean reversion speed (OU, annualized)
    trend_monthly: float = 0.001  # trend per month
    is_mean_reverting: bool = False


class SignalForecaster:
    def __init__(self, method: ForecastMethod = ForecastMethod.ENSEMBLE,
                 n_paths: int = 200, seed: int = 42) -> None:
        self.method = method
        self.n_paths = n_paths
        self.rng = random.Random(seed)

    def forecast(self, spec: SignalSpec, horizon: ForecastHorizon) -> SignalForecast:
        T = _HORIZON_MONTHS[horizon]
        current = spec.current

        if self.method == ForecastMethod.GBM or not spec.is_mean_reverting:
            paths = _gbm_path(current, spec.mu, spec.sigma, T, self.n_paths, self.rng)
        elif self.method == ForecastMethod.MEAN_REVERT:
            paths = _ou_path(current, spec.theta, spec.mu, spec.sigma, T, self.n_paths, self.rng)
        elif self.method == ForecastMethod.TREND_PROJ:
            tp = _trend_path(current, spec.trend_monthly, T)
            paths = [tp] * self.n_paths
        else:  # ENSEMBLE
            gbm_paths = _gbm_path(current, spec.mu / 12, spec.sigma, T, self.n_paths // 2, self.rng)
            ou_paths  = _ou_path(current, spec.theta, spec.mu, spec.sigma, T, self.n_paths // 2, self.rng)
            paths = gbm_paths + ou_paths

        terminal = [p[-1] for p in paths]
        forecast_val = _percentile(terminal, 50)
        ci_lo = _percentile(terminal, 5)
        ci_hi = _percentile(terminal, 95)
        change_pct = (forecast_val - current) / current * 100 if current else 0

        # Monthly median path
        monthly = []
        for m in range(T + 1):
            vals_at_m = [p[m] for p in paths]
            monthly.append(_percentile(vals_at_m, 50))

        # Confidence decreases with horizon
        conf = max(0.3, 0.9 - T * 0.05)

        return SignalForecast(
            signal_id=spec.signal_id,
            signal_name=spec.name,
            horizon=horizon,
            method=self.method,
            current=current,
            forecast=round(forecast_val, 4),
            change_pct=round(change_pct, 2),
            ci_lower=round(ci_lo, 4),
            ci_upper=round(ci_hi, 4),
            confidence=round(conf, 3),
            path=monthly,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Signal spec library (pre-calibrated)
# ─────────────────────────────────────────────────────────────────────────────

def _build_signal_specs(world: WorldState) -> list[SignalSpec]:
    w = world
    inf = w.inflation
    fx  = w.fx
    en  = w.energy
    cm  = w.commodities
    lg  = w.logistics
    geo = w.geopolitics

    # Drift adjustments based on current trends
    geo_shock = geo.supply_chain_stress * 0.05

    return [
        # Inflation
        SignalSpec("cpi_eu",  "EU CPI YoY",          inf.cpi_yoy,     mu=inf.cpi_yoy/100, sigma=0.20, theta=0.5, is_mean_reverting=True),
        SignalSpec("ppi_eu",  "EU PPI YoY",           inf.ppi_yoy,     mu=inf.ppi_yoy/100, sigma=0.30, theta=0.4, is_mean_reverting=True),
        # FX
        SignalSpec("fx_eurusd","EUR/USD",              fx.eur_usd,      mu=0.0, sigma=0.08,  theta=1.0, is_mean_reverting=True),
        SignalSpec("fx_eurcny","EUR/CNY",              fx.eur_cny,      mu=0.0, sigma=0.06,  theta=1.0, is_mean_reverting=True),
        SignalSpec("fx_eurpln","EUR/PLN",              fx.eur_pln,      mu=0.0, sigma=0.07,  theta=1.2, is_mean_reverting=True),
        # Energy
        SignalSpec("oil",     "Brent Oil USD/bbl",    en.oil_usd_bbl,  mu=0.03, sigma=0.25 + geo_shock, theta=0.8, is_mean_reverting=True),
        SignalSpec("gas_eu",  "EU Gas TTF EUR/MWh",   en.natural_gas_eur_mwh, mu=0.0, sigma=0.40 + geo_shock*2, theta=0.6, is_mean_reverting=True),
        SignalSpec("elec_eu", "EU Electricity EUR/MWh",en.electricity_eur_mwh, mu=0.02, sigma=0.30, theta=0.5, is_mean_reverting=True),
        # Commodities
        SignalSpec("steel",   "Steel HRC EUR/t",      cm.steel_hrc_eur_t, mu=0.02, sigma=0.20, theta=0.7, is_mean_reverting=True),
        SignalSpec("alum",    "Aluminium EUR/t",       cm.aluminum_eur_t,  mu=0.03, sigma=0.18, theta=0.6, is_mean_reverting=True),
        SignalSpec("copper",  "Copper EUR/t",          cm.copper_eur_t,    mu=0.04, sigma=0.22, theta=0.5, is_mean_reverting=True),
        SignalSpec("plastic", "Plastics EUR/t",        cm.plastic_pe_eur_t,mu=0.02, sigma=0.15, theta=0.8, is_mean_reverting=True),
        # Logistics
        SignalSpec("freight", "Container Freight USD/FEU", lg.shanghai_rotterdam_usd, mu=0.0, sigma=0.35 + geo_shock, theta=1.0, is_mean_reverting=True),
        SignalSpec("road_eu", "EU Road Freight EUR/t",lg.germany_poland_eur_t, mu=0.03, sigma=0.10, theta=1.0, is_mean_reverting=True),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Cost impact calculator
# ─────────────────────────────────────────────────────────────────────────────

# Cost elasticities: % production cost change per 1% signal change
# Based on typical EU manufacturing (auto-like sector)
_ELASTICITIES = {
    "steel":    lambda mp, ss: mp * ss,                   # material_pct × steel_share
    "alum":     lambda mp, ss: mp * ss,
    "copper":   lambda mp, ss: mp * ss,
    "plastic":  lambda mp, ss: mp * ss,
    "elec_eu":  lambda ep, _:  ep,                        # energy_pct
    "gas_eu":   lambda ep, _:  ep * 0.3,
    "oil":      lambda ep, _:  ep * 0.15,
    "freight":  lambda lp, _:  lp,                        # logistics_pct
    "road_eu":  lambda lp, _:  lp * 0.4,
    "cpi_eu":   lambda op, _:  op * 0.7,                  # overhead_pct (inflation)
    "ppi_eu":   lambda mp, _:  mp * 0.3,                  # indirect material effect
    "fx_eurusd": lambda up, _: up,                         # usd_exposure
    "fx_eurcny": lambda cp, _: cp,                         # cny_exposure
}


def compute_cost_impacts(
    forecasts: list[SignalForecast],
    material_pct: float, labor_pct: float, energy_pct: float,
    logistics_pct: float, overhead_pct: float,
    steel_share: float, alum_share: float, copper_share: float, plastic_share: float,
    usd_exposure: float, cny_exposure: float,
    base_cost_eur: float,
) -> list[CostImpact]:
    impacts = []
    for f in forecasts:
        sid = f.signal_id
        chg = f.change_pct / 100  # as fraction

        # Determine elasticity
        if sid == "steel":
            elast = material_pct * steel_share
        elif sid == "alum":
            elast = material_pct * alum_share
        elif sid == "copper":
            elast = material_pct * copper_share
        elif sid == "plastic":
            elast = material_pct * plastic_share
        elif sid == "elec_eu":
            elast = energy_pct
        elif sid == "gas_eu":
            elast = energy_pct * 0.3
        elif sid == "oil":
            elast = energy_pct * 0.15
        elif sid == "freight":
            elast = logistics_pct
        elif sid == "road_eu":
            elast = logistics_pct * 0.4
        elif sid == "cpi_eu":
            elast = overhead_pct * 0.7 + labor_pct * 0.3
        elif sid == "ppi_eu":
            elast = material_pct * 0.3
        elif sid == "fx_eurusd":
            elast = usd_exposure * (material_pct + energy_pct)
        elif sid == "fx_eurcny":
            elast = cny_exposure * material_pct
        else:
            elast = 0.02

        cost_delta_pct = chg * elast * 100
        cost_delta_eur = base_cost_eur * cost_delta_pct / 100

        if abs(cost_delta_pct) < 0.01:
            continue

        impacts.append(CostImpact(
            signal_name=f.signal_name,
            current_value=f.current,
            forecast_value=f.forecast,
            cost_delta_pct=round(cost_delta_pct, 4),
            cost_delta_eur=round(cost_delta_eur, 6),
            elasticity=round(elast, 4),
            confidence=f.confidence,
        ))

    return sorted(impacts, key=lambda i: abs(i.cost_delta_pct), reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Production Cost Forecaster (orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class ProductionCostForecaster:
    def __init__(
        self,
        method: ForecastMethod = ForecastMethod.ENSEMBLE,
        n_paths: int = 200,
        seed: int = 42,
    ) -> None:
        self.method   = method
        self.n_paths  = n_paths
        self.forecaster = SignalForecaster(method, n_paths, seed)

    def forecast(
        self,
        product_id: str,
        world: WorldState,
        base_cost_eur: float,
        country: str = "DE",
        material_pct: float = 0.40,
        labor_pct:    float = 0.25,
        energy_pct:   float = 0.15,
        logistics_pct: float = 0.10,
        overhead_pct: float = 0.10,
        steel_share:  float = 0.50,
        alum_share:   float = 0.20,
        copper_share: float = 0.10,
        plastic_share: float = 0.20,
        usd_exposure: float = 0.30,
        cny_exposure: float = 0.20,
        scenario:     ScenarioType = ScenarioType.BASELINE,
    ) -> ProductionCostForecast:
        import time
        t0 = time.time()

        specs = _build_signal_specs(world)
        horizons: dict[str, HorizonForecast] = {}

        for h in ForecastHorizon:
            sig_forecasts = [self.forecaster.forecast(s, h) for s in specs]
            impacts = compute_cost_impacts(
                sig_forecasts, material_pct, labor_pct, energy_pct,
                logistics_pct, overhead_pct,
                steel_share, alum_share, copper_share, plastic_share,
                usd_exposure, cny_exposure, base_cost_eur,
            )
            total_pct = sum(i.cost_delta_pct for i in impacts)
            total_eur = base_cost_eur * total_pct / 100
            dominant = impacts[0].signal_name if impacts else "no signal"
            avg_conf = sum(f.confidence for f in sig_forecasts) / max(len(sig_forecasts), 1)

            horizons[h.value] = HorizonForecast(
                horizon=h,
                months_ahead=_HORIZON_MONTHS[h],
                world_state=world,
                signal_forecasts=sig_forecasts,
                cost_impacts=impacts,
                total_cost_delta_pct=round(total_pct, 3),
                total_cost_delta_eur=round(total_eur, 4),
                confidence=round(avg_conf, 3),
                dominant_driver=dominant,
            )

        return ProductionCostForecast(
            product_id=product_id,
            base_cost_eur=base_cost_eur,
            country=country,
            horizons=horizons,
            scenario=scenario,
            run_time_ms=round((time.time() - t0) * 1000, 2),
        )
