import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError
from services.gateway.app.main import build_internal_app, build_public_app, load_public_key

ENV = {"SCHEDULER_SERVICE_TOKEN": "sched-secret"}


def _settings(**overrides):
    base = {"DEMO_MODE": "false", "EVAL_MODE": "false", "GATEWAY_PROFILE": "B2"}
    return from_env({**base, **overrides})


def test_public_app_never_registers_internal_routes():
    async def run():
        transport = httpx.ASGITransport(app=build_public_app(_settings()))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.get("/health")).status_code == 200
            resp = await client.get("/internal/health")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"

    asyncio.run(run())


def test_internal_app_only_has_internal_routes():
    async def run():
        transport = httpx.ASGITransport(app=build_internal_app(_settings(), env=ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/health")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"
            assert (await client.get("/internal/health")).status_code == 401
            ok = await client.get("/internal/health", headers={"X-Service-Token": "sched-secret"})
            assert ok.status_code == 200

    asyncio.run(run())


def test_scheduled_authorize_is_registered_but_stubbed():
    async def run():
        transport = httpx.ASGITransport(app=build_internal_app(_settings(), env=ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/internal/scheduled/authorize", headers={"X-Service-Token": "sched-secret"}
            )
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    asyncio.run(run())


def test_public_routes_require_auth_probe_a():
    async def run():
        transport = httpx.ASGITransport(app=build_public_app(_settings()))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post("/plans/evaluate", json={})
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
            resp = await client.get("/approvals/apv_1")
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "UNAUTHENTICATED"

    asyncio.run(run())


def test_load_public_key_missing_refuses_r1():
    with pytest.raises(GatewayError):
        load_public_key("/nonexistent/does/not/exist.pem")


def test_valid_jwt_with_public_key_passes_auth_r1():
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    app_token = sign_jwt(private_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti-r1")

    async def run():
        env = {"FRONTEND_SERVICE_TOKEN": "frontend-token"}
        app = build_public_app(_settings(), env=env, public_key=public_pem)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/sessions",
                json={},
                headers={
                    "X-Service-Token": "frontend-token",
                    "Authorization": f"Bearer {app_token}",
                },
            )
            # Auth passed; the W1 stub responds 503.
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    asyncio.run(run())
