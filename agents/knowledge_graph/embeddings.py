"""
Section 6 — Embedding Strategy

Pipeline:
  1. Property concatenation → text prompt per node
  2. SentenceTransformer encoding (all-MiniLM-L6-v2 → 384-dim, or custom 512-dim)
  3. Batch upsert into Neo4j vector index
  4. Incremental updates on node change events
  5. Similarity computation utilities
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from .models import NodeType, MaterialNode, SupplierNode, ProductNode

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDING_DIM   = 512          # target dimensionality (padded / projected)
BATCH_SIZE      = 64           # nodes per encoding batch
MODEL_NAME      = "sentence-transformers/all-MiniLM-L6-v2"   # 384-dim native
_NATIVE_DIM     = 384

# ─────────────────────────────────────────────────────────────────────────────
# Text prompt builders — one per node type
# ─────────────────────────────────────────────────────────────────────────────

def _material_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Material: {props.get('name', '')}",
        f"Polish: {props.get('name_pl', '')}",
        f"Class: {props.get('material_class', '')}",
        f"Grade: {props.get('grade', '')}",
        f"Form: {props.get('material_form', '')}",
        f"Sub-class: {props.get('sub_class', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    if props.get("tensile_mpa"):
        parts.append(f"Tensile strength: {props['tensile_mpa']} MPa")
    if props.get("density_kg_m3"):
        parts.append(f"Density: {props['density_kg_m3']} kg/m3")
    if props.get("hs_code"):
        parts.append(f"HS code: {props['hs_code']}")
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


def _supplier_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Supplier: {props.get('name', '')}",
        f"Legal: {props.get('legal_name', '')}",
        f"Country: {props.get('country', '')}",
        f"City: {props.get('city', '')}",
        f"Incoterms: {props.get('incoterms', '')}",
        f"Payment: {props.get('payment_terms', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    certs = []
    if props.get("iso_9001"):    certs.append("ISO9001")
    if props.get("iatf_16949"):  certs.append("IATF16949")
    if props.get("iso_14001"):   certs.append("ISO14001")
    if certs:
        parts.append(f"Certifications: {' '.join(certs)}")
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


def _product_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Product: {props.get('name', '')}",
        f"SKU: {props.get('sku', '')}",
        f"Family: {props.get('product_family', '')}",
        f"Line: {props.get('product_line', '')}",
        f"Description: {props.get('description', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


def _process_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Process: {props.get('name', '')}",
        f"Type: {props.get('process_type', '')}",
        f"Description: {props.get('description', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    if props.get("tolerance_mm"):
        parts.append(f"Tolerance: {props['tolerance_mm']} mm")
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


def _standard_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Standard: {props.get('number', '')}",
        f"Title: {props.get('title', '')}",
        f"Body: {props.get('body', '')}",
        f"Scope: {props.get('scope', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


def _offer_prompt(props: dict[str, Any]) -> str:
    parts = [
        f"Offer: {props.get('offer_number', '')}",
        f"Status: {props.get('status', '')}",
        f"Currency: {props.get('currency', '')}",
        f"Unit: {props.get('unit', '')}",
        f"Tags: {' '.join(props.get('tags', []))}",
    ]
    if props.get("unit_price"):
        parts.append(f"Price: {props['unit_price']} {props.get('currency', '')}")
    return " | ".join(p for p in parts if p.split(": ", 1)[-1])


_PROMPT_BUILDERS: dict[str, Any] = {
    NodeType.MATERIAL.value:  _material_prompt,
    NodeType.SUPPLIER.value:  _supplier_prompt,
    NodeType.PRODUCT.value:   _product_prompt,
    NodeType.PROCESS.value:   _process_prompt,
    NodeType.STANDARD.value:  _standard_prompt,
    NodeType.OFFER.value:     _offer_prompt,
}


def build_node_prompt(node_type: str, props: dict[str, Any]) -> str:
    builder = _PROMPT_BUILDERS.get(node_type, lambda p: str(p.get("name", "")))
    return builder(props)


# ─────────────────────────────────────────────────────────────────────────────
# Vector utilities (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Vectors must have equal dimension")
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def l2_normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    if mag == 0:
        return vec
    return [x / mag for x in vec]


def pad_or_project(vec: list[float], target_dim: int = EMBEDDING_DIM) -> list[float]:
    """Pad with zeros or truncate to match target_dim."""
    if len(vec) == target_dim:
        return vec
    if len(vec) < target_dim:
        return vec + [0.0] * (target_dim - len(vec))
    return vec[:target_dim]


def deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Fallback: deterministic pseudo-embedding from SHA-256 hash."""
    h = hashlib.sha256(text.encode()).digest()
    # extend hash to fill dim floats
    extended = (h * (dim // len(h) + 2))[:dim * 4]
    raw = [(b / 255.0) * 2 - 1.0 for b in extended[:dim]]
    return l2_normalize(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Encoder — wraps SentenceTransformer with fallback
# ─────────────────────────────────────────────────────────────────────────────

class EmbeddingEncoder:
    """
    Wraps SentenceTransformer for batch encoding.
    Falls back to deterministic_embedding when library not installed.
    """

    def __init__(self, model_name: str = MODEL_NAME, device: str = "cpu") -> None:
        self._model_name = model_name
        self._model: Any  = None
        self._device      = device
        self._lock        = asyncio.Lock()

    async def _load(self) -> None:
        if self._model is not None:
            return
        async with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name, device=self._device)
                log.info("SentenceTransformer loaded: %s", self._model_name)
            except Exception as exc:
                log.warning("SentenceTransformer unavailable (%s) — using deterministic fallback", exc)
                self._model = "fallback"

    async def encode(self, texts: list[str]) -> list[list[float]]:
        await self._load()
        if self._model == "fallback":
            return [pad_or_project(deterministic_embedding(t)) for t in texts]
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(
            None,
            lambda: self._model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False, normalize_embeddings=True),
        )
        return [pad_or_project(list(map(float, v))) for v in vecs]

    async def encode_one(self, text: str) -> list[float]:
        results = await self.encode([text])
        return results[0]


# ─────────────────────────────────────────────────────────────────────────────
# Neo4j upsert helpers
# ─────────────────────────────────────────────────────────────────────────────

_UPSERT_EMBEDDING = """
MATCH (n {node_id: $node_id})
SET n.embedding = $embedding, n.embedding_updated_at = datetime()
"""


async def upsert_embedding(driver: Any, node_id: str, embedding: list[float]) -> None:
    async with driver.session() as session:
        await session.run(_UPSERT_EMBEDDING, node_id=node_id, embedding=embedding)


async def batch_upsert_embeddings(
    driver: Any,
    pairs: list[tuple[str, list[float]]],
    batch_size: int = 100,
) -> int:
    query = """
    UNWIND $rows AS row
    MATCH (n {node_id: row.node_id})
    SET n.embedding = row.embedding, n.embedding_updated_at = datetime()
    """
    total = 0
    async with driver.session() as session:
        for i in range(0, len(pairs), batch_size):
            chunk = pairs[i : i + batch_size]
            rows  = [{"node_id": nid, "embedding": vec} for nid, vec in chunk]
            await session.run(query, rows=rows)
            total += len(chunk)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# EmbeddingPipeline — full node encoding + storage
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmbeddingStats:
    processed:     int = 0
    skipped:       int = 0
    errors:        int = 0
    duration_s:    float = 0.0
    nodes_per_sec: float = 0.0


class EmbeddingPipeline:
    """
    Full pipeline: fetch nodes from Neo4j → build prompts → encode → write back.

    Usage:
        pipeline = EmbeddingPipeline(driver, encoder)
        stats = await pipeline.run_full(tenant_id="tenant-x")
        stats = await pipeline.run_incremental(tenant_id, since_hours=24)
    """

    _FETCH_NODES = """
    MATCH (n)
    WHERE n.tenant_id = $tenant_id
      AND ($node_type IS NULL OR $node_type IN labels(n))
      AND (n.embedding IS NULL OR n.embedding_updated_at < datetime() - duration({hours: $since_hours}))
    RETURN n.node_id AS node_id, labels(n)[0] AS node_type, properties(n) AS props
    LIMIT $limit
    """

    def __init__(self, driver: Any, encoder: EmbeddingEncoder | None = None) -> None:
        self._driver  = driver
        self._encoder = encoder or EmbeddingEncoder()

    async def run_full(
        self,
        tenant_id: str,
        node_type: str | None = None,
        limit: int = 10_000,
    ) -> EmbeddingStats:
        return await self._run(tenant_id, node_type, since_hours=10_000, limit=limit)

    async def run_incremental(
        self,
        tenant_id: str,
        since_hours: int = 24,
        node_type: str | None = None,
        limit: int = 5_000,
    ) -> EmbeddingStats:
        return await self._run(tenant_id, node_type, since_hours=since_hours, limit=limit)

    async def _run(
        self,
        tenant_id: str,
        node_type: str | None,
        since_hours: int,
        limit: int,
    ) -> EmbeddingStats:
        t0 = time.monotonic()
        stats = EmbeddingStats()

        async with self._driver.session() as session:
            result = await session.run(
                self._FETCH_NODES,
                tenant_id=tenant_id,
                node_type=node_type,
                since_hours=since_hours,
                limit=limit,
            )
            rows = await result.data()

        if not rows:
            return stats

        texts:    list[str]   = []
        node_ids: list[str]   = []
        for row in rows:
            try:
                prompt = build_node_prompt(row["node_type"], row["props"])
                texts.append(prompt)
                node_ids.append(row["node_id"])
            except Exception:
                stats.skipped += 1

        try:
            embeddings = await self._encoder.encode(texts)
            pairs      = list(zip(node_ids, embeddings))
            await batch_upsert_embeddings(self._driver, pairs)
            stats.processed = len(pairs)
        except Exception as exc:
            log.error("Embedding batch failed: %s", exc)
            stats.errors = len(texts)

        elapsed = time.monotonic() - t0
        stats.duration_s    = round(elapsed, 2)
        stats.nodes_per_sec = round(stats.processed / max(elapsed, 0.001), 1)
        return stats

    async def encode_node(self, node_type: str, props: dict[str, Any]) -> list[float]:
        """Encode a single node (for real-time upsert on create/update)."""
        prompt = build_node_prompt(node_type, props)
        return await self._encoder.encode_one(prompt)


# ─────────────────────────────────────────────────────────────────────────────
# Property-based similarity (no ML — rule-based fallback)
# ─────────────────────────────────────────────────────────────────────────────

def property_similarity_materials(a: dict[str, Any], b: dict[str, Any]) -> float:
    """
    Heuristic material similarity from structured properties.
    Returns 0–1 score.
    """
    score = 0.0
    weight_total = 0.0

    def _compare_str(key: str, w: float) -> None:
        nonlocal score, weight_total
        av, bv = a.get(key), b.get(key)
        if av and bv:
            weight_total += w
            if str(av).lower() == str(bv).lower():
                score += w

    def _compare_num(key: str, w: float, tol_pct: float = 0.10) -> None:
        nonlocal score, weight_total
        av, bv = a.get(key), b.get(key)
        if av is not None and bv is not None:
            weight_total += w
            diff = abs(float(av) - float(bv)) / max(abs(float(av)), 1e-9)
            score += w * max(0.0, 1.0 - diff / tol_pct)

    _compare_str("material_class", 0.30)
    _compare_str("grade",          0.25)
    _compare_str("sub_class",      0.15)
    _compare_str("material_form",  0.10)
    _compare_num("density_kg_m3",  0.05, 0.05)
    _compare_num("tensile_mpa",    0.08, 0.15)
    _compare_num("yield_mpa",      0.07, 0.15)

    if weight_total == 0:
        return 0.0
    return min(score / weight_total, 1.0)


def compute_similarity_edges(
    nodes: list[dict[str, Any]],
    threshold: float = 0.70,
    use_embedding: bool = True,
) -> list[dict[str, Any]]:
    """
    Compute SIMILAR_TO edges between a list of material nodes.
    Returns list of {source_id, target_id, weight, method}.
    """
    edges: list[dict[str, Any]] = []
    n = len(nodes)

    for i in range(n):
        for j in range(i + 1, n):
            a, b = nodes[i], nodes[j]
            if a.get("material_class") != b.get("material_class"):
                continue

            if use_embedding and a.get("embedding") and b.get("embedding"):
                sim    = cosine_similarity(a["embedding"], b["embedding"])
                method = "embedding_cosine"
            else:
                sim    = property_similarity_materials(a, b)
                method = "property_match"

            if sim >= threshold:
                edges.append({
                    "source_id": a["node_id"],
                    "target_id": b["node_id"],
                    "weight":    round(sim, 4),
                    "method":    method,
                    "grade_compat": a.get("grade") == b.get("grade"),
                })
    return edges
