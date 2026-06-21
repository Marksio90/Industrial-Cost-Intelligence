"""
Search result cache — Redis-backed with LRU local fallback.

Cache key: SHA-256(domain + query + filters + top_k + tenant_id)
TTL strategy:
  - Exact keyword queries:   300s  (5 min) — volatile data
  - Vector similarity:       120s  (2 min) — embeddings don't change often
  - Hybrid:                  180s  (3 min)
  - Portfolio/aggregates:    600s (10 min)

Invalidation:
  - On WRITE to entity (materials/suppliers/etc): call invalidate_domain()
  - Redis key pattern:  search:{tenant_id}:{domain}:*
  - Async fire-and-forget: does not block the write path

Local LRU fallback:
  - If Redis is unavailable: falls back to in-process LRU (128 entries).
  - Logs a warning on first Redis error, then silently degrades.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import asdict
from typing import Any

from ..domain.models import SearchDomain, SearchMode, SearchResult

logger = logging.getLogger(__name__)

_TTL: dict[SearchMode, int] = {
    SearchMode.KEYWORD: 300,
    SearchMode.VECTOR:  120,
    SearchMode.HYBRID:  180,
}

_LOCAL_LRU_SIZE = 128


# ---------------------------------------------------------------------------
# Local LRU cache (process-level fallback)
# ---------------------------------------------------------------------------

class _LRUCache:
    def __init__(self, maxsize: int) -> None:
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        value, expires_at = self._cache[key]
        if time.monotonic() > expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, time.monotonic() + ttl)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def delete_pattern(self, prefix: str) -> int:
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            del self._cache[k]
        return len(keys)


_local_lru = _LRUCache(_LOCAL_LRU_SIZE)


# ---------------------------------------------------------------------------
# Cache key builder
# ---------------------------------------------------------------------------

def build_cache_key(
    tenant_id: str,
    domain: SearchDomain,
    query: str,
    mode: SearchMode,
    top_k: int,
    filters: dict | None,
) -> str:
    fingerprint = json.dumps({
        "t": tenant_id, "d": domain.value, "q": query,
        "m": mode.value, "k": top_k, "f": filters or {},
    }, sort_keys=True)
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return f"search:{tenant_id}:{domain.value}:{digest}"


# ---------------------------------------------------------------------------
# Redis-backed cache with LRU fallback
# ---------------------------------------------------------------------------

class SearchCache:

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = None
        self._redis_ok = True  # optimistic; set to False on first error
        if redis_url:
            self._redis_url = redis_url
        else:
            import os
            self._redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url, decode_responses=True, socket_connect_timeout=1)
            except ImportError:
                pass
        return self._redis

    async def get(self, key: str) -> SearchResult | None:
        # Try Redis first
        if self._redis_ok:
            try:
                r = self._get_redis()
                if r:
                    raw = await r.get(key)
                    if raw:
                        data = json.loads(raw)
                        return _deserialise(data)
            except Exception as exc:
                if self._redis_ok:
                    logger.warning("Redis unavailable (%s), falling back to LRU", exc)
                    self._redis_ok = False

        # LRU fallback
        return _local_lru.get(key)

    async def set(self, key: str, result: SearchResult, mode: SearchMode) -> None:
        ttl  = _TTL[mode]
        data = _serialise(result)

        if self._redis_ok:
            try:
                r = self._get_redis()
                if r:
                    await r.setex(key, ttl, json.dumps(data))
                    return
            except Exception as exc:
                logger.debug("Redis set failed: %s", exc)
                self._redis_ok = False

        _local_lru.set(key, result, ttl)

    async def invalidate_domain(self, tenant_id: str, domain: SearchDomain) -> None:
        """
        Best-effort invalidation after entity write.
        Called fire-and-forget (asyncio.create_task) so it never blocks writes.
        """
        pattern = f"search:{tenant_id}:{domain.value}:*"

        if self._redis_ok:
            try:
                r = self._get_redis()
                if r:
                    keys = await r.keys(pattern)
                    if keys:
                        await r.delete(*keys)
                        logger.debug("Invalidated %d cache keys for %s", len(keys), pattern)
                    return
            except Exception:
                pass

        # LRU fallback
        _local_lru.delete_pattern(f"search:{tenant_id}:{domain.value}:")

    async def ping(self) -> bool:
        try:
            r = self._get_redis()
            if r:
                await r.ping()
                return True
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Serialisation helpers (SearchResult ↔ JSON)
# ---------------------------------------------------------------------------

def _serialise(result: SearchResult) -> dict:
    return {
        "query":  result.query,
        "domain": result.domain.value,
        "mode":   result.mode.value,
        "total":  result.total_candidates,
        "ms":     result.latency_ms,
        "hits": [
            {
                "id":    str(h.entity_id),
                "score": h.score,
                "vsc":   h.vector_score,
                "ksc":   h.keyword_score,
                "rank":  h.rank,
                "pl":    h.payload,
            }
            for h in result.hits
        ],
    }


def _deserialise(data: dict) -> SearchResult:
    from uuid import UUID
    from ..domain.models import SearchHit
    hits = [
        SearchHit(
            entity_id=UUID(h["id"]),
            domain=SearchDomain(data["domain"]),
            score=h["score"],
            vector_score=h.get("vsc"),
            keyword_score=h.get("ksc"),
            rank=h["rank"],
            payload=h.get("pl", {}),
        )
        for h in data["hits"]
    ]
    return SearchResult(
        query=data["query"],
        domain=SearchDomain(data["domain"]),
        mode=SearchMode(data["mode"]),
        hits=hits,
        total_candidates=data["total"],
        latency_ms=data["ms"],
        cached=True,
    )
