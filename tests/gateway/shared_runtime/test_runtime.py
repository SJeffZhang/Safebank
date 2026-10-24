import asyncio
import os
from dataclasses import replace
from functools import lru_cache
from types import SimpleNamespace

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
            "APPROVAL_HMAC_KEY": "approval-hmac-key-32-bytes-minimum!!",
            "OTP_HMAC_KEY": "otp-hmac-key-32-bytes-minimum!!!!!",
        }
    )


class Engine:
    def __init__(self):
        self.closed = 0

    async def dispose(self):
        self.closed += 1


@pytest.fixture
def fake_engine_lock_hook(monkeypatch):
    """Only lifecycle unit tests using Engine fake replace this dependency."""
    from services.gateway.app import locks

    configured = []

    def configure(db, max_executions):
        configured.append((db, max_executions))

    monkeypatch.setattr(locks, "configure", configure)
    return configured


class Client:
    def __init__(self, url, token):
        assert url == "http://bank-backend:8300"
        assert token == "l3-token"
        self.closed = 0

    async def close(self):
        self.closed += 1


@pytest.fixture
def fake_lock_configuration(monkeypatch):
    """Isolate only explicitly selected Fake Engine tests from the real lock pool."""
    from services.gateway.app import locks

    calls = []

    def configure(db, *, max_executions):
        calls.append((db, max_executions))

    monkeypatch.setattr(locks, "configure", configure, raising=False)
    return calls


def test_runtime_owns_resources_once_and_both_apps_consume_them(
    secret_settings, fake_engine_lock_hook
):
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
            assert fake_engine_lock_hook == [(db, secret_settings.max_executions)]
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


def test_partial_start_failure_disposes_created_pool(secret_settings, fake_engine_lock_hook):
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
        assert fake_engine_lock_hook == [(db, secret_settings.max_executions)]

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


def test_unavailable_w1_pin_loader_is_unconfigured_not_success(
    secret_settings, monkeypatch, fake_engine_lock_hook
):
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
            assert fake_engine_lock_hook == [(runtime.db, secret_settings.max_executions)]

    asyncio.run(run())


def test_pin_loader_failure_refuses_start(secret_settings, monkeypatch):
    from types import SimpleNamespace

    import services.gateway.app.runtime as module

    def broken(pins_file):
        assert pins_file == secret_settings.demo_pins_file
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)

    monkeypatch.setattr(
        module, "import_module", lambda name: SimpleNamespace(configured_hashes_from_file=broken)
    )

    async def run():
        with pytest.raises(GatewayError):
            async with module.gateway_runtime(secret_settings):
                pytest.fail("invalid PIN config must fail startup")

    asyncio.run(run())


def test_settings_pin_paths_ignore_opposite_environment_and_warm_legacy_cache(
    secret_settings, monkeypatch, tmp_path, fake_lock_configuration
):
    """Catch old env loading, cross-startup caching and request-time reloads.

    Main has no W1 module: these are adapter snapshots, not real PIN hashes.
    """
    import services.gateway.app.runtime as module

    first_path, second_path = str(tmp_path / "pins-a"), str(tmp_path / "pins-b")
    snapshots = {
        first_path: {"cus_zs": "adapter-hash-a"},
        second_path: {"cus_zs": "adapter-hash-b"},
    }
    loaded_paths = []

    @lru_cache(maxsize=1)
    def legacy_loader():
        return {"cus_zs": os.environ["DEMO_PIN_HASHES"]}

    monkeypatch.setenv("DEMO_PIN_HASHES", "legacy-cache-hash")
    assert legacy_loader() == {"cus_zs": "legacy-cache-hash"}

    def load_path(pins_file):
        loaded_paths.append(pins_file)
        return dict(snapshots[pins_file])

    monkeypatch.setattr(
        module,
        "import_module",
        lambda name: SimpleNamespace(
            configured_hashes=legacy_loader, configured_hashes_from_file=load_path
        ),
    )

    async def run():
        first_db, second_db, restarted_db = Engine(), Engine(), Engine()
        clients = []

        def make_client(url, token):
            client = Client(url, token)
            clients.append(client)
            return client

        first_settings = replace(secret_settings, demo_pins_file=first_path)
        second_settings = replace(secret_settings, demo_pins_file=second_path)
        monkeypatch.setenv("DEMO_PINS_FILE", second_path)
        monkeypatch.setenv("DEMO_PIN_HASHES", "opposite-environment-hash-b")
        async with module.gateway_runtime(
            first_settings, db_factory=lambda url: first_db, l3_factory=make_client
        ) as first:
            assert first.pin_hashes == {"cus_zs": "adapter-hash-a"}
            monkeypatch.setenv("DEMO_PINS_FILE", first_path)
            monkeypatch.setenv("DEMO_PIN_HASHES", "opposite-environment-hash-a")
            async with module.gateway_runtime(
                second_settings, db_factory=lambda url: second_db, l3_factory=make_client
            ) as second:
                assert second.pin_hashes == {"cus_zs": "adapter-hash-b"}
                snapshots[first_path] = {"cus_zs": "adapter-hash-a-replaced"}
                public = build_public_app(first_settings, runtime=first)
                internal = build_internal_app(first_settings, runtime=first)
                async with public.router.lifespan_context(public):
                    async with internal.router.lifespan_context(internal):
                        assert public.state.pin_hashes == {"cus_zs": "adapter-hash-a"}
                        assert internal.state.pin_hashes is first.pin_hashes
                        assert second.pin_hashes == {"cus_zs": "adapter-hash-b"}
                assert loaded_paths == [first_path, second_path]
                assert first_db.closed == second_db.closed == 0
        async with module.gateway_runtime(
            first_settings, db_factory=lambda url: restarted_db, l3_factory=make_client
        ) as restarted:
            assert restarted.pin_hashes == {"cus_zs": "adapter-hash-a-replaced"}
        assert loaded_paths == [first_path, second_path, first_path]
        assert first_db.closed == second_db.closed == restarted_db.closed == 1
        assert [client.closed for client in clients] == [1, 1, 1]
        assert fake_lock_configuration == [(first_db, 8), (second_db, 8), (restarted_db, 8)]

    asyncio.run(run())


@pytest.mark.parametrize("interface", ["missing", "not_callable"])
def test_existing_w1_without_path_loader_refuses_start_before_resource_allocation(
    secret_settings, monkeypatch, interface
):
    import services.gateway.app.runtime as module

    # The old entry point remains usable, so falling back would wrongly start.
    pin_module = SimpleNamespace(configured_hashes=lambda: {"cus_zs": "legacy-hash"})
    if interface == "not_callable":
        pin_module.configured_hashes_from_file = None
    monkeypatch.setattr(module, "import_module", lambda name: pin_module)

    def forbidden_resource(*args):
        pytest.fail("PIN interface validation must precede resource allocation")

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with module.gateway_runtime(
                secret_settings, db_factory=forbidden_resource, l3_factory=forbidden_resource
            ):
                pytest.fail("missing path loader must refuse startup")
        assert exc.value.status == 503
        assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["missing_file", "invalid_file"])
def test_w1_pin_configuration_error_propagates_without_fallback_or_allocating_resources(
    secret_settings, monkeypatch, tmp_path, fault
):
    import services.gateway.app.runtime as module

    pins_file = str(tmp_path / fault)
    failure = GatewayError(
        Code.DEPENDENCY_UNAVAILABLE, 503, message="PIN configuration unavailable"
    )

    def reject_path(selected_path):
        assert selected_path == pins_file
        # W1 owns actual file parsing/validation; model its documented failure.
        raise failure

    monkeypatch.setattr(
        module,
        "import_module",
        lambda name: SimpleNamespace(
            configured_hashes=lambda: {"cus_zs": "legacy-hash"},
            configured_hashes_from_file=reject_path,
        ),
    )

    def forbidden_resource(*args):
        pytest.fail("invalid PIN configuration must precede resource allocation")

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with module.gateway_runtime(
                replace(secret_settings, demo_pins_file=pins_file),
                db_factory=forbidden_resource,
                l3_factory=forbidden_resource,
            ):
                pytest.fail("invalid PIN configuration must refuse startup")
        assert exc.value is failure
        assert exc.value.status == 503
        assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE

    asyncio.run(run())


@pytest.mark.parametrize("injected", [{}, {"cus_zs": "explicit-hash"}])
def test_explicit_pin_hashes_bypass_w1_loader_including_empty_mapping(
    secret_settings, monkeypatch, injected, fake_lock_configuration
):
    import services.gateway.app.runtime as module

    def forbidden_import(name):
        pytest.fail("explicit pin_hashes must bypass configured loading")

    monkeypatch.setattr(module, "import_module", forbidden_import)

    async def run():
        db = Engine()
        async with module.gateway_runtime(
            secret_settings, db_factory=lambda url: db, l3_factory=Client, pin_hashes=injected
        ) as runtime:
            assert runtime.pin_hashes is injected
            assert fake_lock_configuration == [(db, 8)]
        assert db.closed == runtime.l3.closed == 1

    asyncio.run(run())


def test_loaded_pin_snapshot_does_not_prevent_partial_start_cleanup(
    secret_settings, monkeypatch, fake_lock_configuration
):
    import services.gateway.app.runtime as module

    monkeypatch.setattr(
        module,
        "import_module",
        lambda name: SimpleNamespace(configured_hashes_from_file=lambda path: {}),
    )

    async def run():
        db = Engine()

        def broken_client(url, token):
            assert fake_lock_configuration == [(db, 8)]
            raise RuntimeError("transport startup failed after PIN loading")

        with pytest.raises(RuntimeError, match="transport startup failed after PIN loading"):
            async with module.gateway_runtime(
                secret_settings, db_factory=lambda url: db, l3_factory=broken_client
            ):
                pytest.fail("L3 initialization failure must refuse startup")
        assert db.closed == 1

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


def test_production_pool_holds_eighteen_real_connections(secret_settings, gateway_db):
    from sqlalchemy import text

    from services.gateway.app.runtime import gateway_runtime

    async def run():
        ready, release = asyncio.Event(), asyncio.Event()
        backends = set()
        async with gateway_runtime(
            replace(secret_settings, database_url=gateway_db.runtime_url),
            l3_factory=Client,
            pin_hashes={},
        ) as runtime:

            async def hold():
                async with runtime.db.connect() as conn:
                    backends.add(await conn.scalar(text("SELECT pg_backend_pid()")))
                    if len(backends) == 18:
                        ready.set()
                    await release.wait()

            tasks = [asyncio.create_task(hold()) for _ in range(18)]
            try:
                await asyncio.wait_for(ready.wait(), 3)
                assert len(backends) == 18
            finally:
                release.set()
                await asyncio.gather(*tasks)

    asyncio.run(run())
