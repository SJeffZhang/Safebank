"""E0b T2a: evaluation harness — real gateway against a real bank backend.

W1's test harness (``tests/gateway/w1/db/support.py``) drives the real gateway
over a real gateway PostgreSQL but points the L3 client at a ``FakeL3``. This
module swaps that one piece for a real bank backend: it seeds ``seed_path``
into the isolated obligations database (the same ``load_seed`` path as T1) and
serves ``services.bank-backend`` on a free port via uvicorn, then hands the
gateway a real ``L3Client`` pointed at it.

The yielded ``EvalHarness`` exposes the gateway ASGI client plus
``new_session`` / ``register_utterance``; the latter returns the assistant JWT
that the gateway itself issued for the utterance.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import uvicorn
from app import models as m
from app.db import engine as bank_engine
from app.main import create_app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from evaluation.runner.seed import load_seed, read_seed
from services.gateway.app import locks
from services.gateway.app.auth.pin import HASHER
from services.gateway.app.config import from_env
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime

ENV = {
    "FRONTEND_SERVICE_TOKEN": "f-test",
    "AGENT_SERVICE_TOKEN": "a-test",
    "SCHEDULER_SERVICE_TOKEN": "s-test",
    "DEMO_CONSOLE_TOKEN": "d" * 32,
    "DEMO_MODE": "true",
    "APPROVAL_HMAC_KEY": "approval-hmac-key-32-bytes-minimum!!",
    "OTP_HMAC_KEY": "otp-hmac-key-32-bytes-minimum!!!!!",
}

L3_SERVICE_TOKEN = "e0b-harness-l3-token"
FINGERPRINT_KEY = "test-fingerprint-key"
PIN = "123456"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app: FastAPI, port: int) -> None:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()


async def _truncate(admin: AsyncEngine) -> None:
    names = ",".join(m.metadata.tables)
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))


async def _serve_l3(obligations_db, seed: dict):
    """Seed the obligations DB and serve the real bank backend; return engines + client."""
    admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = bank_engine(obligations_db.runtime_url, poolclass=NullPool)
    await _truncate(admin)
    async with admin.connect() as conn:
        await load_seed(conn, seed, fingerprint_key=FINGERPRINT_KEY)
    app = create_app(db=runtime, service_token=L3_SERVICE_TOKEN, fingerprint_key=FINGERPRINT_KEY)
    port = _free_port()
    _serve(app, port)
    base = f"http://127.0.0.1:{port}"
    l3 = L3Client(base, L3_SERVICE_TOKEN)
    async with httpx.AsyncClient(base_url=base) as client:
        for _ in range(40):
            try:
                probe = await client.get(
                    "/readyz", headers={"X-Service-Token": L3_SERVICE_TOKEN}, timeout=2.0
                )
                break
            except httpx.TransportError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("uvicorn listener never became ready")
        assert probe.status_code == 200, probe.text
    return admin, runtime, l3


def _headers(token: str | None = None, service: str = "f-test") -> dict:
    result = {"X-Service-Token": service}
    if token:
        result["Authorization"] = "Bearer " + token
    return result


async def _login(client: httpx.AsyncClient, customer_id: str) -> str:
    response = await client.post(
        "/auth/login", headers=_headers(), json={"customer_id": customer_id, "pin": PIN}
    )
    assert response.status_code == 200, response.text
    return response.json()["app_token"]


async def _create_session(client: httpx.AsyncClient, token: str) -> str:
    response = await client.post(
        "/sessions", headers=_headers(token), json={"request_id": "req-" + secrets.token_hex(8)}
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


@dataclass
class EvalHarness:
    app: FastAPI
    client: httpx.AsyncClient
    database: AsyncEngine
    private: bytes
    public: bytes
    l3: L3Client
    _admin: AsyncEngine
    _runtime: AsyncEngine
    _clock: list[datetime]
    _tokens: dict[str, str] = field(default_factory=dict)

    async def new_session(self, customer_id: str) -> str:
        token = await _login(self.client, customer_id)
        sid = await _create_session(self.client, token)
        self._tokens[sid] = token
        return sid

    async def register_utterance(self, sid: str, text: str) -> tuple[str, str]:
        token = self._tokens[sid]
        response = await self.client.post(
            f"/sessions/{sid}/utterances",
            headers=_headers(token),
            json={"request_id": "req-" + secrets.token_hex(8), "kind": "typed", "text": text},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return body["utterance"]["id"], body["assistant_token"]


@asynccontextmanager
async def eval_harness(gateway_db, obligations_db, seed_path, monkeypatch):
    seed = read_seed(seed_path)
    admin, runtime_l3, client_l3 = await _serve_l3(obligations_db, seed)
    database = engine(gateway_db.runtime_url, pool_size=18, max_overflow=0)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    clock = [datetime.now(UTC)]
    pin_hashes = {customer["id"]: HASHER.hash(PIN) for customer in seed["customers"]}
    settings = from_env(ENV)
    runtime = GatewayRuntime(
        settings,
        database,
        client_l3,
        lambda: clock[0],
        private,
        public,
        pin_hashes,
        approval_hmac_key=b"approval-hmac-key-32-bytes-minimum!!",
        otp_hmac_key=b"otp-hmac-key-32-bytes-minimum!!!!!",
    )
    app = build_public_app(settings, env=ENV, runtime=runtime)
    locks.configure(database, max_executions=settings.max_executions)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("test-" + secrets.token_hex(8), 1234)),
            base_url="http://gateway",
        ) as client:
            yield EvalHarness(
                app, client, database, private, public, client_l3, admin, runtime_l3, clock
            )
    finally:
        await client_l3.close()
        await database.dispose()
        await admin.dispose()
        await runtime_l3.dispose()
