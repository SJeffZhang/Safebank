"""Encrypted simulated SMS payload, readable only by the controlled D5 handler."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..middleware import canonical_bytes

DOMAIN = b"safebank/otp-sms/aead/v1"


def _cipher(key: bytes) -> AESGCM:
    return AESGCM(hmac.new(key, DOMAIN, hashlib.sha256).digest())


def encrypt(key: bytes, approval_id: str, view_hash: str, code: str) -> str:
    nonce = secrets.token_bytes(12)
    aad = canonical_bytes({"approval_id": approval_id, "view_hash": view_hash})
    return base64.b64encode(nonce + _cipher(key).encrypt(nonce, code.encode(), aad)).decode()


def decrypt(key: bytes, approval_id: str, view_hash: str, encrypted: str) -> str:
    data = base64.b64decode(encrypted, validate=True)
    return (
        _cipher(key)
        .decrypt(
            data[:12],
            data[12:],
            canonical_bytes({"approval_id": approval_id, "view_hash": view_hash}),
        )
        .decode()
    )
