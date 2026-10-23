"""One process owns gateway resources; both network-isolated apps consume them.

The ASGI apps never own these resources. In production ``serve`` enters this
context once, starts both servers, stops both, then releases the shared clients.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import FastAPI
from safebank_contracts.codes import Code
from sqlalchemy.ext.asyncio import AsyncEngine

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


def _configured_pin_hashes() -> dict[str, str] | None:
    # W1 owns PIN parsing/hashing. Before that component is integrated the
    # resource remains absent; no successful substitute or public default PIN.
    name = "services.gateway.app.auth.pin"
    try:
        module = import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise
    return module.configured_hashes()


@dataclass(frozen=True)
class GatewayRuntime:
    settings: Settings
    db: AsyncEngine
    l3: L3Client
    wall_now: Callable[[], datetime]
    jwt_private_key: bytes
    jwt_public_key: bytes
    pin_hashes: dict[str, str] | None

    def install(self, app: FastAPI) -> None:
        app.state.settings = self.settings
        app.state.db = self.db
        app.state.l3 = self.l3
        app.state.wall_now = self.wall_now
        app.state.jwt_private_key = self.jwt_private_key
        app.state.jwt_public_key = self.jwt_public_key
        if self.pin_hashes is not None:
            app.state.pin_hashes = self.pin_hashes


@asynccontextmanager
async def gateway_runtime(
    settings: Settings,
    *,
    db_factory: Callable = engine,
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
    hashes = _configured_pin_hashes() if pin_hashes is None else pin_hashes
    async with AsyncExitStack() as stack:
        db = db_factory(settings.database_url)
        stack.push_async_callback(db.dispose)
        l3 = l3_factory(settings.l3_base_url, settings.gateway_service_token)
        stack.push_async_callback(l3.close)
        yield GatewayRuntime(settings, db, l3, wall_now, private, public, hashes)
