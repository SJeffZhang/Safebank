"""D4 real credential/audit/claim database, L3 protocol boundary double."""

import asyncio
import json

import httpx
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w5_clock.test_clock import app_with_l3
from tests.gateway.w5_sms.conftest import DEMO_TOKEN, build_demo_app


def test_d4_uses_console_and_shared_event_engine_idempotently(app_config):
    async def run():
        saved = {}

        def responder(req):
            path = req.url.path
            if path == "/internal/snapshot":
                return httpx.Response(
                    200,
                    json={
                        "sim_now": "2026-10-15T09:00:00+08:00",
                        "clock_version": 1,
                        "versions": {"cus_test_1": 0},
                    },
                )
            if path == "/internal/demo/events":
                saved.update(json.loads(req.content))
                return httpx.Response(200, json={"event_id": "evt_demo_real_claim"})
            if path == "/internal/events/evt_demo_real_claim":
                return httpx.Response(
                    200,
                    json={
                        "event_id": "evt_demo_real_claim",
                        "customer_id": "cus_test_1",
                        "kind": "DEMO_EVENT",
                        "payload": saved,
                        "payload_hash": "hash",
                        "execution": None,
                    },
                )
            if path == "/internal/events/evt_demo_real_claim/execute":
                evidence = json.loads(req.content)["authorization_evidence"]
                assert (
                    evidence["kind"] == "EXTERNAL_CREDIT"
                    and evidence["event_id"] == "evt_demo_real_claim"
                )
                assert evidence["console_session_id"] == saved["console_session_id"]
                return httpx.Response(
                    200, json={"status": "SUCCESS", "result": {"status": "SUCCESS"}}
                )
            raise AssertionError(path)

        app, headers, calls = await app_with_l3(app_config, responder)
        try:
            body = {
                "request_id": "request1",
                "kind": "EXTERNAL_CREDIT",
                "customer_id": "cus_test_1",
                "account_id": "acc_test",
                "amount_cents": 100,
            }
            first = await app.client.post("/demo/events", json=body, headers=headers)
            assert first.status_code == 200 and first.json()["result"]["status"] == "SUCCESS", (
                first.text
            )
            second = await app.client.post("/demo/events", json=body, headers=headers)
            assert second.status_code == 200 and second.json()["result"]["status"] == "SUCCESS", (
                second.text
            )
            assert len([r for r in calls if r.url.path.endswith("/execute")]) == 1
            db = engine(app.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                event_types = (
                    (await conn.execute(select(t.audit_events.c.event_type))).scalars().all()
                )
                assert "DEMO_EVENT_REGISTERED" in event_types and "SCHEDULE_RESULT" in event_types
            await db.dispose()
        finally:
            await app.client.aclose()

    asyncio.run(run())


def test_d4_rejects_missing_console_and_unknown_fields_before_l3(app_config):
    async def run():
        app, headers, calls = await app_with_l3(app_config, lambda r: httpx.Response(500))
        body = {
            "request_id": "request1",
            "kind": "EXTERNAL_CREDIT",
            "customer_id": "cus_test_1",
            "account_id": "acc_test",
            "amount_cents": 100,
        }
        r = await app.client.post(
            "/demo/events", json=body, headers={"X-Service-Token": DEMO_TOKEN}
        )
        assert r.status_code == 401 and not calls
        r = await app.client.post(
            "/demo/events", json={**body, "console_session_id": "forged"}, headers=headers
        )
        assert r.status_code == 422 and not calls
        prod = await build_demo_app(app_config, demo_mode=False)
        r = await prod.client.post("/demo/events", json=body)
        assert r.status_code == 404

    asyncio.run(run())
