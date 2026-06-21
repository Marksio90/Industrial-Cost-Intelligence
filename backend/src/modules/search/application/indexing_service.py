"""
Indexing service — fetches domain entities from the DB, builds feature
representations, embeds them in batches, and upserts to the vector store.

Called by:
  - Background job on entity write (incremental)
  - Argo CronWorkflow for full re-index (model version change)
  - /admin/search/reindex endpoint (manual trigger)
"""
from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.models import (
    EmbeddingModel, MaterialFeatures, ProcessFeatures, QuoteFeatures,
    SearchDomain, SupplierFeatures,
)
from ..infrastructure.vector_store import VectorStoreProtocol
from .embedder import DomainIndexer, EmbedderRegistry

logger = logging.getLogger(__name__)


class SearchIndexingService:

    MODEL_VERSION = "1.0"

    def __init__(
        self,
        session: AsyncSession,
        vector_store: VectorStoreProtocol,
        use_mock: bool = False,
    ) -> None:
        self._s       = session
        self._vs      = vector_store
        self._mock    = use_mock

    # ----------------------------------------------------------------
    # Public: index a single entity (called after write)
    # ----------------------------------------------------------------

    async def index_material(self, material_id: UUID, tenant_id: str) -> None:
        row = await self._fetch_one("""
            SELECT m.id, m.material_number, m.name, m.description,
                   m.material_class::TEXT, m.base_unit,
                   m.price_eur, m.lead_time_days, m.density_g_cm3,
                   s.name AS supplier_name
            FROM ici.materials m
            LEFT JOIN ici.suppliers s ON s.id = m.supplier_id AND s.tenant_id = m.tenant_id
            WHERE m.id = :id AND m.tenant_id = :tid AND m.is_deleted = FALSE
        """, {"id": material_id, "tid": tenant_id})
        if not row:
            return
        feats = MaterialFeatures(
            material_number=row["material_number"], name=row["name"],
            description=row["description"] or "",
            material_class=row["material_class"], base_unit=row["base_unit"],
            price_eur=Decimal(str(row["price_eur"])), lead_time_days=row["lead_time_days"],
            density_g_cm3=Decimal(str(row["density_g_cm3"])) if row["density_g_cm3"] else None,
            supplier_name=row["supplier_name"],
        )
        embedder = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=self._mock)
        vec = await embedder.aembed_passages([feats.to_passage()])
        await self._vs.upsert(
            domain=SearchDomain.MATERIALS,
            entity_id=material_id,
            tenant_id=tenant_id,
            vector=vec[0],
            payload={"embedded_text": feats.to_passage(), "name": feats.name,
                     "material_number": feats.material_number, "price_eur": str(feats.price_eur)},
            model_name=EmbeddingModel.MULTILINGUAL_E5_LARGE.value,
            model_version=self.MODEL_VERSION,
        )

    async def index_supplier(self, supplier_id: UUID, tenant_id: str) -> None:
        row = await self._fetch_one("""
            SELECT id, code, name, country_code, city, status::TEXT AS status,
                   quality_score, delivery_score, overall_score
            FROM ici.suppliers
            WHERE id = :id AND tenant_id = :tid AND is_deleted = FALSE
        """, {"id": supplier_id, "tid": tenant_id})
        if not row:
            return
        feats = SupplierFeatures(
            code=row["code"], name=row["name"],
            country_code=row["country_code"], city=row["city"],
            status=row["status"],
            quality_score=Decimal(str(row["quality_score"])) if row["quality_score"] else None,
            delivery_score=Decimal(str(row["delivery_score"])) if row["delivery_score"] else None,
            overall_score=Decimal(str(row["overall_score"])) if row["overall_score"] else None,
            capability_text="",
        )
        embedder = EmbedderRegistry.get(EmbeddingModel.ALL_MPNET_BASE_V2, use_mock=self._mock)
        vec = await embedder.aembed_passages([feats.to_passage()])
        await self._vs.upsert(
            domain=SearchDomain.SUPPLIERS,
            entity_id=supplier_id,
            tenant_id=tenant_id,
            vector=vec[0],
            payload={"embedded_text": feats.to_passage(), "name": feats.name,
                     "code": feats.code, "country_code": feats.country_code,
                     "overall_score": str(feats.overall_score or "")},
            model_name=EmbeddingModel.ALL_MPNET_BASE_V2.value,
            model_version=self.MODEL_VERSION,
        )

    async def index_quote(self, quote_id: UUID, tenant_id: str) -> None:
        row = await self._fetch_one("""
            SELECT q.id, q.quote_number, q.status::TEXT AS status,
                   q.validity_date::TEXT, q.delivery_terms, q.payment_terms,
                   q.notes,
                   s.name AS supplier_name,
                   r.title AS rfq_title,
                   COALESCE(
                       STRING_AGG(li.description, '; '), ''
                   ) AS line_items
            FROM ici.quotes q
            LEFT JOIN ici.suppliers s ON s.id = q.supplier_id AND s.tenant_id = q.tenant_id
            LEFT JOIN ici.rfqs r      ON r.id = q.rfq_id
            LEFT JOIN ici.quote_line_items li ON li.quote_id = q.id
            WHERE q.id = :id AND q.tenant_id = :tid AND q.is_deleted = FALSE
            GROUP BY q.id, q.quote_number, q.status, q.validity_date,
                     q.delivery_terms, q.payment_terms, q.notes, s.name, r.title
        """, {"id": quote_id, "tid": tenant_id})
        if not row:
            return
        feats = QuoteFeatures(
            quote_number=row["quote_number"],
            rfq_title=row["rfq_title"] or "",
            supplier_name=row["supplier_name"] or "",
            validity_date=row["validity_date"] or "",
            status=row["status"],
            delivery_terms=row["delivery_terms"],
            payment_terms=row["payment_terms"],
            total_eur=Decimal("0"),
            line_item_descriptions=(row["line_items"] or "").split("; "),
            notes=row["notes"] or "",
        )
        embedder = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=self._mock)
        vec = await embedder.aembed_passages([feats.to_passage()])
        await self._vs.upsert(
            domain=SearchDomain.QUOTES,
            entity_id=quote_id,
            tenant_id=tenant_id,
            vector=vec[0],
            payload={"embedded_text": feats.to_passage(), "quote_number": feats.quote_number,
                     "supplier_name": feats.supplier_name, "status": feats.status},
            model_name=EmbeddingModel.MULTILINGUAL_E5_LARGE.value,
            model_version=self.MODEL_VERSION,
        )

    # ----------------------------------------------------------------
    # Full re-index (all entities for a tenant)
    # ----------------------------------------------------------------

    async def reindex_all_materials(self, tenant_id: str) -> int:
        rows = (await self._s.execute(text("""
            SELECT m.id, m.material_number, m.name, m.description,
                   m.material_class::TEXT, m.base_unit, m.price_eur,
                   m.lead_time_days, m.density_g_cm3, s.name AS supplier_name
            FROM ici.materials m
            LEFT JOIN ici.suppliers s ON s.id = m.supplier_id AND s.tenant_id = m.tenant_id
            WHERE m.tenant_id = :tid AND m.is_deleted = FALSE
        """), {"tid": tenant_id})).mappings().all()

        features = [MaterialFeatures(
            material_number=r["material_number"], name=r["name"],
            description=r["description"] or "", material_class=r["material_class"],
            base_unit=r["base_unit"], price_eur=Decimal(str(r["price_eur"])),
            lead_time_days=r["lead_time_days"],
            density_g_cm3=Decimal(str(r["density_g_cm3"])) if r["density_g_cm3"] else None,
            supplier_name=r["supplier_name"],
        ) for r in rows]

        ids = [r["id"] for r in rows]
        embedder = EmbedderRegistry.get(EmbeddingModel.MULTILINGUAL_E5_LARGE, use_mock=self._mock)
        indexer  = DomainIndexer(embedder)

        async def upsert_batch(batch_features, vectors):
            for i, (f, v) in enumerate(zip(batch_features, vectors)):
                eid = ids[features.index(f)] if f in features else None
                if eid:
                    await self._vs.upsert(
                        domain=SearchDomain.MATERIALS, entity_id=eid,
                        tenant_id=tenant_id, vector=v,
                        payload={"embedded_text": f.to_passage(), "name": f.name,
                                 "material_number": f.material_number},
                        model_name=EmbeddingModel.MULTILINGUAL_E5_LARGE.value,
                        model_version=self.MODEL_VERSION,
                    )

        return await indexer.index_batch(features, lambda f: f.to_passage(), upsert_batch)

    # ----------------------------------------------------------------
    # Internal helper
    # ----------------------------------------------------------------

    async def _fetch_one(self, sql: str, params: dict) -> dict | None:
        row = (await self._s.execute(text(sql), params)).mappings().first()
        return dict(row) if row else None
