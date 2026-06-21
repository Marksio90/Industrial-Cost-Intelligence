from ....exceptions import BusinessRuleViolation, InvariantViolation, EntityNotFound


class MaterialNotFound(EntityNotFound):
    def __init__(self, material_id) -> None:
        super().__init__("Material", material_id)


class MaterialNumberAlreadyExists(BusinessRuleViolation):
    def __init__(self, number: str) -> None:
        super().__init__(f"Material number '{number}' already exists")


class MaterialBusinessRuleViolation(BusinessRuleViolation):
    pass


class MaterialInvariantViolation(InvariantViolation):
    pass
