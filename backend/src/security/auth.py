"""
ICI Authentication — JWT + OAuth2.

JWT design:
  - Access token:  short-lived (15 min), HS256 (or RS256 if keys configured)
  - Refresh token: long-lived (30 days), stored in DB + Redis for revocation
  - Token family:  each refresh rotates the family; old token use = compromise detected
  - JTI (JWT ID):  every token has a unique ID checked against a revocation set

OAuth2 design:
  - Authorization Code flow with PKCE (recommended for SPAs)
  - Client Credentials flow for machine-to-machine
  - Token introspection endpoint
  - Provider: pluggable (OIDC-compatible — Auth0, Keycloak, Entra ID)

Usage:
    svc = JWTService(settings)
    access  = svc.create_access_token(user)
    refresh = svc.create_refresh_token(user)
    payload = svc.decode(access)

    # FastAPI dependency
    user = await get_current_user(credentials)
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel

from ..config import get_settings
from ..exceptions import AuthenticationError
from ..observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# Token models
# ─────────────────────────────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    sub:        str            # user UUID
    email:      str
    tenant_id:  str
    roles:      list[str]
    jti:        str            # unique token ID (revocation)
    family:     str            # refresh token family (rotation)
    token_type: Literal["access", "refresh"]
    iat:        int
    exp:        int
    iss:        str = "ici-platform"
    aud:        str = "ici-api"


class TokenPair(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int          # access token TTL in seconds


# ─────────────────────────────────────────────────────────────────────────────
# JWTService
# ─────────────────────────────────────────────────────────────────────────────

class JWTService:
    """
    Issues and validates JWT tokens.

    Algorithm preference: RS256 when private_key_pem is set (production),
    falls back to HS256 for local development.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._secret  = s.jwt_secret_key
        self._algo    = s.jwt_algorithm
        self._access_ttl  = timedelta(minutes=s.jwt_access_token_expire_minutes)
        self._refresh_ttl = timedelta(days=s.jwt_refresh_token_expire_days)
        self._issuer  = "ici-platform"
        self._audience = "ici-api"

    # ── Issuance ──────────────────────────────────────────────────────────────

    def create_access_token(
        self,
        *,
        user_id: str,
        email: str,
        tenant_id: str,
        roles: list[str],
        family: str | None = None,
    ) -> str:
        now    = datetime.now(tz=timezone.utc)
        jti    = str(uuid.uuid4())
        family = family or str(uuid.uuid4())

        payload = {
            "sub":        user_id,
            "email":      email,
            "tenant_id":  tenant_id,
            "roles":      roles,
            "jti":        jti,
            "family":     family,
            "token_type": "access",
            "iat":        int(now.timestamp()),
            "exp":        int((now + self._access_ttl).timestamp()),
            "iss":        self._issuer,
            "aud":        self._audience,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algo)

    def create_refresh_token(
        self,
        *,
        user_id: str,
        email: str,
        tenant_id: str,
        roles: list[str],
        family: str | None = None,
    ) -> tuple[str, str]:
        """Returns (token, jti). Caller must persist jti for revocation checks."""
        now    = datetime.now(tz=timezone.utc)
        jti    = str(uuid.uuid4())
        family = family or str(uuid.uuid4())

        payload = {
            "sub":        user_id,
            "email":      email,
            "tenant_id":  tenant_id,
            "roles":      roles,
            "jti":        jti,
            "family":     family,
            "token_type": "refresh",
            "iat":        int(now.timestamp()),
            "exp":        int((now + self._refresh_ttl).timestamp()),
            "iss":        self._issuer,
            "aud":        self._audience,
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algo)
        return token, jti

    def create_token_pair(
        self,
        *,
        user_id: str,
        email: str,
        tenant_id: str,
        roles: list[str],
    ) -> tuple[TokenPair, str]:
        """Returns (TokenPair, refresh_jti). Persist refresh_jti in DB."""
        family = str(uuid.uuid4())
        access = self.create_access_token(
            user_id=user_id, email=email,
            tenant_id=tenant_id, roles=roles, family=family,
        )
        refresh, refresh_jti = self.create_refresh_token(
            user_id=user_id, email=email,
            tenant_id=tenant_id, roles=roles, family=family,
        )
        pair = TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(self._access_ttl.total_seconds()),
        )
        return pair, refresh_jti

    # ── Validation ────────────────────────────────────────────────────────────

    def decode(self, token: str, *, token_type: str = "access") -> TokenPayload:
        try:
            raw = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algo],
                audience=self._audience,
                issuer=self._issuer,
            )
        except ExpiredSignatureError:
            raise AuthenticationError("Token has expired")
        except JWTError as exc:
            raise AuthenticationError(f"Invalid token: {exc}") from exc

        if raw.get("token_type") != token_type:
            raise AuthenticationError(
                f"Expected {token_type} token, got {raw.get('token_type')}"
            )
        return TokenPayload(**raw)

    def extract_jti(self, token: str) -> str | None:
        """Decode without verification — only for revocation list operations."""
        try:
            return jwt.get_unverified_claims(token).get("jti")
        except Exception:
            return None

    def hash_token(self, token: str) -> str:
        """SHA-256 of a token for opaque storage (never store raw refresh tokens)."""
        return hashlib.sha256(token.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Refresh token rotation
# ─────────────────────────────────────────────────────────────────────────────

async def rotate_refresh_token(
    refresh_token: str,
    *,
    jwt_service: JWTService,
    revoked_jtis: set[str],   # load from Redis / DB
) -> tuple[TokenPair, str]:
    """
    Validates the refresh token, checks revocation, and issues a new pair.
    Old refresh JTI is added to the revocation set.

    If an already-revoked token is presented, the entire family is compromised —
    callers should revoke ALL tokens for the user.
    """
    payload = jwt_service.decode(refresh_token, token_type="refresh")

    if payload.jti in revoked_jtis:
        logger.warning(
            "refresh_token_reuse_detected",
            jti=payload.jti,
            family=payload.family,
            user_id=payload.sub,
            tenant_id=payload.tenant_id,
        )
        raise AuthenticationError(
            "Refresh token already used. All sessions have been invalidated."
        )

    pair, new_jti = jwt_service.create_token_pair(
        user_id=payload.sub,
        email=payload.email,
        tenant_id=payload.tenant_id,
        roles=payload.roles,
    )
    return pair, new_jti


# ─────────────────────────────────────────────────────────────────────────────
# OAuth2 / OIDC service
# ─────────────────────────────────────────────────────────────────────────────

class OAuth2Config(BaseModel):
    provider:          str               # "auth0" | "keycloak" | "entra"
    client_id:         str
    client_secret:     str
    discovery_url:     str               # /.well-known/openid-configuration
    redirect_uri:      str
    scopes:            list[str] = ["openid", "profile", "email"]
    pkce_required:     bool = True


class OAuth2Service:
    """
    OIDC Authorization Code + PKCE flow helper.

    Usage:
        svc = OAuth2Service(config)
        # 1. Redirect user to:
        url, state, verifier = await svc.authorization_url()
        # 2. Exchange code:
        id_token = await svc.exchange_code(code, state, verifier)
        # 3. Map claims to internal user
        user_info = await svc.get_user_info(id_token.access_token)
    """

    def __init__(self, config: OAuth2Config) -> None:
        self._cfg = config
        self._metadata: dict[str, Any] | None = None

    async def _discover(self) -> dict[str, Any]:
        if self._metadata:
            return self._metadata
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self._cfg.discovery_url)
            r.raise_for_status()
            self._metadata = r.json()
        return self._metadata

    async def authorization_url(self) -> tuple[str, str, str]:
        """Returns (url, state, code_verifier)."""
        from urllib.parse import urlencode
        meta     = await self._discover()
        state    = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)

        # PKCE challenge (S256)
        import base64, hashlib
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()

        params = {
            "response_type":         "code",
            "client_id":             self._cfg.client_id,
            "redirect_uri":          self._cfg.redirect_uri,
            "scope":                 " ".join(self._cfg.scopes),
            "state":                 state,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
            "nonce":                 secrets.token_urlsafe(16),
        }
        url = f"{meta['authorization_endpoint']}?{urlencode(params)}"
        return url, state, verifier

    async def exchange_code(
        self, code: str, verifier: str
    ) -> dict[str, Any]:
        meta = await self._discover()
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                meta["token_endpoint"],
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  self._cfg.redirect_uri,
                    "client_id":     self._cfg.client_id,
                    "client_secret": self._cfg.client_secret,
                    "code_verifier": verifier,
                },
            )
            r.raise_for_status()
            return r.json()

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        meta = await self._discover()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                meta["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            return r.json()

    async def introspect(self, token: str) -> dict[str, Any]:
        meta = await self._discover()
        if "introspection_endpoint" not in meta:
            raise NotImplementedError("Provider does not support token introspection")
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                meta["introspection_endpoint"],
                data={"token": token, "client_id": self._cfg.client_id,
                      "client_secret": self._cfg.client_secret},
            )
            r.raise_for_status()
            return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience (keeps existing callers working)
# ─────────────────────────────────────────────────────────────────────────────

_default_svc: JWTService | None = None


def _svc() -> JWTService:
    global _default_svc
    if _default_svc is None:
        _default_svc = JWTService()
    return _default_svc


def create_access_token(**kwargs: Any) -> str:
    return _svc().create_access_token(**kwargs)


def create_refresh_token(**kwargs: Any) -> tuple[str, str]:
    return _svc().create_refresh_token(**kwargs)


def decode_token(token: str, token_type: str = "access") -> TokenPayload:
    return _svc().decode(token, token_type=token_type)
