"""Dashboard builders for the World Model."""
from __future__ import annotations
from typing import Any

from .models import ScenarioType, ProductionCostForecast, WorldState
from .simulation import SimulationResult
from .scenario_planning import ScenarioComparison
from .optimization import OptimizationResult


def build_fan_chart(comparison: ScenarioComparison) -> dict[str, Any]:
    """Fan chart: cost trajectories per scenario across 4 horizons."""
    horizons = ["1m", "3m", "6m", "12m"]
    series = []
    for r in comparison.scenarios:
        costs = [r.cost_at_m1, r.cost_at_m3, r.cost_at_m6, r.cost_at_m12]
        series.append({
            "scenario": r.scenario.value,
            "costs": [round(c, 4) for c in costs],
            "delta_pct_m12": round(r.delta_pct_m12, 2),
        })

    return {
        "type": "fan_chart",
        "title": "Prognoza kosztów produkcji — wszystkie scenariusze",
        "x_axis": horizons,
        "y_axis_label": "Koszt jednostkowy [EUR]",
        "base_cost": comparison.base_cost_eur,
        "series": series,
        "annotations": {
            "worst_m12": round(comparison.worst_case_m12, 4),
            "best_m12": round(comparison.best_case_m12, 4),
            "baseline_m12": round(comparison.baseline_m12, 4),
            "spread_pct": round(comparison.spread_pct, 2),
        },
    }


def build_scenario_comparison_table(comparison: ScenarioComparison) -> dict[str, Any]:
    rows = []
    for r in comparison.scenarios:
        rows.append({
            "scenario": r.scenario.value,
            "cost_m1": round(r.cost_at_m1, 4),
            "cost_m3": round(r.cost_at_m3, 4),
            "cost_m6": round(r.cost_at_m6, 4),
            "cost_m12": round(r.cost_at_m12, 4),
            "delta_pct_m12": round(r.delta_pct_m12, 2),
            "var_95_12m": round(r.var_95_12m, 4) if r.var_95_12m else None,
            "n_flags": len(r.risk_flags),
            "top_flag": r.risk_flags[0].message if r.risk_flags else None,
        })
    rows.sort(key=lambda x: x["cost_m12"], reverse=True)
    return {
        "type": "comparison_table",
        "title": "Porównanie scenariuszy kosztowych",
        "columns": ["scenario", "cost_m1", "cost_m3", "cost_m6", "cost_m12", "delta_pct_m12", "var_95_12m"],
        "rows": rows,
    }


def build_var_distribution(sim: SimulationResult) -> dict[str, Any]:
    """VaR distribution: P5/P25/P50/P75/P95 at each horizon."""
    horizons = ["1m", "3m", "6m", "12m"]
    dists = [sim.dist_1m, sim.dist_3m, sim.dist_6m, sim.dist_12m]
    bands = []
    for h, d in zip(horizons, dists):
        bands.append({
            "horizon": h,
            "p5": round(d.p5, 4),
            "p25": round(d.p25, 4),
            "p50": round(d.p50, 4),
            "p75": round(d.p75, 4),
            "p95": round(d.p95, 4),
            "mean": round(d.mean, 4),
        })
    return {
        "type": "var_distribution",
        "title": "Rozkład kosztu jednostkowego (Monte Carlo)",
        "bands": bands,
        "var_95_12m": round(sim.var_95_12m, 4),
        "cvar_95_12m": round(sim.cvar_95_12m, 4),
        "prob_above_10pct": round(sim.probability_above_threshold.get("above_10pct", 0.0), 3),
    }


def build_causal_path_viz(causal_graph_dict: dict[str, Any]) -> dict[str, Any]:
    """Causal graph visualization payload."""
    return {
        "type": "causal_graph",
        "title": "Graf przyczynowo-skutkowy — wpływ na koszty produkcji",
        "nodes": causal_graph_dict.get("nodes", []),
        "edges": causal_graph_dict.get("edges", []),
        "stats": causal_graph_dict.get("stats", {}),
        "layout": "dagre",
    }


def build_signal_heatmap(world: WorldState) -> dict[str, Any]:
    """Heatmap of current world signal values vs historical norms."""
    signals = [
        {"id": "cpi_eu", "label": "Inflacja CPI (EU)", "value": world.inflation.cpi_yoy,
         "norm": 2.0, "unit": "%", "group": "Makro"},
        {"id": "ppi_eu", "label": "Inflacja PPI (EU)", "value": world.inflation.ppi_yoy,
         "norm": 2.5, "unit": "%", "group": "Makro"},
        {"id": "fx_eurusd", "label": "EUR/USD", "value": world.fx.eur_usd,
         "norm": 1.10, "unit": "", "group": "FX"},
        {"id": "fx_eurcny", "label": "EUR/CNY", "value": world.fx.eur_cny,
         "norm": 7.50, "unit": "", "group": "FX"},
        {"id": "oil", "label": "Ropa (USD/bbl)", "value": world.energy.oil_usd_bbl,
         "norm": 70.0, "unit": "USD/bbl", "group": "Energia"},
        {"id": "gas", "label": "Gaz EU (EUR/MWh)", "value": world.energy.gas_eur_mwh,
         "norm": 25.0, "unit": "EUR/MWh", "group": "Energia"},
        {"id": "elec", "label": "Prąd EU (EUR/MWh)", "value": world.energy.electricity_eur_mwh,
         "norm": 60.0, "unit": "EUR/MWh", "group": "Energia"},
        {"id": "steel", "label": "Stal (EUR/t)", "value": world.commodities.steel_eur_t,
         "norm": 550.0, "unit": "EUR/t", "group": "Surowce"},
        {"id": "alum", "label": "Aluminium (EUR/t)", "value": world.commodities.aluminum_eur_t,
         "norm": 2000.0, "unit": "EUR/t", "group": "Surowce"},
        {"id": "copper", "label": "Miedź (EUR/t)", "value": world.commodities.copper_eur_t,
         "norm": 7500.0, "unit": "EUR/t", "group": "Surowce"},
        {"id": "freight", "label": "Frety (USD/FEU)", "value": world.logistics.shanghai_rotterdam_usd,
         "norm": 1200.0, "unit": "USD/FEU", "group": "Logistyka"},
        {"id": "geo_risk", "label": "Ryzyko geopolityczne", "value": world.geopolitics.conflict_risk_eu,
         "norm": 0.10, "unit": "", "group": "Geopolityka"},
    ]
    for s in signals:
        norm = s["norm"]
        if norm:
            s["deviation_pct"] = round((s["value"] - norm) / norm * 100, 1)
            s["status"] = "HIGH" if s["deviation_pct"] > 20 else ("LOW" if s["deviation_pct"] < -15 else "NORMAL")
        else:
            s["deviation_pct"] = 0.0
            s["status"] = "NORMAL"
    return {
        "type": "signal_heatmap",
        "title": "Aktualne sygnały makroekonomiczne vs norma historyczna",
        "signals": signals,
        "groups": ["Makro", "FX", "Energia", "Surowce", "Logistyka", "Geopolityka"],
    }


def build_hedge_recommendation_table(opt_result: OptimizationResult) -> dict[str, Any]:
    sol = opt_result.robust_solution
    rows = [
        {
            "action": h.action.value,
            "description": h.description,
            "cost_eur": round(h.cost_eur, 0),
            "saving_eur_annual": round(h.saving_eur_annual * h.confidence, 0),
            "roi_pct": round((h.saving_eur_annual * h.confidence - h.cost_eur) / h.cost_eur * 100, 1)
                       if h.cost_eur else 0.0,
            "horizon_months": h.horizon_months,
            "confidence": round(h.confidence, 2),
        }
        for h in sol.selected_hedges
    ]
    return {
        "type": "hedge_table",
        "title": "Rekomendowane działania zabezpieczające",
        "rows": rows,
        "summary": {
            "total_hedge_cost_eur": round(sol.total_hedge_cost_eur, 0),
            "expected_saving_eur": round(sol.expected_saving_eur, 0),
            "net_benefit_eur": round(sol.net_benefit_eur, 0),
            "robustness_score": round(sol.robustness_score, 3),
            "worst_case_cost_m12": round(sol.worst_case_cost_m12, 4),
        },
    }


def build_risk_flag_panel(comparison: ScenarioComparison) -> dict[str, Any]:
    flags = [
        {
            "horizon": f.horizon,
            "severity": f.severity,
            "message": f.message,
            "cost_delta_pct": round(f.cost_delta_pct, 2),
        }
        for f in comparison.top_risks
    ]
    return {
        "type": "risk_flag_panel",
        "title": "Flagi ryzyka — top zagrożenia kosztowe",
        "flags": flags,
    }


def build_kpi_tiles(comparison: ScenarioComparison, opt_result: OptimizationResult) -> dict[str, Any]:
    base = comparison.base_cost_eur
    bl12 = comparison.baseline_m12
    worst12 = comparison.worst_case_m12
    rob = opt_result.robust_solution
    return {
        "type": "kpi_tiles",
        "tiles": [
            {"label": "Koszt bazowy", "value": f"{base:.4f} EUR", "status": "INFO"},
            {"label": "Prognoza 12m (baseline)", "value": f"{bl12:.4f} EUR",
             "delta": f"{(bl12-base)/base*100:+.1f}%", "status": "INFO"},
            {"label": "Worst case 12m", "value": f"{worst12:.4f} EUR",
             "delta": f"{(worst12-base)/base*100:+.1f}%", "status": "CRITICAL" if worst12 > base * 1.15 else "MEDIUM"},
            {"label": "Spread scenariuszy", "value": f"{comparison.spread_pct:.1f}%",
             "status": "HIGH" if comparison.spread_pct > 20 else "MEDIUM"},
            {"label": "Korzyść z hedgingu", "value": f"{rob.net_benefit_eur:.0f} EUR/rok",
             "status": "POSITIVE" if rob.net_benefit_eur > 0 else "NEGATIVE"},
            {"label": "Odporność portfela", "value": f"{rob.robustness_score:.1%}",
             "status": "POSITIVE" if rob.robustness_score > 0.7 else "MEDIUM"},
        ],
    }


class WorldModelDashboard:
    def build(
        self,
        comparison: ScenarioComparison,
        opt_result: OptimizationResult,
        world: WorldState,
        baseline_sim: SimulationResult,
        causal_graph_dict: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "product_id": comparison.product_id,
            "base_cost_eur": comparison.base_cost_eur,
            "run_time_ms": comparison.run_time_ms + opt_result.run_time_ms,
            "panels": {
                "kpi_tiles": build_kpi_tiles(comparison, opt_result),
                "fan_chart": build_fan_chart(comparison),
                "scenario_table": build_scenario_comparison_table(comparison),
                "var_distribution": build_var_distribution(baseline_sim),
                "signal_heatmap": build_signal_heatmap(world),
                "causal_graph": build_causal_path_viz(causal_graph_dict),
                "risk_flags": build_risk_flag_panel(comparison),
                "hedge_table": build_hedge_recommendation_table(opt_result),
            },
        }
