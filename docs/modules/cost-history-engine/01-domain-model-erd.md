# Cost History Engine — Domain Model & ERD

**Module:** Cost History Engine (CHE)
**Platform:** Industrial Cost Intelligence
**Document Version:** 1.0.0
**Status:** Authoritative

---

## Table of Contents

1. [Data Model](#1-data-model)
   1. [Bounded Context](#11-bounded-context)
   2. [Context Map](#12-context-map)
   3. [Core Aggregates](#13-core-aggregates)
   4. [Domain Events](#14-domain-events)
   5. [Entity Relationship Summary](#15-entity-relationship-summary)
2. [ERD](#2-erd)
   1. [Full ASCII ERD](#21-full-ascii-erd)
   2. [Color Legend](#22-color-legend)
   3. [Key Design Decisions](#23-key-design-decisions)

---

# 1. Data Model

## 1.1 Bounded Context

### What CHE Owns

The Cost History Engine is the **system of record for all historical cost data** across the Industrial Cost Intelligence platform. It is a read-heavy, analytical, append-only store. CHE is authoritative for:

- **Cost snapshots** — point-in-time representations of a calculated cost for a product, assembly, or component, including all versioned revisions and approval states.
- **Quotes** — formal commercial quotes received from or issued to external parties, with full version lineage and approval workflow states.
- **RFQ rounds** — complete request-for-quotation lifecycle records, including all bids received, bid comparison matrices, and award decisions.
- **Supplier price history** — every recorded price for a supplier–material or supplier–service pair, with index linkages and adjustment audit trails.
- **Material price history** — time-series of market prices and forecasts for materials, indexed from market data providers and the Material Intelligence Engine.
- **Process cost history** — historical unit costs for manufacturing processes, including OEE snapshots captured at recording time.
- **Version lineage** — a structured log of every version created for any domain entity tracked by CHE, including field-level diffs and human-readable labels.
- **Audit events** — an immutable, tamper-evident log of every mutation, approval, or system action that affected any CHE-managed record.
- **Retention policies** — rules governing archival and purge scheduling for historical records, and execution logs thereof.
- **Benchmarks** — periodic competitive benchmark snapshots used for trend analysis and AI forecasting baseline calibration.

### What CHE Does NOT Own

CHE is strictly a **historical and analytical store**. Live master data is owned by other bounded contexts and flows into CHE as snapshots or events:

| Data | Owner | CHE Relationship |
|---|---|---|
| Material definitions, specifications, substitution rules | Material Intelligence Engine (MIE) | CHE stores price history snapshots; references `material_id` as an opaque FK |
| Supplier master records, scorecards, NCR records | Supplier Intelligence Engine (SIE) | CHE stores price history and quote records; references `supplier_id` as an opaque FK |
| Manufacturing process definitions, OEE live data | Manufacturing Process Engine (MPE) | CHE stores process cost snapshots; references `process_id` as an opaque FK |
| Live cost calculations (BOM explosion, routing cost) | Cost Calculation Engine (CCE) | CHE receives completed cost snapshots as domain events; does not replicate CCE logic |
| Active RFQ orchestration | RFQ Engine | CHE archives completed and in-progress RFQ rounds; does not drive RFQ workflow |
| User and role management | Identity & Access Management (IAM) | CHE stores `actor_id` references in audit events; does not own user records |
| ERP purchase orders, actual cost postings | ERP Integration Layer | CHE ingests actual cost confirmations as historical records; ERP remains authoritative for actuals |

### Core Principle

> CHE stores **what things cost, when, and why**. It never stores **what things are** or **who suppliers or materials are** as live entities. All cross-module references are immutable snapshot FKs captured at record creation time.

---

## 1.2 Context Map

The following context map uses DDD notation. Upstream systems are **producers** that feed CHE; downstream systems are **consumers** that read from CHE. Relationships are labeled with integration pattern.

```
                    ┌─────────────────────────────────────────────────────────────────┐
                    │                   UPSTREAM PRODUCERS                            │
                    │                                                                 │
  ┌─────────────┐  │  ┌─────────────┐  ┌────────────┐  ┌────────────┐  ┌─────────┐  │
  │     ERP     │  │  │     CCE     │  │ RFQ Engine │  │    SIE     │  │   MIE   │  │
  │(SAP/Oracle/ │  │  │  (Cost Calc │  │ (RFQ Orch- │  │ (Supplier  │  │(Material│  │
  │  Dynamics)  │  │  │   Engine)   │  │  estrator) │  │   Intel.)  │  │ Intel.) │  │
  └──────┬──────┘  │  └──────┬──────┘  └──────┬─────┘  └─────┬──────┘  └────┬────┘  │
         │         │         │                 │              │              │        │
         │ACL      │         │Published Lang.  │Published     │Conformist    │Conform-│
         │(Anti-   │         │(domain events   │Lang.         │(price events)│ist     │
         │Corrupt. │         │via Kafka)       │(RFQ events   │              │(price &│
         │Layer)   │         │                 │via Kafka)    │              │market  │
         │         │         │                 │              │              │events) │
         └─────────┼─────────┴─────────────────┴──────────────┴──────────────┴────────┘
                   │                              │
                   ▼                              ▼
         ┌─────────────────────────────────────────────────────┐
         │            ┌─────────────────────────┐              │
         │            │                         │              │
         │            │   COST HISTORY ENGINE   │              │
         │            │          (CHE)          │              │
         │            │                         │              │
         │            │  • CostSnapshotAggregate│              │
         │            │  • QuoteAggregate       │              │
         │            │  • RFQAggregate         │              │
         │            │  • SupplierPriceHistory │              │
         │            │  • MaterialPriceHistory │              │
         │            │  • ProcessCostHistory   │              │
         │            │  • VersionAggregate     │              │
         │            │  • AuditAggregate       │              │
         │            │  • RetentionAggregate   │              │
         │            │  • BenchmarkAggregate   │              │
         │            │                         │              │
         │            └─────────────────────────┘              │
         │                          │                          │
         │            ┌─────────────┼─────────────┐            │
         │            │             │             │            │
         ▼            ▼             ▼             ▼            │
  ┌─────────────┐ ┌────────┐ ┌──────────┐ ┌───────────────┐   │
  │  MPE        │ │  BI /  │ │   AI     │ │   Audit /     │   │
  │(Manufactur- │ │Analytics│ │Forecast- │ │  Compliance   │   │
  │ ing Process │ │(Power   │ │ing Svc   │ │  Reporting    │   │
  │  Engine)    │ │BI, etc.)│ │(OpenAI   │ │  (SOX, GDPR)  │   │
  └─────────────┘ └────────┘ │ embed.)  │ └───────────────┘   │
         │                   └──────────┘         │            │
         │ Published Lang.                         │            │
         │ (process cost                           │            │
         │  events via Kafka)                      │            │
         │                              ┌──────────┘            │
         │                              ▼                       │
         │                    ┌─────────────────┐               │
         │                    │   Dashboard /   │               │
         └───────────────────►│   Cost Review   │               │
                              │      UI         │               │
                              └─────────────────┘               │
         └─────────────────────────────────────────────────────┘
                    DOWNSTREAM CONSUMERS

Legend:
  ACL         = Anti-Corruption Layer (CHE translates ERP schema to CHE domain model)
  Published Lang. = Published Language (producer publishes well-defined Kafka event schema)
  Conformist  = CHE conforms to producer's model without translation
```

### Integration Patterns Summary

| Upstream | Pattern | Transport | Notes |
|---|---|---|---|
| CCE | Published Language | Kafka topic `che.cost-snapshots` | CCE publishes `CostCalculationCompleted`; CHE maps to `CostSnapshot` |
| RFQ Engine | Published Language | Kafka topic `che.rfq-events` | Full RFQ lifecycle events consumed |
| SIE | Conformist | Kafka topic `che.supplier-prices` | CHE adopts SIE event schema verbatim |
| MIE | Conformist | Kafka topic `che.material-prices` | CHE adopts MIE market price schema verbatim |
| MPE | Published Language | Kafka topic `che.process-costs` | CHE maps MPE process cost events |
| ERP | Anti-Corruption Layer | REST polling + Kafka | CHE owns the ACL; ERP schema shielded from CHE domain |
| BI/Analytics | Open Host Service | REST API + read replica | CHE exposes versioned read API |
| AI Forecasting | Open Host Service | REST API + Kafka `che.forecasting-feed` | CHE publishes denormalized price series |
| Audit/Compliance | Published Language | Kafka topic `che.audit-events` | Immutable audit events published |
| Dashboard | Open Host Service | REST API (GraphQL gateway) | Paginated historical queries |

---

## 1.3 Core Aggregates

### Aggregate 1 — CostSnapshotAggregate

**Root Entity:** `CostSnapshot`
**Internal Entities:** `SnapshotLineItem`, `SnapshotMeta`

**Purpose:**
Represents a complete, versioned, point-in-time cost record for a calculable entity (product, component, assembly, or project). A `CostSnapshot` is created when the Cost Calculation Engine completes a cost run and publishes the result. It is immutable once created; any change produces a new version with a `superseded_by` reference back-pointing from the old record to the new.

**Invariants:**
- A `CostSnapshot` must reference a valid `reference_entity_id` and `reference_entity_type` (e.g., `PRODUCT`, `ASSEMBLY`, `COMPONENT`).
- `total_cost` must equal the sum of all `SnapshotLineItem.line_cost` values within the same snapshot.
- `valid_from` must be chronologically after all prior snapshots for the same `reference_entity_id` and `cost_type`.
- Once `status = APPROVED`, no fields may change; a new version must be created.
- `superseded_by` is null for the current (active) version; it points to the new snapshot UUID when this snapshot is superseded.
- `currency_code` must be an ISO 4217 code.

**Key Business Rules:**
- **Rule CS-01:** Only one `CostSnapshot` per `(reference_entity_id, cost_type, version)` tuple may exist with `status != SUPERSEDED`.
- **Rule CS-02:** Approval requires an `actor_id` referencing an IAM user with the `COST_APPROVER` role; the approval is recorded as an `AuditEvent`.
- **Rule CS-03:** A superseded snapshot remains permanently readable but must not appear in "current cost" queries unless explicitly requested by version.
- **Rule CS-04:** `SnapshotLineItem` records may not be added, modified, or removed after snapshot creation.
- **Rule CS-05:** `SnapshotMeta` carries JSONB attributes (e.g., BOM version, routing version, overhead rate set) that contextualize the calculation; they are recorded at creation time and never updated.

---

### Aggregate 2 — QuoteAggregate

**Root Entity:** `Quote`
**Internal Entities:** `QuoteLine`, `QuoteVersion`, `QuoteApproval`

**Purpose:**
Tracks the full lifecycle of a commercial quote — either a quote received from a supplier or a quote issued to a customer. Each revision of a quote creates a new `QuoteVersion` entity, preserving the full negotiation history. Approval workflows are recorded as `QuoteApproval` entities linked to the active version at approval time.

**Invariants:**
- A `Quote` must reference exactly one `supplier_id` (for inbound) or `customer_id` (for outbound), and one `rfq_round_id` if issued in response to an RFQ.
- `QuoteLine.unit_price` must be positive and `QuoteLine.quantity` must be greater than zero.
- `Quote.total_value` must equal the sum of `(QuoteLine.unit_price * QuoteLine.quantity)` across all active `QuoteLine` records of the current version.
- Once `status = ACCEPTED`, no new `QuoteVersion` may be created.
- `QuoteApproval` records are immutable once written.

**Key Business Rules:**
- **Rule QU-01:** Every revision must increment `version_number`; the prior version is marked `superseded`.
- **Rule QU-02:** A quote may transition: `DRAFT → SUBMITTED → UNDER_REVIEW → ACCEPTED | REJECTED | EXPIRED`.
- **Rule QU-03:** Rejection requires a mandatory `rejection_reason` field; this is enforced at the application layer and validated before persistence.
- **Rule QU-04:** Currency and Incoterms must be recorded per `QuoteLine`, not only at the quote header level, to support split-currency quotes.
- **Rule QU-05:** `QuoteApproval` must capture the approver's `actor_id`, `approved_at` timestamp, and `approval_level` (e.g., L1, L2, L3).

---

### Aggregate 3 — RFQAggregate

**Root Entity:** `RFQRound`
**Internal Entities:** `RFQLine`, `RFQBid`, `BidComparison`

**Purpose:**
Archives the complete lifecycle of a Request for Quotation round, from initial issuance through bid collection, comparison, and award. Multiple bids from different suppliers are recorded as `RFQBid` entities; the `BidComparison` entity stores the normalized comparison matrix used to reach an award decision.

**Invariants:**
- An `RFQRound` must have at least one `RFQLine` before it can transition to `ISSUED` status.
- Each `RFQBid` must reference an existing `supplier_id` and be linked to the correct `rfq_round_id`.
- A single `RFQRound` may have at most one `RFQBid` per `supplier_id` per `RFQLine` at any point in time (re-bids create a new `RFQBid` superseding the prior).
- `BidComparison` is created once all bids are received or the bid deadline passes; it is immutable.
- Only one `RFQBid` per round may be awarded (`status = AWARDED`).

**Key Business Rules:**
- **Rule RQ-01:** `RFQRound.status` transitions: `DRAFT → ISSUED → BIDS_RECEIVED → UNDER_EVALUATION → AWARDED | CANCELLED`.
- **Rule RQ-02:** `RFQLine.target_price` is confidential and must not be exposed in supplier-facing API responses; it is used only in `BidComparison`.
- **Rule RQ-03:** `BidComparison.comparison_matrix` is stored as JSONB and includes weighted scoring per `RFQLine` across all dimensions (price, lead time, quality rating, payment terms).
- **Rule RQ-04:** Cancellation after `ISSUED` requires a `cancellation_reason` and triggers a `CostSnapshotSuperseded`-equivalent audit event.
- **Rule RQ-05:** Awarded bids must result in a `SupplierPriceRecord` being created in the `SupplierPriceHistoryAggregate`.

---

### Aggregate 4 — SupplierPriceHistoryAggregate

**Root Entity:** `SupplierPriceRecord`
**Internal Entities:** `PriceAdjustment`, `IndexLink`

**Purpose:**
Maintains the complete time-series of agreed or observed prices for each supplier–item pair (where "item" may be a material, component, or service). Prices are append-only; each new price record either extends the time-series or supersedes a prior record for the same effective period. `PriceAdjustment` records document post-agreement modifications (e.g., surcharges, index escalations). `IndexLink` entities link a price record to an external commodity index used to derive or validate it.

**Invariants:**
- `SupplierPriceRecord` must reference a valid `supplier_id` and `item_id` with `item_type` discriminator.
- `valid_from` must be earlier than `valid_to` (when `valid_to` is set); open-ended validity uses `valid_to = NULL`.
- No two active (non-superseded) `SupplierPriceRecord` rows may share the same `(supplier_id, item_id, currency_code)` with overlapping validity periods.
- `PriceAdjustment.adjusted_unit_price` must reflect the base price plus all accumulated adjustment deltas at the time of recording.
- `IndexLink.index_value_at_recording` is immutable after creation.

**Key Business Rules:**
- **Rule SP-01:** A new `SupplierPriceRecord` that overlaps an existing active record for the same supplier–item pair must set `superseded_by` on the prior record before or simultaneously with its own insertion (enforced via transaction).
- **Rule SP-02:** Price adjustments driven by commodity index changes must reference the corresponding `IndexLink.index_id` and the index value at the time of adjustment.
- **Rule SP-03:** Prices sourced from an awarded RFQ must carry `source_type = RFQ_AWARD` and `source_reference_id` pointing to the `RFQBid.bid_id`.
- **Rule SP-04:** Manual price entries require a `justification` field and trigger an `AuditEvent` with `action = MANUAL_PRICE_ENTRY`.

---

### Aggregate 5 — MaterialPriceHistoryAggregate

**Root Entity:** `MaterialPriceRecord`
**Internal Entities:** `MarketDataPoint`, `ForecastRecord`

**Purpose:**
Stores the historical and forecast price time-series for materials as observed or projected from market data providers, MIE-sourced data, and AI-generated forecasts. `MarketDataPoint` entities record raw market observations (e.g., LME closing prices, spot prices). `ForecastRecord` entities store AI-generated or model-based forward price projections with confidence intervals.

**Invariants:**
- `MaterialPriceRecord` must reference a valid `material_id` from MIE.
- `MarketDataPoint.observation_date` must be unique per `(material_id, data_source, price_type)`.
- `ForecastRecord.forecast_horizon_days` must be a positive integer.
- `ForecastRecord.confidence_interval_lower` must be less than or equal to `confidence_interval_upper`.
- `ForecastRecord` records are immutable once created; revised forecasts create new records.

**Key Business Rules:**
- **Rule MP-01:** Market data ingested from external providers is tagged with `data_source` (e.g., `LME`, `PLATTS`, `BLOOMBERG`) and `data_quality` score.
- **Rule MP-02:** AI-generated `ForecastRecord` entries must reference the `model_version` and `embedding_model_id` used, enabling reproducibility audits.
- **Rule MP-03:** Forecasts are partitioned by `forecast_generated_at` to allow efficient retrieval of the latest forecast series without full table scan.
- **Rule MP-04:** When a `MaterialPriceForecastGenerated` event is emitted, the prior forecast for the same `(material_id, forecast_type, horizon)` is marked `superseded`.

---

### Aggregate 6 — ProcessCostHistoryAggregate

**Root Entity:** `ProcessCostRecord`
**Internal Entities:** `CostComponent`, `OEESnapshot`

**Purpose:**
Archives the historical cost of manufacturing processes as calculated by MPE and confirmed by CHE's ingestion pipeline. Each record represents the effective cost of a process for a given period, broken down into `CostComponent` entities (e.g., labor, machine time, energy, tooling, overhead). An `OEESnapshot` captures the Overall Equipment Effectiveness metrics that were current at the time the process cost was recorded, enabling cost-OEE correlation analysis.

**Invariants:**
- `ProcessCostRecord` must reference a valid `process_id` from MPE.
- `ProcessCostRecord.total_cost_per_unit` must equal the sum of all `CostComponent.component_cost` values.
- `OEESnapshot.availability * OEESnapshot.performance * OEESnapshot.quality` must equal `OEESnapshot.oee_percentage` (within floating point tolerance of 0.001).
- `period_year` and `period_month` must form a valid calendar month; `period_month` must be in [1, 12].
- No two non-superseded `ProcessCostRecord` rows may share the same `(process_id, period_year, period_month, cost_type)`.

**Key Business Rules:**
- **Rule PC-01:** `CostComponent.component_type` must use the platform-standard taxonomy: `DIRECT_LABOR`, `MACHINE_TIME`, `ENERGY`, `TOOLING_AMORTIZATION`, `OVERHEAD`, `SCRAP_ALLOWANCE`.
- **Rule PC-02:** Records sourced from MPE events must carry `source_type = MPE_EVENT` and include the originating Kafka `event_id` as `source_reference_id`.
- **Rule PC-03:** Records entered manually for back-loading ERP actuals carry `source_type = ERP_IMPORT` and must include the ERP transaction reference.
- **Rule PC-04:** Revision of an existing period's cost creates a new record with `superseded_by` set on the prior, and emits `ProcessCostRecorded` with `is_revision = true`.

---

### Aggregate 7 — VersionAggregate

**Root Entity:** `EntityVersion`
**Internal Entities:** `VersionDiff`, `VersionLabel`

**Purpose:**
Provides a cross-aggregate versioning ledger for all CHE-managed entities. Every time a new version of any tracked entity is created, a corresponding `EntityVersion` is written here with the entity type, entity ID, old and new version numbers, and a structured diff. `VersionDiff` stores the field-level change set in JSONB. `VersionLabel` allows human-readable names to be attached to specific version milestones (e.g., "Q3-2024 Baseline", "Post-Audit Revision").

**Invariants:**
- `EntityVersion.version_number` must be strictly monotonically increasing per `(entity_type, entity_id)`.
- `VersionDiff.diff_json` must be a valid JSON Patch (RFC 6902) document.
- `VersionLabel.label_text` must be unique per `(entity_type, entity_id)`.
- `EntityVersion` records are immutable once written.

**Key Business Rules:**
- **Rule VE-01:** Every aggregate root mutation in CHE must produce a corresponding `EntityVersion` entry within the same database transaction.
- **Rule VE-02:** Version diffs are computed by the CHE service layer, not by the database trigger, to ensure business-layer semantics (e.g., masking sensitive fields from diffs).
- **Rule VE-03:** `VersionLabel` creation requires the `COST_MANAGER` role; labels cannot be deleted, only deprecated via `is_active = false`.

---

### Aggregate 8 — AuditAggregate

**Root Entity:** `AuditEvent`
**Internal Entities:** `FieldChange`, `ActorContext`

**Purpose:**
Maintains the immutable, tamper-evident audit log for all actions performed within CHE. Every write operation — whether triggered by a Kafka consumer, a REST API call, or a scheduled job — must produce at least one `AuditEvent`. `FieldChange` entities record the before/after values of individual fields. `ActorContext` captures the identity and authorization context of the actor who triggered the event, including IP address, JWT claims summary, and mTLS certificate fingerprint.

**Invariants:**
- `AuditEvent` rows are never deleted or updated (enforced by PostgreSQL RLS: `USING (false)` on UPDATE and DELETE for the `audit_events` table).
- `AuditEvent.occurred_at` must be set by the database server clock (`NOW()`) at insertion time, never by the application clock.
- `ActorContext.actor_id` must be present for all non-system events; for system-generated events, `actor_type = SYSTEM` and `actor_id = NULL` is permitted.
- `FieldChange.field_name` must not contain PII unless the platform's data classification policy explicitly permits it for that entity type.

**Key Business Rules:**
- **Rule AU-01:** The `audit_events` table has PostgreSQL RLS policies granting `SELECT` to `audit_reader` role and `INSERT` to `audit_writer` role; no application role has `UPDATE` or `DELETE` permission.
- **Rule AU-02:** `AuditEvent.event_hash` is a SHA-256 of `(event_id || entity_id || occurred_at || action || actor_id)` computed at insertion time and stored for tamper-detection spot checks.
- **Rule AU-03:** Audit events are forwarded to the external SIEM via Kafka topic `che.audit-events` within 5 seconds of insertion (at-least-once delivery).
- **Rule AU-04:** `FieldChange` records for fields classified `SENSITIVE` store values as `[REDACTED]` in the `before_value` and `after_value` columns; the full values are written only to the SIEM stream over mTLS.

---

### Aggregate 9 — RetentionAggregate

**Root Entity:** `RetentionPolicy`
**Internal Entities:** `RetentionExecution`, `ArchivedRecord`

**Purpose:**
Manages data lifecycle governance for CHE's historical data. `RetentionPolicy` entities define the retention duration, archival target (e.g., S3 Glacier, cold PostgreSQL partition), and purge rules for each entity type. `RetentionExecution` logs each scheduled or manual execution of a retention policy. `ArchivedRecord` holds a metadata stub for records that have been moved out of the primary store, enabling audit trail continuity without retaining the full record in hot storage.

**Invariants:**
- `RetentionPolicy.retention_days` must be greater than zero.
- `RetentionPolicy` must not be deletable if an active `RetentionExecution` is in progress (`status = RUNNING`).
- `RetentionExecution.records_processed` must equal the count of `ArchivedRecord` rows created during that execution at completion.
- `ArchivedRecord.archive_location_uri` must be a valid URI referencing the archival storage location.

**Key Business Rules:**
- **Rule RT-01:** Audit records (`AuditAggregate`) have a minimum retention of 7 years and may not be purged, only archived.
- **Rule RT-02:** `RetentionPolicyExecuted` event is emitted on successful completion; `RetentionExecutionFailed` is emitted on partial failure, with error detail in JSONB.
- **Rule RT-03:** Purge operations require a dual-approval workflow (two `COST_ADMIN` actors) and are subject to a 48-hour cooling-off period after approval before execution.
- **Rule RT-04:** Archived records retain their `entity_id`, `entity_type`, `version_number`, and `occurred_at` as searchable metadata even after the full record is moved to cold storage.

---

### Aggregate 10 — BenchmarkAggregate

**Root Entity:** `BenchmarkSnapshot`
**Internal Entities:** `BenchmarkDataPoint`, `BenchmarkSource`

**Purpose:**
Stores periodic competitive cost benchmarks used for "should-cost" analysis, AI forecasting baseline calibration, and supplier negotiation support. Each `BenchmarkSnapshot` represents a discrete benchmark exercise (e.g., quarterly industry survey, annual purchased parts benchmark). `BenchmarkDataPoint` entities hold individual data points for a specific material, component, or process within the benchmark. `BenchmarkSource` records the provenance of each data point (e.g., industry association, third-party cost database, internal engineering estimate).

**Invariants:**
- `BenchmarkSnapshot.benchmark_period` must be unique per `(benchmark_type, benchmark_scope)`.
- `BenchmarkDataPoint.benchmark_value` must be positive.
- `BenchmarkDataPoint.currency_code` must be an ISO 4217 code.
- `BenchmarkSource.source_credibility_score` must be in [0.0, 1.0].

**Key Business Rules:**
- **Rule BM-01:** `BenchmarkSnapshot` records are immutable once `status = PUBLISHED`; corrections require a new snapshot with an amended `benchmark_period` or a new version label.
- **Rule BM-02:** AI Forecasting Service consumes `BenchmarkSnapshot` records via the `che.forecasting-feed` Kafka topic to recalibrate embedding-based forecast models.
- **Rule BM-03:** Each `BenchmarkDataPoint` must be associated with at least one `BenchmarkSource`; unsourced data points are rejected at insertion time.
- **Rule BM-04:** Benchmark data classified as `CONFIDENTIAL` (e.g., named competitor prices) is stored encrypted at the field level using PostgreSQL `pgcrypto` and is accessible only to the `benchmark_analyst` role.

---

## 1.4 Domain Events

The following table documents all 18 domain events produced and consumed within the CHE bounded context. Events are published to Apache Kafka. All events carry a standard envelope: `event_id` (UUID), `event_type` (string), `aggregate_id` (UUID), `aggregate_type` (string), `occurred_at` (ISO 8601), `schema_version` (semver), `correlation_id` (UUID), `causation_id` (UUID).

| # | Event Name | Trigger | Producer | Consumer(s) |
|---|---|---|---|---|
| 1 | `CostSnapshotCreated` | CCE completes a cost calculation and CHE's Kafka consumer successfully persists the new `CostSnapshot` record | CHE (Kafka consumer of CCE events) | BI/Analytics, Dashboard UI, AI Forecasting Service |
| 2 | `CostSnapshotApproved` | A `COST_APPROVER` actor approves a `CostSnapshot` via the REST API | CHE (REST write path) | Dashboard UI, ERP Integration (for actual cost confirmation), Audit/Compliance |
| 3 | `CostSnapshotSuperseded` | A new `CostSnapshot` version is created for the same `(reference_entity_id, cost_type)`, setting `superseded_by` on the prior record | CHE (internal, triggered within the same transaction as `CostSnapshotCreated`) | Dashboard UI (to invalidate cache), AI Forecasting Service (to update training data pointer), Audit/Compliance |
| 4 | `QuoteSubmitted` | RFQ Engine publishes a supplier quote event; CHE consumer persists the initial `Quote` record with `status = SUBMITTED` | CHE (Kafka consumer of RFQ Engine events) | Dashboard UI, Audit/Compliance, BI/Analytics |
| 5 | `QuoteRevised` | A new `QuoteVersion` is created for an existing `Quote`, incrementing `version_number` | CHE (REST write path or Kafka consumer) | Dashboard UI, BI/Analytics, AI Forecasting Service |
| 6 | `QuoteAccepted` | A `PROCUREMENT_MANAGER` actor sets `Quote.status = ACCEPTED`; triggers creation of `SupplierPriceRecord` | CHE (REST write path) | SIE (to update supplier scorecard), BI/Analytics, Dashboard UI, Audit/Compliance |
| 7 | `QuoteRejected` | A `PROCUREMENT_MANAGER` actor sets `Quote.status = REJECTED` with a mandatory rejection reason | CHE (REST write path) | BI/Analytics, SIE (for supplier performance tracking), Audit/Compliance |
| 8 | `RFQRoundIssued` | RFQ Engine publishes an `RFQIssued` event; CHE consumer creates an `RFQRound` with `status = ISSUED` | CHE (Kafka consumer of RFQ Engine events) | Dashboard UI, BI/Analytics |
| 9 | `BidReceived` | A supplier submits a bid; RFQ Engine publishes the event; CHE consumer creates an `RFQBid` record | CHE (Kafka consumer of RFQ Engine events) | Dashboard UI, BI/Analytics, AI Forecasting Service |
| 10 | `BidAwarded` | An `RFQBid` is awarded; triggers `SupplierPriceRecord` creation and `BidComparison` finalization | CHE (REST write path, dual-step transaction) | SIE, ERP Integration, Dashboard UI, Audit/Compliance, BI/Analytics |
| 11 | `SupplierPriceRecorded` | A `SupplierPriceRecord` is successfully persisted (from RFQ award, manual entry, or ERP import) | CHE (internal, multiple write paths) | AI Forecasting Service, BI/Analytics, Dashboard UI |
| 12 | `SupplierPriceSuperseded` | A new `SupplierPriceRecord` overlaps an existing active record; the prior record is marked superseded | CHE (internal, triggered within supersession transaction) | AI Forecasting Service (to retrain on updated series), BI/Analytics, Audit/Compliance |
| 13 | `MaterialPriceIndexed` | A `MarketDataPoint` is ingested from an external market data provider or from MIE | CHE (Kafka consumer of MIE and market data feed events) | AI Forecasting Service, BI/Analytics, Dashboard UI |
| 14 | `MaterialPriceForecastGenerated` | AI Forecasting Service returns a completed forecast; CHE persists the `ForecastRecord` | CHE (REST inbound from AI Forecasting Service) | Dashboard UI, BI/Analytics, Audit/Compliance |
| 15 | `ProcessCostRecorded` | A `ProcessCostRecord` is persisted from an MPE Kafka event or ERP import | CHE (Kafka consumer of MPE events) | AI Forecasting Service, BI/Analytics, Dashboard UI |
| 16 | `EntityVersionCreated` | Any aggregate root mutation in CHE creates a corresponding `EntityVersion` record | CHE (internal, co-transaction with every write) | Audit/Compliance, BI/Analytics (for version lineage queries) |
| 17 | `AuditEventWritten` | Any write operation produces an `AuditEvent`; this event fans out the audit record to the SIEM | CHE (internal, co-transaction with every write, then Kafka publish post-commit) | SIEM, Audit/Compliance Reporting, Audit/Compliance Dashboard |
| 18 | `RetentionPolicyExecuted` | A scheduled or manually triggered `RetentionExecution` completes successfully | CHE (Retention Job Scheduler) | Audit/Compliance, Storage Management Dashboard |

---

## 1.5 Entity Relationship Summary

| Entity | Aggregate | Owns FK to | Referenced by |
|---|---|---|---|
| `CostSnapshot` | CostSnapshotAggregate | `superseded_by → cost_snapshots.snapshot_id` (self-ref) | `snapshot_line_items.snapshot_id`, `snapshot_meta.snapshot_id`, `entity_versions.entity_id` (polymorphic), `audit_events.entity_id` (polymorphic) |
| `SnapshotLineItem` | CostSnapshotAggregate | `snapshot_id → cost_snapshots.snapshot_id` | _(leaf entity, not referenced externally)_ |
| `SnapshotMeta` | CostSnapshotAggregate | `snapshot_id → cost_snapshots.snapshot_id` | _(leaf entity, not referenced externally)_ |
| `Quote` | QuoteAggregate | `rfq_round_id → rfq_rounds.round_id` (nullable), `superseded_by → quotes.quote_id` (self-ref) | `quote_lines.quote_id`, `quote_versions.quote_id`, `quote_approvals.quote_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `QuoteLine` | QuoteAggregate | `quote_id → quotes.quote_id`, `quote_version_id → quote_versions.version_id` | _(leaf entity)_ |
| `QuoteVersion` | QuoteAggregate | `quote_id → quotes.quote_id` | `quote_lines.quote_version_id`, `quote_approvals.quote_version_id` |
| `QuoteApproval` | QuoteAggregate | `quote_id → quotes.quote_id`, `quote_version_id → quote_versions.version_id` | _(leaf entity)_ |
| `RFQRound` | RFQAggregate | _(no FK to other aggregates at root level)_ | `rfq_lines.round_id`, `rfq_bids.round_id`, `bid_comparisons.round_id`, `quotes.rfq_round_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `RFQLine` | RFQAggregate | `round_id → rfq_rounds.round_id` | `rfq_bids.rfq_line_id` |
| `RFQBid` | RFQAggregate | `round_id → rfq_rounds.round_id`, `rfq_line_id → rfq_lines.line_id`, `superseded_by → rfq_bids.bid_id` (self-ref) | `bid_comparisons.bid_id` (m2m via `bid_comparison_bids`) |
| `BidComparison` | RFQAggregate | `round_id → rfq_rounds.round_id` | _(leaf entity; comparison_matrix stored as JSONB)_ |
| `SupplierPriceRecord` | SupplierPriceHistoryAggregate | `superseded_by → supplier_price_records.record_id` (self-ref) | `price_adjustments.record_id`, `index_links.record_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `PriceAdjustment` | SupplierPriceHistoryAggregate | `record_id → supplier_price_records.record_id` | _(leaf entity)_ |
| `IndexLink` | SupplierPriceHistoryAggregate | `record_id → supplier_price_records.record_id` | _(leaf entity)_ |
| `MaterialPriceRecord` | MaterialPriceHistoryAggregate | `superseded_by → material_price_records.record_id` (self-ref) | `market_data_points.record_id`, `forecast_records.record_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `MarketDataPoint` | MaterialPriceHistoryAggregate | `record_id → material_price_records.record_id` | _(leaf entity)_ |
| `ForecastRecord` | MaterialPriceHistoryAggregate | `record_id → material_price_records.record_id` | _(leaf entity)_ |
| `ProcessCostRecord` | ProcessCostHistoryAggregate | `superseded_by → process_cost_records.record_id` (self-ref) | `cost_components.record_id`, `oee_snapshots.record_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `CostComponent` | ProcessCostHistoryAggregate | `record_id → process_cost_records.record_id` | _(leaf entity)_ |
| `OEESnapshot` | ProcessCostHistoryAggregate | `record_id → process_cost_records.record_id` | _(leaf entity)_ |
| `EntityVersion` | VersionAggregate | `entity_id` (polymorphic, no DB-level FK) | `version_diffs.version_id`, `version_labels.version_id` |
| `VersionDiff` | VersionAggregate | `version_id → entity_versions.version_id` | _(leaf entity)_ |
| `VersionLabel` | VersionAggregate | `version_id → entity_versions.version_id` | _(leaf entity)_ |
| `AuditEvent` | AuditAggregate | `entity_id` (polymorphic, no DB-level FK) | `field_changes.event_id`, `actor_contexts.event_id` |
| `FieldChange` | AuditAggregate | `event_id → audit_events.event_id` | _(leaf entity)_ |
| `ActorContext` | AuditAggregate | `event_id → audit_events.event_id` | _(leaf entity)_ |
| `RetentionPolicy` | RetentionAggregate | _(no FK to other aggregates)_ | `retention_executions.policy_id` |
| `RetentionExecution` | RetentionAggregate | `policy_id → retention_policies.policy_id` | `archived_records.execution_id` |
| `ArchivedRecord` | RetentionAggregate | `execution_id → retention_executions.execution_id` | _(leaf entity)_ |
| `BenchmarkSnapshot` | BenchmarkAggregate | _(no FK to other aggregates at root level)_ | `benchmark_data_points.snapshot_id`, `benchmark_sources.snapshot_id`, `entity_versions.entity_id`, `audit_events.entity_id` |
| `BenchmarkDataPoint` | BenchmarkAggregate | `snapshot_id → benchmark_snapshots.snapshot_id` | `benchmark_sources.data_point_id` |
| `BenchmarkSource` | BenchmarkAggregate | `snapshot_id → benchmark_snapshots.snapshot_id`, `data_point_id → benchmark_data_points.data_point_id` | _(leaf entity)_ |

---

# 2. ERD

## 2.1 Full ASCII ERD

The ERD below uses box-drawing characters and Mermaid-style cardinality notation adapted for ASCII. Tables are grouped by aggregate. Primary keys are marked `(PK)`, foreign keys `(FK)`, and unique constraints `(UQ)`.

```
╔══════════════════════════════════════════════════════════════════════════════════════════════╗
║                          COST HISTORY ENGINE — ENTITY RELATIONSHIP DIAGRAM                  ║
╚══════════════════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  GROUP A — CORE                                                                              │
│  cost_snapshots · snapshot_line_items · snapshot_meta · quotes · quote_lines ·              │
│  quote_versions · quote_approvals · rfq_rounds · rfq_lines · rfq_bids · bid_comparisons    │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│          cost_snapshots              │          │         snapshot_line_items             │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ snapshot_id         UUID    (PK)     │          │ line_item_id        UUID    (PK)        │
│ reference_entity_id UUID    (UQ+IDX) │1       o{│ snapshot_id         UUID    (FK)        │
│ reference_entity_type VARCHAR(64)    ├──────────┤ line_item_type      VARCHAR(64)         │
│ cost_type           VARCHAR(64)      │          │ description         TEXT                │
│ version_number      INTEGER          │          │ quantity            NUMERIC(18,6)       │
│ status              VARCHAR(32)      │          │ unit_cost           NUMERIC(18,6)       │
│ currency_code       CHAR(3)          │          │ line_cost           NUMERIC(18,6)       │
│ total_cost          NUMERIC(18,6)    │          │ currency_code       CHAR(3)             │
│ valid_from          TIMESTAMPTZ      │          │ cost_element_code   VARCHAR(64)         │
│ valid_to            TIMESTAMPTZ      │          │ metadata_json       JSONB               │
│ superseded_by       UUID    (FK→self)│          └────────────────────────────────────────┘
│ source_type         VARCHAR(64)      │
│ source_reference_id UUID             │          ┌────────────────────────────────────────┐
│ approved_by         UUID             │          │          snapshot_meta                 │
│ approved_at         TIMESTAMPTZ      │          ├────────────────────────────────────────┤
│ created_at          TIMESTAMPTZ      │1       ||│ meta_id             UUID    (PK)        │
│ metadata_json       JSONB            ├──────────┤ snapshot_id         UUID    (FK)(UQ)    │
│ tags_json           JSONB            │          │ bom_version         VARCHAR(64)         │
└──────────────────────────────────────┘          │ routing_version     VARCHAR(64)         │
                  │                               │ overhead_rate_set   VARCHAR(64)         │
                  │ self-ref (superseded_by)       │ calculation_params  JSONB               │
                  └──────────────────────┐        │ data_sources_json   JSONB               │
                                         │        └────────────────────────────────────────┘
                              ┌──────────┘
                              │ (superseded_by → snapshot_id)
                              ▼ (same table, self-referential)
                        [cost_snapshots]


┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│              quotes                  │          │           quote_versions                │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ quote_id            UUID    (PK)     │          │ version_id          UUID    (PK)        │
│ quote_reference     VARCHAR(128)(UQ) │1       o{│ quote_id            UUID    (FK)        │
│ rfq_round_id        UUID    (FK,NULL)├──────────┤ version_number      INTEGER             │
│ supplier_id         UUID    (IDX)    │          │ version_label       VARCHAR(256)        │
│ customer_id         UUID    (IDX)    │          │ created_at          TIMESTAMPTZ         │
│ quote_direction     VARCHAR(16)      │          │ created_by          UUID                │
│ status              VARCHAR(32)      │          │ notes               TEXT                │
│ total_value         NUMERIC(18,6)    │          │ status              VARCHAR(32)         │
│ currency_code       CHAR(3)          │          └────────────────────────────────────────┘
│ incoterms           VARCHAR(16)      │
│ valid_from          TIMESTAMPTZ      │          ┌────────────────────────────────────────┐
│ valid_to            TIMESTAMPTZ      │          │            quote_lines                 │
│ superseded_by       UUID    (FK→self)│          ├────────────────────────────────────────┤
│ rejection_reason    TEXT             │1       o{│ line_id             UUID    (PK)        │
│ created_at          TIMESTAMPTZ      ├──────────┤ quote_id            UUID    (FK)        │
│ updated_at          TIMESTAMPTZ      │          │ quote_version_id    UUID    (FK)        │
│ metadata_json       JSONB            │          │ line_number         INTEGER             │
└──────────────────────────────────────┘          │ item_id             UUID                │
              │                                   │ item_type           VARCHAR(64)         │
              │1                                  │ item_description    TEXT                │
              └────────────────────────────o{     │ quantity            NUMERIC(18,6)       │
                                                  │ unit_price          NUMERIC(18,6)       │
                                    ┌─────────────┤ currency_code       CHAR(3)             │
                                    │             │ incoterms           VARCHAR(16)         │
                                    │             │ lead_time_days      INTEGER             │
                                    │             │ line_metadata_json  JSONB               │
                                    │             └────────────────────────────────────────┘
                                    │
                                    │             ┌────────────────────────────────────────┐
                                    │             │          quote_approvals                │
                                    │             ├────────────────────────────────────────┤
                                    └──────────o{ │ approval_id         UUID    (PK)        │
                                                  │ quote_id            UUID    (FK)        │
                                                  │ quote_version_id    UUID    (FK)        │
                                                  │ actor_id            UUID                │
                                                  │ approval_level      VARCHAR(8)          │
                                                  │ approved_at         TIMESTAMPTZ         │
                                                  │ notes               TEXT                │
                                                  └────────────────────────────────────────┘


┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│            rfq_rounds                │          │            rfq_lines                   │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ round_id            UUID    (PK)     │1       o{│ line_id             UUID    (PK)        │
│ rfq_reference       VARCHAR(128)(UQ) ├──────────┤ round_id            UUID    (FK)        │
│ status              VARCHAR(32)      │          │ line_number         INTEGER             │
│ issued_at           TIMESTAMPTZ      │          │ item_id             UUID                │
│ bid_deadline        TIMESTAMPTZ      │          │ item_type           VARCHAR(64)         │
│ awarded_at          TIMESTAMPTZ      │          │ item_description    TEXT                │
│ cancellation_reason TEXT             │          │ target_quantity      NUMERIC(18,6)      │
│ created_at          TIMESTAMPTZ      │          │ target_price        NUMERIC(18,6)       │
│ metadata_json       JSONB            │          │ currency_code       CHAR(3)             │
└──────────────────────────────────────┘          │ required_delivery   DATE                │
              │                                   │ line_metadata_json  JSONB               │
              │1                                  └────────────────────────────────────────┘
              │                                                    │
              └────────────────────────────────────────────────1   │
                                                                   │1
┌──────────────────────────────────────┐          ┌───────────────────────────────────────────┐
│           rfq_bids                   │          │          bid_comparisons                  │
├──────────────────────────────────────┤          ├───────────────────────────────────────────┤
│ bid_id              UUID    (PK)     │          │ comparison_id       UUID    (PK)           │
│ round_id            UUID    (FK)     │          │ round_id            UUID    (FK)(UQ)       │
│ rfq_line_id         UUID    (FK)     │          │ comparison_matrix   JSONB                  │
│ supplier_id         UUID    (IDX)    │          │ recommended_bid_id  UUID    (FK→rfq_bids)  │
│ status              VARCHAR(32)      │          │ scoring_weights     JSONB                  │
│ bid_price           NUMERIC(18,6)    │          │ created_at          TIMESTAMPTZ            │
│ bid_currency        CHAR(3)          │          │ created_by          UUID                   │
│ lead_time_days      INTEGER          │          │ notes               TEXT                   │
│ payment_terms       VARCHAR(128)     │          └───────────────────────────────────────────┘
│ incoterms           VARCHAR(16)      │
│ bid_submitted_at    TIMESTAMPTZ      │
│ superseded_by       UUID    (FK→self)│
│ is_awarded          BOOLEAN          │
│ bid_metadata_json   JSONB            │
└──────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  GROUP B — HISTORY                                                                           │
│  supplier_price_records · price_adjustments · index_links ·                                 │
│  material_price_records · market_data_points · forecast_records ·                           │
│  process_cost_records · cost_components · oee_snapshots                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│       supplier_price_records         │          │         price_adjustments              │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ record_id           UUID    (PK)     │1       o{│ adjustment_id       UUID    (PK)        │
│ supplier_id         UUID    (IDX)    ├──────────┤ record_id           UUID    (FK)        │
│ item_id             UUID    (IDX)    │          │ adjustment_type     VARCHAR(64)         │
│ item_type           VARCHAR(64)      │          │ adjustment_value    NUMERIC(18,6)       │
│ unit_price          NUMERIC(18,6)    │          │ adjusted_unit_price NUMERIC(18,6)       │
│ currency_code       CHAR(3)          │          │ effective_from      TIMESTAMPTZ         │
│ uom                 VARCHAR(32)      │          │ justification       TEXT                │
│ valid_from          TIMESTAMPTZ      │          │ actor_id            UUID                │
│ valid_to            TIMESTAMPTZ      │          │ created_at          TIMESTAMPTZ         │
│ source_type         VARCHAR(64)      │          └────────────────────────────────────────┘
│ source_reference_id UUID             │
│ superseded_by       UUID    (FK→self)│          ┌────────────────────────────────────────┐
│ justification       TEXT             │          │            index_links                 │
│ created_at          TIMESTAMPTZ      │          ├────────────────────────────────────────┤
│ metadata_json       JSONB            │1       o{│ link_id             UUID    (PK)        │
└──────────────────────────────────────┘  ────────┤ record_id           UUID    (FK)        │
                                                  │ index_id            VARCHAR(128)        │
                                                  │ index_name          VARCHAR(256)        │
                                                  │ index_value_at_rec  NUMERIC(18,6)       │
                                                  │ index_date          DATE                │
                                                  │ weight_pct          NUMERIC(5,4)        │
                                                  │ metadata_json       JSONB               │
                                                  └────────────────────────────────────────┘


┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│       material_price_records         │          │         market_data_points             │
│     (PARTITIONED BY valid_date       │          ├────────────────────────────────────────┤
│      RANGE, monthly)                 │1       o{│ data_point_id       UUID    (PK)        │
├──────────────────────────────────────┤  ────────┤ record_id           UUID    (FK)        │
│ record_id           UUID    (PK)     │          │ observation_date    DATE                │
│ material_id         UUID    (IDX)    │          │ price_value         NUMERIC(18,6)       │
│ price_type          VARCHAR(64)      │          │ price_type          VARCHAR(64)         │
│ data_source         VARCHAR(128)     │          │ data_source         VARCHAR(128)        │
│ unit_price          NUMERIC(18,6)    │          │ data_quality        NUMERIC(3,2)        │
│ currency_code       CHAR(3)          │          │ currency_code       CHAR(3)             │
│ uom                 VARCHAR(32)      │          │ raw_payload         JSONB               │
│ valid_date          DATE    (IDX)    │          └────────────────────────────────────────┘
│ superseded_by       UUID    (FK→self)│
│ data_quality        NUMERIC(3,2)     │          ┌────────────────────────────────────────┐
│ created_at          TIMESTAMPTZ      │          │         forecast_records               │
│ metadata_json       JSONB            │          ├────────────────────────────────────────┤
└──────────────────────────────────────┘1       o{│ forecast_id         UUID    (PK)        │
              │                         ─────────┤ record_id           UUID    (FK)        │
              │                                  │ forecast_horizon_d  INTEGER             │
              └──────────────────────────────────┤ forecast_date       DATE                │
                                                  │ forecast_value      NUMERIC(18,6)       │
                                                  │ ci_lower            NUMERIC(18,6)       │
                                                  │ ci_upper            NUMERIC(18,6)       │
                                                  │ model_version       VARCHAR(64)         │
                                                  │ embedding_model_id  VARCHAR(128)        │
                                                  │ superseded_by       UUID    (FK→self)   │
                                                  │ generated_at        TIMESTAMPTZ         │
                                                  │ metadata_json       JSONB               │
                                                  └────────────────────────────────────────┘


┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│       process_cost_records           │          │         cost_components                │
│  (PARTITIONED BY (period_year,       │          ├────────────────────────────────────────┤
│   period_month) LIST/RANGE)          │1       o{│ component_id        UUID    (PK)        │
├──────────────────────────────────────┤  ────────┤ record_id           UUID    (FK)        │
│ record_id           UUID    (PK)     │          │ component_type      VARCHAR(64)         │
│ process_id          UUID    (IDX)    │          │ component_cost      NUMERIC(18,6)       │
│ period_year         SMALLINT         │          │ currency_code       CHAR(3)             │
│ period_month        SMALLINT         │          │ uom                 VARCHAR(32)         │
│ cost_type           VARCHAR(64)      │          │ quantity_consumed    NUMERIC(18,6)      │
│ total_cost_per_unit NUMERIC(18,6)    │          │ unit_rate           NUMERIC(18,6)       │
│ currency_code       CHAR(3)          │          │ metadata_json       JSONB               │
│ source_type         VARCHAR(64)      │          └────────────────────────────────────────┘
│ source_reference_id UUID             │
│ is_revision         BOOLEAN          │          ┌────────────────────────────────────────┐
│ superseded_by       UUID    (FK→self)│          │           oee_snapshots                │
│ created_at          TIMESTAMPTZ      │||      ||│ oee_snapshot_id     UUID    (PK)        │
│ metadata_json       JSONB            ├──────────┤ record_id           UUID    (FK)(UQ)    │
└──────────────────────────────────────┘          │ availability        NUMERIC(5,4)        │
                                                  │ performance         NUMERIC(5,4)        │
                                                  │ quality             NUMERIC(5,4)        │
                                                  │ oee_percentage      NUMERIC(5,4)        │
                                                  │ snapshot_period_st  TIMESTAMPTZ         │
                                                  │ snapshot_period_end TIMESTAMPTZ         │
                                                  │ data_source         VARCHAR(128)        │
                                                  │ metadata_json       JSONB               │
                                                  └────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  GROUP C — VERSIONING                                                                        │
│  entity_versions · version_diffs · version_labels                                           │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│           entity_versions            │          │           version_diffs                │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ version_id          UUID    (PK)     │1       o{│ diff_id             UUID    (PK)        │
│ entity_type         VARCHAR(64)(IDX) ├──────────┤ version_id          UUID    (FK)        │
│ entity_id           UUID    (IDX)    │          │ diff_json           JSONB               │
│ version_number      INTEGER          │          │ diff_format         VARCHAR(32)         │
│ prior_version_id    UUID    (FK→self)│          │ created_at          TIMESTAMPTZ         │
│ created_at          TIMESTAMPTZ      │          └────────────────────────────────────────┘
│ created_by          UUID             │
│ change_summary      TEXT             │          ┌────────────────────────────────────────┐
│ is_system_generated BOOLEAN          │          │          version_labels                │
│ correlation_id      UUID             │          ├────────────────────────────────────────┤
└──────────────────────────────────────┘1       o{│ label_id            UUID    (PK)        │
              └──────────────────────────────────┤ version_id          UUID    (FK)        │
                                                  │ entity_type         VARCHAR(64)         │
                                                  │ entity_id           UUID                │
                                                  │ label_text          VARCHAR(256)        │
                                                  │ is_active           BOOLEAN             │
                                                  │ created_by          UUID                │
                                                  │ created_at          TIMESTAMPTZ         │
                                                  └────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  GROUP D — AUDIT                                                                             │
│  audit_events · field_changes · actor_contexts                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│            audit_events              │          │           field_changes                │
│  (RLS: INSERT only for audit_writer; │          ├────────────────────────────────────────┤
│   SELECT only for audit_reader;      │          │ change_id           UUID    (PK)        │
│   NO UPDATE, NO DELETE)              │1       o{│ event_id            UUID    (FK)        │
├──────────────────────────────────────┤  ────────┤ field_name          VARCHAR(256)        │
│ event_id            UUID    (PK)     │          │ before_value        TEXT                │
│ entity_type         VARCHAR(64)(IDX) │          │ after_value         TEXT                │
│ entity_id           UUID    (IDX)    │          │ is_sensitive        BOOLEAN             │
│ action              VARCHAR(64)      │          │ data_type           VARCHAR(64)         │
│ occurred_at         TIMESTAMPTZ      │          └────────────────────────────────────────┘
│ event_hash          CHAR(64)         │
│ correlation_id      UUID             │          ┌────────────────────────────────────────┐
│ causation_id        UUID             │          │          actor_contexts                │
│ schema_version      VARCHAR(16)      │          ├────────────────────────────────────────┤
│ metadata_json       JSONB            │||      ||│ context_id          UUID    (PK)        │
└──────────────────────────────────────┘  ────────┤ event_id            UUID    (FK)(UQ)    │
                                                  │ actor_id            UUID                │
                                                  │ actor_type          VARCHAR(32)         │
                                                  │ actor_email         VARCHAR(256)        │
                                                  │ ip_address          INET                │
                                                  │ jwt_subject         VARCHAR(256)        │
                                                  │ jwt_issuer          VARCHAR(256)        │
                                                  │ mtls_cert_fp        CHAR(64)            │
                                                  │ roles_at_time       JSONB               │
                                                  │ user_agent          TEXT                │
                                                  └────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  GROUP E — REFERENCE                                                                         │
│  benchmark_snapshots · benchmark_data_points · benchmark_sources ·                          │
│  retention_policies · retention_executions · archived_records                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│        benchmark_snapshots           │          │       benchmark_data_points            │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ snapshot_id         UUID    (PK)     │1       o{│ data_point_id       UUID    (PK)        │
│ benchmark_type      VARCHAR(64)      ├──────────┤ snapshot_id         UUID    (FK)        │
│ benchmark_scope     VARCHAR(128)     │          │ item_id             UUID                │
│ benchmark_period    VARCHAR(32)(UQ*) │          │ item_type           VARCHAR(64)         │
│ status              VARCHAR(32)      │          │ benchmark_value     NUMERIC(18,6)       │
│ published_at        TIMESTAMPTZ      │          │ currency_code       CHAR(3)             │
│ created_by          UUID             │          │ uom                 VARCHAR(32)         │
│ classification      VARCHAR(32)      │          │ percentile_rank     NUMERIC(5,2)        │
│ metadata_json       JSONB            │          │ data_classification VARCHAR(32)         │
└──────────────────────────────────────┘          │ metadata_json       JSONB               │
                                                  └────────────────────────────────────────┘
                                                                    │
                                                                    │1
                                                                    ▼o{
                                                  ┌────────────────────────────────────────┐
                                                  │         benchmark_sources              │
                                                  ├────────────────────────────────────────┤
                                                  │ source_id           UUID    (PK)        │
                                                  │ snapshot_id         UUID    (FK)        │
                                                  │ data_point_id       UUID    (FK)        │
                                                  │ source_name         VARCHAR(256)        │
                                                  │ source_type         VARCHAR(64)         │
                                                  │ source_credibility  NUMERIC(3,2)        │
                                                  │ source_date         DATE                │
                                                  │ source_uri          TEXT                │
                                                  │ metadata_json       JSONB               │
                                                  └────────────────────────────────────────┘


┌──────────────────────────────────────┐          ┌────────────────────────────────────────┐
│        retention_policies            │          │       retention_executions             │
├──────────────────────────────────────┤          ├────────────────────────────────────────┤
│ policy_id           UUID    (PK)     │1       o{│ execution_id        UUID    (PK)        │
│ entity_type         VARCHAR(64)(UQ)  ├──────────┤ policy_id           UUID    (FK)        │
│ retention_days      INTEGER          │          │ status              VARCHAR(32)         │
│ archive_target_uri  TEXT             │          │ started_at          TIMESTAMPTZ         │
│ purge_enabled       BOOLEAN          │          │ completed_at        TIMESTAMPTZ         │
│ min_retention_days  INTEGER          │          │ records_processed   BIGINT              │
│ approval_required   BOOLEAN          │          │ records_archived    BIGINT              │
│ created_by          UUID             │          │ records_purged      BIGINT              │
│ created_at          TIMESTAMPTZ      │          │ error_detail        JSONB               │
│ updated_at          TIMESTAMPTZ      │          │ triggered_by        UUID                │
│ is_active           BOOLEAN          │          │ trigger_type        VARCHAR(32)         │
└──────────────────────────────────────┘          └────────────────────────────────────────┘
                                                                    │
                                                                    │1
                                                                    ▼o{
                                                  ┌────────────────────────────────────────┐
                                                  │          archived_records              │
                                                  ├────────────────────────────────────────┤
                                                  │ archived_record_id  UUID    (PK)        │
                                                  │ execution_id        UUID    (FK)        │
                                                  │ entity_type         VARCHAR(64)         │
                                                  │ entity_id           UUID                │
                                                  │ version_number      INTEGER             │
                                                  │ occurred_at         TIMESTAMPTZ         │
                                                  │ archive_location_uri TEXT               │
                                                  │ archive_format      VARCHAR(32)         │
                                                  │ archived_at         TIMESTAMPTZ         │
                                                  │ checksum            CHAR(64)            │
                                                  └────────────────────────────────────────┘

╔══════════════════════════════════════════════════════════════════╗
║  CARDINALITY NOTATION                                            ║
║  ||──o{   one (mandatory) to zero-or-many                        ║
║  ||──||   one (mandatory) to exactly one                         ║
║  }o──||   many-to-one                                            ║
║  self-ref: superseded_by UUID → same table PK (self-referential) ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 2.2 Color Legend

The following table defines the logical grouping of all CHE tables. In visual tooling (e.g., pgAdmin, DBeaver, Grafana dashboards), these groups correspond to color coding as noted.

| Group | Color Code | Tables | Description |
|---|---|---|---|
| **Core** | Blue (`#2563EB`) | `cost_snapshots`, `snapshot_line_items`, `snapshot_meta`, `quotes`, `quote_lines`, `quote_versions`, `quote_approvals`, `rfq_rounds`, `rfq_lines`, `rfq_bids`, `bid_comparisons` | Primary cost objects that are the principal reason for the module's existence. Highest read and write throughput. Subject to the strictest version-control rules. Partitioned tables. |
| **History** | Green (`#16A34A`) | `supplier_price_records`, `price_adjustments`, `index_links`, `material_price_records`, `market_data_points`, `forecast_records`, `process_cost_records`, `cost_components`, `oee_snapshots` | Time-series historical data ingested from upstream modules and market data providers. Append-only. High volume. Date-partitioned for efficient range queries and archival. |
| **Versioning** | Amber (`#D97706`) | `entity_versions`, `version_diffs`, `version_labels` | Cross-aggregate version lineage ledger. Populated by every write operation in CHE. Enables point-in-time reconstruction of any entity. Moderate volume; predominantly read. |
| **Audit** | Red (`#DC2626`) | `audit_events`, `field_changes`, `actor_contexts` | Immutable, tamper-evident audit log. Protected by PostgreSQL RLS. Continuously streamed to SIEM via Kafka. High insert volume; rarely read except for compliance workflows. |
| **Reference** | Purple (`#7C3AED`) | `benchmark_snapshots`, `benchmark_data_points`, `benchmark_sources`, `retention_policies`, `retention_executions`, `archived_records` | Configuration, benchmark data, and operational metadata. Low write volume; queried for configuration lookups, benchmark analysis, and retention job execution. |

---

## 2.3 Key Design Decisions

### Decision 1 — Append-Only Core Tables: No UPDATE or DELETE

**Decision:** All core CHE tables (`cost_snapshots`, `quotes`, `rfq_rounds`, `supplier_price_records`, `material_price_records`, `process_cost_records`) are strictly append-only. No `UPDATE` or `DELETE` statements are permitted by any application role.

**Rationale:**
- **Audit integrity:** Immutable rows ensure that the historical record cannot be altered after the fact, satisfying SOX, ISO 9001, and IATF 16949 audit requirements without additional change-data-capture infrastructure.
- **Concurrent read performance:** Append-only tables avoid lock contention from row-level updates, which is critical for a read-heavy analytical workload where BI and AI Forecasting services issue range scans across millions of rows.
- **Simplicity of change tracking:** The supersession pattern (`superseded_by` FK pointing to the new version) provides a self-contained history chain without requiring a separate history shadow table or CDC log.
- **Kafka event replay:** Because each record is immutable, Kafka consumers can re-process events idempotently — replaying an already-persisted event results in a conflict on the unique constraint rather than a silent data mutation.

**Implementation:**

```sql
-- PostgreSQL: Revoke UPDATE and DELETE from the application role
REVOKE UPDATE, DELETE ON cost_snapshots FROM che_app_role;
REVOKE UPDATE, DELETE ON quotes FROM che_app_role;
REVOKE UPDATE, DELETE ON rfq_rounds FROM che_app_role;
REVOKE UPDATE, DELETE ON supplier_price_records FROM che_app_role;
REVOKE UPDATE, DELETE ON material_price_records FROM che_app_role;
REVOKE UPDATE, DELETE ON process_cost_records FROM che_app_role;

-- Enforce via RLS as defense-in-depth
ALTER TABLE cost_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY no_update ON cost_snapshots FOR UPDATE USING (false);
CREATE POLICY no_delete ON cost_snapshots FOR DELETE USING (false);
```

---

### Decision 2 — No Physical Deletes: Soft Deletion via Status and Retention Policies

**Decision:** Records are never physically deleted from CHE's primary store during normal operation. Logical deletion is expressed via a `status` field (e.g., `SUPERSEDED`, `CANCELLED`, `EXPIRED`). Physical removal from hot storage is executed exclusively by the `RetentionAggregate` after a policy-governed, dual-approved retention execution.

**Rationale:**
- **Regulatory compliance:** Many jurisdictions and industry standards (SOX Section 802, GDPR Article 17, REACH) require that commercial cost records be retained for 7–10 years. Physical deletion before the retention period would constitute a compliance violation.
- **Forensic auditability:** Even when a record is logically obsolete, regulators and auditors may need to reconstruct the cost basis for a product as of a specific historical date. Physical deletion would break this capability.
- **Data recovery:** Append-only + soft-delete gives the platform a natural "undo" mechanism — a superseded record can be reinstated by creating a new record with the same values, without needing backups or transaction log replay.
- **Retention policy as a first-class domain concept:** By modeling `RetentionPolicy` and `RetentionExecution` as explicit domain objects, the business can control, audit, and approve data lifecycle decisions with the same rigor applied to financial records.

**Implementation:**

```sql
-- Status-based soft deletion query pattern
-- Current records only:
SELECT * FROM cost_snapshots
WHERE reference_entity_id = $1
  AND cost_type = $2
  AND superseded_by IS NULL
  AND status NOT IN ('CANCELLED', 'EXPIRED');

-- Full history including superseded:
SELECT * FROM cost_snapshots
WHERE reference_entity_id = $1
  AND cost_type = $2
ORDER BY version_number ASC;
```

---

### Decision 3 — Soft Versioning: New Row Per Mutation with superseded_by

**Decision:** Every mutation of a versioned entity creates a new database row with an incremented `version_number`. The prior row's `superseded_by` column is set to the UUID of the new row within the same transaction. There is no separate `*_history` shadow table.

**Rationale:**
- **Single table simplicity:** Queries for current state and historical state operate on the same table with simple predicate filters (`superseded_by IS NULL` for current; no filter for full history). This eliminates JOIN complexity between a live table and a history table.
- **Transactional safety:** The supersession link and the new row are written in a single ACID transaction, ensuring there is never a window where both or neither version appears as "current."
- **Version chain traversal:** The `superseded_by` self-referential FK forms a singly-linked list of versions, allowing efficient recursive CTE traversal of the full version history for any entity.
- **pgvector compatibility:** When AI Forecasting embeds historical cost series using `text-embedding-3-small` (1536d), the embeddings are stored in a companion `che_embeddings` table keyed on `(entity_type, entity_id, version_number)`, allowing embeddings to remain valid even as new versions are created.

**Implementation:**

```sql
-- Supersession transaction pattern (pseudocode as SQL)
BEGIN;

  -- Insert new version
  INSERT INTO cost_snapshots (
      snapshot_id, reference_entity_id, cost_type,
      version_number, status, total_cost, valid_from, ...
  ) VALUES (
      gen_random_uuid(), $ref_id, $cost_type,
      (SELECT COALESCE(MAX(version_number), 0) + 1
       FROM cost_snapshots
       WHERE reference_entity_id = $ref_id AND cost_type = $cost_type),
      'ACTIVE', $total_cost, NOW(), ...
  )
  RETURNING snapshot_id INTO _new_snapshot_id;

  -- Supersede the prior current version atomically
  UPDATE cost_snapshots
  SET superseded_by = _new_snapshot_id,
      status        = 'SUPERSEDED'
  WHERE reference_entity_id = $ref_id
    AND cost_type            = $cost_type
    AND superseded_by        IS NULL
    AND status               != 'SUPERSEDED'
    AND snapshot_id          != _new_snapshot_id;

COMMIT;
```

> **Note:** The `UPDATE` here applies only to the `superseded_by` pointer column, which is a controlled exception to the append-only principle. The business data columns (costs, prices, dates) remain immutable. The `superseded_by` column is the sole mutable field on core tables and is only ever transitioned from `NULL` to a non-null UUID (never back to `NULL`).

---

### Decision 4 — Partition by Period

**Decision:** Three high-volume tables are partitioned by time period using PostgreSQL declarative partitioning:

- `cost_snapshots` — `PARTITION BY RANGE (valid_from)`, one partition per calendar month.
- `material_price_records` — `PARTITION BY RANGE (valid_date)`, one partition per calendar month.
- `process_cost_records` — `PARTITION BY LIST (period_year)` with sub-partitioning `BY LIST (period_month)` (or composite `RANGE` depending on data volume per period).

**Rationale:**
- **Query performance:** BI and AI Forecasting queries are almost always range-bounded by time (e.g., "all cost snapshots for 2023"). Partition pruning eliminates entire months of data from the scan plan, reducing I/O by orders of magnitude.
- **Archival efficiency:** The `RetentionAggregate` can detach and archive entire partitions (e.g., `DETACH PARTITION cost_snapshots_2018_01`) without scanning or locking the full table.
- **Index size management:** Indexes on `reference_entity_id`, `supplier_id`, and `material_id` are local to each partition, keeping individual B-tree sizes manageable.
- **Maintenance windows:** `VACUUM`, `ANALYZE`, and `CLUSTER` operations can be scoped to individual partitions, reducing maintenance impact on live partitions.

**Implementation:**

```sql
-- cost_snapshots partitioned by valid_from
CREATE TABLE cost_snapshots (
    snapshot_id          UUID         NOT NULL DEFAULT gen_random_uuid(),
    reference_entity_id  UUID         NOT NULL,
    reference_entity_type VARCHAR(64) NOT NULL,
    cost_type            VARCHAR(64)  NOT NULL,
    version_number       INTEGER      NOT NULL,
    status               VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE',
    currency_code        CHAR(3)      NOT NULL,
    total_cost           NUMERIC(18,6) NOT NULL,
    valid_from           TIMESTAMPTZ  NOT NULL,
    valid_to             TIMESTAMPTZ,
    superseded_by        UUID,
    source_type          VARCHAR(64),
    source_reference_id  UUID,
    approved_by          UUID,
    approved_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metadata_json        JSONB        NOT NULL DEFAULT '{}',
    tags_json            JSONB        NOT NULL DEFAULT '[]'
) PARTITION BY RANGE (valid_from);

-- Monthly partitions (example: 2024-01 through 2024-03)
CREATE TABLE cost_snapshots_2024_01
    PARTITION OF cost_snapshots
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE cost_snapshots_2024_02
    PARTITION OF cost_snapshots
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

-- Default partition catches out-of-range inserts for monitoring
CREATE TABLE cost_snapshots_default
    PARTITION OF cost_snapshots DEFAULT;
```

---

### Decision 5 — UUID Primary Keys Throughout (uuid-ossp)

**Decision:** All primary keys across all CHE tables are `UUID` generated via PostgreSQL's `uuid-ossp` extension (`gen_random_uuid()` / `uuid_generate_v4()`).

**Rationale:**
- **Cross-service correlation:** UUIDs can be generated by the producing service (CCE, RFQ Engine, MPE) before writing to Kafka, enabling idempotent event consumption — the consumer can attempt `INSERT ... ON CONFLICT DO NOTHING` using the pre-assigned UUID.
- **No sequence bottleneck:** Sequential integer sequences become a contention point under high-concurrency inserts across multiple partitions. UUIDs are independent of any central sequence generator.
- **Security:** UUIDs do not expose row count or insertion rate information to external API consumers.
- **Distribution:** UUID v4 distributes evenly across the UUID space, avoiding hotspot effects in B-tree indexes when combined with PostgreSQL's fill factor tuning.

**Implementation:**

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Example: cost_snapshots PK
snapshot_id UUID NOT NULL DEFAULT gen_random_uuid()

-- Cross-service pre-assignment pattern:
-- Producer (CCE) generates UUID before publishing to Kafka.
-- CHE consumer uses the same UUID as the PK.
-- Duplicate Kafka delivery → ON CONFLICT (snapshot_id) DO NOTHING.
```

---

### Decision 6 — JSONB for Flexible Metadata, Tags, and Diffs

**Decision:** Several columns across CHE tables use the PostgreSQL `JSONB` data type: `metadata_json`, `tags_json`, `diff_json`, `comparison_matrix`, `calculation_params`, `roles_at_time`, and others.

**Rationale:**
- **Schema extensibility:** Upstream producers (CCE, MPE, MIE, ERP) may attach context-specific key-value pairs that do not belong in a normalized column. JSONB accommodates this without requiring schema migrations for every new upstream field.
- **JSONB indexing:** GIN indexes on `metadata_json` and `tags_json` support efficient containment queries (`@>`) for filtering cost snapshots by arbitrary tags (e.g., `tags_json @> '["project:X", "bom-version:v3"]'`), which are common in BI dashboards.
- **RFC 6902 diffs:** `VersionDiff.diff_json` stores JSON Patch documents. JSONB preserves the structure without compression artifacts that would break patch application on the client side.
- **AI feature extraction:** The AI Forecasting Service reads `metadata_json` fields from `material_price_records` and `cost_snapshots` to extract features for embedding and forecasting. JSONB is directly consumable by the Python `psycopg3` + `asyncpg` clients used by that service.

**Implementation:**

```sql
-- GIN index for tag containment queries
CREATE INDEX idx_cost_snapshots_tags_gin
    ON cost_snapshots USING GIN (tags_json);

-- GIN index for metadata key lookups
CREATE INDEX idx_cost_snapshots_metadata_gin
    ON cost_snapshots USING GIN (metadata_json jsonb_path_ops);

-- Example containment query
SELECT snapshot_id, total_cost, valid_from
FROM cost_snapshots
WHERE tags_json @> '["project:PROJ-001"]'
  AND superseded_by IS NULL
  AND valid_from >= '2024-01-01'
ORDER BY valid_from DESC;
```

---

### Decision 7 — Immutable Audit Log Enforced by PostgreSQL RLS

**Decision:** The `audit_events`, `field_changes`, and `actor_contexts` tables are protected by PostgreSQL Row Level Security (RLS) policies that permit `INSERT` by the `audit_writer` role, `SELECT` by the `audit_reader` role, and explicitly deny `UPDATE` and `DELETE` to all roles including superusers (via `USING (false)` policies).

**Rationale:**
- **Defense-in-depth:** Application-layer access controls can be misconfigured or bypassed by a compromised service account. RLS enforcement at the database engine level provides a second layer that is independent of application logic.
- **Tamper evidence:** Preventing updates and deletes, combined with the `event_hash` column (SHA-256 of key fields computed at insertion), enables spot-check verification that audit records have not been altered since creation.
- **Compliance requirement:** SOX Section 802 requires that audit logs be protected from alteration or destruction. PCI DSS Requirement 10.5 similarly mandates that audit log integrity be protected. RLS provides a documented, auditable mechanism that satisfies these requirements.
- **Separation of duties:** The `audit_writer` role is granted only to the CHE service's internal Kafka consumer and API write path. The `audit_reader` role is granted separately to the Compliance Reporting service and SIEM forwarder. No single application role holds both, and neither holds the DBA role.

**Implementation:**

```sql
-- Enable RLS on audit tables
ALTER TABLE audit_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE field_changes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE actor_contexts  ENABLE ROW LEVEL SECURITY;

-- Force RLS even for table owner (defense against privilege escalation)
ALTER TABLE audit_events    FORCE ROW LEVEL SECURITY;
ALTER TABLE field_changes   FORCE ROW LEVEL SECURITY;
ALTER TABLE actor_contexts  FORCE ROW LEVEL SECURITY;

-- Allow INSERT for audit_writer only
CREATE POLICY audit_events_insert
    ON audit_events FOR INSERT
    TO audit_writer WITH CHECK (true);

-- Allow SELECT for audit_reader only
CREATE POLICY audit_events_select
    ON audit_events FOR SELECT
    TO audit_reader USING (true);

-- Explicitly block UPDATE and DELETE for all roles
CREATE POLICY audit_events_no_update
    ON audit_events FOR UPDATE USING (false);

CREATE POLICY audit_events_no_delete
    ON audit_events FOR DELETE USING (false);

-- Verify event hash integrity (spot-check function)
CREATE OR REPLACE FUNCTION verify_audit_event_hash(p_event_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_stored_hash  CHAR(64);
    v_computed     CHAR(64);
    v_event        audit_events%ROWTYPE;
BEGIN
    SELECT * INTO v_event FROM audit_events WHERE event_id = p_event_id;
    v_stored_hash := v_event.event_hash;
    v_computed := encode(
        sha256(
            (v_event.event_id::TEXT
             || v_event.entity_id::TEXT
             || v_event.occurred_at::TEXT
             || v_event.action
             || COALESCE((
                 SELECT actor_id::TEXT
                 FROM actor_contexts ac
                 WHERE ac.event_id = p_event_id
                 LIMIT 1
             ), 'SYSTEM'))::BYTEA
        ), 'hex'
    );
    RETURN v_stored_hash = v_computed;
END;
$$;
```

---

*End of document — Cost History Engine Domain Model & ERD v1.0.0*
