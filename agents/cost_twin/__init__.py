"""
ICI Cost Digital Twin

Cyfrowy bliźniak kosztów produktu — symulacja, scenariusze "co jeśli", optymalizacja.

Architecture:
  models.py             § Data models (CostNode, BomLine, RoutingStep, CountryProfile, …)
  causal_graph.py       § 1. Cost Causal Graph (DAG, topological propagation, impact ranking)
  dependency_model.py   § 2. Dependency Model (elasticity, learning curve, volume deps)
  simulation_engine.py  § 3. Cost Simulation Engine (breakdown, Monte Carlo, OAT sensitivity)
  scenario_planning.py  § 4. Scenario Planning (material/supplier/tech/country/volume/compound)
  optimization_engine.py§ 5. Optimization Engine (greedy, genetic, Pareto frontier)
  dashboard.py          § 6. Dashboard builders (waterfall, tornado, fan chart, country bar)
  api.py                § 7. FastAPI router (what-if, scenarios, MC, optimize, dashboard)
  monitoring.py         § 8. Prometheus metrics (computation, scenario, MC, optimization)

What-if scenarios built-in:
  • Zmiana materiału          → run_material_change()
  • Zmiana dostawcy           → run_supplier_change()
  • Zmiana technologii        → run_technology_change()
  • Zmiana kraju produkcji    → run_country_change()
  • Zmiana wolumenu           → run_volume_change()
  • Scenariusz złożony        → run_compound()

Key algorithms:
  DAG        — Kahn's topological sort, forward cost propagation
  GBM-like   — stochastic price perturbation in Monte Carlo
  OAT        — One-At-a-Time sensitivity (tornado chart)
  Wright      — Learning curve: y = y₁ × n^(−b), b = −log(lr)/log(2)
  EOQ        — Tooling amortization per unit
  Greedy     — One-at-a-time variable improvement
  Genetic    — Population-based discrete search
  Pareto     — Cost vs risk frontier enumeration
"""

from .api import router as cost_twin_router

from .models import (
    CostNodeType, CostEdgeType, ScenarioType, CostCategory,
    OptimizationObjective, CountryRiskLevel,
    CostDriver, CostNode, CostEdge,
    BomLine, RoutingStep,
    CountryProfile, SupplierCostProfile, TechnologyProfile,
    ProductCostModel,
    CostChange, CostScenario,
    CostBreakdown, CostDelta, CostResult,
    OptimizationTask, OptimizationResult,
    BomLineIn, RoutingStepIn, ProductModelIn,
    CostChangeIn, ScenarioIn, OptimizationIn, CostResultOut,
)

from .causal_graph import (
    CostGraph,
    CostGraphBuilder,
    build_steel_cost_graph,
)

from .dependency_model import (
    Dependency,
    LinearDependency,
    ProportionalDependency,
    ElasticityDependency,
    StepDependency,
    LearningCurveDependency,
    VolumeDependency,
    DependencyRegistry,
    DependencyModelBuilder,
    CostPropagator,
    COUNTRY_PROFILES,
)

from .simulation_engine import (
    CostSimulationEngine,
    MCSimResult,
    SensitivityEntry,
    compute_material_cost,
    compute_labor_and_machine_cost,
    compute_tooling_cost,
    compute_logistics_cost,
    compute_overhead_cost,
    compute_quality_cost,
    compute_tax_and_finance_cost,
)

from .scenario_planning import (
    ScenarioPlanner,
    ScenarioResult,
    ScenarioLibrary,
    CompoundScenarioBuilder,
    StressTestSummary,
    run_stress_test,
)

from .optimization_engine import (
    DecisionVariable,
    OptimizationCandidate,
    ParetoPoint,
    GreedyOptimizer,
    GeneticOptimizer,
    ParetoFrontierBuilder,
    OptimizationEngine,
)

from .dashboard import (
    DashboardAssembler,
    build_cost_breakdown_pie,
    build_cost_waterfall,
    build_country_comparison_bar,
    build_sensitivity_tornado,
    build_scenario_comparison,
    build_monte_carlo_fan,
    build_optimization_path,
    build_pareto_chart,
    build_kpi_tiles,
    build_learning_curve,
    build_recommendations_panel,
)

from .monitoring import (
    CostTwinHealthSnapshot,
    cost_health_check,
    prometheus_response,
    record_computation,
    record_scenario,
    record_mc_run,
    record_optimization,
    record_whatif,
    update_model_count,
    update_scenario_count,
)

__all__ = [
    "cost_twin_router",
    # Enums
    "CostNodeType", "CostEdgeType", "ScenarioType", "CostCategory",
    "OptimizationObjective", "CountryRiskLevel",
    # Models
    "CostDriver", "CostNode", "CostEdge",
    "BomLine", "RoutingStep",
    "CountryProfile", "SupplierCostProfile", "TechnologyProfile",
    "ProductCostModel",
    "CostChange", "CostScenario",
    "CostBreakdown", "CostDelta", "CostResult",
    "OptimizationTask", "OptimizationResult",
    "BomLineIn", "RoutingStepIn", "ProductModelIn",
    "CostChangeIn", "ScenarioIn", "OptimizationIn", "CostResultOut",
    # Causal Graph
    "CostGraph", "CostGraphBuilder", "build_steel_cost_graph",
    # Dependency Model
    "Dependency", "LinearDependency", "ProportionalDependency",
    "ElasticityDependency", "StepDependency", "LearningCurveDependency",
    "VolumeDependency", "DependencyRegistry", "DependencyModelBuilder",
    "CostPropagator", "COUNTRY_PROFILES",
    # Simulation Engine
    "CostSimulationEngine", "MCSimResult", "SensitivityEntry",
    "compute_material_cost", "compute_labor_and_machine_cost",
    "compute_tooling_cost", "compute_logistics_cost",
    "compute_overhead_cost", "compute_quality_cost",
    "compute_tax_and_finance_cost",
    # Scenario Planning
    "ScenarioPlanner", "ScenarioResult", "ScenarioLibrary",
    "CompoundScenarioBuilder", "StressTestSummary", "run_stress_test",
    # Optimization
    "DecisionVariable", "OptimizationCandidate", "ParetoPoint",
    "GreedyOptimizer", "GeneticOptimizer", "ParetoFrontierBuilder",
    "OptimizationEngine",
    # Dashboard
    "DashboardAssembler",
    "build_cost_breakdown_pie", "build_cost_waterfall",
    "build_country_comparison_bar", "build_sensitivity_tornado",
    "build_scenario_comparison", "build_monte_carlo_fan",
    "build_optimization_path", "build_pareto_chart",
    "build_kpi_tiles", "build_learning_curve", "build_recommendations_panel",
    # Monitoring
    "CostTwinHealthSnapshot", "cost_health_check", "prometheus_response",
    "record_computation", "record_scenario", "record_mc_run",
    "record_optimization", "record_whatif",
    "update_model_count", "update_scenario_count",
]
