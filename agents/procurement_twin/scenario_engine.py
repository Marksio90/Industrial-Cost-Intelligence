"""
Section 2 — Scenario Engine

Zarządzanie scenariuszami "co jeśli":
  - Biblioteka gotowych scenariuszy (supplier loss, price spike, FX, MOQ)
  - Tworzenie scenariuszy złożonych (compound shocks)
  - Uruchamianie scenariusza na kopii baseline state
  - Porównanie: baseline vs scenario
  - Stress test portfolio (worst-case combination)
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ScenarioDefinition, ScenarioShock, ScenarioType,
    SimulationResult, SimulationStatus, DailyKPI,
    ImpactLevel, RiskEvent, SCENARIO_TEMPLATES,
)
from .simulation_engine import SimulationEngine, SimulationState

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ScenarioRunner — runs one scenario against a baseline
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioRunner:
    """
    Runs a ScenarioDefinition against a baseline SimulationState.

    Returns SimulationResult with cost delta, service level delta,
    lead-time changes, and recommendations.
    """

    def __init__(self, baseline: SimulationState) -> None:
        self._baseline = baseline

    def run(
        self,
        scenario:      ScenarioDefinition,
        baseline_result: SimulationResult | None = None,
    ) -> SimulationResult:
        result = SimulationResult(
            scenario_id=scenario.scenario_id,
            tenant_id=scenario.tenant_id,
            status=SimulationStatus.RUNNING,
            horizon_days=scenario.horizon_days,
        )
        t0 = time.monotonic()

        try:
            # ── Baseline (no shocks) ──────────────────────────────────────────
            if baseline_result is None:
                baseline_engine = SimulationEngine(self._baseline, seed=scenario.seed)
                baseline_kpis   = baseline_engine.run(scenario.horizon_days)
                baseline_cost   = sum(
                    o.total_cost for o in baseline_engine.state.orders if o.status == "delivered"
                )
            else:
                baseline_cost = baseline_result.baseline_total_cost
                baseline_kpis = baseline_result.daily_kpis

            # ── Scenario run (with shocks) ────────────────────────────────────
            scenario_state  = copy.deepcopy(self._baseline)
            scenario_engine = SimulationEngine(scenario_state, seed=scenario.seed)

            # Group shocks by day_offset
            shock_map: dict[int, list[ScenarioShock]] = {}
            for shock in scenario.shocks:
                shock_map.setdefault(shock.day_offset, []).append(shock)

            scenario_kpis: list[DailyKPI] = []
            for day in range(1, scenario.horizon_days + 1):
                # Apply shocks scheduled for today
                for shock in shock_map.get(day - 1, []):
                    scenario_engine.apply_shock(
                        target_type=shock.target_type,
                        target_id=shock.target_id,
                        parameter=shock.parameter,
                        value=shock.value,
                        ramp_days=shock.ramp_days,
                    )
                kpi = scenario_engine.step()
                scenario_kpis.append(kpi)

            scenario_cost = sum(
                o.total_cost for o in scenario_engine.state.orders if o.status == "delivered"
            )

            # ── Compute deltas ────────────────────────────────────────────────
            cost_delta     = scenario_cost - baseline_cost
            cost_delta_pct = (cost_delta / max(baseline_cost, 1)) * 100.0

            lead_times  = [k.avg_lead_time_days for k in scenario_kpis if k.avg_lead_time_days > 0]
            service_lvs = [k.service_level for k in scenario_kpis]
            stockouts   = sum(1 for k in scenario_kpis if k.stockout_materials > 0)

            result.baseline_total_cost = baseline_cost
            result.scenario_total_cost = scenario_cost
            result.cost_delta_eur      = round(cost_delta, 2)
            result.cost_delta_pct      = round(cost_delta_pct, 2)
            result.max_lead_time_days  = round(max(lead_times), 1) if lead_times else 0.0
            result.min_service_level   = round(min(service_lvs), 4) if service_lvs else 0.0
            result.stockout_days       = stockouts
            result.supplier_switches   = scenario_engine._supplier_switches
            result.emergency_orders    = scenario_engine._emergency_orders
            result.daily_kpis          = scenario_kpis
            result.events_triggered    = [
                {
                    "event_type":  e.event_type.value,
                    "source":      e.source_name,
                    "day":         e.sim_day,
                    "impact":      e.impact_level.value,
                }
                for e in scenario_engine.risk_events
            ]

            # Impact classification
            result.impact_level   = _classify_impact(cost_delta_pct, result.min_service_level, stockouts)
            result.recommendations = _generate_recommendations(scenario, result, scenario_engine)
            result.status          = SimulationStatus.COMPLETED

        except Exception as exc:
            result.status = SimulationStatus.FAILED
            result.error  = str(exc)
            log.exception("Scenario run failed: %s", exc)

        result.completed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        log.info(
            "Scenario %s completed in %.1fs — Δcost=%.1f%% service_min=%.2f",
            scenario.name, time.monotonic() - t0, result.cost_delta_pct, result.min_service_level,
        )
        return result


def _classify_impact(
    cost_delta_pct: float,
    min_service:    float,
    stockout_days:  int,
) -> ImpactLevel:
    if cost_delta_pct > 25 or min_service < 0.70 or stockout_days > 30:
        return ImpactLevel.CRITICAL
    if cost_delta_pct > 10 or min_service < 0.85 or stockout_days > 10:
        return ImpactLevel.HIGH
    if cost_delta_pct > 3 or min_service < 0.95 or stockout_days > 3:
        return ImpactLevel.MEDIUM
    return ImpactLevel.LOW


def _generate_recommendations(
    scenario:  ScenarioDefinition,
    result:    SimulationResult,
    engine:    SimulationEngine,
) -> list[str]:
    recs: list[str] = []

    if result.cost_delta_pct > 10:
        recs.append(f"Koszt wzrósł o {result.cost_delta_pct:.1f}% — rozważ hedging cenowy lub kontrakty długoterminowe.")

    if result.min_service_level < 0.90:
        recs.append(f"Poziom obsługi spadł do {result.min_service_level:.0%} — zwiększ safety stock dla materiałów krytycznych.")

    if result.stockout_days > 5:
        recs.append(f"{result.stockout_days} dni bez zapasów — dodaj alternatywnych dostawców i bufory strategiczne.")

    if result.emergency_orders > 10:
        recs.append(f"{result.emergency_orders} zamówień awaryjnych (premia +20%) — wdroż VMI lub kanban z dostawcą.")

    if result.supplier_switches > 0:
        recs.append(f"Wykonano {result.supplier_switches} przełączeń dostawcy — zakwalifikuj dostawców tier-2 z wyprzedzeniem.")

    # Scenario-specific
    for shock in scenario.shocks:
        if shock.parameter == "status" and shock.value == "bankrupt":
            recs.append("Zbankrutowanie dostawcy: zbuduj bazę 2–3 kwalifikowanych alternatyw i utrzymuj dual-sourcing.")
        if shock.parameter == "price_delta_pct" and float(shock.value) > 10:
            recs.append(f"Spike cenowy +{shock.value}%: rozważ forward contracts lub indeksację cenową w umowach.")
        if shock.parameter == "rate_delta_pct":
            recs.append("Ryzyko FX: wdroż natural hedge (fakturowanie w EUR) lub zakup opcji walutowych.")
        if shock.parameter == "moq_multiplier":
            recs.append("Wzrost MOQ: negocjuj podział partii z brokerem lub stwórz konsorcjum zakupowe z innymi firmami.")

    if not recs:
        recs.append("Scenariusz w granicach tolerancji — kontynuuj monitoring.")

    return recs


# ─────────────────────────────────────────────────────────────────────────────
# ScenarioLibrary — prebuilt + custom scenarios
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioLibrary:
    """
    In-memory scenario registry.

    Pre-populated with canonical ICI scenarios.
    Scenarios are tenant-scoped.
    """

    def __init__(self) -> None:
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._results:   dict[str, SimulationResult]   = {}

    def create(self, definition: ScenarioDefinition) -> ScenarioDefinition:
        self._scenarios[definition.scenario_id] = definition
        return definition

    def get(self, scenario_id: str) -> ScenarioDefinition | None:
        return self._scenarios.get(scenario_id)

    def list_all(self, tenant_id: str | None = None) -> list[ScenarioDefinition]:
        scenarios = list(self._scenarios.values())
        if tenant_id:
            scenarios = [s for s in scenarios if s.tenant_id == tenant_id]
        return sorted(scenarios, key=lambda s: s.created_at, reverse=True)

    def delete(self, scenario_id: str) -> bool:
        if scenario_id in self._scenarios:
            del self._scenarios[scenario_id]
            self._results.pop(scenario_id, None)
            return True
        return False

    def store_result(self, result: SimulationResult) -> None:
        self._results[result.scenario_id] = result

    def get_result(self, scenario_id: str) -> SimulationResult | None:
        return self._results.get(scenario_id)

    def create_from_template(
        self,
        template_key:  str,
        target_id:     str,
        tenant_id:     str = "tenant-demo",
        horizon_days:  int = 365,
        **override_kwargs: Any,
    ) -> ScenarioDefinition | None:
        tmpl = SCENARIO_TEMPLATES.get(template_key)
        if not tmpl:
            return None

        shocks = [
            ScenarioShock(
                target_type=s.get("target_type", "supplier"),
                target_id=s.get("target_id") or target_id,
                parameter=s["parameter"],
                value=s["value"],
                day_offset=s.get("day_offset", 0),
                duration_days=s.get("duration_days"),
                ramp_days=s.get("ramp_days", 0),
            )
            for s in tmpl["shocks"]
        ]
        defn = ScenarioDefinition(
            name=tmpl["name"],
            description=tmpl.get("description", ""),
            scenario_type=ScenarioType(template_key.split("_")[0] if "_" in template_key else "custom"),
            shocks=shocks,
            horizon_days=horizon_days,
            tenant_id=tenant_id,
        )
        return self.create(defn)


# ─────────────────────────────────────────────────────────────────────────────
# CompoundScenarioBuilder — chaining multiple shocks
# ─────────────────────────────────────────────────────────────────────────────

class CompoundScenarioBuilder:
    """Fluent builder for complex multi-shock scenarios."""

    def __init__(self, name: str, tenant_id: str = "tenant-demo") -> None:
        self._defn = ScenarioDefinition(
            name=name,
            scenario_type=ScenarioType.COMPOUND,
            tenant_id=tenant_id,
        )

    def add_shock(
        self,
        target_type:   str,
        target_id:     str,
        parameter:     str,
        value:         Any,
        day_offset:    int = 0,
        duration_days: int | None = None,
        ramp_days:     int = 0,
    ) -> "CompoundScenarioBuilder":
        self._defn.shocks.append(ScenarioShock(
            target_type=target_type,
            target_id=target_id,
            parameter=parameter,
            value=value,
            day_offset=day_offset,
            duration_days=duration_days,
            ramp_days=ramp_days,
        ))
        return self

    def supplier_bankrupt(self, supplier_id: str, day: int = 0) -> "CompoundScenarioBuilder":
        return self.add_shock("supplier", supplier_id, "status", "bankrupt", day)

    def price_spike(self, material_id: str, pct: float, day: int = 0) -> "CompoundScenarioBuilder":
        return self.add_shock("material", material_id, "price_delta_pct", pct, day)

    def fx_shock(self, pair: str, pct: float, day: int = 0) -> "CompoundScenarioBuilder":
        return self.add_shock("fx", pair, "rate_delta_pct", pct, day)

    def moq_increase(self, supplier_id: str, multiplier: float = 2.0, day: int = 0) -> "CompoundScenarioBuilder":
        return self.add_shock("supplier", supplier_id, "moq_multiplier", multiplier, day)

    def lead_time_increase(self, supplier_id: str, extra_days: float, day: int = 0) -> "CompoundScenarioBuilder":
        return self.add_shock("supplier", supplier_id, "lead_time_delta", extra_days, day)

    def with_horizon(self, days: int) -> "CompoundScenarioBuilder":
        self._defn.horizon_days = days
        return self

    def build(self) -> ScenarioDefinition:
        return self._defn


# ─────────────────────────────────────────────────────────────────────────────
# Stress test — worst-case portfolio analysis
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StressTestResult:
    scenarios_run:      int
    worst_case_cost_delta_pct: float
    worst_scenario_name: str
    total_at_risk_eur:  float
    critical_scenarios: list[dict[str, Any]] = field(default_factory=list)
    summary_table:      list[dict[str, Any]] = field(default_factory=list)


def run_stress_test(
    baseline:      SimulationState,
    scenarios:     list[ScenarioDefinition],
    parallel:      bool = False,
) -> StressTestResult:
    """Run all scenarios and aggregate worst-case portfolio risk."""
    runner  = ScenarioRunner(baseline)
    results = []
    for scenario in scenarios:
        res = runner.run(scenario)
        results.append((scenario, res))

    summary: list[dict[str, Any]] = []
    for scen, res in results:
        summary.append({
            "scenario":         scen.name,
            "cost_delta_pct":   res.cost_delta_pct,
            "min_service":      res.min_service_level,
            "stockout_days":    res.stockout_days,
            "impact":           res.impact_level.value,
        })

    worst = max(results, key=lambda x: x[1].cost_delta_pct, default=None)
    at_risk = sum(r.cost_delta_eur for _, r in results if r.cost_delta_eur > 0)
    critical = [s for s in summary if s["impact"] in ("critical", "high")]

    return StressTestResult(
        scenarios_run=len(results),
        worst_case_cost_delta_pct=worst[1].cost_delta_pct if worst else 0.0,
        worst_scenario_name=worst[0].name if worst else "",
        total_at_risk_eur=round(at_risk, 2),
        critical_scenarios=critical,
        summary_table=summary,
    )
