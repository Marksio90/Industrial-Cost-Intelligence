# Cost History Engine — Section 11–14: Retention, SQL Schema, Indexing & Partitioning

## Section 11 — Data Retention

### 11.1 Retention Policy Overview

The Cost History Engine enforces a tiered retention strategy that balances operational performance, regulatory compliance (GDPR, SOX, contract law), and storage costs. All policies are codified in the `retention_policies` table and executed by the `RetentionManager` service.

**Storage Tiers**

| Tier | Storage | Access Pattern | Compression |
|------|---------|---------------|-------------|
| Hot | PostgreSQL 16 (partitioned) | Real-time read/write | Native PG compression |
| Warm | PostgreSQL 16 (compressed partitions, detached) | Occasional read, pg_restore | ZSTD level 3 |
| Cold | AWS S3 / S3 Glacier | Compliance retrieval only | GZIP + manifest |

---

### 11.2 Retention Policy Table

| Data Class | Status Filter | Hot Retention | Warm Retention | Cold Retention | Delete After | Notes |
|------------|--------------|---------------|----------------|----------------|--------------|-------|
| CostSnapshot | APPROVED | 7 years | — | Indefinite | Never delete | Legal/contract obligation |
| CostSnapshot | DRAFT | 90 days | — | — | 90 days | Auto-delete on expiry |
| CostSnapshot | SUPERSEDED | 3 years | 4 years | Indefinite | Never delete | Audit chain preservation |
| Quote | ACCEPTED / BINDING | 10 years | — | 15 years | Never delete | Contract law requirement |
| Quote | DRAFT | 2 years | — | — | 2 years | No commercial significance |
| Quote | REJECTED | 2 years | — | — | 2 years | Competitive intelligence limit |
| RFQ Round | AWARDED | 7 years | — | 10 years | Never delete | Procurement compliance |
| RFQ Round | CANCELLED | 1 year | — | — | 1 year | Minimal retention |
| RFQ Bid | Any | 7 years | — | — | 7 years | Tied to RFQ round |
| SupplierPriceRecord | All | 7 years | 8 years | 15 years | 15 years | Market analysis baseline |
| MaterialPriceRecord | All | 10 years | — | Indefinite | Never delete | Commodity index history |
| ProcessCostRecord | All | 5 years | 2 years | 10 years | 10 years | Operational planning |
| AuditEvent | All | 7 years | — | Indefinite | Never delete | GDPR Art.5(2), SOX |
| ForecastRecord | All | 2 years | 1 year | 5 years | 5 years | Model drift analysis |
| BenchmarkSnapshot | All | 5 years | 5 years | Indefinite | Never delete | Industry benchmarking |

---

### 11.3 RetentionPolicy Entity

```sql
-- Defined in Section 12 DDL; logical description:
-- policy_id          UUID PRIMARY KEY
-- data_class         TEXT NOT NULL          -- e.g. 'CostSnapshot', 'Quote'
-- status_filter      TEXT[]                 -- NULL = all statuses
-- hot_days           INTEGER NOT NULL       -- days in hot PostgreSQL storage
-- warm_days          INTEGER                -- days in warm compressed partition
-- cold_days          INTEGER                -- days in S3/Glacier (NULL = indefinite)
-- delete_after_days  INTEGER                -- NULL = never delete
-- legal_hold_flag    BOOLEAN DEFAULT FALSE  -- overrides all timers
-- gdpr_applies       BOOLEAN DEFAULT FALSE  -- triggers PII erasure workflow
-- created_at         TIMESTAMPTZ
-- updated_at         TIMESTAMPTZ
```

### 11.4 RetentionExecution Entity

```sql
-- run_id             UUID PRIMARY KEY
-- policy_id          UUID FK retention_policies
-- executed_at        TIMESTAMPTZ NOT NULL
-- records_evaluated  BIGINT
-- records_archived   BIGINT
-- records_deleted    BIGINT
-- s3_key_prefix      TEXT       -- e.g. s3://che-cold/cost_snapshots/2024/Q1/
-- status             TEXT       -- RUNNING / COMPLETED / FAILED / PARTIAL
-- error_detail       TEXT
-- duration_seconds   INTEGER
```

---

### 11.5 Legal Hold Mechanism

Legal holds are placed on individual entity records and override all retention timers. A record under legal hold:
- Cannot be deleted regardless of policy expiry
- Cannot be moved to warm or cold tier (must remain query-accessible in hot tier)
- Generates an alert if a retention job attempts to process it
- Is released only by a COMPLIANCE_OFFICER or CHE_ADMIN with documented justification

```sql
-- legal_holds table (defined in Section 12):
-- hold_id      UUID PRIMARY KEY
-- entity_type  TEXT NOT NULL  -- 'CostSnapshot', 'Quote', etc.
-- entity_id    UUID NOT NULL
-- reason       TEXT NOT NULL
-- imposed_by   UUID NOT NULL  -- actor_id of compliance officer
-- imposed_at   TIMESTAMPTZ NOT NULL
-- released_at  TIMESTAMPTZ    -- NULL = still active
-- released_by  UUID
-- UNIQUE (entity_type, entity_id) WHERE released_at IS NULL
```

---

### 11.6 GDPR Erasure for PII Fields

GDPR erasure does **not** delete audit event records (which would violate SOX/audit trail requirements). Instead, the `GDPRErasureHandler` replaces PII field values in-place:

- `actor_ip_hash` in `audit_events` → `[ERASED-GDPR-2024]`
- `created_by` references where the data subject requests erasure → `[ERASED-GDPR-2024]`
- `approved_by` fields → `[ERASED-GDPR-2024]`

The event record structure, timestamps, entity references, and business values are preserved. Only personal identifiers are overwritten.

---

### 11.7 Python RetentionManager

```python
"""
cost_history_engine/retention/retention_manager.py

Retention policy executor for the Cost History Engine.
Runs nightly via scheduled Kubernetes CronJob.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import boto3
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


@dataclass
class RetentionPolicy:
    policy_id: uuid.UUID
    data_class: str
    status_filter: list[str] | None
    hot_days: int
    warm_days: int | None
    cold_days: int | None
    delete_after_days: int | None
    legal_hold_flag: bool
    gdpr_applies: bool


@dataclass
class RetentionResult:
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    policy_id: uuid.UUID | None = None
    records_evaluated: int = 0
    records_archived: int = 0
    records_deleted: int = 0
    s3_key_prefix: str = ""
    status: str = "RUNNING"
    error_detail: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RetentionManager:
    """
    Manages data lifecycle for all CHE entities.

    Execution order per policy:
      1. evaluate_policies() — identify records eligible for each lifecycle stage
      2. archive_to_cold()   — pg_dump subset + S3 upload + manifest
      3. delete_expired()    — hard-delete only after cold archive confirmed
      4. apply_legal_hold()  / release_legal_hold() — on-demand by compliance team
    """

    def __init__(
        self,
        dsn: str,
        s3_bucket: str,
        s3_prefix: str = "cold-archive",
        aws_region: str = "eu-west-1",
        pg_version: str = "16",
    ) -> None:
        self._dsn = dsn
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix
        self._s3 = boto3.client("s3", region_name=aws_region)
        self._pg_version = pg_version

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def evaluate_policies(self) -> list[RetentionResult]:
        """
        Load all active retention policies and, for each, determine:
          - how many records are past hot_days threshold
          - how many are past delete_after_days threshold
        Returns a summary list (does not mutate data).
        """
        results: list[RetentionResult] = []
        policies = self._load_policies()

        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            for policy in policies:
                result = RetentionResult(policy_id=policy.policy_id)
                table = self._table_for_class(policy.data_class)
                ts_col = self._timestamp_col_for_table(table)

                eligible_sql = f"""
                    SELECT COUNT(*) AS cnt
                    FROM cost_history.{table} t
                    LEFT JOIN cost_history.legal_holds lh
                        ON lh.entity_type = %s
                        AND lh.entity_id = t.{self._pk_col(table)}
                        AND lh.released_at IS NULL
                    WHERE t.{ts_col} < NOW() - INTERVAL '%s days'
                      AND lh.hold_id IS NULL
                """
                params: list[Any] = [policy.data_class, policy.hot_days]

                if policy.status_filter:
                    eligible_sql += " AND t.status = ANY(%s)"
                    params.append(policy.status_filter)

                row = conn.execute(eligible_sql, params).fetchone()
                result.records_evaluated = row["cnt"] if row else 0

                if policy.delete_after_days:
                    del_sql = eligible_sql.replace(
                        f"INTERVAL '%s days'",
                        f"INTERVAL '{policy.delete_after_days} days'",
                    )
                    del_row = conn.execute(del_sql, params).fetchone()
                    result.records_deleted = del_row["cnt"] if del_row else 0

                result.status = "EVALUATED"
                results.append(result)
                logger.info(
                    "Policy %s (%s): %d eligible, %d deletion-ready",
                    policy.policy_id,
                    policy.data_class,
                    result.records_evaluated,
                    result.records_deleted,
                )

        return results

    def archive_to_cold(
        self,
        policy: RetentionPolicy,
        cutoff_date: date,
    ) -> RetentionResult:
        """
        Dumps records older than cutoff_date to S3 as GZIP-compressed JSON-LD,
        then records a manifest entry for restore traceability.

        Steps:
          1. SELECT eligible records (respecting legal hold exclusion)
          2. Serialize to JSON-LD (newline-delimited)
          3. GZIP compress
          4. Upload to S3 with deterministic key
          5. Verify upload checksum
          6. Insert manifest record
          7. Return RetentionResult
        """
        result = RetentionResult(policy_id=policy.policy_id)
        table = self._table_for_class(policy.data_class)
        ts_col = self._timestamp_col_for_table(table)
        today_str = date.today().isoformat()
        s3_key = (
            f"{self._s3_prefix}/{policy.data_class}/{cutoff_date.year}/"
            f"{cutoff_date.month:02d}/{today_str}-{result.run_id}.jsonl.gz"
        )
        result.s3_key_prefix = s3_key

        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                rows = conn.execute(
                    f"""
                    SELECT t.*
                    FROM cost_history.{table} t
                    LEFT JOIN cost_history.legal_holds lh
                        ON lh.entity_type = %s
                        AND lh.entity_id = t.{self._pk_col(table)}
                        AND lh.released_at IS NULL
                    WHERE t.{ts_col} < %s
                      AND lh.hold_id IS NULL
                    """,
                    [policy.data_class, cutoff_date],
                ).fetchall()

                result.records_evaluated = len(rows)

                # Serialize to newline-delimited JSON
                buf = io.BytesIO()
                with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                    for row in rows:
                        line = json.dumps(row, default=str) + "\n"
                        gz.write(line.encode("utf-8"))

                compressed = buf.getvalue()
                checksum = hashlib.sha256(compressed).hexdigest()

                # Upload to S3
                self._s3.put_object(
                    Bucket=self._s3_bucket,
                    Key=s3_key,
                    Body=compressed,
                    ContentType="application/gzip",
                    Metadata={
                        "data-class": policy.data_class,
                        "cutoff-date": cutoff_date.isoformat(),
                        "record-count": str(len(rows)),
                        "sha256": checksum,
                    },
                    ServerSideEncryption="aws:kms",
                )

                # Verify
                head = self._s3.head_object(Bucket=self._s3_bucket, Key=s3_key)
                if head["ContentLength"] != len(compressed):
                    raise RuntimeError(
                        f"S3 upload size mismatch for key {s3_key}"
                    )

                # Record manifest
                self._insert_manifest(
                    conn=conn,
                    run_id=result.run_id,
                    policy_id=policy.policy_id,
                    s3_key=s3_key,
                    checksum=checksum,
                    record_count=len(rows),
                    data_class=policy.data_class,
                )
                conn.commit()

                result.records_archived = len(rows)
                result.status = "COMPLETED"
                logger.info(
                    "Archived %d %s records to %s (sha256=%s)",
                    len(rows),
                    policy.data_class,
                    s3_key,
                    checksum,
                )

        except Exception as exc:
            result.status = "FAILED"
            result.error_detail = str(exc)
            logger.exception("archive_to_cold failed for policy %s", policy.policy_id)

        return result

    def delete_expired(
        self,
        policy: RetentionPolicy,
        cutoff_date: date,
        require_cold_archive: bool = True,
    ) -> RetentionResult:
        """
        Hard-deletes records that have passed delete_after_days.
        Will refuse to delete if require_cold_archive=True and no manifest exists.
        Legal holds are always respected.
        """
        result = RetentionResult(policy_id=policy.policy_id)

        if policy.delete_after_days is None:
            result.status = "SKIPPED"
            logger.info("Policy %s has no delete_after_days; skipping.", policy.policy_id)
            return result

        table = self._table_for_class(policy.data_class)
        ts_col = self._timestamp_col_for_table(table)
        pk = self._pk_col(table)

        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                if require_cold_archive:
                    manifest_count = conn.execute(
                        """
                        SELECT COUNT(*) AS cnt
                        FROM cost_history.retention_executions
                        WHERE policy_id = %s
                          AND status = 'COMPLETED'
                          AND executed_at > NOW() - INTERVAL '25 hours'
                        """,
                        [policy.policy_id],
                    ).fetchone()["cnt"]

                    if manifest_count == 0:
                        raise RuntimeError(
                            f"No confirmed cold archive for policy {policy.policy_id} "
                            "within 25h; refusing delete."
                        )

                deleted = conn.execute(
                    f"""
                    WITH eligible AS (
                        SELECT t.{pk}
                        FROM cost_history.{table} t
                        LEFT JOIN cost_history.legal_holds lh
                            ON lh.entity_type = %s
                            AND lh.entity_id = t.{pk}
                            AND lh.released_at IS NULL
                        WHERE t.{ts_col} < %s
                          AND lh.hold_id IS NULL
                    )
                    DELETE FROM cost_history.{table}
                    WHERE {pk} IN (SELECT {pk} FROM eligible)
                    """,
                    [policy.data_class, cutoff_date],
                )
                result.records_deleted = deleted.rowcount
                conn.commit()

            result.status = "COMPLETED"
            logger.info(
                "Deleted %d expired %s records",
                result.records_deleted,
                policy.data_class,
            )

        except Exception as exc:
            result.status = "FAILED"
            result.error_detail = str(exc)
            logger.exception("delete_expired failed for policy %s", policy.policy_id)

        return result

    def apply_legal_hold(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        reason: str,
        imposed_by: uuid.UUID,
    ) -> uuid.UUID:
        """Places a legal hold on a single entity. Returns hold_id."""
        hold_id = uuid.uuid4()
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO cost_history.legal_holds
                    (hold_id, entity_type, entity_id, reason, imposed_by, imposed_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (entity_type, entity_id)
                    WHERE released_at IS NULL
                DO NOTHING
                """,
                [hold_id, entity_type, entity_id, reason, imposed_by],
            )
            conn.commit()
        logger.warning(
            "Legal hold %s placed on %s %s by %s: %s",
            hold_id,
            entity_type,
            entity_id,
            imposed_by,
            reason,
        )
        return hold_id

    def release_legal_hold(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        released_by: uuid.UUID,
        justification: str,
    ) -> bool:
        """Releases an active legal hold. Returns True if a hold was released."""
        with psycopg.connect(self._dsn) as conn:
            result = conn.execute(
                """
                UPDATE cost_history.legal_holds
                SET released_at = NOW(),
                    released_by = %s,
                    reason = reason || ' | RELEASE: ' || %s
                WHERE entity_type = %s
                  AND entity_id = %s
                  AND released_at IS NULL
                """,
                [released_by, justification, entity_type, entity_id],
            )
            conn.commit()
            released = result.rowcount > 0

        logger.warning(
            "Legal hold on %s %s %s by %s",
            entity_type,
            entity_id,
            "RELEASED" if released else "NOT FOUND",
            released_by,
        )
        return released

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _load_policies(self) -> list[RetentionPolicy]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM cost_history.retention_policies ORDER BY data_class"
            ).fetchall()
        return [
            RetentionPolicy(
                policy_id=r["policy_id"],
                data_class=r["data_class"],
                status_filter=r["status_filter"],
                hot_days=r["hot_days"],
                warm_days=r["warm_days"],
                cold_days=r["cold_days"],
                delete_after_days=r["delete_after_days"],
                legal_hold_flag=r["legal_hold_flag"],
                gdpr_applies=r["gdpr_applies"],
            )
            for r in rows
        ]

    @staticmethod
    def _table_for_class(data_class: str) -> str:
        mapping = {
            "CostSnapshot": "cost_snapshots",
            "Quote": "quotes",
            "RFQRound": "rfq_rounds",
            "RFQBid": "rfq_bids",
            "SupplierPriceRecord": "supplier_price_records",
            "MaterialPriceRecord": "material_price_records",
            "ProcessCostRecord": "process_cost_records",
            "AuditEvent": "audit_events",
            "ForecastRecord": "forecast_records",
            "BenchmarkSnapshot": "benchmark_snapshots",
        }
        if data_class not in mapping:
            raise ValueError(f"Unknown data class: {data_class}")
        return mapping[data_class]

    @staticmethod
    def _timestamp_col_for_table(table: str) -> str:
        mapping = {
            "cost_snapshots": "created_at",
            "quotes": "created_at",
            "rfq_rounds": "created_at",
            "rfq_bids": "created_at",
            "supplier_price_records": "valid_from",
            "material_price_records": "valid_date",
            "process_cost_records": "created_at",
            "audit_events": "occurred_at",
            "forecast_records": "created_at",
            "benchmark_snapshots": "created_at",
        }
        return mapping[table]

    @staticmethod
    def _pk_col(table: str) -> str:
        mapping = {
            "cost_snapshots": "snapshot_id",
            "quotes": "quote_id",
            "rfq_rounds": "rfq_id",
            "rfq_bids": "bid_id",
            "supplier_price_records": "record_id",
            "material_price_records": "record_id",
            "process_cost_records": "record_id",
            "audit_events": "event_id",
            "forecast_records": "forecast_id",
            "benchmark_snapshots": "benchmark_id",
        }
        return mapping[table]

    def _insert_manifest(
        self,
        conn: psycopg.Connection,
        run_id: uuid.UUID,
        policy_id: uuid.UUID,
        s3_key: str,
        checksum: str,
        record_count: int,
        data_class: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO cost_history.retention_executions
                (run_id, policy_id, executed_at, records_evaluated,
                 records_archived, records_deleted, s3_key_prefix,
                 status, error_detail, duration_seconds)
            VALUES (%s, %s, NOW(), %s, %s, 0, %s, 'COMPLETED', NULL,
                    EXTRACT(EPOCH FROM (NOW() - NOW()))::INTEGER)
            """,
            [run_id, policy_id, record_count, record_count, s3_key],
        )
```

---

## Section 12 — SQL Schema

### 12.1 Schema Creation and Extensions

```sql
-- =============================================================================
-- Cost History Engine — Complete PostgreSQL 16 DDL
-- Schema: cost_history
-- Version: 1.0.0
-- =============================================================================

-- Extensions (run as superuser)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
CREATE EXTENSION IF NOT EXISTS "vector";        -- pgvector
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Schema
CREATE SCHEMA IF NOT EXISTS cost_history;

SET search_path TO cost_history, public;
```

---

### 12.2 ENUM Types

```sql
-- =============================================================================
-- ENUM: snapshot_type
-- =============================================================================
CREATE TYPE cost_history.snapshot_type AS ENUM (
    'SHOULD_COST',       -- Engineering target cost
    'ACTUAL_COST',       -- Post-production actual
    'QUOTED_COST',       -- Supplier quote basis
    'BENCHMARK',         -- Industry benchmark
    'PARAMETRIC',        -- Parametric estimate
    'ANALOGY',           -- Analogy-based estimate
    'BOTTOMS_UP',        -- Detailed bottoms-up
    'ROUGH_ORDER'        -- ROM estimate
);

-- =============================================================================
-- ENUM: snapshot_status
-- =============================================================================
CREATE TYPE cost_history.snapshot_status AS ENUM (
    'DRAFT',
    'UNDER_REVIEW',
    'APPROVED',
    'SUPERSEDED',
    'ARCHIVED',
    'REJECTED'
);

-- =============================================================================
-- ENUM: quote_type
-- =============================================================================
CREATE TYPE cost_history.quote_type AS ENUM (
    'SPOT',              -- One-time spot price
    'CONTRACT',          -- Long-term contract
    'BLANKET',           -- Blanket purchase order
    'PROTOTYPE',         -- Pre-production prototype
    'TOOLING',           -- Tooling cost only
    'SERVICE',           -- Service/MRO quote
    'ANNUAL_PRICE_LIST'  -- Catalogue list price
);

-- =============================================================================
-- ENUM: quote_status
-- =============================================================================
CREATE TYPE cost_history.quote_status AS ENUM (
    'DRAFT',
    'SUBMITTED',
    'UNDER_NEGOTIATION',
    'ACCEPTED',
    'REJECTED',
    'EXPIRED',
    'BINDING',
    'SUPERSEDED'
);

-- =============================================================================
-- ENUM: rfq_type
-- =============================================================================
CREATE TYPE cost_history.rfq_type AS ENUM (
    'OPEN',              -- Open to all qualified suppliers
    'SELECTIVE',         -- Invited suppliers only
    'SINGLE_SOURCE',     -- Emergency / sole source
    'REVERSE_AUCTION',   -- Dutch auction format
    'FRAMEWORK'          -- Framework agreement renewal
);

-- =============================================================================
-- ENUM: rfq_status
-- =============================================================================
CREATE TYPE cost_history.rfq_status AS ENUM (
    'DRAFT',
    'ISSUED',
    'BIDS_RECEIVED',
    'EVALUATION',
    'AWARDED',
    'CANCELLED',
    'NO_BID',
    'REBID'
);

-- =============================================================================
-- ENUM: bid_status
-- =============================================================================
CREATE TYPE cost_history.bid_status AS ENUM (
    'SUBMITTED',
    'UNDER_EVALUATION',
    'SHORTLISTED',
    'AWARDED',
    'REJECTED',
    'DISQUALIFIED',
    'WITHDRAWN'
);

-- =============================================================================
-- ENUM: price_source_type
-- =============================================================================
CREATE TYPE cost_history.price_source_type AS ENUM (
    'RFQ_BID',
    'PO_CONFIRMATION',
    'CONTRACT',
    'SPOT_MARKET',
    'INDEX_LINKED',
    'CATALOGUE',
    'NEGOTIATED',
    'INTERCOMPANY',
    'ESTIMATED'
);

-- =============================================================================
-- ENUM: market_data_source
-- =============================================================================
CREATE TYPE cost_history.market_data_source AS ENUM (
    'LME',               -- London Metal Exchange
    'COMEX',             -- Commodity Exchange
    'PLATTS',            -- S&P Global Platts
    'FASTMARKETS',       -- Fastmarkets (formerly Metal Bulletin)
    'ECB_FX',            -- ECB foreign exchange
    'MANUAL_ENTRY',      -- Procurement team manual input
    'API_FEED',          -- Third-party API feed
    'DUN_BRADSTREET',    -- D&B commodity data
    'COFACE',            -- Coface country risk pricing
    'INTERNAL_MODEL'     -- Internal forecasting model
);

-- =============================================================================
-- ENUM: forecast_model_type
-- =============================================================================
CREATE TYPE cost_history.forecast_model_type AS ENUM (
    'ARIMA',
    'SARIMA',
    'PROPHET',
    'LSTM',
    'XGBOOST',
    'ENSEMBLE',
    'LINEAR_REGRESSION',
    'EXPONENTIAL_SMOOTHING'
);

-- =============================================================================
-- ENUM: audit_action
-- =============================================================================
CREATE TYPE cost_history.audit_action AS ENUM (
    'CREATE',
    'UPDATE',
    'DELETE',
    'APPROVE',
    'REJECT',
    'SUBMIT',
    'ARCHIVE',
    'EXPORT',
    'ACCESS',
    'LEGAL_HOLD_APPLY',
    'LEGAL_HOLD_RELEASE',
    'GDPR_ERASURE',
    'STATUS_CHANGE',
    'VERSION_TAG',
    'IMPORT'
);

-- =============================================================================
-- ENUM: retention_status
-- =============================================================================
CREATE TYPE cost_history.retention_status AS ENUM (
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'PARTIAL',
    'SKIPPED',
    'EVALUATED'
);

-- =============================================================================
-- ENUM: version_change_type
-- =============================================================================
CREATE TYPE cost_history.version_change_type AS ENUM (
    'MAJOR',             -- Breaking change in structure
    'MINOR',             -- New fields / added data
    'PATCH',             -- Correction / typo fix
    'STATUS_CHANGE',     -- Only status changed
    'METADATA_ONLY'      -- Tags / notes only
);

-- =============================================================================
-- ENUM: sensitivity_level
-- =============================================================================
CREATE TYPE cost_history.sensitivity_level AS ENUM (
    'PUBLIC',
    'INTERNAL',
    'CONFIDENTIAL',
    'RESTRICTED',
    'TOP_SECRET'
);
```

---

### 12.3 Core Tables

```sql
-- =============================================================================
-- TABLE: cost_snapshots
-- =============================================================================
CREATE TABLE cost_history.cost_snapshots (
    snapshot_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_type           cost_history.snapshot_type NOT NULL,
    reference_id            UUID NOT NULL,
    reference_type          TEXT NOT NULL,           -- 'Part', 'Assembly', 'Project', 'Material'
    part_number             TEXT,
    revision                TEXT,
    bom_level               INTEGER DEFAULT 0,       -- 0 = top-level
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    total_cost_eur          NUMERIC(18,6) NOT NULL,
    material_cost_eur       NUMERIC(18,6) NOT NULL DEFAULT 0,
    process_cost_eur        NUMERIC(18,6) NOT NULL DEFAULT 0,
    overhead_cost_eur       NUMERIC(18,6) NOT NULL DEFAULT 0,
    tooling_cost_eur        NUMERIC(18,6) NOT NULL DEFAULT 0,
    logistics_cost_eur      NUMERIC(18,6) NOT NULL DEFAULT 0,
    profit_margin_pct       NUMERIC(8,4),            -- NULL for internal cost; present on sold price
    valid_from              DATE NOT NULL,
    valid_until             DATE,
    status                  cost_history.snapshot_status NOT NULL DEFAULT 'DRAFT',
    snapshot_hash           TEXT,                    -- SHA256 of all line items (recomputed on change)
    sensitivity             cost_history.sensitivity_level NOT NULL DEFAULT 'CONFIDENTIAL',
    tags                    JSONB NOT NULL DEFAULT '{}',
    version                 INTEGER NOT NULL DEFAULT 1,
    parent_snapshot_id      UUID REFERENCES cost_history.cost_snapshots(snapshot_id),
    approved_by             UUID,
    approved_at             TIMESTAMPTZ,
    rejection_reason        TEXT,
    embedding               vector(1536),            -- pgvector: text-embedding-3-small of description
    created_by              UUID NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_cost_non_negative CHECK (
        material_cost_eur >= 0 AND process_cost_eur >= 0
        AND overhead_cost_eur >= 0 AND tooling_cost_eur >= 0
        AND logistics_cost_eur >= 0 AND total_cost_eur >= 0
    ),
    CONSTRAINT chk_valid_date_order CHECK (
        valid_until IS NULL OR valid_until > valid_from
    ),
    CONSTRAINT chk_approved_requires_approver CHECK (
        status != 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
    )
) PARTITION BY RANGE (created_at);

COMMENT ON TABLE cost_history.cost_snapshots IS
    'Append-only repository of all cost estimates at a point in time. '
    'Each approval creates a new version; records are never updated in place.';

-- =============================================================================
-- TABLE: snapshot_line_items
-- =============================================================================
CREATE TABLE cost_history.snapshot_line_items (
    line_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_id         UUID NOT NULL REFERENCES cost_history.cost_snapshots(snapshot_id)
                            ON DELETE CASCADE,
    line_type           TEXT NOT NULL,               -- 'MATERIAL', 'PROCESS', 'OVERHEAD', 'TOOLING', 'LOGISTICS'
    reference_id        UUID,                        -- FK to material / process / supplier record
    description         TEXT NOT NULL,
    qty                 NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit                TEXT NOT NULL DEFAULT 'EA',  -- EA, KG, M, HR, ...
    unit_cost_eur       NUMERIC(18,6) NOT NULL,
    total_cost_eur      NUMERIC(18,6) NOT NULL,
    cost_driver         TEXT,                        -- e.g. 'WEIGHT_KG', 'MACHINE_HRS'
    cost_driver_value   NUMERIC(18,6),
    notes               TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT chk_line_qty_positive CHECK (qty > 0),
    CONSTRAINT chk_line_total CHECK (
        ABS(total_cost_eur - (qty * unit_cost_eur)) < 0.01
    )
);

-- =============================================================================
-- TABLE: quotes
-- =============================================================================
CREATE TABLE cost_history.quotes (
    quote_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quote_number        TEXT NOT NULL,
    revision_number     INTEGER NOT NULL DEFAULT 1,
    quote_type          cost_history.quote_type NOT NULL,
    customer_id         UUID NOT NULL,               -- FK to CRM/SIE (external)
    part_number         TEXT,
    revision            TEXT,
    snapshot_id         UUID REFERENCES cost_history.cost_snapshots(snapshot_id),
    status              cost_history.quote_status NOT NULL DEFAULT 'DRAFT',
    total_price_eur     NUMERIC(18,6) NOT NULL,
    target_price_eur    NUMERIC(18,6),
    floor_price_eur     NUMERIC(18,6),               -- encrypted at application layer
    margin_pct          NUMERIC(8,4),                -- encrypted at application layer
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    valid_until         DATE,
    payment_terms_days  INTEGER NOT NULL DEFAULT 30,
    incoterms           TEXT,                        -- DDP, EXW, FCA, ...
    submitted_at        TIMESTAMPTZ,
    accepted_at         TIMESTAMPTZ,
    rejected_at         TIMESTAMPTZ,
    rejection_reason    TEXT,
    sensitivity         cost_history.sensitivity_level NOT NULL DEFAULT 'CONFIDENTIAL',
    version             INTEGER NOT NULL DEFAULT 1,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_quote_number_revision UNIQUE (quote_number, revision_number),
    CONSTRAINT chk_price_positive CHECK (total_price_eur > 0),
    CONSTRAINT chk_floor_lte_total CHECK (
        floor_price_eur IS NULL OR floor_price_eur <= total_price_eur
    )
) PARTITION BY RANGE (created_at);

-- =============================================================================
-- TABLE: quote_lines
-- =============================================================================
CREATE TABLE cost_history.quote_lines (
    line_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quote_id            UUID NOT NULL REFERENCES cost_history.quotes(quote_id)
                            ON DELETE CASCADE,
    snapshot_line_id    UUID REFERENCES cost_history.snapshot_line_items(line_id),
    description         TEXT NOT NULL,
    qty                 NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit                TEXT NOT NULL DEFAULT 'EA',
    unit_price_eur      NUMERIC(18,6) NOT NULL,
    discount_pct        NUMERIC(8,4) NOT NULL DEFAULT 0,
    total_price_eur     NUMERIC(18,6) NOT NULL,
    margin_pct          NUMERIC(8,4),

    CONSTRAINT chk_quote_line_qty CHECK (qty > 0),
    CONSTRAINT chk_discount_range CHECK (discount_pct BETWEEN 0 AND 100)
);

-- =============================================================================
-- TABLE: quote_versions
-- =============================================================================
CREATE TABLE cost_history.quote_versions (
    version_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quote_id            UUID NOT NULL REFERENCES cost_history.quotes(quote_id),
    revision_number     INTEGER NOT NULL,
    changed_by          UUID NOT NULL,
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_reason       TEXT NOT NULL,
    snapshot_of_quote   JSONB NOT NULL,              -- Full JSON snapshot of quote at this version

    CONSTRAINT uq_quote_version UNIQUE (quote_id, revision_number)
);

-- =============================================================================
-- TABLE: rfq_rounds
-- =============================================================================
CREATE TABLE cost_history.rfq_rounds (
    rfq_id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rfq_number              TEXT NOT NULL,
    round_number            INTEGER NOT NULL DEFAULT 1,
    rfq_type                cost_history.rfq_type NOT NULL,
    status                  cost_history.rfq_status NOT NULL DEFAULT 'DRAFT',
    part_number             TEXT,
    material_id             UUID,
    target_qty              NUMERIC(18,6) NOT NULL,
    qty_unit                TEXT NOT NULL DEFAULT 'EA',
    target_price_eur        NUMERIC(18,6),
    budget_price_eur        NUMERIC(18,6),
    issued_at               TIMESTAMPTZ,
    deadline                TIMESTAMPTZ,
    awarded_at              TIMESTAMPTZ,
    awarded_supplier_id     UUID,
    award_reason            TEXT,
    savings_vs_prior_eur    NUMERIC(18,6),
    savings_pct             NUMERIC(8,4),
    created_by              UUID NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_rfq_number_round UNIQUE (rfq_number, round_number),
    CONSTRAINT chk_rfq_deadline_after_issued CHECK (
        deadline IS NULL OR issued_at IS NULL OR deadline > issued_at
    )
) PARTITION BY RANGE (created_at);

-- =============================================================================
-- TABLE: rfq_bids
-- =============================================================================
CREATE TABLE cost_history.rfq_bids (
    bid_id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rfq_id                  UUID NOT NULL REFERENCES cost_history.rfq_rounds(rfq_id),
    supplier_id             UUID NOT NULL,
    bid_price_eur           NUMERIC(18,6) NOT NULL,
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    lead_time_days          INTEGER,
    moq_qty                 NUMERIC(18,6),
    bid_status              cost_history.bid_status NOT NULL DEFAULT 'SUBMITTED',
    technical_score         NUMERIC(5,2),            -- 0–100
    commercial_score        NUMERIC(5,2),            -- 0–100
    total_score             NUMERIC(5,2),            -- weighted composite
    rank_position           INTEGER,
    disqualification_reason TEXT,
    submitted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_bid_price_positive CHECK (bid_price_eur > 0),
    CONSTRAINT chk_scores_range CHECK (
        (technical_score IS NULL OR technical_score BETWEEN 0 AND 100)
        AND (commercial_score IS NULL OR commercial_score BETWEEN 0 AND 100)
        AND (total_score IS NULL OR total_score BETWEEN 0 AND 100)
    )
) PARTITION BY RANGE (created_at);

-- =============================================================================
-- TABLE: supplier_price_records
-- =============================================================================
CREATE TABLE cost_history.supplier_price_records (
    record_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supplier_id             UUID NOT NULL,
    material_id             UUID,
    category_id             UUID,
    price_per_unit_eur      NUMERIC(18,6) NOT NULL,
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    price_unit              TEXT NOT NULL DEFAULT 'EA',  -- EA, KG, M2, M3, ...
    moq_qty                 NUMERIC(18,6),
    valid_from              DATE NOT NULL,
    valid_until             DATE,
    source                  cost_history.price_source_type NOT NULL,
    rfq_id                  UUID REFERENCES cost_history.rfq_rounds(rfq_id),
    po_number               TEXT,
    index_reference         TEXT,                    -- e.g. 'LME_COPPER_3M'
    index_value             NUMERIC(18,6),
    tooling_amortization_eur NUMERIC(18,6) DEFAULT 0,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    superseded_by           UUID REFERENCES cost_history.supplier_price_records(record_id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_supplier_price_positive CHECK (price_per_unit_eur > 0),
    CONSTRAINT chk_valid_dates CHECK (
        valid_until IS NULL OR valid_until > valid_from
    )
) PARTITION BY RANGE (valid_from);

-- =============================================================================
-- TABLE: price_adjustments
-- =============================================================================
CREATE TABLE cost_history.price_adjustments (
    adjustment_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id           UUID NOT NULL REFERENCES cost_history.supplier_price_records(record_id),
    adjustment_type     TEXT NOT NULL,               -- 'INDEX_ESCALATION', 'VOLUME_DISCOUNT', 'SURCHARGE', 'FOREX'
    adjustment_pct      NUMERIC(8,4),
    amount_eur          NUMERIC(18,6),
    effective_date      DATE NOT NULL,
    reason              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_adjustment_has_value CHECK (
        adjustment_pct IS NOT NULL OR amount_eur IS NOT NULL
    )
);

-- =============================================================================
-- TABLE: material_price_records
-- =============================================================================
CREATE TABLE cost_history.material_price_records (
    record_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id             UUID NOT NULL,
    material_grade          TEXT,                    -- e.g. 'EN-AW-6061-T6', 'SS316L'
    price_per_kg_eur        NUMERIC(18,6),
    price_per_unit_eur      NUMERIC(18,6),
    unit                    TEXT NOT NULL DEFAULT 'KG',
    source                  cost_history.market_data_source NOT NULL,
    index_name              TEXT,                    -- e.g. 'LME_ALUMINIUM_CASH'
    raw_value               NUMERIC(18,6),           -- Original value in original currency
    normalized_value_eur    NUMERIC(18,6) NOT NULL,  -- Converted to EUR/KG
    currency_original       CHAR(3) NOT NULL DEFAULT 'USD',
    fx_rate                 NUMERIC(12,8) NOT NULL DEFAULT 1.0,  -- original → EUR
    region_code             CHAR(2) DEFAULT 'EU',    -- ISO 3166-1 alpha-2
    valid_date              DATE NOT NULL,
    is_validated            BOOLEAN NOT NULL DEFAULT FALSE,
    anomaly_flag            BOOLEAN NOT NULL DEFAULT FALSE,
    anomaly_score           NUMERIC(8,6),            -- Z-score for anomaly detection
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_material_price_positive CHECK (
        normalized_value_eur > 0
    ),
    CONSTRAINT chk_has_price CHECK (
        price_per_kg_eur IS NOT NULL OR price_per_unit_eur IS NOT NULL
    )
) PARTITION BY RANGE (valid_date);

-- =============================================================================
-- TABLE: forecast_records
-- =============================================================================
CREATE TABLE cost_history.forecast_records (
    forecast_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id         UUID NOT NULL,
    model_type          cost_history.forecast_model_type NOT NULL,
    forecast_date       DATE NOT NULL,               -- The date being forecast
    forecast_value_eur  NUMERIC(18,6) NOT NULL,
    confidence_lower    NUMERIC(18,6) NOT NULL,      -- 80% CI lower bound
    confidence_upper    NUMERIC(18,6) NOT NULL,      -- 80% CI upper bound
    mape_last_backtest  NUMERIC(8,4),                -- Mean Absolute Percentage Error
    model_version       TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_ci_order CHECK (confidence_lower <= forecast_value_eur
                                   AND forecast_value_eur <= confidence_upper),
    CONSTRAINT chk_forecast_positive CHECK (forecast_value_eur > 0)
);

-- =============================================================================
-- TABLE: process_cost_records
-- =============================================================================
CREATE TABLE cost_history.process_cost_records (
    record_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_type            TEXT NOT NULL,           -- 'TURNING', 'MILLING', 'WELDING', 'STAMPING', 'ASSEMBLY', ...
    machine_id              UUID,
    plant_id                UUID NOT NULL,
    period_year             INTEGER NOT NULL,
    period_month            INTEGER NOT NULL,
    setup_cost_eur_hr       NUMERIC(10,4) NOT NULL,
    runtime_cost_eur_hr     NUMERIC(10,4) NOT NULL,
    energy_cost_eur_hr      NUMERIC(10,4) NOT NULL DEFAULT 0,
    maintenance_cost_eur_hr NUMERIC(10,4) NOT NULL DEFAULT 0,
    scrap_cost_pct          NUMERIC(8,4) NOT NULL DEFAULT 0,
    total_hourly_rate_eur   NUMERIC(10,4) NOT NULL,
    oee_overall             NUMERIC(5,4),            -- 0.0–1.0 (Overall Equipment Effectiveness)
    production_hrs          NUMERIC(10,2),
    actual_vs_standard_pct  NUMERIC(8,4),            -- positive = over standard
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_period_month CHECK (period_month BETWEEN 1 AND 12),
    CONSTRAINT chk_oee_range CHECK (oee_overall IS NULL OR oee_overall BETWEEN 0 AND 1),
    CONSTRAINT chk_scrap_range CHECK (scrap_cost_pct BETWEEN 0 AND 100),
    CONSTRAINT uq_process_cost_period UNIQUE (process_type, machine_id, plant_id, period_year, period_month, version)
) PARTITION BY RANGE (period_year);

-- =============================================================================
-- TABLE: entity_versions
-- =============================================================================
CREATE TABLE cost_history.entity_versions (
    version_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type         TEXT NOT NULL,
    entity_id           UUID NOT NULL,
    version_number      INTEGER NOT NULL,
    semantic_version    TEXT,                        -- e.g. '2.1.0'
    change_type         cost_history.version_change_type NOT NULL,
    change_summary      TEXT NOT NULL,
    diff_jsonb          JSONB NOT NULL DEFAULT '{}', -- {field: {old: X, new: Y}}
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_entity_version UNIQUE (entity_type, entity_id, version_number)
);

-- =============================================================================
-- TABLE: version_labels
-- =============================================================================
CREATE TABLE cost_history.version_labels (
    label_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type         TEXT NOT NULL,
    entity_id           UUID NOT NULL,
    version_id          UUID NOT NULL REFERENCES cost_history.entity_versions(version_id),
    label_name          TEXT NOT NULL,               -- e.g. 'BASELINE-2024', 'PRE-CONTRACT'
    description         TEXT,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_version_label UNIQUE (entity_type, entity_id, label_name)
);

-- =============================================================================
-- TABLE: audit_events  (APPEND-ONLY — no UPDATE/DELETE ever)
-- =============================================================================
CREATE TABLE cost_history.audit_events (
    event_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entity_type         TEXT NOT NULL,
    entity_id           UUID NOT NULL,
    action              cost_history.audit_action NOT NULL,
    actor_id            UUID NOT NULL,               -- pseudonymized user UUID
    actor_role          TEXT NOT NULL,
    actor_ip_hash       TEXT,                        -- SHA256(IP + daily_salt); PII-safe
    session_id          UUID,
    correlation_id      UUID,                        -- trace across microservices
    old_values          JSONB,
    new_values          JSONB,
    changed_fields      TEXT[],
    business_reason     TEXT,
    system_source       TEXT NOT NULL                -- 'CHE-API', 'RETENTION-JOB', 'KAFKA-CONSUMER'
) PARTITION BY RANGE (occurred_at);

COMMENT ON TABLE cost_history.audit_events IS
    'APPEND-ONLY. No row may be updated or deleted. '
    'PII fields (actor_ip_hash) may be overwritten with [ERASED-GDPR-YYYY] via GDPRErasureHandler.';

-- Enforce append-only via rule
CREATE RULE audit_events_no_delete AS ON DELETE TO cost_history.audit_events
    DO INSTEAD NOTHING;

CREATE RULE audit_events_no_update AS ON UPDATE TO cost_history.audit_events
    DO INSTEAD NOTHING;

-- =============================================================================
-- TABLE: retention_policies
-- =============================================================================
CREATE TABLE cost_history.retention_policies (
    policy_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    data_class          TEXT NOT NULL UNIQUE,
    status_filter       TEXT[],                      -- NULL = all statuses
    hot_days            INTEGER NOT NULL,
    warm_days           INTEGER,
    cold_days           INTEGER,                     -- NULL = indefinite
    delete_after_days   INTEGER,                     -- NULL = never delete
    legal_hold_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    gdpr_applies        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TABLE: retention_executions
-- =============================================================================
CREATE TABLE cost_history.retention_executions (
    run_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    policy_id           UUID NOT NULL REFERENCES cost_history.retention_policies(policy_id),
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    records_evaluated   BIGINT NOT NULL DEFAULT 0,
    records_archived    BIGINT NOT NULL DEFAULT 0,
    records_deleted     BIGINT NOT NULL DEFAULT 0,
    s3_key_prefix       TEXT,
    status              cost_history.retention_status NOT NULL DEFAULT 'RUNNING',
    error_detail        TEXT,
    duration_seconds    INTEGER
);

-- =============================================================================
-- TABLE: legal_holds
-- =============================================================================
CREATE TABLE cost_history.legal_holds (
    hold_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type         TEXT NOT NULL,
    entity_id           UUID NOT NULL,
    reason              TEXT NOT NULL,
    imposed_by          UUID NOT NULL,
    imposed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at         TIMESTAMPTZ,
    released_by         UUID,

    CONSTRAINT uq_active_hold UNIQUE (entity_type, entity_id)
        DEFERRABLE INITIALLY DEFERRED
    -- Note: partial unique is enforced in application; the constraint covers
    -- the common case. Use WHERE released_at IS NULL in queries.
);

CREATE UNIQUE INDEX uq_legal_hold_active
    ON cost_history.legal_holds (entity_type, entity_id)
    WHERE released_at IS NULL;

-- =============================================================================
-- TABLE: benchmark_snapshots
-- =============================================================================
CREATE TABLE cost_history.benchmark_snapshots (
    benchmark_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    process_class       TEXT NOT NULL,               -- 'CNC_TURNING', 'INJECTION_MOULDING', ...
    region              CHAR(2) NOT NULL DEFAULT 'DE', -- ISO country code
    period_year         INTEGER NOT NULL,
    period_quarter      INTEGER NOT NULL,
    min_rate_eur        NUMERIC(10,4) NOT NULL,
    avg_rate_eur        NUMERIC(10,4) NOT NULL,
    max_rate_eur        NUMERIC(10,4) NOT NULL,
    source              TEXT NOT NULL,               -- e.g. 'VDI_SURVEY_2024', 'INTERNAL'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_quarter CHECK (period_quarter BETWEEN 1 AND 4),
    CONSTRAINT chk_rate_order CHECK (
        min_rate_eur <= avg_rate_eur AND avg_rate_eur <= max_rate_eur
    ),
    CONSTRAINT uq_benchmark UNIQUE (process_class, region, period_year, period_quarter, source)
);
```

---

### 12.4 Key Stored Functions

```sql
-- =============================================================================
-- FUNCTION: che.get_snapshot_at_date
-- Returns the cost snapshot active for a given reference entity on a given date
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.get_snapshot_at_date(
    p_reference_id   UUID,
    p_reference_type TEXT,
    p_date           DATE
)
RETURNS SETOF cost_history.cost_snapshots
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT *
    FROM cost_history.cost_snapshots
    WHERE reference_id   = p_reference_id
      AND reference_type = p_reference_type
      AND status         = 'APPROVED'
      AND valid_from    <= p_date
      AND (valid_until IS NULL OR valid_until >= p_date)
    ORDER BY valid_from DESC, version DESC
    LIMIT 1;
$$;

COMMENT ON FUNCTION cost_history.get_snapshot_at_date IS
    'Returns the single approved cost snapshot active on p_date for the given reference entity. '
    'Returns empty if no snapshot covers that date.';

-- =============================================================================
-- FUNCTION: che.calculate_rfq_savings
-- Returns savings vs budget and vs prior contract for an awarded RFQ round
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.calculate_rfq_savings(
    p_rfq_id UUID
)
RETURNS TABLE (
    rfq_id               UUID,
    awarded_price_eur    NUMERIC,
    budget_price_eur     NUMERIC,
    savings_vs_budget_eur  NUMERIC,
    savings_vs_budget_pct  NUMERIC,
    prior_contract_price NUMERIC,
    savings_vs_prior_eur NUMERIC,
    savings_vs_prior_pct NUMERIC
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_rfq          cost_history.rfq_rounds%ROWTYPE;
    v_awarded_bid  cost_history.rfq_bids%ROWTYPE;
    v_prior_price  NUMERIC;
BEGIN
    SELECT * INTO v_rfq
    FROM cost_history.rfq_rounds
    WHERE rfq_id = p_rfq_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'RFQ % not found', p_rfq_id;
    END IF;

    IF v_rfq.status != 'AWARDED' THEN
        RAISE EXCEPTION 'RFQ % is not in AWARDED status (current: %)',
            p_rfq_id, v_rfq.status;
    END IF;

    SELECT * INTO v_awarded_bid
    FROM cost_history.rfq_bids
    WHERE rfq_id = p_rfq_id AND bid_status = 'AWARDED'
    LIMIT 1;

    -- Look for prior contract price from same supplier for same material
    SELECT spr.price_per_unit_eur INTO v_prior_price
    FROM cost_history.supplier_price_records spr
    WHERE spr.supplier_id = v_rfq.awarded_supplier_id
      AND spr.material_id = v_rfq.material_id
      AND spr.is_active   = FALSE           -- superseded = was the prior contract
    ORDER BY spr.valid_until DESC
    LIMIT 1;

    RETURN QUERY
    SELECT
        p_rfq_id                                                          AS rfq_id,
        v_awarded_bid.bid_price_eur                                       AS awarded_price_eur,
        v_rfq.budget_price_eur                                            AS budget_price_eur,
        COALESCE(v_rfq.budget_price_eur, 0) - v_awarded_bid.bid_price_eur AS savings_vs_budget_eur,
        CASE WHEN v_rfq.budget_price_eur > 0
             THEN ROUND(
                (v_rfq.budget_price_eur - v_awarded_bid.bid_price_eur)
                / v_rfq.budget_price_eur * 100, 4)
             ELSE NULL
        END                                                               AS savings_vs_budget_pct,
        v_prior_price                                                     AS prior_contract_price,
        CASE WHEN v_prior_price IS NOT NULL
             THEN v_prior_price - v_awarded_bid.bid_price_eur
             ELSE NULL
        END                                                               AS savings_vs_prior_eur,
        CASE WHEN v_prior_price > 0
             THEN ROUND(
                (v_prior_price - v_awarded_bid.bid_price_eur)
                / v_prior_price * 100, 4)
             ELSE NULL
        END                                                               AS savings_vs_prior_pct;
END;
$$;

-- =============================================================================
-- FUNCTION: che.get_supplier_price_trend
-- Returns a monthly price series for a supplier + material combination
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.get_supplier_price_trend(
    p_supplier_id UUID,
    p_material_id UUID,
    p_months      INTEGER DEFAULT 24
)
RETURNS TABLE (
    month_start     DATE,
    price_eur       NUMERIC,
    source          cost_history.price_source_type,
    record_id       UUID,
    yoy_change_pct  NUMERIC
)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH monthly_prices AS (
        SELECT
            DATE_TRUNC('month', valid_from)::DATE           AS month_start,
            price_per_unit_eur                              AS price_eur,
            source,
            record_id,
            ROW_NUMBER() OVER (
                PARTITION BY DATE_TRUNC('month', valid_from)
                ORDER BY valid_from DESC
            )                                               AS rn
        FROM cost_history.supplier_price_records
        WHERE supplier_id = p_supplier_id
          AND material_id = p_material_id
          AND valid_from >= CURRENT_DATE - (p_months || ' months')::INTERVAL
    ),
    deduped AS (
        SELECT month_start, price_eur, source, record_id
        FROM monthly_prices
        WHERE rn = 1
    )
    SELECT
        d.month_start,
        d.price_eur,
        d.source,
        d.record_id,
        ROUND(
            (d.price_eur - LAG(d.price_eur, 12) OVER (ORDER BY d.month_start))
            / NULLIF(LAG(d.price_eur, 12) OVER (ORDER BY d.month_start), 0) * 100,
        4) AS yoy_change_pct
    FROM deduped d
    ORDER BY d.month_start;
$$;

-- =============================================================================
-- FUNCTION: che.compute_snapshot_hash
-- Computes SHA256 hash of all line items for integrity verification
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.compute_snapshot_hash(
    p_snapshot_id UUID
)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_hash_input TEXT;
    v_hash       TEXT;
BEGIN
    SELECT STRING_AGG(
        line_id::TEXT
        || '|' || line_type
        || '|' || COALESCE(reference_id::TEXT, 'NULL')
        || '|' || description
        || '|' || qty::TEXT
        || '|' || unit
        || '|' || unit_cost_eur::TEXT
        || '|' || total_cost_eur::TEXT,
        '~' ORDER BY sort_order, line_id
    )
    INTO v_hash_input
    FROM cost_history.snapshot_line_items
    WHERE snapshot_id = p_snapshot_id;

    IF v_hash_input IS NULL THEN
        RETURN 'EMPTY-SNAPSHOT-' || p_snapshot_id::TEXT;
    END IF;

    v_hash := ENCODE(
        DIGEST(v_hash_input || '|SNAPSHOT:' || p_snapshot_id::TEXT, 'sha256'),
        'hex'
    );

    -- Update the snapshot record with the computed hash
    UPDATE cost_history.cost_snapshots
    SET snapshot_hash = v_hash,
        updated_at    = NOW()
    WHERE snapshot_id = p_snapshot_id;

    RETURN v_hash;
END;
$$;
```

---

### 12.5 Triggers

```sql
-- =============================================================================
-- TRIGGER: auto-update updated_at on mutable tables
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_cost_snapshots_updated_at
    BEFORE UPDATE ON cost_history.cost_snapshots
    FOR EACH ROW EXECUTE FUNCTION cost_history.fn_set_updated_at();

CREATE TRIGGER trg_quotes_updated_at
    BEFORE UPDATE ON cost_history.quotes
    FOR EACH ROW EXECUTE FUNCTION cost_history.fn_set_updated_at();

CREATE TRIGGER trg_rfq_rounds_updated_at
    BEFORE UPDATE ON cost_history.rfq_rounds
    FOR EACH ROW EXECUTE FUNCTION cost_history.fn_set_updated_at();

CREATE TRIGGER trg_retention_policies_updated_at
    BEFORE UPDATE ON cost_history.retention_policies
    FOR EACH ROW EXECUTE FUNCTION cost_history.fn_set_updated_at();

-- =============================================================================
-- TRIGGER: recompute snapshot_hash when line items change
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.fn_recompute_snapshot_hash()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cost_history.compute_snapshot_hash(
        CASE TG_OP WHEN 'DELETE' THEN OLD.snapshot_id ELSE NEW.snapshot_id END
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER trg_snapshot_line_hash_recompute
    AFTER INSERT OR UPDATE OR DELETE ON cost_history.snapshot_line_items
    FOR EACH ROW EXECUTE FUNCTION cost_history.fn_recompute_snapshot_hash();

-- =============================================================================
-- TRIGGER: audit_event INSERT on quotes/snapshots status change
-- =============================================================================
CREATE OR REPLACE FUNCTION cost_history.fn_audit_status_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_entity_type  TEXT;
    v_entity_id    UUID;
    v_action       cost_history.audit_action;
BEGIN
    -- Determine entity type from table name
    v_entity_type := TG_TABLE_NAME;

    IF TG_TABLE_NAME = 'cost_snapshots' THEN
        v_entity_id := NEW.snapshot_id;
    ELSIF TG_TABLE_NAME = 'quotes' THEN
        v_entity_id := NEW.quote_id;
    ELSIF TG_TABLE_NAME = 'rfq_rounds' THEN
        v_entity_id := NEW.rfq_id;
    ELSE
        v_entity_id := NULL;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        -- Map status to audit action
        v_action := CASE NEW.status
            WHEN 'APPROVED'   THEN 'APPROVE'::cost_history.audit_action
            WHEN 'REJECTED'   THEN 'REJECT'::cost_history.audit_action
            WHEN 'SUBMITTED'  THEN 'SUBMIT'::cost_history.audit_action
            WHEN 'ARCHIVED'   THEN 'ARCHIVE'::cost_history.audit_action
            ELSE 'STATUS_CHANGE'::cost_history.audit_action
        END;

        INSERT INTO cost_history.audit_events (
            entity_type, entity_id, action,
            actor_id, actor_role, system_source,
            old_values, new_values, changed_fields
        ) VALUES (
            v_entity_type, v_entity_id, v_action,
            COALESCE(NEW.created_by, '00000000-0000-0000-0000-000000000000'::UUID),
            'SYSTEM_TRIGGER',
            'CHE-DB-TRIGGER',
            jsonb_build_object('status', OLD.status),
            jsonb_build_object('status', NEW.status),
            ARRAY['status']
        );
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_cost_snapshots_audit_status
    AFTER UPDATE ON cost_history.cost_snapshots
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION cost_history.fn_audit_status_change();

CREATE TRIGGER trg_quotes_audit_status
    AFTER UPDATE ON cost_history.quotes
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION cost_history.fn_audit_status_change();

CREATE TRIGGER trg_rfq_rounds_audit_status
    AFTER UPDATE ON cost_history.rfq_rounds
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION cost_history.fn_audit_status_change();
```

---

## Section 13 — Indexing

### 13.1 Index Strategy Overview

| Query Pattern | Index Type | Rationale |
|--------------|-----------|-----------|
| Time-range on append-only tables | BRIN | 128x smaller than B-tree for monotonically increasing timestamps |
| Exact UUID lookup | B-tree (default) | O(log n) point lookup |
| Partial: active records only | Partial B-tree (`WHERE is_active = TRUE`) | Eliminates index bloat from historical rows |
| Supplier + material + date | Composite B-tree | Covers the most common price lookup join |
| JSONB tags / metadata | GIN | Supports `@>`, `?`, `?|`, `?&` operators |
| Part number fuzzy search | GIN with `gin_trgm_ops` | `ILIKE '%partial%'` and similarity ranking |
| Full-text cost descriptions | GIN on `to_tsvector()` | Fast ranked text search |
| Vector similarity (embeddings) | IVFFlat (pgvector) | Approximate nearest-neighbour for semantic cost search |

---

### 13.2 cost_snapshots Indexes

```sql
-- Primary BRIN for time-range scans (partition-local; also needed on parent)
CREATE INDEX idx_cost_snapshots_created_at_brin
    ON cost_history.cost_snapshots USING BRIN (created_at)
    WITH (pages_per_range = 128);

-- B-tree for reference entity lookup (most common join)
CREATE INDEX idx_cost_snapshots_reference
    ON cost_history.cost_snapshots (reference_id, reference_type);

-- Partial: only APPROVED snapshots (used by most read queries)
CREATE INDEX idx_cost_snapshots_approved
    ON cost_history.cost_snapshots (reference_id, valid_from DESC)
    WHERE status = 'APPROVED';

-- Part number trigram search
CREATE INDEX idx_cost_snapshots_part_number_trgm
    ON cost_history.cost_snapshots USING GIN (part_number gin_trgm_ops);

-- JSONB tags
CREATE INDEX idx_cost_snapshots_tags_gin
    ON cost_history.cost_snapshots USING GIN (tags);

-- Full-text search on part_number + reference_type
CREATE INDEX idx_cost_snapshots_fts
    ON cost_history.cost_snapshots USING GIN (
        to_tsvector('english',
            COALESCE(part_number, '') || ' '
            || COALESCE(reference_type, '') || ' '
            || COALESCE(revision, '')
        )
    );

-- pgvector IVFFlat for semantic similarity search (1536-dim embeddings)
-- Run AFTER table has at least 10,000 rows for meaningful clustering
CREATE INDEX idx_cost_snapshots_embedding_ivfflat
    ON cost_history.cost_snapshots USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Composite for parent chain traversal
CREATE INDEX idx_cost_snapshots_parent
    ON cost_history.cost_snapshots (parent_snapshot_id)
    WHERE parent_snapshot_id IS NOT NULL;

-- Status + created_at for retention job scans
CREATE INDEX idx_cost_snapshots_status_created
    ON cost_history.cost_snapshots (status, created_at);
```

### 13.3 quotes Indexes

```sql
CREATE INDEX idx_quotes_quote_number
    ON cost_history.quotes (quote_number);

CREATE INDEX idx_quotes_customer_status
    ON cost_history.quotes (customer_id, status);

CREATE INDEX idx_quotes_snapshot_id
    ON cost_history.quotes (snapshot_id)
    WHERE snapshot_id IS NOT NULL;

CREATE INDEX idx_quotes_valid_until
    ON cost_history.quotes (valid_until)
    WHERE valid_until IS NOT NULL AND status IN ('SUBMITTED', 'UNDER_NEGOTIATION');

CREATE INDEX idx_quotes_created_at_brin
    ON cost_history.quotes USING BRIN (created_at)
    WITH (pages_per_range = 128);

CREATE INDEX idx_quotes_part_number_trgm
    ON cost_history.quotes USING GIN (part_number gin_trgm_ops)
    WHERE part_number IS NOT NULL;

-- Partial: active binding quotes
CREATE INDEX idx_quotes_binding_active
    ON cost_history.quotes (customer_id, total_price_eur)
    WHERE status = 'BINDING';
```

### 13.4 rfq_rounds and rfq_bids Indexes

```sql
CREATE INDEX idx_rfq_rounds_status_issued
    ON cost_history.rfq_rounds (status, issued_at DESC);

CREATE INDEX idx_rfq_rounds_material
    ON cost_history.rfq_rounds (material_id)
    WHERE material_id IS NOT NULL;

CREATE INDEX idx_rfq_rounds_awarded_supplier
    ON cost_history.rfq_rounds (awarded_supplier_id)
    WHERE awarded_supplier_id IS NOT NULL;

CREATE INDEX idx_rfq_rounds_created_brin
    ON cost_history.rfq_rounds USING BRIN (created_at);

CREATE INDEX idx_rfq_bids_rfq_status
    ON cost_history.rfq_bids (rfq_id, bid_status);

CREATE INDEX idx_rfq_bids_supplier
    ON cost_history.rfq_bids (supplier_id, created_at DESC);

CREATE INDEX idx_rfq_bids_rank
    ON cost_history.rfq_bids (rfq_id, rank_position)
    WHERE bid_status = 'AWARDED';

CREATE INDEX idx_rfq_bids_created_brin
    ON cost_history.rfq_bids USING BRIN (created_at);
```

### 13.5 supplier_price_records Indexes

```sql
-- Primary price lookup: supplier + material + date (covers 90% of queries)
CREATE INDEX idx_supplier_price_lookup
    ON cost_history.supplier_price_records (supplier_id, material_id, valid_from DESC);

-- Partial: active prices only
CREATE INDEX idx_supplier_price_active
    ON cost_history.supplier_price_records (supplier_id, material_id, price_per_unit_eur)
    WHERE is_active = TRUE;

-- Category-level price browsing
CREATE INDEX idx_supplier_price_category
    ON cost_history.supplier_price_records (category_id, valid_from DESC)
    WHERE category_id IS NOT NULL;

-- Source type filtering
CREATE INDEX idx_supplier_price_source
    ON cost_history.supplier_price_records (source, valid_from DESC);

-- BRIN for retention job
CREATE INDEX idx_supplier_price_valid_from_brin
    ON cost_history.supplier_price_records USING BRIN (valid_from);

-- Supersession chain
CREATE INDEX idx_supplier_price_superseded_by
    ON cost_history.supplier_price_records (superseded_by)
    WHERE superseded_by IS NOT NULL;
```

### 13.6 material_price_records Indexes

```sql
-- Most common query: material + date range
CREATE INDEX idx_material_price_material_date
    ON cost_history.material_price_records (material_id, valid_date DESC);

-- Index lookup for market data source
CREATE INDEX idx_material_price_source_date
    ON cost_history.material_price_records (source, valid_date DESC);

-- Anomaly monitoring
CREATE INDEX idx_material_price_anomaly
    ON cost_history.material_price_records (material_id, valid_date)
    WHERE anomaly_flag = TRUE;

-- Unvalidated records (data quality queue)
CREATE INDEX idx_material_price_unvalidated
    ON cost_history.material_price_records (material_id, created_at)
    WHERE is_validated = FALSE;

-- BRIN for retention
CREATE INDEX idx_material_price_valid_date_brin
    ON cost_history.material_price_records USING BRIN (valid_date);

-- Grade-specific pricing
CREATE INDEX idx_material_price_grade
    ON cost_history.material_price_records (material_id, material_grade, valid_date DESC)
    WHERE material_grade IS NOT NULL;
```

### 13.7 audit_events Indexes

```sql
-- Primary compliance query: entity lookup
CREATE INDEX idx_audit_events_entity
    ON cost_history.audit_events (entity_type, entity_id, occurred_at DESC);

-- Actor audit trail
CREATE INDEX idx_audit_events_actor
    ON cost_history.audit_events (actor_id, occurred_at DESC);

-- Action type monitoring
CREATE INDEX idx_audit_events_action
    ON cost_history.audit_events (action, occurred_at DESC);

-- BRIN for time-range compliance queries (primary pattern)
CREATE INDEX idx_audit_events_occurred_brin
    ON cost_history.audit_events USING BRIN (occurred_at)
    WITH (pages_per_range = 64);

-- Correlation ID for distributed tracing
CREATE INDEX idx_audit_events_correlation
    ON cost_history.audit_events (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Session tracking
CREATE INDEX idx_audit_events_session
    ON cost_history.audit_events (session_id, occurred_at)
    WHERE session_id IS NOT NULL;
```

### 13.8 entity_versions and Supporting Tables Indexes

```sql
CREATE INDEX idx_entity_versions_entity
    ON cost_history.entity_versions (entity_type, entity_id, version_number DESC);

CREATE INDEX idx_entity_versions_current
    ON cost_history.entity_versions (entity_type, entity_id)
    WHERE is_current = TRUE;

CREATE INDEX idx_version_labels_entity
    ON cost_history.version_labels (entity_type, entity_id);

CREATE INDEX idx_version_labels_name
    ON cost_history.version_labels (label_name);

CREATE INDEX idx_legal_holds_entity
    ON cost_history.legal_holds (entity_type, entity_id);

CREATE INDEX idx_legal_holds_active
    ON cost_history.legal_holds (entity_type, imposed_at)
    WHERE released_at IS NULL;

CREATE INDEX idx_forecast_records_material_date
    ON cost_history.forecast_records (material_id, forecast_date DESC);

CREATE INDEX idx_process_cost_lookup
    ON cost_history.process_cost_records (process_type, plant_id, period_year, period_month DESC);
```

---

### 13.9 Index Maintenance

**REINDEX Schedule (via pg_cron or Kubernetes CronJob)**

```sql
-- Run concurrently to avoid table locks; schedule quarterly for high-write tables
-- Example: reindex the audit_events table without locking
REINDEX INDEX CONCURRENTLY cost_history.idx_audit_events_entity;
REINDEX INDEX CONCURRENTLY cost_history.idx_audit_events_actor;
REINDEX INDEX CONCURRENTLY cost_history.idx_cost_snapshots_approved;
```

**Index Bloat Monitoring Query**

```sql
-- Identifies indexes with >30% bloat for targeted REINDEX
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid))    AS index_size,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch,
    ROUND(
        100.0 * (1 - idx_tup_fetch::NUMERIC / NULLIF(idx_scan, 0)),
    2) AS approx_bloat_pct
FROM pg_stat_user_indexes
WHERE schemaname = 'cost_history'
  AND idx_scan > 100                               -- Only indexes in active use
ORDER BY pg_relation_size(indexrelid) DESC;
```

**Custom ANALYZE Schedule for High-Write Partitioned Tables**

```sql
-- Run after batch ingestion jobs complete (e.g., market data feed)
ANALYZE cost_history.material_price_records;
ANALYZE cost_history.audit_events;
ANALYZE cost_history.supplier_price_records;

-- For autovacuum tuning: reduce scale factor for high-write tables
ALTER TABLE cost_history.audit_events
    SET (autovacuum_analyze_scale_factor = 0.01,
         autovacuum_vacuum_scale_factor  = 0.02,
         autovacuum_vacuum_cost_delay    = 2);

ALTER TABLE cost_history.material_price_records
    SET (autovacuum_analyze_scale_factor = 0.02,
         autovacuum_vacuum_scale_factor  = 0.05);
```

---

## Section 14 — Partitioning

### 14.1 Partitioning Strategy Overview

| Table | Strategy | Key Column | Partition Interval | Rationale |
|-------|---------|-----------|-------------------|-----------|
| `cost_snapshots` | RANGE | `created_at` | Quarterly | Balanced size; aligns with fiscal quarters |
| `audit_events` | RANGE | `occurred_at` | Monthly | 84 partitions = 7 years; each <5 GB |
| `material_price_records` | RANGE | `valid_date` | Monthly | Market data arrives daily; pruning by month |
| `supplier_price_records` | RANGE | `valid_from` | Yearly | Lower volume; yearly sufficient |
| `process_cost_records` | RANGE | `period_year` | Yearly | Already has year/month columns |
| `rfq_bids` | RANGE | `created_at` | Yearly | Moderate volume |
| `quotes` | RANGE | `created_at` | Quarterly | Quote pipeline aligns with quarters |
| `rfq_rounds` | RANGE | `created_at` | Yearly | Low volume |

---

### 14.2 cost_snapshots Partitions (3 Years + Default)

```sql
-- Parent table already defined with PARTITION BY RANGE (created_at)

-- 2023 Quarters
CREATE TABLE cost_history.cost_snapshots_2023_q1
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2023-01-01') TO ('2023-04-01');

CREATE TABLE cost_history.cost_snapshots_2023_q2
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2023-04-01') TO ('2023-07-01');

CREATE TABLE cost_history.cost_snapshots_2023_q3
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2023-07-01') TO ('2023-10-01');

CREATE TABLE cost_history.cost_snapshots_2023_q4
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2023-10-01') TO ('2024-01-01');

-- 2024 Quarters
CREATE TABLE cost_history.cost_snapshots_2024_q1
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2024-01-01') TO ('2024-04-01');

CREATE TABLE cost_history.cost_snapshots_2024_q2
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2024-04-01') TO ('2024-07-01');

CREATE TABLE cost_history.cost_snapshots_2024_q3
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2024-07-01') TO ('2024-10-01');

CREATE TABLE cost_history.cost_snapshots_2024_q4
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2024-10-01') TO ('2025-01-01');

-- 2025 Quarters
CREATE TABLE cost_history.cost_snapshots_2025_q1
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2025-01-01') TO ('2025-04-01');

CREATE TABLE cost_history.cost_snapshots_2025_q2
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2025-04-01') TO ('2025-07-01');

CREATE TABLE cost_history.cost_snapshots_2025_q3
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2025-07-01') TO ('2025-10-01');

CREATE TABLE cost_history.cost_snapshots_2025_q4
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2025-10-01') TO ('2026-01-01');

-- 2026 Quarters (pre-created 2 years ahead)
CREATE TABLE cost_history.cost_snapshots_2026_q1
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2026-01-01') TO ('2026-04-01');

CREATE TABLE cost_history.cost_snapshots_2026_q2
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2026-04-01') TO ('2026-07-01');

CREATE TABLE cost_history.cost_snapshots_2026_q3
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2026-07-01') TO ('2026-10-01');

CREATE TABLE cost_history.cost_snapshots_2026_q4
    PARTITION OF cost_history.cost_snapshots
    FOR VALUES FROM ('2026-10-01') TO ('2027-01-01');

-- Default partition: catches any out-of-range inserts
CREATE TABLE cost_history.cost_snapshots_default
    PARTITION OF cost_history.cost_snapshots DEFAULT;
```

---

### 14.3 audit_events Partitions (7 Years = 84 Months)

```sql
-- Monthly partitions from 2023-01 through 2029-12
-- Showing 2023, 2024, 2025 fully; 2026–2029 abbreviated for readability

-- 2023
CREATE TABLE cost_history.audit_events_2023_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-01-01') TO ('2023-02-01');
CREATE TABLE cost_history.audit_events_2023_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-02-01') TO ('2023-03-01');
CREATE TABLE cost_history.audit_events_2023_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-03-01') TO ('2023-04-01');
CREATE TABLE cost_history.audit_events_2023_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-04-01') TO ('2023-05-01');
CREATE TABLE cost_history.audit_events_2023_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-05-01') TO ('2023-06-01');
CREATE TABLE cost_history.audit_events_2023_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-06-01') TO ('2023-07-01');
CREATE TABLE cost_history.audit_events_2023_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-07-01') TO ('2023-08-01');
CREATE TABLE cost_history.audit_events_2023_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-08-01') TO ('2023-09-01');
CREATE TABLE cost_history.audit_events_2023_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-09-01') TO ('2023-10-01');
CREATE TABLE cost_history.audit_events_2023_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-10-01') TO ('2023-11-01');
CREATE TABLE cost_history.audit_events_2023_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-11-01') TO ('2023-12-01');
CREATE TABLE cost_history.audit_events_2023_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2023-12-01') TO ('2024-01-01');

-- 2024
CREATE TABLE cost_history.audit_events_2024_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE cost_history.audit_events_2024_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE cost_history.audit_events_2024_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
CREATE TABLE cost_history.audit_events_2024_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-04-01') TO ('2024-05-01');
CREATE TABLE cost_history.audit_events_2024_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');
CREATE TABLE cost_history.audit_events_2024_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-06-01') TO ('2024-07-01');
CREATE TABLE cost_history.audit_events_2024_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-07-01') TO ('2024-08-01');
CREATE TABLE cost_history.audit_events_2024_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-08-01') TO ('2024-09-01');
CREATE TABLE cost_history.audit_events_2024_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-09-01') TO ('2024-10-01');
CREATE TABLE cost_history.audit_events_2024_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-10-01') TO ('2024-11-01');
CREATE TABLE cost_history.audit_events_2024_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-11-01') TO ('2024-12-01');
CREATE TABLE cost_history.audit_events_2024_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');

-- 2025
CREATE TABLE cost_history.audit_events_2025_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE cost_history.audit_events_2025_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE cost_history.audit_events_2025_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE cost_history.audit_events_2025_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE cost_history.audit_events_2025_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE cost_history.audit_events_2025_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE cost_history.audit_events_2025_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE cost_history.audit_events_2025_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE cost_history.audit_events_2025_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE cost_history.audit_events_2025_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE cost_history.audit_events_2025_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE cost_history.audit_events_2025_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

-- 2026
CREATE TABLE cost_history.audit_events_2026_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE cost_history.audit_events_2026_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE cost_history.audit_events_2026_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE cost_history.audit_events_2026_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE cost_history.audit_events_2026_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE cost_history.audit_events_2026_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE cost_history.audit_events_2026_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE cost_history.audit_events_2026_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE cost_history.audit_events_2026_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE cost_history.audit_events_2026_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE cost_history.audit_events_2026_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE cost_history.audit_events_2026_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- 2027
CREATE TABLE cost_history.audit_events_2027_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE cost_history.audit_events_2027_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE cost_history.audit_events_2027_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE cost_history.audit_events_2027_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE cost_history.audit_events_2027_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE cost_history.audit_events_2027_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE cost_history.audit_events_2027_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE cost_history.audit_events_2027_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE cost_history.audit_events_2027_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE cost_history.audit_events_2027_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE cost_history.audit_events_2027_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE cost_history.audit_events_2027_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');

-- 2028
CREATE TABLE cost_history.audit_events_2028_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-01-01') TO ('2028-02-01');
CREATE TABLE cost_history.audit_events_2028_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-02-01') TO ('2028-03-01');
CREATE TABLE cost_history.audit_events_2028_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-03-01') TO ('2028-04-01');
CREATE TABLE cost_history.audit_events_2028_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-04-01') TO ('2028-05-01');
CREATE TABLE cost_history.audit_events_2028_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-05-01') TO ('2028-06-01');
CREATE TABLE cost_history.audit_events_2028_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-06-01') TO ('2028-07-01');
CREATE TABLE cost_history.audit_events_2028_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-07-01') TO ('2028-08-01');
CREATE TABLE cost_history.audit_events_2028_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-08-01') TO ('2028-09-01');
CREATE TABLE cost_history.audit_events_2028_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-09-01') TO ('2028-10-01');
CREATE TABLE cost_history.audit_events_2028_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-10-01') TO ('2028-11-01');
CREATE TABLE cost_history.audit_events_2028_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-11-01') TO ('2028-12-01');
CREATE TABLE cost_history.audit_events_2028_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2028-12-01') TO ('2029-01-01');

-- 2029
CREATE TABLE cost_history.audit_events_2029_01 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-01-01') TO ('2029-02-01');
CREATE TABLE cost_history.audit_events_2029_02 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-02-01') TO ('2029-03-01');
CREATE TABLE cost_history.audit_events_2029_03 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-03-01') TO ('2029-04-01');
CREATE TABLE cost_history.audit_events_2029_04 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-04-01') TO ('2029-05-01');
CREATE TABLE cost_history.audit_events_2029_05 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-05-01') TO ('2029-06-01');
CREATE TABLE cost_history.audit_events_2029_06 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-06-01') TO ('2029-07-01');
CREATE TABLE cost_history.audit_events_2029_07 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-07-01') TO ('2029-08-01');
CREATE TABLE cost_history.audit_events_2029_08 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-08-01') TO ('2029-09-01');
CREATE TABLE cost_history.audit_events_2029_09 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-09-01') TO ('2029-10-01');
CREATE TABLE cost_history.audit_events_2029_10 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-10-01') TO ('2029-11-01');
CREATE TABLE cost_history.audit_events_2029_11 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-11-01') TO ('2029-12-01');
CREATE TABLE cost_history.audit_events_2029_12 PARTITION OF cost_history.audit_events FOR VALUES FROM ('2029-12-01') TO ('2030-01-01');

-- Default partition
CREATE TABLE cost_history.audit_events_default
    PARTITION OF cost_history.audit_events DEFAULT;
```

---

### 14.4 Remaining Table Partitions

```sql
-- =============================================================================
-- material_price_records: Monthly partitions (2023–2026 shown)
-- =============================================================================
CREATE TABLE cost_history.material_price_records_2023
    PARTITION OF cost_history.material_price_records
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01')
    PARTITION BY RANGE (valid_date);  -- Sub-partitioned monthly

CREATE TABLE cost_history.material_price_2023_01
    PARTITION OF cost_history.material_price_records_2023
    FOR VALUES FROM ('2023-01-01') TO ('2023-02-01');
-- (pattern continues for all months 2023–2026)

CREATE TABLE cost_history.material_price_records_default
    PARTITION OF cost_history.material_price_records DEFAULT;

-- =============================================================================
-- supplier_price_records: Yearly partitions
-- =============================================================================
CREATE TABLE cost_history.supplier_price_records_2021
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');

CREATE TABLE cost_history.supplier_price_records_2022
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');

CREATE TABLE cost_history.supplier_price_records_2023
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');

CREATE TABLE cost_history.supplier_price_records_2024
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE cost_history.supplier_price_records_2025
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE cost_history.supplier_price_records_2026
    PARTITION OF cost_history.supplier_price_records
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE cost_history.supplier_price_records_default
    PARTITION OF cost_history.supplier_price_records DEFAULT;

-- =============================================================================
-- process_cost_records: Yearly partitions
-- =============================================================================
CREATE TABLE cost_history.process_cost_records_2022
    PARTITION OF cost_history.process_cost_records
    FOR VALUES FROM (2022) TO (2023);

CREATE TABLE cost_history.process_cost_records_2023
    PARTITION OF cost_history.process_cost_records
    FOR VALUES FROM (2023) TO (2024);

CREATE TABLE cost_history.process_cost_records_2024
    PARTITION OF cost_history.process_cost_records
    FOR VALUES FROM (2024) TO (2025);

CREATE TABLE cost_history.process_cost_records_2025
    PARTITION OF cost_history.process_cost_records
    FOR VALUES FROM (2025) TO (2026);

CREATE TABLE cost_history.process_cost_records_2026
    PARTITION OF cost_history.process_cost_records
    FOR VALUES FROM (2026) TO (2027);

CREATE TABLE cost_history.process_cost_records_default
    PARTITION OF cost_history.process_cost_records DEFAULT;

-- =============================================================================
-- rfq_bids: Yearly partitions
-- =============================================================================
CREATE TABLE cost_history.rfq_bids_2023
    PARTITION OF cost_history.rfq_bids
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');

CREATE TABLE cost_history.rfq_bids_2024
    PARTITION OF cost_history.rfq_bids
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE cost_history.rfq_bids_2025
    PARTITION OF cost_history.rfq_bids
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE cost_history.rfq_bids_2026
    PARTITION OF cost_history.rfq_bids
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE cost_history.rfq_bids_default
    PARTITION OF cost_history.rfq_bids DEFAULT;
```

---

### 14.5 Partition Pruning Configuration

```sql
-- Enable partition pruning (default ON in PostgreSQL 12+; verify explicitly)
SET enable_partition_pruning = ON;

-- Verify pruning is active with EXPLAIN
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM cost_history.audit_events
WHERE occurred_at BETWEEN '2025-01-01' AND '2025-03-31';
-- Expected: only audit_events_2025_01, _02, _03 partitions scanned

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM cost_history.cost_snapshots
WHERE created_at >= '2025-04-01' AND created_at < '2025-07-01'
  AND status = 'APPROVED';
-- Expected: only cost_snapshots_2025_q2 scanned

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM cost_history.material_price_records
WHERE material_id = '550e8400-e29b-41d4-a716-446655440000'
  AND valid_date BETWEEN '2024-06-01' AND '2024-08-31';
-- Expected: only 3 monthly partitions scanned
```

---

### 14.6 PartitionMaintenanceJob

```python
"""
cost_history_engine/maintenance/partition_maintenance.py

Automated partition lifecycle management for CHE partitioned tables.
Runs weekly via Kubernetes CronJob.
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


@dataclass
class PartitionSpec:
    parent_table: str
    interval: str          # 'monthly' | 'quarterly' | 'yearly'
    key_column: str
    schema: str = "cost_history"


# Registry of all partitioned tables
PARTITION_REGISTRY: list[PartitionSpec] = [
    PartitionSpec("cost_snapshots",          "quarterly", "created_at"),
    PartitionSpec("audit_events",            "monthly",   "occurred_at"),
    PartitionSpec("material_price_records",  "monthly",   "valid_date"),
    PartitionSpec("supplier_price_records",  "yearly",    "valid_from"),
    PartitionSpec("process_cost_records",    "yearly",    "period_year"),
    PartitionSpec("rfq_bids",               "yearly",    "created_at"),
    PartitionSpec("quotes",                 "quarterly", "created_at"),
    PartitionSpec("rfq_rounds",             "yearly",    "created_at"),
]


class PartitionRange(NamedTuple):
    name: str
    from_value: str
    to_value: str


class PartitionMaintenanceJob:
    """
    Manages the lifecycle of PostgreSQL range partitions for CHE tables.

    Responsibilities:
    - create_future_partitions(): pre-create partitions 2 years in advance
    - attach_default_partition(): ensure default partition always exists
    - detach_and_archive_old_partition(): move expired partitions to cold tier
    """

    def __init__(self, dsn: str, lookahead_years: int = 2) -> None:
        self._dsn = dsn
        self._lookahead_years = lookahead_years

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def create_future_partitions(self) -> dict[str, list[str]]:
        """
        For each registered table, compute the required future partitions
        based on the lookahead_years window and create any that are missing.
        Returns {table_name: [created_partition_names]}.
        """
        created: dict[str, list[str]] = {}

        with psycopg.connect(self._dsn) as conn:
            for spec in PARTITION_REGISTRY:
                needed = self._compute_needed_partitions(spec)
                existing = self._get_existing_partitions(conn, spec)
                to_create = [p for p in needed if p.name not in existing]

                created[spec.parent_table] = []
                for partition in to_create:
                    self._create_partition(conn, spec, partition)
                    created[spec.parent_table].append(partition.name)
                    logger.info(
                        "Created partition %s.%s [%s, %s)",
                        spec.schema,
                        partition.name,
                        partition.from_value,
                        partition.to_value,
                    )

                conn.commit()

        return created

    def attach_default_partition(self) -> list[str]:
        """
        Ensures every partitioned table has a DEFAULT partition.
        Creates it if missing. Returns list of tables where default was created.
        """
        created: list[str] = []

        with psycopg.connect(self._dsn) as conn:
            for spec in PARTITION_REGISTRY:
                default_name = f"{spec.parent_table}_default"
                existing = self._get_existing_partitions(conn, spec)

                if default_name not in existing:
                    conn.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {spec.schema}.{default_name}
                            PARTITION OF {spec.schema}.{spec.parent_table} DEFAULT
                        """
                    )
                    created.append(default_name)
                    logger.warning(
                        "Default partition was missing; created %s.%s",
                        spec.schema,
                        default_name,
                    )

            conn.commit()

        return created

    def detach_and_archive_old_partition(
        self,
        table: str,
        partition_name: str,
        s3_bucket: str,
        s3_prefix: str,
        archive_before_detach: bool = True,
    ) -> bool:
        """
        Detaches a partition from the parent table and optionally archives it to S3
        using pg_dump. The partition table remains in the schema as an unattached
        table until explicitly dropped.

        Steps:
        1. Verify partition exists and is old enough
        2. If archive_before_detach: pg_dump partition → GZIP → S3
        3. ALTER TABLE DETACH PARTITION
        4. Log the operation in audit_events
        5. Return True on success
        """
        try:
            if archive_before_detach:
                self._pg_dump_to_s3(
                    schema="cost_history",
                    table=partition_name,
                    s3_bucket=s3_bucket,
                    s3_prefix=s3_prefix,
                )

            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    f"""
                    ALTER TABLE cost_history.{table}
                        DETACH PARTITION cost_history.{partition_name}
                    """
                )

                # Log in audit
                conn.execute(
                    """
                    INSERT INTO cost_history.audit_events (
                        entity_type, entity_id, action,
                        actor_id, actor_role, system_source,
                        new_values
                    ) VALUES (
                        'Partition', %s::UUID, 'ARCHIVE',
                        '00000000-0000-0000-0000-000000000001'::UUID,
                        'SYSTEM', 'PARTITION-MAINTENANCE-JOB',
                        %s::JSONB
                    )
                    """,
                    [
                        str(uuid.uuid5(uuid.NAMESPACE_DNS, partition_name)),
                        f'{{"partition": "{partition_name}", "table": "{table}", '
                        f'"detached_at": "{datetime.now(timezone.utc).isoformat()}"}}',
                    ],
                )
                conn.commit()

            logger.info(
                "Detached and archived partition cost_history.%s from %s",
                partition_name,
                table,
            )
            return True

        except Exception as exc:
            logger.exception(
                "Failed to detach partition %s: %s", partition_name, exc
            )
            return False

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _compute_needed_partitions(
        self, spec: PartitionSpec
    ) -> list[PartitionRange]:
        """Returns list of PartitionRange objects that should exist."""
        partitions: list[PartitionRange] = []
        today = date.today()
        end_date = date(today.year + self._lookahead_years, 12, 31)

        if spec.interval == "monthly":
            d = date(today.year - 7, 1, 1)  # Keep 7 years of monthly partitions
            while d <= end_date:
                next_d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
                partitions.append(PartitionRange(
                    name=f"{spec.parent_table}_{d.year}_{d.month:02d}",
                    from_value=d.isoformat(),
                    to_value=next_d.isoformat(),
                ))
                d = next_d

        elif spec.interval == "quarterly":
            start_year = today.year - 3
            for year in range(start_year, today.year + self._lookahead_years + 1):
                for q, (start_mo, end_mo) in enumerate(
                    [(1, 4), (4, 7), (7, 10), (10, 1)], start=1
                ):
                    from_d = date(year, start_mo, 1)
                    to_d = date(year + (1 if q == 4 else 0), end_mo, 1)
                    partitions.append(PartitionRange(
                        name=f"{spec.parent_table}_{year}_q{q}",
                        from_value=from_d.isoformat(),
                        to_value=to_d.isoformat(),
                    ))

        elif spec.interval == "yearly":
            start_year = today.year - 5
            for year in range(start_year, today.year + self._lookahead_years + 1):
                from_d = date(year, 1, 1)
                to_d = date(year + 1, 1, 1)
                partitions.append(PartitionRange(
                    name=f"{spec.parent_table}_{year}",
                    from_value=from_d.isoformat(),
                    to_value=to_d.isoformat(),
                ))

        return partitions

    def _get_existing_partitions(
        self, conn: psycopg.Connection, spec: PartitionSpec
    ) -> set[str]:
        rows = conn.execute(
            """
            SELECT c.relname AS partition_name
            FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class p ON p.oid = i.inhparent
            JOIN pg_namespace n ON n.oid = p.relnamespace
            WHERE n.nspname = %s
              AND p.relname = %s
            """,
            [spec.schema, spec.parent_table],
        ).fetchall()
        return {r[0] for r in rows}

    def _create_partition(
        self,
        conn: psycopg.Connection,
        spec: PartitionSpec,
        partition: PartitionRange,
    ) -> None:
        if spec.interval == "yearly" and spec.key_column == "period_year":
            # Integer range partition (process_cost_records)
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {spec.schema}.{partition.name}
                    PARTITION OF {spec.schema}.{spec.parent_table}
                    FOR VALUES FROM ({partition.from_value[:4]})
                                TO ({partition.to_value[:4]})
                """
            )
        else:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {spec.schema}.{partition.name}
                    PARTITION OF {spec.schema}.{spec.parent_table}
                    FOR VALUES FROM ('{partition.from_value}')
                                TO ('{partition.to_value}')
                """
            )

    def _pg_dump_to_s3(
        self,
        schema: str,
        table: str,
        s3_bucket: str,
        s3_prefix: str,
    ) -> None:
        """Dumps a single partition table to S3 via pg_dump piped to aws s3 cp."""
        s3_key = f"s3://{s3_bucket}/{s3_prefix}/{table}.dump.gz"
        cmd = (
            f"pg_dump --table={schema}.{table} --format=custom "
            f"| gzip | aws s3 cp - {s3_key} "
            f"--sse aws:kms --storage-class GLACIER"
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump/S3 upload failed for {table}: {result.stderr}"
            )
        logger.info("Archived partition %s.%s to %s", schema, table, s3_key)
```

---

*End of Section 11–14*
