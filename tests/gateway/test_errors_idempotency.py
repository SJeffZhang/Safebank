import asyncio

import httpx
import pytest
from fastapi import FastAPI
from safebank_contracts.codes import Code

from services.gateway.app.errors import GatewayError
from services.gateway.app.middleware import IdempotencyStore, install_middleware


def _app():
    app = FastAPI(docs_url=None, redoc_url=None)
    install_middleware(app)

    @app.get("/boom")
    async def boom():
        raise GatewayError(Code.NOT_FOUND, 404, "missing thing", {"k": "v"})

    return app


def test_error_envelope_and_trace_id_echo():
    async def run():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/boom", headers={"X-Request-ID": "trace-abc"})
            assert resp.status_code == 404
            assert resp.headers["X-Request-ID"] == "trace-abc"
            body = resp.json()
            assert body == {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "missing thing",
                    "details": {"k": "v"},
                    "trace_id": "trace-abc",
                }
            }

    asyncio.run(run())


def test_missing_request_id_gets_generated():
    async def run():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/boom")
            assert resp.status_code == 404
            assert resp.headers["X-Request-ID"]
            assert resp.json()["error"]["trace_id"] == resp.headers["X-Request-ID"]

    asyncio.run(run())


def test_idempotency_same_body_replays():
    async def run():
        store = IdempotencyStore()

        async def produce():
            return {"decision": "ALLOW"}

        first = await store.resolve("plans", "req-1", {"steps": []}, produce)
        second = await store.resolve("plans", "req-1", {"steps": []}, produce)
        assert first == second == {"decision": "ALLOW"}

    asyncio.run(run())


def test_idempotency_different_body_conflicts():
    async def run():
        store = IdempotencyStore()

        async def produce():
            return {"decision": "ALLOW"}

        await store.resolve("plans", "req-1", {"a": 1}, produce)
        with pytest.raises(GatewayError) as exc:
            await store.resolve("plans", "req-1", {"a": 2}, produce)
        assert exc.value.code is Code.IDEMPOTENCY_CONFLICT
        assert exc.value.status == 409

    asyncio.run(run())


def test_idempotency_scope_isolates_request_ids():
    async def run():
        store = IdempotencyStore()

        async def produce():
            return "x"

        assert await store.resolve("plans", "req-1", {"a": 1}, produce) == "x"
        assert await store.resolve("sessions", "req-1", {"a": 1}, produce) == "x"

    asyncio.run(run())
