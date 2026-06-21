from ....exceptions import EntityNotFound, BusinessRuleViolation

class RFQNotFound(EntityNotFound):
    def __init__(self, rid) -> None: super().__init__("RFQ", rid)
