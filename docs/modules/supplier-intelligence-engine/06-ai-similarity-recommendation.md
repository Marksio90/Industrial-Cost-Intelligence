# Supplier Intelligence Engine — AI Layer, Similarity Search & Recommendation Engine

## 16. AI Layer

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      SIE AI Layer                            │
│                                                              │
│  ┌────────────────────┐   ┌──────────────────────────────┐  │
│  │  EmbeddingService  │   │  SupplierRecommendationEngine│  │
│  │  (text-embedding   │   │  (capability + score +       │  │
│  │   -3-small 1536d)  │   │   semantic + risk filters)   │  │
│  └────────┬───────────┘   └──────────────┬───────────────┘  │
│           │                              │                   │
│           ▼                              ▼                   │
│  ┌────────────────────┐   ┌──────────────────────────────┐  │
│  │  Vector Store      │   │  SimilaritySearchEngine      │  │
│  │  (pgvector HNSW)   │   │  (cosine + hybrid re-rank)   │  │
│  └────────────────────┘   └──────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  RAG Context Builder — supplier context for           │  │
│  │  Cost Calculation / RFQ / Risk Decisions              │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### SupplierEmbeddingService

```python
import openai
from dataclasses import dataclass
from typing import Optional
import json

@dataclass
class SupplierEmbeddingInput:
    supplier_id: str
    legal_name: str
    trade_name: Optional[str]
    supplier_type: str
    country_code: str
    strategic_tier: str
    composite_score: Optional[float]
    rating_class: Optional[str]
    # Capabilities
    process_classes: list[str]
    process_types: list[str]
    material_classes: list[str]
    # Certifications
    active_certs: list[str]
    # Categories
    category_names: list[str]
    # KPIs
    ppm: Optional[float]
    otd_pct: Optional[float]
    # Risk
    risk_class: Optional[str]
    country_risk_rating: Optional[str]

class SupplierEmbeddingService:
    MODEL = "text-embedding-3-small"
    DIMENSIONS = 1536
    BATCH_SIZE = 100

    def __init__(self, client: openai.OpenAI):
        self.client = client

    def build_text(self, inp: SupplierEmbeddingInput) -> str:
        parts = [
            f"Supplier: {inp.legal_name}",
        ]
        if inp.trade_name and inp.trade_name != inp.legal_name:
            parts.append(f"Trade name: {inp.trade_name}")
        parts.append(f"Type: {inp.supplier_type}")
        parts.append(f"Country: {inp.country_code}")
        parts.append(f"Tier: {inp.strategic_tier}")

        if inp.process_classes:
            parts.append(f"Processes: {', '.join(inp.process_classes)}")
        if inp.process_types:
            parts.append(f"Process types: {', '.join(inp.process_types[:10])}")
        if inp.material_classes:
            parts.append(f"Materials: {', '.join(inp.material_classes)}")
        if inp.active_certs:
            parts.append(f"Certifications: {', '.join(inp.active_certs)}")
        if inp.category_names:
            parts.append(f"Categories: {', '.join(inp.category_names[:5])}")
        if inp.composite_score is not None:
            parts.append(f"Score: {inp.composite_score:.1f} ({inp.rating_class})")
        if inp.ppm is not None:
            parts.append(f"Quality PPM: {inp.ppm:.0f}")
        if inp.otd_pct is not None:
            parts.append(f"On-Time Delivery: {inp.otd_pct:.1f}%")
        if inp.risk_class:
            parts.append(f"Risk: {inp.risk_class}")

        return ". ".join(parts)

    def generate(self, text: str) -> list[float]:
        response = self.client.embeddings.create(
            model=self.MODEL,
            input=text,
            dimensions=self.DIMENSIONS,
        )
        return response.data[0].embedding

    def generate_batch(self, texts: list[str]) -> list[list[float]]:
        results = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            response = self.client.embeddings.create(
                model=self.MODEL,
                input=batch,
                dimensions=self.DIMENSIONS,
            )
            results.extend([d.embedding for d in response.data])
        return results

    def upsert_embedding(self, conn, supplier_id: str, embedding: list[float], text: str):
        conn.execute("""
            INSERT INTO supplier_intelligence.supplier_embeddings
                (supplier_id, embedding_vector, model_name, embedding_text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (supplier_id) DO UPDATE
                SET embedding_vector = EXCLUDED.embedding_vector,
                    embedding_text   = EXCLUDED.embedding_text,
                    updated_at       = now()
        """, (supplier_id, embedding, self.MODEL, text))
```

### Embedding Refresh Consumer

```python
from kafka import KafkaConsumer
import json

REFRESH_TRIGGERS = {
    "sie.supplier.registered",
    "sie.supplier.status.changed",
    "sie.scorecard.updated",
    "sie.quality.ncr.closed",
    "sie.lead_time.changed",
}

class EmbeddingRefreshConsumer:
    def __init__(self, embedding_service: SupplierEmbeddingService, db_conn, kafka_config: dict):
        self.svc = embedding_service
        self.db  = db_conn
        self.consumer = KafkaConsumer(
            *REFRESH_TRIGGERS,
            **kafka_config,
            group_id="sie-embedding-refresh",
            value_deserializer=lambda v: json.loads(v.decode()),
        )

    def run(self):
        for msg in self.consumer:
            supplier_id = msg.value.get("supplier_id")
            if supplier_id:
                self._refresh(supplier_id)

    def _refresh(self, supplier_id: str):
        inp = self._load_embedding_input(supplier_id)
        text = self.svc.build_text(inp)
        embedding = self.svc.generate(text)
        self.svc.upsert_embedding(self.db, supplier_id, embedding, text)

        # Publish refresh event
        self.db.execute("""
            SELECT pg_notify('sie_embedding_refreshed', %s)
        """, (json.dumps({"supplier_id": supplier_id}),))

    def _load_embedding_input(self, supplier_id: str) -> SupplierEmbeddingInput:
        # Query: JOIN suppliers + capabilities + certifications + scorecards + risk_profiles
        raise NotImplementedError
```

---

## 17. Similarity Search

### SemanticSearchEngine

```python
from dataclasses import dataclass
from typing import Optional
import numpy as np

@dataclass
class SupplierSearchResult:
    supplier_id: str
    legal_name: str
    country_code: str
    supplier_type: str
    strategic_tier: str
    composite_score: Optional[float]
    rating_class: Optional[str]
    similarity_score: float
    matched_capabilities: list[str]

@dataclass
class SearchFilters:
    country_codes: Optional[list[str]] = None
    status_filter: Optional[list[str]] = None
    tier_filter: Optional[list[str]] = None
    category_id: Optional[str] = None
    min_composite_score: Optional[float] = None
    required_certs: Optional[list[str]] = None
    process_classes: Optional[list[str]] = None

class SemanticSearchEngine:
    def __init__(self, embedding_svc: SupplierEmbeddingService, db_conn):
        self.embedding_svc = embedding_svc
        self.db = db_conn

    def search(
        self,
        query: str,
        filters: SearchFilters,
        top_k: int = 10,
        min_similarity: float = 0.70,
    ) -> list[SupplierSearchResult]:
        query_vector = self.embedding_svc.generate(query)
        return self._vector_search(query_vector, filters, top_k, min_similarity)

    def _vector_search(
        self, vector, filters: SearchFilters, top_k: int, min_similarity: float
    ) -> list[SupplierSearchResult]:
        where_clauses = ["s.status = ANY(%(status)s)"]
        params = {
            "vector":      vector,
            "top_k":       top_k,
            "min_sim":     1 - min_similarity,
            "status":      filters.status_filter or ["APPROVED"],
        }

        if filters.country_codes:
            where_clauses.append("s.country_code = ANY(%(countries)s)")
            params["countries"] = filters.country_codes

        if filters.tier_filter:
            where_clauses.append("s.strategic_tier = ANY(%(tiers)s)")
            params["tiers"] = filters.tier_filter

        if filters.min_composite_score:
            where_clauses.append("s.composite_score >= %(min_score)s")
            params["min_score"] = filters.min_composite_score

        if filters.category_id:
            where_clauses.append("""
                EXISTS (SELECT 1 FROM supplier_intelligence.supplier_categories sc
                        WHERE sc.supplier_id = s.supplier_id AND sc.category_id = %(cat_id)s)
            """)
            params["cat_id"] = filters.category_id

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                s.supplier_id, s.legal_name, s.country_code,
                s.supplier_type, s.strategic_tier,
                s.composite_score, s.rating_class,
                1 - (se.embedding_vector <=> %(vector)s::vector) AS similarity
            FROM supplier_intelligence.supplier_embeddings se
            JOIN supplier_intelligence.suppliers s ON se.supplier_id = s.supplier_id
            WHERE {where_sql}
              AND (se.embedding_vector <=> %(vector)s::vector) <= %(min_sim)s
            ORDER BY se.embedding_vector <=> %(vector)s::vector
            LIMIT %(top_k)s
        """
        rows = self.db.execute(sql, params).fetchall()
        return [self._to_result(r) for r in rows]

    @staticmethod
    def _to_result(row) -> SupplierSearchResult:
        return SupplierSearchResult(
            supplier_id=row["supplier_id"],
            legal_name=row["legal_name"],
            country_code=row["country_code"],
            supplier_type=row["supplier_type"],
            strategic_tier=row["strategic_tier"],
            composite_score=row["composite_score"],
            rating_class=row["rating_class"],
            similarity_score=row["similarity"],
            matched_capabilities=[],
        )
```

### Hybrid Search (FTS + Semantic Re-ranking)

```python
class HybridSearchEngine:
    """
    Combines full-text search (BM25 via tsvector) with semantic vector search,
    then re-ranks with RRF (Reciprocal Rank Fusion).
    """
    RRF_K = 60

    def __init__(self, semantic_engine: SemanticSearchEngine, db_conn):
        self.semantic = semantic_engine
        self.db = db_conn

    def search(self, query: str, filters: SearchFilters, top_k: int = 20) -> list[SupplierSearchResult]:
        fts_results    = self._fts_search(query, filters, top_k * 2)
        vector_results = self.semantic.search(query, filters, top_k * 2, min_similarity=0.60)

        fts_ids    = [r.supplier_id for r in fts_results]
        vector_ids = [r.supplier_id for r in vector_results]

        rrf_scores: dict[str, float] = {}
        for rank, sid in enumerate(fts_ids):
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1.0 / (self.RRF_K + rank + 1)
        for rank, sid in enumerate(vector_ids):
            rrf_scores[sid] = rrf_scores.get(sid, 0) + 1.0 / (self.RRF_K + rank + 1)

        all_results = {r.supplier_id: r for r in fts_results + vector_results}
        ranked = sorted(rrf_scores.keys(), key=lambda sid: rrf_scores[sid], reverse=True)

        results = []
        for sid in ranked[:top_k]:
            r = all_results[sid]
            r.similarity_score = rrf_scores[sid]
            results.append(r)
        return results

    def _fts_search(self, query: str, filters: SearchFilters, top_k: int) -> list[SupplierSearchResult]:
        sql = """
            SELECT s.supplier_id, s.legal_name, s.country_code,
                   s.supplier_type, s.strategic_tier, s.composite_score, s.rating_class,
                   ts_rank_cd(s.search_vector, plainto_tsquery('english', %(q)s)) AS rank
            FROM supplier_intelligence.suppliers s
            WHERE s.search_vector @@ plainto_tsquery('english', %(q)s)
              AND s.status = ANY(%(status)s)
            ORDER BY rank DESC
            LIMIT %(limit)s
        """
        rows = self.db.execute(sql, {"q": query, "status": ["APPROVED"], "limit": top_k}).fetchall()
        return [SupplierSearchResult(
            supplier_id=r["supplier_id"], legal_name=r["legal_name"],
            country_code=r["country_code"], supplier_type=r["supplier_type"],
            strategic_tier=r["strategic_tier"], composite_score=r["composite_score"],
            rating_class=r["rating_class"], similarity_score=float(r["rank"]),
            matched_capabilities=[],
        ) for r in rows]
```

---

## 18. Recommendation Engine

### SupplierRecommendationEngine

```python
@dataclass
class RecommendationRequest:
    material_id: str
    process_class: Optional[str]    # CUT / MAC / FOR / JOI / ASS / FIN
    required_qty: Optional[float]
    required_certs: list[str]
    country_preference: Optional[str]
    max_risk_class: str             # LOW / MEDIUM / HIGH
    top_k: int = 5

@dataclass
class SupplierRecommendation:
    supplier_id: str
    legal_name: str
    country_code: str
    composite_score: float
    recommendation_score: float
    rank: int
    rationale: str
    strengths: list[str]
    risks: list[str]
    # Score breakdown
    capability_match: float
    scorecard_match: float
    semantic_match: float
    risk_filter_pass: bool

class SupplierRecommendationEngine:
    """
    Multi-criteria recommendation algorithm:
    1. Hard filters: capability, cert, risk class
    2. Soft scoring: scorecard + semantic similarity + price competitiveness
    3. Diversity bonus: avoid single-country concentration
    """

    WEIGHTS = {
        "scorecard":   0.40,   # composite score (quality+delivery+price+service+risk)
        "capability":  0.30,   # capability match score
        "semantic":    0.20,   # embedding similarity to ideal profile
        "diversity":   0.10,   # penalize if same country as top-3 results
    }

    def __init__(
        self,
        semantic_engine: SemanticSearchEngine,
        db_conn,
        embedding_svc: SupplierEmbeddingService,
    ):
        self.semantic = semantic_engine
        self.db = db_conn
        self.embedding_svc = embedding_svc

    def recommend(self, req: RecommendationRequest) -> list[SupplierRecommendation]:
        candidates = self._get_candidates(req)
        if not candidates:
            return []

        scored = [self._score_candidate(c, req) for c in candidates]
        scored = [s for s in scored if s is not None]

        # Diversity re-ranking
        scored = self._apply_diversity_bonus(scored)

        scored.sort(key=lambda x: x.recommendation_score, reverse=True)
        for i, rec in enumerate(scored[:req.top_k]):
            rec.rank = i + 1
        return scored[:req.top_k]

    def _get_candidates(self, req: RecommendationRequest) -> list[dict]:
        # Hard filter 1: approved suppliers with material mapping
        # Hard filter 2: required certifications
        # Hard filter 3: risk class <= max_risk_class
        sql = """
            SELECT s.supplier_id, s.legal_name, s.country_code,
                   s.composite_score, s.rating_class, s.strategic_tier,
                   rp.risk_class
            FROM supplier_intelligence.suppliers s
            JOIN supplier_intelligence.supplier_categories sm
                ON s.supplier_id = sm.supplier_id
            LEFT JOIN supplier_intelligence.risk_profiles rp
                ON s.supplier_id = rp.supplier_id
            WHERE s.status = 'APPROVED'
              AND sm.category_id = (
                  SELECT primary_category_id
                  FROM material_intelligence.materials
                  WHERE material_id = %(material_id)s
                  LIMIT 1
              )
              AND (%(max_risk)s = 'HIGH' OR rp.risk_class IS NULL
                   OR rp.risk_class <= %(max_risk)s)
        """
        rows = self.db.execute(sql, {
            "material_id": req.material_id,
            "max_risk": req.max_risk_class,
        }).fetchall()
        return [dict(r) for r in rows]

    def _score_candidate(self, candidate: dict, req: RecommendationRequest) -> Optional[SupplierRecommendation]:
        supplier_id = candidate["supplier_id"]

        # Cert check
        if req.required_certs:
            active_certs = self._get_active_certs(supplier_id)
            if not all(c in active_certs for c in req.required_certs):
                return None

        # Scorecard score (normalized 0-1)
        sc_score = (candidate.get("composite_score") or 0) / 100.0

        # Capability match
        cap_score = self._score_capability(supplier_id, req.process_class)

        # Semantic similarity to ideal query
        sem_score = self._score_semantic(supplier_id, req)

        reco_score = (
            sc_score  * self.WEIGHTS["scorecard"] +
            cap_score * self.WEIGHTS["capability"] +
            sem_score * self.WEIGHTS["semantic"]
        )

        strengths, risks = self._generate_narrative(candidate, sc_score, cap_score, req)

        return SupplierRecommendation(
            supplier_id=supplier_id,
            legal_name=candidate["legal_name"],
            country_code=candidate["country_code"],
            composite_score=candidate.get("composite_score") or 0,
            recommendation_score=round(reco_score * 100, 2),
            rank=0,
            rationale=self._build_rationale(candidate, sc_score, cap_score, sem_score),
            strengths=strengths,
            risks=risks,
            capability_match=round(cap_score * 100, 2),
            scorecard_match=round(sc_score * 100, 2),
            semantic_match=round(sem_score * 100, 2),
            risk_filter_pass=True,
        )

    def _score_capability(self, supplier_id: str, process_class: Optional[str]) -> float:
        if not process_class:
            return 0.7  # default if no process specified
        row = self.db.execute("""
            SELECT COUNT(*) AS cnt
            FROM supplier_intelligence.supplier_capabilities
            WHERE supplier_id = %(sid)s AND process_class = %(pc)s
        """, {"sid": supplier_id, "pc": process_class}).fetchone()
        return 1.0 if (row and row["cnt"] > 0) else 0.3

    def _score_semantic(self, supplier_id: str, req: RecommendationRequest) -> float:
        query = f"supplier for material {req.material_id}"
        if req.process_class:
            query += f" process {req.process_class}"
        if req.required_certs:
            query += f" certified {', '.join(req.required_certs)}"

        query_vector = self.embedding_svc.generate(query)
        row = self.db.execute("""
            SELECT 1 - (embedding_vector <=> %(vec)s::vector) AS similarity
            FROM supplier_intelligence.supplier_embeddings
            WHERE supplier_id = %(sid)s
        """, {"vec": query_vector, "sid": supplier_id}).fetchone()
        return float(row["similarity"]) if row else 0.5

    def _apply_diversity_bonus(self, results: list[SupplierRecommendation]) -> list[SupplierRecommendation]:
        country_counts: dict[str, int] = {}
        for r in sorted(results, key=lambda x: x.recommendation_score, reverse=True):
            count = country_counts.get(r.country_code, 0)
            if count >= 2:
                r.recommendation_score *= 0.90   # 10% penalty for 3rd+ supplier from same country
            country_counts[r.country_code] = count + 1
        return results

    def _get_active_certs(self, supplier_id: str) -> set[str]:
        rows = self.db.execute("""
            SELECT cert_type FROM supplier_intelligence.supplier_certifications
            WHERE supplier_id = %(sid)s
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND is_verified = TRUE
        """, {"sid": supplier_id}).fetchall()
        return {r["cert_type"] for r in rows}

    @staticmethod
    def _generate_narrative(candidate, sc_score, cap_score, req):
        strengths, risks = [], []
        composite = candidate.get("composite_score") or 0
        if composite >= 80:
            strengths.append(f"High composite score ({composite:.0f}/100)")
        elif composite >= 65:
            strengths.append(f"Good composite score ({composite:.0f}/100)")
        if cap_score >= 0.9:
            strengths.append(f"Confirmed capability for {req.process_class}")
        if candidate.get("strategic_tier") == "TIER1":
            strengths.append("Strategic TIER1 supplier — partnership framework")
        risk_class = candidate.get("risk_class")
        if risk_class in ("HIGH", "CRITICAL"):
            risks.append(f"Risk class: {risk_class} — review before awarding")
        if not req.country_preference and candidate["country_code"] not in ("DE","PL","CZ","SK","HU"):
            risks.append("Non-EU sourcing — consider tariff and logistics risk")
        return strengths, risks

    @staticmethod
    def _build_rationale(candidate, sc_score, cap_score, sem_score) -> str:
        composite = candidate.get("composite_score") or 0
        tier = candidate.get("strategic_tier", "SPOT")
        return (
            f"Score {composite:.0f}/100 ({tier}). "
            f"Scorecard match {sc_score*100:.0f}%, "
            f"capability match {cap_score*100:.0f}%, "
            f"semantic similarity {sem_score*100:.0f}%."
        )
```

### RAG Context Builder

```python
@dataclass
class SupplierContext:
    context_type: str   # cost_calculation / rfq / risk_decision
    supplier_id: str
    summary: str
    structured_data: dict
    tokens_estimate: int

class SupplierContextBuilder:
    MAX_TOKENS = 1200

    def build(self, supplier_id: str, context_type: str) -> SupplierContext:
        data = self._load(supplier_id)
        if context_type == "cost_calculation":
            return self._cost_context(supplier_id, data)
        elif context_type == "rfq":
            return self._rfq_context(supplier_id, data)
        elif context_type == "risk_decision":
            return self._risk_context(supplier_id, data)
        raise ValueError(f"Unknown context_type: {context_type}")

    def _cost_context(self, supplier_id: str, data: dict) -> SupplierContext:
        summary = (
            f"Supplier: {data['legal_name']} ({data['country_code']}), "
            f"Tier: {data['strategic_tier']}, "
            f"Score: {data.get('composite_score', 'N/A')}/100. "
            f"Active price offers: {data.get('active_price_count', 0)}. "
            f"PPM: {data.get('ppm', 'N/A')}, "
            f"OTD: {data.get('otd_12m', 'N/A')}%."
        )
        return SupplierContext(
            context_type="cost_calculation",
            supplier_id=supplier_id,
            summary=summary,
            structured_data={k: data[k] for k in ["composite_score", "ppm", "otd_12m", "risk_class"] if k in data},
            tokens_estimate=len(summary.split()) * 2,
        )

    def _rfq_context(self, supplier_id: str, data: dict) -> SupplierContext:
        certs = ", ".join(data.get("active_certs", []))
        caps  = ", ".join(data.get("process_classes", []))
        summary = (
            f"{data['legal_name']} — {data['supplier_type']}, {data['country_code']}. "
            f"Score {data.get('composite_score', 'N/A')}/100 ({data.get('rating_class', '?')}). "
            f"Certifications: {certs or 'None'}. "
            f"Capabilities: {caps or 'Not specified'}. "
            f"Lead time: {data.get('standard_lt_days', 'N/A')} days. "
            f"MOQ: {data.get('moq_qty', 'N/A')} {data.get('moq_unit', '')}."
        )
        return SupplierContext(
            context_type="rfq",
            supplier_id=supplier_id,
            summary=summary,
            structured_data=data,
            tokens_estimate=len(summary.split()) * 2,
        )

    def _risk_context(self, supplier_id: str, data: dict) -> SupplierContext:
        alerts = data.get("critical_alerts", 0)
        summary = (
            f"{data['legal_name']} — Risk class: {data.get('risk_class', 'UNKNOWN')}. "
            f"Financial zone: {data.get('altman_zone', 'UNKNOWN')}. "
            f"Country risk: {data.get('country_risk_rating', 'N/A')}. "
            f"Active critical alerts: {alerts}. "
            f"Single-source indicator: {data.get('is_single_source', False)}."
        )
        return SupplierContext(
            context_type="risk_decision",
            supplier_id=supplier_id,
            summary=summary,
            structured_data={k: data.get(k) for k in [
                "risk_class", "altman_zone", "duns_paydex", "country_risk_rating",
                "critical_alerts", "is_single_source",
            ]},
            tokens_estimate=len(summary.split()) * 2,
        )

    def _load(self, supplier_id: str) -> dict:
        raise NotImplementedError
```

### ML Feature Store

```python
SUPPLIER_QUALITY_FEATURES = {
    # Rolling window quality indicators
    "ppm_q1":              "float32",   # PPM last quarter
    "ppm_q4_avg":          "float32",   # Average PPM last 4 quarters
    "ppm_trend":           "float32",   # Slope of PPM over 4 quarters
    "ncr_count_q4":        "int32",     # NCR count last 4 quarters
    "ncr_critical_q4":     "int32",
    "ncr_open_overdue":    "int32",
    "open_8d_avg_days":    "float32",   # Average open 8D duration
    "cert_coverage_pct":   "float32",   # active / required
    "quality_score":       "float32",
}

SUPPLIER_DELIVERY_FEATURES = {
    "otd_q1":              "float32",
    "otd_q4_avg":          "float32",
    "otd_trend":           "float32",
    "otif_q1":             "float32",
    "avg_delay_days_q4":   "float32",
    "lead_time_reliability_q4": "float32",
    "delivery_score":      "float32",
}

SUPPLIER_RISK_FEATURES = {
    "altman_z_score":      "float32",
    "duns_paydex":         "float32",
    "duns_delinquency":    "float32",
    "days_beyond_terms":   "float32",
    "country_risk_score":  "float32",
    "hhi_index":           "float32",
    "is_single_source":    "bool",
    "active_critical_alerts": "int32",
    "financial_risk_score": "float32",
    "geopolitical_score":  "float32",
    "concentration_score": "float32",
}

SUPPLIER_PRICE_FEATURES = {
    "vs_benchmark_avg":    "float32",   # avg price vs benchmark last 4 quarters
    "price_trend_yoy":     "float32",
    "price_stability_cv":  "float32",
    "price_score":         "float32",
}

ALL_SCORECARD_FEATURES = {
    **SUPPLIER_QUALITY_FEATURES,
    **SUPPLIER_DELIVERY_FEATURES,
    **SUPPLIER_RISK_FEATURES,
    **SUPPLIER_PRICE_FEATURES,
}
# Total: 34 features
```

### Anomaly Detection (Supplier Performance)

```python
from sklearn.ensemble import IsolationForest
import numpy as np

class SupplierAnomalyDetector:
    """
    Detects anomalous suppliers (sudden score drops, unusual PPM spikes,
    delivery collapse patterns) using Isolation Forest.
    """
    CONTAMINATION = 0.05    # expected anomaly rate

    def __init__(self):
        self.model = IsolationForest(
            n_estimators=200,
            contamination=self.CONTAMINATION,
            random_state=42,
        )

    def train(self, feature_matrix: np.ndarray):
        self.model.fit(feature_matrix)

    def score(self, features: np.ndarray) -> np.ndarray:
        # Returns anomaly scores: lower = more anomalous (-1 = anomaly)
        return self.model.decision_function(features)

    def predict(self, features: np.ndarray) -> np.ndarray:
        # Returns -1 for anomaly, 1 for normal
        return self.model.predict(features)
```
