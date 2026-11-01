"""Real PG: D4-registered jobs, shared event execution and AA progress."""

import asyncio

import httpx
import pytest
from app import models as m
from app.db import engine
from app.main import create_app
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, NOW, install_core
from tests.obligations_data.test_demo_api import SERVICE_TOKEN, clean_database  # noqa: F401
from tests.obligations_data.test_split_exec import _prepare_execute, _split_steps

HEADERS = {"X-Service-Token": SERVICE_TOKEN}


async def _app(config):
    admin = engine(config.admin_url, poolclass=NullPool)
    runtime = engine(config.runtime_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await install_core(conn)
    app = create_app(
        db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY, demo_mode=True, admin_db=admin
    )
    return app, admin, runtime


async def _register(client, kind="EXTERNAL_CREDIT", **changes):
    body = {
        "request_id": "demo-request1",
        "kind": kind,
        "customer_id": "cus_zs",
        "account_id": "acc_zs_chk",
        "amount_cents": 100,
        "due_at": NOW.isoformat(),
        "console_session_id": "console1",
        **changes,
    }
    response = await client.post("/internal/demo/events", json=body, headers=HEADERS)
    return response, body


async def _execute(client, eid, **changes):
    definition = (await client.get("/internal/events/" + eid, headers=HEADERS)).json()
    snapshot = (
        await client.post(
            "/internal/snapshot",
            json={"customer_id": "cus_zs", "steps": [], "event_id": None},
            headers=HEADERS,
        )
    ).json()
    body = {
        "expected_versions": snapshot["versions"],
        "clock_version": snapshot["clock_version"],
        "expected_payload_hash": definition["payload_hash"],
        "authorization_evidence": {
            "type": "DEMO_EVENT",
            "event_id": eid,
            "console_session_id": "console1",
            "kind": definition["payload"]["kind"],
        },
        **changes,
    }
    return await client.post("/internal/events/" + eid + "/execute", json=body, headers=HEADERS)


def test_registered_split_payment_updates_progress_and_replays_once(obligations_db):
    async def run():
        app, admin, runtime = await _app(obligations_db)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://bank"
            ) as client:
                await _prepare_execute(
                    client,
                    "pln_demo_split",
                    "hash_demo_split",
                    _split_steps(),
                    expected_versions={"cus_zs": 0},
                )
                async with admin.connect() as conn:
                    item = (
                        (
                            await conn.execute(
                                select(m.split_items).order_by(m.split_items.c.amount_cents.desc())
                            )
                        )
                        .mappings()
                        .first()
                    )
                response, _ = await _register(
                    client,
                    "SPLIT_PAYMENT",
                    split_item_id=item["id"],
                    amount_cents=item["amount_cents"],
                )
                assert response.status_code == 200, response.text
                eid = response.json()["event_id"]
                first = await _execute(client, eid)
                assert first.status_code == 200 and first.json()["status"] == "SUCCESS", first.text
                replay = await _execute(client, eid)
                assert replay.status_code == 200 and replay.json()["replayed"]
                async with admin.connect() as conn:
                    paid = (
                        (
                            await conn.execute(
                                select(m.split_items).where(m.split_items.c.id == item["id"])
                            )
                        )
                        .mappings()
                        .one()
                    )
                    amount = await conn.scalar(
                        select(
                            m.accounts.c.credits_posted_cents - m.accounts.c.debits_posted_cents
                        ).where(m.accounts.c.id == "acc_zs_chk")
                    )
                assert paid["status"] == "PAID" and paid["payment_event_id"] == eid
                assert amount == 600000 + item["amount_cents"]
                r, _ = await _register(
                    client,
                    "SPLIT_PAYMENT",
                    request_id="another-payment",
                    split_item_id=item["id"],
                    amount_cents=item["amount_cents"],
                )
                duplicate = await _execute(client, r.json()["event_id"])
                assert duplicate.status_code == 200 and duplicate.json()["status"] == "BLOCKED", (
                    duplicate.text
                )
                read = await client.post(
                    "/internal/read/get_split_status",
                    json={
                        "customer_id": "cus_zs",
                        "args": {"request_id": None, "cursor": None, "limit": 50},
                    },
                    headers=HEADERS,
                )
                assert read.status_code == 200, read.text
                assert read.json()["items"][0]["received_cents"] == item["amount_cents"]
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["event_id", "kind", "console_session_id", "missing"])
def test_event_execution_requires_matching_registered_evidence(obligations_db, mismatch):
    async def run():
        app, admin, runtime = await _app(obligations_db)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://bank"
            ) as client:
                r, _ = await _register(client)
                eid = r.json()["event_id"]
                evidence = {
                    "type": "DEMO_EVENT",
                    "event_id": eid,
                    "console_session_id": "console1",
                    "kind": "EXTERNAL_CREDIT",
                }
                if mismatch == "missing":
                    evidence = {}
                else:
                    evidence[mismatch] = "SPLIT_PAYMENT" if mismatch == "kind" else "wrong"
                result = await _execute(client, eid, authorization_evidence=evidence)
                assert result.status_code in (403, 422), result.text
                async with admin.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(
                                m.accounts.c.credits_posted_cents - m.accounts.c.debits_posted_cents
                            ).where(m.accounts.c.id == "acc_zs_chk")
                        )
                        == 600000
                    )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())


def test_registration_request_id_preserves_job_and_conflicts_on_changed_amount(obligations_db):
    async def run():
        app, admin, runtime = await _app(obligations_db)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://bank"
            ) as client:
                a, _ = await _register(client)
                b, _ = await _register(
                    client,
                    due_at=NOW.replace(day=16).isoformat(),
                    console_session_id="renewed-console",
                )
                assert a.status_code == b.status_code == 200 and a.json() == b.json()
                conflict, _ = await _register(client, amount_cents=200)
                assert conflict.status_code == 409, conflict.text
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())


def test_event_cannot_execute_before_simulation_due_time(obligations_db):
    async def run():
        app, admin, runtime = await _app(obligations_db)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://bank"
            ) as client:
                a, _ = await _register(client, due_at=NOW.replace(day=16).isoformat())
                result = await _execute(client, a.json()["event_id"])
                assert (
                    result.status_code == 409 and result.json()["error"]["code"] == "EVENT_NOT_DUE"
                ), result.text
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())
