"""
World Model — Data Models

Model świata wpływającego na koszty produkcji.
Integruje: inflację, FX, energię, geopolitykę, logistykę, surowce.
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

class SignalType(str, Enum):
    INFLATION         = "inflation"
    FX_RATE           = "fx_rate"
    ENERGY_PRICE      = "energy_price"
    COMMODITY_PRICE   = "commodity_price"
    LOGISTICS_COST    = "logistics_cost"
    GEOPOLITICAL_RISK = "geopolitical_risk"
    LABOR_COST        = "labor_cost"
    INTEREST_RATE     = "interest_rate"
    DEMAND_INDEX      = "demand_index"
    SUPPLY_DISRUPTION = "supply_disruption"

class CausalRelationType(str, Enum):
    DIRECT_POSITIVE    = "direct_positive"    # A↑ → B↑
    DIRECT_NEGATIVE    = "direct_negative"    # A↑ → B↓
    AMPLIFYING         = "amplifying"         # A amplifies effect of B on C
    DAMPENING          = "dampening"
    LAGGED             = "lagged"             # delayed effect
    THRESHOLD          = "threshold"          # effect only above/below threshold
    NONLINEAR          = "nonlinear"          # logarithmic / exponential

class ForecastHorizon(str, Enum):
    M1  = "1m"
    M3  = "3m"
    M6  = "6m"
    M12 = "12m"

class ScenarioType(str, Enum):
    BASELINE   = "baseline"   # most likely path
    OPTIMISTIC = "optimistic" # bull case
    PESSIMISTIC = "pessimistic" # bear case
    SHOCK_WAR  = "shock_war"  # geopolitical shock
    SHOCK_ENERGY = "shock_energy"  # energy crisis
    SHOCK_RECESSION = "shock_recession"
    STAGFLATION = "stagflation"
    CUSTOM     = "custom"

class ForecastMethod(str, Enum):
    GBM        = "gbm"         # Geometric Brownian Motion
    ARIMA      = "arima"       # ARIMA (simulated)
    MEAN_REVERT = "mean_revert" # Ornstein-Uhlenbeck
    TREND_PROJ = "trend_proj"  # linear trend projection
    ENSEMBLE   = "ensemble"    # weighted blend

class NodeEntity(str, Enum):
    COUNTRY    = "country"
    COMMODITY  = "commodity"
    SUPPLIER   = "supplier"
    REGULATION = "regulation"
    EVENT      = "event"
    MARKET     = "market"
    PROCESS    = "process"
    COST_COMPONENT = "cost_component"


# ─────────────────────────────────────────────────────────────────────────────
# World signals — current state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EconomicSignal:
    """One observable economic indicator."""
    signal_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_type:  SignalType = SignalType.INFLATION
    name:         str = ""
    region:       str = "EUR"       # ISO region / currency area
    current_value: float = 0.0
    unit:         str = "%"
    yoy_change:   float = 0.0       # year-over-year %
    mom_change:   float = 0.0       # month-over-month %
    trend:        str = "stable"    # "rising" | "falling" | "stable" | "volatile"
    volatility:   float = 0.05      # annualized
    data_quality: float = 1.0       # 0–1
    source:       str = ""
    timestamp:    float = 0.0


@dataclass
class InflationState:
    cpi_yoy:       float = 3.0    # Consumer Price Index YoY %
    ppi_yoy:       float = 4.0    # Producer Price Index YoY %
    core_yoy:      float = 2.5    # Core inflation (ex energy/food) %
    wage_growth:   float = 3.5    # nominal wage growth %
    real_rate:     float = 0.0    # real interest rate %
    region:        str   = "EUR"


@dataclass
class FXState:
    eur_usd:  float = 1.08
    eur_cny:  float = 7.80
    eur_pln:  float = 4.25
    eur_gbp:  float = 0.86
    eur_jpy:  float = 160.0
    eur_inr:  float = 91.0
    eur_mxn:  float = 18.5
    # Trends: "strengthening" | "weakening" | "stable"
    usd_trend: str = "stable"
    cny_trend: str = "stable"


@dataclass
class EnergyState:
    electricity_eur_mwh:  float = 85.0    # European spot
    natural_gas_eur_mwh:  float = 35.0    # TTF
    oil_usd_bbl:          float = 82.0    # Brent
    oil_eur_bbl:          float = 75.9    # Brent in EUR
    coal_usd_t:           float = 130.0
    carbon_eur_t:         float = 65.0    # EU ETS
    # Supply security
    gas_storage_pct:      float = 75.0    # EU storage level %
    renewable_share:      float = 0.42    # share of renewables in grid


@dataclass
class CommodityState:
    steel_hrc_eur_t:      float = 620.0   # Hot Rolled Coil
    steel_scrap_eur_t:    float = 340.0
    aluminum_eur_t:       float = 2300.0
    copper_eur_t:         float = 8800.0
    nickel_eur_t:         float = 17000.0
    plastic_pe_eur_t:     float = 1100.0
    plastic_pp_eur_t:     float = 1050.0
    lithium_usd_t:        float = 18000.0
    rare_earth_index:     float = 100.0   # custom index


@dataclass
class LogisticsState:
    shanghai_rotterdam_usd: float = 1800.0   # 40ft container $/FEU
    germany_poland_eur_t:   float = 45.0     # road freight €/t
    airfreight_eur_kg:      float = 4.5
    port_congestion_days:   float = 2.0      # avg extra dwell time
    truck_driver_shortage:  float = 0.15     # 0–1 scarcity index
    rail_capacity_pct:      float = 0.78     # utilization
    lead_time_index:        float = 1.0      # 1.0 = normal, >1 = delayed


@dataclass
class GeopoliticalState:
    conflict_risk_eu:     float = 0.15   # 0–1
    conflict_risk_asia:   float = 0.20
    conflict_risk_middle_east: float = 0.30
    trade_restriction_cn: float = 0.25   # tariff/restriction level
    trade_restriction_ru: float = 0.85
    supply_chain_stress:  float = 0.25   # composite 0–1
    sanctions_impact:     float = 0.10   # 0–1
    election_uncertainty: float = 0.15   # political risk


@dataclass
class WorldState:
    """Complete snapshot of all world signals at a point in time."""
    state_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:   float = 0.0
    period_label: str = "now"   # "now" | "2024-Q1" | etc
    inflation:   InflationState = field(default_factory=InflationState)
    fx:          FXState = field(default_factory=FXState)
    energy:      EnergyState = field(default_factory=EnergyState)
    commodities: CommodityState = field(default_factory=CommodityState)
    logistics:   LogisticsState = field(default_factory=LogisticsState)
    geopolitics: GeopoliticalState = field(default_factory=GeopoliticalState)
    # Derived
    signals:     list[EconomicSignal] = field(default_factory=list)
    metadata:    dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Causal Graph
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CausalNode:
    node_id:     str
    name:        str
    signal_type: SignalType
    region:      str = "global"
    current_val: float = 0.0
    unit:        str = ""
    # Structural parameters
    mean_reversion_level: float | None = None
    volatility:  float = 0.05
    drift:       float = 0.0
    metadata:    dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalEdge:
    from_id:      str
    to_id:        str
    relation:     CausalRelationType
    strength:     float = 0.5       # 0–1 elasticity / coefficient
    lag_months:   int   = 0         # months until effect materializes
    threshold:    float | None = None  # for THRESHOLD relations
    description:  str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Graph
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KGNode:
    node_id:    str
    entity:     NodeEntity
    label:      str
    properties: dict[str, Any] = field(default_factory=dict)
    embedding:  list[float] = field(default_factory=list)


@dataclass
class KGRelation:
    rel_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    from_id:    str = ""
    to_id:      str = ""
    rel_type:   str = ""
    weight:     float = 1.0
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Forecast
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalForecast:
    signal_id:   str
    signal_name: str
    horizon:     ForecastHorizon
    method:      ForecastMethod
    current:     float
    forecast:    float
    change_pct:  float
    ci_lower:    float   # 90% confidence interval
    ci_upper:    float
    confidence:  float   # 0–1 model confidence
    path:        list[float] = field(default_factory=list)  # monthly values


@dataclass
class CostImpact:
    """How a signal change propagates to production cost."""
    signal_name:     str
    current_value:   float
    forecast_value:  float
    cost_delta_pct:  float   # % change in production cost
    cost_delta_eur:  float   # absolute EUR/unit change
    elasticity:      float   # cost elasticity to this signal
    confidence:      float


@dataclass
class HorizonForecast:
    """Complete forecast for one horizon (1m/3m/6m/12m)."""
    horizon:         ForecastHorizon
    months_ahead:    int
    world_state:     WorldState    # forecasted world state
    signal_forecasts: list[SignalForecast]
    cost_impacts:    list[CostImpact]
    total_cost_delta_pct: float    # aggregate cost change
    total_cost_delta_eur: float    # if base cost known
    confidence:      float
    dominant_driver: str           # biggest cost driver


@dataclass
class ProductionCostForecast:
    """Full forecast across all horizons for a product."""
    forecast_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id:    str = ""
    base_cost_eur: float = 0.0     # current unit cost
    country:       str = "DE"
    horizons:      dict[str, HorizonForecast] = field(default_factory=dict)  # "1m" → HorizonForecast
    scenario:      ScenarioType = ScenarioType.BASELINE
    run_time_ms:   float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MonteCarloPath:
    path_id:       int
    scenario_type: ScenarioType
    # Monthly values for 12 months
    cost_path:     list[float] = field(default_factory=list)
    fx_path:       list[float] = field(default_factory=list)
    energy_path:   list[float] = field(default_factory=list)
    commodity_path: list[float] = field(default_factory=list)


@dataclass
class SimulationResult:
    n_paths:         int
    base_cost:       float
    # Distribution at each horizon
    dist_1m:         dict[str, float] = field(default_factory=dict)  # p5/p25/p50/p75/p95
    dist_3m:         dict[str, float] = field(default_factory=dict)
    dist_6m:         dict[str, float] = field(default_factory=dict)
    dist_12m:        dict[str, float] = field(default_factory=dict)
    var_95_12m:      float = 0.0     # Value at Risk 95% at 12m
    cvar_95_12m:     float = 0.0     # Conditional VaR
    probability_above_threshold: dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioDefinition:
    scenario_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    scenario_type: ScenarioType = ScenarioType.BASELINE
    name:          str = ""
    description:   str = ""
    probability:   float = 0.4   # subjective probability 0–1
    # Shock parameters (multipliers or absolute changes to WorldState fields)
    shocks:        dict[str, float] = field(default_factory=dict)
    # e.g. {"energy.electricity_eur_mwh": 2.0,  # 2x energy price
    #        "geopolitics.conflict_risk_eu": 0.6}


@dataclass
class ScenarioResult:
    scenario:          ScenarioDefinition
    forecast:          ProductionCostForecast
    cost_at_1m:        float
    cost_at_3m:        float
    cost_at_6m:        float
    cost_at_12m:       float
    max_cost_delta_pct: float
    risk_flags:        list[str] = field(default_factory=list)
    recommendations:   list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Optimization result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SourcingAction:
    action_type:   str   # "hedge_fx", "buy_forward", "switch_supplier", "stockpile", "lock_energy"
    target:        str   # what to act on
    description:   str
    cost_saving_pct: float
    risk_reduction:  float
    implementation_cost_eur: float
    urgency:       str = "medium"  # low/medium/high/critical
    deadline_days: int = 90


@dataclass
class OptimizationResult:
    baseline_cost_12m_eur: float
    optimized_cost_12m_eur: float
    saving_eur:            float
    saving_pct:            float
    actions:               list[SourcingAction]
    scenario_robust:       bool    # is this plan robust across scenarios?
    confidence:            float


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API models
# ─────────────────────────────────────────────────────────────────────────────

class WorldStateIn(BaseModel):
    # Inflation overrides
    cpi_yoy:            float | None = None
    ppi_yoy:            float | None = None
    # FX overrides
    eur_usd:            float | None = None
    eur_cny:            float | None = None
    eur_pln:            float | None = None
    # Energy overrides
    electricity_eur_mwh: float | None = None
    natural_gas_eur_mwh: float | None = None
    oil_usd_bbl:        float | None = None
    # Commodities
    steel_hrc_eur_t:    float | None = None
    aluminum_eur_t:     float | None = None
    copper_eur_t:       float | None = None
    # Logistics
    shanghai_rotterdam_usd: float | None = None
    lead_time_index:    float | None = None
    # Geopolitics
    supply_chain_stress: float | None = None
    conflict_risk_eu:   float | None = None


class ForecastRequestIn(BaseModel):
    product_id:    str
    base_cost_eur: float = Field(10.0, gt=0)
    country:       str = "DE"
    material_pct:  float = 0.40   # material share of total cost
    labor_pct:     float = 0.25
    energy_pct:    float = 0.15
    logistics_pct: float = 0.10
    overhead_pct:  float = 0.10
    # Material mix (share of each commodity in material cost)
    steel_share:   float = 0.50
    aluminum_share: float = 0.20
    copper_share:  float = 0.10
    plastic_share: float = 0.20
    # FX exposure
    usd_exposure:  float = 0.30   # fraction of costs in USD
    cny_exposure:  float = 0.20
    # Optional world state overrides
    world_state:   WorldStateIn | None = None
    scenarios:     list[ScenarioType] = Field(default_factory=lambda: [ScenarioType.BASELINE])
    n_mc_paths:    int = Field(500, ge=100, le=10000)
    method:        ForecastMethod = ForecastMethod.ENSEMBLE


class ScenarioRequestIn(BaseModel):
    product_id:    str
    base_cost_eur: float = 10.0
    country:       str = "DE"
    material_pct:  float = 0.40
    labor_pct:     float = 0.25
    energy_pct:    float = 0.15
    logistics_pct: float = 0.10
    scenario_types: list[ScenarioType] = Field(
        default_factory=lambda: [
            ScenarioType.BASELINE, ScenarioType.OPTIMISTIC,
            ScenarioType.PESSIMISTIC, ScenarioType.SHOCK_ENERGY,
            ScenarioType.SHOCK_WAR,
        ]
    )
