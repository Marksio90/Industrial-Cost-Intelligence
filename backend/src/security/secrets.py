"""
ICI Secrets Manager — unified runtime secret loading.

Lookup chain (first match wins):
    1. Environment variable  (always available; lowest latency)
    2. HashiCorp Vault       (when VAULT_ADDR + VAULT_TOKEN configured)
    3. AWS Secrets Manager   (when AWS_REGION configured)
    4. Kubernetes Secret     (when running inside a pod with mounted secrets)

All resolved secrets are cached in memory for ttl_s seconds (default 300).
Stale cache entries are served on refresh failure (fail-safe read).

Usage:
    sm = SecretsManager()
    db_password = await sm.get("POSTGRES_PASSWORD")
    api_key     = await sm.get("ANTHROPIC_API_KEY", default="")

    # Bulk load
    secrets = await sm.get_many(["REDIS_PASSWORD", "QDRANT_API_KEY"])
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ..observability.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Cache entry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    value:      str
    fetched_at: float = field(default_factory=time.monotonic)
    source:     str   = "env"


# ─────────────────────────────────────────────────────────────────────────────
# Backend protocols
# ─────────────────────────────────────────────────────────────────────────────

class SecretBackend:
    async def get(self, key: str) -> str | None: ...


class EnvBackend(SecretBackend):
    async def get(self, key: str) -> str | None:
        return os.environ.get(key)


class VaultBackend(SecretBackend):
    """
    HashiCorp Vault KV v2 backend.
    Reads VAULT_ADDR, VAULT_TOKEN, VAULT_NAMESPACE, VAULT_SECRET_PATH from env.
    """

    def __init__(self) -> None:
        self._addr      = os.environ.get("VAULT_ADDR", "")
        self._token     = os.environ.get("VAULT_TOKEN", "")
        self._namespace = os.environ.get("VAULT_NAMESPACE", "")
        self._path      = os.environ.get("VAULT_SECRET_PATH", "secret/data/ici")
        self._cache:    dict[str, Any] = {}
        self._fetched:  float = 0.0
        self._ttl:      float = 300.0

    @property
    def _configured(self) -> bool:
        return bool(self._addr and self._token)

    async def _fetch_all(self) -> dict[str, Any]:
        if not self._configured:
            return {}
        now = time.monotonic()
        if self._cache and now - self._fetched < self._ttl:
            return self._cache
        try:
            import httpx
            headers: dict[str, str] = {"X-Vault-Token": self._token}
            if self._namespace:
                headers["X-Vault-Namespace"] = self._namespace
            url = f"{self._addr}/v1/{self._path}"
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                self._cache   = r.json().get("data", {}).get("data", {})
                self._fetched = now
                logger.info("vault_secrets_refreshed", path=self._path)
        except Exception as exc:
            logger.warning("vault_fetch_failed", error=str(exc))
        return self._cache

    async def get(self, key: str) -> str | None:
        data = await self._fetch_all()
        return data.get(key)


class AWSSecretsBackend(SecretBackend):
    """
    AWS Secrets Manager backend.
    Reads AWS_SECRETS_ARN or constructs ARN from AWS_REGION + APP_NAME.
    Requires boto3 installed and IAM permissions.
    """

    def __init__(self) -> None:
        self._arn    = os.environ.get("AWS_SECRETS_ARN", "")
        self._region = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
        self._cache: dict[str, Any] = {}
        self._fetched: float = 0.0
        self._ttl:     float = 300.0

    @property
    def _configured(self) -> bool:
        return bool(self._arn)

    async def _fetch_all(self) -> dict[str, Any]:
        if not self._configured:
            return {}
        now = time.monotonic()
        if self._cache and now - self._fetched < self._ttl:
            return self._cache
        try:
            import boto3, json
            client  = boto3.client("secretsmanager", region_name=self._region)
            resp    = await asyncio.to_thread(
                client.get_secret_value, SecretId=self._arn
            )
            raw     = resp.get("SecretString", "{}")
            self._cache   = json.loads(raw)
            self._fetched = now
            logger.info("aws_secrets_refreshed", arn=self._arn)
        except Exception as exc:
            logger.warning("aws_secrets_fetch_failed", error=str(exc))
        return self._cache

    async def get(self, key: str) -> str | None:
        data = await self._fetch_all()
        return data.get(key)


class K8sSecretBackend(SecretBackend):
    """
    Reads secrets from mounted K8s Secret volume.
    Mount path: /var/run/secrets/ici/ (configure in Deployment spec).
    Each key is a file containing the secret value.
    """

    def __init__(self, mount_path: str = "/var/run/secrets/ici") -> None:
        self._mount = mount_path

    async def get(self, key: str) -> str | None:
        path = os.path.join(self._mount, key)
        try:
            with open(path) as f:
                return f.read().strip()
        except (FileNotFoundError, PermissionError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# SecretsManager
# ─────────────────────────────────────────────────────────────────────────────

class SecretsManager:
    def __init__(self, ttl_s: float = 300.0) -> None:
        self._ttl  = ttl_s
        self._cache: dict[str, _CacheEntry] = {}
        self._lock  = asyncio.Lock()

        # Ordered chain — first hit wins
        self._backends: list[tuple[str, SecretBackend]] = [
            ("env",    EnvBackend()),
            ("vault",  VaultBackend()),
            ("aws",    AWSSecretsBackend()),
            ("k8s",    K8sSecretBackend()),
        ]

    async def get(self, key: str, *, default: str | None = None) -> str | None:
        async with self._lock:
            entry = self._cache.get(key)
            if entry and time.monotonic() - entry.fetched_at < self._ttl:
                return entry.value

        # Outside lock for network I/O
        for source, backend in self._backends:
            try:
                value = await backend.get(key)
                if value is not None:
                    async with self._lock:
                        self._cache[key] = _CacheEntry(value=value, source=source)
                    logger.debug("secret_resolved", key=key, source=source)
                    return value
            except Exception as exc:
                logger.debug("secret_backend_error", backend=source, key=key, error=str(exc))

        # Serve stale cache on resolution failure
        stale = self._cache.get(key)
        if stale:
            logger.warning("secret_serving_stale_cache", key=key)
            return stale.value

        logger.warning("secret_not_found", key=key)
        return default

    async def get_many(self, keys: list[str]) -> dict[str, str | None]:
        results = await asyncio.gather(*[self.get(k) for k in keys])
        return dict(zip(keys, results))

    async def invalidate(self, key: str | None = None) -> None:
        async with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    async def rotate_and_reload(self, key: str) -> str | None:
        """Invalidate cache entry and fetch fresh value — use after secret rotation."""
        await self.invalidate(key)
        return await self.get(key)


# Module-level singleton
_sm: SecretsManager | None = None


def get_secrets_manager() -> SecretsManager:
    global _sm
    if _sm is None:
        _sm = SecretsManager()
    return _sm
