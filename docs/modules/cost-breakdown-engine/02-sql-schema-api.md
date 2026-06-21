# Cost Breakdown Engine — Sections 4–5

## 4. SQL Schema

### 4.1 Schemat PostgreSQL 16 — `cbe`

```sql
-- =============================================================
--  COST BREAKDOWN ENGINE — schemat `cbe`
--  PostgreSQL 16, extensions: pgcrypto, ltree, pg_trgm
-- =============================================================

CREATE SCHEMA IF NOT EXISTS cbe;

-- ─────────────────────────────────────────────────────────────
-- ENUMy
-- ─────────────────────────────────────────────────────────────

CREATE TYPE cbe.cost_category AS ENUM (
    'MATERIAL', 'LABOR', 'MACHINE', 'ENERGY', 'TOOLING', 'OVERHEAD'
);

CREATE TYPE cbe.cost_component_type AS ENUM (
    'RAW_MATERIAL', 'PURCHASED_COMPONENT', 'SURFACE_TREATMENT', 'SCRAP_ALLOWANCE',
    'DIRECT_LABOR', 'SETUP_LABOR', 'INSPECTION_LABOR', 'REWORK_LABOR',
    'MACHINE_DEPRECIATION', 'MACHINE_MAINTENANCE', 'TOOLING_WEAR',
    'ELECTRICITY', 'COMPRESSED_AIR', 'COOLANT', 'HEATING_COOLING',
    'TOOL_AMORTIZATION', 'FIXTURE_COST', 'PROGRAMMING_COST',
    'FACTORY_OVERHEAD', 'SG_AND_A', 'RND_ALLOCATION', 'PROFIT_MARGIN'
);

CREATE TYPE cbe.data_source AS ENUM (
    'QUOTE', 'BOM', 'DRAWING', 'RATE_TABLE', 'MACHINE_DB',
    'ENERGY_RATE', 'OVERHEAD_CONFIG', 'TOOLING_DB', 'ESTIMATE', 'DEFAULT'
);

CREATE TYPE cbe.confidence_band AS ENUM (
    'HIGH',        -- ≥ 0.90
    'MEDIUM',      -- 0.70–0.89
    'LOW',         -- 0.50–0.69
    'INDICATIVE'   -- < 0.50
);

CREATE TYPE cbe.breakdown_status AS ENUM (
    'DRAFT', 'CALCULATED', 'REVIEWED', 'APPROVED', 'SUPERSEDED', 'ARCHIVED'
);

CREATE TYPE cbe.overhead_profile AS ENUM (
    'DEFAULT', 'LEAN', 'PREMIUM', 'EXPORT', 'PROTOTYPE', 'SERIES'
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: maszyny
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.machines (
    machine_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    machine_code            TEXT        NOT NULL UNIQUE,
    machine_type            TEXT        NOT NULL,  -- "CNC_TURNING", "MILLING_3AX", ...
    description             TEXT,
    location_code           TEXT        NOT NULL DEFAULT 'DE',
    capex_eur               NUMERIC(12,2) NOT NULL,
    life_years              NUMERIC(4,1)  NOT NULL,
    oee                     NUMERIC(4,3)  NOT NULL DEFAULT 0.80 CHECK (oee BETWEEN 0.01 AND 1.00),
    power_kw                NUMERIC(8,2),
    air_consumption_m3h     NUMERIC(8,3),
    coolant_lh              NUMERIC(8,3),
    maintenance_rate_pct    NUMERIC(5,2)  NOT NULL DEFAULT 3.00,
    allocation_method       TEXT          NOT NULL DEFAULT 'CYCLE_TIME',
    flat_rate_eur_h         NUMERIC(10,4),
    is_active               BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: stawki lokalizacji
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.location_rates (
    rate_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    location_code           TEXT        NOT NULL,
    valid_from              DATE        NOT NULL,
    valid_to                DATE,
    operator_rate_eur_h     NUMERIC(8,4) NOT NULL,
    setup_rate_eur_h        NUMERIC(8,4) NOT NULL,
    inspection_rate_pct     NUMERIC(5,2) NOT NULL,
    rework_rate_pct         NUMERIC(5,2) NOT NULL,
    electricity_eur_kwh     NUMERIC(8,6) NOT NULL,
    air_eur_m3              NUMERIC(8,6) NOT NULL,
    coolant_eur_l           NUMERIC(8,6) NOT NULL,
    factory_overhead_pct    NUMERIC(5,2) NOT NULL,
    sg_and_a_pct            NUMERIC(5,2) NOT NULL,
    rnd_pct                 NUMERIC(5,2) NOT NULL,
    margin_pct              NUMERIC(5,2) NOT NULL,
    overhead_profile        cbe.overhead_profile NOT NULL DEFAULT 'DEFAULT',
    created_by              TEXT        NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (location_code, valid_from, overhead_profile)
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: stawki materiałów
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.material_rates (
    rate_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    material_designation    TEXT        NOT NULL,
    material_family         TEXT        NOT NULL,
    price_eur_kg            NUMERIC(10,4) NOT NULL,
    price_date              DATE        NOT NULL,
    source                  cbe.data_source NOT NULL DEFAULT 'RATE_TABLE',
    supplier_offer_id       UUID,       -- FK → sop.offer_documents (cross-schema)
    confidence              NUMERIC(4,3) NOT NULL DEFAULT 0.75,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX CONCURRENTLY cbe_material_rates_desig_idx
        ON cbe.material_rates USING btree (material_designation, price_date DESC)
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: kalkulacje kosztów
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.cost_breakdowns (
    breakdown_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    part_id                 UUID        NOT NULL,
    bom_line_id             UUID,       -- FK → bome.bom_lines (cross-schema)
    drawing_id              UUID,       -- FK → dae.drawings (cross-schema)
    supplier_offer_id       UUID,       -- FK → sop.offer_documents (cross-schema)
    quantity                NUMERIC(14,4) NOT NULL DEFAULT 1,
    location_code           TEXT        NOT NULL DEFAULT 'DE',
    overhead_profile        cbe.overhead_profile NOT NULL DEFAULT 'DEFAULT',
    currency                CHAR(3)     NOT NULL DEFAULT 'EUR',

    -- Agregaty kategorii
    material_eur            NUMERIC(16,4) NOT NULL DEFAULT 0,
    labor_eur               NUMERIC(16,4) NOT NULL DEFAULT 0,
    machine_eur             NUMERIC(16,4) NOT NULL DEFAULT 0,
    energy_eur              NUMERIC(16,4) NOT NULL DEFAULT 0,
    tooling_eur             NUMERIC(16,4) NOT NULL DEFAULT 0,
    overhead_eur            NUMERIC(16,4) NOT NULL DEFAULT 0,
    total_cost_eur          NUMERIC(16,4)
        GENERATED ALWAYS AS (
            material_eur + labor_eur + machine_eur +
            energy_eur   + tooling_eur + overhead_eur
        ) STORED,
    unit_cost_eur           NUMERIC(16,6)
        GENERATED ALWAYS AS (
            CASE WHEN quantity > 0
                 THEN (material_eur + labor_eur + machine_eur +
                       energy_eur   + tooling_eur + overhead_eur) / quantity
                 ELSE NULL END
        ) STORED,

    -- Udziały procentowe
    material_pct            NUMERIC(6,3),
    labor_pct               NUMERIC(6,3),
    machine_pct             NUMERIC(6,3),
    energy_pct              NUMERIC(6,3),
    tooling_pct             NUMERIC(6,3),
    overhead_pct            NUMERIC(6,3),

    overall_confidence      NUMERIC(4,3),
    confidence_band         cbe.confidence_band
        GENERATED ALWAYS AS (
            CASE
                WHEN overall_confidence >= 0.90 THEN 'HIGH'::cbe.confidence_band
                WHEN overall_confidence >= 0.70 THEN 'MEDIUM'::cbe.confidence_band
                WHEN overall_confidence >= 0.50 THEN 'LOW'::cbe.confidence_band
                ELSE 'INDICATIVE'::cbe.confidence_band
            END
        ) STORED,

    status                  cbe.breakdown_status NOT NULL DEFAULT 'CALCULATED',
    warnings                JSONB        NOT NULL DEFAULT '[]',
    input_snapshot          JSONB        NOT NULL DEFAULT '{}',  -- CostBreakdownRequest serialized
    created_by              TEXT        NOT NULL DEFAULT 'system',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by             TEXT,
    reviewed_at             TIMESTAMPTZ,
    approved_by             TEXT,
    approved_at             TIMESTAMPTZ
);

CREATE INDEX cbe_breakdowns_part_idx  ON cbe.cost_breakdowns (part_id, created_at DESC);
CREATE INDEX cbe_breakdowns_bom_idx   ON cbe.cost_breakdowns (bom_line_id) WHERE bom_line_id IS NOT NULL;
CREATE INDEX cbe_breakdowns_status_idx ON cbe.cost_breakdowns (status);

-- ─────────────────────────────────────────────────────────────
-- Tabela: składniki kalkulacji
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.cost_components (
    component_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    breakdown_id            UUID        NOT NULL REFERENCES cbe.cost_breakdowns ON DELETE CASCADE,
    category                cbe.cost_category    NOT NULL,
    component_type          cbe.cost_component_type NOT NULL,
    amount_eur              NUMERIC(16,6) NOT NULL,
    basis                   TEXT         NOT NULL,
    confidence              NUMERIC(4,3) NOT NULL,
    data_source             cbe.data_source NOT NULL,
    assumptions             JSONB        NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX cbe_components_breakdown_idx ON cbe.cost_components (breakdown_id);
CREATE INDEX cbe_components_type_idx      ON cbe.cost_components (component_type);

-- ─────────────────────────────────────────────────────────────
-- Tabela: quantity break — tabela progów ilościowych
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.quantity_breaks (
    qb_id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    part_id                 UUID        NOT NULL,
    location_code           TEXT        NOT NULL DEFAULT 'DE',
    overhead_profile        cbe.overhead_profile NOT NULL DEFAULT 'DEFAULT',
    quantity                NUMERIC(14,4) NOT NULL,
    total_cost_eur          NUMERIC(16,4) NOT NULL,
    unit_cost_eur           NUMERIC(16,6) NOT NULL,
    material_pct            NUMERIC(6,3),
    labor_pct               NUMERIC(6,3),
    machine_pct             NUMERIC(6,3),
    tooling_pct             NUMERIC(6,3),
    overhead_pct            NUMERIC(6,3),
    generated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (part_id, location_code, overhead_profile, quantity)
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: snapshoty wersji kalkulacji
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.breakdown_versions (
    version_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    breakdown_id            UUID        NOT NULL REFERENCES cbe.cost_breakdowns,
    version_no              INT         NOT NULL,
    snapshot                JSONB       NOT NULL,
    changed_by              TEXT        NOT NULL,
    changed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_reason           TEXT,
    UNIQUE (breakdown_id, version_no)
);

-- ─────────────────────────────────────────────────────────────
-- Tabela: outbox Kafka
-- ─────────────────────────────────────────────────────────────

CREATE TABLE cbe.outbox_events (
    event_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    topic                   TEXT        NOT NULL,
    key                     TEXT        NOT NULL,
    payload                 JSONB       NOT NULL,
    published               BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX cbe_outbox_unpublished_idx ON cbe.outbox_events (created_at)
    WHERE published = FALSE;

-- ─────────────────────────────────────────────────────────────
-- Triggery
-- ─────────────────────────────────────────────────────────────

-- updated_at auto-update
CREATE OR REPLACE FUNCTION cbe.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

CREATE TRIGGER trg_breakdowns_updated_at
    BEFORE UPDATE ON cbe.cost_breakdowns
    FOR EACH ROW EXECUTE FUNCTION cbe.set_updated_at();

-- Snapshot przy zmianie statusu
CREATE OR REPLACE FUNCTION cbe.snapshot_on_status_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_no INT;
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        SELECT COALESCE(MAX(version_no), 0) + 1
          INTO v_no
          FROM cbe.breakdown_versions
         WHERE breakdown_id = NEW.breakdown_id;

        INSERT INTO cbe.breakdown_versions
            (breakdown_id, version_no, snapshot, changed_by, change_reason)
        VALUES (
            NEW.breakdown_id, v_no,
            row_to_json(NEW)::jsonb,
            NEW.updated_by,
            'status_change:' || OLD.status || '->' || NEW.status
        );
    END IF;
    RETURN NEW;
END; $$;

-- Outbox na APPROVED
CREATE OR REPLACE FUNCTION cbe.publish_breakdown_approved()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'APPROVED' AND OLD.status != 'APPROVED' THEN
        INSERT INTO cbe.outbox_events (topic, key, payload)
        VALUES (
            'cbe.breakdown.approved',
            NEW.breakdown_id::TEXT,
            jsonb_build_object(
                'breakdown_id',   NEW.breakdown_id,
                'part_id',        NEW.part_id,
                'bom_line_id',    NEW.bom_line_id,
                'quantity',       NEW.quantity,
                'unit_cost_eur',  NEW.unit_cost_eur,
                'total_cost_eur', NEW.total_cost_eur,
                'confidence_band',NEW.confidence_band,
                'location_code',  NEW.location_code,
                'approved_by',    NEW.approved_by,
                'approved_at',    NEW.approved_at
            )
        );
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_breakdown_approved
    AFTER UPDATE OF status ON cbe.cost_breakdowns
    FOR EACH ROW EXECUTE FUNCTION cbe.publish_breakdown_approved();

-- ─────────────────────────────────────────────────────────────
-- Widoki analityczne
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW cbe.v_breakdown_summary AS
SELECT
    cb.breakdown_id,
    cb.part_id,
    cb.bom_line_id,
    cb.quantity,
    cb.location_code,
    cb.overhead_profile,
    cb.unit_cost_eur,
    cb.total_cost_eur,
    cb.material_pct,
    cb.labor_pct,
    cb.machine_pct,
    cb.energy_pct,
    cb.tooling_pct,
    cb.overhead_pct,
    cb.overall_confidence,
    cb.confidence_band,
    cb.status,
    cb.created_at
FROM cbe.cost_breakdowns cb
WHERE cb.status NOT IN ('ARCHIVED', 'SUPERSEDED');

CREATE OR REPLACE VIEW cbe.v_cost_drivers AS
SELECT
    cc.breakdown_id,
    cc.category,
    cc.component_type,
    cc.amount_eur,
    ROUND(cc.amount_eur / NULLIF(cb.total_cost_eur, 0) * 100, 2) AS share_pct,
    cc.confidence,
    cc.data_source
FROM cbe.cost_components cc
JOIN cbe.cost_breakdowns cb USING (breakdown_id)
ORDER BY cc.breakdown_id, cc.amount_eur DESC;
```

### 4.2 Indexes i partycjonowanie

```sql
-- Partial index: tylko aktywne kalkulacje
CREATE INDEX cbe_breakdowns_active_idx
    ON cbe.cost_breakdowns (part_id, location_code, created_at DESC)
    WHERE status IN ('CALCULATED', 'REVIEWED', 'APPROVED');

-- Full-text search na basis (dla audytu)
CREATE INDEX cbe_components_basis_gin_idx
    ON cbe.cost_components USING GIN (to_tsvector('english', basis));

-- Partycjonowanie quantity_breaks (zakres dat w generated_at)
-- Przydatne przy L3+ (> 1M rekordów/miesiąc)
-- ALTER TABLE cbe.quantity_breaks PARTITION BY RANGE (generated_at);
```

---

## 5. API

### 5.1 OpenAPI 3.1 — Cost Breakdown Engine

#### Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CBE_VIEWER` | GET breakdowns, components, quantity-breaks, location-rates (read-only) |
| `CBE_ANALYST` | CBE_VIEWER + POST calculate, GET analytics, export CSV/XLSX |
| `CBE_ENGINEER` | CBE_ANALYST + PATCH breakdown (edit draft), recalculate |
| `CBE_APPROVER` | CBE_ENGINEER + POST approve/reject, manage rate overrides |
| `CBE_ADMIN` | Wszystko + DELETE, manage machines, location-rates, audit logs |

#### Endpointy

```
POST   /api/v1/cbe/breakdowns                       Kalkulacja nowego rozbicia
GET    /api/v1/cbe/breakdowns/{breakdown_id}        Pobierz kalkulację
GET    /api/v1/cbe/breakdowns/{breakdown_id}/components  Lista składników
PATCH  /api/v1/cbe/breakdowns/{breakdown_id}        Aktualizacja (DRAFT only)
POST   /api/v1/cbe/breakdowns/{breakdown_id}/approve     Zatwierdź
POST   /api/v1/cbe/breakdowns/{breakdown_id}/reject      Odrzuć
GET    /api/v1/cbe/breakdowns/{breakdown_id}/versions    Historia wersji
GET    /api/v1/cbe/breakdowns/{breakdown_id}/quantity-breaks  Tabela progów ilościowych

GET    /api/v1/cbe/parts/{part_id}/breakdowns       Wszystkie kalkulacje dla części
GET    /api/v1/cbe/parts/{part_id}/quantity-breaks  Tabela progów ilościowych

GET    /api/v1/cbe/analytics/cost-drivers           Analiza głównych sterowników kosztów
GET    /api/v1/cbe/analytics/location-comparison    Porównanie lokalizacji
GET    /api/v1/cbe/analytics/material-sensitivity   Analiza wrażliwości na cenę materiału
GET    /api/v1/cbe/analytics/confidence-report      Rozkład confidence w kalkulacjach

GET    /api/v1/cbe/rates/locations                  Lista stawek lokalizacji
POST   /api/v1/cbe/rates/locations                  Dodaj/nadpisz stawki
GET    /api/v1/cbe/rates/materials                  Stawki materiałów
POST   /api/v1/cbe/rates/materials                  Dodaj cenę materiału

GET    /api/v1/cbe/machines                         Lista maszyn
POST   /api/v1/cbe/machines                         Dodaj maszynę
GET    /api/v1/cbe/machines/{machine_id}            Szczegóły maszyny
PUT    /api/v1/cbe/machines/{machine_id}            Aktualizuj maszynę

GET    /api/v1/cbe/admin/queue-stats                Statystyki kolejki
GET    /api/v1/cbe/admin/audit                      Audit log
```

### 5.2 Przykładowe żądania/odpowiedzi

```http
POST /api/v1/cbe/breakdowns
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "part_id": "550e8400-e29b-41d4-a716-446655440000",
  "bom_line_id": "660f9511-f30c-52e5-b827-557766551111",
  "quantity": 500,
  "location_code": "PL",
  "overhead_profile": "SERIES",
  "currency": "EUR",
  "material": {
    "material_designation": "S235JR",
    "gross_weight_kg": "4.15",
    "net_weight_kg": "3.20",
    "scrap_factor_pct": "5"
  },
  "operations": [
    {
      "operation_code": "TURN",
      "machine_type": "CNC_TURNING",
      "cycle_time_s": "85",
      "setup_time_s": "900",
      "batch_size": 50,
      "operators": "1"
    },
    {
      "operation_code": "MILL",
      "machine_type": "MILLING_3AX",
      "cycle_time_s": "120",
      "setup_time_s": "1200",
      "batch_size": 50,
      "operators": "1"
    }
  ],
  "tooling": [
    {
      "tool_cost_eur": "1200.00",
      "planned_qty": "5000"
    }
  ]
}
```

```json
HTTP/1.1 201 Created

{
  "breakdown_id": "7a2b3c4d-...",
  "part_id": "550e8400-...",
  "quantity": 500,
  "location_code": "PL",
  "overhead_profile": "SERIES",
  "currency": "EUR",
  "unit_cost_eur": "28.4712",
  "total_cost_eur": "14235.60",
  "material_eur": "5418.75",
  "labor_eur": "3612.50",
  "machine_eur": "1890.20",
  "energy_eur": "342.80",
  "tooling_eur": "120.00",
  "overhead_eur": "2851.35",
  "material_pct": 38.07,
  "labor_pct": 25.38,
  "machine_pct": 13.28,
  "energy_pct": 2.41,
  "tooling_pct": 0.84,
  "overhead_pct": 20.03,
  "overall_confidence": 0.836,
  "confidence_band": "MEDIUM",
  "status": "CALCULATED",
  "warnings": [],
  "created_at": "2025-06-20T10:35:00Z"
}
```

### 5.3 Quantity-break endpoint

```http
GET /api/v1/cbe/parts/550e8400-.../quantity-breaks?location_code=PL&profile=SERIES
```

```json
{
  "part_id": "550e8400-...",
  "location_code": "PL",
  "overhead_profile": "SERIES",
  "breaks": [
    {"quantity": 1,      "unit_cost_eur": 89.42, "tooling_pct": 28.1},
    {"quantity": 10,     "unit_cost_eur": 52.18, "tooling_pct": 15.4},
    {"quantity": 50,     "unit_cost_eur": 36.74, "tooling_pct": 7.8},
    {"quantity": 100,    "unit_cost_eur": 31.52, "tooling_pct": 4.3},
    {"quantity": 500,    "unit_cost_eur": 28.47, "tooling_pct": 0.84},
    {"quantity": 1000,   "unit_cost_eur": 27.21, "tooling_pct": 0.44},
    {"quantity": 5000,   "unit_cost_eur": 26.48, "tooling_pct": 0.09},
    {"quantity": 10000,  "unit_cost_eur": 26.31, "tooling_pct": 0.05}
  ]
}
```

### 5.4 Location comparison endpoint

```http
GET /api/v1/cbe/analytics/location-comparison?part_id=550e8400-...&quantity=500
```

```json
{
  "part_id": "550e8400-...",
  "quantity": 500,
  "locations": [
    {"location_code": "DE", "unit_cost_eur": 42.18, "labor_pct": 28.4, "confidence_band": "HIGH"},
    {"location_code": "PL", "unit_cost_eur": 28.47, "labor_pct": 25.4, "confidence_band": "MEDIUM"},
    {"location_code": "MX", "unit_cost_eur": 22.31, "labor_pct": 18.2, "confidence_band": "MEDIUM"},
    {"location_code": "CN", "unit_cost_eur": 16.84, "labor_pct": 14.1, "confidence_band": "LOW"},
    {"location_code": "IN", "unit_cost_eur": 14.92, "labor_pct": 12.3, "confidence_band": "LOW"}
  ],
  "cheapest": "IN",
  "recommended": "PL",
  "recommendation_reason": "Best risk-adjusted cost considering confidence and logistics"
}
```

### 5.5 Material sensitivity

```http
GET /api/v1/cbe/analytics/material-sensitivity
    ?breakdown_id=7a2b3c4d-...&price_range_pct=50&steps=10
```

```json
{
  "breakdown_id": "7a2b3c4d-...",
  "base_material_price_eur_kg": 0.82,
  "base_unit_cost_eur": 28.47,
  "sensitivity": [
    {"material_price_delta_pct": -50, "unit_cost_eur": 19.24, "delta_pct": -32.4},
    {"material_price_delta_pct": -25, "unit_cost_eur": 23.85, "delta_pct": -16.2},
    {"material_price_delta_pct":   0, "unit_cost_eur": 28.47, "delta_pct":   0.0},
    {"material_price_delta_pct": +25, "unit_cost_eur": 33.08, "delta_pct": +16.2},
    {"material_price_delta_pct": +50, "unit_cost_eur": 37.70, "delta_pct": +32.4}
  ],
  "material_elasticity": 0.648
}
```

### 5.6 FastAPI implementation sketch

```python
from fastapi import APIRouter, Depends, HTTPException, status
from uuid import UUID

router = APIRouter(prefix="/api/v1/cbe", tags=["cost-breakdown-engine"])

@router.post("/breakdowns", status_code=status.HTTP_201_CREATED,
             response_model=CostBreakdownResponse)
async def create_breakdown(
    req: CostBreakdownRequest,
    engine: CostBreakdownEngine = Depends(get_engine),
    user: TokenPayload = Depends(require_role("CBE_ANALYST")),
) -> CostBreakdownResponse:
    result = await engine.breakdown(req)
    await db.save_breakdown(result, created_by=user.sub)
    return CostBreakdownResponse.from_result(result)

@router.post("/breakdowns/{breakdown_id}/approve",
             response_model=BreakdownStatusResponse)
async def approve_breakdown(
    breakdown_id: UUID,
    body: ApproveRequest,
    db: AsyncpgPool = Depends(get_db),
    user: TokenPayload = Depends(require_role("CBE_APPROVER")),
) -> BreakdownStatusResponse:
    bd = await db.get_breakdown(breakdown_id)
    if bd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Breakdown not found")
    if bd["status"] not in ("CALCULATED", "REVIEWED"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Cannot approve breakdown in status {bd['status']}")
    await db.update_breakdown_status(
        breakdown_id, "APPROVED",
        approved_by=user.sub, note=body.note
    )
    return BreakdownStatusResponse(breakdown_id=breakdown_id, status="APPROVED")

@router.get("/analytics/location-comparison",
            response_model=LocationComparisonResponse)
async def location_comparison(
    part_id: UUID,
    quantity: Decimal = Decimal("100"),
    engine: CostBreakdownEngine = Depends(get_engine),
    user: TokenPayload = Depends(require_role("CBE_ANALYST")),
) -> LocationComparisonResponse:
    tasks = [
        engine.breakdown(build_req(part_id, quantity, loc))
        for loc in ("DE", "PL", "MX", "CN", "IN")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return build_comparison_response(results)
```
