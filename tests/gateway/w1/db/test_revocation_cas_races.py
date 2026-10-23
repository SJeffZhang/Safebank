"""Real SQL races around W1 revocation; no mocked CAS, lock or auth checker."""

import asyncio

import httpx
import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select, text, update

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w1.db.test_live_job_revocation import revoke, seed_job


async def remove_test_plan(db, plan):
    # Remove only this test's synthetic rows; never fabricate terminal evidence.
    async with db.begin() as conn:
        for table in (t.authorization_claims, t.execution_jobs, t.approvals, t.evaluations):
            await conn.execute(table.delete().where(table.c.plan_id == plan))
        await conn.execute(t.plans.delete().where(t.plans.c.id == plan))


async def waiting_plan_backend(db, pending):
    async with asyncio.timeout(3):
        while True:
            if pending.done():
                response = await pending
                pytest.fail(f"request returned before the forced SQL race: {response.status_code}")
            async with db.connect() as conn:
                pid = await conn.scalar(
                    text("""
                    SELECT pid FROM pg_stat_activity
                    WHERE datname=current_database() AND wait_event_type='Lock'
                    AND query LIKE '%plans%' AND pid <> pg_backend_pid()
                    ORDER BY pid LIMIT 1
                """)
                )
            if pid is not None:
                return pid
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("action", ["cancel", "correction", "replacement", "logout"])
@pytest.mark.parametrize("race", ["evaluation_appears", "new_revision", "job_status"])
def test_cas_reread_does_not_roll_back_revocation(gateway_db, monkeypatch, action, race):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, evidence, _ = await seed_job(
                db,
                sid,
                old,
                clock[0],
                "EXECUTING",
                "EXECUTING",
                evaluation=race == "new_revision",
            )
            revision = 4 if race == "new_revision" else 3
            pending = None
            try:
                # A legacy writer holds the plan and an uncommitted evaluation.
                # W1 first reads absence, then blocks in the real orphan CAS.
                async with db.begin() as writer:
                    await writer.execute(
                        select(t.plans.c.id).where(t.plans.c.id == plan).with_for_update()
                    )
                    if race == "job_status":
                        await writer.execute(
                            update(t.execution_jobs)
                            .where(t.execution_jobs.c.plan_id == plan)
                            .values(status="FENCING")
                        )
                    else:
                        await writer.execute(
                            t.evaluations.insert().values(
                                id=new_id("evl"),
                                plan_id=plan,
                                revision=revision,
                                decision={"decision": "ALLOW"},
                                risk="GREEN",
                                policy_version="v1",
                                created_at_wall=clock[0],
                            )
                        )
                    pending = asyncio.create_task(revoke(client, token, sid, action, new_id("req")))
                    await waiting_plan_backend(db, pending)
                response = await pending
                assert response.status_code == (200 if action == "logout" else 201), response.text
                async with db.connect() as conn:
                    assert await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    ) == ("CANCELLED" if action in {"cancel", "logout"} else "SUPERSEDED")
                    assert await conn.scalar(
                        select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                    ) == (1 if action in {"cancel", "logout"} else 0)
                    job = (
                        (
                            await conn.execute(
                                select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    assert job["status"] == "UNKNOWN" and job["l3_attempt"] == 2
                    assert job["authorization_evidence"] == evidence
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                        == "CLAIMED"
                    )
                    revisions = (
                        (
                            await conn.execute(
                                select(t.evaluations.c.revision)
                                .where(t.evaluations.c.plan_id == plan)
                                .order_by(t.evaluations.c.revision)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert (
                        revisions
                        == {"evaluation_appears": [3], "new_revision": [3, 4], "job_status": []}[
                            race
                        ]
                    )

            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await remove_test_plan(db, plan)

    asyncio.run(run())


def test_malformed_orphan_claim_cannot_restore_revoked_authority(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            try:
                async with db.begin() as conn:
                    await conn.execute(
                        t.authorization_claims.delete().where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                response = await revoke(client, token, sid, "cancel", new_id("req"))
                assert response.status_code == 409, response.text
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.utterances.c.status).where(
                                t.utterances.c.id == old["utterance"]["id"]
                            )
                        )
                        == "CANCELLED"
                    )
                    assert (
                        await conn.scalar(
                            select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                        )
                        == 1
                    )
                    assert (
                        await conn.scalar(
                            select(t.execution_jobs.c.status).where(
                                t.execution_jobs.c.plan_id == plan
                            )
                        )
                        == "EXECUTING"
                    )
                    assert not (
                        await conn.execute(
                            select(t.authorization_claims).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                    ).all()
                visible = await client.get(
                    f"/sessions/{sid}/utterances", headers=headers(old["assistant_token"], "a-test")
                )
                assert visible.status_code == 200 and visible.json()["items"] == []
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


def test_real_lock_connection_loss_is_not_swallowed_as_cas_success(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            pending = None
            admin = engine(gateway_db.admin_url)
            try:
                async with db.begin() as writer:
                    await writer.execute(
                        select(t.plans.c.id).where(t.plans.c.id == plan).with_for_update()
                    )

                    async def observe_http_failure():
                        # Observe the server's actual generic 503 handler;
                        # ASGITransport otherwise re-raises after sending it.
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app, raise_app_exceptions=False),
                            base_url="http://gateway",
                        ) as observer:
                            return await revoke(observer, token, sid, "cancel", new_id("req"))

                    pending = asyncio.create_task(observe_http_failure())
                    pid = await waiting_plan_backend(db, pending)
                    async with admin.begin() as conn:
                        assert await conn.scalar(
                            text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                        )
                    response = await pending
                    assert response.status_code == 503, response.text
                    assert response.json()["error"]["code"] == Code.DEPENDENCY_UNAVAILABLE.value
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.execution_jobs.c.status).where(
                                t.execution_jobs.c.plan_id == plan
                            )
                        )
                        == "EXECUTING"
                    )
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                        == "CLAIMED"
                    )
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await admin.dispose()
                await remove_test_plan(db, plan)

    asyncio.run(run())
