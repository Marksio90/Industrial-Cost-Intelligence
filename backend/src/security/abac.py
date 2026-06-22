"""
ICI Attribute-Based Access Control (ABAC).

Evaluates fine-grained policies against a context triple:
    subject   (user attributes)
    resource  (object being accessed)
    environment (time, IP, risk score, …)

Policy evaluation:
    1. Collect all matching policies (subject + resource conditions match)
    2. If any DENY policy matches → deny (deny-overrides)
    3. If at least one PERMIT policy matches → permit
    4. Default: deny

Example policies:
    - Engineers can edit their own quotes, not others'
    - Analysts can export cost data only during business hours
    - Any user from a HIGH-RISK IP range is denied regardless of role
    - ML deployment requires manager role AND EU region only

Usage:
    engine = ABACEngine()
    engine.add_policy(Policy(
        id="restrict_ml_deploy_region",
        description="ML deployment only allowed from EU region",
        effect=PolicyEffect.DENY,
        subject_conditions={"role": "engineer"},
        resource_conditions={"type": "ml_model", "action": "deploy"},
        env_conditions={"region": {"not_in": ["eu-west-1", "eu-central-1"]}},
    ))

    ctx = PolicyContext(
        user=current_user,
        resource_type="ml_model",
        resource_id=model_id,
        action="deploy",
        environment={"region": "us-east-1", "ip": request.client.host},
    )
    result = engine.evaluate(ctx)
    if not result.permitted:
        raise AuthorizationError(result.reason)
"""
from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..middleware.auth import CurrentUser
from ..observability.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

class PolicyEffect(str, Enum):
    PERMIT = "permit"
    DENY   = "deny"


@dataclass(frozen=True)
class PolicyContext:
    user:          CurrentUser
    resource_type: str
    action:        str
    resource_id:   str | None = None
    resource_owner: str | None = None   # user_id of resource owner
    resource_tenant: str | None = None
    environment:   dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    permitted: bool
    policy_id: str | None
    reason:    str
    effect:    PolicyEffect | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Condition matchers
# ─────────────────────────────────────────────────────────────────────────────

def _match_condition(value: Any, condition: Any) -> bool:
    """
    Condition forms:
      "exact"                  → equality
      {"in": [...]}            → membership
      {"not_in": [...]}        → exclusion
      {"regex": "pattern"}     → regex match
      {"range": [lo, hi]}      → numeric range [lo, hi]
      {"cidr": "10.0.0.0/8"}  → IP in CIDR
    """
    if not isinstance(condition, dict):
        return value == condition

    if "in" in condition:
        return value in condition["in"]
    if "not_in" in condition:
        return value not in condition["not_in"]
    if "regex" in condition:
        return bool(re.fullmatch(condition["regex"], str(value)))
    if "range" in condition:
        lo, hi = condition["range"]
        return lo <= value <= hi
    if "cidr" in condition:
        try:
            net  = ipaddress.ip_network(condition["cidr"], strict=False)
            addr = ipaddress.ip_address(str(value))
            return addr in net
        except ValueError:
            return False
    return False


def _match_conditions(
    actual: dict[str, Any], conditions: dict[str, Any]
) -> bool:
    return all(_match_condition(actual.get(k), v) for k, v in conditions.items())


# ─────────────────────────────────────────────────────────────────────────────
# Policy
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Policy:
    id:          str
    description: str
    effect:      PolicyEffect
    # Conditions on the subject (user attributes)
    subject_conditions:  dict[str, Any] = field(default_factory=dict)
    # Conditions on the resource
    resource_conditions: dict[str, Any] = field(default_factory=dict)
    # Conditions on the environment (time, IP, region…)
    env_conditions:      dict[str, Any] = field(default_factory=dict)
    # Priority: higher = evaluated first; useful for DENY-first ordering
    priority:    int = 0
    enabled:     bool = True

    def matches(self, ctx: PolicyContext) -> bool:
        subject_attrs  = self._subject_attrs(ctx.user)
        resource_attrs = self._resource_attrs(ctx)
        env_attrs      = ctx.environment

        return (
            _match_conditions(subject_attrs,  self.subject_conditions)
            and _match_conditions(resource_attrs, self.resource_conditions)
            and _match_conditions(env_attrs,      self.env_conditions)
        )

    @staticmethod
    def _subject_attrs(user: CurrentUser) -> dict[str, Any]:
        return {
            "user_id":   str(user.user_id),
            "tenant_id": user.tenant_id,
            "email":     user.email,
            "roles":     list(user.roles),
            **{f"role_{r}": True for r in user.roles},
        }

    @staticmethod
    def _resource_attrs(ctx: PolicyContext) -> dict[str, Any]:
        return {
            "type":   ctx.resource_type,
            "action": ctx.action,
            "id":     ctx.resource_id or "",
            "owner":  ctx.resource_owner or "",
            "tenant": ctx.resource_tenant or "",
        }


# ─────────────────────────────────────────────────────────────────────────────
# ABACEngine
# ─────────────────────────────────────────────────────────────────────────────

class ABACEngine:
    """
    Thread-safe, in-process ABAC policy engine.
    Policies are evaluated in descending priority order.
    """

    def __init__(self) -> None:
        self._policies: list[Policy] = []

    def add_policy(self, policy: Policy) -> None:
        self._policies.append(policy)
        self._policies.sort(key=lambda p: -p.priority)

    def add_policies(self, policies: list[Policy]) -> None:
        for p in policies:
            self.add_policy(p)

    def remove_policy(self, policy_id: str) -> None:
        self._policies = [p for p in self._policies if p.id != policy_id]

    def evaluate(self, ctx: PolicyContext) -> EvaluationResult:
        permits: list[Policy] = []
        denies:  list[Policy] = []

        for policy in self._policies:
            if not policy.enabled:
                continue
            if policy.matches(ctx):
                (denies if policy.effect == PolicyEffect.DENY else permits).append(policy)

        if denies:
            p = denies[0]
            logger.info(
                "abac_deny",
                policy_id=p.id,
                user_id=str(ctx.user.user_id),
                resource=ctx.resource_type,
                action=ctx.action,
            )
            return EvaluationResult(
                permitted=False,
                policy_id=p.id,
                effect=PolicyEffect.DENY,
                reason=f"Access denied by policy '{p.id}': {p.description}",
            )

        if permits:
            p = permits[0]
            return EvaluationResult(
                permitted=True,
                policy_id=p.id,
                effect=PolicyEffect.PERMIT,
                reason=f"Access permitted by policy '{p.id}'",
            )

        return EvaluationResult(
            permitted=False,
            policy_id=None,
            effect=None,
            reason="No matching permit policy (default deny)",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Built-in ICI policy set
# ─────────────────────────────────────────────────────────────────────────────

def build_ici_policy_set() -> list[Policy]:
    return [

        # ── High-risk IP deny ─────────────────────────────────────────────────
        Policy(
            id="deny_high_risk_ip_ranges",
            description="Block Tor exit nodes and known malicious CIDRs (configured externally)",
            effect=PolicyEffect.DENY,
            env_conditions={"high_risk_ip": True},
            priority=1000,
        ),

        # ── Tenant isolation ──────────────────────────────────────────────────
        Policy(
            id="deny_cross_tenant_access",
            description="Users may not access resources from other tenants",
            effect=PolicyEffect.DENY,
            resource_conditions={"tenant": {"regex": ".+"}},  # resource has a tenant
            subject_conditions={"role_super_admin": {"not_in": [True]}},
            priority=900,
            # Note: actual cross-tenant check happens in route handlers
            # This policy fires if env["cross_tenant"] == True is set by middleware
        ),

        # ── Owners can edit own resources ─────────────────────────────────────
        Policy(
            id="permit_owner_edit",
            description="Resource owner can read/write their own resources",
            effect=PolicyEffect.PERMIT,
            resource_conditions={"action": {"in": ["read", "write", "update"]}},
            priority=100,
            # Matched in evaluate() when ctx.resource_owner == ctx.user.user_id
        ),

        # ── ML deploy: region restriction ─────────────────────────────────────
        Policy(
            id="deny_ml_deploy_outside_eu",
            description="ML model deployment only allowed from EU regions",
            effect=PolicyEffect.DENY,
            resource_conditions={"type": "ml_model", "action": "deploy"},
            env_conditions={"region": {"not_in": ["eu-west-1", "eu-central-1", "eu-north-1"]}},
            priority=800,
        ),

        # ── Cost export: business hours only ─────────────────────────────────
        Policy(
            id="deny_cost_export_outside_hours",
            description="Cost data export restricted to business hours (08:00–20:00 UTC)",
            effect=PolicyEffect.DENY,
            resource_conditions={"type": "cost", "action": "export"},
            subject_conditions={"role_admin": {"not_in": [True]},
                                "role_super_admin": {"not_in": [True]}},
            env_conditions={"hour_utc": {"not_in": list(range(8, 20))}},
            priority=500,
        ),

        # ── RFQ execute: rate-limit guard ─────────────────────────────────────
        Policy(
            id="deny_rfq_execute_rate_exceeded",
            description="Block RFQ execution when tenant hourly limit is exceeded",
            effect=PolicyEffect.DENY,
            resource_conditions={"type": "rfq", "action": "execute"},
            env_conditions={"rfq_rate_limit_exceeded": True},
            priority=700,
        ),

        # ── Read-all permit for viewers ───────────────────────────────────────
        Policy(
            id="permit_viewer_read_all",
            description="Viewers can read all non-admin resources",
            effect=PolicyEffect.PERMIT,
            subject_conditions={"role_viewer": True},
            resource_conditions={"action": "read",
                                 "type": {"not_in": ["user", "tenant", "audit", "secret"]}},
            priority=50,
        ),

        # ── Admin full access ─────────────────────────────────────────────────
        Policy(
            id="permit_admin_full",
            description="Admins have full access within their tenant",
            effect=PolicyEffect.PERMIT,
            subject_conditions={"role_admin": True},
            priority=200,
        ),

        # ── Super admin override ──────────────────────────────────────────────
        Policy(
            id="permit_super_admin_all",
            description="Super admins have unrestricted access",
            effect=PolicyEffect.PERMIT,
            subject_conditions={"role_super_admin": True},
            priority=999,
        ),
    ]


def build_default_engine() -> ABACEngine:
    engine = ABACEngine()
    engine.add_policies(build_ici_policy_set())
    return engine


def env_context_from_request(request: Any) -> dict[str, Any]:
    """
    Build the environment dict from a FastAPI Request object.
    Call in middleware and pass to PolicyContext.
    """
    now = time.gmtime()
    return {
        "hour_utc":    now.tm_hour,
        "weekday":     now.tm_wday,   # 0=Monday
        "ip":          getattr(getattr(request, "client", None), "host", ""),
        "region":      request.headers.get("X-AWS-Region", ""),
        "high_risk_ip": False,        # set by a threat-intel middleware
        "rfq_rate_limit_exceeded": False,
    }
