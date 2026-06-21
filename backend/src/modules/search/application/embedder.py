"""
Embedding service — model-agnostic interface over sentence-transformers.

Design:
  - EmbedderProtocol: structural protocol, any backend can implement it
  - SentenceTransformerEmbedder: production impl (sentence-transformers)
  - MockEmbedder: unit-test impl (returns deterministic zero-padded vectors)
  - EmbedderRegistry: maps EmbeddingModel enum → embedder instance (singleton)

Asymmetric retrieval (multilingual-e5-large):
  - Documents:  embed with "passage: " prefix
  - Queries:    embed with "query: "   prefix
  Omitting the prefix reduces recall by ~5% on BEIR benchmarks.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import abstractmethod
from functools import lru_cache
from typing import Protocol, runtime_checkable

import numpy as np

from ..domain.models import EmbeddingModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol — swap any backend without touching the rest of the pipeline
# ---------------------------------------------------------------------------

@runtime_checkable
class EmbedderProtocol(Protocol):
    model_name: str
    dimensions: int

    @abstractmethod
    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray: ...

    @abstractmethod
    async def aembed_passages(self, texts: list[str]) -> np.ndarray: ...

    @abstractmethod
    async def aembed_query(self, text: str) -> np.ndarray: ...


# ---------------------------------------------------------------------------
# Sentence-Transformers implementation
# ---------------------------------------------------------------------------

class SentenceTransformerEmbedder:
    """
    Wraps sentence-transformers SentenceTransformer.
    Thread-safe: encode() releases the GIL inside the C++ ONNX runtime.
    Async: offloads to a ThreadPoolExecutor (no event loop blocking).
    """

    # Asymmetric prefix used by E5 model family
    _PASSAGE_PREFIX = "passage: "
    _QUERY_PREFIX   = "query: "

    def __init__(self, model_name: str, dimensions: int, device: str = "cpu") -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._device    = device
        self._model     = None  # lazy-loaded on first use

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name, device=self._device)
                logger.info("Loaded embedding model %s on %s", self.model_name, self._device)
            except ImportError as e:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                ) from e

    # ----- sync interface -----

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        """
        Embed a batch of document passages.
        Adds asymmetric prefix only if model is E5-family.
        Normalises to unit sphere for cosine similarity.
        """
        self._load()
        prefixed = [
            f"{self._PASSAGE_PREFIX}{t}" if self._needs_prefix() else t
            for t in texts
        ]
        vecs = self._model.encode(prefixed, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return np.array(vecs, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        self._load()
        prefixed = f"{self._QUERY_PREFIX}{text}" if self._needs_prefix() else text
        vec = self._model.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
        return np.array(vec[0], dtype=np.float32)

    # ----- async interface (threadpool offload) -----

    async def aembed_passages(self, texts: list[str]) -> np.ndarray:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.embed_passages, texts)

    async def aembed_query(self, text: str) -> np.ndarray:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.embed_query, text)

    def _needs_prefix(self) -> bool:
        return "e5" in self.model_name.lower()


# ---------------------------------------------------------------------------
# Mock embedder (unit tests / CI without GPU)
# ---------------------------------------------------------------------------

class MockEmbedder:
    """
    Deterministic embedder: SHA-256 of text → seeded random unit vector.
    Preserves relative similarity order for identical texts.
    """

    def __init__(self, model_name: str = "mock", dimensions: int = 1024) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    def _hash_to_vec(self, text: str) -> np.ndarray:
        seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
        rng  = np.random.default_rng(seed)
        vec  = rng.standard_normal(self.dimensions).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-12)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._hash_to_vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._hash_to_vec(text)

    async def aembed_passages(self, texts: list[str]) -> np.ndarray:
        return self.embed_passages(texts)

    async def aembed_query(self, text: str) -> np.ndarray:
        return self.embed_query(text)


# ---------------------------------------------------------------------------
# Registry — one embedder instance per model (lazy singleton)
# ---------------------------------------------------------------------------

_MODEL_CONFIGS: dict[EmbeddingModel, tuple[str, int]] = {
    EmbeddingModel.MULTILINGUAL_E5_LARGE: ("intfloat/multilingual-e5-large", 1024),
    EmbeddingModel.ALL_MPNET_BASE_V2:     ("sentence-transformers/all-mpnet-base-v2", 768),
    EmbeddingModel.RESNET50:              ("resnet50", 512),  # handled by CADEmbedder
}


class EmbedderRegistry:
    _instances: dict[EmbeddingModel, EmbedderProtocol] = {}

    @classmethod
    def get(cls, model: EmbeddingModel, use_mock: bool = False) -> EmbedderProtocol:
        if model not in cls._instances:
            if use_mock:
                _, dims = _MODEL_CONFIGS[model]
                cls._instances[model] = MockEmbedder(model.value, dims)
            else:
                name, dims = _MODEL_CONFIGS[model]
                cls._instances[model] = SentenceTransformerEmbedder(name, dims)
        return cls._instances[model]

    @classmethod
    def register(cls, model: EmbeddingModel, embedder: EmbedderProtocol) -> None:
        """Inject custom embedder (e.g., OpenAI, Cohere, vLLM)."""
        cls._instances[model] = embedder

    @classmethod
    def clear(cls) -> None:
        cls._instances.clear()


# ---------------------------------------------------------------------------
# Feature → embedding helpers (one per domain)
# ---------------------------------------------------------------------------

async def embed_material(features, embedder: EmbedderProtocol) -> np.ndarray:
    passage = features.to_passage()
    return await embedder.aembed_query(passage)


async def embed_process(features, embedder: EmbedderProtocol) -> np.ndarray:
    return await embedder.aembed_query(features.to_passage())


async def embed_quote(features, embedder: EmbedderProtocol) -> np.ndarray:
    return await embedder.aembed_query(features.to_passage())


async def embed_supplier(features, embedder: EmbedderProtocol) -> np.ndarray:
    return await embedder.aembed_query(features.to_passage())


async def embed_search_query(query: str, embedder: EmbedderProtocol) -> np.ndarray:
    """Add 'query: ' prefix for asymmetric E5 retrieval."""
    return await embedder.aembed_query(query)


# ---------------------------------------------------------------------------
# Batch re-indexer — called by background job / Argo workflow
# ---------------------------------------------------------------------------

class DomainIndexer:
    """
    Fetches all entities from DB, embeds in batches, upserts to vector store.
    Designed for full re-index (model version change) or incremental catch-up.
    """

    BATCH_SIZE = 64

    def __init__(self, embedder: EmbedderProtocol) -> None:
        self._embedder = embedder

    async def index_batch(
        self,
        features_list: list,
        to_passage_fn,
        upsert_fn,
    ) -> int:
        """Returns number of vectors indexed."""
        indexed = 0
        for i in range(0, len(features_list), self.BATCH_SIZE):
            batch   = features_list[i : i + self.BATCH_SIZE]
            texts   = [to_passage_fn(f) for f in batch]
            t0      = time.monotonic()
            vectors = await self._embedder.aembed_passages(texts)
            ms      = (time.monotonic() - t0) * 1000
            logger.debug("Embedded %d passages in %.1f ms", len(batch), ms)
            await upsert_fn(batch, vectors)
            indexed += len(batch)
        return indexed
