"""Real W4a locks, S1 runtime, auth and state; only L3 uses a contract fake."""

import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.gateway.app import locks
from services.gateway.app.auth.pin import HASHER
from services.gateway.app.auth.tokens import sign_jwt
from services.gateway.app.config import from_env
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime
from tests.gateway.w1.fixtures import bank_state

ENV = {
    "FRONTEND_SERVICE_TOKEN": "f-test",
    "AGENT_SERVICE_TOKEN": "a-test",
    "SCHEDULER_SERVICE_TOKEN": "s-test",
    "DEMO_CONSOLE_TOKEN": "d" * 32,
    "DEMO_MODE": "true",
}


@asynccontextmanager
async def harness(gateway_db, monkeypatch):
    database = engine(gateway_db.runtime_url, pool_size=18, max_overflow=0)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    clock = [datetime.now(UTC)]
    fake = FakeL3()
    fake.set_snapshot(bank_state())
    client_l3 = L3Client("http://bank", fake.token, transport=fake.transport())
    settings = from_env(ENV)
    runtime = GatewayRuntime(
        settings,
        database,
        client_l3,
        lambda: clock[0],
        private,
        public,
        {"cus_zs": HASHER.hash("123456"), "cus_ls": HASHER.hash("234567")},
    )
    app = build_public_app(settings, env=ENV, runtime=runtime)

    locks.configure(database, max_executions=settings.max_executions)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("test-" + secrets.token_hex(8), 1234)),
        base_url="http://gateway",
    ) as client:
        yield app, client, database, private, public, clock, fake
    await client_l3.close()
    await database.dispose()


def headers(token=None, service="f-test"):
    result = {"X-Service-Token": service}
    if token:
        result["Authorization"] = "Bearer " + token
    return result


async def login(client, customer="cus_zs"):
    response = await client.post(
        "/auth/login",
        headers=headers(),
        json={"customer_id": customer, "pin": "123456" if customer == "cus_zs" else "234567"},
    )
    assert response.status_code == 200, response.text
    return response.json()["app_token"]


async def create(client, token, request_id="req_create"):
    response = await client.post(
        "/sessions", headers=headers(token), json={"request_id": request_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


async def utter(client, token, sid, text, request_id="req_utter", **kwargs):
    return await client.post(
        f"/sessions/{sid}/utterances",
        headers=headers(token),
        json={"request_id": request_id, "kind": "typed", "text": text, **kwargs},
    )


def assistant_token(private, sid, ids, customer="cus_zs"):
    return sign_jwt(
        private, sub=customer, aud="safebank-assistant", scope=["assistant"], sid=sid, utts=ids
    )
