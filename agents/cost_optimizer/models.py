"""
Cost Optimizer — Data Models

Autonomiczny system optymalizacji struktury kosztowej produktu.
Analizuje BOM, dostawców, procesy i ryzyka — zwraca najtańszą konfigurację.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class MaterialClass(str, Enum):
    RAW_METAL    = "raw_metal"
    POLYMER      = "polymer"
    COMPOSITE    = "composite"
    ELECTRONIC   = "electronic"
    FASTENER     = "fastener"
    CHEMICAL     = "chemical"
    PACKAGING    = "packaging"
    OTHER        = "other"

class ProcessType(str, Enum):
    MACHINING    = "machining"
    STAMPING     = "stamping"
    CASTING      = "casting"
    INJECTION    = "injection_molding"
    WELDING      = "welding"
    ASSEMBLY     = "assembly"
    COATING      = "coating"
    INSPECTION   = "inspection"
    ADDITIVE     = "additive_manufacturing"
    FORGING      = "forging"

class SupplierTier(str, Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"

class ConstraintType(str, Enum):
    HARD         = "hard"     # must satisfy — infeasible if violated
    SOFT         = "soft"     # penalty if violated
    PREFERENCE   = "preference"  # nice to have

class OptimizationAlgorithm(str, Enum):
    GENETIC      = "genetic"
    SIMULATED_ANNEALING = "simulated_annealing"
    BEAM_SEARCH  = "beam_search"
    GREEDY       = "greedy"
    EXHAUSTIVE   = "exhaustive"

class ObjectiveType(str, Enum):
    MINIMIZE_COST        = "minimize_cost"
    MINIMIZE_RISK        = "minimize_risk"
    MAXIMIZE_QUALITY     = "maximize_quality"
    MAXIMIZE_DELIVERY    = "maximize_delivery"
    MINIMIZE_CO2         = "minimize_co2"

class TradeoffAxis(str, Enum):
    COST_VS_QUALITY  = "cost_vs_quality"
    COST_VS_RISK     = "cost_vs_risk"
    COST_VS_DELIVERY = "cost_vs_delivery"
    COST_VS_CO2      = "cost_vs_co2"


# ─────────────────────────────────────────────────────────────────────────────
# BOM (Bill of Materials)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BOMAlternative:
    """One possible material/component for a BOM position."""
    alt_id:          str
    material_name:   str
    material_class:  MaterialClass
    unit_cost_eur:   float
    density_kg_m3:   float | None = None
    tensile_mpa:     float | None = None   # mechanical property
    quality_index:   float = 1.0           # 0–1, relative to requirement
    co2_kg_per_kg:   float = 0.0
    availability:    float = 1.0           # 0–1 supply availability
    lead_time_days:  float = 14.0
    moq:             int   = 1
    supplier_ids:    list[str] = field(default_factory=list)
    certifications:  list[str] = field(default_factory=list)
    metadata:        dict[str, Any] = field(default_factory=dict)


@dataclass
class BOMItem:
    """A single line on the BOM with one or more alternatives."""
    item_id:         str
    description:     str
    quantity:        float          # per product unit
    unit:            str = "kg"
    required_quality_index: float = 0.9   # minimum acceptable
    function_critical: bool = False        # true = hard constraint on quality
    current_alt_id:  str = ""
    alternatives:    list[BOMAlternative] = field(default_factory=list)


@dataclass
class BOM:
    """Complete Bill of Materials for a product."""
    bom_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str = ""
    product_name: str = ""
    annual_volume: int = 10_000
    items:      list[BOMItem] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Suppliers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SupplierProfile:
    supplier_id:      str
    name:             str
    country:          str = "DE"
    tier:             SupplierTier = SupplierTier.TIER_1
    # Performance
    otd_pct:          float = 95.0
    defect_rate_pct:  float = 0.5
    lead_time_days:   float = 14.0
    # Risk
    financial_risk:   float = 0.2   # 0–1
    geo_risk:         float = 0.2
    single_source_risk: float = 0.0  # 0 = multiple sources available
    # Cost
    price_stability:  float = 0.8   # 0 = volatile, 1 = stable
    discount_moq:     dict[int, float] = field(default_factory=dict)  # {moq: discount_pct}
    # Environment
    co2_logistics_kg_per_kg: float = 0.1
    certifications:   list[str] = field(default_factory=list)
    # Composite scores (computed)
    quality_score:    float = 0.0
    risk_score:       float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Processes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProcessAlternative:
    proc_id:          str
    process_type:     ProcessType
    description:      str
    cycle_time_s:     float
    labor_cost_eur_h: float
    machine_cost_eur_h: float
    scrap_rate_pct:   float = 1.0
    energy_kwh:       float = 0.0    # per cycle
    tooling_eur:      float = 0.0    # amortizable tooling per unit at volume
    quality_index:    float = 1.0
    min_volume:       int   = 0      # economical minimum volume
    max_volume:       int   = 10_000_000
    co2_kg_per_unit:  float = 0.0
    capex_eur:        float = 0.0    # capital investment
    metadata:         dict[str, Any] = field(default_factory=dict)

    @property
    def cost_per_unit(self) -> float:
        """Total process cost per unit (labor + machine + energy + tooling amortized per 10k units)."""
        cycle_h = self.cycle_time_s / 3600
        labor = self.labor_cost_eur_h * cycle_h
        machine = self.machine_cost_eur_h * cycle_h
        energy = self.energy_kwh * 0.12  # €0.12/kWh
        tooling_amort = self.tooling_eur / max(10_000, 1)
        return labor + machine + energy + tooling_amort


@dataclass
class ProcessStep:
    step_id:      str
    description:  str
    alternatives: list[ProcessAlternative] = field(default_factory=list)
    current_proc_id: str = ""


@dataclass
class ProcessRouting:
    routing_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id:  str = ""
    steps:       list[ProcessStep] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Search Space
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchDimension:
    """One axis of the search space (one BOM item or process step)."""
    dim_id:        str
    dim_type:      str   # "material" | "process" | "supplier" | "volume"
    label:         str
    n_options:     int
    option_ids:    list[str] = field(default_factory=list)
    current_idx:   int = 0

@dataclass
class SearchSpace:
    space_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    dimensions:  list[SearchDimension] = field(default_factory=list)
    total_combinations: int = 0

    def compute_size(self) -> int:
        n = 1
        for d in self.dimensions:
            n *= max(d.n_options, 1)
        self.total_combinations = n
        return n


# ─────────────────────────────────────────────────────────────────────────────
# Constraints
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Constraint:
    constraint_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    constraint_type: ConstraintType = ConstraintType.HARD
    name:           str = ""
    description:    str = ""
    # Evaluation function specification
    signal:         str = ""    # e.g. "quality_index", "risk_score", "cost_eur"
    operator:       str = ">="  # ">=", "<=", "==", "!=", "in", "range"
    threshold:      Any = None
    threshold_max:  Any = None  # for range
    penalty_weight: float = 1.0  # for soft constraints
    metadata:       dict[str, Any] = field(default_factory=dict)


@dataclass
class ConstraintViolation:
    constraint_id: str
    constraint_name: str
    constraint_type: ConstraintType
    signal: str
    actual_value: Any
    required_value: Any
    penalty: float = 0.0
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Configuration candidate
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConfigurationCandidate:
    """One possible product configuration — a specific combination of choices."""
    candidate_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    generation:     int = 0
    # Choices: dim_id → option_id
    choices:        dict[str, str] = field(default_factory=dict)
    # Computed KPIs
    material_cost_eur:  float = 0.0
    process_cost_eur:   float = 0.0
    logistics_cost_eur: float = 0.0
    overhead_eur:       float = 0.0
    total_cost_eur:     float = 0.0
    quality_index:      float = 0.0
    risk_score:         float = 0.0
    co2_kg:             float = 0.0
    delivery_days:      float = 0.0
    # Feasibility
    feasible:           bool  = True
    violations:         list[ConstraintViolation] = field(default_factory=list)
    # Scoring
    score:              float = 0.0     # weighted objective score
    pareto_rank:        int   = 0       # 0 = not classified
    crowding_distance:  float = 0.0
    # Explanation
    cost_drivers:       list[str] = field(default_factory=list)
    savings_vs_baseline: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Optimization result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptimizationRun:
    run_id:         str = field(default_factory=lambda: str(uuid.uuid4()))
    algorithm:      OptimizationAlgorithm = OptimizationAlgorithm.GENETIC
    objectives:     list[ObjectiveType] = field(default_factory=list)
    n_evaluated:    int = 0
    n_feasible:     int = 0
    n_generations:  int = 0
    run_time_s:     float = 0.0
    convergence_history: list[float] = field(default_factory=list)  # best score per gen
    best_candidate: ConfigurationCandidate | None = None
    pareto_front:   list[ConfigurationCandidate] = field(default_factory=list)
    all_candidates: list[ConfigurationCandidate] = field(default_factory=list)


@dataclass
class TradeoffPoint:
    """A point on a 2D tradeoff curve."""
    config_id:    str
    x_label:      str
    y_label:      str
    x_value:      float
    y_value:      float
    dominated:    bool = False
    choices:      dict[str, str] = field(default_factory=dict)


@dataclass
class TradeoffCurve:
    axis:         TradeoffAxis
    points:       list[TradeoffPoint]
    pareto_points: list[TradeoffPoint]
    knee_point:   TradeoffPoint | None = None  # optimal balanced choice


@dataclass
class CostBreakdown:
    material_eur:   float = 0.0
    process_eur:    float = 0.0
    logistics_eur:  float = 0.0
    overhead_eur:   float = 0.0
    quality_eur:    float = 0.0   # scrap + rework
    tooling_eur:    float = 0.0
    total_eur:      float = 0.0

    def compute_total(self) -> None:
        self.total_eur = (self.material_eur + self.process_eur + self.logistics_eur
                         + self.overhead_eur + self.quality_eur + self.tooling_eur)


@dataclass
class OptimizationResult:
    """Top-level result returned to the user."""
    result_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id:      str = ""
    run:             OptimizationRun | None = None
    # Best configuration
    best:            ConfigurationCandidate | None = None
    baseline_cost_eur: float = 0.0
    best_cost_eur:   float = 0.0
    saving_eur:      float = 0.0
    saving_pct:      float = 0.0
    # Tradeoffs
    tradeoffs:       dict[str, TradeoffCurve] = field(default_factory=dict)
    # Breakdown
    cost_breakdown:  CostBreakdown | None = None
    # Explanation
    explanation:     list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    risk_flags:      list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API models
# ─────────────────────────────────────────────────────────────────────────────

class BOMAlternativeIn(BaseModel):
    alt_id:          str
    material_name:   str
    material_class:  MaterialClass = MaterialClass.OTHER
    unit_cost_eur:   float
    quality_index:   float = 1.0
    co2_kg_per_kg:   float = 0.0
    availability:    float = 1.0
    lead_time_days:  float = 14.0
    moq:             int   = 1
    supplier_ids:    list[str] = Field(default_factory=list)


class BOMItemIn(BaseModel):
    item_id:          str
    description:      str
    quantity:         float
    unit:             str = "kg"
    required_quality_index: float = 0.9
    function_critical: bool = False
    alternatives:     list[BOMAlternativeIn] = Field(default_factory=list)


class BOMIn(BaseModel):
    product_id:    str
    product_name:  str = ""
    annual_volume: int = 10_000
    items:         list[BOMItemIn] = Field(default_factory=list)


class SupplierIn(BaseModel):
    supplier_id:       str
    name:              str
    country:           str = "DE"
    tier:              SupplierTier = SupplierTier.TIER_1
    otd_pct:           float = 95.0
    defect_rate_pct:   float = 0.5
    lead_time_days:    float = 14.0
    financial_risk:    float = 0.2
    geo_risk:          float = 0.2
    price_stability:   float = 0.8


class ProcessAlternativeIn(BaseModel):
    proc_id:          str
    process_type:     ProcessType
    description:      str = ""
    cycle_time_s:     float
    labor_cost_eur_h: float = 25.0
    machine_cost_eur_h: float = 40.0
    scrap_rate_pct:   float = 1.0
    quality_index:    float = 1.0
    min_volume:       int   = 0
    max_volume:       int   = 10_000_000


class ProcessStepIn(BaseModel):
    step_id:      str
    description:  str
    alternatives: list[ProcessAlternativeIn] = Field(default_factory=list)


class ProcessRoutingIn(BaseModel):
    product_id: str
    steps:      list[ProcessStepIn] = Field(default_factory=list)


class ConstraintIn(BaseModel):
    constraint_type: ConstraintType = ConstraintType.HARD
    name:            str
    signal:          str
    operator:        str = ">="
    threshold:       float
    penalty_weight:  float = 1.0


class OptimizeRequestIn(BaseModel):
    product_id:    str
    bom:           BOMIn
    routing:       ProcessRoutingIn | None = None
    suppliers:     list[SupplierIn] = Field(default_factory=list)
    constraints:   list[ConstraintIn] = Field(default_factory=list)
    objectives:    list[ObjectiveType] = Field(default_factory=lambda: [ObjectiveType.MINIMIZE_COST])
    objective_weights: list[float] = Field(default_factory=lambda: [1.0])
    algorithm:     OptimizationAlgorithm = OptimizationAlgorithm.GENETIC
    annual_volume: int = 10_000
    # Genetic params
    population_size: int = Field(50, ge=10, le=500)
    generations:     int = Field(30, ge=1,  le=200)
    mutation_rate:   float = Field(0.15, ge=0.01, le=0.5)
    # Output
    top_n:           int = 5
