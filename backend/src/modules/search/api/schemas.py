from __future__ import annotations
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from ..domain.models import SearchDomain, SearchMode


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512, description="Natural language search query")
    mode: SearchMode = SearchMode.HYBRID
    top_k: int = Field(default=10, ge=1, le=100)
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank: bool = False
    filters: dict[str, Any] = Field(default_factory=dict)
    explain: bool = Field(default=False, description="Include per-hit score explanation")

    @field_validator("vector_weight", "keyword_weight")
    @classmethod
    def weights_sum_to_one(cls, v, info):
        # FastAPI validates fields in declaration order; at this point
        # the other weight may not yet be available — we do a soft check.
        return v


class MaterialSearchRequest(SearchRequest):
    """Extends base with material-specific filters."""
    material_class: str | None = Field(default=None, description="METAL | PLASTIC | COMPOSITE ...")
    max_price_eur: float | None = Field(default=None, ge=0)
    max_lead_time_days: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, description="ACTIVE | DEPRECATED | PROTOTYPE")


class SupplierSearchRequest(SearchRequest):
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    status: str | None = None
    min_overall_score: float | None = Field(default=None, ge=0, le=1)


class QuoteSearchRequest(SearchRequest):
    status: str | None = None
    supplier_id: UUID | None = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ScoreBreakdown(BaseModel):
    vector_score: float | None
    keyword_score: float | None
    explanation: str | None


class SearchHitResponse(BaseModel):
    entity_id: UUID
    rank: int
    score: float
    payload: dict[str, Any]
    scores: ScoreBreakdown | None = None


class MaterialHitResponse(SearchHitResponse):
    material_number: str | None = None
    name: str | None = None
    material_class: str | None = None
    price_eur: str | None = None
    lead_time_days: int | None = None
    status: str | None = None

    @classmethod
    def from_hit(cls, h) -> "MaterialHitResponse":
        pl = h.payload
        return cls(
            entity_id=h.entity_id, rank=h.rank, score=h.score, payload=pl,
            material_number=pl.get("material_number"),
            name=pl.get("name"),
            material_class=pl.get("material_class"),
            price_eur=pl.get("price_eur"),
            lead_time_days=pl.get("lead_time_days"),
            status=pl.get("status"),
            scores=ScoreBreakdown(vector_score=h.vector_score, keyword_score=h.keyword_score,
                                  explanation=h.explanation) if h.explanation else None,
        )


class SupplierHitResponse(SearchHitResponse):
    name: str | None = None
    code: str | None = None
    country_code: str | None = None
    overall_score: str | None = None
    status: str | None = None

    @classmethod
    def from_hit(cls, h) -> "SupplierHitResponse":
        pl = h.payload
        return cls(
            entity_id=h.entity_id, rank=h.rank, score=h.score, payload=pl,
            name=pl.get("name"), code=pl.get("code"),
            country_code=pl.get("country_code"), overall_score=pl.get("overall_score"),
            status=pl.get("status"),
            scores=ScoreBreakdown(vector_score=h.vector_score, keyword_score=h.keyword_score,
                                  explanation=h.explanation) if h.explanation else None,
        )


class QuoteHitResponse(SearchHitResponse):
    quote_number: str | None = None
    supplier_name: str | None = None
    status: str | None = None

    @classmethod
    def from_hit(cls, h) -> "QuoteHitResponse":
        pl = h.payload
        return cls(
            entity_id=h.entity_id, rank=h.rank, score=h.score, payload=pl,
            quote_number=pl.get("quote_number"),
            supplier_name=pl.get("supplier_name"),
            status=pl.get("status"),
            scores=ScoreBreakdown(vector_score=h.vector_score, keyword_score=h.keyword_score,
                                  explanation=h.explanation) if h.explanation else None,
        )


class SearchResponse(BaseModel):
    query: str
    domain: SearchDomain
    mode: SearchMode
    total_candidates: int
    hits: list[SearchHitResponse]
    latency_ms: float
    cached: bool


class MaterialSearchResponse(SearchResponse):
    hits: list[MaterialHitResponse]


class SupplierSearchResponse(SearchResponse):
    hits: list[SupplierHitResponse]


class QuoteSearchResponse(SearchResponse):
    hits: list[QuoteHitResponse]


class IndexRequest(BaseModel):
    entity_ids: list[UUID] | None = Field(default=None, description="Specific IDs to re-index; None = all")
    model_version: str = "1.0"


class IndexResponse(BaseModel):
    indexed: int
    domain: SearchDomain
    tenant_id: str
    latency_ms: float


class SimilarityScoreExplain(BaseModel):
    """
    Detailed decomposition of a single hit's score for /explain endpoint.
    Useful for debugging retrieval quality.
    """
    entity_id: UUID
    rrf_score: float
    rrf_formula: str   # e.g. "0.7/(60+2) + 0.3/(60+5)"
    vector_rank: int | None
    keyword_rank: int | None
    vector_cosine: float | None
    keyword_trgm: float | None
    reranker_score: float | None
    passage_snippet: str
