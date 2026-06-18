# Manufacturing Process Engine — Resource, Machine, Tooling, Operator, OEE

## 5. Resource Model

### Hierarchia zasobów produkcyjnych

```
ProductionResource (abstrakt)
├── Machine                    -- maszyna / centrum obróbcze
├── WorkCenter                 -- gniazdo robocze (grupa maszyn)
├── AssemblyStation            -- stanowisko montażowe
├── FinishingLine              -- linia wykańczania (malowanie, anodowanie)
├── HumanResource (Operator)   -- operator / pracownik
└── SharedEquipment            -- zasoby współdzielone (suwnica, wózek, miernik)
```

### Encja: ProductionResource

```
ProductionResource
  resource_id          UUID PK
  resource_code        VARCHAR(30) UNIQUE       -- np. "WC-LASER-01"
  resource_name        VARCHAR(200)
  resource_type        ENUM(MACHINE, WORKCENTER, ASSEMBLY, FINISHING, HUMAN, SHARED)
  plant_id             UUID FK                  -- zakład
  department_id        UUID FK                  -- dział/wydział
  cost_center_id       VARCHAR(50)              -- centrum kosztów (ERP)
  location_code        VARCHAR(50)              -- lokalizacja w hali (np. A-02-03)
  status               ENUM(ACTIVE, MAINTENANCE, DECOMMISSIONED, RESERVED)
  hourly_rate_eur      NUMERIC(10,4)            -- koszt maszynogodziny
  currency             CHAR(3)
  available_hours_day  NUMERIC(5,2)             -- nominalne h/dzień
  shifts_per_day       SMALLINT                 -- liczba zmian
  created_at           TIMESTAMPTZ
  updated_at           TIMESTAMPTZ
```

### Encja: ResourceShift (Dostępność zmianowa)

```
ResourceShift
  shift_id             UUID PK
  resource_id          UUID FK
  shift_name           VARCHAR(50)          -- "Zmiana I", "Zmiana II", "Zmiana III"
  shift_start          TIME
  shift_end            TIME
  working_days         INTEGER[]            -- [1,2,3,4,5] = Mon-Fri
  available_minutes    SMALLINT             -- efektywne minuty zmiany
  break_minutes        SMALLINT             -- łączny czas przerw
  overtime_allowed     BOOLEAN
  valid_from           DATE
  valid_to             DATE
```

### Encja: ResourceCapability (Możliwości zasobu)

```
ResourceCapability
  capability_id        UUID PK
  resource_id          UUID FK
  process_type_code    VARCHAR(30)          -- powiązanie z taksonomią
  capability_level     ENUM(PRIMARY, SECONDARY, BACKUP)
  max_dimension_x_mm   NUMERIC(10,2)        -- maks. wymiary obrabianego detalu
  max_dimension_y_mm   NUMERIC(10,2)
  max_dimension_z_mm   NUMERIC(10,2)
  max_weight_kg        NUMERIC(10,2)
  min_feature_size_mm  NUMERIC(8,4)
  notes                TEXT
```

---

## 6. Machine Model

### Encja: Machine

| Atrybut | Typ | Opis |
|---------|-----|------|
| machine_id | UUID PK | Klucz główny |
| resource_id | UUID FK | Powiązanie z ProductionResource |
| machine_class | ENUM | LASER, CNC_MILLING, CNC_TURNING, DRILLING, PRESS_BRAKE, STAMPING_PRESS, WELDING, COATING_LINE, ANODIZING_LINE, BLASTING, ASSEMBLY_ROBOT, OTHER |
| manufacturer | VARCHAR(100) | Producent (TRUMPF, DMG Mori, Mazak, LVD, ...) |
| model | VARCHAR(100) | Model maszyny |
| serial_number | VARCHAR(100) | Numer seryjny |
| year_of_manufacture | SMALLINT | Rok produkcji |
| purchase_date | DATE | Data zakupu |
| purchase_price_eur | NUMERIC(14,2) | Cena zakupu |
| depreciation_years | SMALLINT | Okres amortyzacji |
| depreciation_method | ENUM | LINEAR, DECLINING_BALANCE |
| book_value_eur | NUMERIC(14,2) | Bieżąca wartość księgowa |
| erp_asset_id | VARCHAR(50) | ID środka trwałego w ERP |
| control_system | VARCHAR(100) | Sterownik CNC (Siemens 840D, Fanuc 31i, ...) |
| axis_count | SMALLINT | Liczba osi sterowanych |
| max_power_kw | NUMERIC(8,2) | Moc zainstalowana [kW] |
| avg_power_kw | NUMERIC(8,2) | Średnia moc w trakcie pracy |
| idle_power_kw | NUMERIC(8,2) | Moc biegu jałowego |
| work_envelope_x_mm | NUMERIC(10,2) | Przestrzeń robocza X |
| work_envelope_y_mm | NUMERIC(10,2) | Przestrzeń robocza Y |
| work_envelope_z_mm | NUMERIC(10,2) | Przestrzeń robocza Z |
| max_load_kg | NUMERIC(10,2) | Maks. obciążenie stołu |
| positioning_accuracy_mm | NUMERIC(8,4) | Dokładność pozycjonowania |
| repeatability_mm | NUMERIC(8,4) | Powtarzalność |
| mtbf_hours | NUMERIC(10,2) | Mean Time Between Failures |
| mttr_hours | NUMERIC(8,2) | Mean Time To Repair |
| last_calibration_date | DATE | Ostatnia kalibracja |
| next_calibration_date | DATE | Następna kalibracja |
| last_maintenance_date | DATE | Ostatni przegląd |
| next_maintenance_date | DATE | Następny przegląd |
| iot_device_id | VARCHAR(100) | ID urządzenia IoT/MES |
| is_cnc | BOOLEAN | CNC (automatyczne) |
| is_robot | BOOLEAN | Robot (opcja) |

### MachineClass — specyficzne parametry

#### Maszyny laserowe (LASER)

```json
{
  "laser_type":       "FIBER",
  "laser_power_kw":   12,
  "wavelength_nm":    1064,
  "beam_quality_m2":  1.1,
  "max_sheet_x_mm":   3000,
  "max_sheet_y_mm":   1500,
  "max_thickness_steel_mm": 25,
  "max_thickness_aluminum_mm": 12,
  "max_thickness_stainless_mm": 20,
  "cutting_head":     "Precitec ProCutter 2.0",
  "gas_supply":       ["N2", "O2", "AIR"],
  "pallet_changer":   true,
  "nozzle_changer":   true,
  "monitoring_system": "Precitec OCT"
}
```

#### Centra frezarskie CNC (CNC_MILLING)

```json
{
  "table_size_x_mm": 1200,
  "table_size_y_mm": 600,
  "spindle_speed_max_rpm": 18000,
  "spindle_power_kw":      22,
  "spindle_taper":         "HSK-A63",
  "tool_magazine_capacity": 60,
  "tool_change_time_sec":   3.5,
  "rapid_traverse_m_min":  40,
  "max_feed_rate_m_min":   20,
  "coolant_through_spindle": true,
  "probing_system":         "Renishaw OMP60"
}
```

#### Prasy krawędziowe (PRESS_BRAKE)

```json
{
  "nominal_force_kn": 1000,
  "bending_length_mm": 3100,
  "back_gauge_axes":   2,
  "stroke_mm":         200,
  "open_height_mm":    450,
  "control_system":   "Delem DA-66T",
  "angle_measurement": true,
  "crowning_system":   "CNC hydraulic"
}
```

### Encja: MaintenancePlan

```
MaintenancePlan
  plan_id              UUID PK
  machine_id           UUID FK
  maintenance_type     ENUM(PREVENTIVE, PREDICTIVE, CORRECTIVE, CALIBRATION)
  frequency_hours      NUMERIC(8,2)         -- co ile godzin pracy
  frequency_days       SMALLINT             -- lub co ile dni
  estimated_duration_h NUMERIC(5,2)         -- czas trwania
  estimated_cost_eur   NUMERIC(10,2)        -- szacowany koszt
  requires_external    BOOLEAN              -- wymaga serwisu zewnętrznego
  spare_parts_list     JSONB                -- lista części zamiennych
  procedure_document   VARCHAR(500)         -- link do dokumentu SOP
  last_performed_at    DATE
  next_due_at          DATE
```

---

## 7. Tooling Model

### Hierarchia narzędzi

```
ToolCategory
  └── ToolType
        └── Tool (instance)
              ├── ToolLifeRecord
              └── ToolChangeRecord
```

### Encja: Tool

| Atrybut | Typ | Opis |
|---------|-----|------|
| tool_id | UUID PK | |
| tool_code | VARCHAR(50) | Kod narzędzia (ISO 13399) |
| tool_name | VARCHAR(200) | Nazwa |
| tool_category | ENUM | CUTTING_INSERT, END_MILL, DRILL, TAP, REAMER, LASER_NOZZLE, LASER_LENS, WELDING_TIP, DIE, PUNCH, BENDING_DIE, BENDING_PUNCH, ABRASIVE_DISC, GRINDING_WHEEL, COATING_GUN |
| manufacturer | VARCHAR(100) | |
| catalog_number | VARCHAR(100) | Numer katalogowy |
| material | ENUM | HSS, HM, CBN, PCD, CERAMIC, CARBIDE_COATED |
| coating | VARCHAR(50) | TiN, TiAlN, TiCN, AlCrN |
| diameter_mm | NUMERIC(8,3) | Średnica (jeśli dotyczy) |
| length_mm | NUMERIC(8,2) | Długość całkowita |
| cutting_length_mm | NUMERIC(8,2) | Długość robocza |
| flutes | SMALLINT | Liczba ostrzy |
| shank_type | VARCHAR(30) | HSK-A63, BT40, ISO40, Weldon |
| unit_cost_eur | NUMERIC(10,4) | Koszt jednostkowy |
| nominal_life_minutes | NUMERIC(10,2) | Nominalna trwałość [min] |
| nominal_life_m_cut | NUMERIC(10,2) | Trwałość [mb cięcia] (laser) |
| compatible_processes | VARCHAR(30)[] | Lista process_type_code |
| compatible_materials | VARCHAR(30)[] | Klasy materiałów |
| resharpening_possible | BOOLEAN | Możliwość regeneracji |
| resharpening_cost_eur | NUMERIC(10,4) | Koszt regeneracji |
| max_resharpening_count | SMALLINT | |
| supplier_id | UUID FK | |

### Encja: ToolLifeRecord (Zużycie narzędzia)

```
ToolLifeRecord
  life_id              UUID PK
  tool_id              UUID FK
  machine_id           UUID FK
  start_at             TIMESTAMPTZ
  end_at               TIMESTAMPTZ
  minutes_used         NUMERIC(10,2)
  meters_cut           NUMERIC(10,2)
  parts_produced       INTEGER
  end_reason           ENUM(SCHEDULED_CHANGE, WEAR_LIMIT, BREAKAGE, QUALITY_FAILURE)
  wear_vb_mm           NUMERIC(6,4)         -- flank wear measurement
  surface_quality_ok   BOOLEAN
  notes                TEXT
```

### Logika kosztu narzędzia per operacja

```python
class ToolCostCalculator:
    def cost_per_operation(self, tool: Tool, operation_params: dict) -> ToolCostResult:
        """
        Tool cost = (tool_unit_cost / total_life_operations) + resharpening_amortization
        """
        # Effective life in operations
        if operation_params.get('cutting_meters'):
            ops_per_tool = tool.nominal_life_m_cut / operation_params['cutting_meters']
        else:
            ops_per_tool = tool.nominal_life_minutes / operation_params['machining_time_min']

        # Include resharpening cycles
        total_effective_ops = ops_per_tool * (1 + tool.max_resharpening_count)
        resharpen_cost_per_op = (
            tool.resharpening_cost_eur * tool.max_resharpening_count / total_effective_ops
            if tool.resharpening_possible else 0
        )

        tool_cost_per_op = (tool.unit_cost_eur / total_effective_ops) + resharpen_cost_per_op

        return ToolCostResult(
            tool_code=tool.tool_code,
            cost_per_operation=round(tool_cost_per_op, 6),
            ops_per_tool_life=ops_per_tool,
        )
```

---

## 8. Operator Model

### Encja: Operator

| Atrybut | Typ | Opis |
|---------|-----|------|
| operator_id | UUID PK | |
| employee_id | VARCHAR(50) | Numer pracownika (ERP) |
| first_name | VARCHAR(100) | |
| last_name | VARCHAR(100) | |
| department_id | UUID FK | |
| shift_default | ENUM | SHIFT1, SHIFT2, SHIFT3 |
| hourly_rate_eur | NUMERIC(8,4) | Stawka godzinowa (brutto pracodawca) |
| social_cost_factor | NUMERIC(5,3) | Mnożnik kosztów pracy (narzuty, ZUS) |
| effective_hourly_cost_eur | NUMERIC(8,4) | Pełny koszt godzinowy |
| productive_hours_ratio | NUMERIC(5,3) | Wskaźnik produktywności (0.75–0.95) |
| status | ENUM | ACTIVE, ON_LEAVE, TERMINATED |

### Encja: OperatorCertification

```
OperatorCertification
  cert_id              UUID PK
  operator_id          UUID FK
  process_type_code    VARCHAR(30)      -- np. "JOI.WE.MIG"
  certification_type   VARCHAR(100)     -- ISO 9606-1 spawacz, EN 1090, itp.
  skill_level          ENUM(BASIC, STANDARD, EXPERT, TRAINER)
  issued_date          DATE
  valid_to             DATE
  issuing_body         VARCHAR(100)
  certificate_number   VARCHAR(100)
  machine_types        VARCHAR(30)[]    -- na jakich maszynach uprawniony
```

### Encja: SkillMatrix

```
skill_matrix (view / derived table)
  operator_id   UUID
  process_code  VARCHAR(30)
  skill_level   ENUM(BASIC, STANDARD, EXPERT, TRAINER)
  certified     BOOLEAN
  cert_valid    BOOLEAN
  last_trained  DATE
```

### Koszt operatora per operacja

```python
def operator_cost_per_minute(operator: Operator) -> Decimal:
    """
    Effective cost per productive minute.
    """
    raw_hourly = operator.hourly_rate_eur * operator.social_cost_factor
    return raw_hourly / 60 / operator.productive_hours_ratio
```

---

## 9. OEE Model

### Definicja OEE

```
OEE = Dostępność × Wydajność × Jakość

Dostępność (A) = Czas_operacyjny / Czas_zaplanowany
Wydajność  (P) = (Czas_cyklu_idealny × Liczba_sztuk) / Czas_operacyjny
Jakość     (Q) = Liczba_dobrych / Liczba_wszystkich
```

### Encja: OEERecord (Rekord OEE per zmiana)

| Atrybut | Typ | Opis |
|---------|-----|------|
| oee_id | UUID PK | |
| machine_id | UUID FK | |
| shift_date | DATE | |
| shift_number | SMALLINT | 1, 2, 3 |
| planned_production_time_min | SMALLINT | Zaplanowany czas produkcji [min] |
| unplanned_downtime_min | SMALLINT | Nieplanowane przestoje |
| planned_downtime_min | SMALLINT | Planowane przestoje (przezbrojenie, PM) |
| operating_time_min | SMALLINT | Czas operacyjny (A = operating/planned) |
| ideal_cycle_time_sec | NUMERIC(8,2) | Idealny czas cyklu |
| actual_cycle_time_sec | NUMERIC(8,2) | Rzeczywisty średni czas cyklu |
| total_parts_produced | INTEGER | |
| good_parts | INTEGER | |
| scrap_parts | INTEGER | |
| rework_parts | INTEGER | |
| availability_pct | NUMERIC(6,3) | A [%] |
| performance_pct | NUMERIC(6,3) | P [%] |
| quality_pct | NUMERIC(6,3) | Q [%] |
| oee_pct | NUMERIC(6,3) | OEE [%] = A×P×Q |
| teep_pct | NUMERIC(6,3) | TEEP (Total Effective Equipment Performance) |
| energy_kwh | NUMERIC(10,3) | Energia zużyta w zmianie |
| recorded_by | UUID FK | |
| source | ENUM | MANUAL, MES, IOT_AUTO |
| notes | TEXT | |

### Encja: DowntimeRecord (Rejestr przestojów)

```
DowntimeRecord
  downtime_id          UUID PK
  machine_id           UUID FK
  start_at             TIMESTAMPTZ
  end_at               TIMESTAMPTZ
  duration_min         SMALLINT (computed)
  downtime_category    ENUM(BREAKDOWN, SETUP, MAINTENANCE, TOOLCHANGE,
                            MATERIAL_WAIT, QUALITY_HOLD, OPERATOR_ABSENT,
                            PLANNED_PM, CHANGEOVER)
  loss_type            ENUM(AVAILABILITY, PERFORMANCE, QUALITY)
  description          TEXT
  repair_action        TEXT
  technician_id        UUID FK
  work_order_id        VARCHAR(50)
```

### Benchmarki OEE per klasa maszyn

| Klasa maszyny | OEE Światowej klasy | Typowe PL | Cel minimalny |
|--------------|-------------------|-----------|---------------|
| Laser (FIBER) | 85% | 65–75% | 70% |
| CNC Frezarki | 80% | 60–72% | 68% |
| CNC Tokarki | 82% | 65–75% | 70% |
| Prasy krawędziowe | 75% | 55–68% | 62% |
| Spawarki | 70% | 50–65% | 60% |
| Linie proszkowe | 88% | 70–82% | 75% |
| Linie anodowania | 85% | 68–78% | 72% |

### OEE Aggregation — SQL

```sql
CREATE OR REPLACE FUNCTION get_machine_oee_summary(
    p_machine_id  UUID,
    p_from_date   DATE,
    p_to_date     DATE
) RETURNS TABLE (
    period          TEXT,
    avg_availability NUMERIC,
    avg_performance  NUMERIC,
    avg_quality      NUMERIC,
    avg_oee          NUMERIC,
    total_downtime_h NUMERIC,
    total_scrap_pcs  BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        to_char(shift_date, 'YYYY-MM')    AS period,
        ROUND(AVG(availability_pct), 2)   AS avg_availability,
        ROUND(AVG(performance_pct), 2)    AS avg_performance,
        ROUND(AVG(quality_pct), 2)        AS avg_quality,
        ROUND(AVG(oee_pct), 2)            AS avg_oee,
        ROUND(SUM(unplanned_downtime_min + planned_downtime_min) / 60.0, 2) AS total_downtime_h,
        SUM(scrap_parts)                  AS total_scrap_pcs
    FROM oee_records
    WHERE machine_id = p_machine_id
      AND shift_date BETWEEN p_from_date AND p_to_date
    GROUP BY to_char(shift_date, 'YYYY-MM')
    ORDER BY 1;
END;
$$ LANGUAGE plpgsql STABLE;
```
