"""OTP generation, AEAD encryption, HMAC verification and rate-limit helpers.

No I/O, no globals, no database access.  Every function is deterministic given
its inputs (``generate_otp_code`` uses ``secrets.randbelow`` — cryptographically
secure randomness, not deterministic).

**Code never appears in plaintext in responses/logs/audit.**  The plaintext
exists only in memory during generation; the caller of ``decrypt_otp_code`` (D5
demo console, W5) is responsible for not logging it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ── Constants (R0: PRD §7.10 parameters) ──────────────────────────────────────

OTP_CODE_LENGTH = 6
OTP_TTL = timedelta(minutes=2)  # 2-minute expiry
OTP_CIPHERTEXT_TTL = timedelta(minutes=5)  # 5-min AEAD ciphertext lifetime for D5
OTP_MAX_SENDS = 3  # max per approval (R1: additional sends return RATE_LIMITED)
OTP_SEND_INTERVAL = timedelta(seconds=30)  # minimum interval between sends
OTP_MAX_ATTEMPTS = 5  # cumulative wrong attempts → LOCKED (R1: never resets)


# ── Key derivation (domain-separated HKDF) ────────────────────────────────────


def derive_otp_keys(master_key: bytes) -> tuple[bytes, bytes]:
    """Derive two independent 32-byte keys from the master OTP key.

    Returns ``(code_digest_key, aead_key)`` — deterministic, domain-separated
    via HKDF-SHA256 with distinct info strings.
    """
    digest_hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"safebank-otp-code-digest",
    ).derive(master_key)
    aead_hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"safebank-otp-ciphertext",
    ).derive(master_key)
    return digest_hkdf, aead_hkdf


# ── Code generation ───────────────────────────────────────────────────────────


def generate_otp_code() -> str:
    """6-digit zero-padded string using ``secrets.randbelow`` (cryptographically secure)."""
    return f"{secrets.randbelow(1_000_000):06d}"


# ── Code digest (HMAC-SHA256) ─────────────────────────────────────────────────


def compute_code_digest(code: str, approval_id: str, key: bytes) -> str:
    """HMAC-SHA256 over ``code || approval_id``.

    The key is the domain-separated ``code_digest_key`` from :func:`derive_otp_keys`.
    """
    payload = f"{code}||{approval_id}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


# ── AEAD encryption (AES-256-GCM) ─────────────────────────────────────────────


def _otp_aad(approval_id: str, plan_id: str, view_hash: str, wall_now: datetime) -> str:
    """Canonical AAD string binding ciphertext to its approval, plan, view and time.

    R5: AAD includes approval_id, plan_id, view_hash.
    Key-sorted JSON for deterministic serialisation; no nonces or secrets inside.
    """
    wall_str = wall_now.isoformat()
    return json.dumps(
        {
            "approval_id": approval_id,
            "plan_id": plan_id,
            "view_hash": view_hash,
            "wall_now": wall_str,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def encrypt_otp_code(
    code: str,
    *,
    approval_id: str,
    plan_id: str,
    view_hash: str,
    wall_now: datetime,
    key: bytes,
) -> str:
    """AES-256-GCM encrypt the plaintext OTP code.

    Returns hex-encoded ``nonce || ciphertext``. AAD binds the ciphertext to the
    approval, plan, view and wall-clock time — any mismatch during decryption
    causes AEAD tag validation failure → ``None``.
    """
    nonce = secrets.token_bytes(12)
    aad = _otp_aad(approval_id, plan_id, view_hash, wall_now).encode("utf-8")
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, code.encode("utf-8"), aad)
    return (nonce + ciphertext).hex()


def decrypt_otp_code(
    ciphertext_hex: str,
    *,
    approval_id: str,
    plan_id: str,
    view_hash: str,
    wall_now: datetime,
    key: bytes,
) -> str | None:
    """Decrypt a ciphertext previously produced by :func:`encrypt_otp_code`.

    Returns the plaintext code on success, or ``None`` on any AEAD / AAD
    mismatch.  This function is exported for D5 demo-console use (W5); the
    gateway never calls it during normal operation.
    """
    try:
        raw = bytes.fromhex(ciphertext_hex)
    except ValueError:
        return None
    if len(raw) < 12:
        return None
    nonce, ct = raw[:12], raw[12:]
    aad = _otp_aad(approval_id, plan_id, view_hash, wall_now).encode("utf-8")
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct, aad)
        return plaintext.decode("utf-8")
    except Exception:
        return None


# ── Verification (constant-time) ──────────────────────────────────────────────


def verify_otp(
    challenge: Mapping[str, object],
    submitted_code: str,
    approval_id: str,
    key: bytes,
) -> bool:
    """Constant-time OTP verification via ``hmac.compare_digest``.

    *challenge* is a mapping that must contain ``"code_digest"`` (the HMAC
    from :func:`compute_code_digest`).  The stored digest is never trusted
    directly — we recompute from *submitted_code* and compare.
    """
    expected = compute_code_digest(submitted_code, approval_id, key)
    stored = challenge.get("code_digest", "")
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8", errors="replace")
    return hmac.compare_digest(str(stored).encode("utf-8"), expected.encode("utf-8"))


# ── Rate-limit helpers ────────────────────────────────────────────────────────


def otp_retry_after(
    last_sent_at_wall: datetime | None,
    sent_count: int,
    now: datetime,
) -> int | None:
    """Determine whether a new OTP can be sent.

    Returns:
        ``None`` — the caller may send immediately.
        ``int`` — seconds the caller must wait before retrying.

    Raises:
        ``ValueError`` — *sent_count* ≥ 3, exhausted (R1: no more sends after 3).
    """
    if sent_count >= OTP_MAX_SENDS:
        raise ValueError("OTP send limit exhausted")
    if last_sent_at_wall is None:
        return None
    elapsed = now - last_sent_at_wall
    if elapsed >= OTP_SEND_INTERVAL:
        return None
    remaining = OTP_SEND_INTERVAL - elapsed
    return int(remaining.total_seconds()) + (1 if remaining.microseconds > 0 else 0)


def otp_attempts_exceeded(attempt_count: int) -> bool:
    """True when *attempt_count* ≥ 5 (R1: cumulative, never resets on resend)."""
    return attempt_count >= OTP_MAX_ATTEMPTS
