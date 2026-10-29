import asyncio
import os

import httpx
import pytest
from app.main import create_app
from app.seed import NOW
from app.state import load_bank_state, resolve_scope
from safebank_bankcore.banking import apply_plan

from tests.integration.helpers import database
from tests.integration.test_execution import commands

pytestmark = [pytest.mark.integration, pytest.mark.consistency]


def test_http_contract_auth_ownership_execution_and_outbox():
    async def scenario():
        async with database() as (db, admin):
            app = create_app(
                db, service_token="test-token", fingerprint_key=os.environ["FINGERPRINT_KEY"]
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://l3"
            ) as client:
                assert (await client.get("/health")).status_code == 401
                assert (
                    await client.get("/health", headers={"X-Service-Token": "wrong"})
                ).status_code == 401
                client.headers["X-Service-Token"] = "test-token"
                assert (await client.get("/readyz")).status_code == 200
                response = await client.post(
                    "/internal/read/get_accounts",
                    json={"customer_id": "cus_a", "args": {"history_days": 1}},
                )
                assert response.status_code == 200 and len(response.json()["accounts"]) == 2
                assert "6222020000000001" not in response.text
                for tool, args in [
                    ("list_payees", {"query": None}),
                    ("get_cards", {"account_id": None}),
                    ("get_profile", {}),
                    (
                        "get_transactions",
                        {
                            "account_id": None,
                            "from_at": "2026-10-01T00:00:00+08:00",
                            "to_at": "2026-10-16T00:00:00+08:00",
                            "category": None,
                            "merchant_id": None,
                            "kind": None,
                            "limit": 1,
                        },
                    ),
                ]:
                    result = await client.post(
                        "/internal/read/" + tool, json={"customer_id": "cus_a", "args": args}
                    )
                    assert result.status_code == 200, (tool, result.text)
                    if tool == "list_payees":
                        assert "target_account_id" not in result.text
                    if tool == "get_transactions":
                        assert result.json()["totals_scope"] == "filtered_all"
                assert (
                    await client.post(
                        "/internal/read/get_cards",
                        json={"customer_id": "cus_a", "args": {"account_id": "acc_b"}},
                    )
                ).status_code == 404
                assert (
                    await client.post(
                        "/internal/read/get_accounts",
                        json={"customer_id": "cus_a", "args": {"history_days": True}},
                    )
                ).status_code == 422
                assert (
                    await client.post(
                        "/internal/read/get_holdings",
                        json={
                            "customer_id": "cus_a",
                            "args": {"account_id": None, "product_id": None},
                        },
                    )
                ).status_code == 200
                assert (
                    await client.post(
                        "/internal/read/get_unknown_tool",
                        json={"customer_id": "cus_a", "args": {}},
                    )
                ).json()["error"]["code"] == "TOOL_NOT_ALLOWED"
                p, e = await commands(db)
                snapshot = await client.post(
                    "/internal/snapshot",
                    json={
                        "customer_id": "cus_a",
                        "steps": [s.model_dump(mode="json") for s in e.steps],
                        "event_id": None,
                    },
                )
                assert snapshot.status_code == 200
                async with db.connect() as conn:
                    scope = await resolve_scope(
                        conn, "cus_a", e.steps, os.environ["FINGERPRINT_KEY"]
                    )
                    before = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                predicted = apply_plan(before, e.steps, "cus_a", p.plan_id)
                assert (
                    await client.post("/internal/plans/prepare", json=p.model_dump(mode="json"))
                ).status_code == 200
                result = await client.post(
                    "/internal/plans/execute",
                    json=e.model_dump(mode="json"),
                    headers={"X-Request-ID": "trace-p1"},
                )
                assert result.status_code == 200 and result.json()["status"] == "EXECUTED", (
                    result.text
                )
                assert result.headers["X-Request-ID"] == "trace-p1"
                assert result.json()["steps"] == [
                    s.model_dump(mode="json") for s in predicted.results
                ]
                repeated = await client.post(
                    "/internal/plans/execute", json=e.model_dump(mode="json")
                )
                assert repeated.json()["replayed"]
                assert (
                    await client.get(
                        f"/internal/executions/{p.plan_id}", params={"customer_id": "cus_b"}
                    )
                ).status_code == 404
                events = (await client.get("/internal/outbox")).json()["items"]
                assert len(events) == 1
                ack = {"event_ids": [events[0]["event_id"]]}
                for _ in range(2):
                    assert (await client.post("/internal/outbox/ack", json=ack)).json() == {
                        "acknowledged": 1
                    }

    asyncio.run(scenario())


def test_outbox_rescan_finds_late_commit_with_lower_sequence():
    from app.execution import ExecutionService

    from tests.contracts.samples import step

    async def scenario():
        async with database() as (db, admin):
            entered, release = asyncio.Event(), asyncio.Event()

            async def delayed_commit(conn):
                entered.set()
                await release.wait()

            key = os.environ["FINGERPRINT_KEY"]
            delayed = ExecutionService(db, key, before_commit=delayed_commit)
            normal = ExecutionService(db, key)
            p1, e1 = await commands(
                db, [step(payee_id=None, to_account_id="acc_s")], plan_id="pln_early_seq"
            )
            p2, e2 = await commands(
                db,
                [step("add_payee", account_no="6222020000000097")],
                plan_id="pln_late_seq",
                customer_id="cus_b",
            )
            await delayed.prepare(p1)
            await normal.prepare(p2)
            task = asyncio.create_task(delayed.execute(e1))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert (await normal.execute(e2)).status == "EXECUTED"
                app = create_app(db, service_token="test-token", fingerprint_key=key)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://l3",
                    headers={"X-Service-Token": "test-token"},
                ) as client:
                    first = (await client.get("/internal/outbox")).json()["items"]
                    assert len(first) == 1 and first[0]["payload"]["plan_id"] == p2.plan_id
                    assert (
                        await client.post(
                            "/internal/outbox/ack", json={"event_ids": [first[0]["event_id"]]}
                        )
                    ).status_code == 200
                    release.set()
                    await task
                    second = (await client.get("/internal/outbox", params={"after": 0})).json()[
                        "items"
                    ]
                    assert len(second) == 1 and second[0]["payload"]["plan_id"] == p1.plan_id
                    assert second[0]["commit_seq"] < first[0]["commit_seq"]
            finally:
                release.set()
                await task

    asyncio.run(scenario())
