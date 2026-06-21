# Cost Breakdown Engine — Sections 1–3

## 1. Cost Structure Model

### 1.1 Taksonomia kosztów

System rozbija całkowity koszt wytworzenia (Total Manufacturing Cost) na sześć
pierwszorzędnych składników, każdy z hierarchią podkategorii.

```
Total Manufacturing Cost (TMC)
├── MATERIAL          — surowce, półfabrykaty, zakupy kooperacyjne
│   ├── raw_material          — materiał bazowy wg BOM
│   ├── purchased_components  — elementy zakupione (łączniki, uszczelki, ...)
│   ├── surface_treatment     — galwanizacja, anodowanie, malowanie
│   └── scrap_allowance       — naddatek na odpad / braki (scrap_factor_pct)
│
├── LABOR             — robocizna bezpośrednia i pośrednia
│   ├── direct_labor          — operatorzy maszyn (czas cyklu × stawka)
│   ├── setup_labor           — przezbrojenie / nastawienie maszyny
│   ├── inspection_labor      — KJ, pomiary CMM
│   └── rework_labor          — poprawki (% od direct_labor)
│
├── MACHINE           — amortyzacja i eksploatacja środków trwałych
│   ├── depreciation          — liniowa amortyzacja (capex / life_years / oee)
│   ├── maintenance           — przeglądy, serwis (% od capex)
│   └── tooling_wear          — zużycie narzędzi skrawających
│
├── ENERGY            — media energetyczne
│   ├── electricity           — kWh × taryfa
│   ├── compressed_air        — m³ × taryfa
│   ├── coolant               — l × taryfa
│   └── heating_cooling       — kWh thermal × taryfa
│
├── TOOLING           — oprzyrządowanie dedykowane
│   ├── tool_amortization     — jednorazowy koszt narzędzia / planowana ilość
│   ├── fixture_cost          — uchwyty, sprawdziany
│   └── programming_cost      — CNC, CMM (jednorazowy, amortyzowany)
│
└── OVERHEAD + MARGIN
    ├── factory_overhead      — koszty pośrednie produkcji (rate %)
    ├── sg_and_a              — koszty ogólne i administracyjne (rate %)
    ├── rnd_allocation        — alokacja B+R (rate %)
    └── profit_margin         — zysk planowany (rate %)
```

### 1.2 CostComponentType enum

```python
from enum import Enum

class CostComponentType(str, Enum):
    # Material
    RAW_MATERIAL         = "RAW_MATERIAL"
    PURCHASED_COMPONENT  = "PURCHASED_COMPONENT"
    SURFACE_TREATMENT    = "SURFACE_TREATMENT"
    SCRAP_ALLOWANCE      = "SCRAP_ALLOWANCE"
    # Labor
    DIRECT_LABOR         = "DIRECT_LABOR"
    SETUP_LABOR          = "SETUP_LABOR"
    INSPECTION_LABOR     = "INSPECTION_LABOR"
    REWORK_LABOR         = "REWORK_LABOR"
    # Machine
    MACHINE_DEPRECIATION = "MACHINE_DEPRECIATION"
    MACHINE_MAINTENANCE  = "MACHINE_MAINTENANCE"
    TOOLING_WEAR         = "TOOLING_WEAR"
    # Energy
    ELECTRICITY          = "ELECTRICITY"
    COMPRESSED_AIR       = "COMPRESSED_AIR"
    COOLANT              = "COOLANT"
    HEATING_COOLING      = "HEATING_COOLING"
    # Tooling
    TOOL_AMORTIZATION    = "TOOL_AMORTIZATION"
    FIXTURE_COST         = "FIXTURE_COST"
    PROGRAMMING_COST     = "PROGRAMMING_COST"
    # Overhead & Margin
    FACTORY_OVERHEAD     = "FACTORY_OVERHEAD"
    SG_AND_A             = "SG_AND_A"
    RND_ALLOCATION       = "RND_ALLOCATION"
    PROFIT_MARGIN        = "PROFIT_MARGIN"

# Grupowanie do 6 kategorii pierwszorzędnych
COMPONENT_CATEGORY: dict[CostComponentType, str] = {
    CostComponentType.RAW_MATERIAL:         "MATERIAL",
    CostComponentType.PURCHASED_COMPONENT:  "MATERIAL",
    CostComponentType.SURFACE_TREATMENT:    "MATERIAL",
    CostComponentType.SCRAP_ALLOWANCE:      "MATERIAL",
    CostComponentType.DIRECT_LABOR:         "LABOR",
    CostComponentType.SETUP_LABOR:          "LABOR",
    CostComponentType.INSPECTION_LABOR:     "LABOR",
    CostComponentType.REWORK_LABOR:         "LABOR",
    CostComponentType.MACHINE_DEPRECIATION: "MACHINE",
    CostComponentType.MACHINE_MAINTENANCE:  "MACHINE",
    CostComponentType.TOOLING_WEAR:         "MACHINE",
    CostComponentType.ELECTRICITY:          "ENERGY",
    CostComponentType.COMPRESSED_AIR:       "ENERGY",
    CostComponentType.COOLANT:              "ENERGY",
    CostComponentType.HEATING_COOLING:      "ENERGY",
    CostComponentType.TOOL_AMORTIZATION:    "TOOLING",
    CostComponentType.FIXTURE_COST:         "TOOLING",
    CostComponentType.PROGRAMMING_COST:     "TOOLING",
    CostComponentType.FACTORY_OVERHEAD:     "OVERHEAD",
    CostComponentType.SG_AND_A:             "OVERHEAD",
    CostComponentType.RND_ALLOCATION:       "OVERHEAD",
    CostComponentType.PROFIT_MARGIN:        "OVERHEAD",
}
```

### 1.3 CostBreakdownResult — główny model danych

```python
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

@dataclass
class CostComponent:
    component_type:  CostComponentType
    amount_eur:      Decimal
    basis:           str           # opis podstawy kalkulacji (np. "3.2 kg × 4.50 EUR/kg")
    confidence:      float         # 0.0–1.0
    source:          str           # "BOM", "RATE_TABLE", "QUOTE", "ESTIMATE", "DEFAULT"
    assumptions:     dict          = field(default_factory=dict)

@dataclass
class CostBreakdownResult:
    breakdown_id:    UUID
    part_id:         UUID
    bom_line_id:     UUID | None
    quantity:        Decimal        # ilość dla której liczona jest kalkulacja
    currency:        str            = "EUR"

    components:      list[CostComponent]     = field(default_factory=list)

    # Agregaty pierwszorzędne
    material_eur:    Decimal        = Decimal("0")
    labor_eur:       Decimal        = Decimal("0")
    machine_eur:     Decimal        = Decimal("0")
    energy_eur:      Decimal        = Decimal("0")
    tooling_eur:     Decimal        = Decimal("0")
    overhead_eur:    Decimal        = Decimal("0")

    total_cost_eur:  Decimal        = Decimal("0")
    unit_cost_eur:   Decimal        = Decimal("0")  # total / quantity

    # Udział procentowy (wypełniany przez _compute_shares)
    material_pct:    float          = 0.0
    labor_pct:       float          = 0.0
    machine_pct:     float          = 0.0
    energy_pct:      float          = 0.0
    tooling_pct:     float          = 0.0
    overhead_pct:    float          = 0.0

    overall_confidence: float       = 0.0
    warnings:        list[str]      = field(default_factory=list)

    def _compute_shares(self) -> None:
        total = float(self.total_cost_eur) or 1.0
        self.material_pct = float(self.material_eur) / total * 100
        self.labor_pct    = float(self.labor_eur)    / total * 100
        self.machine_pct  = float(self.machine_eur)  / total * 100
        self.energy_pct   = float(self.energy_eur)   / total * 100
        self.tooling_pct  = float(self.tooling_eur)  / total * 100
        self.overhead_pct = float(self.overhead_eur) / total * 100
```

### 1.4 Modele wejściowe kalkulacji

```python
@dataclass
class MaterialInput:
    material_designation: str       # np. "S235JR", "1.4301"
    gross_weight_kg:      Decimal   # waga z naddatkiem (z BOM lub DAE)
    net_weight_kg:        Decimal
    material_price_eur_kg: Decimal | None = None  # None → lookup z SOP/rate table
    scrap_factor_pct:     Decimal   = Decimal("5")

@dataclass
class OperationInput:
    operation_code:  str            # np. "TURN", "MILL", "GRIND", "WELD", "PRESS"
    machine_id:      UUID | None
    machine_type:    str            # fallback jeśli brak machine_id
    cycle_time_s:    Decimal        # czas cyklu w sekundach
    setup_time_s:    Decimal        = Decimal("0")
    batch_size:      int            = 1
    operators:       Decimal        = Decimal("1")   # ułamkowe (np. 0.5 operator)
    tools_used:      list[str]      = field(default_factory=list)

@dataclass
class ToolingInput:
    tool_id:         UUID | None
    tool_cost_eur:   Decimal
    planned_qty:     Decimal        # ilość sztuk planowanych na narzędzie
    life_cycles:     int | None     = None  # alternatywnie: cykli życia narzędzia

@dataclass
class CostBreakdownRequest:
    part_id:         UUID
    bom_line_id:     UUID | None    = None
    quantity:        Decimal        = Decimal("1")
    location_code:   str            = "DE"   # lokalizacja → stawki robocizny / energii
    overhead_profile: str           = "DEFAULT"
    currency:        str            = "EUR"
    material:        MaterialInput | None = None
    operations:      list[OperationInput] = field(default_factory=list)
    tooling:         list[ToolingInput]   = field(default_factory=list)
    drawing_id:      UUID | None    = None   # enrichment z DAE
    supplier_offer_id: UUID | None  = None  # enrichment z SOP
```

---

## 2. Breakdown Algorithm

### 2.1 Orchestrator

```python
import asyncio
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

class CostBreakdownEngine:
    """Główny orchestrator rozbijania kosztów."""

    def __init__(
        self,
        rate_repo:     "RateRepository",
        material_svc:  "MaterialPriceService",
        machine_repo:  "MachineRepository",
        energy_svc:    "EnergyRateService",
        tooling_repo:  "ToolingRepository",
        overhead_cfg:  "OverheadConfig",
        fx_svc:        "FXRateService",
    ):
        self._rates    = rate_repo
        self._mat      = material_svc
        self._mach     = machine_repo
        self._energy   = energy_svc
        self._tooling  = tooling_repo
        self._overhead = overhead_cfg
        self._fx       = fx_svc

    async def breakdown(self, req: CostBreakdownRequest) -> CostBreakdownResult:
        result = CostBreakdownResult(
            breakdown_id=uuid4(),
            part_id=req.part_id,
            bom_line_id=req.bom_line_id,
            quantity=req.quantity,
            currency=req.currency,
        )

        # Równoległe obliczenia niezależnych składników
        (mat_components,
         labor_components,
         machine_components,
         energy_components,
         tooling_components) = await asyncio.gather(
            self._calc_material(req, result),
            self._calc_labor(req, result),
            self._calc_machine(req, result),
            self._calc_energy(req, result),
            self._calc_tooling(req, result),
        )

        all_primary = (
            mat_components + labor_components + machine_components +
            energy_components + tooling_components
        )

        # Overhead & Margin — bazuje na sumie kosztów pierwotnych
        primary_total = sum(c.amount_eur for c in all_primary)
        overhead_components = self._calc_overhead(
            primary_total, req.location_code, req.overhead_profile
        )

        result.components = all_primary + overhead_components

        # Agregaty
        for c in result.components:
            cat = COMPONENT_CATEGORY[c.component_type]
            match cat:
                case "MATERIAL": result.material_eur += c.amount_eur
                case "LABOR":    result.labor_eur    += c.amount_eur
                case "MACHINE":  result.machine_eur  += c.amount_eur
                case "ENERGY":   result.energy_eur   += c.amount_eur
                case "TOOLING":  result.tooling_eur  += c.amount_eur
                case "OVERHEAD": result.overhead_eur += c.amount_eur

        result.total_cost_eur = (
            result.material_eur + result.labor_eur + result.machine_eur +
            result.energy_eur   + result.tooling_eur + result.overhead_eur
        )
        result.unit_cost_eur = (
            result.total_cost_eur / req.quantity
        ).quantize(Decimal("0.0001"), ROUND_HALF_UP)

        result.overall_confidence = self._weighted_confidence(result.components)
        result._compute_shares()

        # Przeliczenie na żądaną walutę
        if req.currency != "EUR":
            rate = await self._fx.get_rate("EUR", req.currency)
            result = self._apply_fx(result, rate, req.currency)

        return result
```

### 2.2 Material cost

```python
    async def _calc_material(
        self, req: CostBreakdownRequest, result: CostBreakdownResult
    ) -> list[CostComponent]:
        if not req.material:
            result.warnings.append("No material input — material cost skipped.")
            return []

        m = req.material
        components: list[CostComponent] = []

        # Pobierz cenę materiału (SOP → DAE → rate table → default)
        price, source, conf = await self._mat.resolve_price(
            m.material_designation,
            supplier_offer_id=req.supplier_offer_id,
        )

        # RAW_MATERIAL = gross_weight × price_per_kg
        raw_cost = m.gross_weight_kg * price * req.quantity
        components.append(CostComponent(
            component_type=CostComponentType.RAW_MATERIAL,
            amount_eur=raw_cost,
            basis=f"{m.gross_weight_kg} kg × {price} EUR/kg × {req.quantity} pcs",
            confidence=conf,
            source=source,
            assumptions={"material": m.material_designation, "gross_weight_kg": str(m.gross_weight_kg)},
        ))

        # SCRAP_ALLOWANCE = (gross - net) / gross × raw_cost
        if m.gross_weight_kg > m.net_weight_kg:
            scrap_ratio = (m.gross_weight_kg - m.net_weight_kg) / m.gross_weight_kg
            scrap_cost  = raw_cost * scrap_ratio
            # Już wliczony w raw (gross zawiera scrap), więc tylko rejestrujemy jako informację
            components.append(CostComponent(
                component_type=CostComponentType.SCRAP_ALLOWANCE,
                amount_eur=scrap_cost,
                basis=f"Scrap ratio {float(scrap_ratio):.1%} of raw material cost",
                confidence=conf,
                source=source,
                assumptions={"scrap_factor_pct": str(m.scrap_factor_pct)},
            ))

        return components
```

### 2.3 Labor cost

```python
    async def _calc_labor(
        self, req: CostBreakdownRequest, result: CostBreakdownResult
    ) -> list[CostComponent]:
        if not req.operations:
            return []

        components: list[CostComponent] = []
        labor_rates = await self._rates.get_labor_rates(req.location_code)
        # labor_rates: {"OPERATOR": Decimal("35.00"), "SETUP": Decimal("42.00"), ...}  EUR/h

        for op in req.operations:
            # Czas cyklu × ilość / batch_size → godziny direct labor
            cycle_hours = (op.cycle_time_s * req.quantity / op.batch_size
                           / Decimal("3600"))
            dl_rate     = labor_rates.get("OPERATOR", Decimal("35"))
            dl_cost     = cycle_hours * op.operators * dl_rate

            components.append(CostComponent(
                component_type=CostComponentType.DIRECT_LABOR,
                amount_eur=dl_cost,
                basis=(f"{op.cycle_time_s}s/pcs × {req.quantity} pcs "
                       f"/ {op.batch_size} batch / 3600 × "
                       f"{op.operators} op × {dl_rate} EUR/h"),
                confidence=0.85,
                source="RATE_TABLE",
                assumptions={"operation": op.operation_code, "location": req.location_code},
            ))

            # Setup labor — jednorazowo na batch
            if op.setup_time_s > 0:
                setup_batches = (req.quantity / op.batch_size).quantize(
                    Decimal("1"), rounding="ROUND_UP")
                setup_hours = op.setup_time_s * setup_batches / Decimal("3600")
                su_rate     = labor_rates.get("SETUP", Decimal("42"))
                setup_cost  = setup_hours * su_rate

                components.append(CostComponent(
                    component_type=CostComponentType.SETUP_LABOR,
                    amount_eur=setup_cost,
                    basis=(f"{op.setup_time_s}s × {setup_batches} batches "
                           f"/ 3600 × {su_rate} EUR/h"),
                    confidence=0.80,
                    source="RATE_TABLE",
                    assumptions={"operation": op.operation_code},
                ))

        # Inspection labor — ryczałt 3–8% direct labor
        insp_rate   = await self._rates.get_inspection_rate(req.location_code)
        total_dl    = sum(c.amount_eur for c in components
                          if c.component_type == CostComponentType.DIRECT_LABOR)
        insp_cost   = total_dl * insp_rate

        components.append(CostComponent(
            component_type=CostComponentType.INSPECTION_LABOR,
            amount_eur=insp_cost,
            basis=f"{float(insp_rate):.1%} of direct labor ({total_dl:.4f} EUR)",
            confidence=0.75,
            source="RATE_TABLE",
            assumptions={"inspection_rate": str(insp_rate)},
        ))

        return components
```

### 2.4 Machine cost

```python
    async def _calc_machine(
        self, req: CostBreakdownRequest, result: CostBreakdownResult
    ) -> list[CostComponent]:
        if not req.operations:
            return []

        components: list[CostComponent] = []

        for op in req.operations:
            machine = await self._mach.resolve(op.machine_id, op.machine_type)
            if machine is None:
                result.warnings.append(
                    f"Machine not found for operation {op.operation_code} — skipped.")
                continue

            # Machine hour rate = (capex / life_years / working_hours_year) / OEE
            working_hours_year = Decimal("3500")  # 2-shift, 5d/week, ~220 days
            machine_rate = (
                machine.capex_eur / machine.life_years / working_hours_year
                / (machine.oee or Decimal("0.80"))
            )

            cycle_hours = op.cycle_time_s * req.quantity / op.batch_size / Decimal("3600")
            depr_cost   = cycle_hours * machine_rate

            components.append(CostComponent(
                component_type=CostComponentType.MACHINE_DEPRECIATION,
                amount_eur=depr_cost,
                basis=(f"{machine.capex_eur} EUR capex / {machine.life_years}y "
                       f"/ {working_hours_year}h/y / OEE {machine.oee}"),
                confidence=0.80,
                source="MACHINE_DB",
                assumptions={
                    "machine_id":  str(machine.machine_id),
                    "machine_type": machine.machine_type,
                    "cycle_hours":  str(cycle_hours),
                },
            ))

            # Maintenance = configured % of annual depreciation
            maint_rate = machine.maintenance_rate_pct / Decimal("100")
            maint_cost = depr_cost * maint_rate
            components.append(CostComponent(
                component_type=CostComponentType.MACHINE_MAINTENANCE,
                amount_eur=maint_cost,
                basis=f"{float(maint_rate):.1%} of machine depreciation",
                confidence=0.75,
                source="MACHINE_DB",
                assumptions={"maintenance_rate_pct": str(machine.maintenance_rate_pct)},
            ))

        return components
```

### 2.5 Energy cost

```python
    async def _calc_energy(
        self, req: CostBreakdownRequest, result: CostBreakdownResult
    ) -> list[CostComponent]:
        if not req.operations:
            return []

        components: list[CostComponent] = []
        energy_rates = await self._energy.get_rates(req.location_code)
        # energy_rates: {"electricity_eur_kwh": 0.18, "air_eur_m3": 0.02, ...}

        for op in req.operations:
            machine = await self._mach.resolve(op.machine_id, op.machine_type)
            if machine is None:
                continue

            cycle_hours = op.cycle_time_s * req.quantity / op.batch_size / Decimal("3600")

            # Electricity
            if machine.power_kw:
                elec_cost = machine.power_kw * cycle_hours * energy_rates["electricity_eur_kwh"]
                components.append(CostComponent(
                    component_type=CostComponentType.ELECTRICITY,
                    amount_eur=elec_cost,
                    basis=(f"{machine.power_kw} kW × {cycle_hours:.4f} h "
                           f"× {energy_rates['electricity_eur_kwh']} EUR/kWh"),
                    confidence=0.78,
                    source="ENERGY_RATE",
                    assumptions={"location": req.location_code},
                ))

            # Compressed air
            if machine.air_consumption_m3h:
                air_cost = machine.air_consumption_m3h * cycle_hours * energy_rates.get("air_eur_m3", Decimal("0.02"))
                components.append(CostComponent(
                    component_type=CostComponentType.COMPRESSED_AIR,
                    amount_eur=air_cost,
                    basis=f"{machine.air_consumption_m3h} m³/h × {cycle_hours:.4f} h",
                    confidence=0.70,
                    source="ENERGY_RATE",
                    assumptions={},
                ))

            # Coolant
            if machine.coolant_lh:
                coolant_cost = machine.coolant_lh * cycle_hours * energy_rates.get("coolant_eur_l", Decimal("0.05"))
                components.append(CostComponent(
                    component_type=CostComponentType.COOLANT,
                    amount_eur=coolant_cost,
                    basis=f"{machine.coolant_lh} l/h × {cycle_hours:.4f} h",
                    confidence=0.68,
                    source="ENERGY_RATE",
                    assumptions={},
                ))

        return components
```

### 2.6 Tooling cost

```python
    async def _calc_tooling(
        self, req: CostBreakdownRequest, result: CostBreakdownResult
    ) -> list[CostComponent]:
        if not req.tooling:
            return []

        components: list[CostComponent] = []

        for t in req.tooling:
            amort_per_piece = t.tool_cost_eur / t.planned_qty
            total_tool_cost = amort_per_piece * req.quantity

            components.append(CostComponent(
                component_type=CostComponentType.TOOL_AMORTIZATION,
                amount_eur=total_tool_cost,
                basis=(f"{t.tool_cost_eur} EUR tool / {t.planned_qty} planned pcs "
                       f"= {amort_per_piece:.4f} EUR/pcs × {req.quantity}"),
                confidence=0.85,
                source="TOOLING_DB" if t.tool_id else "ESTIMATE",
                assumptions={"tool_id": str(t.tool_id) if t.tool_id else "N/A"},
            ))

        return components
```

### 2.7 Overhead i marża

```python
    def _calc_overhead(
        self,
        primary_total: Decimal,
        location_code: str,
        profile: str,
    ) -> list[CostComponent]:
        rates = self._overhead.get_rates(location_code, profile)
        components: list[CostComponent] = []

        for comp_type, rate_key in [
            (CostComponentType.FACTORY_OVERHEAD, "factory_overhead_pct"),
            (CostComponentType.SG_AND_A,         "sg_and_a_pct"),
            (CostComponentType.RND_ALLOCATION,   "rnd_pct"),
            (CostComponentType.PROFIT_MARGIN,    "margin_pct"),
        ]:
            rate = rates.get(rate_key, Decimal("0"))
            amount = primary_total * (rate / Decimal("100"))
            components.append(CostComponent(
                component_type=comp_type,
                amount_eur=amount,
                basis=f"{rate}% of primary cost ({primary_total:.4f} EUR)",
                confidence=0.90,
                source="OVERHEAD_CONFIG",
                assumptions={"location": location_code, "profile": profile},
            ))

        return components
```

### 2.8 Ważona średnia confidence

```python
    @staticmethod
    def _weighted_confidence(components: list[CostComponent]) -> float:
        if not components:
            return 0.0
        total_amount = sum(abs(float(c.amount_eur)) for c in components)
        if total_amount == 0:
            return 0.0
        weighted = sum(
            c.confidence * abs(float(c.amount_eur)) for c in components
        )
        return round(weighted / total_amount, 4)
```

### 2.9 Priorytety źródeł danych

| Priorytet | Źródło | Confidence |
|-----------|--------|:----------:|
| 1 | `QUOTE` — cena z SOP (oferta dostawcy) | 0.95 |
| 2 | `BOM` — dane z BOM Engine (waga, operacje) | 0.90 |
| 3 | `DRAWING` — dane z DAE (masa, materiał) | 0.85 |
| 4 | `RATE_TABLE` — tabele stawek z konfiguracji | 0.75–0.85 |
| 5 | `MACHINE_DB` — dane maszyn z repozytorium | 0.78–0.85 |
| 6 | `ESTIMATE` — szacunek systemowy / parametryczny | 0.50–0.65 |
| 7 | `DEFAULT` — wartość domyślna (fallback) | 0.40 |

---

## 3. Allocation Rules

### 3.1 OverheadConfig — stawki per lokalizacja

```python
from dataclasses import dataclass

@dataclass
class LocationRates:
    location_code:       str
    # Labor EUR/h
    operator_rate_eur_h: Decimal
    setup_rate_eur_h:    Decimal
    inspection_rate_pct: Decimal  # % od direct labor
    rework_rate_pct:     Decimal  # % od direct labor
    # Energy
    electricity_eur_kwh: Decimal
    air_eur_m3:          Decimal
    coolant_eur_l:       Decimal
    # Overhead & Margin (% od sumy kosztów pierwotnych)
    factory_overhead_pct: Decimal
    sg_and_a_pct:         Decimal
    rnd_pct:              Decimal
    margin_pct:           Decimal

# Tabela domyślna (modyfikowalna przez admina przez API)
DEFAULT_LOCATION_RATES: dict[str, LocationRates] = {
    "DE": LocationRates(
        location_code="DE",
        operator_rate_eur_h  = Decimal("42.00"),
        setup_rate_eur_h     = Decimal("52.00"),
        inspection_rate_pct  = Decimal("6.0"),
        rework_rate_pct      = Decimal("2.5"),
        electricity_eur_kwh  = Decimal("0.22"),
        air_eur_m3           = Decimal("0.025"),
        coolant_eur_l        = Decimal("0.06"),
        factory_overhead_pct = Decimal("28.0"),
        sg_and_a_pct         = Decimal("12.0"),
        rnd_pct              = Decimal("3.0"),
        margin_pct           = Decimal("8.0"),
    ),
    "PL": LocationRates(
        location_code="PL",
        operator_rate_eur_h  = Decimal("18.00"),
        setup_rate_eur_h     = Decimal("22.00"),
        inspection_rate_pct  = Decimal("5.0"),
        rework_rate_pct      = Decimal("2.0"),
        electricity_eur_kwh  = Decimal("0.16"),
        air_eur_m3           = Decimal("0.018"),
        coolant_eur_l        = Decimal("0.04"),
        factory_overhead_pct = Decimal("22.0"),
        sg_and_a_pct         = Decimal("10.0"),
        rnd_pct              = Decimal("2.0"),
        margin_pct           = Decimal("7.0"),
    ),
    "CN": LocationRates(
        location_code="CN",
        operator_rate_eur_h  = Decimal("8.00"),
        setup_rate_eur_h     = Decimal("10.00"),
        inspection_rate_pct  = Decimal("8.0"),
        rework_rate_pct      = Decimal("4.0"),
        electricity_eur_kwh  = Decimal("0.10"),
        air_eur_m3           = Decimal("0.012"),
        coolant_eur_l        = Decimal("0.03"),
        factory_overhead_pct = Decimal("18.0"),
        sg_and_a_pct         = Decimal("8.0"),
        rnd_pct              = Decimal("1.0"),
        margin_pct           = Decimal("6.0"),
    ),
    "MX": LocationRates(
        location_code="MX",
        operator_rate_eur_h  = Decimal("12.00"),
        setup_rate_eur_h     = Decimal("14.00"),
        inspection_rate_pct  = Decimal("6.0"),
        rework_rate_pct      = Decimal("3.0"),
        electricity_eur_kwh  = Decimal("0.12"),
        air_eur_m3           = Decimal("0.015"),
        coolant_eur_l        = Decimal("0.04"),
        factory_overhead_pct = Decimal("20.0"),
        sg_and_a_pct         = Decimal("9.0"),
        rnd_pct              = Decimal("1.5"),
        margin_pct           = Decimal("7.0"),
    ),
    "IN": LocationRates(
        location_code="IN",
        operator_rate_eur_h  = Decimal("6.00"),
        setup_rate_eur_h     = Decimal("8.00"),
        inspection_rate_pct  = Decimal("7.0"),
        rework_rate_pct      = Decimal("3.5"),
        electricity_eur_kwh  = Decimal("0.09"),
        air_eur_m3           = Decimal("0.010"),
        coolant_eur_l        = Decimal("0.03"),
        factory_overhead_pct = Decimal("16.0"),
        sg_and_a_pct         = Decimal("8.0"),
        rnd_pct              = Decimal("1.0"),
        margin_pct           = Decimal("6.0"),
    ),
}
```

### 3.2 Overhead Profile — tryby kalkulacji

```python
class OverheadProfile(str, Enum):
    DEFAULT      = "DEFAULT"     # stawki lokalizacji bez zmian
    LEAN         = "LEAN"        # overhead × 0.80, margin × 0.90
    PREMIUM      = "PREMIUM"     # overhead × 1.10, margin × 1.25
    EXPORT       = "EXPORT"      # SG&A × 1.15 (dodatkowe koszty eksportu)
    PROTOTYPE    = "PROTOTYPE"   # tooling pełen koszt, margin × 0.50
    SERIES       = "SERIES"      # tooling amortyzowany, overhead × 0.95

PROFILE_MULTIPLIERS: dict[OverheadProfile, dict[str, float]] = {
    OverheadProfile.DEFAULT:   {"factory": 1.00, "sga": 1.00, "rnd": 1.00, "margin": 1.00},
    OverheadProfile.LEAN:      {"factory": 0.80, "sga": 0.80, "rnd": 0.80, "margin": 0.90},
    OverheadProfile.PREMIUM:   {"factory": 1.10, "sga": 1.10, "rnd": 1.10, "margin": 1.25},
    OverheadProfile.EXPORT:    {"factory": 1.00, "sga": 1.15, "rnd": 1.00, "margin": 1.00},
    OverheadProfile.PROTOTYPE: {"factory": 1.20, "sga": 1.00, "rnd": 1.50, "margin": 0.50},
    OverheadProfile.SERIES:    {"factory": 0.95, "sga": 0.95, "rnd": 1.00, "margin": 1.00},
}
```

### 3.3 Reguły alokacji kosztów maszynowych

```python
class MachineAllocationMethod(str, Enum):
    CYCLE_TIME   = "CYCLE_TIME"   # proporcjonalnie do czasu cyklu (default)
    MACHINE_RATE = "MACHINE_RATE" # stała stawka maszynogodziny [EUR/h]
    THROUGHPUT   = "THROUGHPUT"   # 1 / throughput_pcs_h
    FLAT_PER_OP  = "FLAT_PER_OP"  # ryczałt na operację

@dataclass
class MachineProfile:
    machine_id:            UUID
    machine_type:          str           # "CNC_TURNING", "MILLING_3AX", "GRINDING", ...
    capex_eur:             Decimal
    life_years:            Decimal
    oee:                   Decimal       = Decimal("0.80")
    power_kw:              Decimal | None = None
    air_consumption_m3h:   Decimal | None = None
    coolant_lh:            Decimal | None = None
    maintenance_rate_pct:  Decimal       = Decimal("3.0")
    allocation_method:     MachineAllocationMethod = MachineAllocationMethod.CYCLE_TIME
    flat_rate_eur_h:       Decimal | None = None   # używany gdy MACHINE_RATE
```

### 3.4 Reguły alokacji materiału — scrap & yield

```python
def gross_weight(net_weight_kg: Decimal, process: str) -> Decimal:
    """Oblicza wagę brutto z naddatkiem zależnym od procesu."""
    PROCESS_SCRAP: dict[str, Decimal] = {
        "TURNING":      Decimal("1.30"),  # 30% materiału usuwane
        "MILLING":      Decimal("1.25"),
        "FORGING":      Decimal("1.08"),  # 8% naddatek kucia
        "CASTING":      Decimal("1.05"),  # 5% naddatek na układy wlewowe
        "SHEET_METAL":  Decimal("1.12"),  # 12% wypad blachy
        "WELDING":      Decimal("1.02"),  # 2% elektrody / spoiwo
        "GRINDING":     Decimal("1.05"),
        "DEFAULT":      Decimal("1.10"),
    }
    factor = PROCESS_SCRAP.get(process, PROCESS_SCRAP["DEFAULT"])
    return (net_weight_kg * factor).quantize(Decimal("0.001"))
```

### 3.5 Reguły alokacji energii per lokalizacja

| Lokalizacja | Energia [EUR/kWh] | Powietrze [EUR/m³] | Chłodziwo [EUR/l] |
|-------------|:-----------------:|:-----------------:|:-----------------:|
| DE | 0.22 | 0.025 | 0.06 |
| PL | 0.16 | 0.018 | 0.04 |
| CN | 0.10 | 0.012 | 0.03 |
| MX | 0.12 | 0.015 | 0.04 |
| IN | 0.09 | 0.010 | 0.03 |

### 3.6 Quantity Break — skalowanie kosztów

Koszty stałe (tooling, setup, programming) amortyzują się przy wyższych
ilościach. System automatycznie przelicza `unit_cost_eur` dla progów ilościowych:

```python
QUANTITY_BREAKS = [1, 10, 50, 100, 250, 500, 1_000, 5_000, 10_000]

async def get_quantity_break_table(
    engine: CostBreakdownEngine,
    req: CostBreakdownRequest,
) -> list[dict]:
    rows = []
    for qty in QUANTITY_BREAKS:
        r = await engine.breakdown(req.with_quantity(qty))
        rows.append({
            "quantity":       qty,
            "total_cost_eur": float(r.total_cost_eur),
            "unit_cost_eur":  float(r.unit_cost_eur),
            "material_pct":   r.material_pct,
            "labor_pct":      r.labor_pct,
            "machine_pct":    r.machine_pct,
            "tooling_pct":    r.tooling_pct,
            "overhead_pct":   r.overhead_pct,
        })
    return rows
```
