from __future__ import annotations
from uuid import UUID
from ....exceptions import EntityNotFound, BusinessRuleViolation


class RiskNotFound(EntityNotFound):
    def __init__(self, risk_id: UUID) -> None:
        super().__init__(f"Risk {risk_id} not found")


class MitigationActionNotFound(EntityNotFound):
    def __init__(self, action_id: UUID) -> None:
        super().__init__(f"Mitigation action {action_id} not found")


class InvalidRiskTransition(BusinessRuleViolation):
    def __init__(self, risk_id: UUID, from_status: str, to_status: str) -> None:
        super().__init__(f"Cannot transition risk {risk_id} from {from_status} to {to_status}")
