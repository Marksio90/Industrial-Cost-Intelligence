"""
ICI Decision Intelligence Engine

Rekomenduje najlepsze decyzje biznesowe na podstawie danych kosztowych,
produkcyjnych, dostawców i rynku.

Architecture:
  models.py                § Data models (contexts, recommendations, confidence)
  decision_graph.py        § 1. Decision Graph (DAG traversal, signal extraction)
  recommendation_engine.py § 2. Recommendation Engine (ranking, dedup, scoring)
  explainability.py        § 3. Explainability Layer (rationale, counterfactuals)
  confidence_engine.py     § 4. Confidence Engine (multi-dim scoring)
  optimization_engine.py   § 5. Multi-Objective Optimizer (Pareto, portfolio)
  dashboard.py             § 6. Dashboard (cards, radar, waterfall, evidence)
  api.py                   § 7. FastAPI router (/decisions prefix)
  monitoring.py            §    Prometheus metrics

Rekomendacje:
  CHANGE_SUPPLIER, INCREASE_MOQ, DECREASE_MOQ, CHANGE_MATERIAL,
  CHANGE_PROCESS, BUY_NOW, WAIT, HEDGE_FX, DUAL_SOURCE, RENEGOTIATE,
  INCREASE_STOCK, REDUCE_STOCK, QUALIFY_ALTERNATIVE, ESCALATE, NO_ACTION
"""

from .api import router as decision_engine_router

from .models import (
    RecommendationType, UrgencyLevel, ImpactDimension, NodeType,
    EvidenceType, ConfidenceLevel, DataQuality,
    CostContext, SupplierContext, MarketContext, ProductionContext,
    DecisionContext,
    Evidence, ConfidenceScore, ImpactEstimate, CounterfactualCase,
    Explanation, ActionParameter, Recommendation, DecisionNode,
    OptimizationConstraint, OptimizationObjective, MultiObjectiveResult,
    DecisionOutcome,
    CostContextIn, SupplierContextIn, MarketContextIn, ProductionContextIn,
    DecisionRequestIn, OutcomeFeedbackIn, OptimizeRequestIn,
)

from .decision_graph import (
    SignalExtractor, DecisionGraph, TraversalResult,
    GraphTraverser, DecisionTreeBuilder,
)

from .recommendation_engine import RecommendationEngine, EngineResult

from .explainability import ExplainabilityEngine

from .confidence_engine import ConfidenceEngine

from .optimization_engine import ParetoPoint, PortfolioResult, MultiObjectiveOptimizer

from .dashboard import (
    DashboardAssembler,
    build_recommendation_cards, build_confidence_bar,
    build_impact_waterfall, build_decision_radar,
    build_counterfactual_table, build_evidence_panel,
    build_pareto_chart, build_signal_heatmap, build_kpi_tiles,
)

from .monitoring import (
    DecisionHealthSnapshot,
    record_analysis, record_feedback, record_optimization,
    decision_health_check, prometheus_response,
)

__all__ = [
    "decision_engine_router",
    # Enums
    "RecommendationType", "UrgencyLevel", "ImpactDimension", "NodeType",
    "EvidenceType", "ConfidenceLevel", "DataQuality",
    # Contexts
    "CostContext", "SupplierContext", "MarketContext", "ProductionContext",
    "DecisionContext",
    # Core models
    "Evidence", "ConfidenceScore", "ImpactEstimate", "CounterfactualCase",
    "Explanation", "ActionParameter", "Recommendation", "DecisionNode",
    "OptimizationConstraint", "OptimizationObjective", "MultiObjectiveResult",
    "DecisionOutcome",
    # Pydantic In
    "CostContextIn", "SupplierContextIn", "MarketContextIn", "ProductionContextIn",
    "DecisionRequestIn", "OutcomeFeedbackIn", "OptimizeRequestIn",
    # Graph
    "SignalExtractor", "DecisionGraph", "TraversalResult",
    "GraphTraverser", "DecisionTreeBuilder",
    # Engines
    "RecommendationEngine", "EngineResult",
    "ExplainabilityEngine",
    "ConfidenceEngine",
    # Optimization
    "ParetoPoint", "PortfolioResult", "MultiObjectiveOptimizer",
    # Dashboard
    "DashboardAssembler",
    "build_recommendation_cards", "build_confidence_bar",
    "build_impact_waterfall", "build_decision_radar",
    "build_counterfactual_table", "build_evidence_panel",
    "build_pareto_chart", "build_signal_heatmap", "build_kpi_tiles",
    # Monitoring
    "DecisionHealthSnapshot",
    "record_analysis", "record_feedback", "record_optimization",
    "decision_health_check", "prometheus_response",
]
