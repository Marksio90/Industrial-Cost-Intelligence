"""
ICI Security subsystem.

Layered defence:
  auth      → JWT issuance/validation, OAuth2 flows, token rotation
  rbac      → role hierarchy + permission registry + FastAPI deps
  abac      → attribute-based policy engine (resource × user × env)
  api       → request hardening middleware (headers, rate-limit, body-size)
  encryption→ field-level AES-GCM + Fernet envelope, key rotation helpers
  secrets   → runtime secret loading (env → Vault → K8s) with caching
  audit     → tamper-evident audit event log (structlog + DB + Redis stream)
"""
from .auth import (
    JWTService,
    OAuth2Service,
    create_access_token,
    create_refresh_token,
    decode_token,
    rotate_refresh_token,
)
from .rbac import (
    Permission,
    Role,
    RBACService,
    require_permission,
    require_role,
)
from .abac import ABACEngine, Policy, PolicyEffect, PolicyContext
from .audit import AuditLogger, AuditEvent, AuditAction
from .encryption import FieldEncryption, encrypt_field, decrypt_field
from .secrets import SecretsManager

__all__ = [
    # Auth
    "JWTService", "OAuth2Service",
    "create_access_token", "create_refresh_token",
    "decode_token", "rotate_refresh_token",
    # RBAC
    "Permission", "Role", "RBACService",
    "require_permission", "require_role",
    # ABAC
    "ABACEngine", "Policy", "PolicyEffect", "PolicyContext",
    # Audit
    "AuditLogger", "AuditEvent", "AuditAction",
    # Encryption
    "FieldEncryption", "encrypt_field", "decrypt_field",
    # Secrets
    "SecretsManager",
]
