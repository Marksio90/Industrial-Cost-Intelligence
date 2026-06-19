# Cost History Engine — Cost Snapshot, Quote, RFQ & Supplier Price History

**Module:** Cost History Engine (CHE)
**Document:** 02 — Cost Snapshot, Quote, RFQ & Supplier Price History
**Platform:** Industrial Cost Intelligence
**Stack:** PostgreSQL 16+, Kafka 3+, Redis 7+, Python 3.12+
**Status:** Production

---

## Table of Contents

- [Section 3 — Cost Snapshot Model](#section-3--cost-snapshot-model)
  - [CostSnapshot Entity](#costsnapshot-entity)
  - [SnapshotLineItem Entity](#snapshotlineitem-entity)
  - [SnapshotMeta Entity](#snapshotmeta-entity)
  - [Python Dataclasses](#python-dataclasses)
  - [CostSnapshotBuilder](#costsnapshotbuilder)
  - [CostSnapshotComparator](#costsnapshotcomparator)
  - [Snapshot Status Machine](#snapshot-status-machine)
- [Section 4 — Quote History](#section-4--quote-history)
  - [Quote Entity](#quote-entity)
  - [QuoteLine Entity](#quoteline-entity)
  - [QuoteVersion Entity](#quoteversion-entity)
  - [QuoteApproval Entity](#quoteapproval-entity)
  - [QuotePricer](#quotepricer)
  - [Volume Discount Table](#volume-discount-table)
  - [Approval Threshold Table](#approval-threshold-table)
- [Section 5 — RFQ History](#section-5--rfq-history)
  - [RFQRound Entity](#rfqround-entity)
  - [RFQLine Entity](#rfqline-entity)
  - [RFQBid Entity](#rfqbid-entity)
  - [BidComparison Entity](#bidcomparison-entity)
  - [RFQScoringEngine](#rfqscoringengine)
- [Section 6 — Supplier Price History](#section-6--supplier-price-history)
  - [SupplierPriceRecord Entity](#supplierpricereport-entity)
  - [PriceAdjustment Entity](#priceadjustment-entity)
  - [IndexLink Entity](#indexlink-entity)
  - [Commodity Index Connector Table](#commodity-index-connector-table)
  - [SupplierPriceTrendAnalyzer](#supplierpricetrendanalyzer)
  - [Price Competitiveness Matrix Table](#price-competitiveness-matrix-table)

---

## Section 3 — Cost Snapshot Model

A **CostSnapshot** is an immutable, versioned record of a calculated cost at a specific point in time. Snapshots are created by the Cost Calculation Engine (CCE), the RFQ Engine, or manually by cost engineers. Once approved, a snapshot is never mutated — it is superseded by a new version. The `snapshot_hash` field (SHA-256 of the canonical JSON representation) guarantees tamper detection and enables audit trails across ERP integrations.

Cross-references:
- Material costs → Material Intelligence Engine (MIE) `material_id`
- Process costs → Manufacturing Process Engine (MPE) `process_id`
- Supplier costs → Supplier Intelligence Engine (SIE) `supplier_id`

---

### CostSnapshot Entity

**Table:** `cost_snapshots`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `snapshot_id` | UUID | PK | Surrogate primary key |
| `snapshot_type` | ENUM | NOT NULL | `PART_COST`, `RFQ`, `QUOTE`, `BUDGET`, `ACTUALS`, `BENCHMARK` |
| `reference_id` | UUID | NULLABLE | FK to source entity (`quote_id`, `rfq_id`, etc.) |
| `reference_type` | VARCHAR(50) | NULLABLE | `'QUOTE'`, `'RFQ'`, `'BUDGET'`, `'ACTUALS'` |
| `part_number` | VARCHAR(100) | NOT NULL | Part number as defined in MIE |
| `revision` | VARCHAR(20) | NULLABLE | Drawing/BOM revision level |
| `bom_level` | INT | DEFAULT 0 | `0` = top-level assembly, `1+` = sub-assembly depth |
| `currency` | CHAR(3) | DEFAULT `'EUR'` | ISO 4217 currency code of original cost |
| `total_cost_eur` | NUMERIC(18,6) | NOT NULL | Total rolled-up cost in EUR |
| `material_cost` | NUMERIC(18,6) | NULLABLE | Aggregated raw material cost |
| `process_cost` | NUMERIC(18,6) | NULLABLE | Aggregated manufacturing process cost |
| `overhead_cost` | NUMERIC(18,6) | NULLABLE | Factory overhead allocation |
| `tooling_cost` | NUMERIC(18,6) | NULLABLE | Amortised tooling cost per unit |
| `logistics_cost` | NUMERIC(18,6) | NULLABLE | Inbound/outbound freight and duties |
| `profit_margin_pct` | NUMERIC(8,4) | NULLABLE | Applied margin percentage (for quote-based snapshots) |
| `valid_from` | TIMESTAMPTZ | NOT NULL | Start of validity window |
| `valid_until` | TIMESTAMPTZ | NULLABLE | End of validity window; NULL = indefinitely valid |
| `status` | ENUM | DEFAULT `'DRAFT'` | `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `SUPERSEDED`, `ARCHIVED` |
| `created_by` | UUID | NOT NULL | FK to identity provider (user UUID) |
| `approved_by` | UUID | NULLABLE | FK to approving user UUID |
| `approved_at` | TIMESTAMPTZ | NULLABLE | Timestamp of approval action |
| `version` | INT | NOT NULL DEFAULT 1 | Monotonically increasing version within a part/reference |
| `superseded_by` | UUID | FK SELF NULLABLE | Points to the replacement snapshot UUID |
| `snapshot_hash` | CHAR(64) | NULLABLE | SHA-256 of the canonical JSON payload |
| `tags` | JSONB | DEFAULT `'{}'` | Arbitrary key-value metadata (project codes, cost centre, etc.) |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | NULLABLE | Last update timestamp (status changes only) |

**Indexes:**
```sql
CREATE UNIQUE INDEX uq_snapshot_hash ON cost_snapshots (snapshot_hash) WHERE snapshot_hash IS NOT NULL;
CREATE INDEX idx_snapshot_part_version ON cost_snapshots (part_number, version DESC);
CREATE INDEX idx_snapshot_reference ON cost_snapshots (reference_id, reference_type);
CREATE INDEX idx_snapshot_valid_window ON cost_snapshots (valid_from, valid_until) WHERE status = 'APPROVED';
```

---

### SnapshotLineItem Entity

**Table:** `snapshot_line_items`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `line_id` | UUID | PK | Surrogate primary key |
| `snapshot_id` | UUID | FK `cost_snapshots` NOT NULL | Parent snapshot |
| `line_type` | ENUM | NOT NULL | `MATERIAL`, `PROCESS`, `SUPPLIER`, `OVERHEAD`, `TOOLING` |
| `reference_id` | UUID | NULLABLE | FK to MIE material / MPE process / SIE supplier record |
| `description` | VARCHAR(500) | NULLABLE | Human-readable line description |
| `qty` | NUMERIC(18,6) | NOT NULL | Quantity consumed per assembly unit |
| `unit` | VARCHAR(20) | NOT NULL | Unit of measure: `KG`, `PIECE`, `HR`, `METER`, `LITER` |
| `unit_cost` | NUMERIC(18,6) | NOT NULL | Cost per unit in `currency` of the parent snapshot |
| `total_cost` | NUMERIC(18,6) | GENERATED ALWAYS AS `(qty * unit_cost)` STORED | Computed line total |
| `cost_driver` | VARCHAR(100) | NULLABLE | Identifies the cost driver: `MATERIAL_WEIGHT_KG`, `MACHINE_TIME_HR`, `SETUP_TIME_HR` |
| `cost_driver_value` | NUMERIC(18,6) | NULLABLE | Measured value of the cost driver |
| `notes` | TEXT | NULLABLE | Free-text engineering notes |
| `sort_order` | INT | NULLABLE | Display ordering within the snapshot |

**DDL excerpt:**
```sql
ALTER TABLE snapshot_line_items
  ADD CONSTRAINT fk_sli_snapshot FOREIGN KEY (snapshot_id)
      REFERENCES cost_snapshots (snapshot_id) ON DELETE CASCADE,
  ADD CONSTRAINT chk_sli_qty CHECK (qty > 0),
  ADD CONSTRAINT chk_sli_unit_cost CHECK (unit_cost >= 0);
```

---

### SnapshotMeta Entity

**Table:** `snapshot_meta`

One-to-one with `cost_snapshots`. Carries provenance and quality indicators that are used by the CCE audit trail and data-quality dashboards.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `meta_id` | UUID | PK | Surrogate primary key |
| `snapshot_id` | UUID | FK `cost_snapshots` UNIQUE NOT NULL | Parent snapshot (1-to-1) |
| `source_system` | VARCHAR(50) | NULLABLE | Originating system: `CCE`, `ERP`, `MANUAL`, `RFQ_ENGINE` |
| `calculation_engine_version` | VARCHAR(20) | NULLABLE | Semantic version of the CCE that produced the snapshot |
| `parameter_set_id` | UUID | NULLABLE | FK to parameter set used during calculation |
| `confidence_score` | NUMERIC(4,3) | CHECK (`0 <= value <= 1`) | CCE model confidence (0.000–1.000) |
| `data_completeness_pct` | NUMERIC(5,2) | CHECK (`0 <= value <= 100`) | Percentage of required inputs that were fully populated |
| `notes` | TEXT | NULLABLE | Free-text provenance notes |
| `extra_metadata` | JSONB | NULLABLE | Unstructured extension point for future attributes |

---

### Python Dataclasses

```python
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid
import hashlib
import json


class SnapshotType(str, Enum):
    PART_COST = "PART_COST"
    RFQ = "RFQ"
    QUOTE = "QUOTE"
    BUDGET = "BUDGET"
    ACTUALS = "ACTUALS"
    BENCHMARK = "BENCHMARK"


class SnapshotStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class LineType(str, Enum):
    MATERIAL = "MATERIAL"
    PROCESS = "PROCESS"
    SUPPLIER = "SUPPLIER"
    OVERHEAD = "OVERHEAD"
    TOOLING = "TOOLING"


@dataclass
class SnapshotLineItem:
    line_id: uuid.UUID
    snapshot_id: uuid.UUID
    line_type: LineType
    qty: Decimal
    unit: str
    unit_cost: Decimal
    description: str = ""
    reference_id: Optional[uuid.UUID] = None
    cost_driver: Optional[str] = None
    cost_driver_value: Optional[Decimal] = None
    notes: Optional[str] = None
    sort_order: int = 0

    @property
    def total_cost(self) -> Decimal:
        return self.qty * self.unit_cost


@dataclass
class SnapshotMeta:
    meta_id: uuid.UUID
    snapshot_id: uuid.UUID
    source_system: str
    calculation_engine_version: str
    confidence_score: Decimal          # 0.000 – 1.000
    data_completeness_pct: Decimal     # 0.00 – 100.00
    parameter_set_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    extra_metadata: dict = field(default_factory=dict)


@dataclass
class CostSnapshot:
    snapshot_id: uuid.UUID
    snapshot_type: SnapshotType
    part_number: str
    currency: str
    total_cost_eur: Decimal
    valid_from: datetime
    created_by: uuid.UUID
    reference_id: Optional[uuid.UUID] = None
    reference_type: Optional[str] = None
    revision: Optional[str] = None
    bom_level: int = 0
    material_cost: Decimal = Decimal("0")
    process_cost: Decimal = Decimal("0")
    overhead_cost: Decimal = Decimal("0")
    tooling_cost: Decimal = Decimal("0")
    logistics_cost: Decimal = Decimal("0")
    profit_margin_pct: Optional[Decimal] = None
    valid_until: Optional[datetime] = None
    status: SnapshotStatus = SnapshotStatus.DRAFT
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    version: int = 1
    superseded_by: Optional[uuid.UUID] = None
    snapshot_hash: Optional[str] = None
    tags: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    line_items: list[SnapshotLineItem] = field(default_factory=list)
    meta: Optional[SnapshotMeta] = None
```

---

### CostSnapshotBuilder

```python
@dataclass
class SnapshotDelta:
    snapshot_a_id: uuid.UUID
    snapshot_b_id: uuid.UUID
    absolute_delta_eur: Decimal
    pct_change: Decimal
    line_deltas: list[dict]
    drivers_changed: list[str]
    comparison_timestamp: datetime = field(default_factory=datetime.utcnow)


class CostSnapshotBuilder:
    """
    Fluent builder for creating immutable CostSnapshot records.

    Usage
    -----
    snapshot = (
        CostSnapshotBuilder(SnapshotType.QUOTE, "PN-5501-A", user_id)
        .with_reference(quote_id, "QUOTE")
        .with_tags({"project": "P-2024-007", "cost_centre": "CC-MFG-01"})
        .with_meta("CCE", "2.4.1", Decimal("0.92"), Decimal("98.50"))
        .add_line(LineType.MATERIAL, "Stainless Steel 316L Sheet", Decimal("4.5"), "KG",
                  Decimal("3.20"), cost_driver="MATERIAL_WEIGHT_KG", cost_driver_value=Decimal("4.5"))
        .add_line(LineType.PROCESS, "CNC Milling — 3-Axis", Decimal("0.75"), "HR",
                  Decimal("85.00"), cost_driver="MACHINE_TIME_HR", cost_driver_value=Decimal("0.75"))
        .add_line(LineType.OVERHEAD, "Factory Overhead Allocation", Decimal("1"), "PIECE",
                  Decimal("12.40"))
        .finalize(valid_from=datetime(2024, 6, 1, tzinfo=timezone.utc))
    )
    """

    def __init__(
        self,
        snapshot_type: SnapshotType,
        part_number: str,
        created_by: uuid.UUID,
    ) -> None:
        self._snapshot_id = uuid.uuid4()
        self._type = snapshot_type
        self._part_number = part_number
        self._created_by = created_by
        self._line_items: list[SnapshotLineItem] = []
        self._meta: Optional[SnapshotMeta] = None
        self._tags: dict = {}
        self._reference_id: Optional[uuid.UUID] = None
        self._reference_type: Optional[str] = None

    def with_reference(
        self, reference_id: uuid.UUID, reference_type: str
    ) -> "CostSnapshotBuilder":
        self._reference_id = reference_id
        self._reference_type = reference_type
        return self

    def with_tags(self, tags: dict) -> "CostSnapshotBuilder":
        self._tags.update(tags)
        return self

    def with_meta(
        self,
        source_system: str,
        engine_version: str,
        confidence_score: Decimal,
        data_completeness_pct: Decimal,
    ) -> "CostSnapshotBuilder":
        self._meta = SnapshotMeta(
            meta_id=uuid.uuid4(),
            snapshot_id=self._snapshot_id,
            source_system=source_system,
            calculation_engine_version=engine_version,
            confidence_score=confidence_score,
            data_completeness_pct=data_completeness_pct,
        )
        return self

    def add_line(
        self,
        line_type: LineType,
        description: str,
        qty: Decimal,
        unit: str,
        unit_cost: Decimal,
        cost_driver: Optional[str] = None,
        cost_driver_value: Optional[Decimal] = None,
        reference_id: Optional[uuid.UUID] = None,
    ) -> "CostSnapshotBuilder":
        item = SnapshotLineItem(
            line_id=uuid.uuid4(),
            snapshot_id=self._snapshot_id,
            line_type=line_type,
            description=description,
            qty=qty,
            unit=unit,
            unit_cost=unit_cost,
            cost_driver=cost_driver,
            cost_driver_value=cost_driver_value,
            reference_id=reference_id,
            sort_order=len(self._line_items),
        )
        self._line_items.append(item)
        return self

    def _aggregate_costs(self) -> dict[LineType, Decimal]:
        totals: dict[LineType, Decimal] = {t: Decimal("0") for t in LineType}
        for item in self._line_items:
            totals[item.line_type] += item.total_cost
        return totals

    def calculate_hash(self, snapshot: "CostSnapshot") -> str:
        """SHA-256 of canonical JSON (sorted keys, fixed-precision decimals)."""
        canonical = {
            "snapshot_id": str(snapshot.snapshot_id),
            "snapshot_type": snapshot.snapshot_type.value,
            "part_number": snapshot.part_number,
            "currency": snapshot.currency,
            "total_cost_eur": str(snapshot.total_cost_eur),
            "material_cost": str(snapshot.material_cost),
            "process_cost": str(snapshot.process_cost),
            "overhead_cost": str(snapshot.overhead_cost),
            "tooling_cost": str(snapshot.tooling_cost),
            "logistics_cost": str(snapshot.logistics_cost),
            "valid_from": snapshot.valid_from.isoformat(),
            "version": snapshot.version,
            "line_items": [
                {
                    "line_id": str(item.line_id),
                    "line_type": item.line_type.value,
                    "qty": str(item.qty),
                    "unit_cost": str(item.unit_cost),
                    "sort_order": item.sort_order,
                }
                for item in sorted(snapshot.line_items, key=lambda x: x.sort_order)
            ],
        }
        payload = json.dumps(canonical, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def finalize(self, valid_from: Optional[datetime] = None) -> "CostSnapshot":
        """
        Build and validate the snapshot.

        Raises
        ------
        ValueError
            If no line items have been added, or if aggregated totals are negative.
        """
        if not self._line_items:
            raise ValueError("CostSnapshot must have at least one line item.")

        costs = self._aggregate_costs()
        total = sum(costs.values(), Decimal("0"))

        if total < Decimal("0"):
            raise ValueError(
                f"Aggregated total_cost_eur is negative ({total}). "
                "Review line items for data errors."
            )

        snapshot = CostSnapshot(
            snapshot_id=self._snapshot_id,
            snapshot_type=self._type,
            part_number=self._part_number,
            currency="EUR",
            total_cost_eur=total,
            material_cost=costs[LineType.MATERIAL],
            process_cost=costs[LineType.PROCESS],
            overhead_cost=costs[LineType.OVERHEAD],
            tooling_cost=costs[LineType.TOOLING],
            logistics_cost=Decimal("0"),
            valid_from=valid_from or datetime.utcnow(),
            created_by=self._created_by,
            reference_id=self._reference_id,
            reference_type=self._reference_type,
            tags=self._tags,
            line_items=self._line_items,
            meta=self._meta,
        )
        snapshot.snapshot_hash = self.calculate_hash(snapshot)
        return snapshot
```

---

### CostSnapshotComparator

```python
class CostSnapshotComparator:
    """
    Computes deltas between two CostSnapshot records and identifies cost drivers
    that changed between versions.

    The comparator operates on in-memory CostSnapshot objects. Persistence of
    SnapshotDelta records is the responsibility of the calling service.
    """

    _COST_FIELDS: tuple[str, ...] = (
        "total_cost_eur",
        "material_cost",
        "process_cost",
        "overhead_cost",
        "tooling_cost",
        "logistics_cost",
    )

    def compare(
        self,
        snapshot_a: CostSnapshot,
        snapshot_b: CostSnapshot,
    ) -> SnapshotDelta:
        """
        Compare snapshot_a (earlier / baseline) against snapshot_b (later / revised).

        Returns
        -------
        SnapshotDelta
            Absolute and percentage cost change, per-field line deltas, and
            a list of cost drivers whose values changed.

        Raises
        ------
        ValueError
            If either snapshot is not in APPROVED or SUPERSEDED status, indicating
            the cost basis may not be finalised.
        """
        allowed_statuses = {SnapshotStatus.APPROVED, SnapshotStatus.SUPERSEDED}
        if snapshot_a.status not in allowed_statuses:
            raise ValueError(
                f"Snapshot A ({snapshot_a.snapshot_id}) has status "
                f"'{snapshot_a.status.value}'; comparison requires APPROVED or SUPERSEDED."
            )
        if snapshot_b.status not in allowed_statuses:
            raise ValueError(
                f"Snapshot B ({snapshot_b.snapshot_id}) has status "
                f"'{snapshot_b.status.value}'; comparison requires APPROVED or SUPERSEDED."
            )

        absolute_delta_eur = snapshot_b.total_cost_eur - snapshot_a.total_cost_eur

        if snapshot_a.total_cost_eur == Decimal("0"):
            pct_change = Decimal("0")
        else:
            pct_change = (
                absolute_delta_eur / snapshot_a.total_cost_eur * Decimal("100")
            ).quantize(Decimal("0.0001"))

        line_deltas = self._compute_line_deltas(snapshot_a, snapshot_b)
        drivers_changed = self._identify_changed_drivers(snapshot_a, snapshot_b)

        return SnapshotDelta(
            snapshot_a_id=snapshot_a.snapshot_id,
            snapshot_b_id=snapshot_b.snapshot_id,
            absolute_delta_eur=absolute_delta_eur,
            pct_change=pct_change,
            line_deltas=line_deltas,
            drivers_changed=drivers_changed,
        )

    def _compute_line_deltas(
        self,
        snapshot_a: CostSnapshot,
        snapshot_b: CostSnapshot,
    ) -> list[dict]:
        """
        Produce a field-level breakdown of cost changes across the top-level
        cost components (material, process, overhead, tooling, logistics).
        """
        deltas: list[dict] = []
        for field_name in self._COST_FIELDS:
            val_a: Decimal = getattr(snapshot_a, field_name, Decimal("0")) or Decimal("0")
            val_b: Decimal = getattr(snapshot_b, field_name, Decimal("0")) or Decimal("0")
            delta = val_b - val_a
            if val_a == Decimal("0"):
                pct = Decimal("0")
            else:
                pct = (delta / val_a * Decimal("100")).quantize(Decimal("0.0001"))
            deltas.append(
                {
                    "field": field_name,
                    "value_a": str(val_a),
                    "value_b": str(val_b),
                    "delta_eur": str(delta),
                    "delta_pct": str(pct),
                }
            )
        return deltas

    def _identify_changed_drivers(
        self,
        snapshot_a: CostSnapshot,
        snapshot_b: CostSnapshot,
    ) -> list[str]:
        """
        Compare line items by cost_driver key. Returns distinct cost driver labels
        whose associated value or unit_cost changed between snapshots.
        """
        drivers_a: dict[str, tuple[Optional[Decimal], Decimal]] = {
            item.cost_driver: (item.cost_driver_value, item.unit_cost)
            for item in snapshot_a.line_items
            if item.cost_driver
        }
        drivers_b: dict[str, tuple[Optional[Decimal], Decimal]] = {
            item.cost_driver: (item.cost_driver_value, item.unit_cost)
            for item in snapshot_b.line_items
            if item.cost_driver
        }

        changed: list[str] = []
        all_drivers = set(drivers_a) | set(drivers_b)
        for driver in sorted(all_drivers):
            if drivers_a.get(driver) != drivers_b.get(driver):
                changed.append(driver)
        return changed

    def summarise(self, delta: SnapshotDelta) -> str:
        """Return a human-readable one-liner suitable for change_summary fields."""
        direction = "increase" if delta.absolute_delta_eur >= Decimal("0") else "decrease"
        abs_val = abs(delta.absolute_delta_eur)
        abs_pct = abs(delta.pct_change)
        drivers = ", ".join(delta.drivers_changed) if delta.drivers_changed else "none identified"
        return (
            f"Cost {direction} of EUR {abs_val:.4f} ({abs_pct:.2f}%) between "
            f"snapshot {delta.snapshot_a_id} → {delta.snapshot_b_id}. "
            f"Changed drivers: {drivers}."
        )
```

---

### Snapshot Status Machine

#### State Diagram

```
                     ┌──────────────────────────────────────────────────────┐
                     │                                                      │
    create()         ▼         submit_for_approval()    approve()          │
  ──────────► [DRAFT] ─────────────────────────► [PENDING_APPROVAL] ──────► [APPROVED]
                  │                                      │                      │
                  │ discard()                            │ reject() / recall()  │ supersede()
                  ▼                                      ▼                      ▼
             [ARCHIVED]                             [DRAFT]              [SUPERSEDED]
                  ▲                                                            │
                  └────────────────────────────────────────────────────────────┘
                                        archive()
```

#### Allowed Transitions

| From | To | Trigger | Who | Business Rules |
|---|---|---|---|---|
| `DRAFT` | `PENDING_APPROVAL` | `submit_for_approval()` | Cost Engineer | All mandatory fields populated; `snapshot_hash` must be set; at least one line item present |
| `DRAFT` | `ARCHIVED` | `discard()` | Cost Engineer / System | Only allowed when no quote or RFQ references this snapshot |
| `PENDING_APPROVAL` | `APPROVED` | `approve()` | Approver (role-based) | Approver UUID recorded; `approved_at` set; immutable after this point |
| `PENDING_APPROVAL` | `DRAFT` | `reject()` | Approver | `rejection_reason` must be provided; snapshot returned to engineer for correction |
| `APPROVED` | `SUPERSEDED` | `supersede(new_snapshot_id)` | System (CCE) / Cost Engineer | `superseded_by` FK is set to the replacement snapshot UUID; the replacement snapshot must itself be `APPROVED` |
| `SUPERSEDED` | `ARCHIVED` | `archive()` | System (scheduled) | Executed by nightly retention job after configurable retention window (default 7 years) |

#### Business Rules

1. **Immutability after APPROVED**: No field on `cost_snapshots` or `snapshot_line_items` may be updated once `status = APPROVED`. Any correction requires creating a new snapshot version and setting `superseded_by`.
2. **Hash integrity check**: Before transition to `PENDING_APPROVAL`, the system recomputes the SHA-256 hash and validates it matches `snapshot_hash`. A mismatch aborts the transition and raises a `SnapshotTamperError`.
3. **Version monotonicity**: When a new snapshot is created for the same `part_number` and `reference_id`, `version` must be `MAX(version) + 1` for that combination. Enforced by a PostgreSQL trigger.
4. **Orphan prevention**: `DRAFT` snapshots older than 90 days with no activity are automatically transitioned to `ARCHIVED` by the CHE housekeeping job.
5. **Audit log**: Every status transition is written to the `snapshot_audit_log` table (not shown here) with `changed_by`, `changed_at`, `from_status`, `to_status`, and `reason`.

---

## Section 4 — Quote History

Quotes represent binding or indicative commercial offers made to customers. Every quote is linked to a `CostSnapshot` that defines its cost basis, ensuring traceability from commercial price back to underlying cost drivers. Quote revisions never overwrite previous versions; instead, a new quote record is created and the prior version is linked via `superseded_by`.

Cross-references:
- Cost basis → `cost_snapshots.snapshot_id` (Section 3)
- BOM costing → `cost_snapshots.snapshot_id` where `snapshot_type = 'PART_COST'`
- Customer → CRM/ERP external reference (`customer_id`)

---

### Quote Entity

**Table:** `quotes`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `quote_id` | UUID | PK | Surrogate primary key |
| `quote_number` | VARCHAR(50) | UNIQUE NOT NULL | Human-readable identifier, e.g. `QUO-2024-00142` |
| `revision_number` | INT | NOT NULL DEFAULT 1 | Revision counter within a quote family |
| `quote_type` | ENUM | NOT NULL | `INITIAL`, `REVISED`, `FINAL`, `BINDING`, `INDICATIVE` |
| `customer_id` | UUID | NOT NULL | FK to CRM/ERP customer record (external system) |
| `part_number` | VARCHAR(100) | NOT NULL | Part number being quoted |
| `revision` | VARCHAR(20) | NULLABLE | Drawing/BOM revision level |
| `bom_snapshot_id` | UUID | FK `cost_snapshots` NULLABLE | Snapshot used for BOM cost roll-up |
| `snapshot_id` | UUID | FK `cost_snapshots` NOT NULL | Approved cost snapshot forming the price basis |
| `status` | ENUM | DEFAULT `'DRAFT'` | `DRAFT`, `SUBMITTED`, `NEGOTIATING`, `ACCEPTED`, `REJECTED`, `EXPIRED`, `WITHDRAWN` |
| `total_price_eur` | NUMERIC(18,6) | NOT NULL | Total quoted price in EUR |
| `target_price_eur` | NUMERIC(18,6) | NULLABLE | Internal target price before customer submission |
| `floor_price_eur` | NUMERIC(18,6) | NOT NULL | Minimum acceptable price (cost × min margin); never disclosed to customer |
| `margin_pct` | NUMERIC(8,4) | NOT NULL | Achieved margin: `(price − cost) / cost × 100` |
| `currency` | CHAR(3) | DEFAULT `'EUR'` | ISO 4217 currency of the quote |
| `valid_until` | DATE | NOT NULL | Expiry date of the commercial offer |
| `incoterms` | VARCHAR(20) | NULLABLE | Delivery terms: `EXW`, `FCA`, `DAP`, `DDP`, etc. |
| `payment_terms_days` | INT | DEFAULT 30 | Net payment days |
| `created_by` | UUID | NOT NULL | FK to user who created the quote |
| `submitted_at` | TIMESTAMPTZ | NULLABLE | Timestamp the quote was sent to the customer |
| `accepted_at` | TIMESTAMPTZ | NULLABLE | Timestamp of customer acceptance |
| `rejected_at` | TIMESTAMPTZ | NULLABLE | Timestamp of customer rejection |
| `rejection_reason` | TEXT | NULLABLE | Free-text rejection reason from customer |
| `superseded_by` | UUID | FK SELF NULLABLE | UUID of the revised quote that replaces this one |
| `version` | INT | NOT NULL DEFAULT 1 | Monotonically increasing version |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Indexes:**
```sql
CREATE INDEX idx_quotes_part_customer ON quotes (part_number, customer_id, status);
CREATE INDEX idx_quotes_snapshot ON quotes (snapshot_id);
CREATE INDEX idx_quotes_valid_until ON quotes (valid_until) WHERE status IN ('SUBMITTED','NEGOTIATING');
```

---

### QuoteLine Entity

**Table:** `quote_lines`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `line_id` | UUID | PK | Surrogate primary key |
| `quote_id` | UUID | FK `quotes` NOT NULL | Parent quote |
| `line_number` | INT | NOT NULL | Sequential line number within the quote |
| `part_number` | VARCHAR(100) | NULLABLE | Part number for this line (may differ from header for multi-part quotes) |
| `description` | VARCHAR(500) | NULLABLE | Line item description |
| `qty` | NUMERIC(18,6) | NOT NULL | Quoted quantity |
| `unit` | VARCHAR(20) | NOT NULL | Unit of measure |
| `unit_price_eur` | NUMERIC(18,6) | NOT NULL | Quoted unit price in EUR |
| `total_price_eur` | NUMERIC(18,6) | GENERATED ALWAYS AS `(qty * unit_price_eur)` STORED | Computed line total |
| `unit_cost_eur` | NUMERIC(18,6) | NULLABLE | Internal cost per unit (from linked snapshot line) |
| `margin_pct` | NUMERIC(8,4) | NULLABLE | Line-level margin percentage |
| `discount_pct` | NUMERIC(8,4) | DEFAULT 0 | Volume or negotiated discount applied to list price |
| `snapshot_line_id` | UUID | FK `snapshot_line_items` NULLABLE | Traceability link to the source cost line |

---

### QuoteVersion Entity

**Table:** `quote_versions`

Append-only audit trail. One row is inserted for every mutation to the parent `quotes` record.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `version_id` | UUID | PK | Surrogate primary key |
| `quote_id` | UUID | FK `quotes` NOT NULL | Parent quote |
| `version_number` | INT | NOT NULL | Version counter matching `quotes.version` at time of change |
| `changed_by` | UUID | NOT NULL | FK to user who made the change |
| `changed_at` | TIMESTAMPTZ | DEFAULT NOW() | Timestamp of change |
| `change_summary` | TEXT | NULLABLE | Human-readable description of what changed and why |
| `price_before` | NUMERIC(18,6) | NULLABLE | `total_price_eur` before the change |
| `price_after` | NUMERIC(18,6) | NULLABLE | `total_price_eur` after the change |
| `diff_jsonb` | JSONB | NULLABLE | Field-level diff: `{"field": {"before": ..., "after": ...}}` |

---

### QuoteApproval Entity

**Table:** `quote_approvals`

One row per approval tier per quote. Multiple rows exist when escalation occurs through tiers.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `approval_id` | UUID | PK | Surrogate primary key |
| `quote_id` | UUID | FK `quotes` NOT NULL | Quote under review |
| `approval_tier` | ENUM | NOT NULL | `AUTO`, `MANAGER`, `DIRECTOR`, `EXECUTIVE` |
| `threshold_min_eur` | NUMERIC(18,6) | NULLABLE | Lower bound of the tier's price range |
| `threshold_max_eur` | NUMERIC(18,6) | NULLABLE | Upper bound of the tier's price range |
| `approver_id` | UUID | NULLABLE | FK to assigned approver (NULL for `AUTO`) |
| `approved_at` | TIMESTAMPTZ | NULLABLE | Timestamp of approval |
| `rejected_at` | TIMESTAMPTZ | NULLABLE | Timestamp of rejection |
| `rejection_reason` | TEXT | NULLABLE | Mandatory when `rejected_at` is set |
| `override_reason` | TEXT | NULLABLE | Mandatory when a lower-tier approver bypasses their threshold |

---

### QuotePricer

```python
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ProductClass(str, Enum):
    STANDARD = "STANDARD"       # minimum margin 12%
    CUSTOM = "CUSTOM"           # minimum margin 18%
    PROTOTYPE = "PROTOTYPE"     # minimum margin 25%
    NRE = "NRE"                 # minimum margin 30%
    TOOLING = "TOOLING"         # minimum margin 8%


MIN_MARGIN_BY_CLASS: dict[ProductClass, Decimal] = {
    ProductClass.STANDARD:  Decimal("0.12"),
    ProductClass.CUSTOM:    Decimal("0.18"),
    ProductClass.PROTOTYPE: Decimal("0.25"),
    ProductClass.NRE:       Decimal("0.30"),
    ProductClass.TOOLING:   Decimal("0.08"),
}

# (min_qty_inclusive, max_qty_inclusive, discount_fraction)
VOLUME_DISCOUNT_TABLE: list[tuple[int, int, Decimal]] = [
    (1,    9,          Decimal("0.00")),
    (10,   49,         Decimal("0.03")),
    (50,   199,        Decimal("0.07")),
    (200,  499,        Decimal("0.12")),
    (500,  999,        Decimal("0.18")),
    (1000, int(1e9),   Decimal("0.25")),
]

# (tier_label, min_eur_inclusive, max_eur_inclusive)
# None = unbounded on that side
APPROVAL_TIERS: list[tuple[str, Optional[Decimal], Optional[Decimal]]] = [
    ("AUTO",      None,                 Decimal("5000")),
    ("MANAGER",   Decimal("5000.01"),   Decimal("50000")),
    ("DIRECTOR",  Decimal("50000.01"),  Decimal("200000")),
    ("EXECUTIVE", Decimal("200000.01"), None),
]


class QuotePricer:
    """
    Calculates floor price, target price, volume discounts, and required
    approval tier for commercial quotes.

    All monetary inputs and outputs are in EUR. Prices are returned rounded
    to 6 decimal places (NUMERIC(18,6) PostgreSQL precision).
    """

    def calculate_floor_price(
        self,
        total_cost_eur: Decimal,
        product_class: ProductClass,
    ) -> Decimal:
        """
        Floor price = total_cost × (1 + MIN_MARGIN_PCT).

        The floor price is the internal minimum at which a quote may be
        submitted. Quotes priced below the floor price are blocked at the
        DRAFT → SUBMITTED transition.
        """
        if total_cost_eur < Decimal("0"):
            raise ValueError("total_cost_eur cannot be negative.")
        min_margin = MIN_MARGIN_BY_CLASS[product_class]
        return (total_cost_eur * (Decimal("1") + min_margin)).quantize(Decimal("0.000001"))

    def calculate_target_price(
        self,
        total_cost_eur: Decimal,
        product_class: ProductClass,
        target_margin_pct: Decimal,
    ) -> Decimal:
        """
        Target price based on an explicit margin over cost.

        If the requested `target_margin_pct` is below the class minimum,
        the minimum is silently enforced.
        """
        if total_cost_eur < Decimal("0"):
            raise ValueError("total_cost_eur cannot be negative.")
        min_margin = MIN_MARGIN_BY_CLASS[product_class]
        effective_margin = max(target_margin_pct, min_margin)
        return (total_cost_eur * (Decimal("1") + effective_margin)).quantize(Decimal("0.000001"))

    def get_volume_discount(self, qty: int) -> Decimal:
        """
        Look up the applicable volume discount fraction for a given quantity.

        Returns the discount as a fraction (e.g., 0.07 for 7%), not a percentage.
        """
        if qty <= 0:
            raise ValueError(f"Quantity must be positive, got {qty}.")
        for min_qty, max_qty, discount in VOLUME_DISCOUNT_TABLE:
            if min_qty <= qty <= max_qty:
                return discount
        return Decimal("0")

    def apply_volume_discount(self, unit_price_eur: Decimal, qty: int) -> Decimal:
        """
        Returns the discounted unit price after volume bracket lookup.

        Discounted price = unit_price × (1 − discount_fraction).
        """
        discount = self.get_volume_discount(qty)
        return (unit_price_eur * (Decimal("1") - discount)).quantize(Decimal("0.000001"))

    def determine_approval_tier(self, total_price_eur: Decimal) -> str:
        """
        Return the required approval tier label for a given quote total.

        Matches the first tier whose range encompasses `total_price_eur`.
        Falls back to EXECUTIVE for any value not captured by the table.
        """
        for tier, min_val, max_val in APPROVAL_TIERS:
            above_min = (min_val is None) or (total_price_eur >= min_val)
            below_max = (max_val is None) or (total_price_eur <= max_val)
            if above_min and below_max:
                return tier
        return "EXECUTIVE"

    def calculate_margin_pct(
        self, unit_price_eur: Decimal, unit_cost_eur: Decimal
    ) -> Decimal:
        """
        Returns achieved margin as a fraction: (price - cost) / cost.

        Raises ValueError if unit_cost_eur is zero.
        """
        if unit_cost_eur == Decimal("0"):
            raise ValueError("unit_cost_eur cannot be zero when calculating margin.")
        return ((unit_price_eur - unit_cost_eur) / unit_cost_eur).quantize(Decimal("0.0001"))
```

---

### Volume Discount Table

| Units (qty) | Discount % | Discount Fraction |
|---|---|---|
| 1 – 9 | 0% | 0.00 |
| 10 – 49 | 3% | 0.03 |
| 50 – 199 | 7% | 0.07 |
| 200 – 499 | 12% | 0.12 |
| 500 – 999 | 18% | 0.18 |
| 1,000+ | 25% | 0.25 |

Volume discounts are applied to the `unit_price_eur` at the `QuoteLine` level. The resulting price must remain at or above the computed `floor_price_eur` for the relevant `ProductClass`. If a discount would breach the floor, the system caps the discount and logs a `FLOOR_PRICE_CONSTRAINT` warning event to the CHE Kafka topic `che.quote.events`.

---

### Approval Threshold Table

| Tier | Min Total (EUR) | Max Total (EUR) | Approver Role | SLA |
|---|---|---|---|---|
| `AUTO` | — | ≤ 5,000 | System (no human required) | Immediate |
| `MANAGER` | 5,001 | 50,000 | Sales Manager | 24 hours |
| `DIRECTOR` | 50,001 | 200,000 | Sales Director / Pricing Director | 48 hours |
| `EXECUTIVE` | 200,001 | — | VP Sales / CFO | 72 hours |

Threshold boundaries are stored in the `quote_approval_config` table and are configurable without a code deployment. The `QuotePricer.determine_approval_tier()` method must be updated to read from that table in production; the hardcoded `APPROVAL_TIERS` constant serves as the fallback default.

---

## Section 5 — RFQ History

The RFQ (Request for Quotation) subsystem manages multi-round competitive sourcing events. Each `RFQRound` captures a single solicitation cycle; if a round closes without award, a new round is created with `round_number` incremented. Supplier bids are scored by the `RFQScoringEngine` using a weighted multi-criteria model covering price, lead time, quality history (from SIE), and risk (from SIE).

Cross-references:
- Supplier data → Supplier Intelligence Engine (SIE)
- Material specs → Material Intelligence Engine (MIE)
- Cost basis → `cost_snapshots` (Section 3)

---

### RFQRound Entity

**Table:** `rfq_rounds`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `rfq_id` | UUID | PK | Surrogate primary key |
| `rfq_number` | VARCHAR(50) | UNIQUE NULLABLE | Human-readable identifier, e.g. `RFQ-2024-00089` |
| `round_number` | INT | NOT NULL DEFAULT 1 | Round counter for multi-round RFQs |
| `rfq_type` | ENUM | NOT NULL | `OPEN`, `SELECTIVE`, `SINGLE_SOURCE`, `FRAMEWORK`, `SPOT` |
| `status` | ENUM | DEFAULT `'DRAFT'` | `DRAFT`, `ISSUED`, `BIDDING`, `EVALUATION`, `AWARDED`, `CANCELLED`, `NO_AWARD` |
| `part_number` | VARCHAR(100) | NOT NULL | Part number being sourced |
| `target_qty` | NUMERIC(18,6) | NOT NULL | Required quantity |
| `target_price_eur` | NUMERIC(18,6) | NULLABLE | Internal budget target price (confidential) |
| `issued_at` | TIMESTAMPTZ | NULLABLE | Timestamp when RFQ was sent to suppliers |
| `deadline` | TIMESTAMPTZ | NOT NULL | Bid submission deadline |
| `awarded_at` | TIMESTAMPTZ | NULLABLE | Timestamp of award decision |
| `awarded_supplier_id` | UUID | NULLABLE | FK to SIE supplier record |
| `award_reason` | TEXT | NULLABLE | Free-text justification for the award decision |
| `savings_vs_prior_eur` | NUMERIC(18,6) | NULLABLE | EUR savings vs. prior contract or budget |
| `savings_pct` | NUMERIC(8,4) | NULLABLE | Savings as a percentage |
| `currency` | CHAR(3) | DEFAULT `'EUR'` | Currency of all monetary values in this RFQ |
| `created_by` | UUID | NOT NULL | FK to user who created the RFQ |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

---

### RFQLine Entity

**Table:** `rfq_lines`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `line_id` | UUID | PK | Surrogate primary key |
| `rfq_id` | UUID | FK `rfq_rounds` NOT NULL | Parent RFQ round |
| `line_number` | INT | NOT NULL | Sequential line number |
| `part_number` | VARCHAR(100) | NOT NULL | Part number for this line |
| `description` | VARCHAR(500) | NULLABLE | Part description |
| `material_spec` | TEXT | NULLABLE | Material grade and applicable standard (e.g. `EN 10088-2 1.4301`) |
| `process_requirements` | TEXT | NULLABLE | Required manufacturing processes (e.g. `CNC turning, anodising`) |
| `cert_requirements` | TEXT[] | NULLABLE | Required certifications: `{'ISO9001','IATF16949','AS9100'}` |
| `delivery_requirements` | TEXT | NULLABLE | Lead time, packaging, and shipping requirements |
| `target_qty` | NUMERIC(18,6) | NULLABLE | Line-level quantity requirement |
| `target_unit_price_eur` | NUMERIC(18,6) | NULLABLE | Internal target unit price (not shared with suppliers) |
| `drawings_ref` | VARCHAR(200) | NULLABLE | Document management system reference for drawings |
| `technical_notes` | TEXT | NULLABLE | Additional engineering requirements or constraints |

---

### RFQBid Entity

**Table:** `rfq_bids`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `bid_id` | UUID | PK | Surrogate primary key |
| `rfq_id` | UUID | FK `rfq_rounds` NOT NULL | Parent RFQ round |
| `supplier_id` | UUID | NOT NULL | FK to SIE supplier record |
| `bid_price` | NUMERIC(18,6) | NOT NULL | Quoted price in `bid_currency` |
| `bid_currency` | CHAR(3) | DEFAULT `'EUR'` | Currency of the bid |
| `bid_price_eur` | NUMERIC(18,6) | NULLABLE | Bid price normalised to EUR via FX snapshot at submission |
| `lead_time_days` | INT | NOT NULL | Committed delivery lead time in calendar days |
| `moq` | INT | NOT NULL DEFAULT 1 | Minimum order quantity |
| `valid_until` | DATE | NULLABLE | Date until which the bid price is valid |
| `bid_status` | ENUM | DEFAULT `'SUBMITTED'` | `SUBMITTED`, `QUALIFIED`, `DISQUALIFIED`, `SHORTLISTED`, `AWARDED`, `REJECTED` |
| `technical_score` | NUMERIC(5,2) | CHECK (`0 <= value <= 100`) | Technical evaluation score from engineering review |
| `commercial_score` | NUMERIC(5,2) | CHECK (`0 <= value <= 100`) | Commercial evaluation score from procurement review |
| `total_score` | NUMERIC(5,2) | NULLABLE | Weighted composite score from `RFQScoringEngine` |
| `disqualification_reason` | TEXT | NULLABLE | Mandatory when `bid_status = 'DISQUALIFIED'` |
| `submitted_at` | TIMESTAMPTZ | DEFAULT NOW() | Bid submission timestamp |
| `notes` | TEXT | NULLABLE | Procurement notes on the bid |

---

### BidComparison Entity

**Table:** `bid_comparisons`

One row per bid per RFQ evaluation run. Rebuilt on each scoring run; prior rows are archived.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `comparison_id` | UUID | PK | Surrogate primary key |
| `rfq_id` | UUID | FK `rfq_rounds` NOT NULL | Parent RFQ round |
| `bid_id` | UUID | FK `rfq_bids` NOT NULL | Bid being evaluated |
| `rank` | INT | NOT NULL | Rank position (1 = best) in this evaluation run |
| `price_normalized` | NUMERIC(8,4) | NULLABLE | Price score normalised to 0–100 |
| `lead_time_normalized` | NUMERIC(8,4) | NULLABLE | Lead time score normalised to 0–100 |
| `quality_normalized` | NUMERIC(8,4) | NULLABLE | Quality score (from SIE scorecard) 0–100 |
| `risk_normalized` | NUMERIC(8,4) | NULLABLE | Risk score (from SIE risk model) 0–100 |
| `composite_score` | NUMERIC(8,4) | NULLABLE | Weighted composite score (see `SCORE_WEIGHTS`) |
| `vs_target_pct` | NUMERIC(8,4) | NULLABLE | `(bid_price_eur − target_price_eur) / target_price_eur × 100` |
| `vs_best_bid_pct` | NUMERIC(8,4) | NULLABLE | Delta vs. rank-1 bid (0 for rank-1 itself) |
| `recommendation` | ENUM | NOT NULL | `AWARD`, `SHORTLIST`, `REJECT` |

---

### RFQScoringEngine

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import uuid


SCORE_WEIGHTS: dict[str, Decimal] = {
    "price":           Decimal("0.50"),
    "lead_time":       Decimal("0.20"),
    "quality_history": Decimal("0.20"),
    "risk":            Decimal("0.10"),
}

assert sum(SCORE_WEIGHTS.values()) == Decimal("1.00"), (
    "SCORE_WEIGHTS must sum to 1.00"
)


@dataclass
class BidScoreInput:
    bid_id: uuid.UUID
    supplier_id: uuid.UUID
    bid_price_eur: Decimal
    lead_time_days: int
    quality_score: Decimal      # 0–100, from SIE supplier scorecard
    risk_score: Decimal         # 0–100, from SIE risk model (higher = lower risk = better)


@dataclass
class BidScoreResult:
    bid_id: uuid.UUID
    supplier_id: uuid.UUID
    price_score: Decimal        # normalized 0–100
    lead_time_score: Decimal    # normalized 0–100
    quality_score: Decimal      # pass-through from SIE
    risk_score: Decimal         # pass-through from SIE
    composite_score: Decimal    # weighted sum
    rank: int = 0


@dataclass
class AwardRecommendation:
    rfq_id: uuid.UUID
    recommended_supplier_id: uuid.UUID
    recommended_bid_id: uuid.UUID
    composite_score: Decimal
    vs_target_pct: Decimal                  # (bid_price - target) / target × 100
    vs_best_competitor_pct: Decimal         # delta vs. 2nd-place bid (negative = winner is cheaper)
    rationale: str
    ranked_bids: list[BidScoreResult]


class RFQScoringEngine:
    """
    Scores and ranks RFQ bids using a weighted multi-criteria model.

    Score Weights
    -------------
    Price:           50% — primary cost driver
    Lead time:       20% — supply chain responsiveness
    Quality history: 20% — SIE supplier scorecard (DPPM, audit results)
    Risk:            10% — SIE risk assessment (financial, geo-political, single-source)

    All scores are normalised to 0–100 before weighting. For price and lead
    time, the best (lowest) value receives 100 and the worst receives 0.
    """

    def _normalize_price(
        self, price: Decimal, all_prices: list[Decimal]
    ) -> Decimal:
        """Lower price = higher score. Best price in the set receives 100."""
        if not all_prices:
            return Decimal("0")
        best = min(all_prices)
        worst = max(all_prices)
        if worst == best:
            return Decimal("100")
        return (
            (worst - price) / (worst - best) * Decimal("100")
        ).quantize(Decimal("0.01"))

    def _normalize_lead_time(
        self, days: int, all_days: list[int]
    ) -> Decimal:
        """Shorter lead time = higher score. Best lead time receives 100."""
        if not all_days:
            return Decimal("0")
        best = min(all_days)
        worst = max(all_days)
        if worst == best:
            return Decimal("100")
        return (
            Decimal(str(worst - days)) / Decimal(str(worst - best)) * Decimal("100")
        ).quantize(Decimal("0.01"))

    def score_bid(
        self,
        bid: BidScoreInput,
        all_bids: list[BidScoreInput],
    ) -> BidScoreResult:
        """
        Score a single bid in the context of all competing bids.

        Parameters
        ----------
        bid:
            The bid to score.
        all_bids:
            The complete list of qualified bids in this RFQ round (including `bid`).
            Normalisation is relative to this population; disqualified bids must be
            excluded before calling this method.
        """
        all_prices = [b.bid_price_eur for b in all_bids]
        all_days = [b.lead_time_days for b in all_bids]

        price_score = self._normalize_price(bid.bid_price_eur, all_prices)
        lead_time_score = self._normalize_lead_time(bid.lead_time_days, all_days)

        composite = (
            price_score     * SCORE_WEIGHTS["price"]
            + lead_time_score * SCORE_WEIGHTS["lead_time"]
            + bid.quality_score * SCORE_WEIGHTS["quality_history"]
            + bid.risk_score    * SCORE_WEIGHTS["risk"]
        ).quantize(Decimal("0.01"))

        return BidScoreResult(
            bid_id=bid.bid_id,
            supplier_id=bid.supplier_id,
            price_score=price_score,
            lead_time_score=lead_time_score,
            quality_score=bid.quality_score,
            risk_score=bid.risk_score,
            composite_score=composite,
        )

    def rank_bids(self, bids: list[BidScoreInput]) -> list[BidScoreResult]:
        """
        Score and rank all bids. Returns the list sorted by composite_score
        descending, with `rank` set to 1-based position.

        Raises
        ------
        ValueError
            If fewer than one bid is provided.
        """
        if not bids:
            raise ValueError("Cannot rank an empty bid list.")
        scored = [self.score_bid(b, bids) for b in bids]
        scored.sort(key=lambda x: x.composite_score, reverse=True)
        for i, result in enumerate(scored):
            result.rank = i + 1
        return scored

    def calculate_savings_vs_budget(
        self,
        awarded_price_eur: Decimal,
        budget_target_eur: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Returns (savings_eur, savings_pct) relative to the internal budget target.

        Negative savings indicate that the awarded price exceeded budget.
        """
        if budget_target_eur == Decimal("0"):
            raise ValueError("budget_target_eur cannot be zero.")
        savings_eur = budget_target_eur - awarded_price_eur
        savings_pct = (
            savings_eur / budget_target_eur * Decimal("100")
        ).quantize(Decimal("0.01"))
        return savings_eur, savings_pct

    def calculate_savings_vs_prior_contract(
        self,
        awarded_price_eur: Decimal,
        prior_contract_price_eur: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Returns (savings_eur, savings_pct) relative to the previous contract price.

        Negative savings indicate a price increase vs. the prior contract.
        """
        if prior_contract_price_eur == Decimal("0"):
            raise ValueError("prior_contract_price_eur cannot be zero.")
        savings_eur = prior_contract_price_eur - awarded_price_eur
        savings_pct = (
            savings_eur / prior_contract_price_eur * Decimal("100")
        ).quantize(Decimal("0.01"))
        return savings_eur, savings_pct

    def generate_award_recommendation(
        self,
        rfq_id: uuid.UUID,
        ranked_bids: list[BidScoreResult],
        bid_inputs: list[BidScoreInput],
        target_price_eur: Decimal,
    ) -> AwardRecommendation:
        """
        Build an AwardRecommendation from a pre-ranked bid list.

        The recommendation is written to `bid_comparisons` and surfaces in
        the CHE procurement dashboard. It does not automatically set
        `rfq_rounds.awarded_supplier_id`; that transition requires a human
        approver action.

        Parameters
        ----------
        rfq_id:
            The RFQ round being evaluated.
        ranked_bids:
            Output of `rank_bids()` — must be sorted by composite_score desc.
        bid_inputs:
            Original input objects needed to retrieve bid prices for delta calculations.
        target_price_eur:
            The internal budget target (from `rfq_rounds.target_price_eur`).

        Raises
        ------
        ValueError
            If `ranked_bids` is empty.
        """
        if not ranked_bids:
            raise ValueError("Cannot generate recommendation from an empty bid list.")

        winner = ranked_bids[0]
        winner_input = next(b for b in bid_inputs if b.bid_id == winner.bid_id)

        runner_up = ranked_bids[1] if len(ranked_bids) > 1 else None
        runner_up_input = (
            next(b for b in bid_inputs if b.bid_id == runner_up.bid_id)
            if runner_up else None
        )

        vs_target_pct = (
            (winner_input.bid_price_eur - target_price_eur) / target_price_eur * Decimal("100")
        ).quantize(Decimal("0.01"))

        vs_best_competitor_pct = Decimal("0")
        if runner_up_input:
            vs_best_competitor_pct = (
                (winner_input.bid_price_eur - runner_up_input.bid_price_eur)
                / runner_up_input.bid_price_eur * Decimal("100")
            ).quantize(Decimal("0.01"))

        rationale = (
            f"Supplier {winner.supplier_id} ranked #1 with composite score "
            f"{winner.composite_score}/100 "
            f"(Price: {winner.price_score}, Lead Time: {winner.lead_time_score}, "
            f"Quality: {winner.quality_score}, Risk: {winner.risk_score}). "
            f"Bid price is {vs_target_pct:+}% vs. internal target."
        )

        return AwardRecommendation(
            rfq_id=rfq_id,
            recommended_supplier_id=winner.supplier_id,
            recommended_bid_id=winner.bid_id,
            composite_score=winner.composite_score,
            vs_target_pct=vs_target_pct,
            vs_best_competitor_pct=vs_best_competitor_pct,
            rationale=rationale,
            ranked_bids=ranked_bids,
        )
```

---

## Section 6 — Supplier Price History

Supplier price history tracks every price received from a supplier — whether through an RFQ bid, a contracted rate, a spot purchase, or a catalogue price. Prices are linked to commodity indices via `IndexLink`, enabling automatic escalation clause calculations and market benchmarking. The `SupplierPriceTrendAnalyzer` provides YoY analysis, CAGR, anomaly detection, and competitive positioning.

Cross-references:
- Supplier master data → Supplier Intelligence Engine (SIE) `supplier_id`
- Material master data → Material Intelligence Engine (MIE) `material_id`
- RFQ bids → `rfq_bids.bid_id` (Section 5)

---

### SupplierPriceRecord Entity

**Table:** `supplier_price_records`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `record_id` | UUID | PK | Surrogate primary key |
| `supplier_id` | UUID | NOT NULL | FK to SIE supplier record |
| `material_id` | UUID | NULLABLE | FK to MIE material record |
| `category_id` | UUID | NULLABLE | FK to MIE material category (for category-level contracts) |
| `price_per_unit` | NUMERIC(18,6) | NOT NULL | Price in `currency` per `price_unit` |
| `currency` | CHAR(3) | NOT NULL | ISO 4217 currency of the quoted price |
| `price_unit` | VARCHAR(20) | NOT NULL | Unit of measure: `KG`, `PIECE`, `METER`, `LITER`, `HOUR` |
| `price_eur_normalized` | NUMERIC(18,6) | NULLABLE | Price converted to EUR at FX rate current at `valid_from` |
| `moq_qty` | NUMERIC(18,6) | NOT NULL DEFAULT 1 | Minimum order quantity for this price to apply |
| `valid_from` | DATE | NOT NULL | Start of price validity |
| `valid_until` | DATE | NULLABLE | End of price validity; NULL = open-ended |
| `source` | ENUM | NOT NULL | `RFQ`, `CONTRACT`, `SPOT`, `CATALOGUE`, `ESTIMATE` |
| `rfq_id` | UUID | FK `rfq_rounds` NULLABLE | Source RFQ round (if `source = 'RFQ'`) |
| `po_number` | VARCHAR(100) | NULLABLE | Purchase order reference (if `source = 'CONTRACT'` or `'SPOT'`) |
| `index_reference` | VARCHAR(50) | NULLABLE | Commodity index code linked to this price: `LME_COPPER`, `PLATTS_HRC` |
| `index_value_at_recording` | NUMERIC(18,6) | NULLABLE | Index value on `valid_from` date; used as escalation base |
| `tooling_amortization_eur` | NUMERIC(18,6) | DEFAULT 0 | Per-unit tooling amortisation included in the price |
| `is_active` | BOOLEAN | DEFAULT TRUE | FALSE when superseded or expired |
| `superseded_by` | UUID | FK SELF NULLABLE | FK to the replacement price record |
| `version` | INT | NOT NULL DEFAULT 1 | Version counter within a supplier/material combination |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |
| `created_by` | UUID | NOT NULL | FK to user or system that created the record |

**Indexes:**
```sql
CREATE INDEX idx_spr_supplier_material ON supplier_price_records (supplier_id, material_id, valid_from DESC)
    WHERE is_active = TRUE;
CREATE INDEX idx_spr_index_reference ON supplier_price_records (index_reference)
    WHERE index_reference IS NOT NULL;
```

---

### PriceAdjustment Entity

**Table:** `price_adjustments`

Tracks contractual adjustments applied on top of a base `SupplierPriceRecord`, including index escalations, volume rebates, and currency surcharges.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `adjustment_id` | UUID | PK | Surrogate primary key |
| `record_id` | UUID | FK `supplier_price_records` NOT NULL | Base price record being adjusted |
| `adjustment_type` | ENUM | NOT NULL | `INDEX_ESCALATION`, `VOLUME_REBATE`, `TOOLING_AMORTIZATION`, `CURRENCY_FX`, `SURCHARGE` |
| `adjustment_pct` | NUMERIC(8,4) | NULLABLE | Adjustment expressed as a percentage (positive = increase) |
| `amount_eur` | NUMERIC(18,6) | NULLABLE | Adjustment expressed as a fixed EUR amount per unit |
| `effective_date` | DATE | NOT NULL | Date from which the adjustment applies |
| `expiry_date` | DATE | NULLABLE | Date after which the adjustment ceases |
| `reason` | TEXT | NULLABLE | Business justification for the adjustment |
| `approved_by` | UUID | NULLABLE | FK to approving user |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

---

### IndexLink Entity

**Table:** `index_links`

Links a `SupplierPriceRecord` to one or more commodity indices. The `weight` column defines the fraction of the unit price that is index-linked; weights across all links for a given `record_id` should sum to ≤ 1.0.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `link_id` | UUID | PK | Surrogate primary key |
| `record_id` | UUID | FK `supplier_price_records` NOT NULL | Parent price record |
| `index_name` | VARCHAR(50) | NOT NULL | Commodity index identifier: `LME_COPPER`, `PLATTS_HRC`, `PPI_DE_METALS` |
| `index_value` | NUMERIC(18,6) | NOT NULL | Index value at the time the price was agreed |
| `weight` | NUMERIC(5,4) | NOT NULL CHECK (`0 < weight <= 1`) | Fraction of the unit price linked to this index |
| `base_date` | DATE | NOT NULL | Reference date for the base index value |
| `base_value` | NUMERIC(18,6) | NOT NULL | Index value on `base_date`; used to compute escalation factor |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Escalation formula:**

```
escalated_price = base_price × (1 − weight + weight × (current_index / base_value))
```

---

### Commodity Index Connector Table

| Index Name | Provider | Update Frequency | Unit | Connector Class |
|---|---|---|---|---|
| LME Copper | London Metal Exchange | Daily | USD/tonne | `LMEConnector` |
| LME Aluminum | London Metal Exchange | Daily | USD/tonne | `LMEConnector` |
| LME Zinc | London Metal Exchange | Daily | USD/tonne | `LMEConnector` |
| LME Steel HR | London Metal Exchange | Daily | USD/tonne | `LMEConnector` |
| Platts HRC Europe | S&P Global Platts | Weekly | EUR/tonne | `PlattsConnector` |
| ICIS PA6 (Polyamide 6) | ICIS | Weekly | EUR/tonne | `ICISConnector` |
| ICIS PP (Polypropylene) | ICIS | Weekly | EUR/tonne | `ICISConnector` |
| PPI Germany Metals | EUROSTAT / Destatis | Monthly | Index (2015=100) | `PPIConnector` |

All connectors implement the `CommodityIndexConnector` abstract base class, which exposes `fetch_latest(index_name: str) -> IndexSnapshot` and `fetch_history(index_name: str, from_date: date, to_date: date) -> list[IndexSnapshot]`. Index data is cached in Redis 7+ with a TTL of 1 hour (daily indices) or 6 hours (weekly/monthly indices) and published to Kafka topic `che.commodity.index.updates` on each refresh.

---

### SupplierPriceTrendAnalyzer

```python
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
from enum import Enum
from typing import Optional
import uuid
import statistics


class MarketPosition(str, Enum):
    LEADER       = "LEADER"        # ≤ −5% vs. benchmark
    COMPETITIVE  = "COMPETITIVE"   # −5% to +5% vs. benchmark
    ABOVE_MARKET = "ABOVE_MARKET"  # +5% to +15% vs. benchmark
    PREMIUM      = "PREMIUM"       # > +15% vs. benchmark


@dataclass
class PriceTrendResult:
    supplier_id: uuid.UUID
    material_id: uuid.UUID
    yoy_change_pct: Optional[Decimal]
    cagr_pct: Optional[Decimal]
    is_anomaly: bool
    anomaly_zscore: Optional[Decimal]
    forecast_next_period_eur: Optional[Decimal]
    vs_benchmark_pct: Optional[Decimal]
    vs_best_bid_pct: Optional[Decimal]
    vs_prior_contract_pct: Optional[Decimal]
    market_position: Optional[MarketPosition]


@dataclass
class CompetitivenessMatrix:
    supplier_id: uuid.UUID
    material_id: uuid.UUID
    current_price_eur: Decimal
    benchmark_price_eur: Decimal
    best_bid_price_eur: Optional[Decimal]
    prior_contract_price_eur: Optional[Decimal]
    vs_benchmark_pct: Decimal
    vs_best_bid_pct: Optional[Decimal]
    vs_prior_contract_pct: Optional[Decimal]
    market_position: MarketPosition


class SupplierPriceTrendAnalyzer:
    """
    Analyses supplier price history for trends, anomalies, and competitive
    positioning against market benchmarks.

    All price inputs must be EUR-normalised before passing to this class.
    Currency conversion is the responsibility of the calling service.
    """

    ANOMALY_SIGMA_THRESHOLD = Decimal("3.0")

    def calculate_yoy_change(
        self,
        price_current: Decimal,
        price_prior_year: Decimal,
    ) -> Decimal:
        """
        Year-over-year price change as a percentage.

        Positive result = price increase. Negative = price decrease.

        Raises
        ------
        ValueError
            If `price_prior_year` is zero.
        """
        if price_prior_year == Decimal("0"):
            raise ValueError("prior year price cannot be zero.")
        return (
            (price_current - price_prior_year) / price_prior_year * Decimal("100")
        ).quantize(Decimal("0.01"))

    def calculate_cagr(
        self,
        price_start: Decimal,
        price_end: Decimal,
        years: Decimal,
    ) -> Decimal:
        """
        Compound Annual Growth Rate (CAGR) over a multi-year period.

        Returns CAGR as a percentage. A result of 4.50 means 4.50% per year.

        Raises
        ------
        ValueError
            If `price_start` is not positive, or `years` is not positive.
        """
        if price_start <= Decimal("0"):
            raise ValueError("price_start must be positive.")
        if years <= Decimal("0"):
            raise ValueError("years must be positive.")
        ratio = float(price_end / price_start)
        cagr = (ratio ** (1.0 / float(years)) - 1.0) * 100.0
        return Decimal(str(round(cagr, 4)))

    def detect_anomaly(
        self,
        price_series: list[Decimal],
        current_price: Decimal,
    ) -> tuple[bool, Optional[Decimal]]:
        """
        Flag `current_price` as a statistical anomaly using z-score analysis.

        A price is flagged if its z-score exceeds ANOMALY_SIGMA_THRESHOLD (3σ).
        Requires at least 5 historical observations for a meaningful result;
        returns (False, None) for shorter series.

        Returns
        -------
        tuple[bool, Optional[Decimal]]
            (is_anomaly, z_score) — z_score is None if the series is too short.
        """
        if len(price_series) < 5:
            return False, None
        floats = [float(p) for p in price_series]
        mean = statistics.mean(floats)
        std = statistics.stdev(floats)
        if std == 0.0:
            return False, Decimal("0")
        z_score = Decimal(str(abs((float(current_price) - mean) / std)))
        is_anomaly = z_score > self.ANOMALY_SIGMA_THRESHOLD
        return is_anomaly, z_score.quantize(Decimal("0.0001"))

    def forecast_next_period(
        self,
        price_series: list[tuple[date, Decimal]],
        periods_ahead: int = 1,
    ) -> Optional[Decimal]:
        """
        Simple linear-regression trend forecast.

        This is a lightweight fallback for environments where the full
        `PriceForecastEngine` (Prophet/ARIMA ensemble) is unavailable.
        In production, the CHE forecasting service delegates to
        `PriceForecastEngine.forecast(model=ENSEMBLE)` for higher accuracy.

        Parameters
        ----------
        price_series:
            List of (date, price_eur) tuples. Must contain at least 3 points.
        periods_ahead:
            Number of periods (same cadence as input data) to project forward.

        Returns
        -------
        Optional[Decimal]
            Forecasted price, or None if the series has fewer than 3 observations.
        """
        if len(price_series) < 3:
            return None
        prices = [float(p) for _, p in sorted(price_series, key=lambda x: x[0])]
        n = len(prices)
        x_mean = (n - 1) / 2.0
        y_mean = statistics.mean(prices)
        numerator = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0.0:
            return Decimal(str(round(y_mean, 6)))
        slope = numerator / denominator
        forecast = y_mean + slope * (n - 1 + periods_ahead - x_mean)
        return Decimal(str(round(forecast, 6)))

    def compare_to_market_index(
        self,
        supplier_price_eur: Decimal,
        benchmark_price_eur: Decimal,
    ) -> tuple[Decimal, MarketPosition]:
        """
        Compare a supplier's price to a market benchmark index price.

        Returns
        -------
        tuple[Decimal, MarketPosition]
            (vs_benchmark_pct, market_position)

            vs_benchmark_pct: positive = supplier is above benchmark (worse),
                              negative = supplier is below benchmark (better).

        Raises
        ------
        ValueError
            If `benchmark_price_eur` is zero.
        """
        if benchmark_price_eur == Decimal("0"):
            raise ValueError("benchmark_price_eur cannot be zero.")
        vs_benchmark_pct = (
            (supplier_price_eur - benchmark_price_eur) / benchmark_price_eur * Decimal("100")
        ).quantize(Decimal("0.01"))

        if vs_benchmark_pct <= Decimal("-5"):
            position = MarketPosition.LEADER
        elif vs_benchmark_pct <= Decimal("5"):
            position = MarketPosition.COMPETITIVE
        elif vs_benchmark_pct <= Decimal("15"):
            position = MarketPosition.ABOVE_MARKET
        else:
            position = MarketPosition.PREMIUM

        return vs_benchmark_pct, position

    def build_competitiveness_matrix(
        self,
        supplier_id: uuid.UUID,
        material_id: uuid.UUID,
        current_price_eur: Decimal,
        benchmark_price_eur: Decimal,
        best_bid_price_eur: Optional[Decimal] = None,
        prior_contract_price_eur: Optional[Decimal] = None,
    ) -> CompetitivenessMatrix:
        """
        Assemble a full CompetitivenessMatrix for a supplier/material pair.

        Parameters
        ----------
        supplier_id:
            SIE supplier UUID.
        material_id:
            MIE material UUID.
        current_price_eur:
            The supplier's current active price in EUR.
        benchmark_price_eur:
            Market benchmark price in EUR (from commodity index or market survey).
        best_bid_price_eur:
            Lowest price received in the most recent RFQ round for this material.
        prior_contract_price_eur:
            Price from the previous contract period for delta comparison.
        """
        vs_benchmark_pct, market_position = self.compare_to_market_index(
            current_price_eur, benchmark_price_eur
        )

        vs_best_bid_pct: Optional[Decimal] = None
        if best_bid_price_eur is not None and best_bid_price_eur != Decimal("0"):
            vs_best_bid_pct = (
                (current_price_eur - best_bid_price_eur) / best_bid_price_eur * Decimal("100")
            ).quantize(Decimal("0.01"))

        vs_prior_contract_pct: Optional[Decimal] = None
        if prior_contract_price_eur is not None and prior_contract_price_eur != Decimal("0"):
            vs_prior_contract_pct = (
                (current_price_eur - prior_contract_price_eur) / prior_contract_price_eur * Decimal("100")
            ).quantize(Decimal("0.01"))

        return CompetitivenessMatrix(
            supplier_id=supplier_id,
            material_id=material_id,
            current_price_eur=current_price_eur,
            benchmark_price_eur=benchmark_price_eur,
            best_bid_price_eur=best_bid_price_eur,
            prior_contract_price_eur=prior_contract_price_eur,
            vs_benchmark_pct=vs_benchmark_pct,
            vs_best_bid_pct=vs_best_bid_pct,
            vs_prior_contract_pct=vs_prior_contract_pct,
            market_position=market_position,
        )
```

---

### Price Competitiveness Matrix Table

Example competitiveness matrix output for three suppliers competing on the same material (`Stainless Steel 316L Sheet, 2mm`). Benchmark is the current LME index-derived price for SS316L in Europe.

| Supplier | Material | Current Price (EUR/kg) | vs Benchmark | vs Best Bid | vs Prior Contract | Market Position |
|---|---|---|---|---|---|---|
| Outokumpu Stainless AB | SS 316L Sheet 2mm | 4.82 | −3.20% | 0.00% | −1.85% | `COMPETITIVE` |
| Acerinox Europa S.A.U. | SS 316L Sheet 2mm | 5.10 | +2.40% | +5.81% | +2.00% | `COMPETITIVE` |
| Aperam Stainless Europe | SS 316L Sheet 2mm | 5.64 | +13.25% | +16.94% | +8.85% | `ABOVE_MARKET` |

**Notes on the example:**
- Benchmark price: EUR 4.98/kg (LME SS316L Europe index, week of 2024-06-10)
- Best bid: EUR 4.82/kg (Outokumpu, RFQ-2024-00089)
- Prior contract: EUR 4.91/kg (contracted 2023-Q3 with Outokumpu)
- Outokumpu's `vs_prior_contract_pct` of −1.85% confirms a favourable renegotiation
- Aperam's `ABOVE_MARKET` position would trigger a `PRICE_COMPETITIVENESS_ALERT` event on Kafka topic `che.supplier.alerts` and flag a mandatory re-negotiation review in the SIE supplier dashboard
