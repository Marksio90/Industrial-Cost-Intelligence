from ....exceptions import EntityNotFound, BusinessRuleViolation

class ProcessNotFound(EntityNotFound):
    def __init__(self, pid) -> None: super().__init__("Process", pid)

class ProcessCodeExists(BusinessRuleViolation):
    def __init__(self, code: str) -> None: super().__init__(f"Process code '{code}' exists")
