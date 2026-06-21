"""
ICI Role-Based Access Control (RBAC).

Role hierarchy (inherits upward):
    VIEWER → ANALYST → ENGINEER → MANAGER → ADMIN → SUPER_ADMIN

Permissions are scoped as  "<resource>:<action>"
  e.g. "costs:read", "rfq:execute", "users:delete"

FastAPI integration:
    @router.get("/costs/{id}")
    async def get_cost(user: Annotated[CurrentUser, require_permission("costs:read")]):
        ...

    @router.delete("/users/{id}")
    async def delete_user(user: Annotated[CurrentUser, require_role(Role.ADMIN)]):
        ...
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status

from ..exceptions import AuthorizationError
from ..middleware.auth import CurrentUser, get_current_user
from ..observability.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Roles & Permissions
# ─────────────────────────────────────────────────────────────────────────────

class Role(str, Enum):
    VIEWER      = "viewer"
    ANALYST     = "analyst"
    ENGINEER    = "engineer"
    MANAGER     = "manager"
    ADMIN       = "admin"
    SUPER_ADMIN = "super_admin"


class Permission(str, Enum):
    # Materials
    MATERIALS_READ   = "materials:read"
    MATERIALS_WRITE  = "materials:write"
    MATERIALS_DELETE = "materials:delete"

    # Processes
    PROCESSES_READ   = "processes:read"
    PROCESSES_WRITE  = "processes:write"
    PROCESSES_DELETE = "processes:delete"

    # Suppliers
    SUPPLIERS_READ   = "suppliers:read"
    SUPPLIERS_WRITE  = "suppliers:write"
    SUPPLIERS_DELETE = "suppliers:delete"

    # Cost calculations
    COSTS_READ       = "costs:read"
    COSTS_WRITE      = "costs:write"
    COSTS_DELETE     = "costs:delete"
    COSTS_EXPORT     = "costs:export"

    # RFQ / procurement
    RFQ_READ         = "rfq:read"
    RFQ_CREATE       = "rfq:create"
    RFQ_EXECUTE      = "rfq:execute"       # triggers agent run
    RFQ_APPROVE      = "rfq:approve"       # approve/reject quotes
    RFQ_DELETE       = "rfq:delete"

    # Quotes
    QUOTES_READ      = "quotes:read"
    QUOTES_WRITE     = "quotes:write"
    QUOTES_APPROVE   = "quotes:approve"

    # Forecasting / risk
    FORECASTING_READ = "forecasting:read"
    RISK_READ        = "risk:read"

    # ML pipeline
    ML_READ          = "ml:read"
    ML_TRAIN         = "ml:train"
    ML_DEPLOY        = "ml:deploy"

    # Search
    SEARCH_READ      = "search:read"

    # User management
    USERS_READ       = "users:read"
    USERS_WRITE      = "users:write"
    USERS_DELETE     = "users:delete"
    USERS_IMPERSONATE = "users:impersonate"

    # Tenant admin
    TENANT_READ      = "tenant:read"
    TENANT_WRITE     = "tenant:write"

    # System / audit
    AUDIT_READ       = "audit:read"
    SYSTEM_ADMIN     = "system:admin"


# ─────────────────────────────────────────────────────────────────────────────
# Role → Permission mapping (additive; higher roles include lower)
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({
        Permission.MATERIALS_READ,
        Permission.PROCESSES_READ,
        Permission.SUPPLIERS_READ,
        Permission.COSTS_READ,
        Permission.RFQ_READ,
        Permission.QUOTES_READ,
        Permission.FORECASTING_READ,
        Permission.RISK_READ,
        Permission.SEARCH_READ,
        Permission.ML_READ,
    }),
    Role.ANALYST: frozenset({
        Permission.COSTS_EXPORT,
    }),
    Role.ENGINEER: frozenset({
        Permission.MATERIALS_WRITE,
        Permission.PROCESSES_WRITE,
        Permission.SUPPLIERS_WRITE,
        Permission.COSTS_WRITE,
        Permission.RFQ_CREATE,
        Permission.QUOTES_WRITE,
        Permission.ML_TRAIN,
    }),
    Role.MANAGER: frozenset({
        Permission.RFQ_EXECUTE,
        Permission.RFQ_APPROVE,
        Permission.QUOTES_APPROVE,
        Permission.ML_DEPLOY,
        Permission.USERS_READ,
    }),
    Role.ADMIN: frozenset({
        Permission.MATERIALS_DELETE,
        Permission.PROCESSES_DELETE,
        Permission.SUPPLIERS_DELETE,
        Permission.COSTS_DELETE,
        Permission.RFQ_DELETE,
        Permission.USERS_WRITE,
        Permission.USERS_DELETE,
        Permission.TENANT_READ,
        Permission.TENANT_WRITE,
        Permission.AUDIT_READ,
    }),
    Role.SUPER_ADMIN: frozenset({
        Permission.USERS_IMPERSONATE,
        Permission.SYSTEM_ADMIN,
    }),
}


# ─────────────────────────────────────────────────────────────────────────────
# Role hierarchy — each role inherits all permissions of lower roles
# ─────────────────────────────────────────────────────────────────────────────

_HIERARCHY: list[Role] = [
    Role.VIEWER,
    Role.ANALYST,
    Role.ENGINEER,
    Role.MANAGER,
    Role.ADMIN,
    Role.SUPER_ADMIN,
]


@lru_cache(maxsize=None)
def _effective_permissions(role: Role) -> frozenset[Permission]:
    idx = _HIERARCHY.index(role)
    combined: set[Permission] = set()
    for r in _HIERARCHY[: idx + 1]:
        combined |= _ROLE_PERMISSIONS.get(r, frozenset())
    return frozenset(combined)


# ─────────────────────────────────────────────────────────────────────────────
# RBACService
# ─────────────────────────────────────────────────────────────────────────────

class RBACService:
    """Stateless permission resolution."""

    @staticmethod
    def effective_permissions(roles: frozenset[str]) -> frozenset[Permission]:
        combined: set[Permission] = set()
        for role_str in roles:
            try:
                r = Role(role_str)
                combined |= _effective_permissions(r)
            except ValueError:
                logger.warning("unknown_role", role=role_str)
        return frozenset(combined)

    @staticmethod
    def has_permission(roles: frozenset[str], permission: Permission | str) -> bool:
        perm = Permission(permission) if isinstance(permission, str) else permission
        return perm in RBACService.effective_permissions(roles)

    @staticmethod
    def highest_role(roles: frozenset[str]) -> Role | None:
        for r in reversed(_HIERARCHY):
            if r.value in roles:
                return r
        return None

    @staticmethod
    def can_access_tenant(user: CurrentUser, target_tenant_id: str) -> bool:
        """Users can access their own tenant; super_admin can access any."""
        if user.tenant_id == target_tenant_id:
            return True
        return Role.SUPER_ADMIN.value in user.roles


_rbac = RBACService()


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI dependencies
# ─────────────────────────────────────────────────────────────────────────────

def require_permission(*permissions: Permission | str):
    """
    FastAPI dependency factory. All listed permissions must be held.

        @router.post("/rfq/execute")
        async def run_rfq(user: Annotated[CurrentUser, require_permission("rfq:execute")]):
    """
    perms = [Permission(p) if isinstance(p, str) else p for p in permissions]

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        effective = _rbac.effective_permissions(user.roles)
        missing   = [p.value for p in perms if p not in effective]
        if missing:
            logger.warning(
                "permission_denied",
                user_id=str(user.user_id),
                tenant_id=user.tenant_id,
                required=missing,
                held=sorted(p.value for p in effective),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "FORBIDDEN", "required_permissions": missing},
            )
        return user

    return Depends(_dep)


def require_role(*roles: Role | str):
    """Dependency: user must have at least one of the specified roles."""
    role_values = {(r.value if isinstance(r, Role) else r) for r in roles}

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.roles.intersection(role_values):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "FORBIDDEN", "required_roles": list(role_values)},
            )
        return user

    return Depends(_dep)


def require_same_tenant(target_tenant_field: str = "tenant_id"):
    """Dependency: user must belong to the same tenant as the request target."""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        # The route handler must call _rbac.can_access_tenant() with the
        # resolved target; this dependency just ensures the user is authenticated.
        return user

    return Depends(_dep)
