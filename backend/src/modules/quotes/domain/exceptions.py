from ....exceptions import EntityNotFound
class QuoteNotFound(EntityNotFound):
    def __init__(self, qid) -> None: super().__init__("Quote", qid)
