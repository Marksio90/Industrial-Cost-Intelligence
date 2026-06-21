"""
ICI Cost Optimizer — Autonomous Cost Structure Optimizer

System samodzielnie znajduje najtańszą konfigurację produktu analizując
BOM, dostawców, procesy i ryzyka jednocześnie.

Architecture:
  models.py              § Data models (BOM, Supplier, Process, SearchSpace, …)
  search_space.py        § 1. Search Space Definition + Cost Evaluator
  constraint_engine.py   § 2. Constraint Engine (HARD/SOFT/PREFERENCE)
  optimization_engine.py § 3. Optimization Engine (Greedy, Beam, SA, Genetic)
  multi_objective.py     § 4. Multi-Objective Optimization (NSGA-II, Pareto)
  tradeoff_engine.py     § 5+6. Cost vs Quality + Risk vs Cost Tradeoffs
  explainability.py      § 7. Explainability (Waterfall, Rationale, Sensitivity)
  dashboard.py           §    Dashboard builders
  api.py                 §    FastAPI router (/cost-optimizer prefix)
  monitoring.py          § 8. Prometheus metrics

Algorytmy:
  • Greedy      — O(∑ N_i), szybki baseline
  • Beam Search — O(K × ∑ N_i), top-K ekspansja
  • Simulated Annealing — ucieczka z lokalnych minimów
  • Genetic Algorithm   — ewolucja populacji (NSGA-II aware)
"""

from .api import router as cost_optimizer_router

from .models import (
    MaterialClass, ProcessType, SupplierTier,
    ConstraintType, OptimizationAlgorithm, ObjectiveType, TradeoffAxis,
    BOMAlternative, BOMItem, BOM,
    SupplierProfile,
    ProcessAlternative, ProcessStep, ProcessRouting,
    SearchDimension, SearchSpace,
    Constraint, ConstraintViolation,
    ConfigurationCandidate, OptimizationRun,
    TradeoffPoint, TradeoffCurve, CostBreakdown, OptimizationResult,
    BOMAlternativeIn, BOMItemIn, BOMIn,
    SupplierIn, ProcessAlternativeIn, ProcessStepIn, ProcessRoutingIn,
    ConstraintIn, OptimizeRequestIn,
)

from .search_space import (
    SearchSpaceBuilder, ConfigurationEncoder,
    BaselineExtractor, CostEvaluator,
)

from .constraint_engine import (
    ConstraintEngine, ConstraintAnalyser, ConstraintReport,
    default_constraints,
)

from .optimization_engine import (
    build_objective_fn,
    GreedyOptimizer, BeamSearchOptimizer,
    SimulatedAnnealingOptimizer, GeneticOptimizer,
    OptimizationEngine,
)

from .multi_objective import (
    non_dominated_sort, compute_crowding_distance, find_knee_point,
    NSGAResult, TradeoffCurveBuilder, MultiObjectiveOptimizer,
)

from .tradeoff_engine import (
    QualityCostPoint, CostQualityAnalysis, CostQualityAnalyzer,
    RiskCostPoint, RiskCostAnalysis, RiskCostAnalyzer,
)

from .explainability import (
    WaterfallStep, CostWaterfall, SensitivityEntry,
    DimensionRationale, CounterfactualScenario, ExplanationReport,
    ExplainabilityEngine,
    build_waterfall, sensitivity_analysis, build_rationale,
    generate_summary, build_counterfactuals,
)

from .dashboard import (
    DashboardAssembler,
    build_pareto_scatter, build_cost_waterfall, build_sensitivity_tornado,
    build_tradeoff_chart, build_cost_breakdown_donut,
    build_convergence_chart, build_kpi_tiles, build_search_space_summary,
    build_rationale_table,
)

from .monitoring import (
    OptimizerHealthSnapshot,
    record_run, record_violation,
    optimizer_health_check, prometheus_response,
)

__all__ = [
    "cost_optimizer_router",
    # Enums
    "MaterialClass", "ProcessType", "SupplierTier",
    "ConstraintType", "OptimizationAlgorithm", "ObjectiveType", "TradeoffAxis",
    # BOM
    "BOMAlternative", "BOMItem", "BOM",
    # Supplier
    "SupplierProfile",
    # Process
    "ProcessAlternative", "ProcessStep", "ProcessRouting",
    # Search Space
    "SearchDimension", "SearchSpace",
    # Constraints
    "Constraint", "ConstraintViolation",
    # Candidates & Results
    "ConfigurationCandidate", "OptimizationRun",
    "TradeoffPoint", "TradeoffCurve", "CostBreakdown", "OptimizationResult",
    # Pydantic
    "BOMAlternativeIn", "BOMItemIn", "BOMIn",
    "SupplierIn", "ProcessAlternativeIn", "ProcessStepIn", "ProcessRoutingIn",
    "ConstraintIn", "OptimizeRequestIn",
    # Search Space
    "SearchSpaceBuilder", "ConfigurationEncoder", "BaselineExtractor", "CostEvaluator",
    # Constraints
    "ConstraintEngine", "ConstraintAnalyser", "ConstraintReport", "default_constraints",
    # Optimization
    "build_objective_fn",
    "GreedyOptimizer", "BeamSearchOptimizer",
    "SimulatedAnnealingOptimizer", "GeneticOptimizer", "OptimizationEngine",
    # Multi-objective
    "non_dominated_sort", "compute_crowding_distance", "find_knee_point",
    "NSGAResult", "TradeoffCurveBuilder", "MultiObjectiveOptimizer",
    # Tradeoff
    "QualityCostPoint", "CostQualityAnalysis", "CostQualityAnalyzer",
    "RiskCostPoint", "RiskCostAnalysis", "RiskCostAnalyzer",
    # Explainability
    "WaterfallStep", "CostWaterfall", "SensitivityEntry",
    "DimensionRationale", "CounterfactualScenario", "ExplanationReport",
    "ExplainabilityEngine",
    "build_waterfall", "sensitivity_analysis", "build_rationale",
    "generate_summary", "build_counterfactuals",
    # Dashboard
    "DashboardAssembler",
    "build_pareto_scatter", "build_cost_waterfall", "build_sensitivity_tornado",
    "build_tradeoff_chart", "build_cost_breakdown_donut",
    "build_convergence_chart", "build_kpi_tiles", "build_search_space_summary",
    "build_rationale_table",
    # Monitoring
    "OptimizerHealthSnapshot",
    "record_run", "record_violation", "optimizer_health_check", "prometheus_response",
]
