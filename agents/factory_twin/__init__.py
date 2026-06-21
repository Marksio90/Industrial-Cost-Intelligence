"""
ICI Factory Digital Twin

Cyfrowy bliźniak fabryki — symulacja, harmonogramowanie, optymalizacja.

Architecture:
  models.py           § Data models (Machine, Worker, Buffer, ProductionOrder, …)
  factory_graph.py    § 1. Factory Graph (DAG, BFS, CPM, SPF, PageRank, layout)
  simulation_model.py § 2. Simulation Model (DES, cycle, setup, breakdown, energy)
  event_engine.py     § 3. Event Engine (calendar, dispatcher, failure/PM/quality/energy events)
  scheduling.py       § 4. Scheduling Layer (FIFO/SPT/EDD/SLACK/CR, CRP, rescheduler)
  optimization.py     § 5. Optimization Layer (genetic, SA, rule comparison, buffer opt)
  dashboard.py        § 6. Dashboard (map, OEE tiles, Gantt, radar, heatmap, timeline)
  api.py              § 7. FastAPI router (layout CRUD, simulate, schedule, optimize, WS)
  monitoring.py       §    Prometheus metrics (OEE, throughput, WIP, breakdowns)

Symulowane zjawiska:
  • Obciążenie maszyn (cycle time, parallel slots, queue)
  • Przestoje (MTBF/MTTR, Weibull, eksponencjalny)
  • Awarie (DES: time-to-failure, repair, cascade)
  • Kolejki (FIFO/SPT/EDD/SLACK, sequence-dependent setup)
  • Przepływ materiału (AGV, conveyor, manual transport)
  • Energia (kW per stan + taryfa godzinowa + curtailment)
  • Pracownicy (zmiana, przerwa, zmęczenie, skill matching)
  • OEE (Availability × Performance × Quality)

KPI:
  OEE, Throughput, WIP, Lead Time, On-Time Delivery, Scrap Rate,
  Energy kWh, Bottleneck ID, Tardiness, Makespan
"""

from .api import router as factory_twin_router

from .models import (
    MachineType, MachineStatus, WorkerStatus, BufferType,
    TransportType, EventType, ShiftType,
    FailureDistribution, SchedulingRule, OptimizationGoal,
    FailureModel, EnergyProfile, SetupMatrix,
    Machine, WorkerSkill, Worker, Buffer, TransportUnit,
    RoutingStep, ProductDefinition, ProductionOrder,
    ShiftSchedule, ManufacturingCell, FactoryLayout,
    MachineKPI, FactoryKPI, SimulationConfig, SimulationResult,
    MachineIn, WorkerIn, BufferIn, OrderIn, SimConfigIn, OptimizeIn, FactoryKPIOut,
)

from .factory_graph import (
    FactoryNode, FactoryEdge, FactoryGraph,
    FactoryGraphBuilder, LayoutAutoplacer,
)

from .simulation_model import (
    Job, FactorySimulator,
    sample_ttf, sample_ttr,
    MachineState,
)

from .event_engine import (
    FactoryEvent, EventCalendar, EventDispatcher,
    FailureEventGenerator, MaintenanceScheduler,
    QualityEventGenerator, OrderEventGenerator,
    EnergyEventGenerator, WorkerShiftGenerator,
    MaterialDeliveryGenerator, CascadeEngine,
    EventEngine, make_event,
)

from .scheduling import (
    JobQueue, Dispatcher, SetupOptimizer,
    ScheduledOperation, SchedulePlan, StaticScheduler,
    RealTimeRescheduler, CapacityLoad, CapacityPlanner,
)

from .optimization import (
    OptimizationCandidate, RuleComparisonResult,
    BufferOptResult, MaintenanceOptResult,
    EnergyShiftRecommendation, WorkerAssignment,
    SchedulingRuleOptimizer, BufferOptimizer,
    MaintenanceOptimizer, EnergyOptimizer,
    WorkerAssignmentOptimizer, GeneticOptimizer,
    SimulatedAnnealingOptimizer, OptimizationReport,
    OptimizationEngine,
)

from .dashboard import (
    DashboardAssembler,
    build_factory_map, build_oee_tiles, build_gantt_chart,
    build_throughput_chart, build_oee_timeseries,
    build_wip_chart, build_energy_chart,
    build_breakdown_timeline, build_utilization_bar,
    build_kpi_summary, build_bottleneck_radar,
    build_queue_heatmap,
)

from .monitoring import (
    FactoryHealthSnapshot,
    record_simulation, record_optimization,
    factory_health_check, prometheus_response,
)

__all__ = [
    "factory_twin_router",
    # Enums
    "MachineType", "MachineStatus", "WorkerStatus", "BufferType",
    "TransportType", "EventType", "ShiftType",
    "FailureDistribution", "SchedulingRule", "OptimizationGoal",
    # Sub-models
    "FailureModel", "EnergyProfile", "SetupMatrix", "WorkerSkill",
    # Entities
    "Machine", "Worker", "Buffer", "TransportUnit",
    "RoutingStep", "ProductDefinition", "ProductionOrder",
    "ShiftSchedule", "ManufacturingCell", "FactoryLayout",
    # KPI + Config
    "MachineKPI", "FactoryKPI", "SimulationConfig", "SimulationResult",
    # Pydantic
    "MachineIn", "WorkerIn", "BufferIn", "OrderIn",
    "SimConfigIn", "OptimizeIn", "FactoryKPIOut",
    # Graph
    "FactoryNode", "FactoryEdge", "FactoryGraph",
    "FactoryGraphBuilder", "LayoutAutoplacer",
    # Simulation
    "Job", "MachineState", "FactorySimulator",
    "sample_ttf", "sample_ttr",
    # Events
    "FactoryEvent", "EventCalendar", "EventDispatcher",
    "FailureEventGenerator", "MaintenanceScheduler",
    "QualityEventGenerator", "OrderEventGenerator",
    "EnergyEventGenerator", "WorkerShiftGenerator",
    "MaterialDeliveryGenerator", "CascadeEngine",
    "EventEngine", "make_event",
    # Scheduling
    "JobQueue", "Dispatcher", "SetupOptimizer",
    "ScheduledOperation", "SchedulePlan", "StaticScheduler",
    "RealTimeRescheduler", "CapacityLoad", "CapacityPlanner",
    # Optimization
    "OptimizationCandidate", "RuleComparisonResult",
    "BufferOptResult", "MaintenanceOptResult",
    "EnergyShiftRecommendation", "WorkerAssignment",
    "SchedulingRuleOptimizer", "BufferOptimizer",
    "MaintenanceOptimizer", "EnergyOptimizer",
    "WorkerAssignmentOptimizer", "GeneticOptimizer",
    "SimulatedAnnealingOptimizer", "OptimizationReport",
    "OptimizationEngine",
    # Dashboard
    "DashboardAssembler",
    "build_factory_map", "build_oee_tiles", "build_gantt_chart",
    "build_throughput_chart", "build_oee_timeseries",
    "build_wip_chart", "build_energy_chart",
    "build_breakdown_timeline", "build_utilization_bar",
    "build_kpi_summary", "build_bottleneck_radar", "build_queue_heatmap",
    # Monitoring
    "FactoryHealthSnapshot",
    "record_simulation", "record_optimization",
    "factory_health_check", "prometheus_response",
]
