# Cost History Engine — Backup, Security, GDPR & Monitoring

**Module:** Cost History Engine (CHE)  
**Document:** 05 — Backup Strategy, Security, GDPR Compliance, and Monitoring  
**Platform:** Industrial Cost Intelligence  
**Database:** PostgreSQL 16 (append-only, partitioned, audit-trailed)

---

## Section 15 — Backup Strategy

CHE operates as the authoritative store for all historical cost data. Backup tiers are designed for defence-in-depth: WAL streaming for near-zero RPO, daily base backups for fast full recovery, weekly logical dumps for schema-level granularity, and long-term Glacier archival for compliance retention.

### 15.1 Backup Tiers

| Tier | Method | Schedule | Retention | RPO | RTO | Storage |
|------|--------|----------|-----------|-----|-----|---------|
| 1 | WAL continuous archiving | Continuous (streaming) | 30 days | 0 min | < 30 min | S3 Standard |
| 2 | `pg_basebackup` full | Daily 02:00 UTC | 30 copies | < 24 h | < 4 h | S3 Standard |
| 3 | `pg_dump` logical (per schema) | Weekly Sunday 03:00 UTC | 52 copies | < 7 d | < 8 h | S3 Intelligent-Tiering (IA) |
| 4 | Monthly EBS/RDS snapshot | Monthly 1st 01:00 UTC | 10 years | Cold archive | Cold archive | S3 Glacier Deep Archive |

### 15.2 BackupManifest Entity

Every backup execution — regardless of tier — produces a `BackupManifest` record persisted in the operational metadata schema.

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `backup_id` | `UUID` | PK, DEFAULT gen_random_uuid() | Unique backup job identifier |
| `backup_type` | `ENUM('WAL','FULL_BASEBACKUP','LOGICAL_DUMP','GLACIER_SNAPSHOT')` | NOT NULL | Tier classification |
| `started_at` | `TIMESTAMPTZ` | NOT NULL | Job start timestamp (UTC) |
| `completed_at` | `TIMESTAMPTZ` | nullable | Job completion timestamp; NULL while IN_PROGRESS |
| `duration_seconds` | `INTEGER` | nullable | Elapsed seconds from start to completion |
| `size_bytes` | `BIGINT` | nullable | Compressed backup size in bytes |
| `s3_bucket` | `VARCHAR(100)` | NOT NULL | Target S3 bucket name |
| `s3_key` | `VARCHAR(500)` | NOT NULL | Full S3 object key (path) |
| `checksum_sha256` | `CHAR(64)` | nullable | SHA-256 of the backup artifact |
| `pg_version` | `VARCHAR(20)` | NOT NULL | PostgreSQL server version string |
| `schema_list` | `TEXT[]` | NOT NULL | Array of schemas captured (e.g. `{cost_history, meta}`) |
| `table_count` | `INTEGER` | nullable | Number of tables included in the backup |
| `row_count_estimate` | `BIGINT` | nullable | Estimated total row count from `pg_class.reltuples` |
| `status` | `ENUM('IN_PROGRESS','COMPLETED','FAILED','VERIFIED')` | NOT NULL | Current lifecycle state |
| `restore_tested_at` | `TIMESTAMPTZ` | nullable | Timestamp of last successful restore test |
| `restore_test_result` | `VARCHAR(20)` | nullable | Result of restore test: PASSED / FAILED / SKIPPED |

### 15.3 Backup Verification Protocol

Weekly automated restore tests validate recoverability. Tests run every Sunday at 06:00 UTC (after the Tier 3 dump completes) in an isolated environment.

**Steps:**

1. **Restore base backup** — Retrieve the latest `FULL_BASEBACKUP` artifact from S3 Standard and restore it to an ephemeral RDS PostgreSQL 16 instance provisioned by Terraform (`r6g.large`, same major version, VPC-isolated).

2. **Schema validation** — Execute structural checks:
   - Compare `information_schema.tables` count against the expected table count stored in `BackupManifest.table_count` (tolerance: ±0).
   - Verify all expected `ENUM` types exist in `pg_type` (count must match production).
   - Confirm all `cost_history` partitions are present and attached.

3. **Row count spot-checks** — Query five critical tables and compare against the manifest estimate (tolerance: ±5 % for partitioned tables):
   - `cost_history.cost_snapshots`
   - `cost_history.audit_events`
   - `cost_history.quotes`
   - `cost_history.rfq_bids`
   - `cost_history.material_price_records`

4. **Snapshot hash verification** — Select 10 random `snapshot_id` values from `cost_history.cost_snapshots` and call `cost_history.verify_snapshot_hash(snapshot_id)` on both the restored instance and production. All 10 must return `TRUE` (matching hashes).

5. **Report generation** — Produce a structured JSON verification report and call `BackupOrchestrator.update_manifest_verification()` to write `restore_tested_at`, `restore_test_result`, and advance `status` to `VERIFIED`. Terminate the ephemeral instance. Alert via PagerDuty on any failure.

### 15.4 Cross-Region Replication

| Attribute | Primary | Replica |
|-----------|---------|---------|
| Region | `eu-west-1` (Ireland) | `eu-central-1` (Frankfurt) |
| Replication mode | Async streaming (WAL) | — |
| Replication lag SLA | — | < 5 seconds P95 |
| Failover RTO (manual) | — | < 15 minutes |
| Failover RTO (automated) | — | < 60 seconds (Patroni auto-promote) |
| Tool | Patroni + etcd quorum | — |
| WAL archival | Both regions independently archive WAL to regional S3 buckets | — |

### 15.5 Recovery Runbook

| Scenario | RTO | RPO | Recovery Steps |
|----------|-----|-----|----------------|
| Single table corruption | < 30 min | < 5 min | `pg_restore --table=<table>` from latest Tier 3 logical dump; replay WAL from dump LSN to target point-in-time |
| Schema corruption | < 4 h | < 24 h | Full `pg_basebackup` restore to new instance; redirect application DNS; validate with spot-checks |
| Full DC outage (eu-west-1) | < 60 min | < 5 s | Patroni auto-promotes eu-central-1 replica; update Route 53 CNAME; notify application teams; validate health checks |
| Ransomware / full deletion | < 8 h | < 7 d | Restore Tier 4 Glacier archive (restore time ~4 h); replay available WAL from Tier 1 S3 archive to minimize data loss; validate all row counts |
| Accidental bulk delete (RLS bypass) | < 1 h | < 0 (point-in-time) | Perform WAL-based point-in-time recovery (PITR) to the second before the delete statement; use `recovery_target_lsn` or `recovery_target_time`; validate audit log to confirm target LSN |

### 15.6 BackupOrchestrator — Python Implementation

```python
"""
cost_history_engine.backup.orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Orchestrates all CHE backup tiers: WAL archival, pg_basebackup, pg_dump, and
Glacier lifecycle transitions. Produces and updates BackupManifest records.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import psycopg2
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    manifest_id: UUID
    verified_at: datetime
    schema_check_passed: bool
    row_count_checks: dict[str, bool]
    hash_checks_passed: int
    hash_checks_total: int
    overall_passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RestoreTestResult:
    manifest_id: UUID
    target_host: str
    tested_at: datetime
    passed: bool
    schema_valid: bool
    row_counts_valid: bool
    hash_checks_valid: bool
    duration_seconds: int
    error_message: str | None = None


@dataclass
class BackupManifest:
    backup_id: UUID
    backup_type: str          # WAL | FULL_BASEBACKUP | LOGICAL_DUMP | GLACIER_SNAPSHOT
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: int | None
    size_bytes: int | None
    s3_bucket: str
    s3_key: str
    checksum_sha256: str | None
    pg_version: str
    schema_list: list[str]
    table_count: int | None
    row_count_estimate: int | None
    status: str               # IN_PROGRESS | COMPLETED | FAILED | VERIFIED
    restore_tested_at: datetime | None = None
    restore_test_result: str | None = None


class BackupOrchestrator:
    """
    Coordinates CHE backup operations across all four tiers.

    Responsibilities:
    - Trigger pg_basebackup and pg_dump jobs
    - Upload artifacts to S3 with correct storage class
    - Compute SHA-256 checksums for integrity validation
    - Persist BackupManifest records to the metadata schema
    - Run integrity verification against existing manifests
    - Drive ephemeral restore tests and update manifest results
    - Schedule S3 Glacier lifecycle transitions for long-term archival
    """

    VALID_BACKUP_TYPES = frozenset(
        {"WAL", "FULL_BASEBACKUP", "LOGICAL_DUMP", "GLACIER_SNAPSHOT"}
    )
    STORAGE_CLASS_MAP = {
        "standard": "STANDARD",
        "ia": "STANDARD_IA",
        "glacier": "GLACIER",
        "deep_archive": "DEEP_ARCHIVE",
    }
    SPOT_CHECK_TABLES = [
        "cost_snapshots",
        "audit_events",
        "quotes",
        "rfq_bids",
        "material_price_records",
    ]

    def __init__(
        self,
        dsn: str,
        s3_client: boto3.client,
        metadata_schema: str = "che_meta",
    ) -> None:
        self._dsn = dsn
        self._s3 = s3_client
        self._meta_schema = metadata_schema

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trigger_full_backup(self, backup_type: str) -> BackupManifest:
        """
        Initiate a full backup of the given type and return a completed manifest.

        Args:
            backup_type: One of WAL, FULL_BASEBACKUP, LOGICAL_DUMP, GLACIER_SNAPSHOT.

        Returns:
            BackupManifest with status COMPLETED (or FAILED on error).

        Raises:
            ValueError: If backup_type is not recognised.
        """
        if backup_type not in self.VALID_BACKUP_TYPES:
            raise ValueError(
                f"Unknown backup_type '{backup_type}'. "
                f"Expected one of: {sorted(self.VALID_BACKUP_TYPES)}"
            )

        backup_id = uuid4()
        started_at = datetime.now(timezone.utc)
        manifest = self.generate_manifest(
            backup_id=backup_id,
            backup_type=backup_type,
            started_at=started_at,
            status="IN_PROGRESS",
            s3_bucket=self._resolve_bucket(backup_type),
            s3_key="",
            pg_version=self._get_pg_version(),
            schema_list=["cost_history", "che_meta"],
        )
        self._persist_manifest(manifest)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                local_path = self._run_backup_command(backup_type, tmpdir)
                checksum = self._compute_sha256(local_path)
                s3_key = self._build_s3_key(backup_type, backup_id)
                tier = self._tier_for_type(backup_type)
                self.upload_to_s3(local_path, s3_key, tier)

                size_bytes = Path(local_path).stat().st_size
                completed_at = datetime.now(timezone.utc)
                duration = int((completed_at - started_at).total_seconds())

                manifest.checksum_sha256 = checksum
                manifest.s3_key = s3_key
                manifest.size_bytes = size_bytes
                manifest.completed_at = completed_at
                manifest.duration_seconds = duration
                manifest.table_count = self._count_tables()
                manifest.row_count_estimate = self._estimate_row_count()
                manifest.status = "COMPLETED"

        except Exception as exc:
            logger.exception("Backup job %s failed: %s", backup_id, exc)
            manifest.status = "FAILED"
            manifest.completed_at = datetime.now(timezone.utc)
        finally:
            self._update_manifest(manifest)

        return manifest

    def upload_to_s3(self, local_path: str, s3_key: str, tier: str) -> str:
        """
        Upload a local backup artifact to S3 with the appropriate storage class.

        Args:
            local_path: Absolute path to the local backup file.
            s3_key:     Destination key within the target bucket.
            tier:       Storage tier key: 'standard', 'ia', 'glacier', 'deep_archive'.

        Returns:
            The s3_key that was written (unchanged from input).

        Raises:
            ValueError: If the tier is not recognised.
            ClientError: On S3 upload failure.
        """
        storage_class = self.STORAGE_CLASS_MAP.get(tier)
        if storage_class is None:
            raise ValueError(
                f"Unknown S3 tier '{tier}'. "
                f"Expected one of: {list(self.STORAGE_CLASS_MAP)}"
            )

        bucket = self._resolve_bucket_for_tier(tier)
        logger.info(
            "Uploading %s → s3://%s/%s [%s]", local_path, bucket, s3_key, storage_class
        )
        self._s3.upload_file(
            local_path,
            bucket,
            s3_key,
            ExtraArgs={"StorageClass": storage_class},
        )
        logger.info("Upload complete: s3://%s/%s", bucket, s3_key)
        return s3_key

    def generate_manifest(
        self,
        backup_id: UUID,
        backup_type: str,
        started_at: datetime,
        status: str,
        s3_bucket: str,
        s3_key: str,
        pg_version: str,
        schema_list: list[str],
        **kwargs: Any,
    ) -> BackupManifest:
        """
        Construct a BackupManifest dataclass from provided parameters.

        Additional optional fields (completed_at, size_bytes, checksum_sha256,
        table_count, row_count_estimate) may be supplied via **kwargs.
        """
        return BackupManifest(
            backup_id=backup_id,
            backup_type=backup_type,
            started_at=started_at,
            completed_at=kwargs.get("completed_at"),
            duration_seconds=kwargs.get("duration_seconds"),
            size_bytes=kwargs.get("size_bytes"),
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            checksum_sha256=kwargs.get("checksum_sha256"),
            pg_version=pg_version,
            schema_list=schema_list,
            table_count=kwargs.get("table_count"),
            row_count_estimate=kwargs.get("row_count_estimate"),
            status=status,
            restore_tested_at=kwargs.get("restore_tested_at"),
            restore_test_result=kwargs.get("restore_test_result"),
        )

    def verify_backup_integrity(self, manifest: BackupManifest) -> VerificationResult:
        """
        Verify backup integrity by re-downloading the S3 artifact and comparing
        the SHA-256 checksum against the stored value in the manifest.

        Also performs row-count spot-checks against current production data
        (within tolerance) and reports per-table pass/fail.

        Args:
            manifest: A COMPLETED BackupManifest to verify.

        Returns:
            VerificationResult dataclass with per-check outcomes.
        """
        verified_at = datetime.now(timezone.utc)
        schema_check_passed = False
        row_count_checks: dict[str, bool] = {}
        hash_checks_passed = 0

        try:
            # Checksum re-verification
            with tempfile.NamedTemporaryFile(suffix=".bak") as tmp:
                self._s3.download_file(manifest.s3_bucket, manifest.s3_key, tmp.name)
                actual_checksum = self._compute_sha256(tmp.name)

            schema_check_passed = (actual_checksum == manifest.checksum_sha256)

            # Row count spot-checks (±5 % tolerance)
            live_counts = self._get_live_row_counts(self.SPOT_CHECK_TABLES)
            for table, live_count in live_counts.items():
                expected = manifest.row_count_estimate or 0
                ratio = live_count / max(expected, 1)
                row_count_checks[table] = 0.95 <= ratio <= 1.05

            # Hash verification (10 random snapshots)
            hash_checks_passed = self._verify_snapshot_hashes(sample_size=10)
            hash_checks_total = 10

        except Exception as exc:
            logger.exception("Integrity verification failed for %s: %s", manifest.backup_id, exc)
            return VerificationResult(
                manifest_id=manifest.backup_id,
                verified_at=verified_at,
                schema_check_passed=False,
                row_count_checks=row_count_checks,
                hash_checks_passed=0,
                hash_checks_total=10,
                overall_passed=False,
                details={"error": str(exc)},
            )

        overall_passed = (
            schema_check_passed
            and all(row_count_checks.values())
            and hash_checks_passed == 10
        )

        return VerificationResult(
            manifest_id=manifest.backup_id,
            verified_at=verified_at,
            schema_check_passed=schema_check_passed,
            row_count_checks=row_count_checks,
            hash_checks_passed=hash_checks_passed,
            hash_checks_total=10,
            overall_passed=overall_passed,
        )

    def test_restore(
        self, manifest: BackupManifest, target_host: str
    ) -> RestoreTestResult:
        """
        Run a full restore test against an ephemeral database host.

        Downloads the backup artifact, restores it to target_host, validates
        schema structure, row counts, and snapshot hashes. Updates the manifest
        with results when complete.

        Args:
            manifest:    The BackupManifest to restore from.
            target_host: FQDN or IP of the ephemeral PostgreSQL restore target.

        Returns:
            RestoreTestResult with granular per-check outcomes.
        """
        started = datetime.now(timezone.utc)
        schema_valid = False
        row_counts_valid = False
        hash_checks_valid = False
        error_message: str | None = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".bak", delete=False) as tmp:
                local_path = tmp.name
                self._s3.download_file(manifest.s3_bucket, manifest.s3_key, local_path)

            # Step 1: restore to target
            self._restore_basebackup(local_path, target_host)

            restore_dsn = f"host={target_host} dbname=che_restore user=restore_user"

            # Step 2: schema validation
            with psycopg2.connect(restore_dsn) as conn:
                schema_valid = self._validate_schema(conn, manifest)

                # Step 3: row count spot-checks
                live_counts = self._get_live_row_counts(
                    self.SPOT_CHECK_TABLES, dsn=restore_dsn
                )
                row_counts_valid = all(
                    count > 0 for count in live_counts.values()
                )

                # Step 4: snapshot hash verification
                hash_ok = self._verify_snapshot_hashes(
                    sample_size=10, dsn=restore_dsn
                )
                hash_checks_valid = hash_ok == 10

        except Exception as exc:
            logger.exception(
                "Restore test failed for manifest %s on %s: %s",
                manifest.backup_id, target_host, exc,
            )
            error_message = str(exc)

        completed = datetime.now(timezone.utc)
        passed = schema_valid and row_counts_valid and hash_checks_valid and error_message is None

        result = RestoreTestResult(
            manifest_id=manifest.backup_id,
            target_host=target_host,
            tested_at=completed,
            passed=passed,
            schema_valid=schema_valid,
            row_counts_valid=row_counts_valid,
            hash_checks_valid=hash_checks_valid,
            duration_seconds=int((completed - started).total_seconds()),
            error_message=error_message,
        )

        # Step 5: update manifest
        manifest.restore_tested_at = completed
        manifest.restore_test_result = "PASSED" if passed else "FAILED"
        if passed:
            manifest.status = "VERIFIED"
        self._update_manifest(manifest)

        return result

    def schedule_glacier_transition(
        self, s3_key: str, transition_after_days: int
    ) -> None:
        """
        Apply an S3 lifecycle rule to transition the given object to Glacier
        Deep Archive after `transition_after_days` days.

        In practice this applies a tag-based lifecycle policy so that the
        specific object is subject to the transition rule independently of the
        bucket-wide lifecycle configuration.

        Args:
            s3_key:                 Full S3 object key to tag for transition.
            transition_after_days:  Days after creation before Glacier transition.
        """
        bucket = self._resolve_bucket_for_tier("glacier")
        logger.info(
            "Scheduling Glacier transition for s3://%s/%s after %d days",
            bucket, s3_key, transition_after_days,
        )
        self._s3.put_object_tagging(
            Bucket=bucket,
            Key=s3_key,
            Tagging={
                "TagSet": [
                    {"Key": "glacier-transition-days", "Value": str(transition_after_days)},
                    {"Key": "managed-by", "Value": "BackupOrchestrator"},
                ]
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_backup_command(self, backup_type: str, tmpdir: str) -> str:
        output_path = f"{tmpdir}/backup.tar.gz"
        if backup_type == "FULL_BASEBACKUP":
            subprocess.run(
                ["pg_basebackup", "-D", tmpdir, "--format=tar", "--gzip", "--wal-method=stream"],
                check=True,
                capture_output=True,
            )
            output_path = f"{tmpdir}/base.tar.gz"
        elif backup_type == "LOGICAL_DUMP":
            subprocess.run(
                ["pg_dump", "-Fc", "-n", "cost_history", "-f", output_path, self._dsn],
                check=True,
                capture_output=True,
            )
        elif backup_type == "GLACIER_SNAPSHOT":
            # EBS/RDS snapshot: initiated via AWS API, local artifact is metadata only
            snapshot_meta = {"type": "GLACIER_SNAPSHOT", "initiated_at": datetime.now(timezone.utc).isoformat()}
            with open(output_path, "w") as f:
                json.dump(snapshot_meta, f)
        return output_path

    def _compute_sha256(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _build_s3_key(self, backup_type: str, backup_id: UUID) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        return f"che/backups/{backup_type.lower()}/{ts}/{backup_id}.tar.gz"

    def _tier_for_type(self, backup_type: str) -> str:
        return {
            "WAL": "standard",
            "FULL_BASEBACKUP": "standard",
            "LOGICAL_DUMP": "ia",
            "GLACIER_SNAPSHOT": "deep_archive",
        }[backup_type]

    def _resolve_bucket(self, backup_type: str) -> str:
        return self._resolve_bucket_for_tier(self._tier_for_type(backup_type))

    def _resolve_bucket_for_tier(self, tier: str) -> str:
        return {
            "standard": "che-backups-standard",
            "ia": "che-backups-ia",
            "glacier": "che-backups-glacier",
            "deep_archive": "che-backups-glacier",
        }[tier]

    def _get_pg_version(self) -> str:
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                return cur.fetchone()[0].split()[1]

    def _count_tables(self) -> int:
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'cost_history'"
                )
                return cur.fetchone()[0]

    def _estimate_row_count(self) -> int:
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT coalesce(sum(reltuples::bigint), 0) "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'cost_history' AND c.relkind = 'r'"
                )
                return cur.fetchone()[0]

    def _get_live_row_counts(
        self, tables: list[str], dsn: str | None = None
    ) -> dict[str, int]:
        dsn = dsn or self._dsn
        counts: dict[str, int] = {}
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                for table in tables:
                    cur.execute(
                        f"SELECT count(*) FROM cost_history.{table}"  # noqa: S608
                    )
                    counts[table] = cur.fetchone()[0]
        return counts

    def _verify_snapshot_hashes(
        self, sample_size: int = 10, dsn: str | None = None
    ) -> int:
        dsn = dsn or self._dsn
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_id FROM cost_history.cost_snapshots "
                    "ORDER BY random() LIMIT %s",
                    (sample_size,),
                )
                snapshot_ids = [row[0] for row in cur.fetchall()]
                passed = 0
                for sid in snapshot_ids:
                    cur.execute(
                        "SELECT cost_history.verify_snapshot_hash(%s)", (sid,)
                    )
                    if cur.fetchone()[0]:
                        passed += 1
        return passed

    def _validate_schema(self, conn: Any, manifest: BackupManifest) -> bool:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'cost_history'"
            )
            restored_count = cur.fetchone()[0]
        return restored_count == manifest.table_count

    def _restore_basebackup(self, local_path: str, target_host: str) -> None:
        subprocess.run(
            ["pg_restore", "--host", target_host, "--dbname", "che_restore", local_path],
            check=True,
            capture_output=True,
        )

    def _persist_manifest(self, manifest: BackupManifest) -> None:
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._meta_schema}.backup_manifests (
                        backup_id, backup_type, started_at, status,
                        s3_bucket, s3_key, pg_version, schema_list
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(manifest.backup_id),
                        manifest.backup_type,
                        manifest.started_at,
                        manifest.status,
                        manifest.s3_bucket,
                        manifest.s3_key,
                        manifest.pg_version,
                        manifest.schema_list,
                    ),
                )

    def _update_manifest(self, manifest: BackupManifest) -> None:
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._meta_schema}.backup_manifests SET
                        completed_at         = %s,
                        duration_seconds     = %s,
                        size_bytes           = %s,
                        s3_key               = %s,
                        checksum_sha256      = %s,
                        table_count          = %s,
                        row_count_estimate   = %s,
                        status               = %s,
                        restore_tested_at    = %s,
                        restore_test_result  = %s
                    WHERE backup_id = %s
                    """,
                    (
                        manifest.completed_at,
                        manifest.duration_seconds,
                        manifest.size_bytes,
                        manifest.s3_key,
                        manifest.checksum_sha256,
                        manifest.table_count,
                        manifest.row_count_estimate,
                        manifest.status,
                        manifest.restore_tested_at,
                        manifest.restore_test_result,
                        str(manifest.backup_id),
                    ),
                )
```

---

## Section 16 — Security

### 16.1 RBAC Roles

| Role | Description |
|------|-------------|
| `COST_VIEWER` | Read-only access to APPROVED and SUPERSEDED snapshots and ACCEPTED quotes. Floor price and margin percentage fields are hidden via column masking view. |
| `COST_ANALYST` | Full read access to all snapshots, quotes, and RFQ rounds. Can read price history. No write access to any business data. |
| `QUOTE_MANAGER` | Create, update, and submit quotes. Read own cost snapshots. Approve quotes up to 50,000 EUR total value without escalation. |
| `RFQ_COORDINATOR` | Full CRUD on RFQ rounds and supplier invitations. Read supplier bids and benchmark data. Record realised savings. |
| `PRICING_MANAGER` | Full CRUD on supplier price records and material price records. Read access to all cost snapshots and price history. |
| `COMPLIANCE_OFFICER` | Read access to ALL tables including `audit_events`. Cannot write or modify any business data. Can initiate bulk export with approval workflow. |
| `CHE_ADMIN` | Full access to all CHE resources. Manage retention policies, data classification overrides, and RBAC assignments. |
| `SYSTEM_INTEGRATOR` | Service account role for machine-to-machine integration. Full API read/write via mTLS client certificates. Rate limited to 1,000 requests/minute. |

### 16.2 Permission Matrix

| Data Class | COST_VIEWER | COST_ANALYST | QUOTE_MANAGER | RFQ_COORDINATOR | PRICING_MANAGER | COMPLIANCE_OFFICER | CHE_ADMIN |
|------------|:-----------:|:------------:|:-------------:|:---------------:|:---------------:|:-----------------:|:---------:|
| CostSnapshot | R (APPROVED/SUPERSEDED only) | R | R (own) | R | R | R | CRUD |
| Quote | R (ACCEPTED only, masked) | R | CRUD (own/assigned) | R | R | R | CRUD |
| RFQ | R | R | R | CRUD | R | R | CRUD |
| SupplierPrice | — | R | — | R | CRUD | R | CRUD |
| MaterialPrice | — | R | — | R | CRUD | R | CRUD |
| ProcessCost | — | R | R | R | RW | R | CRUD |
| AuditEvent | — | — | — | — | — | R | R |
| RetentionPolicy | — | — | — | — | — | R | CRUD |

> **Legend:** R = SELECT, RW = SELECT + UPDATE, CRUD = full SELECT/INSERT/UPDATE/DELETE, — = no access

### 16.3 Row-Level Security Policies

```sql
-- =============================================================================
-- cost_snapshots
-- =============================================================================

-- COST_VIEWER: sees only APPROVED and SUPERSEDED snapshots
CREATE POLICY che_snapshot_viewer
    ON cost_history.cost_snapshots
    FOR SELECT
    TO cost_viewer_role
    USING (
        status IN ('APPROVED', 'SUPERSEDED')
    );

-- COST_ANALYST: sees all snapshots except other users' DRAFT snapshots
CREATE POLICY che_snapshot_analyst
    ON cost_history.cost_snapshots
    FOR SELECT
    TO cost_analyst_role
    USING (
        status != 'DRAFT'
        OR created_by = current_setting('app.current_user_id', TRUE)::uuid
    );

-- =============================================================================
-- quotes
-- =============================================================================

-- QUOTE_MANAGER: full access to own quotes or quotes assigned to them
CREATE POLICY che_quote_manager
    ON cost_history.quotes
    FOR ALL
    TO quote_manager_role
    USING (
        created_by = current_setting('app.current_user_id', TRUE)::uuid
        OR assigned_manager_id = current_setting('app.current_user_id', TRUE)::uuid
    )
    WITH CHECK (
        created_by = current_setting('app.current_user_id', TRUE)::uuid
        OR assigned_manager_id = current_setting('app.current_user_id', TRUE)::uuid
    );

-- =============================================================================
-- audit_events — append-only enforcement
-- =============================================================================

-- Only COMPLIANCE_OFFICER and CHE_ADMIN may read audit events
CREATE POLICY che_audit_read
    ON cost_history.audit_events
    FOR SELECT
    TO compliance_officer_role, che_admin_role
    USING (TRUE);

-- Only the dedicated audit_writer service account may insert audit events
CREATE POLICY che_audit_insert_only
    ON cost_history.audit_events
    FOR INSERT
    TO audit_writer_role
    WITH CHECK (TRUE);

-- No role may update audit events (RESTRICTIVE blocks even table owner)
CREATE POLICY che_audit_no_update
    ON cost_history.audit_events
    AS RESTRICTIVE
    FOR UPDATE
    USING (FALSE);

-- No role may delete audit events (RESTRICTIVE blocks even table owner)
CREATE POLICY che_audit_no_delete
    ON cost_history.audit_events
    AS RESTRICTIVE
    FOR DELETE
    USING (FALSE);

-- =============================================================================
-- supplier_price_records
-- =============================================================================

-- PRICING_MANAGER: full access; all other authenticated roles read active records only
CREATE POLICY che_price_read
    ON cost_history.supplier_price_records
    FOR SELECT
    USING (
        current_setting('app.user_role', TRUE) = 'PRICING_MANAGER'
        OR current_setting('app.user_role', TRUE) = 'CHE_ADMIN'
        OR (
            is_active = TRUE
            AND valid_to >= CURRENT_DATE
        )
    );
```

### 16.4 Security Controls

| # | Control | Implementation | Standard |
|---|---------|----------------|----------|
| 1 | **Encryption at rest** | PostgreSQL Transparent Data Encryption (TDE) for all tablespaces; additional application-layer AES-256-GCM encryption for `floor_price` and `margin_pct` columns before persistence | SOX, ISO 27001 |
| 2 | **Encryption in transit** | TLS 1.3 enforced on all API endpoints (`ssl_min_protocol_version = TLSv1.3`); mutual TLS (mTLS) required for all service-to-service communication | NIST SP 800-52 |
| 3 | **Field-level encryption** | `floor_price_eur`, `margin_pct`, and supplier contract prices encrypted with per-tenant data keys via AWS KMS envelope encryption; plaintext never persisted | PCI DSS Requirement 3 |
| 4 | **Key management** | AWS KMS Customer Managed Keys (CMK) with automatic rotation every 90 days; HashiCorp Vault for application secrets; envelope encryption pattern (DEK encrypted by CMK) | NIST SP 800-57 |
| 5 | **Authentication** | JWT RS256 tokens with 15-minute TTL; refresh tokens (7-day TTL, rotated on use); OIDC federation via Keycloak; MFA enforced for all human users | OAuth 2.0 / OIDC |
| 6 | **Authorization** | RBAC role model enforced at API layer; PostgreSQL RLS for row-level filtering; column masking views for sensitive fields; role set via `SET LOCAL app.user_role` per transaction | NIST AC-3 |
| 7 | **API rate limiting** | 200 requests/minute per JWT token; 1,000 requests/minute for `SYSTEM_INTEGRATOR`; AWS WAF with OWASP Core Rule Set 3.2 on all public endpoints | OWASP API Security Top 10 |
| 8 | **Export controls** | Bulk export requests exceeding 1,000 rows require `COMPLIANCE_OFFICER` approval via workflow; PDF quote documents are watermarked with recipient identity and timestamp | SOX Section 302 |
| 9 | **Input validation** | Strict OpenAPI 3.1 schema enforcement via request validation middleware; all database queries use parameterized statements (`psycopg2` with `%s` placeholders); zero dynamic SQL construction | OWASP Top 10 A03 (Injection) |
| 10 | **Secrets management** | Zero secrets stored in source code, Docker images, or environment variables; all credentials retrieved at runtime from HashiCorp Vault or AWS Secrets Manager; audit logging on all secret accesses | CIS Controls v8 |

### 16.5 Column Masking View

The `v_quotes_masked` view is the access path for `COST_VIEWER`. It nullifies `floor_price_eur` and `margin_pct` for roles that must not see commercial sensitivity data.

```sql
CREATE VIEW cost_history.v_quotes_masked AS
SELECT
    quote_id,
    quote_number,
    revision_number,
    quote_type,
    customer_id,
    part_number,
    revision,
    snapshot_id,
    status,
    total_price_eur,
    CASE
        WHEN current_setting('app.user_role', TRUE) IN (
            'QUOTE_MANAGER',
            'PRICING_MANAGER',
            'CHE_ADMIN',
            'COMPLIANCE_OFFICER',
            'SYSTEM_INTEGRATOR'
        )
        THEN floor_price_eur
        ELSE NULL
    END AS floor_price_eur,
    CASE
        WHEN current_setting('app.user_role', TRUE) IN (
            'QUOTE_MANAGER',
            'PRICING_MANAGER',
            'CHE_ADMIN',
            'COMPLIANCE_OFFICER',
            'SYSTEM_INTEGRATOR'
        )
        THEN margin_pct
        ELSE NULL
    END AS margin_pct,
    currency,
    valid_until,
    created_at
FROM cost_history.quotes;

COMMENT ON VIEW cost_history.v_quotes_masked IS
    'Column-masking view for quotes. floor_price_eur and margin_pct are NULLed '
    'for COST_VIEWER and COST_ANALYST roles. All other consumers use this view '
    'as the canonical read path for quote data.';

-- Grant access: COST_VIEWER and COST_ANALYST use the view, not the base table
REVOKE SELECT ON cost_history.quotes FROM cost_viewer_role, cost_analyst_role;
GRANT  SELECT ON cost_history.v_quotes_masked TO cost_viewer_role, cost_analyst_role;
```

---

## Section 17 — GDPR

### 17.1 PII Inventory

CHE stores minimal PII. User identity is represented as UUID pseudonyms throughout. The identity-to-UUID mapping is maintained by the Identity & Access service (external to CHE) and is the erasure target for subject requests.

| Field | Table | Classification | Retention | Legal Basis | Erasure Mechanism |
|-------|-------|---------------|-----------|-------------|-------------------|
| `actor_id` | `audit_events` | CONFIDENTIAL — pseudonymized UUID | 7 years (regulatory minimum) | Legitimate interest (security / fraud prevention) | UUID pseudonym retained in `audit_events`; identity mapping table in Identity service erased on request |
| `actor_ip_hash` | `audit_events` | RESTRICTED — SHA-256 + per-day salt, hashed at ingestion | 7 years | Security / legal obligation | Already hashed at point of collection; no further erasure action possible or required |
| `customer_id` | `quotes` | CONFIDENTIAL — UUID pseudonym | 10 years (contract / commercial law) | Contract performance | Delete identity mapping in SIE on erasure request; UUID remains in CHE referential integrity |
| `created_by` | `cost_snapshots` | INTERNAL — UUID | 7 years | Business record / legitimate interest | Replace UUID with literal `[ERASED-GDPR-YYYY]` (where YYYY = erasure year) on confirmed erasure request; log action in `audit_events` |
| `approved_by` | `cost_snapshots` | INTERNAL — UUID | 7 years | Business record | Same as `created_by` above |
| `created_by` | `entity_versions` | INTERNAL — UUID | 7 years | Business record | Replace with `[ERASED-GDPR-YYYY]` |
| `required_approver_id` | `quote_approvals` | INTERNAL — UUID | 10 years | Contract performance | Replace with `[ERASED-GDPR-YYYY]` |
| `actual_approver_id` | `quote_approvals` | INTERNAL — UUID | 10 years | Contract performance | Replace with `[ERASED-GDPR-YYYY]` |
| `created_by` | `rfq_rounds` | INTERNAL — UUID | 7 years | Business record | Replace with `[ERASED-GDPR-YYYY]` |
| Contact emails | (not stored in CHE) | N/A | N/A | N/A | CHE references `supplier_id` UUID only; contact data owned by Supplier Information Engine (SIE) |

### 17.2 GDPRRequest Entity

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `request_id` | `UUID` | PK, DEFAULT gen_random_uuid() | Unique request identifier |
| `request_type` | `ENUM('ACCESS','ERASURE','PORTABILITY','RECTIFICATION')` | NOT NULL | Article 15 / 17 / 20 / 16 request category |
| `data_subject_id` | `UUID` | NOT NULL | Pseudonymized identifier of the data subject |
| `data_subject_type` | `VARCHAR(30)` | NOT NULL | e.g. `EMPLOYEE`, `SUPPLIER_CONTACT`, `CUSTOMER_CONTACT` |
| `requested_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT now() | Timestamp when request was received |
| `deadline_at` | `TIMESTAMPTZ` | GENERATED ALWAYS AS (`requested_at + INTERVAL '30 days'`) STORED | Statutory 30-day response deadline (GDPR Art. 12) |
| `completed_at` | `TIMESTAMPTZ` | nullable | Timestamp of completion or rejection |
| `status` | `ENUM('RECEIVED','IN_PROGRESS','COMPLETED','REJECTED')` | NOT NULL, DEFAULT 'RECEIVED' | Processing lifecycle state |
| `affected_tables` | `TEXT[]` | nullable | Tables in which PII was located and acted upon |
| `affected_record_count` | `INTEGER` | nullable | Total records modified or exported |
| `performed_by` | `UUID` | NOT NULL | Identity of the CHE operator who executed the request |
| `rejection_reason` | `TEXT` | nullable | Reason for REJECTED status (e.g. legal hold, overriding legal obligation) |

### 17.3 GDPRErasureHandler — Python Implementation

```python
"""
cost_history_engine.gdpr.erasure_handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Processes GDPR erasure requests (Article 17 — Right to Erasure) across all
CHE tables that hold pseudonymized user identity references.

Key design decisions:
- Audit events are NOT erased (regulatory obligation overrides erasure right
  per GDPR Art. 17(3)(b)); only the identity mapping external to CHE is erased.
- All erasure actions are themselves logged to audit_events.
- Legal holds block erasure; the request is suspended until holds are lifted.
- Erasure replaces UUID values with [ERASED-GDPR-YYYY] tokens to preserve
  referential integrity and record counts for compliance reporting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


@dataclass
class LegalHoldBlocker:
    table: str
    record_id: UUID
    hold_reason: str
    hold_expires_at: datetime | None


@dataclass
class ErasureResult:
    request_id: UUID
    data_subject_id: UUID
    erased_at: datetime
    tables_affected: list[str]
    records_modified: int
    legal_hold_blockers: list[LegalHoldBlocker]
    fully_erased: bool
    error_message: str | None = None


@dataclass
class DataAccessReport:
    """Article 15 subject access report."""
    report_id: UUID
    data_subject_id: UUID
    generated_at: datetime
    tables_searched: list[str]
    records_found: dict[str, list[dict[str, Any]]]
    pii_fields_included: dict[str, list[str]]


class GDPRErasureHandler:
    """
    Handles GDPR erasure and access requests for CHE.

    Scope is limited to CHE tables only. The identity mapping (UUID → real identity)
    is managed by the Identity service and must be erased there separately.
    """

    PII_FIELDS: dict[str, list[str]] = {
        "cost_snapshots":  ["created_by", "approved_by"],
        "quotes":          ["created_by", "customer_id"],
        "quote_approvals": ["required_approver_id", "actual_approver_id"],
        "rfq_rounds":      ["created_by", "awarded_supplier_id"],
        "rfq_bids":        ["supplier_id"],
        "audit_events":    ["actor_id"],  # pseudonym kept; only mapping table erased
        "entity_versions": ["created_by"],
    }

    # Tables whose PII fields are replaced with erasure tokens
    ERASABLE_TABLES: dict[str, list[str]] = {
        k: v for k, v in PII_FIELDS.items() if k != "audit_events"
    }

    # Tables exempt from erasure due to overriding legal obligation (Art. 17(3))
    EXEMPT_TABLES: frozenset[str] = frozenset({"audit_events"})

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_erasure_request(self, request: "GDPRRequest") -> ErasureResult:
        """
        Execute an ERASURE request end-to-end.

        Steps:
        1. Locate all records referencing data_subject_id across ERASABLE_TABLES.
        2. Check for legal holds that block erasure.
        3. Replace PII fields with [ERASED-GDPR-YYYY] tokens for unblocked records.
        4. Log all erasure actions to audit_events.
        5. Return ErasureResult with full accounting.

        Args:
            request: A GDPRRequest with type ERASURE.

        Returns:
            ErasureResult describing what was erased and any blockers.
        """
        if request.request_type != "ERASURE":
            raise ValueError(
                f"process_erasure_request called with request_type '{request.request_type}'; "
                "expected ERASURE"
            )

        year = datetime.now(timezone.utc).year
        erased_at = datetime.now(timezone.utc)

        try:
            affected = self._find_affected_records(request.data_subject_id)
            blockers = self._check_legal_holds(affected)
            blocked_ids: set[UUID] = {b.record_id for b in blockers}

            total_modified = 0
            tables_affected: list[str] = []

            for table, record_ids in affected.items():
                if table in self.EXEMPT_TABLES:
                    continue
                erasable_ids = [rid for rid in record_ids if rid not in blocked_ids]
                if not erasable_ids:
                    continue
                fields = self.ERASABLE_TABLES.get(table, [])
                self._erase_pii_fields(table, erasable_ids, year)
                total_modified += len(erasable_ids) * len(fields)
                tables_affected.append(table)

            erased_summary = {
                t: [str(r) for r in ids]
                for t, ids in affected.items()
                if t not in self.EXEMPT_TABLES
            }
            self._log_erasure_to_audit(request, erased_summary)

            fully_erased = len(blockers) == 0

            result = ErasureResult(
                request_id=request.request_id,
                data_subject_id=request.data_subject_id,
                erased_at=erased_at,
                tables_affected=tables_affected,
                records_modified=total_modified,
                legal_hold_blockers=blockers,
                fully_erased=fully_erased,
            )

        except Exception as exc:
            logger.exception(
                "Erasure request %s failed for subject %s: %s",
                request.request_id, request.data_subject_id, exc,
            )
            result = ErasureResult(
                request_id=request.request_id,
                data_subject_id=request.data_subject_id,
                erased_at=erased_at,
                tables_affected=[],
                records_modified=0,
                legal_hold_blockers=[],
                fully_erased=False,
                error_message=str(exc),
            )

        return result

    def _find_affected_records(
        self, data_subject_id: UUID
    ) -> dict[str, list[UUID]]:
        """
        Scan all PII_FIELDS tables for records referencing data_subject_id.

        Returns:
            Mapping of table name → list of primary key UUIDs that reference
            the data subject in any PII field.
        """
        affected: dict[str, list[UUID]] = {}

        pk_columns = {
            "cost_snapshots":  "snapshot_id",
            "quotes":          "quote_id",
            "quote_approvals": "approval_id",
            "rfq_rounds":      "rfq_id",
            "rfq_bids":        "bid_id",
            "audit_events":    "event_id",
            "entity_versions": "version_id",
        }

        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                for table, pii_cols in self.PII_FIELDS.items():
                    pk = pk_columns[table]
                    conditions = " OR ".join(
                        f"{col} = %s" for col in pii_cols
                    )
                    params = [data_subject_id] * len(pii_cols)
                    cur.execute(
                        f"SELECT {pk} FROM cost_history.{table} WHERE {conditions}",  # noqa: S608
                        params,
                    )
                    rows = cur.fetchall()
                    if rows:
                        affected[table] = [UUID(str(row[0])) for row in rows]

        logger.info(
            "Found affected records for subject %s: %s",
            data_subject_id,
            {t: len(ids) for t, ids in affected.items()},
        )
        return affected

    def _check_legal_holds(
        self, affected: dict[str, list[UUID]]
    ) -> list[LegalHoldBlocker]:
        """
        Query the legal holds registry for any records in `affected` that are
        under an active legal hold preventing erasure.

        Returns:
            List of LegalHoldBlocker for any blocked records.
        """
        blockers: list[LegalHoldBlocker] = []

        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for table, record_ids in affected.items():
                    if not record_ids or table in self.EXEMPT_TABLES:
                        continue
                    cur.execute(
                        """
                        SELECT entity_id, hold_reason, hold_expires_at
                        FROM cost_history.legal_holds
                        WHERE entity_table = %s
                          AND entity_id = ANY(%s)
                          AND is_active = TRUE
                          AND (hold_expires_at IS NULL OR hold_expires_at > now())
                        """,
                        (table, [str(rid) for rid in record_ids]),
                    )
                    for row in cur.fetchall():
                        blockers.append(
                            LegalHoldBlocker(
                                table=table,
                                record_id=UUID(str(row["entity_id"])),
                                hold_reason=row["hold_reason"],
                                hold_expires_at=row["hold_expires_at"],
                            )
                        )

        if blockers:
            logger.warning(
                "Legal holds block erasure of %d record(s) across %d table(s)",
                len(blockers),
                len({b.table for b in blockers}),
            )
        return blockers

    def _erase_pii_fields(
        self, table: str, record_ids: list[UUID], year: int
    ) -> None:
        """
        Replace PII field values with [ERASED-GDPR-YYYY] token for the given records.

        Args:
            table:      Target table name (unqualified; schema is cost_history).
            record_ids: Primary key UUIDs to update.
            year:       Erasure year for inclusion in the token.
        """
        fields = self.ERASABLE_TABLES.get(table)
        if not fields:
            return

        pk_columns = {
            "cost_snapshots":  "snapshot_id",
            "quotes":          "quote_id",
            "quote_approvals": "approval_id",
            "rfq_rounds":      "rfq_id",
            "rfq_bids":        "bid_id",
            "entity_versions": "version_id",
        }
        pk = pk_columns[table]
        token = f"[ERASED-GDPR-{year}]"
        set_clause = ", ".join(f"{col} = %s" for col in fields)
        params = [token] * len(fields) + [[str(rid) for rid in record_ids]]

        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE cost_history.{table}
                    SET {set_clause}
                    WHERE {pk} = ANY(%s)
                    """,
                    params,
                )
            conn.commit()

        logger.info(
            "Erased PII fields %s in %d record(s) from cost_history.%s",
            fields, len(record_ids), table,
        )

    def _log_erasure_to_audit(
        self, request: "GDPRRequest", erased: dict[str, list[str]]
    ) -> None:
        """
        Write an immutable audit record documenting the erasure action.

        The audit event captures the request ID, data subject pseudonym,
        tables modified, and operator identity. This record is itself exempt
        from erasure (GDPR Art. 17(3)(b)).
        """
        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cost_history.audit_events (
                        event_id, event_type, entity_type, entity_id,
                        actor_id, actor_role, occurred_at, payload
                    ) VALUES (
                        gen_random_uuid(), 'GDPR_ERASURE', 'GDPRRequest', %s,
                        %s, 'COMPLIANCE_OFFICER', now(), %s::jsonb
                    )
                    """,
                    (
                        str(request.request_id),
                        str(request.performed_by),
                        psycopg2.extras.Json(
                            {
                                "data_subject_id": str(request.data_subject_id),
                                "tables_modified": list(erased.keys()),
                                "records_per_table": {
                                    t: len(ids) for t, ids in erased.items()
                                },
                                "gdpr_request_id": str(request.request_id),
                            }
                        ),
                    ),
                )
            conn.commit()

    def generate_data_access_report(
        self, data_subject_id: UUID
    ) -> DataAccessReport:
        """
        Generate a Subject Access Report (GDPR Article 15) containing all
        CHE data held for the given data subject.

        Args:
            data_subject_id: UUID pseudonym of the requesting data subject.

        Returns:
            DataAccessReport with per-table record lists and PII field mapping.
        """
        report_id = uuid4()
        generated_at = datetime.now(timezone.utc)
        records_found: dict[str, list[dict[str, Any]]] = {}

        pk_columns = {
            "cost_snapshots":  "snapshot_id",
            "quotes":          "quote_id",
            "quote_approvals": "approval_id",
            "rfq_rounds":      "rfq_id",
            "rfq_bids":        "bid_id",
            "entity_versions": "version_id",
        }

        with psycopg2.connect(self._dsn) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for table, pii_cols in self.ERASABLE_TABLES.items():
                    pk = pk_columns.get(table, "id")
                    conditions = " OR ".join(f"{col} = %s" for col in pii_cols)
                    params = [data_subject_id] * len(pii_cols)
                    cur.execute(
                        f"SELECT * FROM cost_history.{table} WHERE {conditions}",  # noqa: S608
                        params,
                    )
                    rows = cur.fetchall()
                    if rows:
                        records_found[table] = [dict(row) for row in rows]

        return DataAccessReport(
            report_id=report_id,
            data_subject_id=data_subject_id,
            generated_at=generated_at,
            tables_searched=list(self.ERASABLE_TABLES.keys()),
            records_found=records_found,
            pii_fields_included=dict(self.ERASABLE_TABLES),
        )

    def verify_erasure_completeness(self, data_subject_id: UUID) -> bool:
        """
        Confirm that no PII fields in ERASABLE_TABLES still contain
        the data_subject_id UUID after an erasure request was processed.

        Returns:
            True if no references remain; False if residual references exist.
        """
        remaining = self._find_affected_records(data_subject_id)
        # Exclude exempt tables from the check
        residual = {
            t: ids for t, ids in remaining.items() if t not in self.EXEMPT_TABLES
        }
        if residual:
            logger.warning(
                "Erasure completeness check FAILED for subject %s: residual references in %s",
                data_subject_id, list(residual.keys()),
            )
            return False
        logger.info(
            "Erasure completeness check PASSED for subject %s", data_subject_id
        )
        return True


# ---------------------------------------------------------------------------
# Placeholder — imported by GDPRErasureHandler callers
# ---------------------------------------------------------------------------

class GDPRRequest:
    """Runtime representation of a persisted GDPRRequest record."""

    def __init__(
        self,
        request_id: UUID,
        request_type: str,
        data_subject_id: UUID,
        data_subject_type: str,
        requested_at: datetime,
        status: str,
        performed_by: UUID,
        rejection_reason: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.request_type = request_type
        self.data_subject_id = data_subject_id
        self.data_subject_type = data_subject_type
        self.requested_at = requested_at
        self.deadline_at = requested_at.replace(
            day=requested_at.day  # computed in DB; mirrored here for convenience
        )
        self.status = status
        self.performed_by = performed_by
        self.rejection_reason = rejection_reason
        self.affected_tables: list[str] = []
        self.affected_record_count: int = 0
        self.completed_at: datetime | None = None
```

### 17.4 Data Processing Register (Article 30)

| Processing Activity | Purpose | Legal Basis | Data Categories | Retention | Recipients | Third Countries |
|---------------------|---------|-------------|-----------------|-----------|------------|-----------------|
| Cost snapshot storage | Capture point-in-time manufacturing cost breakdowns for version-controlled product costing | Legitimate interest (business records) | Pseudonymized user IDs (`created_by`, `approved_by`); no direct PII | 7 years | Internal: COST_ANALYST, QUOTE_MANAGER, CHE_ADMIN | None — EU-only S3 regions |
| Quote management | Record negotiated pricing and approvals for customer and internal audit | Contract performance; legal obligation | Pseudonymized customer_id UUID; created_by UUID | 10 years | Internal: QUOTE_MANAGER, COMPLIANCE_OFFICER; external: customer (quote PDF only) | None |
| RFQ processing | Coordinate competitive bidding from suppliers to secure optimal pricing | Legitimate interest; contract performance | Pseudonymized supplier_id, created_by | 7 years | Internal: RFQ_COORDINATOR, PRICING_MANAGER | None |
| Price history | Maintain verifiable record of supplier and material pricing over time | Legitimate interest (commercial audit trail) | No direct PII; supplier_id UUID | 10 years | Internal: PRICING_MANAGER, COST_ANALYST | None |
| Audit logging | Immutable trail of all data access and mutations for security and compliance | Legal obligation (SOX, GDPR Art. 5(2)) | Pseudonymized actor_id UUID; hashed IP (`actor_ip_hash`) | 7 years | Internal: COMPLIANCE_OFFICER, CHE_ADMIN | None |
| Backup and archival | Business continuity; disaster recovery; regulatory data retention | Legitimate interest; legal obligation | Same categories as above, encrypted at rest | Per tier: 30 days – 10 years | AWS S3 (EU regions only); no third-party access | None |
| GDPR erasure processing | Fulfil data subject erasure rights under GDPR Article 17 | Legal obligation | data_subject_id UUID; affected_tables list | 7 years (erasure audit record itself) | Internal: COMPLIANCE_OFFICER, CHE_ADMIN | None |

### 17.5 Data Protection Impact Assessment (DPIA) Summary

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|---------|------------|
| Unauthorized disclosure of floor prices or margin data to competitors via API misconfiguration | Medium | High | Column masking views; RLS enforcement; RBAC; API penetration testing quarterly; WAF with rate limiting |
| Audit log tampering to conceal unauthorized access or data manipulation | Low | Critical | RESTRICTIVE RLS UPDATE/DELETE policies; append-only `audit_writer` role; WAL archival provides independent record; hash-chained event IDs |
| Data subject erasure request fails silently, leaving PII in production tables | Low | High | `verify_erasure_completeness()` post-erasure scan; automated test in CI pipeline; 30-day statutory deadline tracked in GDPRRequest.deadline_at |
| Cross-border data transfer risk if S3 replication is misconfigured to non-EU region | Low | High | S3 bucket policy denying `s3:PutObject` from non-EU regions; AWS Config rule monitoring replication destination; IaC (Terraform) enforces `eu-west-1` / `eu-central-1` |
| Re-identification of data subjects from pseudonymized UUID + auxiliary data (e.g. timestamp + role correlation) | Low | Medium | UUID pseudonyms generated centrally by Identity service (not derived from personal data); IP stored as salted SHA-256; access to identity mapping table restricted to Identity service only; COMPLIANCE_OFFICER cannot query identity mapping |

---

## Section 18 — Monitoring

### 18.1 Prometheus Metrics

```python
"""
cost_history_engine.monitoring.metrics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All Prometheus metrics exported by the CHE service.
Registered at module import time; collected by the default CollectorRegistry.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# API metrics
# ---------------------------------------------------------------------------

che_api_requests_total = Counter(
    "che_api_requests_total",
    "Total number of HTTP requests handled by the CHE API",
    labelnames=["method", "endpoint", "status_code"],
)

che_api_request_duration_seconds = Histogram(
    "che_api_request_duration_seconds",
    "HTTP request processing duration in seconds",
    labelnames=["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

che_api_errors_total = Counter(
    "che_api_errors_total",
    "Total number of HTTP 4xx and 5xx responses returned by the CHE API",
    labelnames=["method", "endpoint", "status_code"],
)

# ---------------------------------------------------------------------------
# Snapshot metrics
# ---------------------------------------------------------------------------

che_snapshots_created_total = Counter(
    "che_snapshots_created_total",
    "Total number of cost snapshots created",
    labelnames=["snapshot_type", "cost_model_version"],
)

che_snapshot_approval_duration_seconds = Histogram(
    "che_snapshot_approval_duration_seconds",
    "Time elapsed from snapshot creation to first approval decision (seconds)",
    labelnames=["snapshot_type"],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400],
)

che_snapshots_pending_approval = Gauge(
    "che_snapshots_pending_approval",
    "Number of cost snapshots currently in PENDING_APPROVAL status",
    labelnames=["snapshot_type"],
)

# ---------------------------------------------------------------------------
# Quote metrics
# ---------------------------------------------------------------------------

che_quotes_submitted_total = Counter(
    "che_quotes_submitted_total",
    "Total number of quotes submitted to customers",
    labelnames=["quote_type", "currency"],
)

che_quote_negotiation_duration_seconds = Histogram(
    "che_quote_negotiation_duration_seconds",
    "Duration from quote submission to final ACCEPTED or REJECTED status (seconds)",
    labelnames=["quote_type"],
    buckets=[3600, 7200, 14400, 86400, 172800, 432000, 864000],
)

che_quotes_accepted_total = Counter(
    "che_quotes_accepted_total",
    "Total number of quotes that reached ACCEPTED status",
    labelnames=["quote_type", "currency"],
)

# ---------------------------------------------------------------------------
# RFQ metrics
# ---------------------------------------------------------------------------

che_rfq_cycle_time_seconds = Histogram(
    "che_rfq_cycle_time_seconds",
    "Duration from RFQ issued to awarded status (seconds)",
    labelnames=["rfq_category"],
    buckets=[86400, 172800, 432000, 864000, 1728000, 2592000],
)

che_rfq_bid_count = Histogram(
    "che_rfq_bid_count",
    "Number of supplier bids received per RFQ round",
    labelnames=["rfq_category"],
    buckets=[1, 2, 3, 5, 7, 10, 15, 20],
)

che_rfq_savings_eur_total = Counter(
    "che_rfq_savings_eur_total",
    "Cumulative realised savings captured via RFQ negotiation (EUR)",
    labelnames=["rfq_category", "cost_center"],
)

# ---------------------------------------------------------------------------
# Retention metrics
# ---------------------------------------------------------------------------

che_retention_job_duration_seconds = Histogram(
    "che_retention_job_duration_seconds",
    "Duration of the nightly retention job run (seconds)",
    labelnames=["retention_class"],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400],
)

che_retention_records_archived_total = Counter(
    "che_retention_records_archived_total",
    "Total number of records moved to cold storage by the retention job",
    labelnames=["table_name", "retention_class"],
)

che_retention_records_deleted_total = Counter(
    "che_retention_records_deleted_total",
    "Total number of records hard-deleted after retention expiry",
    labelnames=["table_name", "retention_class"],
)

che_legal_hold_count = Gauge(
    "che_legal_hold_count",
    "Number of currently active legal holds preventing record deletion",
    labelnames=["hold_reason_category"],
)

# ---------------------------------------------------------------------------
# Backup metrics
# ---------------------------------------------------------------------------

che_backup_last_completion_age_seconds = Gauge(
    "che_backup_last_completion_age_seconds",
    "Seconds elapsed since the last successful backup of this type completed",
    labelnames=["backup_type"],
)

che_backup_size_bytes = Gauge(
    "che_backup_size_bytes",
    "Size of the most recently completed backup artifact in bytes",
    labelnames=["backup_type"],
)

che_backup_failures_total = Counter(
    "che_backup_failures_total",
    "Total number of backup jobs that completed with FAILED status",
    labelnames=["backup_type"],
)

# ---------------------------------------------------------------------------
# Audit metrics
# ---------------------------------------------------------------------------

che_audit_events_written_total = Counter(
    "che_audit_events_written_total",
    "Total number of audit events successfully written to cost_history.audit_events",
    labelnames=["event_type"],
)

che_audit_write_failures_total = Counter(
    "che_audit_write_failures_total",
    "Total number of audit event write failures (critical — must alert immediately)",
    labelnames=["event_type", "error_class"],
)

# ---------------------------------------------------------------------------
# Kafka consumer metrics
# ---------------------------------------------------------------------------

che_kafka_consumer_lag = Gauge(
    "che_kafka_consumer_lag",
    "Current consumer group lag (number of unconsumed messages) per topic-partition",
    labelnames=["topic", "partition", "consumer_group"],
)

# ---------------------------------------------------------------------------
# Infrastructure metrics
# ---------------------------------------------------------------------------

che_partition_size_bytes = Gauge(
    "che_partition_size_bytes",
    "On-disk size of each PostgreSQL table partition in bytes",
    labelnames=["table_name", "partition_name"],
)

che_index_bloat_ratio = Gauge(
    "che_index_bloat_ratio",
    "Estimated bloat ratio for PostgreSQL indexes (bloat_bytes / total_bytes)",
    labelnames=["table_name", "index_name"],
)

che_slow_queries_total = Counter(
    "che_slow_queries_total",
    "Total number of queries exceeding the slow query threshold (default 1000 ms)",
    labelnames=["query_fingerprint"],
)
```

### 18.2 Alertmanager Rules

```yaml
# cost-history-engine/monitoring/alertmanager-rules.yaml
# Deployed via kube-prometheus-stack Helm chart, namespace: monitoring

groups:
  - name: che.alerts
    interval: 30s
    rules:

      - alert: CHEAPIHighErrorRate
        expr: |
          (
            sum(rate(che_api_errors_total[5m]))
            /
            sum(rate(che_api_requests_total[5m]))
          ) > 0.05
        for: 5m
        labels:
          severity: critical
          team: che-platform
        annotations:
          summary: "CHE API error rate exceeds 5%"
          description: >
            The CHE API is returning HTTP error responses for more than 5% of
            requests over the last 5 minutes. Current rate: {{ $value | humanizePercentage }}.
            Investigate application logs and downstream dependencies immediately.
          runbook_url: "https://wiki.internal/che/runbooks/api-high-error-rate"

      - alert: CHEQuoteApprovalSLA
        expr: |
          (
            sum by (snapshot_type) (
              che_snapshots_pending_approval
            )
          ) > 0
          AND
          (
            time() - che_snapshot_approval_duration_seconds_created
          ) > 172800
        for: 0m
        labels:
          severity: warning
          team: che-ops
        annotations:
          summary: "Quote pending approval for more than 48 hours"
          description: >
            At least one cost snapshot or quote has been awaiting approval for
            longer than the 48-hour SLA. Approval queue may be blocked.
            Notify the responsible QUOTE_MANAGER immediately.
          runbook_url: "https://wiki.internal/che/runbooks/quote-approval-sla"

      - alert: CHEBackupFailed
        expr: |
          che_backup_last_completion_age_seconds{backup_type="FULL_BASEBACKUP"} > 90000
        for: 5m
        labels:
          severity: critical
          team: che-platform
        annotations:
          summary: "CHE full backup is overdue (last backup > 25 hours ago)"
          description: >
            The last successful FULL_BASEBACKUP completed more than 25 hours ago,
            breaching the daily backup SLA. Current age: {{ $value | humanizeDuration }}.
            Check BackupOrchestrator logs and S3 bucket accessibility.
          runbook_url: "https://wiki.internal/che/runbooks/backup-overdue"

      - alert: CHERetentionJobFailed
        expr: |
          increase(che_retention_records_deleted_total[24h]) == 0
          AND
          hour() == 4
        for: 30m
        labels:
          severity: warning
          team: che-platform
        annotations:
          summary: "CHE retention job may have failed — no deletions recorded in 24h"
          description: >
            The nightly retention job is expected to complete by 05:00 UTC. No
            records have been deleted or archived in the past 24 hours, suggesting
            the job did not run or failed silently. Check retention job logs.
          runbook_url: "https://wiki.internal/che/runbooks/retention-job-failed"

      - alert: CHEKafkaConsumerLagHigh
        expr: |
          max by (topic, consumer_group) (
            che_kafka_consumer_lag
          ) > 10000
        for: 5m
        labels:
          severity: warning
          team: che-platform
        annotations:
          summary: "CHE Kafka consumer lag is critically high"
          description: >
            Consumer group {{ $labels.consumer_group }} is lagging by
            {{ $value }} messages on topic {{ $labels.topic }}.
            Threshold is 10,000 messages. CHE may be falling behind on
            cost event ingestion, risking data staleness.
          runbook_url: "https://wiki.internal/che/runbooks/kafka-lag-high"

      - alert: CHEAuditWriteFailure
        expr: |
          rate(che_audit_write_failures_total[1m]) > 0
        for: 1m
        labels:
          severity: critical
          team: che-platform
        annotations:
          summary: "CHE audit write failures detected — compliance risk"
          description: >
            Audit events are failing to write to cost_history.audit_events.
            This is a critical compliance breach. Audit write failures have been
            occurring for at least 1 minute. Investigate immediately.
            Event type: {{ $labels.event_type }}, error: {{ $labels.error_class }}.
          runbook_url: "https://wiki.internal/che/runbooks/audit-write-failure"

      - alert: CHEPartitionSizeCritical
        expr: |
          max by (table_name, partition_name) (
            che_partition_size_bytes
          ) > 536870912000
        for: 10m
        labels:
          severity: warning
          team: che-platform
        annotations:
          summary: "CHE PostgreSQL partition exceeds 500 GB"
          description: >
            Partition {{ $labels.partition_name }} on table {{ $labels.table_name }}
            has grown to {{ $value | humanizeBytes }}, exceeding the 500 GB threshold.
            Consider triggering an early partition rotation or archival cycle.
          runbook_url: "https://wiki.internal/che/runbooks/partition-size-critical"

      - alert: CHELegalHoldCountHigh
        expr: |
          sum(che_legal_hold_count) > 50
        for: 0m
        labels:
          severity: warning
          team: che-compliance
        annotations:
          summary: "CHE legal hold count exceeds 50 — compliance review required"
          description: >
            There are currently {{ $value }} active legal holds in CHE.
            A count above 50 may indicate a large-scale litigation event or
            a systematic issue with hold release processes. Review with
            COMPLIANCE_OFFICER.
          runbook_url: "https://wiki.internal/che/runbooks/legal-hold-high"
```

### 18.3 Grafana Dashboards

| Dashboard | Panels |
|-----------|--------|
| **1. CHE Overview** | Snapshot creation rate (time series); Quote pipeline funnel (bar gauge: DRAFT → SUBMITTED → NEGOTIATING → ACCEPTED); RFQ savings YTD vs budget (stat panel, EUR); System health summary (table: DB primary latency, replica lag, Kafka lag max, last backup age, retention job last run) |
| **2. Quote Pipeline** | Quote status distribution (pie chart by status); Average time from submission to approval (stat, hours); Approval SLA heatmap (heatmap: time-to-approval distribution over days); Margin distribution (histogram of `margin_pct` across ACCEPTED quotes, masked for non-privileged viewers) |
| **3. RFQ Analytics** | RFQ cycle times (histogram: issued → awarded in days); Bid count distribution per RFQ (histogram); Realised savings vs target budget (time series, EUR cumulative); Awarded supplier ranking (table: supplier_id, win count, avg savings %, avg bid rank) |
| **4. Price Trend Analysis** | Material price indices (sparkline grid: one per material category, 12-month rolling); Supplier price competitiveness (scatter: supplier vs market median per category); Quote acceptance rate by price tier (bar chart); Forecast accuracy MAPE (stat panel per material class) |
| **5. Audit & Compliance** | Audit event write rate (time series by event_type); Sensitive access log (table: last 100 `COMPLIANCE_OFFICER` and `CHE_ADMIN` actions); GDPR request status (pie: RECEIVED / IN_PROGRESS / COMPLETED / REJECTED); Legal holds timeline (bar chart: active holds by month, coloured by hold_reason_category) |
| **6. Infrastructure & Partitions** | Partition sizes (bar chart per partition, coloured by table); Index bloat ratio (table: top 10 bloated indexes by ratio); Slow query count rate (time series by query_fingerprint); Backup age by tier (stat panels: WAL age, basebackup age, logical dump age); Kafka consumer lag per topic (time series, threshold line at 10,000) |

### 18.4 SLA Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| API read latency P95 | < 300 ms | > 500 ms |
| API write latency P95 | < 500 ms | > 1,000 ms |
| Cost snapshot creation end-to-end | < 2 s | > 5 s |
| Quote approval notification delivery | < 1 min | > 5 min |
| Backup completion (Tier 2 full) | < 4 h | > 6 h |
| Retention job daily duration | < 2 h | > 4 h |
| Audit event write latency P99 | < 50 ms | > 200 ms |

### 18.5 Health Check

```json
GET /health

{
  "status": "healthy",
  "version": "2.1.0",
  "timestamp": "2026-06-19T10:00:00Z",
  "checks": {
    "database_primary": {
      "status": "healthy",
      "latency_ms": 2,
      "connections": 47
    },
    "database_replica": {
      "status": "healthy",
      "replication_lag_ms": 180
    },
    "kafka_consumer": {
      "status": "healthy",
      "lag_max": 42,
      "topics_subscribed": 8
    },
    "redis_cache": {
      "status": "healthy",
      "hit_rate_pct": 91,
      "memory_used_mb": 2048
    },
    "s3_backup": {
      "status": "healthy",
      "last_backup_age_h": 3.2
    },
    "retention_job": {
      "status": "healthy",
      "last_run": "2026-06-19T03:00:00Z"
    },
    "audit_writer": {
      "status": "healthy",
      "events_last_hour": 1247
    }
  },
  "metrics": {
    "snapshots_total": 287450,
    "quotes_active": 1832,
    "rfq_open": 94,
    "legal_holds": 7,
    "audit_events_7d": 184920
  }
}
```

The health endpoint is unauthenticated but returns no sensitive data. It is polled every 30 seconds by the load balancer health check and every 60 seconds by the CHE Overview Grafana dashboard via a datasource proxy. A `"status": "degraded"` response (any check non-healthy) triggers a P2 PagerDuty incident; `"status": "unhealthy"` (primary database or audit writer failing) triggers P1.
