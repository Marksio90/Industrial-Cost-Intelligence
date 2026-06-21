from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


# ── Domain Exceptions ─────────────────────────────────────────────────────────

class DomainError(Exception):
    """Base class for all domain errors."""
    code: str = "DOMAIN_ERROR"
    http_status: int = 422

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EntityNotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404

    def __init__(self, entity: str, entity_id: UUID | str) -> None:
        super().__init__(f"{entity} '{entity_id}' not found")
        self.entity = entity
        self.entity_id = entity_id


class DuplicateEntity(DomainError):
    code = "DUPLICATE"
    http_status = 409

    def __init__(self, entity: str, field: str, value: Any) -> None:
        super().__init__(f"{entity} with {field}='{value}' already exists")


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 422


class BusinessRuleViolation(DomainError):
    code = "BUSINESS_RULE_VIOLATION"
    http_status = 422


class InvariantViolation(DomainError):
    code = "INVARIANT_VIOLATION"
    http_status = 422


class AuthorizationError(DomainError):
    code = "FORBIDDEN"
    http_status = 403


class AuthenticationError(DomainError):
    code = "UNAUTHORIZED"
    http_status = 401


# ── Application Exceptions ────────────────────────────────────────────────────

class ApplicationError(Exception):
    code: str = "APPLICATION_ERROR"
    http_status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ExternalServiceError(ApplicationError):
    code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502


class ConfigurationError(ApplicationError):
    code = "CONFIGURATION_ERROR"
    http_status = 500
