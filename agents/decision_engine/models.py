"""
Decision Intelligence Engine — Data Models

Encje:
  DecisionContext   — pełny kontekst wejściowy (koszt, produkcja, dostawcy, rynek)
  DecisionNode      — węzeł grafu decyzyjnego (warunek, akcja, brama)
  Recommendation    — rekomendacja z wyjaśnieniem i ufnością
  Evidence          — dowód wspierający rekomendację (dane źródłowe)
  ConfidenceScore   — wielowymiarowa ocena ufności
  Explanation       — warstwowe wyjaśnienie (summary → factors → counterfactual)
  OptimizationResult— wynik optymalizacji multi-kryterialnej
  DecisionOutcome   — rejestr podjętej decyzji + wynik (feedback loop)
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

class RecommendationType(str, Enum):
    CHANGE_SUPPLIER    = "change_supplier"
    INCREASE_MOQ       = "increase_moq"
    DECREASE_MOQ       = "decrease_moq"
    CHANGE_MATERIAL    = "change_material"
    CHANGE_PROCESS     = "change_process"
    BUY_NOW            = "buy_now"
    WAIT               = "wait"
    HEDGE_FX           = "hedge_fx"
    DUAL_SOURCE        = "dual_source"
    RENEGOTIATE        = "renegotiate"
    INCREASE_STOCK     = "increase_stock"
    REDUCE_STOCK       = "reduce_stock"
    QUALIFY_ALTERNATIVE= "qualify_alternative"
    ESCALATE           = "escalate"        # human review required
    NO_ACTION          = "no_action"


class UrgencyLevel(str, Enum):
    CRITICAL  = "critical"    # act within 24h
    HIGH      = "high"        # act within 1 week
    MEDIUM    = "medium"      # act within 1 month
    LOW       = "low"         # act within quarter
    WATCH     = "watch"       # monitor only


class ImpactDimension(str, Enum):
    COST         = "cost"
    QUALITY      = "quality"
    DELIVERY     = "delivery"
    RISK         = "risk"
    SUSTAINABILITY = "sustainability"
    CASH_FLOW    = "cash_flow"
    FLEXIBILITY  = "flexibility"


class NodeType(str, Enum):
    CONDITION   = "condition"    # boolean check
    THRESHOLD   = "threshold"    # numeric comparison
    AGGREGATION = "aggregation"  # combine multiple signals
    ACTION      = "action"       # leaf → recommendation
    GATE_AND    = "gate_and"     # all children must be true
    GATE_OR     = "gate_or"      # any child sufficient
    PROBABILITY = "probability"  # probabilistic branching
    LOOKUP      = "lookup"       # table / ML model lookup


class EvidenceType(str, Enum):
    COST_DELTA        = "cost_delta"
    PRICE_TREND       = "price_trend"
    SUPPLIER_RISK     = "supplier_risk"
    MARKET_SIGNAL     = "market_signal"
    INVENTORY_LEVEL   = "inventory_level"
    LEAD_TIME_CHANGE  = "lead_time_change"
    QUALITY_ISSUE     = "quality_issue"
    FX_MOVEMENT       = "fx_movement"
    DEMAND_FORECAST   = "demand_forecast"
    BENCHMARK         = "benchmark"
    HISTORICAL_PATTERN = "historical_pattern"
    RULE_TRIGGER      = "rule_trigger"


class ConfidenceLevel(str, Enum):
    VERY_HIGH = "very_high"   # > 90%
    HIGH      = "high"        # 75–90%
    MEDIUM    = "medium"      # 55–75%
    LOW       = "low"         # 35–55%
    VERY_LOW  = "very_low"    # < 35%


class DataQuality(str, Enum):
    VERIFIED   = "verified"
    ESTIMATED  = "estimated"
    STALE      = "stale"
    MISSING    = "missing"
    CONFLICTING= "conflicting"


# ─────────────────────────────────────────────────────────────────────────────
# Input context
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostContext:
    """Cost data from cost_twin or direct input."""
    material_id:        str
    current_unit_cost:  float          # EUR per unit
    target_unit_cost:   float | None   # budget / target
    cost_trend_pct:     float = 0.0    # % change YoY
    material_pct:       float = 0.0    # material as % of total cost
    labor_pct:          float = 0.0
    logistics_pct:      float = 0.0
    overhead_pct:       float = 0.0
    benchmark_cost:     float | None = None   # market benchmark
    currency:           str = "EUR"
    data_quality:       DataQuality = DataQuality.VERIFIED


@dataclass
class SupplierContext:
    """Supplier data from procurement_twin or direct input."""
    supplier_id:         str
    name:                str
    current_price_eur:   float
    lead_time_days:      float
    otd_pct:             float = 95.0   # On-Time Delivery %
    defect_rate_pct:     float = 0.5
    financial_risk:      float = 0.2    # 0–1
    geo_risk:            float = 0.2    # 0–1
    single_source:       bool  = False
    moq:                 int   = 1000
    annual_volume:       int   = 10000
    price_delta_pct:     float = 0.0    # recent price change
    contract_expiry_days: int  = 365
    alternative_count:   int   = 0
    certification:       list[str] = field(default_factory=list)
    data_quality:        DataQuality = DataQuality.VERIFIED


@dataclass
class MarketContext:
    """Market intelligence data."""
    material_id:         str
    spot_price_eur:      float
    futures_price_eur:   float | None = None   # 3-month forward
    price_volatility_30d: float = 0.05
    trend_direction:     str = "stable"        # "rising" | "falling" | "stable"
    trend_strength:      float = 0.0           # 0–1
    seasonal_factor:     float = 1.0           # current season multiplier
    supply_disruption_risk: float = 0.1        # 0–1
    demand_index:        float = 1.0           # relative demand
    alternative_materials: list[str] = field(default_factory=list)
    fx_eur_usd:          float = 1.08
    fx_trend:            str = "stable"
    data_quality:        DataQuality = DataQuality.ESTIMATED


@dataclass
class ProductionContext:
    """Production data from factory_twin or direct input."""
    product_id:          str
    annual_volume:       int
    current_inventory_days: float = 30.0   # days of cover
    safety_stock_days:   float = 14.0
    reorder_point_days:  float = 7.0
    production_rate_pct: float = 85.0      # OEE / utilization
    bottleneck_machine:  str   = ""
    scrap_rate_pct:      float = 0.5
    demand_forecast_units: int = 0
    demand_confidence:   float = 0.8
    data_quality:        DataQuality = DataQuality.VERIFIED


@dataclass
class DecisionContext:
    """Complete decision context — all signals for one decision request."""
    context_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id:    str = "default"
    created_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Data domains
    cost:         CostContext | None = None
    supplier:     SupplierContext | None = None
    market:       MarketContext | None = None
    production:   ProductionContext | None = None
    # Preferences
    risk_appetite:   float = 0.5    # 0=risk averse, 1=risk seeking
    cost_weight:     float = 0.35
    risk_weight:     float = 0.30
    quality_weight:  float = 0.20
    delivery_weight: float = 0.15
    # Optional overrides
    custom_signals:  dict[str, Any] = field(default_factory=dict)
    tags:            list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Evidence:
    evidence_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    evidence_type: EvidenceType = EvidenceType.RULE_TRIGGER
    description:   str = ""
    value:         Any = None
    unit:          str = ""
    threshold:     Any = None
    direction:     str = "above"   # "above" | "below" | "equal"
    weight:        float = 1.0     # importance weight
    source:        str = ""        # "cost_twin" | "market" | "supplier" | "rule"
    quality:       DataQuality = DataQuality.VERIFIED
    timestamp:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_sentence(self) -> str:
        d = ">" if self.direction == "above" else "<" if self.direction == "below" else "="
        val_str = f"{self.value:.2f}{self.unit}" if isinstance(self.value, (int, float)) else str(self.value)
        thr_str = f"{self.threshold:.2f}{self.unit}" if isinstance(self.threshold, (int, float)) else str(self.threshold)
        return f"{self.description}: {val_str} {d} {thr_str}"


# ─────────────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConfidenceScore:
    overall:       float = 0.0      # 0–1
    level:         ConfidenceLevel = ConfidenceLevel.LOW
    # Dimensional breakdown
    data_quality:  float = 0.0      # input data completeness / freshness
    signal_strength: float = 0.0   # how strong / clear the signals are
    consensus:     float = 0.0      # agreement across multiple signals
    historical_accuracy: float = 0.0  # historical hit rate for this pattern
    # Metadata
    n_signals:     int   = 0        # number of supporting signals
    n_conflicts:   int   = 0        # conflicting signals
    missing_data:  list[str] = field(default_factory=list)  # data gaps

    def compute(self) -> None:
        raw = (self.data_quality * 0.25 + self.signal_strength * 0.35
               + self.consensus * 0.25 + self.historical_accuracy * 0.15)
        # Penalty for missing data
        penalty = len(self.missing_data) * 0.05
        self.overall = max(0.0, min(1.0, raw - penalty))
        if self.overall >= 0.90:
            self.level = ConfidenceLevel.VERY_HIGH
        elif self.overall >= 0.75:
            self.level = ConfidenceLevel.HIGH
        elif self.overall >= 0.55:
            self.level = ConfidenceLevel.MEDIUM
        elif self.overall >= 0.35:
            self.level = ConfidenceLevel.LOW
        else:
            self.level = ConfidenceLevel.VERY_LOW


# ─────────────────────────────────────────────────────────────────────────────
# Explanation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImpactEstimate:
    dimension:     ImpactDimension
    delta_eur:     float = 0.0
    delta_pct:     float = 0.0
    description:   str   = ""
    uncertainty:   float = 0.1   # ± fraction


@dataclass
class CounterfactualCase:
    """What would happen if we did NOT follow this recommendation."""
    scenario:          str
    cost_delta_eur:    float = 0.0
    risk_delta:        float = 0.0
    probability:       float = 0.5
    time_horizon_days: int   = 90


@dataclass
class Explanation:
    summary:          str                        # 1–2 sentence business summary
    rationale:        list[str]                  # bullet points (why)
    evidence:         list[Evidence]             # supporting data points
    impacts:          list[ImpactEstimate]       # quantified impact per dimension
    counterfactuals:  list[CounterfactualCase]   # "if we wait / do nothing"
    assumptions:      list[str]                  # model assumptions
    caveats:          list[str]                  # known limitations
    similar_cases:    list[str] = field(default_factory=list)  # historical precedents


# ─────────────────────────────────────────────────────────────────────────────
# Recommendation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ActionParameter:
    key:   str
    value: Any
    unit:  str = ""
    description: str = ""


@dataclass
class Recommendation:
    rec_id:      str = field(default_factory=lambda: str(uuid.uuid4()))
    context_id:  str = ""
    tenant_id:   str = "default"
    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Core
    rec_type:    RecommendationType = RecommendationType.NO_ACTION
    title:       str = ""
    urgency:     UrgencyLevel = UrgencyLevel.LOW
    confidence:  ConfidenceScore = field(default_factory=ConfidenceScore)
    explanation: Explanation | None = None

    # Action details
    parameters:  list[ActionParameter] = field(default_factory=list)
    target_id:   str = ""    # supplier_id / material_id / process_id

    # Financial
    expected_saving_eur:   float = 0.0
    expected_saving_pct:   float = 0.0
    implementation_cost_eur: float = 0.0
    payback_months:        float = 0.0
    roi_pct:               float = 0.0

    # Risk
    risk_level:   str = "medium"    # low / medium / high
    risk_factors: list[str] = field(default_factory=list)

    # Meta
    score:       float = 0.0    # composite ranking score
    rank:        int   = 1
    tags:        list[str] = field(default_factory=list)

    def compute_roi(self) -> None:
        if self.implementation_cost_eur > 0 and self.expected_saving_eur > 0:
            self.roi_pct = (self.expected_saving_eur - self.implementation_cost_eur) / self.implementation_cost_eur * 100
            if self.expected_saving_eur > 0:
                self.payback_months = self.implementation_cost_eur / (self.expected_saving_eur / 12)


# ─────────────────────────────────────────────────────────────────────────────
# Decision Graph Node
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DecisionNode:
    node_id:    str
    node_type:  NodeType
    label:      str
    # Condition / threshold
    signal_key: str   = ""       # which signal from context to read
    operator:   str   = ">"      # ">", "<", ">=", "<=", "==", "!=", "in"
    threshold:  Any   = None
    # Action (leaf)
    rec_type:   RecommendationType | None = None
    urgency:    UrgencyLevel = UrgencyLevel.LOW
    base_score: float = 0.5
    # Graph
    children_ids: list[str] = field(default_factory=list)
    parent_ids:   list[str] = field(default_factory=list)
    # Weights for aggregation / probability nodes
    weights:    dict[str, float] = field(default_factory=dict)
    metadata:   dict[str, Any]   = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Optimization
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptimizationConstraint:
    dimension:  ImpactDimension
    operator:   str    # ">=" | "<=" | "==" | "range"
    value:      float
    value_max:  float | None = None  # for range
    hard:       bool = False         # hard constraint = must satisfy


@dataclass
class OptimizationObjective:
    dimensions:  list[ImpactDimension]
    weights:     list[float]          # sum = 1
    maximize:    bool = True          # True = maximize score


@dataclass
class MultiObjectiveResult:
    pareto_front:     list[Recommendation]
    weights_used:     dict[str, float]
    best_balanced:    Recommendation | None
    best_cost:        Recommendation | None
    best_risk:        Recommendation | None
    constraint_violations: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# Feedback & outcome tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DecisionOutcome:
    """Records what actually happened after acting on a recommendation."""
    outcome_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    rec_id:         str = ""
    context_id:     str = ""
    tenant_id:      str = "default"
    # Actual vs. predicted
    actual_saving_eur:     float = 0.0
    predicted_saving_eur:  float = 0.0
    actual_risk_delta:     float = 0.0
    was_correct:           bool  = True
    feedback_notes:        str   = ""
    recorded_at:           datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API models
# ─────────────────────────────────────────────────────────────────────────────

class CostContextIn(BaseModel):
    material_id:         str
    current_unit_cost:   float
    target_unit_cost:    float | None = None
    cost_trend_pct:      float = 0.0
    material_pct:        float = 0.0
    benchmark_cost:      float | None = None
    currency:            str = "EUR"


class SupplierContextIn(BaseModel):
    supplier_id:          str
    name:                 str = ""
    current_price_eur:    float
    lead_time_days:       float = 14.0
    otd_pct:              float = 95.0
    defect_rate_pct:      float = 0.5
    financial_risk:       float = 0.2
    geo_risk:             float = 0.2
    single_source:        bool  = False
    moq:                  int   = 1000
    annual_volume:        int   = 10000
    price_delta_pct:      float = 0.0
    contract_expiry_days: int   = 365
    alternative_count:    int   = 0


class MarketContextIn(BaseModel):
    material_id:              str
    spot_price_eur:           float
    futures_price_eur:        float | None = None
    price_volatility_30d:     float = 0.05
    trend_direction:          str = "stable"
    trend_strength:           float = 0.0
    supply_disruption_risk:   float = 0.1
    demand_index:             float = 1.0
    alternative_materials:    list[str] = Field(default_factory=list)
    fx_eur_usd:               float = 1.08
    fx_trend:                 str = "stable"


class ProductionContextIn(BaseModel):
    product_id:               str
    annual_volume:            int
    current_inventory_days:   float = 30.0
    safety_stock_days:        float = 14.0
    production_rate_pct:      float = 85.0
    scrap_rate_pct:           float = 0.5
    demand_forecast_units:    int   = 0
    demand_confidence:        float = 0.8


class DecisionRequestIn(BaseModel):
    tenant_id:    str = "default"
    cost:         CostContextIn | None = None
    supplier:     SupplierContextIn | None = None
    market:       MarketContextIn | None = None
    production:   ProductionContextIn | None = None
    risk_appetite: float = 0.5
    cost_weight:   float = 0.35
    risk_weight:   float = 0.30
    quality_weight: float = 0.20
    delivery_weight: float = 0.15
    tags:          list[str] = Field(default_factory=list)


class OutcomeFeedbackIn(BaseModel):
    rec_id:             str
    actual_saving_eur:  float
    was_correct:        bool = True
    feedback_notes:     str = ""


class OptimizeRequestIn(BaseModel):
    context_id:   str
    dimensions:   list[ImpactDimension] = Field(default_factory=lambda: [ImpactDimension.COST, ImpactDimension.RISK])
    weights:      list[float] = Field(default_factory=lambda: [0.6, 0.4])
    constraints:  list[dict[str, Any]] = Field(default_factory=list)
