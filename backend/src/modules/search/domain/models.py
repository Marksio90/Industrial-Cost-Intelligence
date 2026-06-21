from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


class SearchDomain(StrEnum):
    MATERIALS = "materials"
    QUOTES    = "quotes"
    SUPPLIERS = "suppliers"
    PROCESSES = "processes"


class SearchMode(StrEnum):
    KEYWORD = "keyword"    # BM25 / trigram SQL only
    VECTOR  = "vector"     # pure ANN
    HYBRID  = "hybrid"     # RRF fusion of both


class EmbeddingModel(StrEnum):
    # Text models
    MULTILINGUAL_E5_LARGE  = "multilingual-e5-large"    # 1024-dim, primary
    ALL_MPNET_BASE_V2      = "all-mpnet-base-v2"        # 768-dim, supplier docs
    # Vision model
    RESNET50               = "resnet50"                  # 512-dim, CAD/images


# ---------------------------------------------------------------------------
# Feature schemas — the text templates used before embedding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaterialFeatures:
    """
    Canonical text representation of a material for embedding.
    Passage prefix for multilingual-e5-large asymmetric retrieval.
    """
    material_number: str
    name: str
    description: str
    material_class: str
    base_unit: str
    price_eur: Decimal
    lead_time_days: int
    density_g_cm3: Decimal | None
    supplier_name: str | None

    def to_passage(self) -> str:
        parts = [
            f"passage: Material {self.material_number}: {self.name}.",
            f"Class: {self.material_class}. Unit: {self.base_unit}.",
        ]
        if self.description:
            parts.append(self.description)
        if self.density_g_cm3:
            parts.append(f"Density {self.density_g_cm3} g/cm³.")
        parts.append(f"Price {self.price_eur} EUR/{self.base_unit}.")
        parts.append(f"Lead time {self.lead_time_days} days.")
        if self.supplier_name:
            parts.append(f"Preferred supplier: {self.supplier_name}.")
        return " ".join(parts)

    def to_sparse_tokens(self) -> list[str]:
        """Token bag for BM25/trigram matching."""
        tokens = [
            self.material_number.replace("-", " "),
            self.name,
            self.material_class.lower(),
            self.description,
        ]
        if self.supplier_name:
            tokens.append(self.supplier_name)
        return [t for t in tokens if t]


@dataclass(frozen=True)
class ProcessFeatures:
    process_code: str
    name: str
    process_type: str
    description: str
    machine_rate_eur_hr: Decimal
    labor_rate_eur_hr: Decimal
    cycle_time_seconds: Decimal
    scrap_rate: Decimal

    def to_passage(self) -> str:
        parts = [
            f"passage: Process {self.process_code}: {self.name}.",
            f"Type: {self.process_type}.",
        ]
        if self.description:
            parts.append(self.description)
        parts.append(
            f"Machine rate {self.machine_rate_eur_hr} EUR/hr, "
            f"labor rate {self.labor_rate_eur_hr} EUR/hr, "
            f"cycle time {self.cycle_time_seconds}s, "
            f"scrap rate {self.scrap_rate:.1%}."
        )
        return " ".join(parts)

    def to_sparse_tokens(self) -> list[str]:
        return [self.process_code.replace("-", " "), self.name, self.process_type.lower(), self.description]


@dataclass(frozen=True)
class QuoteFeatures:
    quote_number: str
    rfq_title: str
    supplier_name: str
    validity_date: str
    status: str
    delivery_terms: str
    payment_terms: str
    total_eur: Decimal
    line_item_descriptions: list[str]
    notes: str

    def to_passage(self) -> str:
        items = "; ".join(self.line_item_descriptions[:10])
        parts = [
            f"passage: Quote {self.quote_number} from {self.supplier_name}.",
            f"For RFQ: {self.rfq_title}.",
            f"Status: {self.status}. Valid until {self.validity_date}.",
            f"Total: {self.total_eur} EUR.",
            f"Delivery: {self.delivery_terms}. Payment: {self.payment_terms}.",
            f"Items: {items}.",
        ]
        if self.notes:
            parts.append(self.notes)
        return " ".join(parts)

    def to_sparse_tokens(self) -> list[str]:
        base = [self.quote_number, self.supplier_name, self.rfq_title, self.status.lower()]
        return base + self.line_item_descriptions


@dataclass(frozen=True)
class SupplierFeatures:
    code: str
    name: str
    country_code: str
    city: str
    status: str
    quality_score: Decimal | None
    delivery_score: Decimal | None
    overall_score: Decimal | None
    capability_text: str  # concatenated from supplier documents

    def to_passage(self) -> str:
        parts = [
            f"passage: Supplier {self.code}: {self.name}.",
            f"Location: {self.city}, {self.country_code}.",
            f"Status: {self.status}.",
        ]
        if self.overall_score:
            parts.append(
                f"Overall score {self.overall_score:.2f} "
                f"(quality {self.quality_score or 'N/A'}, "
                f"delivery {self.delivery_score or 'N/A'})."
            )
        if self.capability_text:
            parts.append(self.capability_text[:512])  # cap at 512 chars
        return " ".join(parts)

    def to_sparse_tokens(self) -> list[str]:
        return [self.code, self.name, self.city, self.country_code, self.capability_text]


# ---------------------------------------------------------------------------
# Search request / result models
# ---------------------------------------------------------------------------

@dataclass
class SearchQuery:
    query: str
    domain: SearchDomain
    mode: SearchMode = SearchMode.HYBRID
    tenant_id: str = ""
    top_k: int = 10
    vector_weight: float = 0.7   # α in RRF fusion
    keyword_weight: float = 0.3  # β in RRF fusion
    filters: dict[str, Any] = field(default_factory=dict)
    score_threshold: float = 0.0


@dataclass
class SearchHit:
    entity_id: UUID
    domain: SearchDomain
    score: float                  # final fused score [0, 1]
    vector_score: float | None    # cosine similarity
    keyword_score: float | None   # BM25-approximated via pg_trgm
    rank: int
    payload: dict[str, Any]       # domain-specific metadata
    explanation: str | None = None  # debug mode only


@dataclass
class SearchResult:
    query: str
    domain: SearchDomain
    mode: SearchMode
    hits: list[SearchHit]
    total_candidates: int
    latency_ms: float
    cached: bool = False
