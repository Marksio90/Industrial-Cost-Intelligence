"""
Cross-encoder reranker — model-agnostic interface.

Default model: cross-encoder/ms-marco-MiniLM-L-6-v2
  - 6-layer MiniLM, ~22M params
  - Latency: ~7ms per pair on CPU, ~1ms on GPU
  - Suitable for reranking top-20 candidates (< 150ms total on CPU)

For production:
  - Use cross-encoder/ms-marco-electra-base for higher accuracy
  - Deploy as separate HTTP microservice to avoid GIL contention
  - Or use Cohere Rerank API (swap in CohereCrossEncoder below)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


@runtime_checkable
class RerankerProtocol(Protocol):
    async def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder wrapper."""

    DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        self._model_name = model_name
        self._device     = device
        self._model      = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self._model_name, device=self._device)
                logger.info("Loaded reranker %s on %s", self._model_name, self._device)
            except ImportError as e:
                raise RuntimeError("pip install sentence-transformers") from e

    def _predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self._load()
        scores = self._model.predict(pairs)
        return scores.tolist() if hasattr(scores, "tolist") else list(scores)

    async def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._predict, pairs)


class MockReranker:
    """Unit-test reranker — returns inverted-index scores (no model needed)."""

    async def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        # Score = character overlap ratio (pure token count)
        scores = []
        for q, p in pairs:
            q_tokens = set(q.lower().split())
            p_tokens = set(p.lower().split())
            overlap  = len(q_tokens & p_tokens) / max(len(q_tokens), 1)
            scores.append(overlap)
        return scores


class CohereReranker:
    """
    Cohere Rerank API adapter — zero-infrastructure, pay-per-use.
    Set COHERE_API_KEY env var.
    """

    DEFAULT_MODEL = "rerank-multilingual-v3.0"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model   = model

    async def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        try:
            import cohere
        except ImportError as e:
            raise RuntimeError("pip install cohere") from e

        query     = pairs[0][0]
        documents = [p for _, p in pairs]

        co      = cohere.AsyncClient(api_key=self._api_key)
        results = await co.rerank(query=query, documents=documents, model=self._model, top_n=len(documents))

        # re-order scores to original document order
        score_map = {r.index: r.relevance_score for r in results.results}
        return [score_map.get(i, 0.0) for i in range(len(pairs))]
