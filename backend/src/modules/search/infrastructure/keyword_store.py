"""
Keyword search backend — SQL + pg_trgm for BM25-approximate ranking.

Strategy:
  - pg_trgm similarity() gives word-overlap score in [0, 1].
  - word_similarity() is preferred for phrase-in-document matching.
  - We combine ts_rank (full-text FTS) with trigram similarity via
    a weighted sum, mimicking BM25 without an external index.

FTS vectors (tsvector) are stored as GENERATED columns or populated at
insert time via to_tsvector().  This file works on the existing schema
from migration 002 (no DDL changes needed) using dynamic expressions.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.models import SearchDomain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL templates per domain
# pg_trgm word_similarity: asymmetric, query term must appear in document.
# ts_rank: full-text ranking; uses indexed GIN tsvector where available.
# We normalise both to [0, 1] and combine with weights.
# ---------------------------------------------------------------------------

_KEYWORD_SQL: dict[SearchDomain, str] = {

    SearchDomain.MATERIALS: """
        SELECT
            m.id                                                AS entity_id,
            -- trigram component (word_similarity handles partial matches)
            (
                0.5 * word_similarity(:q, m.name)
              + 0.3 * word_similarity(:q, m.description)
              + 0.2 * word_similarity(:q, m.material_number)
            )                                                   AS trgm_score,
            -- full-text component
            COALESCE(
                ts_rank(
                    to_tsvector('english', m.name || ' ' || m.description || ' ' || m.material_number),
                    plainto_tsquery('english', :q)
                ), 0
            )                                                   AS fts_score,
            m.name, m.material_number, m.material_class::TEXT AS material_class,
            m.price_eur, m.lead_time_days, m.status::TEXT AS status
        FROM ici.materials m
        WHERE m.tenant_id  = :tenant_id
          AND m.is_deleted = FALSE
          AND m.status    != 'DEPRECATED'
          AND (
               word_similarity(:q, m.name)           > 0.2
            OR word_similarity(:q, m.material_number) > 0.3
            OR to_tsvector('english', m.name || ' ' || m.description)
                @@ plainto_tsquery('english', :q)
          )
        {filter_clause}
        ORDER BY trgm_score DESC
        LIMIT :top_k
    """,

    SearchDomain.SUPPLIERS: """
        SELECT
            s.id                                                AS entity_id,
            (
                0.6 * word_similarity(:q, s.name)
              + 0.2 * word_similarity(:q, s.code)
              + 0.2 * word_similarity(:q, s.city)
            )                                                   AS trgm_score,
            COALESCE(
                ts_rank(
                    to_tsvector('english', s.name || ' ' || s.city || ' ' || COALESCE(s.region, '')),
                    plainto_tsquery('english', :q)
                ), 0
            )                                                   AS fts_score,
            s.name, s.code, s.country_code, s.city,
            s.status::TEXT AS status, s.overall_score
        FROM ici.suppliers s
        WHERE s.tenant_id  = :tenant_id
          AND s.is_deleted = FALSE
          AND (
               word_similarity(:q, s.name) > 0.2
            OR word_similarity(:q, s.code) > 0.3
            OR to_tsvector('english', s.name || ' ' || s.city)
                @@ plainto_tsquery('english', :q)
          )
        {filter_clause}
        ORDER BY trgm_score DESC
        LIMIT :top_k
    """,

    SearchDomain.QUOTES: """
        SELECT
            q.id                                                AS entity_id,
            (
                0.5 * word_similarity(:q, q.quote_number)
              + 0.5 * word_similarity(:q, q.notes)
            )                                                   AS trgm_score,
            COALESCE(
                ts_rank(
                    to_tsvector('english', q.quote_number || ' ' || q.notes),
                    plainto_tsquery('english', :q)
                ), 0
            )                                                   AS fts_score,
            q.quote_number, q.status::TEXT AS status,
            q.validity_date, q.supplier_id, q.rfq_id
        FROM ici.quotes q
        WHERE q.tenant_id  = :tenant_id
          AND q.is_deleted = FALSE
          AND (
               word_similarity(:q, q.quote_number) > 0.3
            OR to_tsvector('english', q.quote_number || ' ' || q.notes)
                @@ plainto_tsquery('english', :q)
          )
        {filter_clause}
        ORDER BY trgm_score DESC
        LIMIT :top_k
    """,

    SearchDomain.PROCESSES: """
        SELECT
            p.id                                                AS entity_id,
            (
                0.5 * word_similarity(:q, p.name)
              + 0.3 * word_similarity(:q, p.description)
              + 0.2 * word_similarity(:q, p.code)
            )                                                   AS trgm_score,
            COALESCE(
                ts_rank(
                    to_tsvector('english', p.name || ' ' || p.description),
                    plainto_tsquery('english', :q)
                ), 0
            )                                                   AS fts_score,
            p.name, p.code, p.process_type::TEXT AS process_type,
            p.machine_rate_eur_hr, p.labor_rate_eur_hr, p.scrap_rate
        FROM ici.processes p
        WHERE p.tenant_id  = :tenant_id
          AND p.is_deleted = FALSE
          AND p.is_active  = TRUE
          AND (
               word_similarity(:q, p.name) > 0.2
            OR word_similarity(:q, p.code) > 0.3
            OR to_tsvector('english', p.name || ' ' || p.description)
                @@ plainto_tsquery('english', :q)
          )
        {filter_clause}
        ORDER BY trgm_score DESC
        LIMIT :top_k
    """,
}

# Domain-specific SQL filter fragments
_FILTER_FRAGMENTS: dict[str, dict[str, str]] = {
    "status": {
        SearchDomain.MATERIALS: "AND m.status = :status",
        SearchDomain.SUPPLIERS: "AND s.status = :status",
        SearchDomain.QUOTES:    "AND q.status = :status",
        SearchDomain.PROCESSES: "",
    },
    "material_class": {
        SearchDomain.MATERIALS: "AND m.material_class = :material_class",
    },
    "country_code": {
        SearchDomain.SUPPLIERS: "AND s.country_code = :country_code",
    },
    "max_price_eur": {
        SearchDomain.MATERIALS: "AND m.price_eur <= :max_price_eur",
    },
    "max_lead_time_days": {
        SearchDomain.MATERIALS: "AND m.lead_time_days <= :max_lead_time_days",
    },
}


def _build_filter_clause(domain: SearchDomain, filters: dict[str, Any]) -> tuple[str, dict]:
    """Returns (SQL fragment, extra params)."""
    clauses, extra = [], {}
    for key, value in filters.items():
        frag_map = _FILTER_FRAGMENTS.get(key, {})
        frag = frag_map.get(domain.value, frag_map.get(domain, ""))
        if frag:
            clauses.append(frag)
            extra[key] = value
    return "\n".join(clauses), extra


class KeywordStore:

    # Weights for combining trgm + fts scores into a single keyword score
    _TRGM_W = 0.65
    _FTS_W  = 0.35

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def query(
        self,
        domain: SearchDomain,
        query_text: str,
        tenant_id: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not query_text.strip():
            return []

        filter_clause, extra_params = _build_filter_clause(domain, filters or {})
        sql_template = _KEYWORD_SQL.get(domain)
        if not sql_template:
            return []

        sql = sql_template.format(filter_clause=filter_clause)

        params = {"q": query_text, "tenant_id": tenant_id, "top_k": top_k, **extra_params}
        rows = (await self._s.execute(text(sql), params)).mappings().all()

        results = []
        for r in rows:
            trgm = float(r.get("trgm_score") or 0)
            fts  = float(r.get("fts_score")  or 0)
            # Normalise fts (ts_rank can exceed 1 for multi-term queries)
            fts_norm = min(fts / 0.5, 1.0)
            score = self._TRGM_W * trgm + self._FTS_W * fts_norm
            results.append({
                "entity_id": r["entity_id"],
                "score":     score,
                "payload":   {k: v for k, v in dict(r).items() if k not in ("entity_id", "trgm_score", "fts_score")},
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
