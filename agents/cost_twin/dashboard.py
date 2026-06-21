"""
Section 6 — Dashboard

Builders widgetów dla cost digital twin:
  - build_cost_waterfall()   → kaskadowy wykres kosztu (breakdown → delta)
  - build_cost_breakdown_pie() → struktura kosztu (kołowy / donut)
  - build_country_comparison_bar() → porównanie krajów (słupkowy)
  - build_sensitivity_tornado() → wykres tornado OAT
  - build_scenario_comparison() → porównanie scenariuszy
  - build_monte_carlo_fan() → wachlarz percentyli MC
  - build_optimization_path() → historia iteracji optymalizatora
  - build_pareto_chart() → frontier koszt vs ryzyko
  - build_kpi_tiles() → kafelki KPI
  - build_learning_curve() → krzywa uczenia Wright'a
  - DashboardAssembler → kompletna odpowiedź dla frontendu
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import CostBreakdown, CostDelta, CostResult
from .simulation_engine import MCSimResult, SensitivityEntry


# ─────────────────────────────────────────────────────────────────────────────
# Widget helpers
# ─────────────────────────────────────────────────────────────────────────────

def _round(v: float, n: int = 2) -> float:
    return round(v, n)


def build_cost_breakdown_pie(breakdown: CostBreakdown) -> dict[str, Any]:
    """Donut chart — cost structure breakdown."""
    items = [
        ("Materiały bezpośrednie",  breakdown.direct_material_eur),
        ("Robocizna bezpośrednia",  breakdown.direct_labor_eur),
        ("Maszyny",                 breakdown.machine_cost_eur),
        ("Energia",                 breakdown.energy_cost_eur),
        ("Oprzyrządowanie",         breakdown.tooling_cost_eur),
        ("Narzut fabryczny",        breakdown.factory_overhead_eur),
        ("Narzut admin.",           breakdown.admin_overhead_eur),
        ("Logistyka",               breakdown.logistics_eur),
        ("Jakość / braki",          breakdown.quality_cost_eur),
        ("CO₂ / podatki",           breakdown.co2_tax_eur),
        ("Finansowanie",            breakdown.finance_cost_eur),
        ("Marża",                   breakdown.margin_eur),
    ]
    total = breakdown.total_price_eur or 1.0
    return {
        "type":   "pie",
        "labels": [i[0] for i in items],
        "values": [_round(i[1]) for i in items],
        "pcts":   [_round(i[1] / total * 100) for i in items],
        "total_eur": _round(total),
    }


def build_cost_waterfall(
    breakdown: CostBreakdown,
    highlight_delta: CostDelta | None = None,
) -> dict[str, Any]:
    """
    Waterfall chart — cumulative cost build-up.
    Optional delta overlay when comparing two scenarios.
    """
    steps = [
        ("Materiały",   breakdown.direct_material_eur),
        ("Robocizna",   breakdown.direct_labor_eur),
        ("Maszyny",     breakdown.machine_cost_eur),
        ("Energia",     breakdown.energy_cost_eur),
        ("Narzuty",     breakdown.factory_overhead_eur + breakdown.admin_overhead_eur),
        ("Oprzyr.",     breakdown.tooling_cost_eur),
        ("Logistyka",   breakdown.logistics_eur),
        ("Jakość",      breakdown.quality_cost_eur),
        ("CO₂+fin.",    breakdown.co2_tax_eur + breakdown.finance_cost_eur),
        ("Marża",       breakdown.margin_eur),
    ]

    running  = 0.0
    labels:  list[str]   = []
    values:  list[float] = []
    bases:   list[float] = []
    colors:  list[str]   = []

    for label, val in steps:
        labels.append(label)
        bases.append(_round(running))
        values.append(_round(val))
        running += val
        colors.append("#2196F3" if val >= 0 else "#F44336")

    labels.append("Łącznie")
    bases.append(0.0)
    values.append(_round(running))
    colors.append("#4CAF50")

    result: dict[str, Any] = {
        "type":   "waterfall",
        "labels": labels,
        "bases":  bases,
        "values": values,
        "colors": colors,
        "total":  _round(running),
    }

    if highlight_delta:
        result["delta_eur"] = _round(highlight_delta.delta_eur)
        result["delta_pct"] = _round(highlight_delta.delta_pct)

    return result


def build_country_comparison_bar(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Stacked bar chart — cost per country.
    rows: output from OptimizationEngine.quick_country_comparison()
    """
    return {
        "type":     "bar_stacked",
        "countries": [r["country"] for r in rows],
        "series": {
            "Materiały":  [r["material_eur"] for r in rows],
            "Robocizna":  [r["labor_eur"]    for r in rows],
            "Logistyka":  [r["logistics_eur"] for r in rows],
            "Pozostałe":  [
                _round(r["total_cost_eur"] - r["material_eur"] - r["labor_eur"] - r["logistics_eur"])
                for r in rows
            ],
        },
        "totals":   [r["total_cost_eur"]  for r in rows],
        "risks":    [r["risk_level"]       for r in rows],
        "baseline": rows[0]["country"] if rows else None,
    }


def build_sensitivity_tornado(entries: list[SensitivityEntry]) -> dict[str, Any]:
    """Tornado chart — OAT sensitivity ranked by |swing|."""
    top = entries[:15]  # top 15 drivers
    return {
        "type":     "tornado",
        "drivers":  [e.driver     for e in top],
        "hi_eur":   [_round(e.hi_eur)    for e in top],
        "lo_eur":   [_round(e.lo_eur)    for e in top],
        "swing":    [_round(e.swing_eur) for e in top],
        "elasticity": [_round(e.elasticity, 3) for e in top],
    }


def build_scenario_comparison(
    baseline_result: CostResult,
    scenario_results: list[tuple[str, CostResult]],
) -> dict[str, Any]:
    """
    Side-by-side scenario comparison.
    scenario_results: list of (scenario_name, CostResult)
    """
    def _extract(r: CostResult) -> dict[str, float]:
        b = r.breakdown
        return {
            "total":    _round(b.total_cost_eur),
            "material": _round(b.direct_material_eur),
            "labor":    _round(b.direct_labor_eur),
            "logistics":_round(b.logistics_eur),
            "overhead": _round(b.factory_overhead_eur + b.admin_overhead_eur),
        }

    base = _extract(baseline_result)
    scenarios_out = []
    for name, r in scenario_results:
        s = _extract(r)
        delta = s["total"] - base["total"]
        scenarios_out.append({
            "name":      name,
            "values":    s,
            "delta_eur": _round(delta),
            "delta_pct": _round(delta / base["total"] * 100 if base["total"] else 0.0),
        })

    return {
        "type":      "scenario_comparison",
        "baseline":  base,
        "scenarios": scenarios_out,
        "metrics":   ["total", "material", "labor", "logistics", "overhead"],
    }


def build_monte_carlo_fan(mc: MCSimResult) -> dict[str, Any]:
    """Fan chart for Monte Carlo cost distribution."""
    hist = mc.histogram(bins=30)
    return {
        "type":      "mc_fan",
        "mean":      _round(mc.mean_eur),
        "std":       _round(mc.std_eur),
        "p5":        _round(mc.p5_eur),
        "p50":       _round(mc.p50_eur),
        "p95":       _round(mc.p95_eur),
        "var_95":    _round(mc.var_95),
        "var_99":    _round(mc.var_99),
        "cvar_95":   _round(mc.cvar_95),
        "n_runs":    mc.n_runs,
        "histogram": {str(k): v for k, v in hist.items()},
    }


def build_optimization_path(improvement_path: list[dict[str, Any]]) -> dict[str, Any]:
    """Line chart — cost evolution during optimization."""
    return {
        "type":   "optimization_path",
        "steps":  [r["step"] for r in improvement_path],
        "costs":  [_round(r["cost_eur"]) for r in improvement_path],
        "labels": [
            r.get("metadata", {}).get("improved_var", f"step {r['step']}")
            for r in improvement_path
        ],
    }


def build_pareto_chart(pareto_frontier: list[dict[str, Any]]) -> dict[str, Any]:
    """Scatter plot — cost vs risk Pareto frontier."""
    return {
        "type":      "pareto",
        "points": [
            {
                "cost_eur":   _round(p["cost_eur"]),
                "risk_score": _round(p["risk_score"], 3),
                "decisions":  p["decisions"],
            }
            for p in pareto_frontier
        ],
    }


def build_kpi_tiles(result: CostResult, delta: CostDelta | None = None) -> list[dict[str, Any]]:
    """4–6 KPI tiles for header panel."""
    b = result.breakdown
    tiles = [
        {
            "id":    "total_cost",
            "label": "Koszt jednostkowy",
            "value": _round(b.total_cost_eur),
            "unit":  "EUR",
            "icon":  "euro",
            "color": "blue",
        },
        {
            "id":    "material_share",
            "label": "Udział materiałów",
            "value": _round(b.direct_material_eur / b.total_cost_eur * 100 if b.total_cost_eur else 0.0),
            "unit":  "%",
            "icon":  "layers",
            "color": "orange",
        },
        {
            "id":    "labor_share",
            "label": "Udział robocizny",
            "value": _round(b.direct_labor_eur / b.total_cost_eur * 100 if b.total_cost_eur else 0.0),
            "unit":  "%",
            "icon":  "people",
            "color": "purple",
        },
        {
            "id":    "margin",
            "label": "Marża",
            "value": _round(b.margin_eur / b.total_price_eur * 100 if b.total_price_eur else 0.0),
            "unit":  "%",
            "icon":  "trending_up",
            "color": "green",
        },
    ]

    if delta:
        tiles.append({
            "id":    "cost_delta",
            "label": "Zmiana kosztu",
            "value": _round(delta.delta_eur),
            "unit":  "EUR",
            "icon":  "swap_vert",
            "color": "red" if delta.delta_eur > 0 else "green",
            "trend": "up" if delta.delta_eur > 0 else "down",
            "delta_pct": _round(delta.delta_pct),
        })

    return tiles


def build_learning_curve(
    base_cost:     float,
    base_volume:   int,
    volumes:       list[int],
    learning_rate: float = 0.85,
) -> dict[str, Any]:
    """Wright's learning curve: cost vs cumulative volume."""
    import math
    b = -math.log(1 / learning_rate) / math.log(2)
    costs = [base_cost * (v / base_volume) ** b for v in volumes]
    return {
        "type":          "learning_curve",
        "volumes":       volumes,
        "costs":         [_round(c) for c in costs],
        "learning_rate": learning_rate,
        "base_volume":   base_volume,
        "base_cost":     _round(base_cost),
    }


def build_recommendations_panel(recommendations: list[str]) -> dict[str, Any]:
    return {
        "type":            "recommendations",
        "count":           len(recommendations),
        "recommendations": recommendations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full dashboard assembler
# ─────────────────────────────────────────────────────────────────────────────

class DashboardAssembler:
    """
    Assembles a complete dashboard payload for the cost twin.
    Accepts a baseline result + optional scenario/optimization/MC results.
    """

    def build(
        self,
        baseline:           CostResult,
        delta:              CostDelta               | None = None,
        scenario_results:   list[tuple[str, CostResult]] = (),
        sensitivity:        list[SensitivityEntry]  | None = None,
        mc_result:          MCSimResult             | None = None,
        optimization_path:  list[dict[str, Any]]   | None = None,
        pareto_frontier:    list[dict[str, Any]]   | None = None,
        country_comparison: list[dict[str, Any]]   | None = None,
        recommendations:    list[str]              | None = None,
        base_volume:        int                    | None = None,
    ) -> dict[str, Any]:
        dashboard: dict[str, Any] = {
            "kpi_tiles":       build_kpi_tiles(baseline, delta),
            "cost_breakdown":  build_cost_breakdown_pie(baseline.breakdown),
            "cost_waterfall":  build_cost_waterfall(baseline.breakdown, delta),
        }

        if scenario_results:
            dashboard["scenario_comparison"] = build_scenario_comparison(baseline, list(scenario_results))

        if sensitivity:
            dashboard["sensitivity_tornado"] = build_sensitivity_tornado(sensitivity)

        if mc_result:
            dashboard["monte_carlo_fan"] = build_monte_carlo_fan(mc_result)

        if optimization_path:
            dashboard["optimization_path"] = build_optimization_path(optimization_path)

        if pareto_frontier:
            dashboard["pareto_chart"] = build_pareto_chart(pareto_frontier)

        if country_comparison:
            dashboard["country_comparison"] = build_country_comparison_bar(country_comparison)

        if recommendations:
            dashboard["recommendations"] = build_recommendations_panel(recommendations)

        if base_volume:
            volumes = [base_volume // 4, base_volume // 2, base_volume, base_volume * 2, base_volume * 4]
            dashboard["learning_curve"] = build_learning_curve(
                baseline.breakdown.direct_labor_eur, base_volume, volumes
            )

        return dashboard
