# Cost Estimation Engine — Part 2
# Overhead Model, Setup Cost, Volume Discount, Scrap Model, Time Estimation

---

## Section 6: Overhead Model

### 6.1 Overhead Theory (Absorption Costing)

Factory overhead is the set of indirect costs that cannot be directly attributed to a specific unit
of production. The CEE uses **absorption costing** (traditional full-cost method) where overhead is
allocated to each unit based on a cost driver (machine hours or direct cost).

```
Overhead Rate (%) = Total Indirect Factory Costs / Direct Cost Base × 100

where Direct Cost Base = Material Cost + Process Cost (or Machine Hours)

Overhead Cost per Piece = (Material Cost + Process Cost) × overhead_rate_pct
```

**Overhead Components:**

| Sub-Category | Typical % of Direct Cost | Notes |
|-------------|-------------------------|-------|
| Factory depreciation | 8–15% | Machines, buildings, fixtures |
| Maintenance & repair | 4–8% | Planned + reactive |
| Utilities (electricity, gas, water) | 3–7% | Beyond direct energy |
| Indirect labor (supervisors, logistics, QA) | 6–12% | Non-direct headcount |
| Factory consumables | 2–4% | Coolants, lubricants, PPE |
| Insurance | 1–2% | Factory insurance |
| General & Administrative (G&A) | 5–10% | Management, IT, finance |
| R&D allocation | 1–3% | Engineering amortized |
| **Total overhead** | **30–61%** | Varies by industry |

**Industry benchmark overhead rates:**

| Industry | Overhead Rate Range |
|----------|-------------------|
| Aerospace machining | 80–120% |
| Automotive stamping | 40–60% |
| General machining job shop | 50–80% |
| Injection moulding | 35–55% |
| Sheet metal fabrication | 45–65% |
| Electronics assembly | 30–50% |
| Casting (die cast) | 55–75% |
| Precision grinding | 70–90% |

### 6.2 OverheadModel

```python
@dataclass
class OverheadResult:
    rate_pct:       float
    overhead_eur:   float
    ga_eur:         float
    total_eur:      float
    driver:         str     # "direct_cost" | "machine_hours"
    breakdown:      dict

class OverheadModel:
    
    # Baseline overhead rates by process class (% of direct cost)
    OVERHEAD_BY_PROCESS_CLASS = {
        "CUT":  0.65,    # machining: high capital intensity
        "MAC":  0.65,
        "FOR":  0.55,    # forming: moderate
        "JOI":  0.45,    # joining: lower capital
        "ASS":  0.35,    # assembly: labor-intensive, lower overhead
        "FIN":  0.40,    # finishing: moderate
    }
    
    # Location overhead adjustment factors
    LOCATION_OVERHEAD_FACTORS = {
        "DE": 1.00,   # baseline (high regulatory burden, high building costs)
        "PL": 0.72,
        "CZ": 0.75,
        "RO": 0.65,
        "CN": 0.55,
        "IN": 0.50,
        "MX": 0.58,
        "US": 0.90,
        "TR": 0.62,
    }
    
    # G&A rates (% of manufacturing cost)
    GA_RATES = {
        "small_shop":   0.12,   # < 50 employees
        "medium_shop":  0.09,   # 50–250
        "large_factory": 0.07,  # > 250
    }
    
    def calculate(
        self,
        direct_cost_eur: float,
        production_location: str,
        process_steps: list,
        company_size: str = "medium_shop",
    ) -> OverheadResult:
        # Derive overhead rate from process mix
        base_rate = self._derive_process_overhead_rate(process_steps)
        
        # Apply location factor
        loc_factor = self.LOCATION_OVERHEAD_FACTORS.get(production_location, 0.75)
        factory_rate = base_rate * loc_factor
        
        # G&A
        ga_rate = self.GA_RATES.get(company_size, 0.09)
        
        total_rate = factory_rate + ga_rate
        
        factory_overhead = direct_cost_eur * factory_rate
        ga_overhead      = direct_cost_eur * ga_rate
        total_overhead   = factory_overhead + ga_overhead
        
        return OverheadResult(
            rate_pct=total_rate * 100,
            overhead_eur=factory_overhead,
            ga_eur=ga_overhead,
            total_eur=total_overhead,
            driver="direct_cost",
            breakdown={
                "factory_overhead_pct": factory_rate * 100,
                "ga_pct":               ga_rate * 100,
                "location_factor":      loc_factor,
            },
        )
    
    def _derive_process_overhead_rate(self, process_steps: list) -> float:
        if not process_steps:
            return 0.55  # default
        rates = [
            self.OVERHEAD_BY_PROCESS_CLASS.get(s.process_class, 0.55)
            for s in process_steps
        ]
        return sum(rates) / len(rates)  # simple average across steps

class ActivityBasedOverheadModel:
    """
    Advanced ABC (Activity-Based Costing) overhead model.
    Allocates overhead by activity pool: setup activities, machining activities,
    inspection activities, materials handling activities.
    Used for complex multi-step parts where standard absorption costing is inaccurate.
    """
    
    ACTIVITY_POOLS = {
        "machine_setup":     12.0,   # EUR per setup event
        "material_handling": 2.50,   # EUR per kg moved
        "inspection":        0.80,   # EUR per inspection point
        "scheduling":        1.20,   # EUR per production order
        "quality_assurance": 3.50,   # EUR per process step
        "nc_programming":    0.015,  # EUR per line of NC code
    }
    
    def calculate_abc(self, process_steps: list, geometry, material) -> float:
        """Returns overhead EUR per piece using activity-based drivers."""
        overhead = 0.0
        overhead += self.ACTIVITY_POOLS["machine_setup"] * len(process_steps)
        overhead += self.ACTIVITY_POOLS["material_handling"] * (material.density_g_cm3 * geometry.net_volume_cm3 / 1000)
        overhead += self.ACTIVITY_POOLS["inspection"] * geometry.feature_count * 0.3
        overhead += self.ACTIVITY_POOLS["scheduling"]
        overhead += self.ACTIVITY_POOLS["quality_assurance"] * len(process_steps)
        return overhead
```

### 6.3 Location Cost Index

```python
LOCATION_COST_INDEX = {
    # Fully-loaded manufacturing cost relative to Germany = 100
    "DE": 100, "AT": 96, "CH": 108, "US": 88, "GB": 80,
    "PL": 52,  "CZ": 56, "HU": 50, "RO": 45, "SK": 53,
    "CN": 38,  "IN": 30, "VN": 28, "TH": 35, "MX": 42,
    "TR": 40,  "MA": 35, "TN": 32,
}

def location_cost_adjusted(base_eur: float, from_country: str, to_country: str) -> float:
    """Adjust a cost estimate from one production location to another."""
    base_idx = LOCATION_COST_INDEX.get(from_country, 100)
    target_idx = LOCATION_COST_INDEX.get(to_country, 100)
    return base_eur * (target_idx / base_idx)
```

---

## Section 7: Setup Cost Model

### 7.1 Setup Cost Theory

Setup cost is a **batch-level cost** — it is incurred once per production run regardless of
batch size. The cost-per-piece depends critically on batch size and is therefore the primary
driver of economic order quantity (EOQ) calculations.

```
Setup Cost per Piece = Total Setup Cost per Run / Batch Size

Total Setup Cost per Run =
    machine_setup_time_hr    × machine_rate_eur_hr
  + operator_setup_time_hr   × operator_rate_eur_hr
  + first_article_inspection_eur
  + fixture_change_eur
  + nc_program_load_eur
```

**SMED (Single Minute Exchange of Die) model:**
Setup time is classified into external (can be done offline) and internal (machine must stop):

```
Internal setup time (reduces with SMED maturity):
    SMED_maturity = 0 (no optimization):     setup_time_baseline
    SMED_maturity = 1 (basic external prep): setup_time × 0.70
    SMED_maturity = 2 (SMED implemented):    setup_time × 0.45
    SMED_maturity = 3 (advanced quick-change): setup_time × 0.25
```

### 7.2 SetupCostEngine

```python
@dataclass
class SetupCostResult:
    setup_time_min:      float
    machine_setup_eur:   float
    labor_setup_eur:     float
    fai_cost_eur:        float         # First Article Inspection
    fixture_cost_eur:    float
    programming_cost_eur: float
    total_setup_eur:     float
    cost_per_piece_eur:  float         # amortized over batch_size
    breakeven_batch_size: int          # where setup < 5% of unit cost

class SetupCostEngine:
    
    # Setup time benchmarks by process type (minutes per setup)
    SETUP_TIME_BENCHMARKS = {
        "CNC_LATHE":       45,     # tool presets + offset setting
        "CNC_MILL_3AXIS":  90,     # fixture + program + tool presetter
        "CNC_MILL_5AXIS":  120,    # complex fixturing
        "PRESS_BRAKE":     20,     # bend program + tooling
        "PUNCH_PRESS":     35,     # die change
        "WELDING_JIG":     30,     # fixture setup
        "INJECTION_MOULD": 240,    # mould change + purge (4 hrs)
        "LASER_CUT":       15,     # program load + focus
        "CMM_INSPECTION":  60,     # program + fixture
        "GRINDING":        75,     # wheel dress + offset
        "EDM_WIRE":        90,     # wire thread + program
    }
    
    # First Article Inspection costs (EUR, by complexity)
    FAI_COSTS = {
        "SIMPLE":       150,
        "MODERATE":     450,
        "COMPLEX":      900,
        "VERY_COMPLEX": 2000,
    }
    
    # NC programming costs (EUR, by complexity)
    PROGRAMMING_COSTS = {
        "SIMPLE":       80,
        "MODERATE":     300,
        "COMPLEX":      750,
        "VERY_COMPLEX": 2000,
    }
    
    def __init__(self, smed_maturity: int = 1):
        self.smed_maturity = smed_maturity
        self.smed_factors  = [1.0, 0.70, 0.45, 0.25]
    
    def calculate(
        self,
        process_steps: list,
        batch_size: int,
        location: "ProductionLocation",
        geometry: "ProductGeometry" = None,
    ) -> SetupCostResult:
        smed_factor = self.smed_factors[min(self.smed_maturity, 3)]
        
        total_setup_min   = 0.0
        machine_setup_eur = 0.0
        labor_setup_eur   = 0.0
        fixture_eur       = 0.0
        
        for step in process_steps:
            # Setup time for this step
            if step.setup_time_min > 0:
                raw_setup = step.setup_time_min
            else:
                raw_setup = float(self.SETUP_TIME_BENCHMARKS.get(step.process_type, 45))
            
            adjusted_setup = raw_setup * smed_factor
            total_setup_min += adjusted_setup
            
            setup_hrs = adjusted_setup / 60.0
            machine_rate = ProcessCostEngine.MACHINE_RATES.get(step.machine_type, 70)
            # Apply location factor (capital cost is ~60% of rate, labor 40%)
            machine_rate_adj = machine_rate * (0.60 + 0.40 * location.labor_rate_eur_hr / 45.0)
            machine_setup_eur += setup_hrs * machine_rate_adj
            labor_setup_eur   += setup_hrs * location.labor_rate_eur_hr
            fixture_eur       += self._fixture_cost(step) / 500  # amortized over 500 runs
        
        # FAI (once per new part number, amortized over first-year volume)
        complexity = geometry.complexity_class if geometry else "MODERATE"
        fai_total  = float(self.FAI_COSTS.get(complexity, 450))
        fai_per_piece = fai_total / max(batch_size, 1)
        
        # NC programming
        prog_total   = float(self.PROGRAMMING_COSTS.get(complexity, 300))
        prog_per_run = prog_total / 20.0   # program reused for ~20 runs
        
        total_setup_eur = machine_setup_eur + labor_setup_eur + fixture_eur + prog_per_run
        cost_per_piece  = (total_setup_eur + fai_per_piece) / max(batch_size, 1)
        
        # Breakeven: find batch size where setup_eur/batch = 5% of process_cost
        # (Simplified: rule of thumb)
        breakeven = int(total_setup_eur / 0.50) + 1  # assuming €0.50/piece process cost
        
        return SetupCostResult(
            setup_time_min=total_setup_min,
            machine_setup_eur=machine_setup_eur,
            labor_setup_eur=labor_setup_eur,
            fai_cost_eur=fai_per_piece,
            fixture_cost_eur=fixture_eur,
            programming_cost_eur=prog_per_run / max(batch_size, 1),
            total_setup_eur=total_setup_eur,
            cost_per_piece_eur=cost_per_piece,
            breakeven_batch_size=breakeven,
        )
    
    @staticmethod
    def _fixture_cost(step) -> float:
        """Estimate fixture/jig cost by process type."""
        costs = {
            "CNC_MILL_3AXIS": 3000,
            "CNC_MILL_5AXIS": 6000,
            "WELDING_JIG":    2000,
            "CMM_INSPECTION": 1500,
        }
        return float(costs.get(step.process_type, 500))


class ToolingCostEngine:
    """
    Amortizes capital tooling investment (molds, dies) over expected production volume.
    Separate from consumable tooling wear (which is in ProcessCostEngine).
    """
    
    # Tooling investment benchmarks
    TOOLING_COSTS = {
        "INJECTION_MOULD_SIMPLE":   15_000,
        "INJECTION_MOULD_COMPLEX":  80_000,
        "DIE_CAST_MOULD":           40_000,
        "PRESS_DIE_BLANKING":        8_000,
        "PRESS_DIE_PROGRESSIVE":    35_000,
        "FORGING_DIE":              25_000,
        "WELDING_FIXTURE":           3_000,
        "CASTING_PATTERN":           5_000,
        "EXTRUSION_DIE":             2_500,
        "CNC_FIXTURE_SIMPLE":          800,
        "CNC_FIXTURE_COMPLEX":        4_000,
    }
    
    # Expected tooling life (pieces before replacement)
    TOOLING_LIFE = {
        "INJECTION_MOULD_SIMPLE":   500_000,
        "INJECTION_MOULD_COMPLEX":  300_000,
        "DIE_CAST_MOULD":           100_000,
        "PRESS_DIE_BLANKING":       800_000,
        "PRESS_DIE_PROGRESSIVE":  1_000_000,
        "FORGING_DIE":               50_000,
        "WELDING_FIXTURE":           20_000,
        "CNC_FIXTURE_SIMPLE":        50_000,
        "CNC_FIXTURE_COMPLEX":       30_000,
    }
    
    def calculate(
        self,
        process_steps: list,
        annual_volume: int,
        geometry: "ProductGeometry",
    ):
        total_investment = 0.0
        cost_per_piece   = 0.0
        
        for step in process_steps:
            tooling_key  = self._infer_tooling_type(step, geometry)
            investment   = float(self.TOOLING_COSTS.get(tooling_key, 0))
            life_pieces  = self.TOOLING_LIFE.get(tooling_key, 100_000)
            amort        = investment / life_pieces
            
            total_investment += investment
            cost_per_piece   += amort
        
        return type('ToolingResult', (), {
            'total_investment_eur': total_investment,
            'cost_per_piece_eur':   cost_per_piece,
        })()
    
    def _infer_tooling_type(self, step, geometry) -> str:
        if step.process_type == "INJECTION_MOULDING":
            return "INJECTION_MOULD_COMPLEX" if geometry.complexity_class in ("COMPLEX","VERY_COMPLEX") \
                   else "INJECTION_MOULD_SIMPLE"
        if step.process_type in ("BLANKING", "PUNCHING"):
            return "PRESS_DIE_BLANKING"
        if step.process_type == "PROGRESSIVE_STAMPING":
            return "PRESS_DIE_PROGRESSIVE"
        if step.process_type == "FORGING":
            return "FORGING_DIE"
        if "CNC" in step.process_type:
            return "CNC_FIXTURE_COMPLEX" if geometry.complexity_class in ("COMPLEX","VERY_COMPLEX") \
                   else "CNC_FIXTURE_SIMPLE"
        return ""
```

---

## Section 8: Volume Discount Model

### 8.1 Theory: Experience Curve (Wright's Law)

Every time cumulative production doubles, unit cost decreases by a fixed percentage
(the **learning rate**). This is the empirical **Wright's Law** (1936):

```
C(n) = C(1) × n^(log(1 - learning_rate) / log(2))

where:
  C(n)          = unit cost at cumulative volume n
  C(1)          = unit cost of first unit
  learning_rate = fraction of cost reduction per doubling (typically 0.10–0.25)
  exponent      = b = log(1 - LR) / log(2)

Example: LR = 0.15 (15% reduction per doubling)
  b = log(0.85) / log(2) = -0.234
  C(1000) = C(1) × 1000^(-0.234) = C(1) × 0.178 → 82.2% reduction
```

**Typical learning rates by process:**

| Process | Learning Rate | Comment |
|---------|--------------|---------|
| Assembly (manual) | 15–25% | High labor content |
| CNC machining | 5–12% | Low labor, high automation |
| Injection moulding | 3–8% | Cycle time essentially fixed |
| Welding | 10–18% | Operator-driven |
| Electronics assembly | 15–20% | Driven by procurement + design |
| Aerospace composite | 20–25% | Complex, high labor |

### 8.2 VolumeDiscountModel

```python
import math

@dataclass
class VolumeDiscountResult:
    annual_volume:     int
    discount_rate:     float    # 0–1 fractional discount
    discount_eur:      float    # EUR saved per piece
    adjusted_cost_eur: float
    curve_used:        str      # "experience_curve" | "price_break" | "none"
    learning_rate:     float

class VolumeDiscountModel:
    """
    Three-tier volume model:
    1. Price break tiers (discrete steps from supplier quote structures)
    2. Experience curve (continuous, based on Wright's Law)
    3. Combined (price breaks override experience curve above certain volumes)
    """
    
    # Price break tiers (% discount from base price) by annual volume
    PRICE_BREAKS = [
        (1,      0.0),
        (50,     0.03),    # 3% for 50+ pcs/year
        (250,    0.07),    # 7% for 250+ pcs/year
        (1_000,  0.12),    # 12% for 1K+ pcs/year
        (5_000,  0.17),    # 17% for 5K+ pcs/year
        (25_000, 0.22),    # 22% for 25K+ pcs/year
        (100_000,0.28),    # 28% for 100K+ pcs/year
    ]
    
    # Learning rates by dominant process class
    LEARNING_RATES = {
        "CUT":  0.08,
        "MAC":  0.08,
        "FOR":  0.12,
        "JOI":  0.15,
        "ASS":  0.20,
        "FIN":  0.10,
    }
    
    def __init__(self, mode: str = "combined"):
        self.mode = mode   # "price_break" | "experience_curve" | "combined"
    
    def calculate(
        self,
        annual_volume: int,
        base_cost_eur: float,
        process_steps: list = None,
        reference_volume: int = 100,
    ) -> VolumeDiscountResult:
        if self.mode == "price_break":
            discount_rate = self._price_break_discount(annual_volume)
            curve = "price_break"
        elif self.mode == "experience_curve":
            lr = self._derive_learning_rate(process_steps)
            discount_rate = self._experience_curve_discount(annual_volume, reference_volume, lr)
            curve = "experience_curve"
        else:  # combined: use higher of the two
            pb_rate = self._price_break_discount(annual_volume)
            lr      = self._derive_learning_rate(process_steps)
            ec_rate = self._experience_curve_discount(annual_volume, reference_volume, lr)
            discount_rate = max(pb_rate, ec_rate)
            curve = "combined"
        
        discount_eur     = base_cost_eur * discount_rate
        adjusted_cost    = base_cost_eur * (1 - discount_rate)
        lr_used          = self._derive_learning_rate(process_steps) if process_steps else 0.10
        
        return VolumeDiscountResult(
            annual_volume=annual_volume,
            discount_rate=discount_rate,
            discount_eur=discount_eur,
            adjusted_cost_eur=adjusted_cost,
            curve_used=curve,
            learning_rate=lr_used,
        )
    
    def _price_break_discount(self, volume: int) -> float:
        discount = 0.0
        for min_vol, disc in self.PRICE_BREAKS:
            if volume >= min_vol:
                discount = disc
        return discount
    
    def _experience_curve_discount(
        self,
        current_volume: int,
        reference_volume: int,
        learning_rate: float,
    ) -> float:
        """Returns fractional cost reduction vs reference volume."""
        if current_volume <= reference_volume:
            return 0.0
        b = math.log(1 - learning_rate) / math.log(2)
        cost_ratio = (current_volume / reference_volume) ** b
        return max(0.0, min(1 - cost_ratio, 0.50))  # cap at 50% reduction
    
    def _derive_learning_rate(self, process_steps: list) -> float:
        if not process_steps:
            return 0.10
        rates = [self.LEARNING_RATES.get(s.process_class, 0.10) for s in process_steps]
        # Weight by cycle time if available
        return sum(rates) / len(rates)


class LogisticsModel:
    """Outbound freight and packaging cost model."""
    
    # Base freight rates EUR/kg by Incoterm and distance zone
    FREIGHT_RATES = {
        "EU_SHORT": 0.35,    # < 500 km intra-EU
        "EU_LONG":  0.45,    # 500–2000 km intra-EU
        "INTERCONT_AIR":  8.0,
        "INTERCONT_SEA":  0.25,
        "ROAD_LTL":       0.55,
        "ROAD_FTL":       0.20,
    }
    
    MIN_SHIPMENT_CHARGE = 15.0  # EUR per shipment
    
    def calculate(self, weight_kg: float, production_location: str, incoterm: str) -> object:
        zone = self._distance_zone(production_location)
        rate = self.FREIGHT_RATES.get(zone, 0.45)
        freight = max(weight_kg * rate, self.MIN_SHIPMENT_CHARGE / 100)
        packaging = max(0.10, weight_kg * 0.05)
        return type('LogisticsResult', (), {
            'freight_eur': freight,
            'packaging_eur': packaging,
            'total_eur': freight + packaging,
        })()
    
    def _distance_zone(self, country: str) -> str:
        eu_short = {"DE","AT","CH","NL","BE","FR","LU"}
        eu_long  = {"PL","CZ","HU","RO","SK","SI","HR","IT","ES","PT"}
        if country in eu_short:
            return "EU_SHORT"
        if country in eu_long:
            return "EU_LONG"
        return "INTERCONT_SEA"
```

---

## Section 9: Scrap Model

### 9.1 Theory: Process Capability → Scrap Rate

The scrap rate is derived from **process capability index Cpk** using the normal distribution:

```
Cpk = min(USL - μ, μ - LSL) / (3σ)

Scrap Rate = P(X < LSL) + P(X > USL)
           = Φ(−3·Cpk) + Φ(−3·Cpk) × 2 (for bilateral tolerance)
           ≈ 2 × Φ(−3·Cpk)

where Φ = standard normal CDF

Cpk → Scrap Rate (approximate):
  Cpk = 0.67  → scrap ≈ 4.6%  (1-sigma process)
  Cpk = 1.00  → scrap ≈ 0.27% (2700 PPM)
  Cpk = 1.33  → scrap ≈ 63 PPM
  Cpk = 1.67  → scrap ≈ 0.57 PPM
  Cpk = 2.00  → scrap ≈ 0.001 PPM (near-zero)
```

### 9.2 ScrapModel

```python
import scipy.stats as stats

@dataclass
class ScrapResult:
    cpk_estimated:      float
    scrap_rate_pct:     float
    rework_rate_pct:    float
    effective_scrap_rate_pct: float   # net after rework recovery
    scrap_cost_eur:     float         # per good piece
    inspection_cost_eur: float
    rework_cost_eur:    float
    total_quality_cost_eur: float

class ScrapModel:
    """
    Estimates scrap and rework costs from process capability.
    
    Scrap amplification effect: scrap occurs AFTER value has been added.
    Therefore scrap cost = (material + process cost of scrapped piece) × scrap_rate / (1 - scrap_rate)
    """
    
    # Baseline Cpk by IT tolerance grade (assumes well-maintained machines)
    CPK_BY_IT_GRADE = {
        4:  1.67,   # extremely tight — special process
        5:  1.50,
        6:  1.33,   # automotive standard (Ppk ≥ 1.33)
        7:  1.25,
        8:  1.10,
        9:  1.00,
        10: 0.90,
        11: 0.80,
        12: 0.70,
    }
    
    # Rework fraction (% of scrapped parts that can be reworked)
    REWORK_FRACTION = {
        "SIMPLE":       0.60,
        "MODERATE":     0.45,
        "COMPLEX":      0.30,
        "VERY_COMPLEX": 0.15,
    }
    
    # Inspection cost per piece by method (EUR)
    INSPECTION_COSTS = {
        "VISUAL":           0.50,
        "DIMENSIONAL_100PCT": 4.00,
        "CMM_SAMPLING":     1.50,
        "CMM_100PCT":       6.00,
        "NDT_UT":          12.00,
        "NDT_RT":          18.00,
        "FUNCTIONAL_TEST":  8.00,
    }
    
    def calculate(
        self,
        pre_scrap_cost_eur: float,
        process_steps: list,
        geometry: "ProductGeometry",
        inspection_method: str = "CMM_SAMPLING",
    ) -> ScrapResult:
        # Estimate effective Cpk from process steps
        cpk = self._estimate_cpk(process_steps)
        
        # Compute scrap rate from Cpk
        raw_scrap_rate = self._cpk_to_scrap_rate(cpk)
        
        # Complexity modifier (complex parts harder to produce within tolerance)
        complexity_factor = {
            "SIMPLE": 1.0, "MODERATE": 1.3, "COMPLEX": 1.8, "VERY_COMPLEX": 2.5
        }.get(geometry.complexity_class if geometry else "MODERATE", 1.3)
        scrap_rate = min(raw_scrap_rate * complexity_factor, 0.30)  # cap at 30%
        
        # Rework recovery
        rework_fraction = self.REWORK_FRACTION.get(
            geometry.complexity_class if geometry else "MODERATE", 0.40
        )
        rework_rate = scrap_rate * rework_fraction
        effective_scrap_rate = scrap_rate * (1 - rework_fraction)
        
        # Scrap cost: applied as multiplier on pre-scrap cost
        # To produce 1 good piece at scrap_rate s: need 1/(1-s) pieces started
        pieces_needed = 1.0 / (1.0 - effective_scrap_rate) if effective_scrap_rate < 1.0 else 2.0
        scrap_cost = pre_scrap_cost_eur * (pieces_needed - 1.0)
        
        # Rework cost: half of process cost (re-runs affected operations only)
        rework_cost = pre_scrap_cost_eur * rework_rate * 0.50
        
        # Inspection cost
        inspection_cost = self.INSPECTION_COSTS.get(inspection_method, 1.50)
        
        return ScrapResult(
            cpk_estimated=cpk,
            scrap_rate_pct=scrap_rate * 100,
            rework_rate_pct=rework_rate * 100,
            effective_scrap_rate_pct=effective_scrap_rate * 100,
            scrap_cost_eur=scrap_cost,
            inspection_cost_eur=inspection_cost,
            rework_cost_eur=rework_cost,
            total_quality_cost_eur=scrap_cost + rework_cost + inspection_cost,
        )
    
    def _estimate_cpk(self, process_steps: list) -> float:
        """Use tightest IT grade as limiting Cpk."""
        if not process_steps:
            return 1.00
        it_grades = [s.tolerance_it for s in process_steps]
        tightest_it = min(it_grades)
        return self.CPK_BY_IT_GRADE.get(tightest_it, 1.00)
    
    @staticmethod
    def _cpk_to_scrap_rate(cpk: float) -> float:
        """Convert Cpk to scrap rate using normal distribution."""
        # For bilateral tolerance: P(defect) = 2 × Φ(-3 × Cpk)
        z = -3.0 * cpk
        return 2.0 * stats.norm.cdf(z)
```

---

## Section 10: Time Estimation Model

### 10.1 Time Estimation Framework

Accurate time estimation is the foundation of process cost accuracy. The CEE uses a
**three-tier approach**:

1. **Direct input**: process step provides explicit `cycle_time_sec` → use as-is
2. **Analytical model**: geometry-driven formulas (MRR, bending, welding length)
3. **ML prediction**: trained regression model on historical cycle time data

```
Priority: Direct Input > Analytical Model > ML Prediction > Expert Default
```

### 10.2 Analytical Time Models

```python
import math

class TimeEstimationEngine:
    """
    Analytical and ML-hybrid time estimation for common manufacturing processes.
    All times returned in minutes.
    """
    
    def estimate(self, step: "ProcessStep", geometry: "ProductGeometry",
                 material: "MaterialSpec") -> float:
        """Dispatch to appropriate analytical model or return step.cycle_time_sec/60."""
        if step.cycle_time_sec > 0:
            return step.cycle_time_sec / 60.0
        
        dispatch = {
            "TURNING":              self._turning_time,
            "MILLING":              self._milling_time,
            "DRILLING":             self._drilling_time,
            "GRINDING_CYLINDRICAL": self._cylindrical_grinding_time,
            "GRINDING_SURFACE":     self._surface_grinding_time,
            "BENDING":              self._bending_time,
            "LASER_CUTTING":        self._laser_cut_time,
            "WELDING_MIG":          self._welding_time,
            "WELDING_TIG":          self._welding_time,
            "INJECTION_MOULDING":   self._injection_moulding_time,
        }
        
        fn = dispatch.get(step.process_type)
        if fn:
            return fn(step, geometry, material)
        
        # Fallback: complexity heuristic
        return self._complexity_heuristic(geometry)
    
    # ─── Turning ────────────────────────────────────────────────────────────
    def _turning_time(self, step, geometry, material) -> float:
        D = geometry.bounding_box_mm[1]  # diameter
        L = geometry.bounding_box_mm[0]  # length
        v_c = self._cutting_speed(material, "TURNING")  # m/min
        n_rpm = (v_c * 1000) / (math.pi * D)
        f = 0.20  # mm/rev (feed)
        t_cut = (L / (f * n_rpm)) + (5.0 / (f * n_rpm))   # + approach
        t_idle = 0.5  # tool change, indexing
        return round(t_cut + t_idle, 2)
    
    # ─── Milling ────────────────────────────────────────────────────────────
    def _milling_time(self, step, geometry, material) -> float:
        A = geometry.surface_area_cm2 * 100  # mm²
        D_tool = 16.0  # mm
        ae = D_tool * 0.60  # step-over
        v_c = self._cutting_speed(material, "MILLING")
        n = (v_c * 1000) / (math.pi * D_tool)
        fz = 0.08 * step.automation_level + 0.04   # mm/tooth
        f_mm_min = fz * 4 * n
        path_mm = A / ae * (geometry.feature_count + 1) * 2
        return round(path_mm / f_mm_min + 1.5, 2)
    
    # ─── Drilling ────────────────────────────────────────────────────────────
    def _drilling_time(self, step, geometry, material) -> float:
        n_holes = max(geometry.feature_count, 1)
        D_drill = 6.0    # mm assumed
        depth   = 20.0   # mm assumed
        v_c  = self._cutting_speed(material, "DRILLING")
        n    = (v_c * 1000) / (math.pi * D_drill)
        f    = 0.10  # mm/rev
        t_per_hole = (depth + 3.0) / (f * n)
        return round(t_per_hole * n_holes + 1.0, 2)
    
    # ─── Surface grinding ───────────────────────────────────────────────────
    def _surface_grinding_time(self, step, geometry, material) -> float:
        A = geometry.surface_area_cm2 * 100  # mm²
        wheel_width = 25.0  # mm
        passes = 4 + (8 - step.tolerance_it) * 2  # finer tolerance = more passes
        v_table = 15_000  # mm/min
        path = A / wheel_width * passes
        return round(path / v_table + 2.0, 2)
    
    # ─── Cylindrical grinding ───────────────────────────────────────────────
    def _cylindrical_grinding_time(self, step, geometry, material) -> float:
        D = geometry.bounding_box_mm[1]
        L = geometry.bounding_box_mm[0]
        v_w = 25.0  # m/min workpiece surface speed
        n_w = (v_w * 1000) / (math.pi * D)
        f_a = 0.15  # mm/rev axial feed
        passes = 6 + (8 - step.tolerance_it)
        t = passes * L / (f_a * n_w)
        return round(t + 2.0, 2)
    
    # ─── Bending ────────────────────────────────────────────────────────────
    def _bending_time(self, step, geometry, material) -> float:
        n_bends = max(geometry.feature_count, 1)
        t_per_bend = 0.5  # min per bend (auto back gauge)
        t_load = 1.0      # load/unload
        return round(t_per_bend * n_bends + t_load, 2)
    
    # ─── Laser cutting ──────────────────────────────────────────────────────
    def _laser_cut_time(self, step, geometry, material) -> float:
        # Perimeter estimation from surface area
        perimeter_mm = 4 * math.sqrt(geometry.surface_area_cm2 * 100)
        # Cutting speed by material and power (6kW laser assumed)
        speeds = {
            "steel": {"1mm": 10000, "3mm": 6000, "6mm": 2500, "12mm": 900},
            "aluminum": {"3mm": 8000, "6mm": 3500},
        }
        mat_grp = material.material_group.lower()
        # Approximate thickness from bounding box and density
        thickness_mm = min(geometry.bounding_box_mm) if geometry.bounding_box_mm else 3.0
        speed_mm_min = 6000 * material.machinability_index  # default 6m/min
        t_cut  = perimeter_mm / speed_mm_min
        t_pier = geometry.feature_count * 0.01  # piercing time
        return round(t_cut + t_pier + 0.5, 2)
    
    # ─── Welding ────────────────────────────────────────────────────────────
    def _welding_time(self, step, geometry, material) -> float:
        # Weld length estimation: assume 1/3 of bounding box perimeter
        perimeter = sum(geometry.bounding_box_mm) * 0.67
        # Travel speed: 300–500 mm/min for MIG, 100–200 for TIG
        if "TIG" in step.process_type:
            speed = 150.0
        else:
            speed = 350.0
        t_weld = perimeter / speed
        t_prep = 2.0  # cleaning, tacking
        t_cool = 1.0  # inter-pass cooling
        return round(t_weld + t_prep + t_cool, 2)
    
    # ─── Injection moulding ─────────────────────────────────────────────────
    def _injection_moulding_time(self, step, geometry, material) -> float:
        """
        Injection moulding cycle time:
        t_cycle = t_fill + t_pack + t_cool + t_eject + t_open_close
        """
        V_shot_cm3 = geometry.net_volume_cm3 * 1.05  # + runner
        t_fill   = V_shot_cm3 * 0.005      # ~0.005 min/cm³
        t_pack   = 3.0
        # Cooling time: Wallace & Throne: t_cool ≈ (s²/α) × ln(π/4 × (T_melt-T_mould)/(T_eject-T_mould))
        # Simplified: proportional to square of wall thickness
        wall_mm  = min(geometry.bounding_box_mm) * 0.3 if geometry.bounding_box_mm else 2.0
        t_cool   = (wall_mm ** 2) * 0.3
        t_eject  = 2.0
        t_open   = 1.5
        return round(t_fill + t_pack + t_cool + t_eject + t_open, 2)
    
    # ─── Fallback ────────────────────────────────────────────────────────────
    @staticmethod
    def _complexity_heuristic(geometry) -> float:
        table = {"SIMPLE": 3.0, "MODERATE": 12.0, "COMPLEX": 35.0, "VERY_COMPLEX": 80.0}
        return table.get(getattr(geometry, 'complexity_class', 'MODERATE'), 12.0)
    
    @staticmethod
    def _cutting_speed(material: "MaterialSpec", process_type: str) -> float:
        """Cutting speed (m/min) based on machinability index."""
        base_speeds = {
            "TURNING":  120.0,
            "MILLING":  100.0,
            "DRILLING":  60.0,
        }
        base = base_speeds.get(process_type, 80.0)
        return base * material.machinability_index


class MonteCarloUncertaintyEngine:
    """
    Monte Carlo simulation (10K samples) for uncertainty quantification.
    Each cost driver is modeled as a random variable with specified distribution.
    """
    
    import numpy as np  # type: ignore
    
    def run(
        self,
        inp: "EstimationInput",
        cbs: "CostBreakdownStructure",
        n_samples: int = 10_000,
    ) -> "MCResult":
        import numpy as np
        rng = np.random.default_rng(42)
        
        # Material price uncertainty: log-normal, σ = 10% of mean
        mat_price_samples = rng.lognormal(
            mean=0.0, sigma=0.10, size=n_samples
        ) * cbs.total_material_cost
        
        # Cycle time uncertainty: normal, σ = 15% of mean
        cycle_samples = rng.normal(
            loc=1.0, scale=0.15, size=n_samples
        ) * cbs.total_process_cost
        
        # Scrap rate uncertainty: beta distribution
        scrap_mean = cbs.scrap_rate_pct / 100
        scrap_var  = (scrap_mean * 0.5) ** 2
        α = scrap_mean * ((scrap_mean * (1 - scrap_mean) / scrap_var) - 1)
        β = (1 - scrap_mean) * ((scrap_mean * (1 - scrap_mean) / scrap_var) - 1)
        α, β = max(α, 0.1), max(β, 0.1)
        scrap_samples = rng.beta(α, β, size=n_samples) * \
                       (cbs.total_material_cost + cbs.total_process_cost)
        
        # Overhead uncertainty: ±5% absolute
        overhead_samples = rng.normal(
            loc=cbs.overhead_cost, scale=cbs.overhead_cost * 0.05, size=n_samples
        )
        
        # Total unit cost distribution
        total_samples = (
            mat_price_samples + cycle_samples + scrap_samples + overhead_samples
            + cbs.tooling_cost_per_piece + cbs.setup_cost_per_piece + cbs.total_logistics
        )
        total_samples *= (1.0 + cbs.margin_pct)
        
        p10  = float(np.percentile(total_samples, 10))
        p50  = float(np.percentile(total_samples, 50))
        p90  = float(np.percentile(total_samples, 90))
        
        return type('MCResult', (), {
            'p10': p10,
            'p50': p50,
            'p90': p90,
            'spread_pct': (p90 - p10) / p50 * 100 if p50 > 0 else 0,
            'std_dev': float(total_samples.std()),
            'cv': float(total_samples.std() / total_samples.mean()),
        })()
```
