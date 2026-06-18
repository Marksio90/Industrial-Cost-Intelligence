# Manufacturing Process Engine — Capacity, Scheduling, SQL Schema

## 15. Capacity Planning

### Architektura planowania zdolności produkcyjnych

```
CapacityEngine
├── CapacityCalculator        -- oblicza dostępną zdolność per zasób
├── LoadCalculator            -- oblicza obciążenie z zleceń (orders)
├── BottleneckDetector        -- wykrywa wąskie gardła
├── CapacityConstraintStore   -- przechowuje ograniczenia
└── CapacityReport            -- raporty i dashboardy
```

### Encja: CapacityPlan

```
CapacityPlan
  plan_id              UUID PK
  plant_id             UUID FK
  plan_period_from     DATE
  plan_period_to       DATE
  plan_horizon_days    SMALLINT
  plan_type            ENUM(ROUGH_CUT, DETAILED, FINITE, INFINITE)
  created_at           TIMESTAMPTZ
  created_by           UUID FK
  status               ENUM(DRAFT, PUBLISHED, ARCHIVED)
```

### Encja: CapacitySlot (Dostępna zdolność per zasób per dzień)

```
CapacitySlot
  slot_id              UUID PK
  plan_id              UUID FK
  resource_id          UUID FK
  slot_date            DATE
  shift_number         SMALLINT
  available_minutes    SMALLINT          -- minuty dostępne
  reserved_minutes     SMALLINT          -- minuty zarezerwowane (planowane PM, szkolenia)
  free_minutes         SMALLINT          -- wolne = available - reserved
  oee_adjusted_min     NUMERIC(8,2)      -- skorygowane OEE
  loaded_minutes       NUMERIC(8,2)      -- obciążenie ze zleceń
  overload_pct         NUMERIC(6,2)      -- % przeładowania (loaded/free × 100)
  utilization_pct      NUMERIC(6,2)      -- % wykorzystania
```

### Obliczanie dostępnej zdolności

```python
def calculate_available_capacity(
    resource_id: str,
    date_from: date,
    date_to: date,
    apply_oee: bool = True
) -> list[CapacitySlot]:
    """
    Calculates net available capacity per day per resource.
    """
    slots = []
    resource = get_resource(resource_id)
    shifts = get_active_shifts(resource_id)
    holidays = get_plant_holidays(resource.plant_id, date_from, date_to)
    planned_downtime = get_planned_downtime(resource_id, date_from, date_to)

    for day in date_range(date_from, date_to):
        if day in holidays or day.weekday() > 4:
            continue  # Skip weekends and holidays

        for shift in shifts:
            if day.weekday() not in shift.working_days:
                continue

            # Gross available
            available = shift.available_minutes

            # Subtract planned downtime
            pd_minutes = sum(
                d.duration_min for d in planned_downtime
                if d.date == day and d.shift == shift.shift_number
            )
            available -= pd_minutes

            # Apply OEE factor
            if apply_oee:
                oee = get_average_oee(resource_id, lookback_days=30)
                available_oee = available * (oee / 100)
            else:
                available_oee = available

            slots.append(CapacitySlot(
                resource_id=resource_id,
                slot_date=day,
                shift_number=shift.shift_number,
                available_minutes=available,
                oee_adjusted_min=available_oee,
            ))

    return slots
```

### Bottleneck Detection

```python
class BottleneckDetector:
    """
    Identifies bottleneck resources using theory of constraints (TOC).
    A resource is a bottleneck when: load > 85% of capacity for 3+ consecutive days.
    """

    BOTTLENECK_THRESHOLD_PCT = 85.0
    CONSECUTIVE_DAYS_TRIGGER = 3

    def detect(self, plan: CapacityPlan) -> list[BottleneckAlert]:
        alerts = []
        resources = get_all_resources(plan.plant_id)

        for resource in resources:
            slots = get_loaded_slots(resource.resource_id, plan)
            overloaded_streak = 0

            for slot in sorted(slots, key=lambda s: s.slot_date):
                if slot.utilization_pct >= self.BOTTLENECK_THRESHOLD_PCT:
                    overloaded_streak += 1
                else:
                    overloaded_streak = 0

                if overloaded_streak >= self.CONSECUTIVE_DAYS_TRIGGER:
                    alerts.append(BottleneckAlert(
                        resource_id=resource.resource_id,
                        resource_code=resource.resource_code,
                        alert_date=slot.slot_date,
                        avg_utilization_pct=slot.utilization_pct,
                        severity='HIGH' if slot.utilization_pct > 100 else 'MEDIUM',
                        recommendation=self._recommend_action(resource, slot)
                    ))
                    break

        return alerts
```

---

## 16. Scheduling Inputs

### Dane wejściowe dla schedulera

MPE dostarcza schedulerowi następujące dane:

```python
@dataclass
class SchedulingInput:
    """
    Complete scheduling input package for a manufacturing order.
    """
    # Operation definition
    operation_id:         str
    process_type_code:    str
    process_name:         str

    # Timing
    setup_time_min:       float      # przezbraojenie
    cycle_time_sec:       float      # czas cyklu per szt.
    min_batch_size:       int
    max_batch_size:       int
    lot_splitting_allowed: bool       # czy można dzielić zlecenie

    # Resources
    primary_resource_id:  str         # maszyna podstawowa
    alternate_resources:  list[str]   # maszyny zastępcze
    operator_required:    bool
    operator_skill:       str         # wymagany poziom

    # Constraints
    must_start_after:     datetime | None     # najwcześniejszy start
    must_finish_before:   datetime | None     # deadline
    predecessor_ops:      list[str]           # poprzedzające operacje
    successor_ops:        list[str]           # następne operacje
    min_wait_after_min:   float       # min. czas oczekiwania po operacji (np. schnięcie kleju)
    max_wait_after_min:   float | None # maks. czas (np. hartowanie)

    # Capacity
    available_slots:      list[CapacitySlot]
    calendar_exceptions:  list[date]   # świętai, urlopy

    # Cost (for cost-aware scheduling)
    hourly_rate_eur:      float
    overtime_rate_factor: float        # mnożnik nadgodzin
    shift_preference:     str          # preferred shift
```

### Reguły pierwszeństwa (Priority Rules)

```python
SCHEDULING_RULES = {
    'SPT':  'Shortest Processing Time — minimalizacja WIP',
    'EDD':  'Earliest Due Date — minimalizacja opóźnień',
    'CR':   'Critical Ratio = (due_date - now) / remaining_work_days',
    'WSPT': 'Weighted Shortest Processing Time — z priorytetem klienta',
    'MS':   'Minimum Slack = (due_date - now) - remaining_processing_time',
    'LCFS': 'Last Come First Served (LIFO stack)',
}
```

### Integracja z ERP (SAP PP / Oracle MFG)

```
MPE → ERP Export:
  Work Center master data        (Arbeitplatz / Resource)
  Operation times                (Vorgangzeiten)
  Machine hourly rates           (Kostenstellen)
  Capacity groups                (Kapazitätsgruppen)
  Available capacity             (verfügbare Kapazität)

ERP → MPE Import:
  Production orders              (Fertigungsaufträge)
  Routing assignments            (Arbeitspläne)
  Actual times (confirmation)    (Rückmeldungen)
  Material availability          (Materialverfügbarkeit)
```

---

## 17. SQL Schema

```sql
-- =============================================================================
-- MANUFACTURING PROCESS ENGINE — SQL SCHEMA
-- Database: PostgreSQL 16+
-- Schema: manufacturing_process
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS manufacturing_process;
SET search_path TO manufacturing_process, public;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- ENUMS
-- =============================================================================

CREATE TYPE process_class_enum AS ENUM (
    'CUT','MAC','FOR','JOI','ASS','FIN'
);

CREATE TYPE machine_class_enum AS ENUM (
    'LASER','CNC_MILLING','CNC_TURNING','DRILLING','PRESS_BRAKE',
    'STAMPING_PRESS','WELDING_MACHINE','WELDING_ROBOT',
    'COATING_LINE','ANODIZING_LINE','BLASTING_CABINET',
    'ASSEMBLY_STATION','ASSEMBLY_ROBOT','OTHER'
);

CREATE TYPE resource_type_enum AS ENUM (
    'MACHINE','WORKCENTER','ASSEMBLY_STATION','FINISHING_LINE','HUMAN','SHARED'
);

CREATE TYPE resource_status_enum AS ENUM (
    'ACTIVE','MAINTENANCE','DECOMMISSIONED','RESERVED','SETUP'
);

CREATE TYPE process_status_enum AS ENUM (
    'ACTIVE','DEPRECATED','DRAFT','UNDER_REVIEW'
);

CREATE TYPE compatibility_level_enum AS ENUM (
    'OPTIMAL','ACCEPTABLE','POSSIBLE_WITH_CAUTION','NOT_RECOMMENDED','FORBIDDEN'
);

CREATE TYPE skill_level_enum AS ENUM (
    'BASIC','STANDARD','EXPERT','TRAINER'
);

CREATE TYPE oee_source_enum AS ENUM (
    'MANUAL','MES','IOT_AUTO'
);

CREATE TYPE downtime_category_enum AS ENUM (
    'BREAKDOWN','SETUP','MAINTENANCE','TOOLCHANGE','MATERIAL_WAIT',
    'QUALITY_HOLD','OPERATOR_ABSENT','PLANNED_PM','CHANGEOVER','OTHER'
);

CREATE TYPE loss_type_enum AS ENUM (
    'AVAILABILITY','PERFORMANCE','QUALITY'
);

CREATE TYPE maintenance_type_enum AS ENUM (
    'PREVENTIVE','PREDICTIVE','CORRECTIVE','CALIBRATION','OVERHAUL'
);

CREATE TYPE tool_category_enum AS ENUM (
    'CUTTING_INSERT','END_MILL','DRILL','TAP','REAMER',
    'LASER_NOZZLE','LASER_LENS','WELDING_TIP',
    'DIE','PUNCH','BENDING_DIE','BENDING_PUNCH',
    'ABRASIVE_DISC','GRINDING_WHEEL','COATING_GUN','OTHER'
);

-- =============================================================================
-- TABLE: process_categories (Taxonomy nodes)
-- =============================================================================

CREATE TABLE process_categories (
    category_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parent_id         UUID REFERENCES process_categories(category_id),
    category_code     VARCHAR(30) NOT NULL,
    category_name     VARCHAR(100) NOT NULL,
    category_name_en  VARCHAR(100),
    process_class     process_class_enum NOT NULL,
    hierarchy_level   SMALLINT NOT NULL CHECK (hierarchy_level BETWEEN 1 AND 5),
    materialized_path VARCHAR(500) NOT NULL,
    sort_order        SMALLINT DEFAULT 0,
    is_leaf           BOOLEAN NOT NULL DEFAULT FALSE,
    description       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_category_code UNIQUE (category_code)
);

CREATE INDEX idx_proc_cat_parent ON process_categories(parent_id);
CREATE INDEX idx_proc_cat_class ON process_categories(process_class);
CREATE INDEX idx_proc_cat_path ON process_categories USING GIST (materialized_path gist_trgm_ops);

-- =============================================================================
-- TABLE: manufacturing_processes (Core entity)
-- =============================================================================

CREATE TABLE manufacturing_processes (
    process_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_code        VARCHAR(50) NOT NULL,
    process_name        VARCHAR(200) NOT NULL,
    process_name_en     VARCHAR(200),
    category_id         UUID NOT NULL REFERENCES process_categories(category_id),
    process_class       process_class_enum NOT NULL,
    process_type_code   VARCHAR(30) NOT NULL,        -- taxonomy leaf (e.g. CUT.TH.LC.FIB)
    status              process_status_enum NOT NULL DEFAULT 'DRAFT',
    description         TEXT,
    technology_notes    TEXT,
    applicable_standards VARCHAR(200)[],              -- ISO 9013, EN 1090, etc.
    search_vector       TSVECTOR,
    erp_operation_code  VARCHAR(50),                 -- SAP/Oracle operation code
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          UUID,
    version             INTEGER NOT NULL DEFAULT 1,

    CONSTRAINT uq_process_code UNIQUE (process_code)
);

CREATE INDEX idx_proc_category ON manufacturing_processes(category_id);
CREATE INDEX idx_proc_class ON manufacturing_processes(process_class);
CREATE INDEX idx_proc_type_code ON manufacturing_processes(process_type_code);
CREATE INDEX idx_proc_status ON manufacturing_processes(status) WHERE status = 'ACTIVE';
CREATE INDEX idx_proc_search ON manufacturing_processes USING GIN(search_vector);
CREATE INDEX idx_proc_name_trgm ON manufacturing_processes USING GIN(process_name gin_trgm_ops);

CREATE OR REPLACE FUNCTION mpe_process_search_update() RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', COALESCE(NEW.process_code, '')), 'A') ||
        setweight(to_tsvector('simple', COALESCE(NEW.process_name, '')), 'A') ||
        setweight(to_tsvector('simple', COALESCE(NEW.process_type_code, '')), 'B') ||
        setweight(to_tsvector('simple', COALESCE(NEW.description, '')), 'D');
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_proc_search_update
    BEFORE INSERT OR UPDATE ON manufacturing_processes
    FOR EACH ROW EXECUTE FUNCTION mpe_process_search_update();

-- =============================================================================
-- TABLE: process_parameters (Parametry procesu — JSONB + typed columns)
-- =============================================================================

CREATE TABLE process_parameters (
    param_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_id          UUID NOT NULL REFERENCES manufacturing_processes(process_id) ON DELETE CASCADE,
    param_key           VARCHAR(100) NOT NULL,
    param_value         TEXT,
    param_value_numeric NUMERIC(20,6),
    param_value_json    JSONB,
    param_type          VARCHAR(20) NOT NULL,         -- NUMERIC, TEXT, ENUM, BOOLEAN, JSON
    unit                VARCHAR(30),
    is_inherited        BOOLEAN NOT NULL DEFAULT FALSE,
    override_level      VARCHAR(20) DEFAULT 'TYPE',   -- CLASS/FAMILY/TYPE/VARIANT/OPERATION
    min_value           NUMERIC(20,6),
    max_value           NUMERIC(20,6),
    default_value       TEXT,
    description         TEXT,

    CONSTRAINT uq_process_param UNIQUE (process_id, param_key)
);

CREATE INDEX idx_proc_params_process ON process_parameters(process_id);
CREATE INDEX idx_proc_params_key ON process_parameters(param_key);
CREATE INDEX idx_proc_params_json ON process_parameters USING GIN(param_value_json);

-- =============================================================================
-- TABLE: process_material_compatibility
-- =============================================================================

CREATE TABLE process_material_compatibility (
    compat_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_type_code       VARCHAR(30) NOT NULL,
    material_class          VARCHAR(30),               -- METAL, POLYMER, etc.
    material_grade          VARCHAR(50),               -- specific grade (optional)
    compatibility_level     compatibility_level_enum NOT NULL,
    min_thickness_mm        NUMERIC(8,3),
    max_thickness_mm        NUMERIC(8,3),
    min_hardness_hb         NUMERIC(6,1),
    max_hardness_hb         NUMERIC(6,1),
    speed_factor            NUMERIC(6,3) DEFAULT 1.0,
    quality_notes           TEXT,
    process_parameter_overrides JSONB DEFAULT '{}',
    scrap_rate_override_pct NUMERIC(6,3),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_proc_mat_compat UNIQUE (process_type_code, material_class, material_grade)
);

CREATE INDEX idx_pmc_process ON process_material_compatibility(process_type_code);
CREATE INDEX idx_pmc_material ON process_material_compatibility(material_class, material_grade);

-- =============================================================================
-- TABLE: production_resources
-- =============================================================================

CREATE TABLE production_resources (
    resource_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_code         VARCHAR(30) NOT NULL,
    resource_name         VARCHAR(200) NOT NULL,
    resource_type         resource_type_enum NOT NULL,
    plant_id              UUID,
    department_id         UUID,
    cost_center_id        VARCHAR(50),
    location_code         VARCHAR(50),
    status                resource_status_enum NOT NULL DEFAULT 'ACTIVE',
    hourly_rate_eur       NUMERIC(10,4),
    currency              CHAR(3) DEFAULT 'EUR',
    available_hours_day   NUMERIC(5,2) DEFAULT 8,
    shifts_per_day        SMALLINT DEFAULT 1,
    erp_workcenter_id     VARCHAR(50),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_resource_code UNIQUE (resource_code)
);

CREATE INDEX idx_resources_type ON production_resources(resource_type);
CREATE INDEX idx_resources_status ON production_resources(status) WHERE status = 'ACTIVE';
CREATE INDEX idx_resources_plant ON production_resources(plant_id);

-- =============================================================================
-- TABLE: machines
-- =============================================================================

CREATE TABLE machines (
    machine_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id               UUID NOT NULL REFERENCES production_resources(resource_id),
    machine_class             machine_class_enum NOT NULL,
    manufacturer              VARCHAR(100),
    model                     VARCHAR(100),
    serial_number             VARCHAR(100),
    year_of_manufacture       SMALLINT,
    purchase_date             DATE,
    purchase_price_eur        NUMERIC(14,2),
    depreciation_years        SMALLINT DEFAULT 10,
    book_value_eur            NUMERIC(14,2),
    erp_asset_id              VARCHAR(50),
    control_system            VARCHAR(100),
    axis_count                SMALLINT,
    max_power_kw              NUMERIC(8,2),
    avg_power_kw              NUMERIC(8,2),
    idle_power_kw             NUMERIC(8,2),
    work_envelope_x_mm        NUMERIC(10,2),
    work_envelope_y_mm        NUMERIC(10,2),
    work_envelope_z_mm        NUMERIC(10,2),
    max_load_kg               NUMERIC(10,2),
    positioning_accuracy_mm   NUMERIC(8,4),
    repeatability_mm          NUMERIC(8,4),
    mtbf_hours                NUMERIC(10,2),
    mttr_hours                NUMERIC(8,2),
    last_calibration_date     DATE,
    next_calibration_date     DATE,
    last_maintenance_date     DATE,
    next_maintenance_date     DATE,
    iot_device_id             VARCHAR(100),
    is_cnc                    BOOLEAN DEFAULT FALSE,
    is_robot                  BOOLEAN DEFAULT FALSE,
    machine_specific_params   JSONB DEFAULT '{}',   -- class-specific parameters
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_machine_resource UNIQUE (resource_id)
);

CREATE INDEX idx_machines_class ON machines(machine_class);
CREATE INDEX idx_machines_resource ON machines(resource_id);
CREATE INDEX idx_machines_iot ON machines(iot_device_id) WHERE iot_device_id IS NOT NULL;

-- =============================================================================
-- TABLE: tools
-- =============================================================================

CREATE TABLE tools (
    tool_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tool_code              VARCHAR(50) NOT NULL,
    tool_name              VARCHAR(200) NOT NULL,
    tool_category          tool_category_enum NOT NULL,
    manufacturer           VARCHAR(100),
    catalog_number         VARCHAR(100),
    material               VARCHAR(50),              -- HSS, HM, CBN, PCD
    coating                VARCHAR(50),              -- TiN, TiAlN
    diameter_mm            NUMERIC(8,3),
    length_mm              NUMERIC(8,2),
    cutting_length_mm      NUMERIC(8,2),
    flutes                 SMALLINT,
    shank_type             VARCHAR(30),
    unit_cost_eur          NUMERIC(10,4) NOT NULL,
    nominal_life_minutes   NUMERIC(10,2),
    nominal_life_m_cut     NUMERIC(10,2),
    compatible_processes   VARCHAR(30)[],
    compatible_materials   VARCHAR(30)[],
    resharpening_possible  BOOLEAN DEFAULT FALSE,
    resharpening_cost_eur  NUMERIC(10,4),
    max_resharpening_count SMALLINT DEFAULT 0,
    supplier_id            UUID,
    is_active              BOOLEAN DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tool_code UNIQUE (tool_code)
);

CREATE INDEX idx_tools_category ON tools(tool_category);
CREATE INDEX idx_tools_active ON tools(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_tools_processes ON tools USING GIN(compatible_processes);

-- =============================================================================
-- TABLE: oee_records
-- =============================================================================

CREATE TABLE oee_records (
    oee_id                         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    machine_id                     UUID NOT NULL REFERENCES machines(machine_id),
    shift_date                     DATE NOT NULL,
    shift_number                   SMALLINT NOT NULL,
    planned_production_time_min    SMALLINT NOT NULL,
    unplanned_downtime_min         SMALLINT NOT NULL DEFAULT 0,
    planned_downtime_min           SMALLINT NOT NULL DEFAULT 0,
    operating_time_min             SMALLINT GENERATED ALWAYS AS
                                   (planned_production_time_min - unplanned_downtime_min - planned_downtime_min)
                                   STORED,
    ideal_cycle_time_sec           NUMERIC(8,2),
    actual_cycle_time_sec          NUMERIC(8,2),
    total_parts_produced           INTEGER NOT NULL DEFAULT 0,
    good_parts                     INTEGER NOT NULL DEFAULT 0,
    scrap_parts                    INTEGER NOT NULL DEFAULT 0,
    rework_parts                   INTEGER NOT NULL DEFAULT 0,
    availability_pct               NUMERIC(6,3),
    performance_pct                NUMERIC(6,3),
    quality_pct                    NUMERIC(6,3),
    oee_pct                        NUMERIC(6,3),
    teep_pct                       NUMERIC(6,3),
    energy_kwh                     NUMERIC(10,3),
    recorded_by                    UUID,
    source                         oee_source_enum NOT NULL DEFAULT 'MANUAL',
    notes                          TEXT,
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_oee_record UNIQUE (machine_id, shift_date, shift_number)
) PARTITION BY RANGE (shift_date);

CREATE TABLE oee_records_2025 PARTITION OF oee_records
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE oee_records_2026 PARTITION OF oee_records
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE oee_records_2027 PARTITION OF oee_records
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE INDEX idx_oee_machine_date ON oee_records(machine_id, shift_date DESC);
CREATE INDEX idx_oee_date ON oee_records(shift_date DESC);

-- =============================================================================
-- TABLE: downtime_records (partitioned)
-- =============================================================================

CREATE TABLE downtime_records (
    downtime_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    machine_id         UUID NOT NULL REFERENCES machines(machine_id),
    start_at           TIMESTAMPTZ NOT NULL,
    end_at             TIMESTAMPTZ,
    duration_min       SMALLINT,
    downtime_category  downtime_category_enum NOT NULL,
    loss_type          loss_type_enum NOT NULL,
    description        TEXT,
    repair_action      TEXT,
    technician_id      UUID,
    work_order_id      VARCHAR(50),
    cost_impact_eur    NUMERIC(10,4),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (start_at);

CREATE TABLE downtime_records_2025 PARTITION OF downtime_records
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE downtime_records_2026 PARTITION OF downtime_records
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE INDEX idx_downtime_machine ON downtime_records(machine_id, start_at DESC);
CREATE INDEX idx_downtime_category ON downtime_records(downtime_category);

-- =============================================================================
-- TABLE: setup_cost_models
-- =============================================================================

CREATE TABLE setup_cost_models (
    setup_cost_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_type_code           VARCHAR(30) NOT NULL,
    machine_id                  UUID REFERENCES machines(machine_id),
    setup_time_min              NUMERIC(8,2) NOT NULL,
    cnc_programming_time_min    NUMERIC(8,2) DEFAULT 0,
    first_article_parts         SMALLINT DEFAULT 1,
    first_article_scrap_rate    NUMERIC(6,3) DEFAULT 10,
    fixture_cost_eur            NUMERIC(10,4) DEFAULT 0,
    fixture_life_setups         INTEGER DEFAULT 1000,
    operator_skill_required     skill_level_enum DEFAULT 'STANDARD',
    setup_complexity            VARCHAR(20) DEFAULT 'STANDARD',
    min_batch_for_amortization  INTEGER DEFAULT 1,
    valid_from                  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to                    DATE,

    CONSTRAINT uq_setup_cost UNIQUE (process_type_code, machine_id, valid_from)
);

CREATE INDEX idx_setup_cost_process ON setup_cost_models(process_type_code);

-- =============================================================================
-- TABLE: runtime_cost_models
-- =============================================================================

CREATE TABLE runtime_cost_models (
    runtime_cost_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_type_code           VARCHAR(30) NOT NULL,
    machine_id                  UUID REFERENCES machines(machine_id),
    cycle_time_sec              NUMERIC(10,4) NOT NULL,
    machine_hourly_rate_eur     NUMERIC(10,4) NOT NULL,
    operator_count              NUMERIC(4,2) DEFAULT 1,
    operator_hourly_rate_eur    NUMERIC(10,4) NOT NULL,
    tool_cost_per_op_eur        NUMERIC(10,6) DEFAULT 0,
    consumable_cost_per_op_eur  NUMERIC(10,6) DEFAULT 0,
    process_media_cost_per_op   NUMERIC(10,6) DEFAULT 0,
    overhead_pct                NUMERIC(6,3) DEFAULT 25,
    learning_curve_factor       NUMERIC(6,4) DEFAULT 1.0,
    oee_factor                  NUMERIC(6,4) DEFAULT 0.75,
    currency                    CHAR(3) DEFAULT 'EUR',
    valid_from                  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to                    DATE,

    CONSTRAINT uq_runtime_cost UNIQUE (process_type_code, machine_id, valid_from)
);

CREATE INDEX idx_runtime_cost_process ON runtime_cost_models(process_type_code);
CREATE INDEX idx_runtime_cost_machine ON runtime_cost_models(machine_id);

-- =============================================================================
-- TABLE: energy_tariffs
-- =============================================================================

CREATE TABLE energy_tariffs (
    tariff_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_id               UUID NOT NULL,
    tariff_name            VARCHAR(100) NOT NULL,
    valid_from             DATE NOT NULL,
    valid_to               DATE,
    peak_start_h           SMALLINT CHECK (peak_start_h BETWEEN 0 AND 23),
    peak_end_h             SMALLINT CHECK (peak_end_h BETWEEN 0 AND 23),
    peak_rate_eur_kwh      NUMERIC(10,6) NOT NULL,
    offpeak_rate_eur_kwh   NUMERIC(10,6) NOT NULL,
    weekend_rate_eur_kwh   NUMERIC(10,6),
    distribution_fee       NUMERIC(10,6) DEFAULT 0,
    co2_factor_kg_kwh      NUMERIC(8,6) DEFAULT 0.4,

    CONSTRAINT uq_tariff UNIQUE (plant_id, valid_from)
);

-- =============================================================================
-- TABLE: scrap_cost_models
-- =============================================================================

CREATE TABLE scrap_cost_models (
    scrap_model_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_type_code           VARCHAR(30) NOT NULL,
    material_class              VARCHAR(30),
    geometric_scrap_pct         NUMERIC(6,3) DEFAULT 10,
    process_reject_pct          NUMERIC(6,3) DEFAULT 0.5,
    setup_scrap_pct             NUMERIC(6,3) DEFAULT 2,
    rework_rate_pct             NUMERIC(6,3) DEFAULT 1,
    rework_time_factor          NUMERIC(5,3) DEFAULT 1.5,
    scrap_recovery_rate         NUMERIC(6,3) DEFAULT 25,
    scrap_handling_cost_eur_kg  NUMERIC(8,4) DEFAULT 0.05,
    valid_from                  DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to                    DATE,

    CONSTRAINT uq_scrap_model UNIQUE (process_type_code, material_class, valid_from)
);

-- =============================================================================
-- TABLE: capacity_slots
-- =============================================================================

CREATE TABLE capacity_slots (
    slot_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id           UUID NOT NULL REFERENCES production_resources(resource_id),
    slot_date             DATE NOT NULL,
    shift_number          SMALLINT NOT NULL,
    available_minutes     SMALLINT NOT NULL,
    reserved_minutes      SMALLINT DEFAULT 0,
    free_minutes          SMALLINT GENERATED ALWAYS AS (available_minutes - reserved_minutes) STORED,
    oee_adjusted_min      NUMERIC(8,2),
    loaded_minutes        NUMERIC(8,2) DEFAULT 0,
    utilization_pct       NUMERIC(6,2) GENERATED ALWAYS AS
                          (CASE WHEN (available_minutes - reserved_minutes) > 0
                           THEN (loaded_minutes / (available_minutes - reserved_minutes) * 100)
                           ELSE 0 END) STORED,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_capacity_slot UNIQUE (resource_id, slot_date, shift_number)
) PARTITION BY RANGE (slot_date);

CREATE TABLE capacity_slots_2026_h1 PARTITION OF capacity_slots
    FOR VALUES FROM ('2026-01-01') TO ('2026-07-01');
CREATE TABLE capacity_slots_2026_h2 PARTITION OF capacity_slots
    FOR VALUES FROM ('2026-07-01') TO ('2027-01-01');
CREATE TABLE capacity_slots_2027_h1 PARTITION OF capacity_slots
    FOR VALUES FROM ('2027-01-01') TO ('2027-07-01');

CREATE INDEX idx_cap_slots_resource ON capacity_slots(resource_id, slot_date);
CREATE INDEX idx_cap_slots_overload ON capacity_slots(slot_date)
    WHERE loaded_minutes > available_minutes * 0.85;

-- =============================================================================
-- TABLE: operators
-- =============================================================================

CREATE TABLE operators (
    operator_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id              VARCHAR(50) UNIQUE,
    first_name               VARCHAR(100),
    last_name                VARCHAR(100),
    department_id            UUID,
    shift_default            VARCHAR(10) DEFAULT 'SHIFT1',
    hourly_rate_eur          NUMERIC(8,4) NOT NULL,
    social_cost_factor       NUMERIC(5,3) DEFAULT 1.3,
    effective_hourly_cost_eur NUMERIC(8,4) GENERATED ALWAYS AS
                             (hourly_rate_eur * social_cost_factor) STORED,
    productive_hours_ratio   NUMERIC(5,3) DEFAULT 0.85,
    status                   VARCHAR(20) DEFAULT 'ACTIVE',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_operators_employee ON operators(employee_id);
CREATE INDEX idx_operators_status ON operators(status) WHERE status = 'ACTIVE';

-- =============================================================================
-- TABLE: operator_certifications
-- =============================================================================

CREATE TABLE operator_certifications (
    cert_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    operator_id          UUID NOT NULL REFERENCES operators(operator_id),
    process_type_code    VARCHAR(30) NOT NULL,
    certification_type   VARCHAR(100),
    skill_level          skill_level_enum NOT NULL,
    issued_date          DATE NOT NULL,
    valid_to             DATE,
    issuing_body         VARCHAR(100),
    certificate_number   VARCHAR(100),
    machine_types        VARCHAR(30)[],
    is_valid             BOOLEAN GENERATED ALWAYS AS
                         (valid_to IS NULL OR valid_to >= CURRENT_DATE) STORED,

    CONSTRAINT uq_operator_cert UNIQUE (operator_id, process_type_code, certification_type)
);

CREATE INDEX idx_op_cert_operator ON operator_certifications(operator_id);
CREATE INDEX idx_op_cert_process ON operator_certifications(process_type_code);
CREATE INDEX idx_op_cert_valid ON operator_certifications(valid_to) WHERE valid_to IS NOT NULL;

-- =============================================================================
-- TABLE: process_embeddings (AI)
-- =============================================================================

CREATE TABLE process_embeddings (
    embedding_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_id        UUID NOT NULL REFERENCES manufacturing_processes(process_id) ON DELETE CASCADE,
    embedding_model   VARCHAR(100) NOT NULL,
    embedding_version VARCHAR(20) NOT NULL,
    vector            VECTOR(1536),
    text_input        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_current        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX idx_proc_embed_process ON process_embeddings(process_id) WHERE is_current = TRUE;
CREATE INDEX idx_proc_embed_hnsw ON process_embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Active processes with current cost
CREATE VIEW v_processes_with_cost AS
SELECT
    p.process_id,
    p.process_code,
    p.process_name,
    p.process_class,
    p.process_type_code,
    p.status,
    r.cycle_time_sec,
    r.machine_hourly_rate_eur,
    r.operator_hourly_rate_eur,
    r.operator_count,
    r.oee_factor,
    ROUND((r.cycle_time_sec / 3600) * r.machine_hourly_rate_eur, 4)   AS machine_cost_per_op,
    ROUND((r.cycle_time_sec / 3600) * r.operator_hourly_rate_eur
          * r.operator_count, 4)                                        AS operator_cost_per_op
FROM manufacturing_processes p
LEFT JOIN LATERAL (
    SELECT cycle_time_sec, machine_hourly_rate_eur,
           operator_hourly_rate_eur, operator_count, oee_factor
    FROM runtime_cost_models
    WHERE process_type_code = p.process_type_code
      AND valid_to IS NULL
    ORDER BY valid_from DESC
    LIMIT 1
) r ON TRUE
WHERE p.status = 'ACTIVE';

-- Machine OEE summary (last 30 days)
CREATE VIEW v_machine_oee_30d AS
SELECT
    m.machine_id,
    r.resource_code,
    r.resource_name,
    m.machine_class,
    ROUND(AVG(o.availability_pct), 2) AS avg_availability,
    ROUND(AVG(o.performance_pct), 2)  AS avg_performance,
    ROUND(AVG(o.quality_pct), 2)      AS avg_quality,
    ROUND(AVG(o.oee_pct), 2)          AS avg_oee,
    SUM(o.good_parts)                 AS total_good_parts,
    SUM(o.scrap_parts)                AS total_scrap,
    COUNT(*)                          AS shifts_recorded
FROM machines m
JOIN production_resources r ON r.resource_id = m.resource_id
LEFT JOIN oee_records o ON o.machine_id = m.machine_id
    AND o.shift_date >= CURRENT_DATE - INTERVAL '30 days'
WHERE r.status = 'ACTIVE'
GROUP BY m.machine_id, r.resource_code, r.resource_name, m.machine_class;

-- Capacity utilization view
CREATE VIEW v_capacity_utilization AS
SELECT
    r.resource_code,
    r.resource_name,
    cs.slot_date,
    SUM(cs.available_minutes)  AS total_available_min,
    SUM(cs.loaded_minutes)     AS total_loaded_min,
    ROUND(AVG(cs.utilization_pct), 2) AS avg_utilization_pct,
    BOOL_OR(cs.utilization_pct > 90)  AS is_near_capacity
FROM capacity_slots cs
JOIN production_resources r ON r.resource_id = cs.resource_id
GROUP BY r.resource_code, r.resource_name, cs.slot_date;
```
