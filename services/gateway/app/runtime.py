"""One process owns gateway resources; both network-isolated apps consume them.

The ASGI apps never own these resources. In production ``serve`` enters this
context once, starts both servers, stops both, then releases the shared clients.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import FastAPI
from safebank_contracts.codes import Code
from sqlalchemy.ext.asyncio import AsyncEngine

from . import locks
from .config import Settings
from .db import engine
from .errors import GatewayError
from .l3.client import L3Client


def _unavailable(message: str) -> GatewayError:
    return GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, message=message)


def private_key_bytes(path: str) -> bytes:
    """Read a regular 0600 secret without following symlinks or racing stat/open."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("secret must be a regular 0600 file")
            return source.read()
    except (OSError, ValueError) as exc:
        raise _unavailable("JWT signing key unavailable or unsafe permissions") from exc


def _key_pair(settings: Settings) -> tuple[bytes, bytes]:
    private = private_key_bytes(settings.jwt_private_key_path)
    try:
        public = Path(settings.jwt_public_key_path).read_bytes()
        signing_key = serialization.load_pem_private_key(private, password=None)
        verify_key = serialization.load_pem_public_key(public)
        if not isinstance(signing_key, Ed25519PrivateKey) or not isinstance(
            verify_key, Ed25519PublicKey
        ):
            raise ValueError("Ed25519 keys required")
        encoding = serialization.Encoding.Raw
        format_ = serialization.PublicFormat.Raw
        if signing_key.public_key().public_bytes(encoding, format_) != verify_key.public_bytes(
            encoding, format_
        ):
            raise ValueError("JWT key pair mismatch")
    except (OSError, ValueError, TypeError) as exc:
        raise _unavailable("JWT key pair unavailable or invalid") from exc
    return private, public


def _configured_pin_hashes(settings: Settings) -> dict[str, str] | None:
    # W1 owns PIN parsing/hashing. Before that component is integrated the
    # resource remains absent; no successful substitute or public default PIN.
    name = "services.gateway.app.auth.pin"
    try:
        module = import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise
    loader = getattr(module, "configured_hashes_from_file", None)
    if not callable(loader):
        raise _unavailable("PIN file loader unavailable")
    return loader(settings.demo_pins_file)


def _validate_approval_keys(settings: Settings) -> tuple[bytes, bytes]:
    """Validate approval/OTP HMAC keys before allocating any resource.

    Returns (approval_bytes, otp_bytes) on success. Raises GatewayError(503)
    with a generic message on any failure — the message never includes the key
    values.  Must be called before ``db_factory``, ``locks.configure`` or
    ``l3_factory`` so no pool, lock connection or L3 client is created for bad
    configuration.
    """
    approval = settings.approval_hmac_key
    otp = settings.otp_hmac_key

    if not isinstance(approval, str) or not approval.strip():
        raise _unavailable("approval HMAC key must not be empty or blank")
    if not isinstance(otp, str) or not otp.strip():
        raise _unavailable("OTP HMAC key must not be empty or blank")

    approval_bytes = approval.encode("utf-8")
    otp_bytes = otp.encode("utf-8")

    if len(approval_bytes) < 32:
        raise _unavailable("approval HMAC key must be at least 32 bytes")
    if len(otp_bytes) < 32:
        raise _unavailable("OTP HMAC key must be at least 32 bytes")

    if approval_bytes == otp_bytes:
        raise _unavailable("approval and OTP HMAC keys must be different")

    for token in settings.service_tokens.values():
        if token and approval_bytes == token.encode("utf-8"):
            raise _unavailable("approval HMAC key must not reuse a service token")
        if token and otp_bytes == token.encode("utf-8"):
            raise _unavailable("OTP HMAC key must not reuse a service token")

    if settings.gateway_service_token:
        if approval_bytes == settings.gateway_service_token.encode("utf-8"):
            raise _unavailable("approval HMAC key must not reuse the gateway service token")
        if otp_bytes == settings.gateway_service_token.encode("utf-8"):
            raise _unavailable("OTP HMAC key must not reuse the gateway service token")

    return approval_bytes, otp_bytes


@dataclass(frozen=True)
class GatewayRuntime:
    settings: Settings
    db: AsyncEngine
    l3: L3Client
    wall_now: Callable[[], datetime]
    jwt_private_key: bytes
    jwt_public_key: bytes
    pin_hashes: dict[str, str] | None
    approval_hmac_key: bytes = field(repr=False)
    otp_hmac_key: bytes = field(repr=False)

    def install(self, app: FastAPI) -> None:
        app.state.settings = self.settings
        app.state.db = self.db
        app.state.l3 = self.l3
        app.state.wall_now = self.wall_now
        app.state.jwt_private_key = self.jwt_private_key
        app.state.jwt_public_key = self.jwt_public_key
        app.state.approval_hmac_key = self.approval_hmac_key
        app.state.otp_hmac_key = self.otp_hmac_key
        if self.pin_hashes is not None:
            app.state.pin_hashes = self.pin_hashes


@asynccontextmanager
async def gateway_runtime(
    settings: Settings,
    *,
    db_factory: Callable | None = None,
    l3_factory: Callable = L3Client,
    wall_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    pin_hashes: dict[str, str] | None = None,
):
    """Allocate once and release in reverse order, even on partial startup failure."""
    settings.validate()
    if not settings.database_url or not settings.gateway_service_token:
        raise _unavailable("gateway database and L3 service credential required")
    now = wall_now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise _unavailable("gateway wall clock must be timezone-aware")
    private, public = _key_pair(settings)
    hashes = _configured_pin_hashes(settings) if pin_hashes is None else pin_hashes
    approval_key, otp_key = _validate_approval_keys(settings)
    async with AsyncExitStack() as stack:
        # One lock connection and one short-transaction slot per concurrent
        # executor, plus two slots for other traffic; PRD floor is eighteen.
        capacity = max(18, 2 * settings.max_executions + 2)
        db = (
            engine(settings.database_url, pool_size=capacity, max_overflow=0)
            if db_factory is None
            else db_factory(settings.database_url)
        )
        stack.push_async_callback(db.dispose)
        configure_locks = getattr(locks, "configure", None)
        if callable(configure_locks):
            configure_locks(db, max_executions=settings.max_executions)
        l3 = l3_factory(settings.l3_base_url, settings.gateway_service_token)
        stack.push_async_callback(l3.close)
        yield GatewayRuntime(
            settings, db, l3, wall_now, private, public, hashes, approval_key, otp_key
        )
