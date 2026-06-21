"""
Procurement Digital Twin — Data Models

Entities:
  SupplierProfile     — symulowany dostawca (cena, lead time, ryzyko, MOQ)
  MaterialSpec        — materiał z parametrami rynkowymi
  ProcurementOrder    — zamówienie zakupowe
  SimulationState     — pełny stan cyfrowego bliźniaka w chwili T
  ScenarioDefinition  — definicja scenariusza (co jeśli)
  SimulationResult    — wynik przebiegu symulacji
  RiskEvent           — zdarzenie ryzyka (bankructwo, opóźnienie, spike ceny)
  MonteCarloResult    — rozkład wyników MC
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class SupplierStatus(str, Enum):
    ACTIVE      = "active"
    DEGRADED    = "degraded"       # partial capacity
    DISRUPTED   = "disrupted"      # major delay
    BANKRUPT    = "bankrupt"       # gone
    SANCTIONED  = "sanctioned"     # regulatory block
    PREFERRED   = "preferred"
    BLACKLISTED = "blacklisted"


class RiskEventType(str, Enum):
    SUPPLIER_BANKRUPT     = "supplier_bankrupt"
    SUPPLIER_CAPACITY_CUT = "supplier_capacity_cut"
    PRICE_SPIKE           = "price_spike"
    PRICE_DROP            = "price_drop"
    LEAD_TIME_INCREASE    = "lead_time_increase"
    MOQ_INCREASE          = "moq_increase"
    FX_SHOCK              = "fx_shock"
    LOGISTICS_DISRUPTION  = "logistics_disruption"
    QUALITY_FAILURE       = "quality_failure"
    GEOPOLITICAL_EVENT    = "geopolitical_event"
    NATURAL_DISASTER      = "natural_disaster"
    DEMAND_SURGE          = "demand_surge"
    REGULATORY_CHANGE     = "regulatory_change"


class ScenarioType(str, Enum):
    SUPPLIER_LOSS         = "supplier_loss"
    PRICE_CHANGE          = "price_change"
    FX_CHANGE             = "fx_change"
    MOQ_CHANGE            = "moq_change"
    LEAD_TIME_CHANGE      = "lead_time_change"
    DEMAND_CHANGE         = "demand_change"
    COMPOUND              = "compound"        # multiple changes at once
    STRESS_TEST           = "stress_test"     # worst-case portfolio
    CUSTOM                = "custom"


class SimulationStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class DistributionType(str, Enum):
    NORMAL      = "normal"
    LOGNORMAL   = "lognormal"
    TRIANGULAR  = "triangular"
    UNIFORM     = "uniform"
    BETA        = "beta"
    POISSON     = "poisson"
    EXPONENTIAL = "exponential"


class ImpactLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Core data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PriceDistribution:
    """Stochastic price model for a material/supplier pair."""
    base_price:    float                     # EUR/unit current
    currency:      str     = "EUR"
    unit:          str     = "kg"
    # Distribution parameters
    distribution:  DistributionType = DistributionType.LOGNORMAL
    mean_pct_yoy:  float   = 0.0             # expected annual drift %
    volatility:    float   = 0.15            # annualised σ (15% default)
    skew:          float   = 0.0             # lognormal parameter
    # Bounds
    floor_pct:     float   = -0.30           # max drop −30%
    ceiling_pct:   float   = 1.00            # max spike +100%
    # Seasonality (index per month, 1.0 = neutral)
    seasonal_idx:  list[float] = field(default_factory=lambda: [1.0] * 12)


@dataclass
class LeadTimeDistribution:
    """Stochastic lead-time model (days)."""
    nominal_days:  int                       # contracted lead time
    distribution:  DistributionType = DistributionType.LOGNORMAL
    mean_days:     float = 0.0               # offset from nominal (+ = slower)
    std_days:      float = 3.0               # daily std
    min_days:      int   = 1
    max_days:      int   = 180
    # P90 / P99 under stress
    stress_p90:    float = 0.0               # extra days at P90 stress
    stress_p99:    float = 0.0


@dataclass
class SupplierProfile:
    """Full stochastic model of a supplier."""
    supplier_id:    str = field(default_factory=lambda: str(uuid4()))
    name:           str = ""
    country:        str = ""
    tier:           int = 1          # 1 = direct, 2 = sub-supplier
    status:         SupplierStatus = SupplierStatus.ACTIVE
    # Pricing
    price_model:    PriceDistribution = field(default_factory=PriceDistribution)
    # Lead time
    lead_time:      LeadTimeDistribution = field(default_factory=LeadTimeDistribution)
    # Capacity
    capacity_units_per_month:  float = 10_000.0
    capacity_utilisation_pct:  float = 0.70
    # Minimum order
    moq:            float = 100.0
    moq_unit:       str   = "kg"
    # Risk parameters
    bankruptcy_prob_annual: float = 0.02       # P(bankrupt in 12 months)
    quality_defect_rate:    float = 0.01       # 1% defect rate
    on_time_delivery_pct:   float = 0.92
    # Financial health
    credit_rating:  str   = "BBB"
    payment_terms:  str   = "NET30"
    # Geographic risk
    geo_risk_score: float = 0.20               # 0=safe 1=extreme
    single_port:    bool  = False              # uses single export port
    # Materials supplied
    material_ids:   list[str] = field(default_factory=list)
    # Metadata
    tags:           list[str] = field(default_factory=list)
    tenant_id:      str = "tenant-demo"


@dataclass
class MaterialSpec:
    """Material with market linkage."""
    material_id:    str = field(default_factory=lambda: str(uuid4()))
    name:           str = ""
    sku:            str = ""
    unit:           str = "kg"
    # Market
    commodity_code: str | None = None          # link to market monitor
    price_model:    PriceDistribution = field(default_factory=PriceDistribution)
    # BOM usage
    annual_usage:   float = 0.0               # units/year
    safety_stock:   float = 0.0               # units
    reorder_point:  float = 0.0               # units
    # Criticality
    is_critical:    bool  = False
    substitutes:    list[str] = field(default_factory=list)   # material_ids
    # Suppliers
    supplier_ids:   list[str] = field(default_factory=list)
    primary_supplier_id: str | None = None
    # Cost breakdown
    material_cost_pct:  float = 0.60          # % of total unit cost
    logistics_cost_pct: float = 0.15
    duty_pct:           float = 0.05
    tenant_id:          str   = "tenant-demo"


@dataclass
class ProcurementOrder:
    order_id:       str = field(default_factory=lambda: str(uuid4()))
    material_id:    str = ""
    supplier_id:    str = ""
    quantity:       float = 0.0
    unit:           str   = "kg"
    unit_price:     float = 0.0
    currency:       str   = "EUR"
    total_cost:     float = 0.0
    order_date:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expected_delivery: datetime | None = None
    actual_delivery:   datetime | None = None
    status:         str   = "open"       # open | delivered | delayed | cancelled
    delay_days:     int   = 0
    quality_ok:     bool  = True
    notes:          str   = ""


@dataclass
class FXRate:
    base:   str   = "EUR"
    quote:  str   = "USD"
    rate:   float = 1.0
    date:   date  = field(default_factory=date.today)
    # Stochastic model
    volatility:  float = 0.08    # annualised FX vol
    drift:       float = 0.0     # expected drift


@dataclass
class InventoryState:
    material_id:     str
    on_hand:         float = 0.0
    on_order:        float = 0.0
    safety_stock:    float = 0.0
    demand_per_day:  float = 0.0
    days_of_supply:  float = 0.0
    stockout_risk:   float = 0.0    # probability of stockout in 30d


@dataclass
class SimulationState:
    """Snapshot of the entire procurement system at time T."""
    sim_id:       str = field(default_factory=lambda: str(uuid4()))
    tenant_id:    str = ""
    timestamp:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    day:          int = 0                    # simulation day
    # Entities
    suppliers:    dict[str, SupplierProfile] = field(default_factory=dict)
    materials:    dict[str, MaterialSpec]    = field(default_factory=dict)
    orders:       list[ProcurementOrder]     = field(default_factory=list)
    inventory:    dict[str, InventoryState]  = field(default_factory=dict)
    # FX
    fx_rates:     dict[str, FXRate]          = field(default_factory=dict)
    # KPIs
    total_spend:  float = 0.0
    total_savings: float = 0.0
    avg_lead_time: float = 0.0
    service_level: float = 1.0
    at_risk_spend: float = 0.0
    # Active risk events
    active_events: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario definition
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioShock:
    """A single parameter perturbation within a scenario."""
    target_type:   str            # "supplier" | "material" | "fx" | "market"
    target_id:     str            # supplier_id / material_id / currency pair
    parameter:     str            # "status" | "price_delta_pct" | "lead_time_delta" | "moq_multiplier" | "rate_delta_pct"
    value:         Any            # new value or delta
    day_offset:    int = 0        # when shock hits (simulation day)
    duration_days: int | None = None   # None = permanent
    ramp_days:     int = 0        # gradual change over N days


@dataclass
class ScenarioDefinition:
    scenario_id:   str = field(default_factory=lambda: str(uuid4()))
    name:          str = ""
    description:   str = ""
    scenario_type: ScenarioType = ScenarioType.CUSTOM
    shocks:        list[ScenarioShock] = field(default_factory=list)
    # Simulation params
    horizon_days:  int   = 365
    warm_up_days:  int   = 30
    seed:          int   = 42
    tenant_id:     str   = "tenant-demo"
    created_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Built-in scenario templates
SCENARIO_TEMPLATES: dict[str, dict[str, Any]] = {
    "supplier_loss": {
        "name":        "Dostawca A znika",
        "description": "Bankruptcy of primary supplier",
        "shocks": [{"target_type": "supplier", "parameter": "status", "value": "bankrupt", "day_offset": 0}],
    },
    "steel_price_15pct": {
        "name":        "Cena stali +15%",
        "description": "Steel price spike 15% permanent",
        "shocks": [{"target_type": "material", "parameter": "price_delta_pct", "value": 15.0, "day_offset": 0}],
    },
    "eur_fx_10pct": {
        "name":        "EUR +10%",
        "description": "EUR appreciation 10% vs PLN/USD",
        "shocks": [{"target_type": "fx", "target_id": "EUR/PLN", "parameter": "rate_delta_pct", "value": 10.0, "day_offset": 0}],
    },
    "moq_double": {
        "name":        "MOQ x2",
        "description": "Supplier doubles minimum order quantity",
        "shocks": [{"target_type": "supplier", "parameter": "moq_multiplier", "value": 2.0, "day_offset": 0}],
    },
    "compound_stress": {
        "name":        "Stress test kompilowany",
        "description": "Supplier loss + price spike + FX shock simultaneously",
        "shocks": [
            {"target_type": "supplier", "parameter": "status",            "value": "bankrupt", "day_offset": 0},
            {"target_type": "material", "parameter": "price_delta_pct",   "value": 20.0,       "day_offset": 5},
            {"target_type": "fx",       "parameter": "rate_delta_pct",    "value": 12.0,       "day_offset": 0},
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Simulation result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DailyKPI:
    day:                  int
    total_spend_eur:      float
    avg_unit_price_eur:   float
    avg_lead_time_days:   float
    service_level:        float
    at_risk_spend_eur:    float
    active_supplier_count: int
    stockout_materials:   int
    inventory_value_eur:  float


@dataclass
class SimulationResult:
    simulation_id: str = field(default_factory=lambda: str(uuid4()))
    scenario_id:   str = ""
    tenant_id:     str = ""
    status:        SimulationStatus = SimulationStatus.PENDING
    started_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:  datetime | None = None
    horizon_days:  int = 365
    # Time series
    daily_kpis:    list[DailyKPI] = field(default_factory=list)
    # Aggregates (vs baseline)
    baseline_total_cost:  float = 0.0
    scenario_total_cost:  float = 0.0
    cost_delta_eur:       float = 0.0
    cost_delta_pct:       float = 0.0
    max_lead_time_days:   float = 0.0
    min_service_level:    float = 1.0
    stockout_days:        int   = 0
    supplier_switches:    int   = 0
    emergency_orders:     int   = 0
    # Risk events triggered
    events_triggered:     list[dict[str, Any]] = field(default_factory=list)
    # Summary
    impact_level:         ImpactLevel = ImpactLevel.LOW
    recommendations:      list[str]   = field(default_factory=list)
    error:                str | None  = None


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MonteCarloConfig:
    n_runs:          int   = 1_000
    horizon_days:    int   = 365
    seed:            int   = 42
    parallel_workers: int  = 4
    confidence_levels: list[float] = field(default_factory=lambda: [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])


@dataclass
class Percentile:
    level:  float   # 0.05 = P5
    value:  float


@dataclass
class MonteCarloResult:
    mc_id:           str = field(default_factory=lambda: str(uuid4()))
    scenario_id:     str = ""
    n_runs:          int = 0
    horizon_days:    int = 365
    # Cost distribution
    cost_mean:       float = 0.0
    cost_std:        float = 0.0
    cost_var:        float = 0.0       # coefficient of variation
    cost_percentiles: list[Percentile] = field(default_factory=list)
    # VaR / CVaR
    var_95:          float = 0.0       # Value at Risk 95%
    cvar_95:         float = 0.0       # Conditional VaR (Expected Shortfall)
    # Lead time distribution
    lead_mean:       float = 0.0
    lead_std:        float = 0.0
    lead_p95:        float = 0.0
    # Service level
    service_mean:    float = 0.0
    service_p5:      float = 0.0       # worst 5%
    # Stockout
    stockout_prob:   float = 0.0       # P(at least one stockout)
    # Histogram buckets
    cost_histogram:  list[dict[str, float]] = field(default_factory=list)
    lead_histogram:  list[dict[str, float]] = field(default_factory=list)
    # Sensitivity (Tornado chart data)
    sensitivity:     list[dict[str, Any]]   = field(default_factory=list)
    completed_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_s:      float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Risk events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskEvent:
    event_id:      str = field(default_factory=lambda: str(uuid4()))
    event_type:    RiskEventType = RiskEventType.PRICE_SPIKE
    tenant_id:     str = ""
    triggered_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sim_day:       int = 0
    # Source
    source_id:     str = ""    # supplier_id or material_id
    source_name:   str = ""
    # Impact
    impact_level:  ImpactLevel = ImpactLevel.MEDIUM
    impact_eur:    float = 0.0
    affected_ids:  list[str] = field(default_factory=list)    # downstream
    # Parameters
    parameters:    dict[str, Any] = field(default_factory=dict)
    # Propagation
    propagated_to: list[str] = field(default_factory=list)    # cascaded event_ids
    probability:   float = 1.0    # if stochastic trigger


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API schemas
# ─────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel, Field


class ScenarioShockIn(BaseModel):
    target_type:   str
    target_id:     str = ""
    parameter:     str
    value:         Any
    day_offset:    int = 0
    duration_days: int | None = None
    ramp_days:     int = 0


class ScenarioCreateIn(BaseModel):
    name:          str
    description:   str = ""
    scenario_type: str = "custom"
    shocks:        list[ScenarioShockIn] = Field(default_factory=list)
    horizon_days:  int = Field(default=365, ge=30, le=1825)
    seed:          int = 42
    tenant_id:     str = "tenant-demo"


class MCRunIn(BaseModel):
    scenario_id:   str
    n_runs:        int = Field(default=1000, ge=100, le=10000)
    horizon_days:  int = Field(default=365, ge=30, le=1825)
    seed:          int = 42
    tenant_id:     str = "tenant-demo"


class SimulationRunIn(BaseModel):
    scenario_id:   str
    tenant_id:     str = "tenant-demo"


class SupplierProfileIn(BaseModel):
    name:           str
    country:        str
    tier:           int    = 1
    base_price:     float
    currency:       str    = "EUR"
    unit:           str    = "kg"
    lead_time_days: int    = 30
    moq:            float  = 100.0
    capacity:       float  = 10000.0
    bankruptcy_prob: float = 0.02
    on_time_pct:    float  = 0.92
    geo_risk:       float  = 0.20
    material_ids:   list[str] = Field(default_factory=list)
    tenant_id:      str    = "tenant-demo"


class KPIOut(BaseModel):
    total_spend_eur:      float
    avg_unit_price_eur:   float
    avg_lead_time_days:   float
    service_level:        float
    at_risk_spend_eur:    float
    cost_delta_pct:       float | None = None
    impact_level:         str | None   = None


class SimResultOut(BaseModel):
    simulation_id:       str
    scenario_id:         str
    status:              str
    cost_delta_eur:      float
    cost_delta_pct:      float
    max_lead_time_days:  float
    min_service_level:   float
    stockout_days:       int
    impact_level:        str
    recommendations:     list[str]
    events_triggered:    int


class MCResultOut(BaseModel):
    mc_id:           str
    n_runs:          int
    cost_mean:       float
    cost_std:        float
    var_95:          float
    cvar_95:         float
    service_p5:      float
    stockout_prob:   float
    sensitivity:     list[dict[str, Any]]
    cost_histogram:  list[dict[str, float]]
