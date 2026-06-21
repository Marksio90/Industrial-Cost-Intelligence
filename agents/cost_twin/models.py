"""
Cost Digital Twin — Data Models

Encje:
  CostNode         — węzeł grafu kosztu (materiał, praca, energia, maszyna, podatek, logistyka)
  CostEdge         — relacja przyczynowa (A drives B, mnożnik wpływu)
  CostDriver       — parametryczny sterownik kosztu (cena, stawka, kurs, wskaźnik)
  ProductCostModel — pełny model kosztowy produktu (BOM + routing + overhead)
  CostScenario     — scenariusz "co jeśli" (zmiana materiału / dostawcy / technologii / kraju)
  CostResult       — wynik symulacji kosztowej
  OptimizationTask — zadanie optymalizacji (minimalizuj koszt przy ograniczeniach)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class CostNodeType(str, Enum):
    MATERIAL        = "material"        # surowiec, komponent
    LABOR           = "labor"           # praca bezpośrednia i pośrednia
    ENERGY          = "energy"          # elektryczność, gaz, para
    MACHINE         = "machine"         # amortyzacja, utrzymanie ruchu
    TOOLING         = "tooling"         # oprzyrządowanie, formy wtryskowe
    LOGISTICS       = "logistics"       # transport, magazyn, opakowanie
    OVERHEAD        = "overhead"        # koszty ogólnozakładowe
    QUALITY         = "quality"         # kontrola jakości, brakowość
    TAX_DUTY        = "tax_duty"        # cło, podatki eksportowe, VAT
    FINANCE         = "finance"         # koszt kapitału, hedging FX
    WASTE           = "waste"           # odpady, złom, recykling
    CERTIFICATION   = "certification"   # normy, atesty, audyty
    MARGIN          = "margin"          # marża dostawcy / producenta


class CostEdgeType(str, Enum):
    DRIVES          = "drives"          # A bezpośrednio napędza B
    SCALES_WITH     = "scales_with"     # A skaluje B proporcjonalnie
    CONSTRAINS      = "constrains"      # A ogranicza B (górna/dolna granica)
    SUBSTITUTES     = "substitutes"     # A może zastąpić B
    AMPLIFIES       = "amplifies"       # A wzmacnia wpływ B (nieliniowy)


class ScenarioType(str, Enum):
    MATERIAL_CHANGE   = "material_change"
    SUPPLIER_CHANGE   = "supplier_change"
    TECHNOLOGY_CHANGE = "technology_change"
    COUNTRY_CHANGE    = "country_change"
    VOLUME_CHANGE     = "volume_change"
    COMPOUND          = "compound"
    OPTIMIZATION      = "optimization"


class CostCategory(str, Enum):
    DIRECT_MATERIAL   = "direct_material"
    DIRECT_LABOR      = "direct_labor"
    MANUFACTURING_OH  = "manufacturing_overhead"
    LOGISTICS         = "logistics"
    QUALITY           = "quality"
    TAX_COMPLIANCE    = "tax_compliance"
    FINANCE           = "finance"
    MARGIN            = "margin"


class OptimizationObjective(str, Enum):
    MIN_TOTAL_COST    = "min_total_cost"
    MIN_MATERIAL_COST = "min_material_cost"
    MIN_LOGISTICS     = "min_logistics"
    MAX_MARGIN        = "max_margin"
    BALANCED          = "balanced"       # Pareto front cost vs risk vs lead-time


class CountryRiskLevel(str, Enum):
    VERY_LOW  = "very_low"
    LOW       = "low"
    MEDIUM    = "medium"
    HIGH      = "high"
    VERY_HIGH = "very_high"


# ─────────────────────────────────────────────────────────────────────────────
# Core cost structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostDriver:
    """Parametryczny sterownik kosztu — wejście do grafu."""
    driver_id:    str = field(default_factory=lambda: str(uuid4()))
    name:         str = ""
    value:        float = 0.0
    unit:         str = "EUR"
    # Uncertainty
    uncertainty:  float = 0.0      # ± relative % (e.g. 0.05 = ±5%)
    distribution: str = "normal"   # normal | lognormal | uniform
    # Time dependency
    trend_pct_pa: float = 0.0      # expected annual drift %
    # Bounds
    min_value:    float | None = None
    max_value:    float | None = None
    # Meta
    source:       str = ""         # "LME" | "manual" | "ERP" | "quote"
    valid_until:  datetime | None = None
    tags:         list[str] = field(default_factory=list)


@dataclass
class CostNode:
    """Węzeł w grafie kosztu."""
    node_id:      str = field(default_factory=lambda: str(uuid4()))
    name:         str = ""
    node_type:    CostNodeType = CostNodeType.MATERIAL
    category:     CostCategory = CostCategory.DIRECT_MATERIAL
    # Base cost
    base_cost:    float = 0.0      # EUR per unit
    unit:         str   = "EUR/kg"
    quantity:     float = 1.0      # quantity per product unit
    quantity_unit: str  = "kg"
    # Cost formula
    formula:      str = "base_cost * quantity"   # Python-eval-able expression
    driver_refs:  list[str] = field(default_factory=list)   # driver_ids used in formula
    # Flags
    is_fixed:     bool = False     # fixed regardless of volume
    is_variable:  bool = True
    scalability:  float = 1.0      # 1.0 = linear; <1 = economies of scale
    # Waste / scrap
    scrap_rate:   float = 0.0      # fraction lost as waste
    # Country-specific
    country_code: str = ""
    # Meta
    tenant_id:    str = "tenant-demo"
    tags:         list[str] = field(default_factory=list)


@dataclass
class CostEdge:
    """Relacja przyczynowa między węzłami."""
    edge_id:      str = field(default_factory=lambda: str(uuid4()))
    source_id:    str = ""
    target_id:    str = ""
    edge_type:    CostEdgeType = CostEdgeType.DRIVES
    weight:       float = 1.0      # strength of influence 0–1
    multiplier:   float = 1.0      # cost multiplier when edge fires
    elasticity:   float = 1.0      # % change in target per 1% change in source
    condition:    str = ""         # optional: "volume > 1000"
    notes:        str = ""


@dataclass
class BomLine:
    """Jedna linia BOM (materiał + ilość + dostawca)."""
    material_id:    str
    material_name:  str
    quantity:       float
    unit:           str
    unit_price:     float          # EUR
    currency:       str = "EUR"
    supplier_id:    str = ""
    scrap_rate:     float = 0.0
    substitutes:    list[str] = field(default_factory=list)   # alt material_ids


@dataclass
class RoutingStep:
    """Jedna operacja w routingu technologicznym."""
    step_id:        str = field(default_factory=lambda: str(uuid4()))
    sequence:       int = 0
    operation_name: str = ""
    process_type:   str = ""       # turning | welding | injection | stamping …
    work_center:    str = ""
    machine_id:     str = ""
    cycle_time_s:   float = 0.0
    setup_time_min: float = 0.0
    labor_rate:     float = 0.0    # EUR/hour
    machine_rate:   float = 0.0    # EUR/hour (amortisation + maintenance + energy)
    yield_pct:      float = 1.0    # fraction of good parts
    country_code:   str = ""


@dataclass
class CountryProfile:
    """Profil kosztowy kraju produkcji."""
    country_code:     str = ""
    country_name:     str = ""
    currency:         str = "EUR"
    fx_to_eur:        float = 1.0
    fx_volatility:    float = 0.05
    # Labor
    avg_wage_eur_h:   float = 0.0      # average manufacturing wage EUR/h
    social_charge_pct: float = 0.0     # employer social charges %
    # Energy
    electricity_eur_kwh: float = 0.0
    gas_eur_mwh:         float = 0.0
    # Taxes
    corporate_tax_pct:   float = 0.0
    import_duty_pct:     float = 0.0   # duty on imported components
    export_duty_pct:     float = 0.0
    vat_pct:             float = 0.0
    # Logistics
    avg_inland_transport_eur_t: float = 0.0
    port_handling_eur_t:        float = 0.0
    customs_clearance_eur:      float = 0.0
    # Regulatory
    reach_applicable:    bool = True
    rohs_applicable:     bool = True
    co2_tax_eur_t:       float = 0.0   # carbon price
    # Risk
    risk_level:           CountryRiskLevel = CountryRiskLevel.MEDIUM
    political_risk_score: float = 0.30
    logistics_risk_score: float = 0.20
    # Overhead rates
    factory_overhead_pct: float = 0.25  # % of direct labor
    admin_overhead_pct:   float = 0.10


@dataclass
class SupplierCostProfile:
    """Profil dostawcy — narzuty i warunki cenowe."""
    supplier_id:     str = ""
    name:            str = ""
    country_code:    str = ""
    # Pricing
    margin_pct:      float = 0.10     # supplier margin
    volume_discount_breaks: list[tuple[float, float]] = field(default_factory=list)  # [(qty, disc_pct)]
    tooling_amort_per_unit: float = 0.0
    # Logistics from supplier
    incoterms:       str = "DAP"
    shipping_eur_t:  float = 0.0
    lead_time_days:  int = 30
    # Quality
    defect_rate:     float = 0.01
    rework_cost_pct: float = 0.02     # % of part cost for rework/inspection
    # Payment
    payment_terms:   str = "NET30"
    early_pay_disc_pct: float = 0.0
    # Qualification cost (one-time)
    qualification_eur: float = 0.0


@dataclass
class TechnologyProfile:
    """Profil technologii wytwarzania."""
    tech_id:         str = field(default_factory=lambda: str(uuid4()))
    name:            str = ""
    process_type:    str = ""
    # Cost parameters
    machine_rate_eur_h:   float = 0.0
    energy_kwh_per_h:     float = 0.0
    tooling_cost_eur:     float = 0.0    # one-time
    tooling_life_units:   int   = 100_000
    # Performance
    cycle_time_s:         float = 0.0
    yield_pct:            float = 0.98
    scrap_rate:           float = 0.02
    tolerance_mm:         float = 0.1
    # Automation
    automation_level:     float = 0.5    # 0=manual, 1=fully automated
    labor_h_per_unit:     float = 0.0
    # CO2
    co2_kg_per_unit:      float = 0.0
    # Capex
    capex_eur:            float = 0.0
    payback_years:        float = 5.0


@dataclass
class ProductCostModel:
    """Kompletny model kosztowy produktu."""
    product_id:    str = field(default_factory=lambda: str(uuid4()))
    product_name:  str = ""
    sku:           str = ""
    version:       str = "1.0"
    tenant_id:     str = "tenant-demo"
    # BOM
    bom_lines:     list[BomLine] = field(default_factory=list)
    # Routing
    routing:       list[RoutingStep] = field(default_factory=list)
    # Country
    production_country: str = "DE"
    # Volume
    annual_volume: float = 10_000.0
    lot_size:      int   = 500
    # Cost breakdown (computed)
    direct_material_eur:  float = 0.0
    direct_labor_eur:     float = 0.0
    machine_cost_eur:     float = 0.0
    tooling_per_unit_eur: float = 0.0
    energy_cost_eur:      float = 0.0
    quality_cost_eur:     float = 0.0
    logistics_eur:        float = 0.0
    overhead_eur:         float = 0.0
    duty_tax_eur:         float = 0.0
    total_cost_eur:       float = 0.0
    # Target
    target_cost_eur:      float | None = None
    target_gap_pct:       float | None = None
    # Meta
    created_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Scenario
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostChange:
    """Pojedyncza zmiana parametru w scenariuszu."""
    change_type:   str    # "material" | "supplier" | "technology" | "country" | "volume" | "driver"
    target_id:     str    # ID elementu do zmiany
    parameter:     str    # "unit_price" | "quantity" | "labor_rate" | …
    new_value:     Any
    old_value:     Any = None
    rationale:     str = ""


@dataclass
class CostScenario:
    scenario_id:   str = field(default_factory=lambda: str(uuid4()))
    name:          str = ""
    description:   str = ""
    scenario_type: ScenarioType = ScenarioType.COMPOUND
    changes:       list[CostChange] = field(default_factory=list)
    baseline_id:   str = ""
    tenant_id:     str = "tenant-demo"
    created_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostBreakdown:
    direct_material:   float = 0.0
    direct_labor:      float = 0.0
    machine_cost:      float = 0.0
    tooling_per_unit:  float = 0.0
    energy:            float = 0.0
    quality:           float = 0.0
    logistics:         float = 0.0
    overhead:          float = 0.0
    duty_tax:          float = 0.0
    finance:           float = 0.0
    total:             float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "direct_material":  round(self.direct_material, 4),
            "direct_labor":     round(self.direct_labor, 4),
            "machine_cost":     round(self.machine_cost, 4),
            "tooling_per_unit": round(self.tooling_per_unit, 4),
            "energy":           round(self.energy, 4),
            "quality":          round(self.quality, 4),
            "logistics":        round(self.logistics, 4),
            "overhead":         round(self.overhead, 4),
            "duty_tax":         round(self.duty_tax, 4),
            "finance":          round(self.finance, 4),
            "total":            round(self.total, 4),
        }


@dataclass
class CostDelta:
    component:     str
    baseline:      float
    scenario:      float
    delta_eur:     float
    delta_pct:     float
    driver:        str    # what caused this delta


@dataclass
class CostResult:
    result_id:     str = field(default_factory=lambda: str(uuid4()))
    scenario_id:   str = ""
    product_id:    str = ""
    tenant_id:     str = ""
    # Costs
    baseline:      CostBreakdown = field(default_factory=CostBreakdown)
    scenario_cost: CostBreakdown = field(default_factory=CostBreakdown)
    # Deltas
    deltas:        list[CostDelta] = field(default_factory=list)
    total_delta_eur: float = 0.0
    total_delta_pct: float = 0.0
    # Annual impact
    annual_impact_eur: float = 0.0
    # Recommendations
    recommendations: list[str] = field(default_factory=list)
    # Sensitivity (most impactful drivers)
    sensitivity:   list[dict[str, Any]] = field(default_factory=list)
    # Meta
    computed_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms:   float = 0.0


@dataclass
class OptimizationTask:
    task_id:        str = field(default_factory=lambda: str(uuid4()))
    product_id:     str = ""
    tenant_id:      str = "tenant-demo"
    objective:      OptimizationObjective = OptimizationObjective.MIN_TOTAL_COST
    # Decision variables
    allow_material_sub:     bool = True
    allow_supplier_change:  bool = True
    allow_tech_change:      bool = False   # technology change = risky
    allow_country_change:   bool = False
    # Constraints
    max_lead_time_days:     int   | None = None
    min_quality_score:      float | None = None
    max_geo_risk:           float | None = None
    target_cost_eur:        float | None = None
    min_volume:             float | None = None
    # Budget
    max_capex_eur:          float | None = None   # for tech change
    payback_max_years:      float | None = None
    # Options
    n_alternatives:         int = 5    # number of solutions to return


@dataclass
class OptimizationResult:
    task_id:       str = ""
    product_id:    str = ""
    baseline_cost: float = 0.0
    solutions:     list[dict[str, Any]] = field(default_factory=list)
    best_cost:     float = 0.0
    best_delta_pct: float = 0.0
    pareto_front:  list[dict[str, Any]] = field(default_factory=list)
    computed_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_s:    float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API schemas
# ─────────────────────────────────────────────────────────────────────────────

class BomLineIn(BaseModel):
    material_id:   str
    material_name: str
    quantity:      float
    unit:          str = "kg"
    unit_price:    float
    supplier_id:   str = ""
    scrap_rate:    float = 0.0
    substitutes:   list[str] = Field(default_factory=list)


class RoutingStepIn(BaseModel):
    sequence:       int
    operation_name: str
    process_type:   str
    cycle_time_s:   float
    setup_time_min: float = 0.0
    labor_rate:     float = 25.0
    machine_rate:   float = 50.0
    yield_pct:      float = 0.98
    country_code:   str = "DE"


class ProductModelIn(BaseModel):
    product_name:    str
    sku:             str = ""
    bom_lines:       list[BomLineIn] = Field(default_factory=list)
    routing:         list[RoutingStepIn] = Field(default_factory=list)
    production_country: str = "DE"
    annual_volume:   float = 10_000.0
    lot_size:        int   = 500
    target_cost_eur: float | None = None
    tenant_id:       str = "tenant-demo"


class CostChangeIn(BaseModel):
    change_type: str
    target_id:   str
    parameter:   str
    new_value:   Any
    rationale:   str = ""


class ScenarioIn(BaseModel):
    name:          str
    description:   str = ""
    scenario_type: str = "compound"
    changes:       list[CostChangeIn] = Field(default_factory=list)
    baseline_id:   str
    tenant_id:     str = "tenant-demo"


class OptimizationIn(BaseModel):
    product_id:          str
    objective:           str = "min_total_cost"
    allow_material_sub:  bool = True
    allow_supplier_change: bool = True
    allow_tech_change:   bool = False
    allow_country_change: bool = False
    target_cost_eur:     float | None = None
    max_lead_time_days:  int   | None = None
    max_capex_eur:       float | None = None
    n_alternatives:      int = 5
    tenant_id:           str = "tenant-demo"


class CostResultOut(BaseModel):
    result_id:         str
    scenario_id:       str
    baseline_total:    float
    scenario_total:    float
    total_delta_eur:   float
    total_delta_pct:   float
    annual_impact_eur: float
    breakdown:         dict[str, Any]
    deltas:            list[dict[str, Any]]
    recommendations:   list[str]
    sensitivity:       list[dict[str, Any]]
