"""
ICI Platform — Vector Seed Script

Loads material records from PostgreSQL, generates embeddings,
and upserts them into Qdrant for similarity search.

Usage (inside backend container):
    python -m scripts.seed_vectors [--collection ici_materials] [--batch 64] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import time
from typing import Any

# ── Logging ────────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed_vectors")


# ── Args ──────────────────────────────────────────────────────────────────────
def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed Qdrant with material embeddings")
    p.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "ici_materials"))
    p.add_argument("--batch",      type=int, default=64)
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--reset",      action="store_true", help="Drop and recreate collection")
    return p.parse_args()


# ── Embedder ──────────────────────────────────────────────────────────────────
class _Embedder:
    """Uses sentence-transformers if available, else deterministic mock (dev)."""

    def __init__(self) -> None:
        use_mock = os.environ.get("ML_USE_MOCK_EMBEDDER", "false").lower() == "true"
        self._dim = 384
        self._model = None

        if not use_mock:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
                log.info("Using SentenceTransformer all-MiniLM-L6-v2 (dim=384)")
            except ImportError:
                log.warning("sentence-transformers not installed — using mock embedder")

        if self._model is None:
            log.info("Using deterministic mock embedder (dim=384)")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is not None:
            vecs = self._model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        return [self._mock_embed(t) for t in texts]

    @staticmethod
    def _mock_embed(text: str) -> list[float]:
        """Reproducible pseudo-random unit vector from SHA-256 of text."""
        import struct, math
        h = hashlib.sha256(text.encode()).digest()
        dim = 384
        raw = []
        for i in range(0, min(dim * 4, len(h) * (dim * 4 // len(h) + 1)), 4):
            seed = h[i % len(h) : i % len(h) + 4]
            raw.append(struct.unpack(">f", seed.ljust(4, b"\x00"))[0])
        raw = (raw * (dim // len(raw) + 1))[:dim]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    @property
    def dim(self) -> int:
        return self._dim


# ── Text representation of a material ─────────────────────────────────────────
def _material_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("name", ""),
        row.get("material_class", ""),
        row.get("sub_class", ""),
        row.get("grade", ""),
        f"density {row.get('density_kg_m3', '')} kg/m3" if row.get("density_kg_m3") else "",
        f"unit {row.get('unit', '')}",
        row.get("description", ""),
    ]
    return " | ".join(p for p in parts if p).strip()


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    args = _parse()

    # ── Connect to PostgreSQL ──────────────────────────────────────────────────
    db_url = os.environ.get(
        "DATABASE_URL_SYNC",
        os.environ.get("DATABASE_URL", "postgresql://ici:ici@postgres:5432/ici")
    ).replace("+asyncpg", "")

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        log.info("Connected to PostgreSQL")
    except Exception as exc:
        log.error("Cannot connect to PostgreSQL: %s", exc)
        sys.exit(1)

    # ── Fetch materials ────────────────────────────────────────────────────────
    try:
        cur.execute("""
            SELECT id::text, tenant_id, code, name, material_class, sub_class,
                   grade, density_kg_m3, unit, description
            FROM ici.materials
            ORDER BY created_at
        """)
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    except Exception as exc:
        log.error("Cannot fetch materials: %s", exc)
        cur.execute("""
            SELECT id::text, 'tenant-demo' as tenant_id,
                   code, name, 'METAL' as material_class, '' as sub_class,
                   '' as grade, NULL as density_kg_m3, 'KG' as unit, '' as description
            FROM materials
            ORDER BY created_at
            LIMIT 1000
        """)
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

    log.info("Found %d material records", len(rows))

    if not rows:
        log.warning("No materials found — run seed SQL first: make seed")
        return

    # ── Connect to Qdrant ──────────────────────────────────────────────────────
    qdrant_host = os.environ.get("QDRANT_HOST", "qdrant")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    qdrant_key  = os.environ.get("QDRANT_API_KEY", "")
    collection  = args.collection

    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct
    except ImportError:
        log.error("qdrant-client not installed. Run: pip install qdrant-client")
        sys.exit(1)

    qc = QdrantClient(host=qdrant_host, port=qdrant_port,
                      api_key=qdrant_key or None, timeout=30)
    log.info("Connected to Qdrant at %s:%d", qdrant_host, qdrant_port)

    embedder = _Embedder()

    # ── Create / recreate collection ──────────────────────────────────────────
    existing = [c.name for c in qc.get_collections().collections]
    if collection in existing and args.reset:
        log.info("Dropping collection '%s'", collection)
        qc.delete_collection(collection)
        existing = []

    if collection not in existing:
        log.info("Creating collection '%s' (dim=%d, cosine)", collection, embedder.dim)
        if not args.dry_run:
            from qdrant_client.models import Distance, VectorParams
            qc.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=embedder.dim, distance=Distance.COSINE),
            )

    # ── Batch embed + upsert ──────────────────────────────────────────────────
    from qdrant_client.models import PointStruct

    total = 0
    batch_size = args.batch
    t0 = time.monotonic()

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [_material_text(r) for r in batch]
        vectors = embedder.embed(texts)

        points = [
            PointStruct(
                id=abs(hash(r["id"])) % (2**63),  # Qdrant needs uint64
                vector=vec,
                payload={
                    "material_id":    r["id"],
                    "tenant_id":      r["tenant_id"],
                    "code":           r.get("code", ""),
                    "name":           r.get("name", ""),
                    "material_class": r.get("material_class", ""),
                    "sub_class":      r.get("sub_class", ""),
                    "grade":          r.get("grade", ""),
                    "text":           texts[j],
                },
            )
            for j, (r, vec) in enumerate(zip(batch, vectors))
        ]

        if not args.dry_run:
            qc.upsert(collection_name=collection, points=points)
        total += len(points)
        log.info("  Upserted %d / %d", total, len(rows))

    elapsed = time.monotonic() - t0
    log.info("Done. %d vectors in Qdrant collection '%s' (%.1fs)", total, collection, elapsed)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
