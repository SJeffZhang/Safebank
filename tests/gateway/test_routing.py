import asyncio

import httpx

from services.gateway.app.config import from_env
from services.gateway.app.main import build_internal_app, build_public_app

ENV = {"SCHEDULER_SERVICE_TOKEN": "sched-secret"}


def _settings():
    return from_env({"DEMO_MODE": "false", "EVAL_MODE": "false", "GATEWAY_PROFILE": "B2"})


def test_public_app_never_registers_internal_routes():
    async def run():
        transport = httpx.ASGITransport(app=build_public_app(_settings()))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/internal/health")).status_code == 404
            assert (await client.post("/internal/scheduled/authorize")).status_code == 404

    asyncio.run(run())


def test_internal_app_only_has_internal_routes():
    async def run():
        transport = httpx.ASGITransport(app=build_internal_app(_settings(), env=ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.get("/health")).status_code == 404
            # Requires the scheduler service token.
            assert (await client.get("/internal/health")).status_code == 401
            ok = await client.get("/internal/health", headers={"X-Service-Token": "sched-secret"})
            assert ok.status_code == 200
            assert ok.json()["service"] == "gateway-internal"

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
