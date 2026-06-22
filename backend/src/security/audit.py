"""
ICI Audit Logging — tamper-evident event log.

Design:
  - Every security-relevant action emits an AuditEvent
  - Events are written to: structlog (JSON), PostgreSQL audit table, Redis stream
  - Each event carries a SHA-256 chain hash of the previous event (per-tenant)
    making log tampering detectable
  - Sensitive fields are hashed (user IP → SHA-256), never stored in plain
  - Redis stream key: "ici:audit:{tenant_id}" with MAXLEN 100_000

Covered event types (AuditAction enum):
  Authentication: login, logout, token_refresh, login_failed, mfa_challenge
  Authorization:  permission_denied, privilege_escalation
  Data:           record_create/read/update/delete, bulk_export, data_decrypt
  Admin:          user_create/update/delete, role_change, tenant_config_change
  Security:       rate_limit_exceeded, suspicious_request, secret_accessed,
                  encryption_key_rotated

Usage:
    audit = AuditLogger(db_session, redis_client)

    await audit.log(AuditEvent(
        action    = AuditAction.RECORD_CREATE,
        actor_id  = str(current_user.user_id),
        tenant_id = current_user.tenant_id,
        resource_type = "rfq",
        resource_id   = str(rfq.id),
        outcome   = "success",
        metadata  = {"supplier_count": 5},
        request   = request,
    ))

    # Convenience decorator (logs entry + exit)
    @audited(AuditAction.RECORD_UPDATE, resource_type="cost")
    async def update_cost(cost_id: UUID, data: ..., current_user: ..., request: ...):
        ...
"""
from __future__ import annotations

import functools
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from ..observability.logging import get_logger
from ..observability.metrics import Counter

logger = get_logger(__name__)

AUDIT_EVENTS_TOTAL = Counter(
    "ici_audit_events_total",
    "Total audit events emitted",
    ["action", "outcome", "tenant_id"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────────────────────

class AuditAction(str, Enum):
    # Authentication
    LOGIN               = "auth.login"
    LOGOUT              = "auth.logout"
    TOKEN_REFRESH       = "auth.token_refresh"
    LOGIN_FAILED        = "auth.login_failed"
    MFA_CHALLENGE       = "auth.mfa_challenge"
    PASSWORD_RESET      = "auth.password_reset"
    ACCOUNT_LOCKED      = "auth.account_locked"

    # Authorization
    PERMISSION_DENIED    = "authz.permission_denied"
    PRIVILEGE_ESCALATION = "authz.privilege_escalation"
    IMPERSONATION        = "authz.impersonation"

    # Data operations
    RECORD_CREATE  = "data.create"
    RECORD_READ    = "data.read"
    RECORD_UPDATE  = "data.update"
    RECORD_DELETE  = "data.delete"
    BULK_EXPORT    = "data.bulk_export"
    DATA_DECRYPT   = "data.decrypt"

    # RFQ / procurement
    RFQ_EXECUTE    = "rfq.execute"
    RFQ_EMAIL_SENT = "rfq.email_sent"
    QUOTE_APPROVED = "rfq.quote_approved"

    # ML
    ML_MODEL_DEPLOYED  = "ml.model_deployed"
    ML_TRAINING_STARTED = "ml.training_started"

    # Administration
    USER_CREATE         = "admin.user_create"
    USER_UPDATE         = "admin.user_update"
    USER_DELETE         = "admin.user_delete"
    ROLE_CHANGE         = "admin.role_change"
    TENANT_CONFIG_CHANGE = "admin.tenant_config"

    # Security
    RATE_LIMIT_EXCEEDED   = "security.rate_limit"
    SUSPICIOUS_REQUEST    = "security.suspicious_request"
    SECRET_ACCESSED       = "security.secret_accessed"
    ENCRYPTION_KEY_ROTATED = "security.key_rotated"
    BRUTE_FORCE_DETECTED  = "security.brute_force"


# ─────────────────────────────────────────────────────────────────────────────
# AuditEvent
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    action:        AuditAction
    actor_id:      str                    # user UUID or "system"
    tenant_id:     str
    outcome:       str                    # "success" | "failure" | "attempt"

    resource_type: str    = ""
    resource_id:   str    = ""
    actor_email:   str    = ""
    actor_ip:      str    = ""            # will be hashed before storage
    actor_roles:   list[str] = field(default_factory=list)
    request_id:    str    = ""
    trace_id:      str    = ""
    user_agent:    str    = ""
    metadata:      dict[str, Any] = field(default_factory=dict)

    # Auto-populated
    event_id:      str    = field(default_factory=lambda: str(uuid.uuid4()))
    ts:            float  = field(default_factory=time.time)
    chain_hash:    str    = ""            # populated by AuditLogger

    def enrich_from_request(self, request: Any) -> "AuditEvent":
        """Populate actor_ip, user_agent, request_id from a FastAPI Request."""
        self.actor_ip   = _hash_ip(
            (getattr(request.headers, "get", lambda *_: "")(
                "X-Forwarded-For", ""
            ) or "").split(",")[0].strip()
            or getattr(getattr(request, "client", None), "host", "")
        )
        self.user_agent  = request.headers.get("User-Agent", "")[:512]
        self.request_id  = request.headers.get("X-Request-ID", "")
        self.trace_id    = request.headers.get("X-Trace-ID", "")
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


def _hash_ip(ip: str) -> str:
    """One-way hash so IPs are pseudonymised in the audit log (GDPR)."""
    if not ip:
        return ""
    return "sha256:" + hashlib.sha256(ip.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Chain hash (tamper evidence)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_chain_hash(event: AuditEvent, previous_hash: str) -> str:
    """
    SHA-256 of (previous_hash || event_id || action || actor_id || ts).
    Chaining makes it detectable if an event is inserted, deleted, or modified.
    """
    data = "|".join([
        previous_hash,
        event.event_id,
        event.action.value,
        event.actor_id,
        str(event.ts),
        event.tenant_id,
    ])
    return hashlib.sha256(data.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# AuditLogger
# ─────────────────────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Writes audit events to three sinks in parallel:
      1. structlog (synchronous — always available)
      2. Redis stream (best-effort)
      3. PostgreSQL audit_events table (best-effort)

    The chain hash state is kept per-tenant in Redis for fast access.
    Falls back to a genesis hash ("0" * 64) on Redis failure.
    """

    def __init__(
        self,
        redis: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._redis   = redis
        self._db_factory = db_session_factory

    async def log(self, event: AuditEvent) -> None:
        # Compute chain hash
        prev_hash = await self._get_previous_hash(event.tenant_id)
        event.chain_hash = _compute_chain_hash(event, prev_hash)

        # 1. structlog — synchronous, never fails
        log_fn = logger.warning if event.outcome == "failure" else logger.info
        log_fn("audit_event", **event.to_dict())

        AUDIT_EVENTS_TOTAL.labels(
            action=event.action.value,
            outcome=event.outcome,
            tenant_id=event.tenant_id,
        ).inc()

        # 2 & 3 — best-effort async
        import asyncio
        await asyncio.gather(
            self._write_redis(event),
            self._write_db(event),
            return_exceptions=True,
        )

    async def _get_previous_hash(self, tenant_id: str) -> str:
        if self._redis is None:
            return "0" * 64
        try:
            val = await self._redis.get(f"ici:audit:chain:{tenant_id}")
            return val or "0" * 64
        except Exception:
            return "0" * 64

    async def _write_redis(self, event: AuditEvent) -> None:
        if self._redis is None:
            return
        try:
            stream_key = f"ici:audit:{event.tenant_id}"
            chain_key  = f"ici:audit:chain:{event.tenant_id}"
            payload    = {k: str(v) for k, v in event.to_dict().items()
                          if not isinstance(v, dict)}
            payload["metadata"] = json.dumps(event.metadata)

            pipe = self._redis.pipeline()
            pipe.xadd(stream_key, payload, maxlen=100_000, approximate=True)
            pipe.set(chain_key, event.chain_hash, ex=86400 * 365)
            await pipe.execute()
        except Exception as exc:
            logger.warning("audit_redis_write_failed", error=str(exc))

    async def _write_db(self, event: AuditEvent) -> None:
        if self._db_factory is None:
            return
        try:
            from sqlalchemy import text
            async with self._db_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO audit_events (
                            event_id, action, actor_id, actor_email,
                            actor_ip, actor_roles, tenant_id, resource_type,
                            resource_id, outcome, request_id, trace_id,
                            user_agent, metadata, chain_hash, created_at
                        ) VALUES (
                            :event_id, :action, :actor_id, :actor_email,
                            :actor_ip, :actor_roles, :tenant_id, :resource_type,
                            :resource_id, :outcome, :request_id, :trace_id,
                            :user_agent, :metadata, :chain_hash, to_timestamp(:ts)
                        )
                    """),
                    {
                        **event.to_dict(),
                        "action":      event.action.value,
                        "actor_roles": json.dumps(event.actor_roles),
                        "metadata":    json.dumps(event.metadata),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.warning("audit_db_write_failed", error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# @audited decorator
# ─────────────────────────────────────────────────────────────────────────────

def audited(
    action: AuditAction,
    *,
    resource_type: str = "",
    resource_id_kwarg: str = "",  # name of kwarg that holds resource ID
    outcome_on_exception: str = "failure",
):
    """
    Decorator that wraps an async route handler with audit logging.

    The wrapped function must have a `current_user` and `request` parameter.

        @router.put("/costs/{cost_id}")
        @audited(AuditAction.RECORD_UPDATE, resource_type="cost", resource_id_kwarg="cost_id")
        async def update_cost(cost_id: UUID, current_user: CurrentUser, request: Request, ...):
    """
    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            user    = kwargs.get("current_user")
            request = kwargs.get("request")
            rid     = str(kwargs.get(resource_id_kwarg, "")) if resource_id_kwarg else ""

            event = AuditEvent(
                action        = action,
                actor_id      = str(user.user_id) if user else "unknown",
                actor_email   = getattr(user, "email", ""),
                actor_roles   = list(getattr(user, "roles", [])),
                tenant_id     = getattr(user, "tenant_id", "unknown"),
                resource_type = resource_type,
                resource_id   = rid,
                outcome       = "attempt",
            )
            if request:
                event.enrich_from_request(request)

            # Attempt — logged before the handler to capture even hard failures
            logger.debug("audit_attempt", action=action.value, resource_id=rid)

            try:
                result = await fn(*args, **kwargs)
                event.outcome = "success"
                return result
            except Exception as exc:
                event.outcome = outcome_on_exception
                event.metadata["exception"] = type(exc).__name__
                raise
            finally:
                # Best-effort — get audit logger from app state if available
                audit: AuditLogger | None = None
                if request:
                    audit = getattr(
                        getattr(request, "app", None),
                        "__audit_logger__",
                        None,
                    )
                if audit:
                    import asyncio
                    asyncio.create_task(audit.log(event))
                else:
                    logger.info("audit_event", **event.to_dict())

        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL DDL (run via Alembic migration)
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    action         TEXT        NOT NULL,
    actor_id       TEXT        NOT NULL,
    actor_email    TEXT        NOT NULL DEFAULT '',
    actor_ip       TEXT        NOT NULL DEFAULT '',   -- SHA-256 pseudonymised
    actor_roles    JSONB       NOT NULL DEFAULT '[]',
    tenant_id      TEXT        NOT NULL,
    resource_type  TEXT        NOT NULL DEFAULT '',
    resource_id    TEXT        NOT NULL DEFAULT '',
    outcome        TEXT        NOT NULL,              -- success | failure | attempt
    request_id     TEXT        NOT NULL DEFAULT '',
    trace_id       TEXT        NOT NULL DEFAULT '',
    user_agent     TEXT        NOT NULL DEFAULT '',
    metadata       JSONB       NOT NULL DEFAULT '{}',
    chain_hash     TEXT        NOT NULL DEFAULT '',   -- tamper-evidence chain
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common audit queries
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts   ON audit_events (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor        ON audit_events (actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action       ON audit_events (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource     ON audit_events (resource_type, resource_id);

-- Partition by month for large deployments (optional):
-- ALTER TABLE audit_events PARTITION BY RANGE (created_at);

-- Retention: delete events older than 2 years (run via pg_cron)
-- SELECT cron.schedule('audit_cleanup', '0 3 1 * *',
--   $$DELETE FROM audit_events WHERE created_at < now() - INTERVAL '2 years'$$);
"""
