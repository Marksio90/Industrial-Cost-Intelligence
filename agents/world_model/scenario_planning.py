"""Section 5: Scenario Planning Engine."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    ScenarioType, ForecastHorizon, WorldState, ProductionCostForecast,
    HorizonForecast, SimulationResult,
)
from .forecasting import ProductionCostForecaster
from .simulation import MonteCarloEngine


@dataclass
class ScenarioRiskFlag:
    horizon: str
    severity: str  # LOW / MEDIUM / HIGH / CRITICAL
    message: str
    cost_delta_pct: float


@dataclass
class ScenarioRecommendation:
    action: str
    rationale: str
    priority: str  # IMMEDIATE / SHORT_TERM / LONG_TERM


@dataclass
class ScenarioResult:
    scenario: ScenarioType
    forecast: ProductionCostForecast
    simulation: SimulationResult
    cost_at_m1: float
    cost_at_m3: float
    cost_at_m6: float
    cost_at_m12: float
    delta_pct_m12: float
    var_95_12m: float
    risk_flags: list[ScenarioRiskFlag] = field(default_factory=list)
    recommendations: list[ScenarioRecommendation] = field(default_factory=list)


@dataclass
class ScenarioComparison:
    product_id: str
    base_cost_eur: float
    scenarios: list[ScenarioResult]
    worst_case_m12: float
    best_case_m12: float
    baseline_m12: float
    spread_pct: float  # (worst - best) / base * 100
    run_time_ms: float
    top_risks: list[ScenarioRiskFlag]
    top_recommendations: list[ScenarioRecommendation]


_SEVERITY_THRESHOLDS = {
    "CRITICAL": 20.0,
    "HIGH": 10.0,
    "MEDIUM": 5.0,
    "LOW": 2.0,
}

_SCENARIO_NARRATIVES = {
    ScenarioType.BASELINE: "Kontynuacja obecnych trendów bez istotnych zakłóceń.",
    ScenarioType.OPTIMISTIC: "Poprawa warunków: tańsza energia, silniejsze EUR, niższe frety.",
    ScenarioType.PESSIMISTIC: "Pogorszenie warunków: wyższa inflacja, droższe surowce.",
    ScenarioType.SHOCK_WAR: "Eskalacja konfliktu geopolitycznego: gwałtowny wzrost cen energii i fretów.",
    ScenarioType.SHOCK_ENERGY: "Kryzys energetyczny: ceny gazu ×2.5, elektryczność ×2.0.",
    ScenarioType.SHOCK_RECESSION: "Recesja gospodarcza: spadek popytu, niższe ceny surowców.",
    ScenarioType.STAGFLATION: "Stagflacja: wysoka inflacja przy słabym wzroście.",
}


def _severity(delta_pct: float) -> str:
    abs_d = abs(delta_pct)
    for sev, thr in _SEVERITY_THRESHOLDS.items():
        if abs_d >= thr:
            return sev
    return "LOW"


def _risk_flags(scenario: ScenarioType, result: ScenarioResult, base: float) -> list[ScenarioRiskFlag]:
    flags: list[ScenarioRiskFlag] = []
    for horizon, cost in [("1m", result.cost_at_m1), ("3m", result.cost_at_m3),
                          ("6m", result.cost_at_m6), ("12m", result.cost_at_m12)]:
        delta_pct = (cost - base) / base * 100 if base else 0.0
        if abs(delta_pct) >= 2.0:
            sev = _severity(delta_pct)
            direction = "wzrost" if delta_pct > 0 else "spadek"
            flags.append(ScenarioRiskFlag(
                horizon=horizon,
                severity=sev,
                message=f"{_SCENARIO_NARRATIVES[scenario]} Przewidywany {direction} kosztów o {abs(delta_pct):.1f}% za {horizon}.",
                cost_delta_pct=delta_pct,
            ))
    if result.var_95_12m and base:
        var_pct = (result.var_95_12m - base) / base * 100
        if var_pct >= 5.0:
            flags.append(ScenarioRiskFlag(
                horizon="12m",
                severity=_severity(var_pct),
                message=f"VaR 95% za 12 miesięcy: {result.var_95_12m:.2f} EUR ({var_pct:+.1f}% vs baza).",
                cost_delta_pct=var_pct,
            ))
    return flags


def _recommendations(scenario: ScenarioType, flags: list[ScenarioRiskFlag]) -> list[ScenarioRecommendation]:
    recs: list[ScenarioRecommendation] = []
    critical = [f for f in flags if f.severity == "CRITICAL"]
    high = [f for f in flags if f.severity == "HIGH"]

    if scenario in (ScenarioType.SHOCK_ENERGY, ScenarioType.STAGFLATION) and (critical or high):
        recs.append(ScenarioRecommendation(
            action="Zabezpiecz ceny energii kontraktami forward na 6-12 miesięcy",
            rationale="Scenariusz energetyczny oznacza wzrost kosztów >20%. Hedging ograniczy ekspozycję.",
            priority="IMMEDIATE",
        ))
    if scenario == ScenarioType.SHOCK_WAR and (critical or high):
        recs.append(ScenarioRecommendation(
            action="Zwiększ zapasy surowców do 3-miesięcznego poziomu",
            rationale="Zakłócenia łańcucha dostaw w scenariuszu wojennym mogą trwać 6-12 miesięcy.",
            priority="IMMEDIATE",
        ))
        recs.append(ScenarioRecommendation(
            action="Zdywersyfikuj dostawców — dodaj dostawcę spoza strefy konfliktu",
            rationale="Pojedyncze źródło dostaw w scenariuszu geopolitycznym to ryzyko krytyczne.",
            priority="SHORT_TERM",
        ))
    if scenario == ScenarioType.PESSIMISTIC and high:
        recs.append(ScenarioRecommendation(
            action="Negocjuj długoterminowe umowy na surowce po obecnych cenach",
            rationale="Przy pogorszeniu warunków blokada cen bieżących to oszczędność 5-10%.",
            priority="SHORT_TERM",
        ))
    if scenario == ScenarioType.SHOCK_RECESSION:
        recs.append(ScenarioRecommendation(
            action="Wykorzystaj recesję do renegocjacji kontraktów dostawczych",
            rationale="Ceny surowców spadają — czas na korzystne warunki długoterminowe.",
            priority="SHORT_TERM",
        ))
    if scenario == ScenarioType.OPTIMISTIC:
        recs.append(ScenarioRecommendation(
            action="Monitoruj rynek i nie blokuj kosztów energii przy obecnym optimum",
            rationale="Scenariusz optymistyczny: ceny energii mogą dalej spadać.",
            priority="LONG_TERM",
        ))
    return recs


def _extract_horizon_cost(forecast: ProductionCostForecast, horizon_key: str, base_cost: float) -> float:
    h = forecast.horizons.get(horizon_key)
    if h is None:
        return base_cost
    return base_cost * (1.0 + h.total_cost_delta_pct / 100.0)


class ScenarioPlanningEngine:
    def __init__(
        self,
        n_mc_paths: int = 200,
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
        self._forecaster = ProductionCostForecaster(n_paths=n_mc_paths)
        self._mc = MonteCarloEngine(n_paths=n_mc_paths)
        self._cost_weights = {
            "material_pct": material_pct, "labor_pct": labor_pct,
            "energy_pct": energy_pct, "logistics_pct": logistics_pct,
            "overhead_pct": overhead_pct, "steel_share": steel_share,
            "alum_share": alum_share, "copper_share": copper_share,
            "plastic_share": plastic_share, "usd_exposure": usd_exposure,
            "cny_exposure": cny_exposure,
        }

    def run_scenario(
        self,
        scenario: ScenarioType,
        product_id: str,
        base_cost_eur: float,
        world: WorldState,
        country: str = "DE",
    ) -> ScenarioResult:
        forecast = self._forecaster.forecast(
            product_id=product_id,
            world=world,
            base_cost_eur=base_cost_eur,
            country=country,
            **self._cost_weights,
        )
        sim = self._mc.simulate(
            world=world,
            base_cost_eur=base_cost_eur,
            scenario=scenario,
            cost_weights=self._cost_weights,
        )

        c1 = _extract_horizon_cost(forecast, "1m", base_cost_eur)
        c3 = _extract_horizon_cost(forecast, "3m", base_cost_eur)
        c6 = _extract_horizon_cost(forecast, "6m", base_cost_eur)
        c12 = _extract_horizon_cost(forecast, "12m", base_cost_eur)
        delta12 = (c12 - base_cost_eur) / base_cost_eur * 100 if base_cost_eur else 0.0

        result = ScenarioResult(
            scenario=scenario,
            forecast=forecast,
            simulation=sim,
            cost_at_m1=c1,
            cost_at_m3=c3,
            cost_at_m6=c6,
            cost_at_m12=c12,
            delta_pct_m12=delta12,
            var_95_12m=sim.var_95_12m,
        )
        result.risk_flags = _risk_flags(scenario, result, base_cost_eur)
        result.recommendations = _recommendations(scenario, result.risk_flags)
        return result

    def run_all(
        self,
        product_id: str,
        base_cost_eur: float,
        world: WorldState,
        country: str = "DE",
        scenarios: Optional[list[ScenarioType]] = None,
    ) -> ScenarioComparison:
        t0 = time.perf_counter()
        if scenarios is None:
            scenarios = list(ScenarioType)

        results: list[ScenarioResult] = []
        for sc in scenarios:
            results.append(self.run_scenario(sc, product_id, base_cost_eur, world, country))

        costs_m12 = [r.cost_at_m12 for r in results]
        worst = max(costs_m12)
        best = min(costs_m12)
        baseline_m12 = next(
            (r.cost_at_m12 for r in results if r.scenario == ScenarioType.BASELINE),
            base_cost_eur,
        )
        spread = (worst - best) / base_cost_eur * 100 if base_cost_eur else 0.0

        all_flags = [f for r in results for f in r.risk_flags]
        all_flags.sort(key=lambda f: (
            ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(f.severity),
            -abs(f.cost_delta_pct),
        ))
        all_recs = [rec for r in results for rec in r.recommendations]
        seen: set[str] = set()
        deduped_recs: list[ScenarioRecommendation] = []
        for rec in all_recs:
            if rec.action not in seen:
                seen.add(rec.action)
                deduped_recs.append(rec)

        elapsed = (time.perf_counter() - t0) * 1000
        return ScenarioComparison(
            product_id=product_id,
            base_cost_eur=base_cost_eur,
            scenarios=results,
            worst_case_m12=worst,
            best_case_m12=best,
            baseline_m12=baseline_m12,
            spread_pct=spread,
            run_time_ms=elapsed,
            top_risks=all_flags[:10],
            top_recommendations=deduped_recs[:8],
        )
