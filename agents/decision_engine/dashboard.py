"""
Dashboard — visualization builders for Decision Intelligence Engine.
"""
from __future__ import annotations

from typing import Any

from .models import Recommendation, UrgencyLevel, ImpactDimension

_URGENCY_COLOR = {
    UrgencyLevel.CRITICAL: "#dc2626",
    UrgencyLevel.HIGH:     "#ea580c",
    UrgencyLevel.MEDIUM:   "#d97706",
    UrgencyLevel.LOW:      "#16a34a",
    UrgencyLevel.WATCH:    "#2563eb",
}


def build_recommendation_cards(recs: list[Recommendation]) -> list[dict]:
    return [
        {
            "type": "recommendation_card",
            "rec_id": r.rec_id,
            "title": r.title,
            "urgency": r.urgency.value,
            "urgency_color": _URGENCY_COLOR.get(r.urgency, "#6b7280"),
            "confidence_pct": round(r.confidence.overall * 100, 1),
            "saving_eur": r.expected_saving_eur,
            "saving_pct": r.expected_saving_pct,
            "roi_pct": r.roi_pct,
            "payback_months": r.payback_months,
            "risk_level": r.risk_level,
            "score": r.score,
            "rank": r.rank,
            "rec_type": r.rec_type.value,
        }
        for r in recs
    ]


def build_confidence_bar(recs: list[Recommendation]) -> dict:
    if not recs:
        return {}
    return {
        "type": "confidence_bar",
        "series": [
            {
                "name": r.title[:30],
                "confidence": round(r.confidence.overall * 100, 1),
                "data_quality": round(r.confidence.data_quality * 100, 1),
                "signal_strength": round(r.confidence.signal_strength * 100, 1),
                "consensus": round(r.confidence.consensus * 100, 1),
                "color": _URGENCY_COLOR.get(r.urgency, "#6b7280"),
            }
            for r in recs
        ],
    }


def build_impact_waterfall(recs: list[Recommendation]) -> dict:
    if not recs:
        return {}
    items, running = [], 0.0
    for r in recs:
        running += r.expected_saving_eur
        items.append({"label": r.title[:25], "delta": r.expected_saving_eur, "cumulative": running})
    return {"type": "impact_waterfall", "items": items, "total_eur": round(running, 2)}


def build_decision_radar(recs: list[Recommendation]) -> dict:
    axes = ["Oszczędność", "Pewność", "Pilność", "ROI", "Ryzyko"]
    urgency_norm = {UrgencyLevel.CRITICAL: 1.0, UrgencyLevel.HIGH: 0.8,
                    UrgencyLevel.MEDIUM: 0.6, UrgencyLevel.LOW: 0.4, UrgencyLevel.WATCH: 0.2}
    risk_norm = {"NISKIE": 0.9, "ŚREDNIE": 0.6, "WYSOKIE": 0.3, "KRYTYCZNE": 0.1}
    max_saving = max((r.expected_saving_eur for r in recs), default=1) or 1
    series = []
    for r in recs[:6]:
        series.append({
            "name": r.title[:20],
            "values": [
                round(min(r.expected_saving_eur / max_saving, 1.0), 3),
                round(r.confidence.overall, 3),
                round(urgency_norm.get(r.urgency, 0.5), 3),
                round(min(r.roi_pct / 200, 1.0), 3),
                round(risk_norm.get(r.risk_level, 0.5), 3),
            ],
        })
    return {"type": "decision_radar", "axes": axes, "series": series}


def build_counterfactual_table(recs: list[Recommendation]) -> dict:
    rows = []
    for r in recs:
        if r.explanation and r.explanation.counterfactuals:
            for cf in r.explanation.counterfactuals:
                rows.append({
                    "recommendation": r.title[:30],
                    "scenario": cf.scenario,
                    "cost_delta_eur": cf.cost_delta_eur,
                    "risk_delta": cf.risk_delta,
                    "probability": cf.probability,
                    "time_horizon_days": cf.time_horizon_days,
                })
    return {"type": "counterfactual_table", "rows": rows}


def build_evidence_panel(recs: list[Recommendation]) -> dict:
    items = []
    for r in recs[:5]:
        if r.explanation and r.explanation.evidence:
            for e in r.explanation.evidence:
                items.append({
                    "rec_title": r.title[:25],
                    "evidence_type": e.evidence_type.value,
                    "description": e.description,
                    "value": e.value,
                    "unit": e.unit,
                    "threshold": e.threshold,
                    "direction": e.direction,
                    "quality": e.quality.value if e.quality else "unknown",
                    "sentence": e.to_sentence(),
                })
    return {"type": "evidence_panel", "items": items}


def build_pareto_chart(pareto_points: list) -> dict:
    return {
        "type": "pareto_chart",
        "x_axis": "Ryzyko",
        "y_axis": "Oszczędność EUR",
        "points": [{"x": p.risk_score, "y": p.saving_eur, "recs": p.recs} for p in pareto_points],
    }


def build_signal_heatmap(signals: dict[str, Any]) -> dict:
    numeric = {k: v for k, v in signals.items() if isinstance(v, (int, float))}
    groups: dict[str, list] = {}
    for k, v in numeric.items():
        prefix = k.split(".")[0]
        groups.setdefault(prefix, []).append({"signal": k, "value": round(float(v), 4)})
    return {"type": "signal_heatmap", "groups": groups}


def build_kpi_tiles(context_id: str, recs: list[Recommendation], run_time_ms: float) -> list[dict]:
    if not recs:
        return []
    total_saving = sum(r.expected_saving_eur for r in recs)
    avg_conf = sum(r.confidence.overall for r in recs) / len(recs)
    top = recs[0]
    return [
        {"label": "Łączna oszczędność",   "value": f"{total_saving:,.0f} EUR", "color": "#16a34a"},
        {"label": "Rekomendacji",          "value": len(recs),                   "color": "#2563eb"},
        {"label": "Śr. pewność",           "value": f"{avg_conf:.0%}",           "color": "#d97706"},
        {"label": "Top rekomendacja",      "value": top.title,                   "color": _URGENCY_COLOR.get(top.urgency, "#6b7280")},
        {"label": "Czas analizy",          "value": f"{run_time_ms:.0f} ms",     "color": "#6b7280"},
    ]


class DashboardAssembler:
    def build(self, result: Any, portfolio: Any = None) -> dict:
        recs = result.recommendations
        return {
            "kpi_tiles":            build_kpi_tiles(result.context_id, recs, result.run_time_ms),
            "recommendation_cards": build_recommendation_cards(recs),
            "decision_radar":       build_decision_radar(recs),
            "confidence_bar":       build_confidence_bar(recs),
            "impact_waterfall":     build_impact_waterfall(recs),
            "counterfactual_table": build_counterfactual_table(recs),
            "evidence_panel":       build_evidence_panel(recs),
            "signal_heatmap":       build_signal_heatmap(result.signals),
            "pareto_chart":         build_pareto_chart(portfolio.pareto_frontier if portfolio else []),
            "meta": {
                "context_id":   result.context_id,
                "n_activated":  result.n_nodes_activated,
                "n_recs":       result.n_recs_final,
                "run_time_ms":  result.run_time_ms,
            },
        }
