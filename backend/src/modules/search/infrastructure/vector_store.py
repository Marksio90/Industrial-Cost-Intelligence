"""
Vector store abstraction — supports both pgvector (PostgreSQL) and Qdrant.

VectorStoreProtocol: structural interface (upsert / query / delete)
PgvectorStore:       SQL-native, same DB transaction as business data
QdrantStore:         dedicated vector DB, better for >10M vectors

Usage: inject via FastAPI Depends — configured by VECTOR_BACKEND env var.

pgvector query uses cosine distance operator <=> which is index-compatible
with HNSW (added in migration 003).  ef_search is session-scoped for tuning.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.models import SearchDomain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class VectorStoreProtocol(Protocol):

    @abstractmethod
    async def upsert(
        self,
        domain: SearchDomain,
        entity_id: UUID,
        tenant_id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
        model_name: str,
        model_version: str = "1.0",
    ) -> None: ...

    @abstractmethod
    async def query(
        self,
        domain: SearchDomain,
        query_vector: np.ndarray,
        tenant_id: str,
        top_k: int = 20,
        score_threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, domain: SearchDomain, entity_id: UUID, tenant_id: str) -> None: ...


# ---------------------------------------------------------------------------
# pgvector store
# ---------------------------------------------------------------------------

_DOMAIN_TABLE: dict[SearchDomain, str] = {
    SearchDomain.MATERIALS: "ici_vectors.material_embeddings",
    SearchDomain.SUPPLIERS: "ici_vectors.supplier_embeddings",
    SearchDomain.QUOTES:    "ici_vectors.rfq_embeddings",        # reuse rfq table for quotes
    SearchDomain.PROCESSES: "ici_vectors.material_embeddings",   # processes share 1024-dim space
}

_ENTITY_COL: dict[SearchDomain, str] = {
    SearchDomain.MATERIALS: "material_id",
    SearchDomain.SUPPLIERS: "supplier_id",
    SearchDomain.QUOTES:    "rfq_id",
    SearchDomain.PROCESSES: "material_id",  # stores process_id in material_id column
}

_DIMS: dict[SearchDomain, int] = {
    SearchDomain.MATERIALS: 1024,
    SearchDomain.SUPPLIERS: 768,
    SearchDomain.QUOTES:    1024,
    SearchDomain.PROCESSES: 1024,
}


class PgvectorStore:
    """
    Uses the existing ici_vectors.* tables (created in migration 003).
    ef_search: higher = better recall, lower = faster.
    Optimal range: 64-256 for production; 40 for low-latency paths.
    """

    DEFAULT_EF_SEARCH = 100

    def __init__(self, session: AsyncSession, ef_search: int = DEFAULT_EF_SEARCH) -> None:
        self._s = session
        self._ef = ef_search

    async def _set_ef_search(self) -> None:
        await self._s.execute(text(f"SET hnsw.ef_search = {self._ef}"))

    async def upsert(
        self,
        domain: SearchDomain,
        entity_id: UUID,
        tenant_id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
        model_name: str,
        model_version: str = "1.0",
    ) -> None:
        table   = _DOMAIN_TABLE[domain]
        id_col  = _ENTITY_COL[domain]
        vec_str = "[" + ",".join(f"{v:.8f}" for v in vector.tolist()) + "]"

        await self._s.execute(text(f"""
            INSERT INTO {table} (tenant_id, {id_col}, model_name, model_version, embedded_text, embedding)
            VALUES (:tenant_id, :entity_id, :model_name, :model_version,
                    :embedded_text, :embedding::vector)
            ON CONFLICT (tenant_id, {id_col}, model_name, model_version)
            DO UPDATE SET
                embedding    = EXCLUDED.embedding,
                embedded_text = EXCLUDED.embedded_text,
                updated_at   = NOW()
        """), {
            "tenant_id":    tenant_id,
            "entity_id":    entity_id,
            "model_name":   model_name,
            "model_version": model_version,
            "embedded_text": payload.get("embedded_text", ""),
            "embedding":    vec_str,
        })

    async def query(
        self,
        domain: SearchDomain,
        query_vector: np.ndarray,
        tenant_id: str,
        top_k: int = 20,
        score_threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        await self._set_ef_search()
        table  = _DOMAIN_TABLE[domain]
        id_col = _ENTITY_COL[domain]
        vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vector.tolist()) + "]"

        # 1 - cosine_distance = cosine_similarity
        sql = f"""
            SELECT
                {id_col}                        AS entity_id,
                1 - (embedding <=> :qvec::vector) AS score,
                embedded_text
            FROM {table}
            WHERE tenant_id = :tenant_id
              AND 1 - (embedding <=> :qvec::vector) >= :threshold
            ORDER BY embedding <=> :qvec::vector
            LIMIT :top_k
        """
        rows = (await self._s.execute(text(sql), {
            "qvec":      vec_str,
            "tenant_id": tenant_id,
            "threshold": score_threshold,
            "top_k":     top_k,
        })).mappings().all()

        return [{"entity_id": r["entity_id"], "score": float(r["score"]), "text": r["embedded_text"]} for r in rows]

    async def delete(self, domain: SearchDomain, entity_id: UUID, tenant_id: str) -> None:
        table  = _DOMAIN_TABLE[domain]
        id_col = _ENTITY_COL[domain]
        await self._s.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :t AND {id_col} = :e"),
            {"t": tenant_id, "e": entity_id},
        )


# ---------------------------------------------------------------------------
# Qdrant store (async client)
# ---------------------------------------------------------------------------

_QDRANT_COLLECTION: dict[SearchDomain, str] = {
    SearchDomain.MATERIALS: "ici_materials",
    SearchDomain.SUPPLIERS: "ici_suppliers",
    SearchDomain.QUOTES:    "ici_quotes",
    SearchDomain.PROCESSES: "ici_processes",
}


class QdrantStore:
    """
    Qdrant async client wrapper.
    Collection naming: ici_<domain>   (created by infrastructure setup job).
    Payload filter translates SearchQuery.filters to Qdrant FieldCondition.

    Qdrant HNSW config (set at collection creation):
        m=16, ef_construct=128, full_scan_threshold=20000, on_disk=false
    For >10M vectors per collection: on_disk=true, m=32.
    """

    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from qdrant_client import AsyncQdrantClient
                self._client = AsyncQdrantClient(url=self._url, api_key=self._api_key)
            except ImportError as e:
                raise RuntimeError("pip install qdrant-client") from e
        return self._client

    async def upsert(
        self,
        domain: SearchDomain,
        entity_id: UUID,
        tenant_id: str,
        vector: np.ndarray,
        payload: dict[str, Any],
        model_name: str,
        model_version: str = "1.0",
    ) -> None:
        from qdrant_client.models import PointStruct
        client = self._get_client()
        point  = PointStruct(
            id=str(entity_id),
            vector=vector.tolist(),
            payload={
                "tenant_id":    tenant_id,
                "model_name":   model_name,
                "model_version": model_version,
                **payload,
            },
        )
        await client.upsert(collection_name=_QDRANT_COLLECTION[domain], points=[point])

    async def query(
        self,
        domain: SearchDomain,
        query_vector: np.ndarray,
        tenant_id: str,
        top_k: int = 20,
        score_threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        client = self._get_client()

        # Always scope to tenant
        conditions = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
        if filters:
            for k, v in filters.items():
                conditions.append(FieldCondition(key=k, match=MatchValue(value=v)))

        results = await client.search(
            collection_name=_QDRANT_COLLECTION[domain],
            query_vector=query_vector.tolist(),
            query_filter=Filter(must=conditions),
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [{"entity_id": UUID(r.id), "score": r.score, "payload": r.payload} for r in results]

    async def delete(self, domain: SearchDomain, entity_id: UUID, tenant_id: str) -> None:
        from qdrant_client.models import PointIdsList
        client = self._get_client()
        await client.delete(
            collection_name=_QDRANT_COLLECTION[domain],
            points_selector=PointIdsList(points=[str(entity_id)]),
        )

    async def ensure_collections(self) -> None:
        """Idempotent collection creation with HNSW config."""
        from qdrant_client.models import Distance, HnswConfigDiff, OptimizersConfigDiff, VectorParams

        client = self._get_client()
        existing = {c.name for c in (await client.get_collections()).collections}

        configs = [
            (_QDRANT_COLLECTION[SearchDomain.MATERIALS], 1024, Distance.COSINE),
            (_QDRANT_COLLECTION[SearchDomain.SUPPLIERS], 768,  Distance.COSINE),
            (_QDRANT_COLLECTION[SearchDomain.QUOTES],    1024, Distance.COSINE),
            (_QDRANT_COLLECTION[SearchDomain.PROCESSES], 1024, Distance.COSINE),
        ]
        for name, dims, dist in configs:
            if name not in existing:
                await client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=dims, distance=dist),
                    hnsw_config=HnswConfigDiff(m=16, ef_construct=128, full_scan_threshold=20_000),
                    optimizers_config=OptimizersConfigDiff(indexing_threshold=10_000),
                )
                logger.info("Created Qdrant collection: %s (%d-dim)", name, dims)


# ---------------------------------------------------------------------------
# Factory — selects backend from VECTOR_BACKEND env var
# ---------------------------------------------------------------------------

def get_vector_store(session: AsyncSession) -> VectorStoreProtocol:
    """
    FastAPI dependency.
    VECTOR_BACKEND=pgvector  →  PgvectorStore (default, no extra infra)
    VECTOR_BACKEND=qdrant    →  QdrantStore
    """
    import os
    backend = os.getenv("VECTOR_BACKEND", "pgvector").lower()
    if backend == "qdrant":
        url     = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY")
        return QdrantStore(url=url, api_key=api_key)
    return PgvectorStore(session)
