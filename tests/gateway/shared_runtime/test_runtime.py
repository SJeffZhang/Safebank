import asyncio
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from safebank_contracts.codes import Code

from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError
from services.gateway.app.main import build_internal_app, build_public_app


@pytest.fixture
def secret_settings(tmp_path):
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return from_env(
        {
            "JWT_PRIVATE_KEY_PATH": str(private_path),
            "JWT_PUBLIC_KEY_PATH": str(public_path),
            "GATEWAY_DATABASE_URL": "postgresql://unused/db",
            "GATEWAY_SERVICE_TOKEN": "l3-token",
        }
    )


class Engine:
    def __init__(self):
        self.closed = 0

    async def dispose(self):
        self.closed += 1


class Client:
    def __init__(self, url, token):
        assert url == "http://bank-backend:8300"
        assert token == "l3-token"
        self.closed = 0

    async def close(self):
        self.closed += 1


def test_runtime_owns_resources_once_and_both_apps_consume_them(secret_settings):
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        db, clients = Engine(), []

        def make_client(url, token):
            client = Client(url, token)
            clients.append(client)
            return client

        async with gateway_runtime(
            secret_settings,
            db_factory=lambda url: db,
            l3_factory=make_client,
            pin_hashes={"cus_zs": "hash"},
        ) as runtime:
            public = build_public_app(secret_settings, runtime=runtime)
            internal = build_internal_app(secret_settings, runtime=runtime)
            assert public.state.settings is internal.state.settings is secret_settings
            assert public.state.db is internal.state.db is db
            assert public.state.l3 is internal.state.l3 is clients[0]
            assert public.state.jwt_private_key.startswith(b"-----BEGIN PRIVATE KEY-----")
            assert public.state.pin_hashes == {"cus_zs": "hash"}
            assert public.state.wall_now().utcoffset().total_seconds() == 0
            async with public.router.lifespan_context(public):
                async with internal.router.lifespan_context(internal):
                    pass
            assert db.closed == clients[0].closed == 0
        assert db.closed == clients[0].closed == 1

    asyncio.run(run())


def test_partial_start_failure_disposes_created_pool(secret_settings):
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        db = Engine()

        def broken_client(url, token):
            raise RuntimeError("transport startup failed")

        with pytest.raises(RuntimeError, match="transport startup"):
            async with gateway_runtime(
                secret_settings, db_factory=lambda url: db, l3_factory=broken_client, pin_hashes={}
            ):
                pytest.fail("startup must fail")
        assert db.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["missing", "permissions", "mismatch", "malformed", "symlink"])
def test_secret_key_faults_refuse_start_before_creating_pool(secret_settings, tmp_path, fault):
    from pathlib import Path

    from services.gateway.app.runtime import gateway_runtime

    path = Path(secret_settings.jwt_private_key_path)
    if fault == "missing":
        path.unlink()
    elif fault == "permissions":
        path.chmod(0o644)
    elif fault == "mismatch":
        other = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        Path(secret_settings.jwt_public_key_path).write_bytes(other)
    elif fault == "malformed":
        path.write_bytes(b"private-secret-must-not-appear")
    else:
        path.rename(tmp_path / "target")
        path.symlink_to(tmp_path / "target")

    def forbidden_pool(url):
        pytest.fail("secret validation must precede pool allocation")

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with gateway_runtime(secret_settings, db_factory=forbidden_pool, pin_hashes={}):
                pytest.fail("must refuse invalid key")
        assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
        assert "private-secret" not in str(exc.value)

    asyncio.run(run())


def test_missing_runtime_configuration_refuses_start(secret_settings):
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        for settings in [
            replace(secret_settings, database_url=None),
            replace(secret_settings, gateway_service_token=""),
        ]:
            with pytest.raises(GatewayError):
                async with gateway_runtime(settings, pin_hashes={}):
                    pytest.fail("must fail closed")

    asyncio.run(run())


def test_unavailable_w1_pin_loader_is_unconfigured_not_success(secret_settings, monkeypatch):
    import services.gateway.app.runtime as module

    original = module.import_module

    def missing(name):
        if name.endswith(".auth.pin"):
            raise ModuleNotFoundError(name=name)
        return original(name)

    monkeypatch.setattr(module, "import_module", missing)

    async def run():
        async with module.gateway_runtime(
            secret_settings, db_factory=lambda url: Engine(), l3_factory=Client
        ) as runtime:
            assert runtime.pin_hashes is None

    asyncio.run(run())


def test_pin_loader_failure_refuses_start(secret_settings, monkeypatch):
    from types import SimpleNamespace

    import services.gateway.app.runtime as module

    def broken():
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)

    monkeypatch.setattr(
        module, "import_module", lambda name: SimpleNamespace(configured_hashes=broken)
    )

    async def run():
        with pytest.raises(GatewayError):
            async with module.gateway_runtime(secret_settings):
                pytest.fail("invalid PIN config must fail startup")

    asyncio.run(run())


def test_shared_real_pool_and_l3_client_are_usable_then_closed(secret_settings, gateway_db):
    import httpx
    from sqlalchemy import text

    from services.gateway.app.db import engine
    from services.gateway.app.l3.client import L3Client
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        def reply(request):
            assert request.headers["X-Service-Token"] == "l3-token"
            return httpx.Response(200, json={"data": {"accounts": []}})

        def client(url, token):
            return L3Client(url, token, transport=httpx.MockTransport(reply))

        settings = replace(secret_settings, database_url=gateway_db.runtime_url)
        async with gateway_runtime(
            settings, db_factory=engine, l3_factory=client, pin_hashes={}
        ) as runtime:
            public = build_public_app(settings, runtime=runtime)
            internal = build_internal_app(
                settings, env={"SCHEDULER_SERVICE_TOKEN": "s"}, runtime=runtime
            )
            async with public.state.db.connect() as conn:
                assert await conn.scalar(text("SELECT 42")) == 42
            assert await internal.state.l3.read("get_accounts", {}) == {"data": {"accounts": []}}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(public), base_url="http://t"
            ) as c:
                assert (await c.get("/health")).status_code == 200
                assert (await c.get("/internal/health")).status_code == 404
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(internal), base_url="http://t"
            ) as c:
                assert (await c.get("/health")).status_code == 404
                assert (
                    await c.get("/internal/health", headers={"X-Service-Token": "s"})
                ).status_code == 200
        with pytest.raises(RuntimeError, match="client has been closed"):
            await runtime.l3.read("get_accounts", {})

    asyncio.run(run())


@pytest.mark.parametrize("executions,want", [(1, 18), (8, 18), (20, 42)])
def test_production_pool_capacity_supports_locks_and_short_transactions(
    secret_settings, executions, want
):
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        async with gateway_runtime(
            replace(secret_settings, max_executions=executions), l3_factory=Client, pin_hashes={}
        ) as runtime:
            assert runtime.db.pool.size() == want
            assert runtime.db.pool._max_overflow == 0

    asyncio.run(run())


def test_available_lock_hook_uses_existing_pool_and_execution_limit(secret_settings, monkeypatch):
    from services.gateway.app import locks
    from services.gateway.app.runtime import gateway_runtime

    configured = []

    def configure(db, max_executions):
        configured.append((db, max_executions))

    monkeypatch.setattr(locks, "configure", configure, raising=False)

    async def run():
        async with gateway_runtime(secret_settings, l3_factory=Client, pin_hashes={}) as runtime:
            assert configured == [(runtime.db, 8)]

    asyncio.run(run())


def test_lock_configuration_failure_releases_pool(secret_settings, monkeypatch):
    from services.gateway.app import locks
    from services.gateway.app.runtime import gateway_runtime

    db = Engine()

    def configure(db, max_executions):
        raise RuntimeError("lock configuration failed")

    monkeypatch.setattr(locks, "configure", configure, raising=False)

    async def run():
        with pytest.raises(RuntimeError, match="lock configuration failed"):
            async with gateway_runtime(
                secret_settings, db_factory=lambda url: db, l3_factory=Client, pin_hashes={}
            ):
                pytest.fail("startup must fail")
        assert db.closed == 1

    asyncio.run(run())
