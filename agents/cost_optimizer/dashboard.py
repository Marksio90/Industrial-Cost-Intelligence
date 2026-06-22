"""
Dashboard — visualization builders for Cost Optimizer.
"""
from __future__ import annotations

from typing import Any

from .models import (
    ConfigurationCandidate, OptimizationResult, TradeoffCurve,
    CostBreakdown, SearchSpace,
)
from .explainability import ExplanationReport, CostWaterfall, SensitivityEntry


def build_pareto_scatter(
    candidates: list[ConfigurationCandidate],
    x_key: str = "total_cost_eur",
    y_key: str = "risk_score",
    color_key: str = "quality_index",
) -> dict:
    def val(c, k):
        return getattr(c, k, 0)

    return {
        "type": "pareto_scatter",
        "x_axis": x_key.replace("_", " ").title(),
        "y_axis": y_key.replace("_", " ").title(),
        "color_axis": color_key.replace("_", " ").title(),
        "series": [
            {
                "id": c.candidate_id[:8],
                "x": val(c, x_key),
                "y": val(c, y_key),
                "color": val(c, color_key),
                "pareto_rank": c.pareto_rank,
                "feasible": c.feasible,
                "score": c.score,
                "label": "Pareto" if c.pareto_rank == 1 else "",
            }
            for c in candidates if c.feasible
        ],
    }


def build_cost_waterfall(waterfall: CostWaterfall) -> dict:
    items = [{"label": "Baseline", "value": waterfall.baseline_eur, "type": "total"}]
    for step in waterfall.steps:
        items.append({
            "label": step.label[:35],
            "value": step.delta_eur,
            "type": "saving" if step.delta_eur < 0 else "increase",
            "from": step.choice_from[:20],
            "to": step.choice_to[:20],
        })
    items.append({"label": "Optimum", "value": waterfall.optimum_eur, "type": "total"})
    return {
        "type": "cost_waterfall",
        "items": items,
        "total_saving": waterfall.total_saving,
        "saving_pct": waterfall.saving_pct,
    }


def build_sensitivity_tornado(sensitivity: list[SensitivityEntry], top_n: int = 12) -> dict:
    top = sorted(sensitivity, key=lambda e: abs(e.cost_impact), reverse=True)[:top_n]
    return {
        "type": "sensitivity_tornado",
        "entries": [
            {
                "label": e.label[:40],
                "cost_impact_eur": e.cost_impact,
                "pct_impact": e.pct_impact,
                "dim_id": e.dim_id,
            }
            for e in top
        ],
    }


def build_tradeoff_chart(curve: TradeoffCurve) -> dict:
    return {
        "type": "tradeoff_chart",
        "axis": curve.axis.value,
        "all_points": [
            {"x": p.x_value, "y": p.y_value, "id": p.config_id[:8], "dominated": p.dominated}
            for p in curve.points
        ],
        "pareto_points": [
            {"x": p.x_value, "y": p.y_value, "id": p.config_id[:8]}
            for p in curve.pareto_points
        ],
        "knee_point": {
            "x": curve.knee_point.x_value,
            "y": curve.knee_point.y_value,
            "id": curve.knee_point.config_id[:8],
        } if curve.knee_point else None,
    }


def build_cost_breakdown_donut(breakdown: CostBreakdown) -> dict:
    return {
        "type": "cost_breakdown_donut",
        "slices": [
            {"label": "Materiał",    "value": breakdown.material_eur,   "color": "#3b82f6"},
            {"label": "Proces",      "value": breakdown.process_eur,    "color": "#8b5cf6"},
            {"label": "Logistyka",   "value": breakdown.logistics_eur,  "color": "#06b6d4"},
            {"label": "Overhead",    "value": breakdown.overhead_eur,   "color": "#f59e0b"},
            {"label": "Jakość",      "value": breakdown.quality_eur,    "color": "#ef4444"},
            {"label": "Oprzyrząd.", "value": breakdown.tooling_eur,    "color": "#10b981"},
        ],
        "total_eur": breakdown.total_eur,
    }


def build_convergence_chart(convergence: list[float]) -> dict:
    return {
        "type": "convergence_chart",
        "series": [{"generation": i, "best_score": v} for i, v in enumerate(convergence)],
        "final": convergence[-1] if convergence else None,
    }


def build_kpi_tiles(result: OptimizationResult) -> list[dict]:
    best = result.best
    if not best:
        return []
    return [
        {"label": "Koszt optymalny",   "value": f"{best.total_cost_eur:.4f} EUR/szt.",  "color": "#16a34a"},
        {"label": "Oszczędność",        "value": f"{result.saving_eur:.4f} EUR ({result.saving_pct:.1f}%)", "color": "#2563eb"},
        {"label": "Jakość",             "value": f"{best.quality_index:.3f}",             "color": "#7c3aed"},
        {"label": "Ryzyko",             "value": f"{best.risk_score:.3f}",                "color": "#dc2626"},
        {"label": "CO₂",                "value": f"{best.co2_kg:.2f} kg/szt.",            "color": "#059669"},
        {"label": "Czas dostawy",       "value": f"{best.delivery_days:.0f} dni",         "color": "#d97706"},
        {"label": "Ewaluacji",          "value": result.run.n_evaluated if result.run else 0, "color": "#6b7280"},
    ]


def build_search_space_summary(space: SearchSpace) -> dict:
    return {
        "type": "search_space_summary",
        "n_dimensions": len(space.dimensions),
        "total_combinations": space.total_combinations,
        "dimensions": [
            {
                "dim_id": d.dim_id,
                "label": d.label[:50],
                "type": d.dim_type,
                "n_options": d.n_options,
            }
            for d in space.dimensions
        ],
    }


def build_rationale_table(rationale: list) -> dict:
    return {
        "type": "rationale_table",
        "rows": [
            {
                "dimension": r.label[:40],
                "chosen": r.chosen_id[:20],
                "cost_eur": r.chosen_cost,
                "n_alternatives": len(r.alternatives),
                "reason": r.reason[:120],
            }
            for r in rationale
        ],
    }


class DashboardAssembler:
    def build(self, result: OptimizationResult, explanation: ExplanationReport | None = None) -> dict:
        tiles = build_kpi_tiles(result)
        pareto = build_pareto_scatter(result.run.all_candidates if result.run else [])
        breakdown = build_cost_breakdown_donut(result.cost_breakdown) if result.cost_breakdown else {}
        convergence = build_convergence_chart(result.run.convergence_history if result.run else [])
        tradeoffs = {
            k: build_tradeoff_chart(v)
            for k, v in (result.tradeoffs or {}).items()
        }

        expl_section: dict = {}
        if explanation:
            expl_section = {
                "waterfall":    build_cost_waterfall(explanation.waterfall),
                "tornado":      build_sensitivity_tornado(explanation.sensitivity),
                "rationale":    build_rationale_table(explanation.rationale),
                "summary":      explanation.summary,
                "counterfacts": [
                    {"scenario": c.scenario, "cost_delta": c.cost_delta_eur, "description": c.description}
                    for c in explanation.counterfactuals
                ],
                "annual_saving_eur": explanation.annual_saving_eur,
            }

        return {
            "kpi_tiles":    tiles,
            "pareto_chart": pareto,
            "cost_breakdown": breakdown,
            "convergence":  convergence,
            "tradeoffs":    tradeoffs,
            "explanation":  expl_section,
            "meta": {
                "result_id":   result.result_id,
                "product_id":  result.product_id,
                "saving_eur":  result.saving_eur,
                "saving_pct":  result.saving_pct,
                "n_evaluated": result.run.n_evaluated if result.run else 0,
                "run_time_s":  result.run.run_time_s if result.run else 0,
            },
        }
