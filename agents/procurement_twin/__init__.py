"""
ICI Procurement Digital Twin

Cyfrowy bliźniak procesu zakupowego — symulacja, analiza ryzyka, scenariusze "co jeśli".

Architecture:
  models.py           § Data models (SupplierProfile, MaterialSpec, ScenarioDefinition, …)
  simulation_engine.py§ 1. Discrete-time simulation (GBM pricing, EOQ/ROP, lead-time sampling)
  scenario_engine.py  § 2. Scenario engine (what-if, compound shocks, stress test)
  monte_carlo.py      § 3. Monte Carlo layer (VaR, CVaR, sensitivity tornado, convergence)
  event_simulation.py § 4. DES event queue (Poisson arrivals, cascades, event calendar)
  risk_propagation.py § 5. Risk propagation (ripple effect, resilience score, portfolio VaR)
  dashboard.py        § 6. Dashboard builders (KPI tiles, fan chart, heatmap, waterfall)
  api.py              § 7. FastAPI router (scenarios, MC, what-if, WebSocket stream)
  monitoring.py       § 8. Prometheus metrics (sim latency, VaR, resilience, risk events)

What-if scenarios built-in:
  • Dostawca A znika           → simulate_supplier_failure()
  • Cena stali +15%            → simulate_price_shock()
  • EUR/PLN +10%               → simulate_fx_shock()
  • MOQ ×2                     → scenario template "moq_double"
  • Compound stress test       → scenario template "compound_stress"

Key algorithms:
  GBM       — Geometric Brownian Motion for price simulation
  EOQ       — Economic Order Quantity (Wilson formula)
  Safety SS — demand × lead-time variance formula (service-level Z-score)
  OAT       — One-At-a-Time sensitivity analysis (tornado chart)
  RRF       — Ripple Risk Factor (BFS supply graph traversal)
  HHI       — Herfindahl–Hirschman Index (supplier concentration)
  VaR/CVaR  — Value at Risk / Expected Shortfall at 95% confidence
"""

from .api import router as procurement_twin_router

from .models import (
    SupplierStatus, RiskEventType, ScenarioType, SimulationStatus,
    DistributionType, ImpactLevel,
    PriceDistribution, LeadTimeDistribution,
    SupplierProfile, MaterialSpec, ProcurementOrder,
    FXRate, InventoryState, SimulationState,
    ScenarioShock, ScenarioDefinition, SCENARIO_TEMPLATES,
    DailyKPI, SimulationResult,
    MonteCarloConfig, Percentile, MonteCarloResult,
    RiskEvent,
    # Pydantic
    SupplierProfileIn, ScenarioCreateIn, MCRunIn, SimulationRunIn,
    KPIOut, SimResultOut, MCResultOut,
)

from .simulation_engine import (
    SimulationEngine,
    SimulationStateBuilder,
    PendingDelivery,
    compute_eoq,
    compute_safety_stock,
)

from .scenario_engine import (
    ScenarioRunner,
    ScenarioLibrary,
    CompoundScenarioBuilder,
    StressTestResult,
    run_stress_test,
)

from .monte_carlo import (
    MonteCarloEngine,
    ConvergenceReport,
    analyse_convergence,
)

from .event_simulation import (
    EventQueue,
    ScheduledEvent,
    EventPriority,
    EventHandler,
    EventGenerator,
    EventDrivenSimulator,
)

from .risk_propagation import (
    RiskNode, RiskEdge, RiskGraph,
    PropagationImpact, ResilienceReport,
    RiskPropagationEngine,
)

from .dashboard import (
    DashboardAssembler,
    build_kpi_tiles,
    build_scenario_comparison,
    build_mc_fan_chart,
    build_mc_stats_panel,
    build_tornado_chart,
    build_risk_heatmap,
    build_resilience_spider,
    build_event_timeline,
    build_supplier_scorecard,
    build_cost_waterfall,
    build_recommendations_panel,
)

from .monitoring import (
    TwinMonitor,
    TwinHealthSnapshot,
    twin_health_check,
    prometheus_response,
    record_simulation,
    record_scenario_result,
    record_mc_result,
    record_risk_event,
    record_resilience,
    record_whatif,
)

__all__ = [
    "procurement_twin_router",
    # Enums
    "SupplierStatus", "RiskEventType", "ScenarioType", "SimulationStatus",
    "DistributionType", "ImpactLevel",
    # Models
    "PriceDistribution", "LeadTimeDistribution",
    "SupplierProfile", "MaterialSpec", "ProcurementOrder",
    "FXRate", "InventoryState", "SimulationState",
    "ScenarioShock", "ScenarioDefinition", "SCENARIO_TEMPLATES",
    "DailyKPI", "SimulationResult",
    "MonteCarloConfig", "Percentile", "MonteCarloResult",
    "RiskEvent",
    "SupplierProfileIn", "ScenarioCreateIn", "MCRunIn", "SimulationRunIn",
    "KPIOut", "SimResultOut", "MCResultOut",
    # Simulation
    "SimulationEngine", "SimulationStateBuilder", "PendingDelivery",
    "compute_eoq", "compute_safety_stock",
    # Scenario
    "ScenarioRunner", "ScenarioLibrary", "CompoundScenarioBuilder",
    "StressTestResult", "run_stress_test",
    # Monte Carlo
    "MonteCarloEngine", "ConvergenceReport", "analyse_convergence",
    # Events
    "EventQueue", "ScheduledEvent", "EventPriority",
    "EventHandler", "EventGenerator", "EventDrivenSimulator",
    # Risk
    "RiskNode", "RiskEdge", "RiskGraph",
    "PropagationImpact", "ResilienceReport", "RiskPropagationEngine",
    # Dashboard
    "DashboardAssembler",
    "build_kpi_tiles", "build_scenario_comparison",
    "build_mc_fan_chart", "build_mc_stats_panel", "build_tornado_chart",
    "build_risk_heatmap", "build_resilience_spider",
    "build_event_timeline", "build_supplier_scorecard",
    "build_cost_waterfall", "build_recommendations_panel",
    # Monitoring
    "TwinMonitor", "TwinHealthSnapshot",
    "twin_health_check", "prometheus_response",
    "record_simulation", "record_scenario_result", "record_mc_result",
    "record_risk_event", "record_resilience", "record_whatif",
]
