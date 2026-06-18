# Manufacturing Process Engine — Modele Kosztowe

## 10. Setup Cost Model

### Definicja

Koszt nastawu (setup) to jednorazowy koszt przygotowania maszyny, narzędzi i stanowiska pracy do produkcji danego zlecenia lub partii. Jest amortyzowany na partię.

### Składniki kosztu nastawu

```
Koszt_nastawu = Czas_nastawu × (Stawka_maszyny_min + Stawka_operatora_min)
              + Koszt_programowania_CNC (jeśli dotyczy)
              + Koszt_mocowań_i_oprzyrządowania
              + Koszt_materiałów_próbnych (first article)
              + Koszt_kontroli_nastawczej
```

### Encja: SetupCostModel

```
SetupCostModel
  setup_cost_id             UUID PK
  process_type_code         VARCHAR(30)
  machine_id                UUID FK (opcjonalnie)
  setup_time_min            NUMERIC(8,2)     -- podstawowy czas nastawu
  setup_time_per_fixture_min NUMERIC(8,2)    -- czas/szt. mocowania
  cnc_programming_time_min  NUMERIC(8,2)     -- czas programowania (CAM)
  first_article_parts       SMALLINT         -- sztuki na first article
  first_article_scrap_rate  NUMERIC(6,3)     -- % złomu na nastawie
  fixture_cost_eur          NUMERIC(10,4)    -- amortyzacja przyrządu
  fixture_life_setups       INTEGER          -- trwałość przyrządu
  operator_skill_required   ENUM(BASIC,STANDARD,EXPERT)
  setup_complexity          ENUM(SIMPLE,STANDARD,COMPLEX,VERY_COMPLEX)
  min_batch_for_amortization INTEGER          -- min. partia ekonomiczna
  valid_from                DATE
  valid_to                  DATE
```

### Formuła kosztu nastawu per sztuka

```python
def setup_cost_per_piece(setup: SetupCostModel,
                          machine: Machine,
                          operator: Operator,
                          batch_size: int) -> dict:
    """
    Amortizes setup cost over batch size.
    """
    # Machine cost during setup
    machine_rate_min = machine.hourly_rate / 60
    machine_setup_cost = setup.setup_time_min * machine_rate_min

    # Operator cost during setup
    op_rate_min = operator_cost_per_minute(operator)
    operator_setup_cost = setup.setup_time_min * op_rate_min

    # CNC programming (amortized over batch)
    programming_cost = (setup.cnc_programming_time_min * op_rate_min
                        if machine.is_cnc else 0)

    # First article cost
    fa_material_cost = (setup.first_article_parts
                        * setup.first_article_scrap_rate / 100
                        * machine.hourly_rate / 60 * setup.cycle_time_min)

    # Fixture amortization per setup
    fixture_cost_per_setup = (setup.fixture_cost_eur / setup.fixture_life_setups
                              if setup.fixture_life_setups > 0 else 0)

    total_setup_cost = (machine_setup_cost + operator_setup_cost
                        + programming_cost + fa_material_cost
                        + fixture_cost_per_setup)

    return {
        'total_setup_cost_eur':     round(total_setup_cost, 4),
        'setup_cost_per_piece_eur': round(total_setup_cost / batch_size, 6),
        'machine_setup_cost':       round(machine_setup_cost, 4),
        'operator_setup_cost':      round(operator_setup_cost, 4),
        'programming_cost':         round(programming_cost, 4),
        'fixture_cost_per_setup':   round(fixture_cost_per_setup, 4),
        'batch_size':               batch_size,
        'setup_time_min':           setup.setup_time_min,
    }
```

### Typowe czasy nastawu per proces

| Proces | Czas nastawu (prosty) | Czas nastawu (złożony) | Kto nastawia |
|--------|----------------------|------------------------|--------------|
| Laser (nowy program) | 15 min | 45 min | Operator STANDARD |
| Laser (znany program) | 5 min | 15 min | Operator BASIC |
| CNC frezowanie (3D) | 30 min | 120 min | Operator EXPERT |
| CNC toczenie | 20 min | 60 min | Operator STANDARD |
| Prasa krawędziowa | 10 min | 45 min | Operator STANDARD |
| Spawanie (ustawienie) | 20 min | 90 min | Spawacz |
| Malowanie proszkowe | 30 min | 90 min | Operator STANDARD |
| Anodowanie | 45 min | 120 min | Chemik/Operator |

---

## 11. Runtime Cost Model

### Definicja

Koszt wykonania (runtime) to koszt bezpośredni operacji: maszyna + operator + narzędzia, naliczany za czas cyklu × liczba sztuk.

### Składniki kosztu wykonania

```
Koszt_runtime = Czas_cyklu × Stawka_maszyny
              + Czas_cyklu × Stawka_operatora
              + Koszt_narzędzi_per_operacja
              + Koszt_mediów_procesowych (gaz, chłodziwo)
              + Koszt_oprzyrządowania_zużywalnego
```

### Encja: RuntimeCostModel

```
RuntimeCostModel
  runtime_cost_id             UUID PK
  process_type_code           VARCHAR(30)
  machine_id                  UUID FK
  cycle_time_sec              NUMERIC(10,4)        -- idealny czas cyklu
  setup_piece_time_sec        NUMERIC(8,4)         -- czas podania/zabrania
  machine_hourly_rate_eur     NUMERIC(10,4)        -- stawka maszynogodziny
  operator_count              NUMERIC(4,2)         -- liczba operatorów (0.5=1op/2masz)
  operator_hourly_rate_eur    NUMERIC(10,4)        -- stawka operatora
  tool_cost_per_op_eur        NUMERIC(10,6)        -- narzędzia/operacja
  consumable_cost_per_op_eur  NUMERIC(10,6)        -- materiały zużywalne
  process_media_cost_per_op   NUMERIC(10,6)        -- gaz, chłodziwo/operacja
  overhead_pct                NUMERIC(6,3)         -- narzut na koszty pośrednie [%]
  learning_curve_factor       NUMERIC(6,4)         -- współczynnik krzywej uczenia
  oee_factor                  NUMERIC(6,4)         -- korekta OEE (rzeczywista wydajność)
  valid_from                  DATE
```

### Stawki maszynogodziny — przykładowe wartości (EUR/h, Europa Zachodnia)

| Klasa maszyny | Min | Typowa | Max | Podstawa |
|--------------|-----|--------|-----|---------|
| Laser 4kW fiber | 45 | 65 | 90 | Amortyzacja + energia + PM |
| Laser 12kW fiber | 70 | 95 | 130 | |
| CNC frezarka 3-osiowa | 35 | 55 | 80 | |
| CNC frezarka 5-osiowa | 60 | 90 | 140 | |
| CNC tokarka | 30 | 50 | 75 | |
| Centrum toczenia + frezowania | 55 | 85 | 120 | |
| Prasa krawędziowa (CNC) | 25 | 40 | 60 | |
| Prasa wykrawająca | 30 | 50 | 70 | |
| Spawarka MIG/MAG | 15 | 25 | 40 | |
| Robot spawalniczy | 30 | 50 | 70 | |
| Linia proszkowa | 20 | 35 | 55 | (per m²) |
| Linia anodowania | 25 | 40 | 60 | (per m²) |

### Formuła runtime cost per sztuka

```python
def runtime_cost_per_piece(model: RuntimeCostModel,
                            quantity: int = 1) -> RuntimeCostBreakdown:
    """
    Full runtime cost per piece including OEE correction.
    """
    # Effective cycle time with OEE correction
    effective_cycle_sec = model.cycle_time_sec / model.oee_factor

    # Time in hours
    cycle_h = effective_cycle_sec / 3600

    # Machine cost
    machine_cost = cycle_h * model.machine_hourly_rate_eur

    # Operator cost (may be fractional: 0.5 if one op runs 2 machines)
    operator_cost = cycle_h * model.operator_hourly_rate_eur * model.operator_count

    # Direct costs
    direct_cost = (machine_cost + operator_cost
                   + model.tool_cost_per_op_eur
                   + model.consumable_cost_per_op_eur
                   + model.process_media_cost_per_op)

    # Overhead
    total_cost = direct_cost * (1 + model.overhead_pct / 100)

    # Learning curve correction (Wright's law: 80% curve)
    if model.learning_curve_factor and quantity > 1:
        lc_factor = quantity ** (math.log2(model.learning_curve_factor))
        total_cost *= lc_factor

    return RuntimeCostBreakdown(
        cycle_time_sec=effective_cycle_sec,
        machine_cost_eur=round(machine_cost, 6),
        operator_cost_eur=round(operator_cost, 6),
        tool_cost_eur=model.tool_cost_per_op_eur,
        consumable_cost_eur=model.consumable_cost_per_op_eur,
        media_cost_eur=model.process_media_cost_per_op,
        overhead_eur=round(direct_cost * model.overhead_pct / 100, 6),
        total_cost_eur=round(total_cost, 6),
    )
```

---

## 12. Energy Cost Model

### Architektura modelu energetycznego

```
EnergyCostModel
├── MachineEnergyProfile      -- profil mocy maszyny w stanach pracy
├── EnergyTariff              -- taryfa energetyczna (strefy czasowe, peak/offpeak)
├── EnergyConsumptionRecord   -- rzeczywiste zużycie (z licznika / IoT)
└── EnergyForecast            -- prognoza kosztu energii
```

### Encja: MachineEnergyProfile

```
MachineEnergyProfile
  profile_id            UUID PK
  machine_id            UUID FK
  state                 ENUM(IDLE, WARM_UP, SETUP, CUTTING, RAPID_MOVE,
                             COOLANT_PUMP, CHIP_CONVEYOR, STANDBY, OFF)
  power_kw              NUMERIC(8,3)          -- moc w danym stanie
  typical_fraction_pct  NUMERIC(5,2)          -- % czasu w stanie
  notes                 TEXT
```

### Profil energetyczny — przykład Laser 12kW

```
State            Power [kW]   Fraction [%]
IDLE             2.5          10%
WARM_UP          6.0           5%
CUTTING          11.0         60%
RAPID_MOVE       4.0          15%
PIERCE           12.0          5%
STANDBY          1.5           5%

Avg power = Σ(power × fraction) = 11.0×0.60 + 4.0×0.15 + ... = 8.15 kW
Energy per hour = 8.15 kWh
```

### Encja: EnergyTariff

```
EnergyTariff
  tariff_id             UUID PK
  plant_id              UUID FK
  tariff_name           VARCHAR(100)          -- np. "G12 Przemysłowy 2026"
  valid_from            DATE
  valid_to              DATE
  peak_start_h          SMALLINT              -- godzina początku szczytu
  peak_end_h            SMALLINT
  peak_rate_eur_kwh     NUMERIC(8,6)          -- cena szczyt
  offpeak_rate_eur_kwh  NUMERIC(8,6)          -- cena poza szczytem
  weekend_rate_eur_kwh  NUMERIC(8,6)
  distribution_fee      NUMERIC(8,6)          -- opłata dystrybucyjna
  capacity_fee_eur_kw   NUMERIC(10,4)         -- opłata za moc zamówioną
  renewable_pct         NUMERIC(5,2)          -- % energii odnawialnej
  co2_factor_kg_kwh     NUMERIC(8,6)          -- emisja CO₂ [kg/kWh]
```

### Formuła kosztu energii per operacja

```python
def energy_cost_per_operation(machine: Machine,
                               cycle_time_sec: float,
                               tariff: EnergyTariff,
                               shift_time: datetime) -> EnergyResult:
    """
    Calculates energy cost for one operation.
    """
    profile = machine.energy_profile
    avg_power_kw = sum(
        s.power_kw * s.typical_fraction_pct / 100
        for s in profile
    )

    energy_kwh = avg_power_kw * cycle_time_sec / 3600

    # Time-of-use pricing
    hour = shift_time.hour
    if tariff.peak_start_h <= hour < tariff.peak_end_h:
        rate = tariff.peak_rate_eur_kwh + tariff.distribution_fee
    else:
        rate = tariff.offpeak_rate_eur_kwh + tariff.distribution_fee

    energy_cost = energy_kwh * rate
    co2_kg = energy_kwh * tariff.co2_factor_kg_kwh

    return EnergyResult(
        energy_kwh=round(energy_kwh, 6),
        avg_power_kw=avg_power_kw,
        rate_eur_kwh=rate,
        energy_cost_eur=round(energy_cost, 6),
        co2_emissions_kg=round(co2_kg, 6),
    )
```

### Benchmarki zużycia energii per proces

| Proces | Energia na 1h pracy | CO₂/h (@ 0.4 kg/kWh) |
|--------|--------------------|-----------------------|
| Laser 6kW (cięcie stali) | 5–8 kWh | 2–3.2 kg |
| Laser 12kW (cięcie stali) | 8–14 kWh | 3.2–5.6 kg |
| CNC frezarka 22kW | 8–18 kWh | 3.2–7.2 kg |
| CNC tokarka 15kW | 6–12 kWh | 2.4–4.8 kg |
| Prasa krawędziowa 110kW | 5–40 kWh | 2–16 kg |
| Spawarka MIG (inverter) | 3–12 kWh | 1.2–4.8 kg |
| Linia proszkowa (piec) | 25–60 kWh | 10–24 kg |
| Linia anodowania | 30–80 kWh | 12–32 kg |

---

## 13. Maintenance Cost Model

### Składniki kosztu utrzymania ruchu

```
Koszt_utrzymania = Amortyzacja_maszyny
                  + Planowe_przeglądy_PM
                  + Nieplanowane_naprawy_CM
                  + Koszt_części_zamiennych
                  + Koszt_serwisu_zewnętrznego
                  + Koszt_przestojów (OEE_loss × machine_rate)
```

### Encja: MaintenanceCostModel

```
MaintenanceCostModel
  model_id                  UUID PK
  machine_id                UUID FK
  annual_depreciation_eur   NUMERIC(14,2)      -- roczna amortyzacja
  pm_cost_annual_eur        NUMERIC(14,2)      -- roczny koszt PM
  cm_cost_annual_eur        NUMERIC(14,2)      -- roczny koszt napraw nieplan.
  spare_parts_annual_eur    NUMERIC(14,2)      -- części zamienne rocznie
  external_service_annual   NUMERIC(14,2)      -- serwis zewnętrzny
  calibration_annual_eur    NUMERIC(14,2)      -- kalibracje
  insurance_annual_eur      NUMERIC(14,2)      -- ubezpieczenie
  total_annual_eur          NUMERIC(14,2)      -- suma roczna
  annual_operating_hours    NUMERIC(8,2)       -- planowane h/rok
  maintenance_rate_eur_h    NUMERIC(10,6)      -- koszt/h (= total/hours)
  valid_from                DATE
```

### Formuła stawki utrzymania ruchu per godzinę

```python
def maintenance_hourly_rate(machine: Machine,
                             planned_hours_per_year: float) -> MaintenanceRate:
    """
    All-in maintenance rate per productive hour.
    """
    # Depreciation
    depreciation_annual = machine.purchase_price_eur / machine.depreciation_years

    # PM: estimated from MTBF/MTTR
    pm_events_annual = planned_hours_per_year / machine.mtbf_hours
    pm_cost = pm_events_annual * machine.mttr_hours * EXTERNAL_TECH_RATE_EUR_H

    # CM (corrective): industry benchmark 1.5–3% of purchase price
    cm_cost = machine.purchase_price_eur * 0.02

    # Spare parts: 0.5–2% of purchase price
    spare_parts = machine.purchase_price_eur * 0.01

    total_annual = depreciation_annual + pm_cost + cm_cost + spare_parts
    rate_per_hour = total_annual / planned_hours_per_year

    return MaintenanceRate(
        depreciation_eur_h=round(depreciation_annual / planned_hours_per_year, 4),
        pm_eur_h=round(pm_cost / planned_hours_per_year, 4),
        cm_eur_h=round(cm_cost / planned_hours_per_year, 4),
        spare_parts_eur_h=round(spare_parts / planned_hours_per_year, 4),
        total_eur_h=round(rate_per_hour, 4),
    )
```

---

## 14. Scrap Cost Model

### Rodzaje złomu w procesach

| Kategoria | Opis | Odzysk |
|-----------|------|--------|
| GEOMETRIC_SCRAP | Odpad z geometrii (szkielet po laserze, wióry) | Tak (cena złomu) |
| PROCESS_REJECT | Niezgodność wymiarowa/jakościowa | Częściowy (rework) |
| SETUP_SCRAP | Złom z first article / nastawu | Minimalny |
| TOOLING_SCRAP | Zniszczenie przez złamanie narzędzia | Nie |
| REWORK | Naprawa (dodatkowy czas) | N/A |
| SCRAP_MATERIAL | Całkowite odrzucenie partii | Cena złomu |

### Encja: ScrapCostModel

```
ScrapCostModel
  scrap_model_id            UUID PK
  process_type_code         VARCHAR(30)
  material_class            VARCHAR(30)
  geometric_scrap_pct       NUMERIC(6,3)     -- odpad geometryczny
  process_reject_pct        NUMERIC(6,3)     -- % niezgodności
  setup_scrap_pct           NUMERIC(6,3)     -- złom nastawczy
  rework_rate_pct           NUMERIC(6,3)     -- % detali do reworku
  rework_time_factor        NUMERIC(5,3)     -- czas reworku = factor × cycle_time
  scrap_recovery_rate       NUMERIC(6,3)     -- % wartości odzysku ze złomu
  scrap_handling_cost_eur_kg NUMERIC(8,4)   -- koszt obsługi złomu/kg
  valid_from                DATE
```

### Formuła kosztu złomu per operacja

```python
def scrap_cost_per_operation(model: ScrapCostModel,
                              material_cost_eur: float,
                              part_weight_kg: float,
                              runtime_cost_eur: float,
                              scrap_price_eur_kg: float) -> ScrapCostResult:
    """
    Full scrap cost including material waste, rework, and scrap handling.
    """
    # Geometric scrap (material loss, partially recovered)
    geometric_loss = material_cost_eur * model.geometric_scrap_pct / 100
    geometric_recovery = geometric_loss * model.scrap_recovery_rate / 100
    net_geometric_scrap = geometric_loss - geometric_recovery

    # Process reject cost (lost manufacturing time + material)
    reject_cost = (material_cost_eur + runtime_cost_eur) * model.process_reject_pct / 100

    # Rework cost (extra machining time)
    rework_cost = runtime_cost_eur * model.rework_rate_pct / 100 * model.rework_time_factor

    # Scrap handling
    scrap_weight_kg = part_weight_kg * (model.geometric_scrap_pct + model.process_reject_pct) / 100
    handling_cost = scrap_weight_kg * model.scrap_handling_cost_eur_kg

    total = net_geometric_scrap + reject_cost + rework_cost + handling_cost

    return ScrapCostResult(
        geometric_scrap_net_eur=round(net_geometric_scrap, 6),
        process_reject_eur=round(reject_cost, 6),
        rework_eur=round(rework_cost, 6),
        handling_eur=round(handling_cost, 6),
        total_scrap_cost_eur=round(total, 6),
        total_scrap_pct_of_direct=(total / (material_cost_eur + runtime_cost_eur) * 100),
    )
```

### Typowe wskaźniki złomu per proces

| Proces | Odpad geometryczny | Odrzut procesowy | Rework |
|--------|-------------------|-----------------|--------|
| Laser (blachy, nesting) | 8–24% masy | 0.1–0.5% | 0.2% |
| CNC frezowanie | 20–70% (wiórowanie) | 0.3–1.0% | 0.5% |
| CNC toczenie | 15–50% | 0.3–0.8% | 0.3% |
| Tłoczenie (blanking) | 15–35% | 0.5–2.0% | 0.3% |
| Gięcie | 0% (no material loss) | 0.5–2.0% | 2.0% |
| Spawanie MIG | 0.5–1.5% drutu | 1.0–3.0% | 1.5% |
| Malowanie proszkowe | 2–50% proszku | 0.3–1.0% | 1.0% |
| Anodowanie | — | 0.5–2.0% | 0.5% |

---

## 20. Cost Formulas — Kompletne formuły kosztowe

### Pełna kalkulacja kosztu operacji

```
Total_Operation_Cost = Setup_Cost_Per_Piece
                     + Runtime_Cost_Per_Piece
                     + Energy_Cost_Per_Piece
                     + Maintenance_Cost_Per_Piece
                     + Scrap_Cost_Per_Piece
                     + Overhead_Allocation
```

Gdzie:

```python
def total_operation_cost(
    operation: ProcessOperation,
    machine: Machine,
    operator: Operator,
    material: Material,
    batch_size: int,
    part_weight_kg: float,
    shift_datetime: datetime
) -> OperationCostBreakdown:

    # 1. Setup cost
    setup = setup_cost_per_piece(operation.setup_model, machine,
                                  operator, batch_size)

    # 2. Runtime cost
    runtime = runtime_cost_per_piece(operation.runtime_model)

    # 3. Energy cost
    energy = energy_cost_per_operation(machine, operation.cycle_time_sec,
                                        get_tariff(machine.plant_id), shift_datetime)

    # 4. Maintenance cost (rate × time)
    maint_rate = get_maintenance_hourly_rate(machine)
    maint_cost = maint_rate * (operation.cycle_time_sec / 3600)

    # 5. Scrap cost
    material_cost_eur = get_material_cost(material, part_weight_kg)
    scrap = scrap_cost_per_operation(operation.scrap_model,
                                      material_cost_eur, part_weight_kg,
                                      runtime.total_cost_eur,
                                      get_scrap_price(material))

    total = (setup['setup_cost_per_piece_eur']
             + runtime.total_cost_eur
             + energy.energy_cost_eur
             + maint_cost
             + scrap.total_scrap_cost_eur)

    return OperationCostBreakdown(
        setup_eur=setup['setup_cost_per_piece_eur'],
        runtime_eur=runtime.total_cost_eur,
        energy_eur=energy.energy_cost_eur,
        maintenance_eur=maint_cost,
        scrap_eur=scrap.total_scrap_cost_eur,
        total_eur=total,
        cost_per_min=total / (operation.cycle_time_sec / 60),
        cost_breakdown_pct={
            'setup':       round(setup['setup_cost_per_piece_eur'] / total * 100, 1),
            'runtime':     round(runtime.total_cost_eur / total * 100, 1),
            'energy':      round(energy.energy_cost_eur / total * 100, 1),
            'maintenance': round(maint_cost / total * 100, 1),
            'scrap':       round(scrap.total_scrap_cost_eur / total * 100, 1),
        }
    )
```

### Routing cost (suma operacji)

```python
def routing_cost(operations: list[ProcessOperation],
                  batch_size: int,
                  part_geometry: PartGeometry) -> RoutingCostResult:
    """
    Calculates total manufacturing cost for a complete routing.
    Handles parallel operations and shared setup.
    """
    ops_costs = []
    for op in operations:
        cost = total_operation_cost(op, op.assigned_machine, op.operator,
                                     op.material, batch_size, part_geometry.weight_kg,
                                     next_shift_start())
        ops_costs.append(cost)

    total = sum(c.total_eur for c in ops_costs)
    critical_path_time = calculate_critical_path(operations)

    return RoutingCostResult(
        operations=ops_costs,
        total_manufacturing_cost_eur=round(total, 4),
        critical_path_minutes=critical_path_time,
        throughput_time_hours=critical_path_time / 60,
        cost_per_operation={op.process_code: cost.total_eur
                            for op, cost in zip(operations, ops_costs)}
    )
```
