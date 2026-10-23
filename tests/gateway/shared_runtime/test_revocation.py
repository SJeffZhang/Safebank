import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI
from safebank_contracts.codes import Code
from sqlalchemy.exc import OperationalError

from services.gateway.app.auth import tokens
from services.gateway.app.auth.deps import caller_dependency
from services.gateway.app.auth.tokens import APP_AUDIENCE, ASSISTANT_AUDIENCE, sign_jwt
from services.gateway.app.errors import GatewayError
from services.gateway.app.middleware import install_middleware

NOW = datetime.now(UTC)
ENV = {"FRONTEND_SERVICE_TOKEN": "f", "AGENT_SERVICE_TOKEN": "a", "SCHEDULER_SERVICE_TOKEN": "s"}


class Db:
    @asynccontextmanager
    async def connect(self):
        yield object()


@pytest.mark.parametrize(
    "status,want",
    [
        ("active", 200),
        ("revoked", 401),
        ("missing", 401),
        ("expired", 401),
        ("missing_session", 401),
        ("db_failure", 503),
    ],
)
@pytest.mark.parametrize(
    "aud,service,path,method,scope",
    [
        (APP_AUDIENCE, "f", "/audit", "GET", "app"),
        (ASSISTANT_AUDIENCE, "a", "/plans/evaluate", "POST", "assistant"),
    ],
)
def test_revocation_is_checked_before_customer_handler(
    monkeypatch, status, want, aud, service, path, method, scope
):
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # Signature-compatible substitute verifies wiring only; W1 owns persistent semantics.
    async def check_revocation(conn, caller, *, now=None):
        assert conn is not None and caller.customer_id == "cus_zs"
        assert now == NOW
        if status == "db_failure":
            raise OperationalError(
                "private database details", {}, Exception("private database details")
            )
        if status != "active":
            code = Code.TOKEN_EXPIRED if status == "expired" else Code.UNAUTHENTICATED
            raise GatewayError(code, 401)

    monkeypatch.setattr(tokens, "check_revocation", check_revocation, raising=False)
    app = FastAPI(dependencies=[Depends(caller_dependency(ENV, public))])
    install_middleware(app)
    app.state.db, app.state.wall_now = Db(), lambda: NOW

    @app.api_route(path, methods=[method])
    async def endpoint():
        return {"entered": True}

    token = sign_jwt(
        pem,
        sub="cus_zs",
        aud=aud,
        scope=[scope],
        jti="jti_test",
        sid="ses_test" if service == "a" else None,
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://t"
        ) as client:
            resp = await client.request(
                method,
                path,
                headers={"X-Service-Token": service, "Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == want
            if want == 200:
                assert resp.json() == {"entered": True}
            else:
                assert "entered" not in resp.json()
                assert "private database details" not in resp.text

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["db", "checker", "clock"])
def test_valid_jwt_missing_revocation_dependencies_is_denied(monkeypatch, missing):
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    async def check_revocation(conn, caller, *, now=None):
        return None

    monkeypatch.setattr(tokens, "check_revocation", check_revocation, raising=False)
    if missing == "checker":
        monkeypatch.delattr(tokens, "check_revocation")
    app = FastAPI(dependencies=[Depends(caller_dependency(ENV, public))])
    install_middleware(app)
    if missing != "db":
        app.state.db = Db()
    if missing != "clock":
        app.state.wall_now = lambda: NOW

    @app.get("/audit")
    async def endpoint():
        return {"entered": True}

    token = sign_jwt(pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti_test")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://t"
        ) as client:
            resp = await client.get(
                "/audit", headers={"X-Service-Token": "f", "Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    asyncio.run(run())


def test_service_only_path_does_not_require_customer_database():
    app = FastAPI(dependencies=[Depends(caller_dependency(ENV))])
    install_middleware(app)

    @app.get("/internal/health")
    async def health():
        return {"ok": True}

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://t"
        ) as client:
            assert (await client.get("/internal/health")).status_code == 401
            resp = await client.get("/internal/health", headers={"X-Service-Token": "s"})
            assert resp.status_code == 200

    asyncio.run(run())


def test_console_jwt_is_not_a_customer_login():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    app = FastAPI(dependencies=[Depends(caller_dependency({"DEMO_CONSOLE_TOKEN": "d"}, public))])
    install_middleware(app)

    @app.get("/demo/plans")
    async def console():
        return {"console": True}

    token = sign_jwt(pem, sub="operator", aud="demo-console", scope=["console"], jti="console_jti")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://t"
        ) as client:
            resp = await client.get(
                "/demo/plans", headers={"X-Service-Token": "d", "Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200

    asyncio.run(run())
