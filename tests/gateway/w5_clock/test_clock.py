"""D3 real gateway auth/audit with an HTTP-level L3 test double."""

import asyncio
import json

import httpx
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from tests.gateway.w5_sms.conftest import DEMO_TOKEN, build_demo_app


async def app_with_l3(config, responder):
    app = await build_demo_app(config)
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["X-Service-Token"] == "l3-test-token"
        return responder(request)

    app.client._transport.app.state.l3 = L3Client(
        "http://bank", "l3-test-token", transport=httpx.MockTransport(respond)
    )
    login = await app.client.post(
        "/demo/auth/login", json={"secret": DEMO_TOKEN}, headers={"X-Service-Token": DEMO_TOKEN}
    )
    headers = {"X-Service-Token": DEMO_TOKEN, "Authorization": "Bearer " + login.json()["token"]}
    return app, headers, calls


def test_clock_proxies_exact_request_and_status_with_audit(app_config):
    async def run():
        body = {"request_id": "req_clock_1", "target_at": "2026-10-15T09:40:00+08:00"}
        status = {
            "status": "DONE",
            "sim_now": body["target_at"],
            "processed": 0,
            "blocked": 0,
            "remaining": 0,
        }

        def responder(req):
            if req.method == "POST":
                assert req.url.path == "/internal/clock/advance"
                assert json.loads(req.content) == body
                return httpx.Response(200, json={"advance_job_id": "job_clock_1"})
            assert req.url.path == "/internal/clock/advances/job_clock_1"
            return httpx.Response(200, json=status)

        app, headers, calls = await app_with_l3(app_config, responder)
        submitted = await app.client.post("/demo/clock/advance", json=body, headers=headers)
        assert submitted.status_code == 200, submitted.text
        assert submitted.json() == {"advance_job_id": "job_clock_1"}
        result = await app.client.get("/demo/clock/advances/job_clock_1", headers=headers)
        assert result.status_code == 200 and result.json() == status
        assert len(calls) == 2
        db = engine(app.db_url, poolclass=NullPool)
        async with db.connect() as conn:
            rows = (await conn.execute(select(t.audit_events.c.event_type))).scalars().all()
        await db.dispose()
        assert "DEMO_CLOCK_ADVANCE" in rows and "DEMO_CLOCK_STATUS" in rows

    asyncio.run(run())


def test_clock_rejects_naive_datetime_before_l3(app_config):
    async def run():
        app, headers, calls = await app_with_l3(app_config, lambda r: httpx.Response(500))
        r = await app.client.post(
            "/demo/clock/advance",
            headers=headers,
            json={"request_id": "req_naive", "target_at": "2026-10-15T09:40:00"},
        )
        assert r.status_code == 422
        assert not calls

    asyncio.run(run())


def test_clock_transport_failure_is_not_success(app_config):
    async def run():
        def unavailable(req):
            raise httpx.ReadTimeout("lost reply")

        app, headers, calls = await app_with_l3(app_config, unavailable)
        r = await app.client.post(
            "/demo/clock/advance",
            headers=headers,
            json={"request_id": "req_lost", "target_at": "2026-10-15T09:40:00+08:00"},
        )
        assert r.status_code == 503
        assert len(calls) == 1
        assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    asyncio.run(run())


def test_clock_requires_console_and_is_absent_in_production(app_config):
    async def run():
        app = await build_demo_app(app_config)
        r = await app.client.post(
            "/demo/clock/advance",
            headers={"X-Service-Token": DEMO_TOKEN},
            json={"request_id": "req_nojwt", "target_at": "2026-10-15T09:40:00+08:00"},
        )
        assert r.status_code == 401
        prod = await build_demo_app(app_config, demo_mode=False)
        assert (await prod.client.get("/demo/clock/advances/x")).status_code == 404

    asyncio.run(run())
