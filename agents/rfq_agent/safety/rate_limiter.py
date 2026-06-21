from __future__ import annotations

import asyncio
import time
from collections import deque
from urllib.parse import urlparse

import structlog
from redis.asyncio import Redis

from ..config import AgentSettings

log = structlog.get_logger(__name__)


class TokenBucket:
    """Thread-safe in-process token bucket for per-process rate limiting."""

    def __init__(self, capacity: int, refill_rate: float) -> None:
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_rate = refill_rate  # tokens / second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class SlidingWindowCounter:
    """In-process sliding window (last N seconds)."""

    def __init__(self, window_s: int, max_count: int) -> None:
        self._window = window_s
        self._max = max_count
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True


class RateLimiter:
    """
    Two-layer rate limiting:
    1. Global hourly cap (in-process sliding window)
    2. Per-domain daily cap (Redis-backed, falls back to no-op)
    3. Minimum interval between consecutive sends (token bucket)
    """

    def __init__(self, settings: AgentSettings, redis: Redis | None = None) -> None:
        self._settings = settings
        self._redis = redis

        # Global: max_emails_per_hour in a 3600s window
        self._global_window = SlidingWindowCounter(
            window_s=3600, max_count=settings.max_emails_per_hour
        )
        # Min interval between sends
        self._interval_bucket = TokenBucket(
            capacity=1,
            refill_rate=1.0 / max(settings.min_email_interval_s, 1),
        )

    async def check_global(self) -> tuple[bool, str]:
        if not self._global_window.allow():
            return False, f"Global hourly cap ({self._settings.max_emails_per_hour}) reached"
        return True, ""

    async def check_interval(self) -> tuple[bool, str]:
        if not await self._interval_bucket.consume():
            return (
                False,
                f"Minimum interval {self._settings.min_email_interval_s}s not elapsed",
            )
        return True, ""

    async def check_domain_daily(self, email: str) -> tuple[bool, str]:
        domain = _extract_domain(email)
        limit = self._settings.max_emails_per_supplier_per_day

        if self._redis:
            key = f"rfq:email_day:{domain}:{_today_key()}"
            try:
                count = await self._redis.get(key)
                current = int(count) if count else 0
                if current >= limit:
                    return False, f"Daily cap ({limit}) for domain {domain} reached"
                return True, ""
            except Exception as exc:
                log.warning("redis_rate_check_failed", domain=domain, error=str(exc))
                # Fail open: allow the send, flag the issue
                return True, ""
        return True, ""

    async def record_send(self, email: str) -> None:
        domain = _extract_domain(email)
        if self._redis:
            key = f"rfq:email_day:{domain}:{_today_key()}"
            try:
                pipe = self._redis.pipeline()
                await pipe.incr(key)
                await pipe.expire(key, 86_400)
                await pipe.execute()
            except Exception as exc:
                log.warning("redis_rate_record_failed", domain=domain, error=str(exc))

    async def wait_for_interval(self) -> None:
        """Block until the minimum send interval has elapsed."""
        while True:
            ok, _ = await self.check_interval()
            if ok:
                return
            await asyncio.sleep(self._settings.min_email_interval_s * 0.1)


def _extract_domain(email: str) -> str:
    if "@" in email:
        return email.split("@", 1)[1].lower()
    try:
        return urlparse(email).netloc.lower()
    except Exception:
        return email.lower()


def _today_key() -> str:
    from datetime import date
    return date.today().isoformat()
