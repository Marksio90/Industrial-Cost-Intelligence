"""
Section 6 — Dashboard

Panel danych cyfrowego bliźniaka:
  - Overview header: KPI tiles (koszt, lead time, service level, ryzyko)
  - Scenario comparison: baseline vs scenario time series
  - Monte Carlo fan chart (P5/P25/P50/P75/P95)
  - Risk heatmap: dostawcy × materiały
  - Resilience spider chart
  - Event timeline
  - Supplier scorecard table
  - Cost waterfall: cena stali, FX, MOQ, logistyka
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import (
    SimulationResult, MonteCarloResult, DailyKPI,
    SupplierProfile, SupplierStatus, MaterialSpec,
    ImpactLevel,
)
from .risk_propagation import ResilienceReport, RiskPropagationEngine


# ─────────────────────────────────────────────────────────────────────────────
# Data builders
# ─────────────────────────────────────────────────────────────────────────────

def build_kpi_tiles(result: SimulationResult) -> list[dict[str, Any]]:
    """4 KPI tiles for the overview header."""
    kpis = result.daily_kpis
    if not kpis:
        return []

    last   = kpis[-1]
    costs  = [k.total_spend_eur for k in kpis]
    svcs   = [k.service_level   for k in kpis]
    lts    = [k.avg_lead_time_days for k in kpis if k.avg_lead_time_days > 0]

    delta_color = (
        "red"    if result.cost_delta_pct > 10 else
        "yellow" if result.cost_delta_pct > 3  else
        "green"
    )

    return [
        {
            "id":    "total_cost",
            "title": "Koszt łączny",
            "value": f"{result.scenario_total_cost:,.0f} EUR",
            "delta": f"{result.cost_delta_pct:+.1f}% vs baseline",
            "color": delta_color,
            "trend": _sparkline(costs[-30:]),
        },
        {
            "id":    "service_level",
            "title": "Poziom obsługi",
            "value": f"{result.min_service_level:.1%}",
            "delta": "min" if result.min_service_level < 0.95 else "OK",
            "color": "red" if result.min_service_level < 0.85 else "green",
            "trend": _sparkline(svcs[-30:]),
        },
        {
            "id":    "lead_time",
            "title": "Lead Time (maks)",
            "value": f"{result.max_lead_time_days:.0f} dni",
            "delta": f"avg {sum(lts)/len(lts):.0f}d" if lts else "N/A",
            "color": "red" if result.max_lead_time_days > 60 else "yellow" if result.max_lead_time_days > 30 else "green",
            "trend": _sparkline(lts[-30:]),
        },
        {
            "id":    "impact",
            "title": "Poziom ryzyka",
            "value": result.impact_level.value.upper(),
            "delta": f"{len(result.events_triggered)} zdarzeń",
            "color": {"critical": "red", "high": "orange", "medium": "yellow", "low": "green"}.get(result.impact_level.value, "gray"),
            "trend": [],
        },
    ]


def build_scenario_comparison(
    baseline: SimulationResult,
    scenario: SimulationResult,
    metric:   str = "total_spend_eur",
) -> dict[str, Any]:
    """
    Time series comparison of baseline vs scenario for given metric.
    Returns chart-ready {labels, baseline, scenario, delta} arrays.
    """
    def _extract(kpis: list[DailyKPI], key: str) -> list[float]:
        return [getattr(k, key, 0.0) for k in kpis]

    bl_vals  = _extract(baseline.daily_kpis, metric)
    sc_vals  = _extract(scenario.daily_kpis, metric)
    labels   = list(range(1, max(len(bl_vals), len(sc_vals)) + 1))
    delta    = [round(s - b, 2) for b, s in zip(bl_vals, sc_vals)]

    return {
        "metric":   metric,
        "labels":   labels,
        "baseline": bl_vals,
        "scenario": sc_vals,
        "delta":    delta,
        "delta_pct": [round((s - b) / max(abs(b), 1) * 100, 2) for b, s in zip(bl_vals, sc_vals)],
    }


def build_mc_fan_chart(mc: MonteCarloResult) -> dict[str, Any]:
    """
    Fan chart data for Monte Carlo cost distribution.
    Bands: P5–P95, P25–P75, P50 (median).
    """
    def _pct(level: float) -> float:
        for p in mc.cost_percentiles:
            if abs(p.level - level) < 0.001:
                return p.value
        return mc.cost_mean

    return {
        "type":      "fan",
        "horizon_days": mc.horizon_days,
        "p5":        _pct(0.05),
        "p25":       _pct(0.25),
        "p50":       _pct(0.50),
        "p75":       _pct(0.75),
        "p95":       _pct(0.95),
        "mean":      mc.cost_mean,
        "var_95":    mc.var_95,
        "cvar_95":   mc.cvar_95,
        "cost_histogram": mc.cost_histogram,
        "lead_histogram": mc.lead_histogram,
    }


def build_mc_stats_panel(mc: MonteCarloResult) -> dict[str, Any]:
    """Summary statistics panel for Monte Carlo results."""
    return {
        "n_runs":          mc.n_runs,
        "cost_mean_eur":   f"{mc.cost_mean:,.0f}",
        "cost_std_eur":    f"{mc.cost_std:,.0f}",
        "cost_cv":         f"{mc.cost_var:.2%}",
        "var_95_eur":      f"{mc.var_95:,.0f}",
        "cvar_95_eur":     f"{mc.cvar_95:,.0f}",
        "service_p5":      f"{mc.service_p5:.1%}",
        "stockout_prob":   f"{mc.stockout_prob:.1%}",
        "lead_p95_days":   f"{mc.lead_p95:.0f}",
    }


def build_tornado_chart(mc: MonteCarloResult) -> list[dict[str, Any]]:
    """Tornado chart from MC sensitivity analysis."""
    return [
        {
            "rank":        i + 1,
            "parameter":   s["parameter"],
            "target":      s["target_id"],
            "swing":       s["swing"],
            "hi_impact":   s["hi_cost_delta"],
            "lo_impact":   s["lo_cost_delta"],
            "bar_right":   max(s["hi_cost_delta"], 0),
            "bar_left":    min(s["lo_cost_delta"], 0),
        }
        for i, s in enumerate(mc.sensitivity[:10])
    ]


def build_risk_heatmap(
    suppliers: dict[str, SupplierProfile],
    materials: dict[str, MaterialSpec],
) -> dict[str, Any]:
    """
    Risk heatmap: rows = materials, columns = suppliers,
    cell color = risk exposure.
    """
    from .risk_propagation import _supplier_risk_score, _material_criticality
    from .models import SimulationState

    state = SimulationState(suppliers=suppliers, materials=materials)
    rows: list[dict[str, Any]] = []

    for mat_id, mat in list(materials.items())[:20]:
        mat_crit = _material_criticality(mat, state)
        cells: list[dict[str, Any]] = []
        for sup_id in mat.supplier_ids:
            sup = suppliers.get(sup_id)
            if not sup:
                continue
            sup_risk = _supplier_risk_score(sup)
            exposure = mat.annual_usage * mat.price_model.base_price / max(len(mat.supplier_ids), 1)
            combined_risk = round((sup_risk * 0.5 + mat_crit * 0.5), 3)
            cells.append({
                "supplier_id":   sup_id,
                "supplier_name": sup.name,
                "risk_score":    combined_risk,
                "exposure_eur":  round(exposure, 0),
                "color":         _risk_color(combined_risk),
            })
        rows.append({
            "material_id":   mat_id,
            "material_name": mat.name,
            "criticality":   round(mat_crit, 3),
            "cells":         cells,
        })

    return {"rows": rows, "legend": {"green": "<0.3", "yellow": "0.3–0.6", "red": ">0.6"}}


def build_resilience_spider(report: ResilienceReport) -> dict[str, Any]:
    """
    Spider/radar chart for supply chain resilience dimensions.
    5 axes: Supplier diversity, Geo diversity, Quality, Financial, Agility
    """
    # Derived from report
    sup_diversity = max(0.0, 1.0 - report.hhi_supplier / 10_000)
    geo_max = max(report.geo_concentration.values(), default=0) / 100
    geo_div  = max(0.0, 1.0 - geo_max)
    fin_hlth = max(0.0, 1.0 - report.high_risk_spend_pct)
    single_r = max(0.0, 1.0 - report.single_source_count / 10)
    return {
        "axes": ["Dywersyfikacja\ndostawców", "Dywersyfikacja\ngeo", "Siła\nfinansowa", "Single-source\nryzyko", "Odporność\nogólna"],
        "values": [
            round(sup_diversity, 3),
            round(geo_div, 3),
            round(fin_hlth, 3),
            round(single_r, 3),
            round(report.overall_score, 3),
        ],
        "max": 1.0,
        "overall_score": report.overall_score,
        "color": _risk_color(1.0 - report.overall_score),
    }


def build_event_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Timeline entries for the event feed."""
    color_map = {
        "supplier_bankrupt":     "red",
        "price_spike":           "orange",
        "lead_time_increase":    "yellow",
        "fx_shock":              "blue",
        "moq_increase":          "purple",
        "logistics_disruption":  "gray",
        "quality_failure":       "pink",
        "demand_surge":          "teal",
    }
    return [
        {
            "day":        e.get("day", 0),
            "event_type": e.get("event_type", ""),
            "source":     e.get("source", ""),
            "impact":     e.get("impact", "medium"),
            "color":      color_map.get(e.get("event_type", ""), "gray"),
            "label":      f"Day {e.get('day', 0)}: {e.get('event_type', '').replace('_', ' ').title()}",
        }
        for e in sorted(events, key=lambda x: x.get("day", 0))
    ]


def build_supplier_scorecard(
    suppliers: dict[str, SupplierProfile],
) -> list[dict[str, Any]]:
    """Supplier scorecard table sorted by risk."""
    from .risk_propagation import _supplier_risk_score
    rows = []
    for sid, sup in suppliers.items():
        risk = _supplier_risk_score(sup)
        rows.append({
            "supplier_id":     sid,
            "name":            sup.name,
            "country":         sup.country,
            "status":          sup.status.value,
            "risk_score":      round(risk, 3),
            "risk_color":      _risk_color(risk),
            "on_time_pct":     f"{sup.on_time_delivery_pct:.0%}",
            "lead_time_days":  sup.lead_time.nominal_days,
            "moq":             f"{sup.moq:,.0f} {sup.moq_unit}",
            "bankruptcy_risk": f"{sup.bankruptcy_prob_annual:.1%}",
            "iso_9001":        "✓" if sup.iso_9001 else "✗",
            "iatf":            "✓" if sup.iatf_16949 else "✗",
        })
    return sorted(rows, key=lambda r: -r["risk_score"])


def build_cost_waterfall(
    baseline_cost:  float,
    scenario_cost:  float,
    components:     dict[str, float],   # label → EUR delta
) -> dict[str, Any]:
    """
    Waterfall chart: baseline → each cost driver → scenario.
    components: {"Cena stali": 50000, "EUR/PLN FX": -10000, "MOQ wzrost": 20000}
    """
    bars = [{"label": "Baseline", "value": baseline_cost, "type": "base", "cumulative": baseline_cost}]
    running = baseline_cost
    for label, delta in components.items():
        running += delta
        bars.append({
            "label":       label,
            "value":       delta,
            "type":        "increase" if delta > 0 else "decrease",
            "cumulative":  running,
        })
    bars.append({"label": "Scenariusz", "value": scenario_cost, "type": "total", "cumulative": scenario_cost})

    return {
        "bars":           bars,
        "total_delta":    round(scenario_cost - baseline_cost, 2),
        "total_delta_pct": round((scenario_cost - baseline_cost) / max(baseline_cost, 1) * 100, 2),
    }


def build_recommendations_panel(result: SimulationResult) -> dict[str, Any]:
    """Prioritised action list for the recommendations panel."""
    priority_colors = {
        0: "red",    # highest priority
        1: "orange",
        2: "yellow",
    }
    return {
        "impact_level": result.impact_level.value,
        "actions": [
            {
                "rank":        i + 1,
                "description": rec,
                "color":       priority_colors.get(i, "gray"),
            }
            for i, rec in enumerate(result.recommendations)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# DashboardAssembler — full dashboard payload
# ─────────────────────────────────────────────────────────────────────────────

class DashboardAssembler:
    """Assembles the full dashboard payload for frontend consumption."""

    def __init__(
        self,
        baseline:  SimulationResult,
        scenario:  SimulationResult,
        mc:        MonteCarloResult | None = None,
        resilience: ResilienceReport | None = None,
    ) -> None:
        self._baseline   = baseline
        self._scenario   = scenario
        self._mc         = mc
        self._resilience = resilience

    def build(self) -> dict[str, Any]:
        dashboard: dict[str, Any] = {
            "kpi_tiles":           build_kpi_tiles(self._scenario),
            "scenario_comparison": build_scenario_comparison(self._baseline, self._scenario),
            "event_timeline":      build_event_timeline(self._scenario.events_triggered),
            "recommendations":     build_recommendations_panel(self._scenario),
        }

        if self._mc:
            dashboard["mc_fan_chart"]  = build_mc_fan_chart(self._mc)
            dashboard["mc_stats"]      = build_mc_stats_panel(self._mc)
            dashboard["tornado_chart"] = build_tornado_chart(self._mc)

        if self._resilience:
            dashboard["resilience_spider"] = build_resilience_spider(self._resilience)
            dashboard["mitigation_actions"] = self._resilience.mitigation_actions

        return dashboard


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _risk_color(score: float) -> str:
    if score > 0.6:
        return "red"
    if score > 0.3:
        return "yellow"
    return "green"


def _sparkline(values: list[float], width: int = 10) -> list[float]:
    """Downsample a series to `width` points for sparkline."""
    if not values:
        return []
    if len(values) <= width:
        return [round(v, 2) for v in values]
    step = len(values) / width
    return [round(values[int(i * step)], 2) for i in range(width)]
