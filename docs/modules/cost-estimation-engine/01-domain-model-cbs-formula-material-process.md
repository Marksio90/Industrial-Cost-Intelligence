# Cost Estimation Engine — Part 1
# Domain Model, Cost Breakdown Structure, Formula Engine, Material Cost, Process Cost

---

## Section 1: Domain Model

### 1.1 Bounded Context

The Cost Estimation Engine (CEE) is the central computational core of the Industrial Cost
Intelligence platform. It receives geometric, material, process, and volume inputs and produces
a fully itemized, uncertainty-bounded cost estimate for any industrial product.

**Context boundaries:**
- **Upstream consumers**: RFQ Engine, Product Configurator, Procurement Portal, NPI (New Product Introduction) workflow
- **Upstream providers**: Material Intelligence Engine (material prices), Supplier Intelligence Engine (supplier rates), Manufacturing Process Engine (process parameters), Similarity Cost Search Engine (comparable quotes)
- **Downstream consumers**: Cost History Engine (archiving estimates), Finance/Controlling (budget planning), ERP (purchase order pricing baseline)

### 1.2 Core Aggregates

```python
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4
from datetime import datetime
from typing import Any

class EstimationStatus(Enum):
    DRAFT      = "DRAFT"       # input incomplete
    COMPUTING  = "COMPUTING"   # calculation in progress
    COMPLETE   = "COMPLETE"    # result available
    APPROVED   = "APPROVED"    # reviewed and approved
    SUPERSEDED = "SUPERSEDED"  # replaced by newer estimate
    ARCHIVED   = "ARCHIVED"

class CostComponent(Enum):
    MATERIAL   = "MATERIAL"    # raw material cost
    PROCESS    = "PROCESS"     # machining / forming / joining
    TOOLING    = "TOOLING"     # amortized tooling cost
    SETUP      = "SETUP"       # changeover and first-article cost
    OVERHEAD   = "OVERHEAD"    # factory overhead allocation
    SCRAP      = "SCRAP"       # expected scrap / rework cost
    LOGISTICS  = "LOGISTICS"   # inbound / outbound freight
    ENERGY     = "ENERGY"      # energy surcharge
    QUALITY    = "QUALITY"     # inspection / testing cost
    MARGIN     = "MARGIN"      # supplier profit margin

class ConfidenceLevel(Enum):
    HIGH       = "HIGH"        # MAPE < 5%
    MEDIUM     = "MEDIUM"      # MAPE 5–15%
    LOW        = "LOW"         # MAPE 15–30%
    INDICATIVE = "INDICATIVE"  # MAPE > 30%, order-of-magnitude only

@dataclass
class ProductGeometry:
    """Geometric descriptor of the part being estimated."""
    geometry_id:     UUID = field(default_factory=uuid4)
    volume_cm3:      float = 0.0        # bounding volume
    net_volume_cm3:  float = 0.0        # actual material volume after machining
    surface_area_cm2: float = 0.0
    bounding_box_mm: tuple[float,float,float] = (0.0, 0.0, 0.0)  # L × W × H
    complexity_class: str = "SIMPLE"   # SIMPLE / MODERATE / COMPLEX / VERY_COMPLEX
    feature_count:   int = 0           # number of distinct machined features
    thin_wall_flag:  bool = False       # min wall < 1mm
    deep_hole_flag:  bool = False       # depth/diameter > 10:1
    undercut_flag:   bool = False       # requires special tooling
    # Derived
    buy_to_fly_ratio: float = 1.0      # gross_volume / net_volume (machining waste)

@dataclass
class MaterialSpec:
    material_id:     UUID = field(default_factory=uuid4)
    name:            str = ""
    material_group:  str = ""          # steel / aluminum / titanium / polymer / composite
    iso_grade:       str = ""
    density_g_cm3:   float = 0.0
    price_eur_per_kg: float = 0.0
    price_date:      datetime = field(default_factory=datetime.utcnow)
    machinability_index: float = 1.0   # relative to 1018 steel = 1.0 (higher = easier)
    hardness_hrc:    float = 0.0
    # Supply chain
    availability_score: float = 1.0   # 0–1
    min_order_kg:    float = 0.0
    leadtime_weeks:  int = 4

@dataclass
class ProcessStep:
    step_id:         UUID = field(default_factory=uuid4)
    sequence:        int = 1
    process_type:    str = ""          # TURN / MILL / GRIND / WELD / BEND / COAT / etc.
    process_class:   str = ""          # CUT / MAC / FOR / JOI / FIN
    machine_type:    str = ""
    cost_per_hour_eur: float = 0.0
    setup_time_min:  float = 0.0
    cycle_time_sec:  float = 0.0
    tool_wear_factor: float = 1.0     # relative tool consumption
    tolerance_it:    int = 7           # IT grade
    surface_ra_um:   float = 1.6       # target surface roughness
    operator_count:  float = 1.0       # FTE per machine
    automation_level: float = 0.5     # 0=manual, 1=fully automated
    energy_kw:       float = 10.0

@dataclass
class EstimationInput:
    """Root input aggregate for a cost estimation request."""
    input_id:        UUID = field(default_factory=uuid4)
    product_id:      UUID | None = None   # reference to existing product (optional)
    product_name:    str = ""
    geometry:        ProductGeometry = field(default_factory=ProductGeometry)
    material:        MaterialSpec = field(default_factory=MaterialSpec)
    process_steps:   list[ProcessStep] = field(default_factory=list)
    annual_volume:   int = 1000           # pieces per year
    batch_size:      int = 100            # pieces per production run
    production_location: str = "DE"       # ISO country code
    target_currency: str = "EUR"
    surface_finish:  str = "STANDARD"    # STANDARD / FINE / MIRROR / COATED
    general_tolerance: str = "ISO2768-m" # ISO tolerance class
    requested_by:    UUID | None = None
    requested_at:    datetime = field(default_factory=datetime.utcnow)
    notes:           str = ""

@dataclass
class CostLineItem:
    component:       CostComponent
    description:     str
    quantity:        float
    unit:            str               # kg / hr / pcs / setup
    unit_cost_eur:   float
    total_cost_eur:  float
    confidence:      float             # 0–1 confidence on this line item
    data_source:     str               # "market_price" / "historical" / "ml_estimate" / "default"
    notes:           str = ""

@dataclass
class CostEstimate:
    """Root output aggregate."""
    estimate_id:     UUID = field(default_factory=uuid4)
    input_id:        UUID = field(default_factory=uuid4)
    status:          EstimationStatus = EstimationStatus.DRAFT
    
    # Summary
    unit_cost_eur:   float = 0.0
    total_cost_eur:  float = 0.0        # unit_cost × annual_volume
    
    # Component breakdown
    material_cost_eur:  float = 0.0
    process_cost_eur:   float = 0.0
    tooling_cost_eur:   float = 0.0
    setup_cost_eur:     float = 0.0
    overhead_cost_eur:  float = 0.0
    scrap_cost_eur:     float = 0.0
    logistics_cost_eur: float = 0.0
    
    # Uncertainty
    confidence_level:      ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_score:      float = 0.0     # 0–1
    uncertainty_pct:       float = 15.0    # ± %
    cost_low_eur:          float = 0.0     # lower bound (P10)
    cost_high_eur:         float = 0.0     # upper bound (P90)
    
    # Detailed breakdown
    line_items:      list[CostLineItem] = field(default_factory=list)
    
    # Metadata
    calculation_engine_version: str = "1.0.0"
    calculated_at:   datetime = field(default_factory=datetime.utcnow)
    similar_quotes_used: list[UUID] = field(default_factory=list)
    ml_models_used:  list[str] = field(default_factory=list)
    warnings:        list[str] = field(default_factory=list)
```

### 1.3 Domain Events (18)

| Event | Trigger | Consumers |
|-------|---------|-----------|
| `EstimationRequested` | POST /estimate | CEE CalculationOrchestrator |
| `EstimationStarted` | Orchestrator picks up job | Monitoring, RFQ Engine |
| `MaterialCostCalculated` | MaterialCostModel complete | Orchestrator |
| `ProcessCostCalculated` | ProcessCostModel complete | Orchestrator |
| `ToolingCostCalculated` | ToolingCostModel complete | Orchestrator |
| `SetupCostCalculated` | SetupCostModel complete | Orchestrator |
| `OverheadAllocated` | OverheadModel complete | Orchestrator |
| `ScrapCostApplied` | ScrapModel complete | Orchestrator |
| `VolumeDiscountApplied` | VolumeDiscountModel complete | Orchestrator |
| `SimilarQuotesInjected` | SCSE lookup complete | Orchestrator |
| `ConfidenceScored` | ConfidenceCalculator complete | Orchestrator |
| `EstimationCompleted` | All components assembled | Cost History Engine, RFQ Engine |
| `EstimationApproved` | Cost engineer approval | Procurement, Finance |
| `EstimationRejected` | Review rejection | Requestor |
| `EstimationSuperseded` | New estimate for same product | Cost History Engine |
| `BenchmarkUpdated` | New market price available | Staleness check |
| `MLModelRetrained` | Accuracy drift detected | Model Registry |
| `CostDriverChanged` | Material/energy price spike >10% | Alert, re-estimate trigger |

### 1.4 Value Objects

```python
@dataclass(frozen=True)
class Money:
    amount: float
    currency: str = "EUR"
    
    def __add__(self, other: "Money") -> "Money":
        assert self.currency == other.currency
        return Money(self.amount + other.amount, self.currency)
    
    def __mul__(self, factor: float) -> "Money":
        return Money(self.amount * factor, self.currency)

@dataclass(frozen=True)
class UncertaintyBound:
    low:    float    # P10 estimate (EUR)
    point:  float    # best estimate (EUR)
    high:   float    # P90 estimate (EUR)
    
    @property
    def spread_pct(self) -> float:
        if self.point == 0:
            return 0.0
        return (self.high - self.low) / self.point * 100

@dataclass(frozen=True)
class ProductionLocation:
    country_iso: str
    region:      str
    labor_rate_eur_hr: float
    overhead_rate_pct: float
    energy_cost_eur_kwh: float
    logistics_factor:   float    # multiplier vs baseline DE
    currency_pair:      str      # e.g. "PLN/EUR"
    fx_rate:            float

PRODUCTION_LOCATIONS = {
    "DE": ProductionLocation("DE", "EU-WEST",  45.0, 0.80, 0.28, 1.00, "EUR/EUR", 1.000),
    "PL": ProductionLocation("PL", "EU-EAST",  18.0, 0.65, 0.22, 1.05, "PLN/EUR", 0.232),
    "CZ": ProductionLocation("CZ", "EU-EAST",  20.0, 0.68, 0.21, 1.04, "CZK/EUR", 0.040),
    "RO": ProductionLocation("RO", "EU-EAST",  14.0, 0.60, 0.18, 1.08, "RON/EUR", 0.201),
    "CN": ProductionLocation("CN", "ASIA",     8.0,  0.50, 0.12, 1.25, "CNY/EUR", 0.130),
    "IN": ProductionLocation("IN", "ASIA",     6.0,  0.45, 0.10, 1.30, "INR/EUR", 0.011),
    "MX": ProductionLocation("MX", "AMERICAS", 9.0,  0.55, 0.14, 1.20, "MXN/EUR", 0.052),
    "US": ProductionLocation("US", "AMERICAS", 38.0, 0.75, 0.20, 1.10, "USD/EUR", 0.920),
    "TR": ProductionLocation("TR", "MENA",     12.0, 0.58, 0.16, 1.10, "TRY/EUR", 0.031),
}
```

### 1.5 Context Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Cost Estimation Engine (CEE)                            │
│                                                                             │
│  Upstream Data Providers (consume via REST / Kafka):                        │
│  ┌────────────────────┐  ┌──────────────────────┐  ┌───────────────────┐   │
│  │ Material           │  │ Manufacturing Process │  │ Supplier          │   │
│  │ Intelligence Engine│  │ Engine               │  │ Intelligence Engine│  │
│  │                    │  │                      │  │                   │   │
│  │ → material prices  │  │ → process params     │  │ → labor rates     │   │
│  │ → density, grade   │  │ → cycle times        │  │ → overhead rates  │   │
│  │ → LME spot         │  │ → machine costs      │  │ → MOQ, lead time  │   │
│  └────────────────────┘  └──────────────────────┘  └───────────────────┘   │
│                                                                             │
│  ┌────────────────────┐                                                     │
│  │ Similarity Cost    │  → similar quotes inject confidence anchors         │
│  │ Search Engine      │                                                     │
│  └────────────────────┘                                                     │
│                                                                             │
│  CEE Internal Engines:                                                      │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │  CalculationOrchestrator                                         │       │
│  │  ├── MaterialCostEngine     ── LME price + density + BTF ratio  │       │
│  │  ├── ProcessCostEngine      ── cycle time × rate + energy       │       │
│  │  ├── ToolingCostEngine      ── amortization over volume         │       │
│  │  ├── SetupCostEngine        ── changeover + SMED model          │       │
│  │  ├── OverheadModel          ── absorption costing               │       │
│  │  ├── ScrapModel             ── Cp/Cpk → scrap rate → cost       │       │
│  │  ├── VolumeDiscountModel    ── experience curve + breaks        │       │
│  │  ├── LogisticsModel         ── Incoterm + distance + weight     │       │
│  │  ├── UncertaintyEngine      ── Monte Carlo (10K samples)        │       │
│  │  ├── SimilarityInjectionLayer ── SCSE anchor + Bayesian update  │       │
│  │  ├── MLEnsemble             ── XGBoost + LightGBM + fallback    │       │
│  │  └── ConfidenceCalculator   ── 7-component confidence score     │       │
│  └──────────────────────────────────────────────────────────────────┘       │
│                                                                             │
│  Downstream consumers:                                                      │
│  RFQ Engine → Cost History Engine → Finance/ERP → Procurement Portal       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Section 2: Cost Breakdown Structure

### 2.1 CBS Hierarchy

The Cost Breakdown Structure follows the DIN 8593 / VDI 2225 standard for manufacturing cost
decomposition:

```
Total Product Cost
│
├── 1. Direct Manufacturing Cost
│   ├── 1.1 Material Cost
│   │   ├── 1.1.1 Raw Material (price × weight)
│   │   ├── 1.1.2 Material Waste (buy-to-fly ratio)
│   │   └── 1.1.3 Material Logistics (inbound freight)
│   │
│   ├── 1.2 Process Cost
│   │   ├── 1.2.1 Machine Cost (hourly rate × cycle time)
│   │   ├── 1.2.2 Labor Cost (operator rate × attendance)
│   │   ├── 1.2.3 Tooling Consumption (wear per piece)
│   │   └── 1.2.4 Energy Cost (kWh × rate)
│   │
│   └── 1.3 Quality Cost
│       ├── 1.3.1 Inspection (CMM / visual / NDT)
│       ├── 1.3.2 Scrap Cost (scrap_rate × material+process cost)
│       └── 1.3.3 Rework Cost (rework_rate × rework_hours × rate)
│
├── 2. Non-Recurring Costs (amortized per piece)
│   ├── 2.1 Tooling Investment (molds, dies, fixtures / volume)
│   ├── 2.2 Setup Cost (changeover time × rate / batch size)
│   ├── 2.3 Programming Cost (NC programming / volume)
│   └── 2.4 First Article Inspection (FAI / volume)
│
├── 3. Overhead
│   ├── 3.1 Factory Overhead (depreciation, maintenance, utilities)
│   ├── 3.2 G&A (management, admin, IT)
│   └── 3.3 R&D Allocation
│
├── 4. Logistics & Supply Chain
│   ├── 4.1 Outbound Freight
│   ├── 4.2 Packaging
│   └── 4.3 Insurance
│
└── 5. Margin
    ├── 5.1 Supplier Profit Margin (%)
    └── 5.2 Risk Premium (volatility buffer)
```

### 2.2 CBS Weighting Benchmarks

Industry benchmarks for CBS component percentages (varies by process family):

| Component | Machining | Sheet Metal | Casting | Injection Moulding | Assembly |
|-----------|-----------|-------------|---------|-------------------|---------|
| Material | 40–60% | 55–70% | 50–65% | 35–50% | 20–35% |
| Process | 20–35% | 15–25% | 10–20% | 8–15% | 35–55% |
| Tooling (amort.) | 2–8% | 3–10% | 5–15% | 10–25% | 1–3% |
| Setup (amort.) | 3–8% | 2–6% | 1–3% | 1–2% | 2–5% |
| Overhead | 10–20% | 10–18% | 12–22% | 8–15% | 12–20% |
| Scrap | 1–5% | 2–6% | 3–8% | 2–5% | 0.5–2% |
| Logistics | 2–5% | 2–5% | 3–7% | 2–4% | 1–3% |
| Margin | 5–15% | 5–12% | 8–15% | 10–20% | 5–12% |

### 2.3 CostBreakdownStructure class

```python
@dataclass
class CostBreakdownStructure:
    """Complete CBS for one estimation result."""
    
    # Level 1.1 — Material
    raw_material_kg:      float = 0.0   # net weight
    gross_material_kg:    float = 0.0   # gross (before machining)
    raw_material_price:   float = 0.0   # EUR/kg
    raw_material_cost:    float = 0.0   # EUR/piece
    material_waste_cost:  float = 0.0   # cost of removed material
    inbound_freight_cost: float = 0.0   # EUR/piece
    total_material_cost:  float = 0.0   # EUR/piece
    material_pct:         float = 0.0   # % of total
    
    # Level 1.2 — Process
    total_cycle_time_min: float = 0.0
    machine_cost_eur_hr:  float = 0.0   # blended rate
    machine_cost:         float = 0.0   # EUR/piece
    labor_cost:           float = 0.0   # EUR/piece
    tooling_wear_cost:    float = 0.0   # EUR/piece (consumable)
    energy_cost:          float = 0.0   # EUR/piece
    total_process_cost:   float = 0.0   # EUR/piece
    process_pct:          float = 0.0
    
    # Level 1.3 — Quality
    scrap_rate_pct:       float = 0.0
    scrap_cost:           float = 0.0   # EUR/piece
    inspection_cost:      float = 0.0   # EUR/piece
    total_quality_cost:   float = 0.0
    
    # Level 2 — NRC amortized
    tooling_investment:   float = 0.0   # total EUR
    tooling_life_pieces:  int = 0
    tooling_cost_per_piece: float = 0.0
    setup_cost_per_piece: float = 0.0
    programming_cost_per_piece: float = 0.0
    fai_cost_per_piece:   float = 0.0
    total_nrc_amortized:  float = 0.0
    
    # Level 3 — Overhead
    overhead_rate_pct:    float = 0.0
    overhead_cost:        float = 0.0
    
    # Level 4 — Logistics
    outbound_freight:     float = 0.0
    packaging_cost:       float = 0.0
    total_logistics:      float = 0.0
    
    # Level 5 — Margin
    margin_pct:           float = 0.0
    margin_eur:           float = 0.0
    
    # Totals
    cost_ex_margin:       float = 0.0   # total before margin
    unit_cost_eur:        float = 0.0   # final unit cost
    annual_volume:        int = 0
    total_annual_cost:    float = 0.0
```

---

## Section 3: Cost Formula Engine

### 3.1 Master Cost Formula

```
Unit Cost (EUR/piece) =
    Material_Cost
  + Process_Cost
  + Tooling_Cost_Amortized
  + Setup_Cost_Amortized
  + Overhead_Allocation
  + Scrap_Cost
  + Logistics_Cost
  + Margin_EUR

where:
  Material_Cost           = f(density, volume, buy_to_fly, price_per_kg, location)
  Process_Cost            = Σ_i [cycle_time_i × machine_rate_i + labor_i + energy_i]
  Tooling_Cost_Amortized  = Σ_j tooling_investment_j / MIN(volume, tool_life_j)
  Setup_Cost_Amortized    = (setup_time × rate) / batch_size
  Overhead_Allocation     = (Material_Cost + Process_Cost) × overhead_rate
  Scrap_Cost              = (Material_Cost + Process_Cost) × scrap_rate / (1 - scrap_rate)
  Logistics_Cost          = f(weight, distance, incoterm, mode)
  Margin_EUR              = cost_ex_margin × margin_pct
```

### 3.2 CostFormulaEngine

```python
import math
from dataclasses import dataclass

class CostFormulaEngine:
    """
    Deterministic cost formula evaluator.
    Orchestrated by CalculationOrchestrator; each sub-model is called in dependency order.
    """
    
    def __init__(
        self,
        material_engine: "MaterialCostEngine",
        process_engine:  "ProcessCostEngine",
        tooling_engine:  "ToolingCostEngine",
        setup_engine:    "SetupCostEngine",
        overhead_model:  "OverheadModel",
        scrap_model:     "ScrapModel",
        volume_model:    "VolumeDiscountModel",
        logistics_model: "LogisticsModel",
    ):
        self.material  = material_engine
        self.process   = process_engine
        self.tooling   = tooling_engine
        self.setup     = setup_engine
        self.overhead  = overhead_model
        self.scrap     = scrap_model
        self.volume    = volume_model
        self.logistics = logistics_model
    
    def calculate(self, inp: EstimationInput) -> CostBreakdownStructure:
        cbs = CostBreakdownStructure(annual_volume=inp.annual_volume)
        loc = PRODUCTION_LOCATIONS.get(inp.production_location, PRODUCTION_LOCATIONS["DE"])
        
        # Step 1: Material cost (independent)
        mat = self.material.calculate(inp.geometry, inp.material, loc)
        cbs.raw_material_kg        = mat.net_weight_kg
        cbs.gross_material_kg      = mat.gross_weight_kg
        cbs.raw_material_price     = mat.price_eur_per_kg
        cbs.raw_material_cost      = mat.raw_material_cost_eur
        cbs.material_waste_cost    = mat.waste_cost_eur
        cbs.inbound_freight_cost   = mat.freight_cost_eur
        cbs.total_material_cost    = mat.total_cost_eur
        
        # Step 2: Process cost (depends on material → machinability)
        proc = self.process.calculate(inp.process_steps, inp.geometry, inp.material, loc)
        cbs.total_cycle_time_min   = proc.total_cycle_time_min
        cbs.machine_cost           = proc.machine_cost_eur
        cbs.labor_cost             = proc.labor_cost_eur
        cbs.tooling_wear_cost      = proc.tooling_wear_eur
        cbs.energy_cost            = proc.energy_cost_eur
        cbs.total_process_cost     = proc.total_cost_eur
        
        # Step 3: Tooling amortization
        tool = self.tooling.calculate(inp.process_steps, inp.annual_volume, inp.geometry)
        cbs.tooling_investment      = tool.total_investment_eur
        cbs.tooling_cost_per_piece  = tool.cost_per_piece_eur
        
        # Step 4: Setup cost (depends on batch size)
        stp = self.setup.calculate(inp.process_steps, inp.batch_size, loc)
        cbs.setup_cost_per_piece    = stp.cost_per_piece_eur
        cbs.programming_cost_per_piece = stp.programming_cost_eur
        cbs.fai_cost_per_piece      = stp.fai_cost_eur
        cbs.total_nrc_amortized     = (
            cbs.tooling_cost_per_piece
            + cbs.setup_cost_per_piece
            + cbs.programming_cost_per_piece
            + cbs.fai_cost_per_piece
        )
        
        # Step 5: Overhead (on direct costs)
        direct_cost = cbs.total_material_cost + cbs.total_process_cost
        ovh = self.overhead.calculate(direct_cost, inp.production_location, inp.process_steps)
        cbs.overhead_rate_pct  = ovh.rate_pct
        cbs.overhead_cost      = ovh.overhead_eur
        
        # Step 6: Scrap (applied on direct + overhead)
        pre_scrap = direct_cost + cbs.total_nrc_amortized + cbs.overhead_cost
        scr = self.scrap.calculate(pre_scrap, inp.process_steps, inp.geometry)
        cbs.scrap_rate_pct  = scr.effective_scrap_rate_pct
        cbs.scrap_cost      = scr.scrap_cost_eur
        cbs.inspection_cost = scr.inspection_cost_eur
        cbs.total_quality_cost = cbs.scrap_cost + cbs.inspection_cost
        
        # Step 7: Logistics
        part_weight_kg = mat.net_weight_kg
        lgx = self.logistics.calculate(part_weight_kg, inp.production_location, "DAP")
        cbs.outbound_freight  = lgx.freight_eur
        cbs.packaging_cost    = lgx.packaging_eur
        cbs.total_logistics   = lgx.total_eur
        
        # Subtotal before margin
        cbs.cost_ex_margin = (
            cbs.total_material_cost
            + cbs.total_process_cost
            + cbs.total_nrc_amortized
            + cbs.overhead_cost
            + cbs.total_quality_cost
            + cbs.total_logistics
        )
        
        # Step 8: Volume discount (adjusts cost_ex_margin downward)
        vol_discount = self.volume.calculate(inp.annual_volume, cbs.cost_ex_margin)
        cbs.cost_ex_margin *= (1.0 - vol_discount.discount_rate)
        
        # Step 9: Margin
        cbs.margin_pct = self._derive_margin(inp.production_location, inp.process_steps)
        cbs.margin_eur = cbs.cost_ex_margin * cbs.margin_pct
        
        # Final
        cbs.unit_cost_eur         = cbs.cost_ex_margin + cbs.margin_eur
        cbs.total_annual_cost     = cbs.unit_cost_eur * inp.annual_volume
        cbs.material_pct          = cbs.total_material_cost / cbs.unit_cost_eur * 100
        cbs.process_pct           = cbs.total_process_cost / cbs.unit_cost_eur * 100
        
        return cbs
    
    @staticmethod
    def _derive_margin(country: str, steps: list[ProcessStep]) -> float:
        """Derive target margin from location and process complexity."""
        base = 0.10  # 10% baseline
        # Adjust for location (lower labor = higher margin expectation by supplier)
        if country in ("CN", "IN", "VN"):
            base = 0.15
        # Adjust for complexity
        avg_it = sum(s.tolerance_it for s in steps) / max(len(steps), 1)
        if avg_it <= 5:   # tight tolerance → higher margin
            base += 0.05
        return base
```

### 3.3 Calculation Orchestrator

```python
import asyncio
from typing import Optional

class CalculationOrchestrator:
    """Async orchestrator: runs sub-engines in dependency order with timeout control."""
    
    ENGINE_TIMEOUT_SEC = 30
    
    def __init__(
        self,
        formula_engine:   CostFormulaEngine,
        ml_ensemble:      "MLEnsembleEngine",
        similarity_layer: "SimilarityInjectionLayer",
        confidence_calc:  "ConfidenceCalculator",
        uncertainty_mc:   "MonteCarloUncertaintyEngine",
        outbox:           "CEEOutboxPublisher",
    ):
        self.formula     = formula_engine
        self.ml          = ml_ensemble
        self.similarity  = similarity_layer
        self.confidence  = confidence_calc
        self.mc          = uncertainty_mc
        self.outbox      = outbox
    
    async def estimate(self, inp: EstimationInput) -> CostEstimate:
        estimate = CostEstimate(input_id=inp.input_id)
        estimate.status = EstimationStatus.COMPUTING
        
        # Stage 1: Deterministic formula (fast path, < 200ms)
        cbs = await asyncio.wait_for(
            asyncio.to_thread(self.formula.calculate, inp),
            timeout=self.ENGINE_TIMEOUT_SEC,
        )
        estimate = self._apply_cbs(estimate, cbs)
        
        # Stage 2: ML ensemble refinement (parallel with Stage 3)
        ml_task   = asyncio.create_task(self.ml.predict(inp, cbs))
        sim_task  = asyncio.create_task(self.similarity.fetch_anchors(inp))
        ml_result, sim_anchors = await asyncio.gather(ml_task, sim_task)
        
        # Stage 3: Bayesian update with similarity anchors
        estimate = self.similarity.apply_update(estimate, sim_anchors, ml_result)
        
        # Stage 4: Monte Carlo uncertainty (10K samples)
        mc_result = await asyncio.to_thread(self.mc.run, inp, cbs, n_samples=10_000)
        estimate.cost_low_eur  = mc_result.p10
        estimate.cost_high_eur = mc_result.p90
        estimate.uncertainty_pct = mc_result.spread_pct
        
        # Stage 5: Confidence scoring
        conf = self.confidence.score(inp, cbs, ml_result, sim_anchors, mc_result)
        estimate.confidence_score = conf.overall
        estimate.confidence_level = conf.grade
        
        estimate.status = EstimationStatus.COMPLETE
        estimate.calculated_at = datetime.utcnow()
        
        await self.outbox.publish_estimation_completed(estimate)
        return estimate
    
    @staticmethod
    def _apply_cbs(estimate: CostEstimate, cbs: CostBreakdownStructure) -> CostEstimate:
        estimate.unit_cost_eur      = cbs.unit_cost_eur
        estimate.total_cost_eur     = cbs.total_annual_cost
        estimate.material_cost_eur  = cbs.total_material_cost
        estimate.process_cost_eur   = cbs.total_process_cost
        estimate.tooling_cost_eur   = cbs.tooling_cost_per_piece
        estimate.setup_cost_eur     = cbs.setup_cost_per_piece
        estimate.overhead_cost_eur  = cbs.overhead_cost
        estimate.scrap_cost_eur     = cbs.scrap_cost
        estimate.logistics_cost_eur = cbs.total_logistics
        return estimate
```

---

## Section 4: Material Cost Model

### 4.1 Core Formula

```
Material Cost (EUR/piece) = gross_weight_kg × price_eur_per_kg × location_factor
                           + scrap_value_credit (optional)

gross_weight_kg = net_volume_cm3 × density_g_cm3 / 1000 × buy_to_fly_ratio

buy_to_fly_ratio = {
    CASTING:             1.05–1.15  (minimal waste)
    SHEET_METAL:         1.10–1.30  (blank optimization)
    EXTRUSION:           1.05–1.10
    MACHINING (simple):  1.5–3.0    (significant chips)
    MACHINING (complex): 3.0–10.0   (aerospace typical: 5–20)
    FORGING:             1.15–1.40
    INJECTION_MOULDING:  1.02–1.05  (gate + runner)
}
```

### 4.2 MaterialCostEngine

```python
@dataclass
class MaterialCostResult:
    net_weight_kg:      float
    gross_weight_kg:    float
    buy_to_fly_ratio:   float
    price_eur_per_kg:   float
    raw_material_cost_eur: float
    waste_cost_eur:     float
    scrap_credit_eur:   float
    freight_cost_eur:   float
    total_cost_eur:     float
    price_source:       str    # "live_market" / "historical_30d" / "default"

class MaterialCostEngine:
    
    # Buy-to-fly ratios by dominant process class
    BTF_BY_PROCESS = {
        "CASTING":     1.10,
        "FORGING":     1.25,
        "EXTRUSION":   1.08,
        "SHEET_METAL": 1.20,
        "MACHINING":   None,    # computed from geometry
        "MOULDING":    1.03,
        "WELDING":     1.15,
        "ADDITIVE":    1.05,
    }
    
    # Scrap credit rates (fraction of raw material price recoverable)
    SCRAP_CREDIT_BY_MATERIAL = {
        "steel":     0.15,    # steel swarf ~ 15% of raw
        "aluminum":  0.40,    # high scrap value
        "titanium":  0.60,    # very high scrap value
        "copper":    0.55,
        "polymer":   0.00,    # typically no credit
        "composite": 0.00,
    }
    
    def __init__(self, price_service: "MaterialPriceService"):
        self.price_service = price_service
    
    def calculate(
        self,
        geometry: ProductGeometry,
        material: MaterialSpec,
        location: ProductionLocation,
    ) -> MaterialCostResult:
        # 1. Determine buy-to-fly ratio
        btf = self._compute_btf(geometry)
        
        # 2. Compute weights
        net_weight_kg   = geometry.net_volume_cm3 * material.density_g_cm3 / 1000
        gross_weight_kg = net_weight_kg * btf
        waste_weight_kg = gross_weight_kg - net_weight_kg
        
        # 3. Get current material price
        price_eur_kg, source = self.price_service.get_price(
            material.material_id, location.country_iso
        )
        
        # 4. Cost calculation
        raw_cost  = gross_weight_kg * price_eur_kg
        
        # MOQ penalty: if below minimum order, apply premium
        moq_factor = 1.0
        if net_weight_kg < material.min_order_kg:
            moq_factor = 1.10  # 10% premium for below-MOQ orders
        raw_cost *= moq_factor
        
        # 5. Waste cost (opportunity cost of removed material)
        waste_cost = waste_weight_kg * price_eur_kg
        
        # 6. Scrap credit (swarf resale value)
        mat_group = material.material_group.lower()
        credit_rate = self.SCRAP_CREDIT_BY_MATERIAL.get(mat_group, 0.0)
        scrap_credit = waste_weight_kg * price_eur_kg * credit_rate
        
        # 7. Inbound freight
        freight = self._compute_inbound_freight(gross_weight_kg, material, location)
        
        total = raw_cost - scrap_credit + freight
        
        return MaterialCostResult(
            net_weight_kg=net_weight_kg,
            gross_weight_kg=gross_weight_kg,
            buy_to_fly_ratio=btf,
            price_eur_per_kg=price_eur_kg,
            raw_material_cost_eur=raw_cost,
            waste_cost_eur=waste_cost,
            scrap_credit_eur=scrap_credit,
            freight_cost_eur=freight,
            total_cost_eur=total,
            price_source=source,
        )
    
    def _compute_btf(self, geometry: ProductGeometry) -> float:
        """Compute buy-to-fly from geometry attributes."""
        if geometry.buy_to_fly_ratio > 1.0:
            return geometry.buy_to_fly_ratio  # override from CAD
        
        # Heuristic for machined parts
        if geometry.net_volume_cm3 > 0 and geometry.volume_cm3 > 0:
            btf = geometry.volume_cm3 / geometry.net_volume_cm3
        else:
            btf = 2.0  # default assumption
        
        # Complexity modifiers
        if geometry.complexity_class == "VERY_COMPLEX":
            btf *= 1.3
        if geometry.deep_hole_flag:
            btf *= 1.1
        if geometry.undercut_flag:
            btf *= 1.15
        
        return round(min(btf, 20.0), 2)  # cap at 20:1
    
    @staticmethod
    def _compute_inbound_freight(weight_kg: float, material: MaterialSpec,
                                  location: ProductionLocation) -> float:
        """Simplified freight model: base rate + weight surcharge."""
        base_rate_eur = 25.0
        per_kg_rate   = 0.08 * location.logistics_factor
        return base_rate_eur + weight_kg * per_kg_rate

class MaterialPriceService:
    """Fetches current market prices from Material Intelligence Engine."""
    
    CACHE_TTL_HOURS = 4
    
    def __init__(self, mie_client: "MIEClient", redis_client: "Redis"):
        self.mie   = mie_client
        self.redis = redis_client
    
    def get_price(self, material_id: UUID, country: str) -> tuple[float, str]:
        cache_key = f"cee:matprice:{material_id}:{country}"
        cached = self.redis.get(cache_key)
        if cached:
            return float(cached), "cache"
        
        try:
            price = self.mie.get_current_price(material_id, country)
            self.redis.setex(cache_key, self.CACHE_TTL_HOURS * 3600, str(price))
            return price, "live_market"
        except Exception:
            # Fallback: historical 30-day average
            price = self.mie.get_historical_avg_price(material_id, days=30)
            return price, "historical_30d"
```

### 4.3 Material Surcharge Model

Real-world steel/aluminum pricing includes energy and alloy surcharges that fluctuate independently:

```python
@dataclass
class MaterialSurcharge:
    energy_surcharge_pct:   float = 0.0    # varies monthly
    alloy_surcharge_eur_kg: float = 0.0    # e.g. Cr, Ni surcharges for stainless
    cutting_fluid_eur_kg:   float = 0.02   # coolant cost per kg machined
    heat_treatment_eur_kg:  float = 0.0    # if HT required

class SurchargeModel:
    """Applies energy and alloy surcharges to raw material price."""
    
    def apply(self, base_price: float, material: MaterialSpec) -> float:
        surcharges = self._fetch_surcharges(material)
        adjusted = base_price * (1 + surcharges.energy_surcharge_pct)
        adjusted += surcharges.alloy_surcharge_eur_kg
        return adjusted
    
    def _fetch_surcharges(self, material: MaterialSpec) -> MaterialSurcharge:
        """Published monthly by steel/aluminum associations."""
        # Stainless steel: Ni surcharge ~€8–15/kg, Cr ~€2–4/kg
        if "stainless" in material.material_group.lower():
            return MaterialSurcharge(
                energy_surcharge_pct=0.08,
                alloy_surcharge_eur_kg=9.5,  # live from MIE
            )
        # Carbon steel: energy surcharge only
        elif "carbon_steel" in material.material_group.lower():
            return MaterialSurcharge(energy_surcharge_pct=0.05)
        # Aluminum: energy-intensive production
        elif "aluminum" in material.material_group.lower():
            return MaterialSurcharge(energy_surcharge_pct=0.12)
        return MaterialSurcharge()
```

---

## Section 5: Process Cost Model

### 5.1 Core Formula (per process step i)

```
Process Cost_i (EUR/piece) =
    (cycle_time_i_min / 60) × machine_rate_i_eur_hr × machinability_factor
  + (cycle_time_i_min / 60) × labor_rate_eur_hr × operator_count_i
  + energy_kw_i × (cycle_time_i_min / 60) × energy_rate_eur_kwh
  + tooling_wear_cost_i

Total Process Cost = Σ_i Process_Cost_i
```

### 5.2 Cycle Time Calculation (if not provided)

When cycle time is not directly specified, it is estimated from process parameters:

```python
class CycleTimeEstimator:
    """
    Analytical cycle time models for standard processes.
    Uses MRR (Material Removal Rate) for machining,
    forming rate models for sheet metal, etc.
    """
    
    def estimate_turning_cycle_time(
        self,
        diameter_mm: float,
        length_mm: float,
        material: MaterialSpec,
        depth_of_cut_mm: float = 1.5,
        feed_mm_rev: float = 0.2,
    ) -> float:
        """Returns cycle time in minutes."""
        # Cutting speed from machinability index (relative to 1018 steel = 100 m/min)
        v_c = 100 * material.machinability_index  # m/min
        n_rpm  = (v_c * 1000) / (math.pi * diameter_mm)
        f_mm_min = feed_mm_rev * n_rpm
        # Machining time (main cut)
        t_main = length_mm / f_mm_min
        # Tool approach + overrun
        t_approach = 5.0 / f_mm_min
        # Indexing + idle
        t_idle = 0.3
        return t_main + t_approach + t_idle
    
    def estimate_milling_cycle_time(
        self,
        surface_area_mm2: float,
        material: MaterialSpec,
        depth_per_pass_mm: float = 1.0,
        passes: int = 3,
        tool_diameter_mm: float = 16.0,
        stepover_pct: float = 0.60,
    ) -> float:
        """Returns cycle time in minutes."""
        v_c    = 80 * material.machinability_index  # m/min
        n_rpm  = (v_c * 1000) / (math.pi * tool_diameter_mm)
        fz     = 0.08  # mm/tooth, 4-flute
        f_mm_min = fz * 4 * n_rpm
        
        # Effective width per pass
        ae = tool_diameter_mm * stepover_pct
        # Path length estimation
        path_length_mm = surface_area_mm2 / ae * passes
        
        t_machine = path_length_mm / f_mm_min
        t_idle    = 1.5  # tool change + repositioning
        return t_machine + t_idle
    
    def estimate_bending_cycle_time(self, bend_count: int) -> float:
        """Sheet metal bending: ~30s per bend + 60s setup."""
        return (0.5 * bend_count) + 1.0
```

### 5.3 ProcessCostEngine

```python
@dataclass
class ProcessStepCost:
    step_id:         UUID
    process_type:    str
    cycle_time_min:  float
    machine_cost_eur: float
    labor_cost_eur:  float
    energy_cost_eur: float
    tooling_wear_eur: float
    total_cost_eur:  float

@dataclass
class ProcessCostResult:
    step_costs:          list[ProcessStepCost]
    total_cycle_time_min: float
    machine_cost_eur:    float
    labor_cost_eur:      float
    energy_cost_eur:     float
    tooling_wear_eur:    float
    total_cost_eur:      float

class ProcessCostEngine:
    
    # Machine rate database (EUR/hr, fully loaded: depreciation + maintenance + floor space)
    MACHINE_RATES = {
        "CNC_LATHE_SMALL":      55,   # ø < 200mm
        "CNC_LATHE_LARGE":      85,   # ø > 200mm
        "CNC_MILL_3AXIS":       75,
        "CNC_MILL_5AXIS":      120,
        "VMC_3AXIS":            80,
        "HMC_4AXIS":           110,
        "GRINDER_CYLINDRICAL":  95,
        "GRINDER_SURFACE":      70,
        "EDM_WIRE":            100,
        "EDM_SINKER":          130,
        "LASER_CUT_2KW":        65,
        "LASER_CUT_6KW":        90,
        "WATERJET":             55,
        "PRESS_BRAKE":          45,
        "PRESS_100T":           60,
        "PRESS_500T":          100,
        "SPOT_WELDER":          40,
        "MIG_WELDER":           50,
        "TIG_WELDER":           60,
        "INJECTION_MOULD_100T": 80,
        "INJECTION_MOULD_500T": 140,
        "CMM_ZEISS":            80,
        "WASH_UNIT":            25,
    }
    
    def __init__(
        self,
        cycle_time_estimator: CycleTimeEstimator,
        price_service: "ProcessRateService",
    ):
        self.cycle_estimator = cycle_time_estimator
        self.rate_service    = price_service
    
    def calculate(
        self,
        process_steps: list[ProcessStep],
        geometry: ProductGeometry,
        material: MaterialSpec,
        location: ProductionLocation,
    ) -> ProcessCostResult:
        step_costs = []
        
        for step in process_steps:
            # Get or estimate cycle time
            ct = step.cycle_time_sec / 60.0 if step.cycle_time_sec > 0 else \
                 self._estimate_cycle_time(step, geometry, material)
            
            # Apply machinability factor for cutting processes
            mac_factor = 1.0
            if step.process_class in ("CUT", "MAC"):
                mac_factor = 1.0 / material.machinability_index  # lower machinability = longer time
            ct_adjusted = ct * mac_factor
            
            # Machine rate (location-adjusted)
            machine_rate = self._get_machine_rate(step.machine_type, location)
            
            # Labor rate
            labor_rate = location.labor_rate_eur_hr * step.operator_count
            
            # Costs per piece
            ct_hrs = ct_adjusted / 60.0
            machine_cost = ct_hrs * machine_rate
            labor_cost   = ct_hrs * labor_rate
            energy_cost  = step.energy_kw * ct_hrs * location.energy_cost_eur_kwh
            
            # Tooling wear (consumable inserts / cutters)
            tooling_wear = self._tooling_wear_cost(step, material, ct_adjusted)
            
            total = machine_cost + labor_cost + energy_cost + tooling_wear
            
            step_costs.append(ProcessStepCost(
                step_id=step.step_id,
                process_type=step.process_type,
                cycle_time_min=ct_adjusted,
                machine_cost_eur=machine_cost,
                labor_cost_eur=labor_cost,
                energy_cost_eur=energy_cost,
                tooling_wear_eur=tooling_wear,
                total_cost_eur=total,
            ))
        
        return ProcessCostResult(
            step_costs=step_costs,
            total_cycle_time_min=sum(s.cycle_time_min for s in step_costs),
            machine_cost_eur=sum(s.machine_cost_eur for s in step_costs),
            labor_cost_eur=sum(s.labor_cost_eur for s in step_costs),
            energy_cost_eur=sum(s.energy_cost_eur for s in step_costs),
            tooling_wear_eur=sum(s.tooling_wear_eur for s in step_costs),
            total_cost_eur=sum(s.total_cost_eur for s in step_costs),
        )
    
    def _get_machine_rate(self, machine_type: str, location: ProductionLocation) -> float:
        base_rate = self.MACHINE_RATES.get(machine_type, 70.0)
        # Location factor: German rates are baseline; Poland ~50%, China ~25%
        rate_factor = location.labor_rate_eur_hr / 45.0  # normalize to DE rate
        # Capital cost (machine purchase) is global; only labor part scales
        machine_capital_pct = 0.60   # 60% of hourly rate is capital-related
        machine_labor_pct   = 0.40   # 40% is labor-related
        adjusted = base_rate * (machine_capital_pct + machine_labor_pct * rate_factor)
        return adjusted
    
    def _tooling_wear_cost(
        self,
        step: ProcessStep,
        material: MaterialSpec,
        cycle_time_min: float,
    ) -> float:
        """Estimate consumable tooling cost per piece."""
        # Insert cost: assume €3–12 per insert, life varies by material
        if step.process_class not in ("CUT", "MAC"):
            return 0.0
        insert_cost_eur = 6.0
        insert_life_min = 30.0 / material.tool_wear_factor if hasattr(material, 'tool_wear_factor') else 30.0
        # Harder material → faster tool wear
        hardness_factor = 1.0 + max(0, (material.hardness_hrc - 30) / 30)
        insert_life_min /= hardness_factor
        return (cycle_time_min / insert_life_min) * insert_cost_eur
    
    def _estimate_cycle_time(
        self,
        step: ProcessStep,
        geometry: ProductGeometry,
        material: MaterialSpec,
    ) -> float:
        """Fallback cycle time estimation from process + geometry."""
        if step.process_type in ("TURNING", "CNC_LATHE"):
            return self.cycle_estimator.estimate_turning_cycle_time(
                diameter_mm=geometry.bounding_box_mm[1],
                length_mm=geometry.bounding_box_mm[0],
                material=material,
            )
        elif step.process_type in ("MILLING", "CNC_MILL"):
            return self.cycle_estimator.estimate_milling_cycle_time(
                surface_area_mm2=geometry.surface_area_cm2 * 100,
                material=material,
            )
        else:
            # Generic fallback: complexity-based heuristic
            complexity_map = {"SIMPLE": 5, "MODERATE": 15, "COMPLEX": 40, "VERY_COMPLEX": 90}
            return float(complexity_map.get(geometry.complexity_class, 15))
```

### 5.4 Process Rate Service

```python
class ProcessRateService:
    """Fetches current machine and labor rates from Supplier Intelligence Engine."""
    
    def get_blended_rate(self, process_type: str, location: str) -> float:
        """Returns fully-loaded machine + labor rate for a given process + location."""
        # Queries SIE for approved supplier average rates
        # Falls back to internal MACHINE_RATES if no market data
        ...
    
    def get_energy_rate(self, location: str) -> float:
        """EUR/kWh for given country (industrial tariff)."""
        rates = {
            "DE": 0.28, "PL": 0.22, "CZ": 0.21, "CN": 0.12,
            "IN": 0.10, "US": 0.18, "MX": 0.14, "TR": 0.16,
        }
        return rates.get(location, 0.25)
```
