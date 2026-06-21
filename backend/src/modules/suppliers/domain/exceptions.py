from ....exceptions import BusinessRuleViolation, EntityNotFound


class SupplierNotFound(EntityNotFound):
    def __init__(self, sid) -> None:
        super().__init__("Supplier", sid)


class SupplierCodeExists(BusinessRuleViolation):
    def __init__(self, code: str) -> None:
        super().__init__(f"Supplier code '{code}' already exists")
