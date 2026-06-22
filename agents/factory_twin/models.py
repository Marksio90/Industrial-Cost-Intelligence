"""
Factory Digital Twin — Data Models

Encje:
  Machine           — maszyna (CNC, prasa, robot, piec, linia montażowa)
  Workstation       — stanowisko robocze (człowiek + maszyna)
  Worker            — pracownik (kwalifikacje, zmiany, dostępność)
  Buffer            — bufor / magazyn WIP między operacjami
  TransportUnit     — wózek, AGV, przenośnik
  Process           — operacja technologiczna (czas cyklu, SPC, yield)
  ProductionOrder   — zlecenie produkcyjne (produkt, ilość, termin)
  ShiftCalendar     — kalendarz zmianowy
  SimulationConfig  — parametry symulacji
  SimulationResult  — wynik symulacji
  KPISnapshot       — migawka KPI (OEE, throughput, WIP, utilization)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class MachineType(str, Enum):
    CNC_MILLING      = "cnc_milling"
    CNC_TURNING      = "cnc_turning"
    PRESS            = "press"
    WELDING_ROBOT    = "welding_robot"
    ASSEMBLY_ROBOT   = "assembly_robot"
    INSPECTION_CMM   = "inspection_cmm"
    CONVEYOR         = "conveyor"
    AGV              = "agv"
    FURNACE          = "furnace"
    PAINTING_BOOTH   = "painting_booth"
    MANUAL_STATION   = "manual_station"
    LASER_CUTTER     = "laser_cutter"
    INJECTION_MOLD   = "injection_mold"
    PACKAGING        = "packaging"
    GENERIC          = "generic"


class MachineStatus(str, Enum):
    IDLE          = "idle"
    PROCESSING    = "processing"
    SETUP         = "setup"
    BREAKDOWN     = "breakdown"
    MAINTENANCE   = "maintenance"
    STARVED       = "starved"       # waiting for input
    BLOCKED       = "blocked"       # output buffer full
    OFFLINE       = "offline"


class WorkerStatus(str, Enum):
    AVAILABLE   = "available"
    BUSY        = "busy"
    BREAK       = "break"
    ABSENT      = "absent"
    TRAINING    = "training"


class BufferType(str, Enum):
    RAW_MATERIAL = "raw_material"
    WIP          = "wip"            # work-in-progress
    FINISHED     = "finished"
    SCRAP        = "scrap"
    QUARANTINE   = "quarantine"


class TransportType(str, Enum):
    AGV          = "agv"
    FORKLIFT     = "forklift"
    CONVEYOR     = "conveyor"
    MANUAL       = "manual"
    OVERHEAD     = "overhead_crane"


class EventType(str, Enum):
    # Machine events
    MACHINE_BREAKDOWN     = "machine_breakdown"
    MACHINE_REPAIR        = "machine_repair"
    MACHINE_SETUP         = "machine_setup"
    MACHINE_SETUP_DONE    = "machine_setup_done"
    MAINTENANCE_START     = "maintenance_start"
    MAINTENANCE_DONE      = "maintenance_done"
    # Process events
    JOB_ARRIVE            = "job_arrive"
    JOB_START             = "job_start"
    JOB_DONE              = "job_done"
    JOB_SCRAP             = "job_scrap"
    JOB_REWORK            = "job_rework"
    # Worker events
    WORKER_SHIFT_START    = "worker_shift_start"
    WORKER_SHIFT_END      = "worker_shift_end"
    WORKER_BREAK_START    = "worker_break_start"
    WORKER_BREAK_END      = "worker_break_end"
    WORKER_ABSENT         = "worker_absent"
    # Material events
    MATERIAL_DELIVERY     = "material_delivery"
    BUFFER_FULL           = "buffer_full"
    BUFFER_EMPTY          = "buffer_empty"
    TRANSPORT_DISPATCH    = "transport_dispatch"
    TRANSPORT_ARRIVE      = "transport_arrive"
    # Energy events
    ENERGY_SPIKE          = "energy_spike"
    ENERGY_CURTAILMENT    = "energy_curtailment"
    # Scheduling events
    ORDER_RELEASED        = "order_released"
    ORDER_COMPLETED       = "order_completed"
    ORDER_LATE            = "order_late"
    PRIORITY_CHANGE       = "priority_change"
    # Quality events
    QUALITY_ALARM         = "quality_alarm"
    SPC_VIOLATION         = "spc_violation"


class ShiftType(str, Enum):
    MORNING     = "morning"      # 06:00–14:00
    AFTERNOON   = "afternoon"    # 14:00–22:00
    NIGHT       = "night"        # 22:00–06:00
    DAY_8H      = "day_8h"       # 08:00–16:00
    CONTINUOUS  = "continuous"   # 24/7


class FailureDistribution(str, Enum):
    EXPONENTIAL  = "exponential"   # memoryless (most common)
    WEIBULL      = "weibull"       # bathtub curve
    NORMAL       = "normal"        # wear-out
    LOGNORMAL    = "lognormal"


class SchedulingRule(str, Enum):
    FIFO         = "fifo"            # First In First Out
    SPT          = "spt"             # Shortest Processing Time
    LPT          = "lpt"             # Longest Processing Time
    EDD          = "edd"             # Earliest Due Date
    SLACK         = "slack"          # Minimum Slack (due_date - now - remaining_time)
    CRITICAL_RATIO = "critical_ratio" # (due_date - now) / remaining_time
    PRIORITY     = "priority"        # explicit priority field


class OptimizationGoal(str, Enum):
    MAX_THROUGHPUT     = "max_throughput"
    MIN_MAKESPAN       = "min_makespan"
    MIN_TARDINESS      = "min_tardiness"
    MAX_OEE            = "max_oee"
    MIN_WIP            = "min_wip"
    MIN_ENERGY         = "min_energy"
    BALANCED           = "balanced"


# ─────────────────────────────────────────────────────────────────────────────
# Core dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FailureModel:
    """MTBF / MTTR parameters for a machine."""
    mtbf_hours:       float = 500.0    # Mean Time Between Failures
    mttr_hours:       float = 2.0      # Mean Time To Repair
    distribution:     FailureDistribution = FailureDistribution.EXPONENTIAL
    weibull_shape:    float = 1.5      # shape parameter (if Weibull)
    degradation_rate: float = 0.0      # OEE degradation per operating hour


@dataclass
class EnergyProfile:
    """Power consumption per machine state (kW)."""
    idle_kw:       float = 2.0
    processing_kw: float = 15.0
    setup_kw:      float = 5.0
    breakdown_kw:  float = 0.5        # standby
    peak_kw:       float = 20.0       # transient peak
    co2_kg_per_kwh: float = 0.233     # grid emission factor


@dataclass
class SetupMatrix:
    """Sequence-dependent setup times between product families."""
    from_to: dict[tuple[str, str], float] = field(default_factory=dict)  # (from_family, to_family) → minutes

    def get(self, from_fam: str, to_fam: str, default: float = 0.0) -> float:
        return self.from_to.get((from_fam, to_fam), default)


@dataclass
class Machine:
    machine_id:    str
    name:          str
    machine_type:  MachineType
    cell_id:       str             # belongs to manufacturing cell
    status:        MachineStatus = MachineStatus.IDLE
    # Capacity
    cycle_time_s:  float = 60.0   # nominal cycle time per unit
    parallel_slots: int = 1        # how many jobs can run in parallel
    # Reliability
    failure_model: FailureModel = field(default_factory=FailureModel)
    # Energy
    energy:        EnergyProfile = field(default_factory=EnergyProfile)
    # Setup
    setup_matrix:  SetupMatrix   = field(default_factory=SetupMatrix)
    current_family: str | None   = None
    # Quality
    cpk:           float = 1.33   # process capability index
    defect_rate:   float = 0.005  # baseline defect rate
    # State tracking
    hours_since_maintenance: float = 0.0
    total_parts_produced:    int   = 0
    current_job_id:          str | None = None
    queue:                   list[str] = field(default_factory=list)  # job_ids waiting
    # Coordinates (for layout visualization)
    x: float = 0.0
    y: float = 0.0


@dataclass
class WorkerSkill:
    machine_types:  list[MachineType]
    skill_level:    float = 1.0    # 0–1 efficiency multiplier
    certified:      bool  = True


@dataclass
class Worker:
    worker_id:  str
    name:       str
    cell_id:    str
    status:     WorkerStatus = WorkerStatus.AVAILABLE
    shift:      ShiftType    = ShiftType.MORNING
    skills:     list[WorkerSkill] = field(default_factory=list)
    # State
    current_machine_id: str | None = None
    hours_worked_today: float = 0.0
    fatigue_factor:     float = 1.0   # 1.0 = fully rested, <1 = fatigued
    # Stats
    parts_produced:     int = 0
    defects_caused:     int = 0


@dataclass
class Buffer:
    buffer_id:    str
    name:         str
    buffer_type:  BufferType
    cell_id:      str
    capacity:     int = 100       # max units
    current_qty:  int = 0
    # Material tracking: product_id → qty
    contents:     dict[str, int] = field(default_factory=dict)
    # Location
    x: float = 0.0
    y: float = 0.0

    @property
    def fill_pct(self) -> float:
        return self.current_qty / self.capacity if self.capacity else 0.0

    @property
    def is_full(self) -> bool:
        return self.current_qty >= self.capacity

    @property
    def is_empty(self) -> bool:
        return self.current_qty == 0


@dataclass
class TransportUnit:
    unit_id:       str
    transport_type: TransportType
    capacity_units: int = 10
    speed_m_s:     float = 1.5       # travel speed m/s
    current_load:  int = 0
    available:     bool = True
    location_id:   str = "depot"     # current node id
    destination_id: str | None = None
    x: float = 0.0
    y: float = 0.0


@dataclass
class RoutingStep:
    step_id:       str
    sequence:      int
    machine_id:    str             # required machine
    process_type:  str
    cycle_time_s:  float           # per unit
    setup_time_s:  float = 0.0    # per changeover
    worker_required: bool = False
    required_skill:  MachineType | None = None
    yield_pct:     float = 1.0     # fraction of good parts
    # SPC control limits
    usl: float | None = None
    lsl: float | None = None


@dataclass
class ProductDefinition:
    product_id:    str
    name:          str
    family:        str
    routing:       list[RoutingStep]
    priority:      int   = 5       # 1 = highest
    weight_kg:     float = 1.0
    # BOM (simplified)
    raw_material_kg: float = 1.0


@dataclass
class ProductionOrder:
    order_id:     str
    product_id:   str
    quantity:     int
    due_date:     datetime
    released_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    priority:     int = 5
    # Progress
    completed_qty:  int = 0
    scrapped_qty:   int = 0
    remaining_qty:  int = 0
    status:         str = "pending"   # pending / in_progress / done / late
    # Tracking
    actual_start:   datetime | None = None
    actual_end:     datetime | None = None

    def __post_init__(self) -> None:
        if self.remaining_qty == 0:
            self.remaining_qty = self.quantity


@dataclass
class ShiftSchedule:
    shift:      ShiftType
    start_hour: int    # 0–23
    end_hour:   int
    break_minutes: int = 30
    days:       list[int] = field(default_factory=lambda: [0,1,2,3,4])  # Mon–Fri

    @property
    def duration_h(self) -> float:
        raw = (self.end_hour - self.start_hour) % 24
        return raw - self.break_minutes / 60.0


@dataclass
class ManufacturingCell:
    cell_id:    str
    name:       str
    machines:   list[str] = field(default_factory=list)   # machine_ids
    workers:    list[str] = field(default_factory=list)   # worker_ids
    buffers:    list[str] = field(default_factory=list)   # buffer_ids
    x: float = 0.0
    y: float = 0.0
    width: float = 10.0
    height: float = 8.0


@dataclass
class FactoryLayout:
    factory_id:  str
    name:        str
    cells:       list[ManufacturingCell] = field(default_factory=list)
    machines:    dict[str, Machine]      = field(default_factory=dict)
    workers:     dict[str, Worker]       = field(default_factory=dict)
    buffers:     dict[str, Buffer]       = field(default_factory=dict)
    transports:  dict[str, TransportUnit] = field(default_factory=dict)
    products:    dict[str, ProductDefinition] = field(default_factory=dict)
    orders:      dict[str, ProductionOrder]   = field(default_factory=dict)
    # Edges: (from_node_id, to_node_id) → transport_distance_m
    edges:       dict[tuple[str, str], float] = field(default_factory=dict)
    # Energy tariff (hour → EUR/kWh)
    energy_tariff: dict[int, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# KPI + Result models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MachineKPI:
    machine_id:       str
    utilization_pct:  float = 0.0    # processing / available time
    availability_pct: float = 100.0  # (available - breakdown) / available
    performance_pct:  float = 100.0  # actual rate / theoretical rate
    quality_pct:      float = 100.0  # good parts / total parts
    oee:              float = 0.0    # A × P × Q
    throughput_h:     float = 0.0    # parts / hour
    starved_pct:      float = 0.0
    blocked_pct:      float = 0.0
    breakdown_count:  int   = 0
    total_downtime_h: float = 0.0
    energy_kwh:       float = 0.0
    defect_count:     int   = 0

    def compute_oee(self) -> None:
        self.oee = (self.availability_pct / 100) * (self.performance_pct / 100) * (self.quality_pct / 100) * 100


@dataclass
class FactoryKPI:
    simulation_id:     str
    sim_time_h:        float = 0.0
    throughput_total:  int   = 0
    throughput_h:      float = 0.0
    makespan_h:        float = 0.0
    avg_wip:           float = 0.0
    avg_lead_time_h:   float = 0.0
    total_energy_kwh:  float = 0.0
    energy_cost_eur:   float = 0.0
    avg_oee:           float = 0.0
    machine_kpis:      dict[str, MachineKPI] = field(default_factory=dict)
    late_orders:       int   = 0
    total_orders:      int   = 0
    on_time_delivery:  float = 100.0
    total_scrap:       int   = 0
    scrap_rate:        float = 0.0
    bottleneck_id:     str   = ""
    wip_by_buffer:     dict[str, int] = field(default_factory=dict)


@dataclass
class SimulationConfig:
    sim_id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    horizon_hours:   float = 8.0       # simulation horizon
    time_step_s:     float = 1.0       # simulation tick in seconds
    warm_up_hours:   float = 1.0       # warm-up period (excluded from KPIs)
    scheduling_rule: SchedulingRule = SchedulingRule.FIFO
    seed:            int | None = None
    # Flags
    simulate_failures:  bool = True
    simulate_energy:    bool = True
    simulate_transport: bool = True
    simulate_workers:   bool = True
    realtime_factor:    float = 0.0    # 0 = as fast as possible


@dataclass
class SimulationResult:
    sim_id:          str
    config:          SimulationConfig
    factory_kpi:     FactoryKPI
    event_log:       list[dict[str, Any]] = field(default_factory=list)
    gantt_data:      list[dict[str, Any]] = field(default_factory=list)
    kpi_timeseries:  list[dict[str, Any]] = field(default_factory=list)
    run_time_s:      float = 0.0
    warnings:        list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API models
# ─────────────────────────────────────────────────────────────────────────────

class MachineIn(BaseModel):
    machine_id:    str
    name:          str
    machine_type:  MachineType
    cell_id:       str
    cycle_time_s:  float = 60.0
    mtbf_hours:    float = 500.0
    mttr_hours:    float = 2.0
    defect_rate:   float = 0.005
    idle_kw:       float = 2.0
    processing_kw: float = 15.0
    x: float = 0.0
    y: float = 0.0


class WorkerIn(BaseModel):
    worker_id:    str
    name:         str
    cell_id:      str
    shift:        ShiftType = ShiftType.MORNING
    skill_types:  list[MachineType] = Field(default_factory=list)
    skill_level:  float = 1.0


class BufferIn(BaseModel):
    buffer_id:   str
    name:        str
    buffer_type: BufferType
    cell_id:     str
    capacity:    int = 100
    x: float = 0.0
    y: float = 0.0


class OrderIn(BaseModel):
    order_id:   str
    product_id: str
    quantity:   int
    due_date:   datetime
    priority:   int = 5


class SimConfigIn(BaseModel):
    horizon_hours:      float = 8.0
    time_step_s:        float = 1.0
    warm_up_hours:      float = 1.0
    scheduling_rule:    SchedulingRule = SchedulingRule.FIFO
    seed:               int | None = None
    simulate_failures:  bool = True
    simulate_energy:    bool = True


class OptimizeIn(BaseModel):
    goal:               OptimizationGoal = OptimizationGoal.MAX_OEE
    algorithm:          str = "greedy"   # greedy | genetic | simulated_annealing
    max_iterations:     int = 50
    population_size:    int = 20
    scheduling_rules:   list[SchedulingRule] = Field(default_factory=list)


class FactoryKPIOut(BaseModel):
    throughput_total: int
    throughput_h:     float
    avg_oee:          float
    avg_wip:          float
    total_energy_kwh: float
    on_time_delivery: float
    scrap_rate:       float
    bottleneck_id:    str
    late_orders:      int
