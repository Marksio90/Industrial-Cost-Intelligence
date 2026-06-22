"""
ICI Field-Level Encryption.

Architecture:
  - Data Encryption Key (DEK): AES-256-GCM, unique per record
  - Key Encryption Key (KEK):  Fernet (AES-128-CBC + HMAC), stored in secrets manager
  - Envelope encryption:       DEK is encrypted by KEK and stored alongside ciphertext
  - Key rotation:              Re-encrypt DEK with new KEK; plaintext never re-read

Ciphertext wire format (base64-url):
    <version:1B> | <kek_id:16B> | <iv:12B> | <tag:16B> | <encrypted_dek:48B> | <ciphertext>

In-database storage:
    Columns marked with @encrypted_field store the base64 envelope.
    SQLAlchemy TypeDecorator transparently encrypts on INSERT and decrypts on SELECT.

In-transit:
    TLS 1.3 is enforced at the nginx/ingress layer (see nginx config).
    This module handles at-rest encryption only.

Usage:
    enc = FieldEncryption.from_settings()

    # Encrypt
    ciphertext = enc.encrypt("sensitive value")

    # Decrypt
    plaintext = enc.decrypt(ciphertext)

    # SQLAlchemy column type
    class Supplier(Base):
        contact_email: Mapped[str] = mapped_column(EncryptedString(enc))

    # Django-style helpers
    encrypt_field("value")
    decrypt_field("base64...")
"""
from __future__ import annotations

import base64
import os
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import String, TypeDecorator

from ..observability.logging import get_logger

logger = get_logger(__name__)

_FORMAT_VERSION = b"\x01"
_IV_LENGTH      = 12   # AES-GCM recommended
_TAG_LENGTH     = 16
_KEY_LENGTH     = 32   # AES-256


# ─────────────────────────────────────────────────────────────────────────────
# Key management
# ─────────────────────────────────────────────────────────────────────────────

class KeyStore:
    """
    Manages KEK ring for key rotation.
    Keys are loaded from secrets manager / env at startup and never stored in
    application memory longer than the process lifetime.

    Environment variable format:
        ENCRYPTION_KEYS=<id1>:<fernet_key1>,<id2>:<fernet_key2>
        ENCRYPTION_PRIMARY_KEY_ID=<id1>

    The first key in ENCRYPTION_KEYS is used for encryption; all keys are
    tried for decryption (enables zero-downtime rotation).
    """

    def __init__(
        self,
        keys: dict[str, bytes],   # {key_id: fernet_key_bytes}
        primary_key_id: str,
    ) -> None:
        if primary_key_id not in keys:
            raise ValueError(f"Primary key ID '{primary_key_id}' not in keystore")
        self._keys           = keys
        self._primary_key_id = primary_key_id
        self._fernets        = {kid: Fernet(k) for kid, k in keys.items()}
        self._primary_fernet = self._fernets[primary_key_id]

    @classmethod
    def from_env(cls) -> "KeyStore":
        raw = os.environ.get("ENCRYPTION_KEYS", "")
        if not raw:
            # Development fallback — generate an ephemeral key
            logger.warning(
                "encryption_key_not_configured",
                detail="Using ephemeral DEV key — data will be unreadable after restart",
            )
            kid = "dev-ephemeral"
            k   = Fernet.generate_key()
            return cls({kid: k}, kid)

        keys: dict[str, bytes] = {}
        for entry in raw.split(","):
            kid, _, key = entry.partition(":")
            keys[kid.strip()] = key.strip().encode()

        primary = os.environ.get(
            "ENCRYPTION_PRIMARY_KEY_ID", next(iter(keys))
        )
        return cls(keys, primary)

    def encrypt_dek(self, dek: bytes) -> tuple[str, bytes]:
        """Returns (key_id, encrypted_dek)."""
        return self._primary_key_id, self._primary_fernet.encrypt(dek)

    def decrypt_dek(self, key_id: str, encrypted_dek: bytes) -> bytes:
        fernet = self._fernets.get(key_id)
        if fernet is None:
            raise ValueError(f"Unknown key ID: {key_id}")
        try:
            return fernet.decrypt(encrypted_dek)
        except InvalidToken as exc:
            raise ValueError("DEK decryption failed — wrong key or corrupted ciphertext") from exc

    def rotate_dek(self, ciphertext: bytes) -> bytes:
        """Re-encrypt a ciphertext DEK with the current primary KEK."""
        # Parse existing kek_id from ciphertext header to locate old DEK
        header_len = 1 + 16 + _IV_LENGTH + _TAG_LENGTH + 48
        if len(ciphertext) < header_len:
            raise ValueError("Ciphertext too short to contain valid header")
        version  = ciphertext[0:1]
        kek_id_b = ciphertext[1:17]
        kek_id   = kek_id_b.rstrip(b"\x00").decode()
        iv       = ciphertext[17:29]
        tag      = ciphertext[29:45]
        enc_dek  = ciphertext[45:93]
        body     = ciphertext[93:]

        old_dek = self.decrypt_dek(kek_id, enc_dek)
        new_kid, new_enc_dek = self.encrypt_dek(old_dek)

        kid_padded = new_kid.encode().ljust(16, b"\x00")[:16]
        return version + kid_padded + iv + tag + new_enc_dek + body


# ─────────────────────────────────────────────────────────────────────────────
# FieldEncryption
# ─────────────────────────────────────────────────────────────────────────────

class FieldEncryption:
    """
    Symmetric envelope encryption for individual database fields.
    Each call to encrypt() generates a fresh DEK — no two fields share a key.
    """

    def __init__(self, key_store: KeyStore) -> None:
        self._ks = key_store

    @classmethod
    def from_env(cls) -> "FieldEncryption":
        return cls(KeyStore.from_env())

    def encrypt(self, plaintext: str | bytes) -> str:
        """Encrypt and return base64url-encoded ciphertext envelope."""
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()

        # Fresh DEK per record
        dek = os.urandom(_KEY_LENGTH)
        iv  = os.urandom(_IV_LENGTH)
        aesgcm = AESGCM(dek)
        body   = aesgcm.encrypt(iv, plaintext, None)   # tag is appended by cryptography

        kek_id, enc_dek = self._ks.encrypt_dek(dek)
        kek_id_padded   = kek_id.encode().ljust(16, b"\x00")[:16]

        # body = tag (last 16 bytes) + ciphertext; split for header
        tag        = body[-_TAG_LENGTH:]
        ciphertext = body[:-_TAG_LENGTH]

        envelope = (
            _FORMAT_VERSION
            + kek_id_padded
            + iv
            + tag
            + enc_dek
            + ciphertext
        )
        return base64.urlsafe_b64encode(envelope).decode()

    def decrypt(self, envelope_b64: str) -> str:
        """Decrypt a base64url envelope and return the plaintext string."""
        try:
            envelope = base64.urlsafe_b64decode(envelope_b64.encode())
        except Exception as exc:
            raise ValueError("Invalid base64 envelope") from exc

        version  = envelope[0:1]
        if version != _FORMAT_VERSION:
            raise ValueError(f"Unknown envelope version: {version!r}")

        kek_id   = envelope[1:17].rstrip(b"\x00").decode()
        iv       = envelope[17:29]
        tag      = envelope[29:45]
        enc_dek  = envelope[45:93]
        body     = envelope[93:]

        dek = self._ks.decrypt_dek(kek_id, enc_dek)
        aesgcm = AESGCM(dek)
        plaintext = aesgcm.decrypt(iv, body + tag, None)
        return plaintext.decode()

    def rotate(self, envelope_b64: str) -> str:
        """Re-encrypt the field DEK with the current primary KEK."""
        envelope = base64.urlsafe_b64decode(envelope_b64.encode())
        rotated  = self._ks.rotate_dek(envelope)
        return base64.urlsafe_b64encode(rotated).decode()


# ─────────────────────────────────────────────────────────────────────────────
# SQLAlchemy TypeDecorator
# ─────────────────────────────────────────────────────────────────────────────

class EncryptedString(TypeDecorator):
    """
    SQLAlchemy column type that transparently encrypts on write and decrypts on read.

    Usage:
        class Supplier(Base):
            contact_email: Mapped[str] = mapped_column(EncryptedString(enc))
            tax_id:        Mapped[str] = mapped_column(EncryptedString(enc))
    """
    impl     = String
    cache_ok = True

    def __init__(self, encryption: FieldEncryption) -> None:
        super().__init__()
        self._enc = encryption

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return self._enc.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        try:
            return self._enc.decrypt(value)
        except Exception as exc:
            logger.error("field_decryption_failed", error=str(exc))
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton helpers
# ─────────────────────────────────────────────────────────────────────────────

_default_enc: FieldEncryption | None = None


def _enc() -> FieldEncryption:
    global _default_enc
    if _default_enc is None:
        _default_enc = FieldEncryption.from_env()
    return _default_enc


def encrypt_field(value: str) -> str:
    return _enc().encrypt(value)


def decrypt_field(envelope: str) -> str:
    return _enc().decrypt(envelope)


def rotate_field(envelope: str) -> str:
    return _enc().rotate(envelope)
