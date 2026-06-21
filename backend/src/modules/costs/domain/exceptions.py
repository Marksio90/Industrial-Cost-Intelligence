from ....exceptions import EntityNotFound, BusinessRuleViolation


class CostBreakdownNotFound(EntityNotFound):
    def __init__(self, bid) -> None:
        super().__init__("CostBreakdown", bid)


class CostBreakdownAlreadyApproved(BusinessRuleViolation):
    def __init__(self) -> None:
        super().__init__("Cost breakdown is already approved")
