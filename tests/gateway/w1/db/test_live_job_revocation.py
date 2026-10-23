"""Real gateway PG/locks; seeded jobs prove persistence, never genuine L3 fences."""

import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select, text

from services.gateway.app import locks, state
from services.gateway.app import tables as t
from services.gateway.app.errors import GatewayError
from services.gateway.app.utterance.loader import load_for_plan
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w1.db.test_review_r1 import caller_for


async def seed_job(db, sid, old, now, plan_status, job_status, *, evaluation=True):
    plan, evl, approval = new_id("pln"), new_id("evl"), new_id("apv")
    evidence = {
        "type": "INTERACTIVE",
        "origin": "agent",
        "scope_ids": [old["utterance"]["id"]],
        "utterance_ids": [old["utterance"]["id"]],
        "evaluation_id": evl,
        "approved_evaluation_id": None,
        "plan_hash": "b" * 64,
        "approval_id": None,
        "confirmed_view_hash": None,
        "final_view_hash": "d" * 64,
        "risk": "GREEN",
        "confirmed_at_wall": None,
        "authorization_epoch": 0,
    }
    deadline = state.FENCE_AT if job_status == "PAUSED" else now + timedelta(minutes=3)
    async with db.begin() as conn:
        await conn.execute(
            t.plans.insert().values(
                id=plan,
                customer_id="cus_zs",
                sid=sid,
                origin="agent",
                request_id=plan,
                request_hash="a" * 64,
                plan_hash="b" * 64,
                steps_hash="c" * 64,
                resolved_steps=[],
                utterance_ids=[old["utterance"]["id"]],
                scope_ids=[old["utterance"]["id"]],
                status=plan_status,
                created_at_wall=now,
            )
        )
        if evaluation:
            await conn.execute(
                t.evaluations.insert().values(
                    id=evl,
                    plan_id=plan,
                    revision=3,
                    decision={"decision": "ALLOW"},
                    risk="GREEN",
                    policy_version="v1",
                    view_hash="d" * 64,
                    created_at_wall=now,
                )
            )
            await conn.execute(
                t.approvals.insert().values(
                    id=approval,
                    plan_id=plan,
                    evaluation_id=evl,
                    customer_id="cus_zs",
                    sid=sid,
                    plan_hash="b" * 64,
                    view_hash="d" * 64,
                    binding_mac="e" * 64,
                    status="CONFIRMED",
                    expires_at_wall=now + timedelta(minutes=5),
                )
            )
        await conn.execute(
            t.execution_jobs.insert().values(
                plan_id=plan,
                status=job_status,
                l3_attempt=2,
                retry_count=7,
                authorization_evidence=evidence if evaluation else None,
                deadline_wall=deadline,
                deadline_cap_wall=now + timedelta(minutes=5),
                created_at_wall=now,
                updated_at_wall=now,
            )
        )
        await conn.execute(
            t.authorization_claims.insert().values(
                scope_id=old["utterance"]["id"], plan_id=plan, status="CLAIMED"
            )
        )
    return plan, evidence if evaluation else None, deadline


async def revoke(client, token, sid, action, request_id):
    if action == "logout":
        return await client.post(
            "/auth/logout", headers=headers(token), json={"request_id": request_id}
        )
    return await utter(
        client,
        token,
        sid,
        {"cancel": "停止本次", "correction": "不对；改成给赵六九百", "replacement": "给赵六900"}[
            action
        ],
        request_id,
    )


@pytest.mark.parametrize("action", ["cancel", "correction", "replacement", "logout"])
@pytest.mark.parametrize(
    "plan_status,job_status",
    [
        ("EXECUTING", "EXECUTING"),
        ("EXECUTING", "FENCING"),
        ("CONFIRMED", "PAUSED"),
        ("DENIED", "PAUSED"),
        ("UNKNOWN", "UNKNOWN"),
    ],
)
def test_revocation_keeps_live_job_unknown_with_claim_and_captured_submission(
    gateway_db, monkeypatch, action, plan_status, job_status
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, evidence, deadline = await seed_job(
                db, sid, old, clock[0], plan_status, job_status
            )
            request_id = new_id("req")
            response = await revoke(client, token, sid, action, request_id)
            assert response.status_code == (200 if action == "logout" else 201), response.text
            async with db.connect() as conn:
                original = (
                    (
                        await conn.execute(
                            select(t.utterances).where(t.utterances.c.id == old["utterance"]["id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                assert original["status"] == (
                    "CANCELLED" if action in {"cancel", "logout"} else "SUPERSEDED"
                )
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
                assert job["status"] == "UNKNOWN"
                assert (
                    await conn.scalar(select(t.plans.c.status).where(t.plans.c.id == plan))
                    == "UNKNOWN"
                )
                assert job["authorization_evidence"] == evidence and job["l3_attempt"] == 2
                assert job["retry_count"] == 7 and job["deadline_wall"] == deadline
                assert job["deadline_cap_wall"] == clock[0] + timedelta(minutes=5)
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                    == "CLAIMED"
                )
                assert (
                    await conn.scalar(
                        select(t.approvals.c.status).where(t.approvals.c.plan_id == plan)
                    )
                    == "SUPERSEDED"
                )
                with pytest.raises(GatewayError) as denied:
                    await load_for_plan(
                        conn, sid, [old["utterance"]["id"]], caller_for(old, public)
                    )
                assert (
                    denied.value.code
                    is {
                        "cancel": Code.AUTHORIZATION_CANCELLED,
                        "correction": Code.AUTHORIZATION_CONSUMED,
                        "replacement": Code.AUTHORIZATION_CONSUMED,
                        "logout": Code.UNAUTHENTICATED,
                    }[action]
                )
            visible = await client.get(
                f"/sessions/{sid}/utterances", headers=headers(old["assistant_token"], "a-test")
            )
            assert visible.status_code == (401 if action == "logout" else 200)
            if action != "logout":
                assert visible.json()["items"] == []
                # Repeating the same cancel/correction/replacement must not
                # advance the captured attempt or free a claim.
                replayed = await revoke(client, token, sid, action, request_id)
                assert replayed.json() == response.json()
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.execution_jobs.c.l3_attempt).where(
                                t.execution_jobs.c.plan_id == plan
                            )
                        )
                        == 2
                    )
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                        == "CLAIMED"
                    )

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "correction", "replacement", "logout"])
@pytest.mark.parametrize("job_status", ["EXECUTING", "FENCING", "PAUSED", "UNKNOWN"])
def test_orphaned_job_revocation_cannot_restore_authorization(
    gateway_db, monkeypatch, action, job_status
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, deadline = await seed_job(
                db, sid, old, clock[0], "EXECUTING", job_status, evaluation=False
            )
            try:
                response = await revoke(client, token, sid, action, new_id("req"))
                assert response.status_code == (200 if action == "logout" else 201), response.text
                async with db.connect() as conn:
                    assert await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    ) == ("CANCELLED" if action in {"cancel", "logout"} else "SUPERSEDED"), (
                        response.text
                    )
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
                    assert job["status"] == "UNKNOWN"
                    assert job["l3_attempt"] == 2 and job["retry_count"] == 7
                    assert job["authorization_evidence"] is None
                    assert job["deadline_wall"] == deadline
                    assert job["deadline_cap_wall"] == clock[0] + timedelta(minutes=5)
                    assert not (
                        await conn.execute(
                            select(t.evaluations).where(t.evaluations.c.plan_id == plan)
                        )
                    ).all()
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                        == "CLAIMED"
                    )
            finally:
                # Teardown removes ONLY this test-owned orphan from the ephemeral
                # gateway fixture. It creates no L3 terminal proof, never consumes
                # a claim, and must not turn this failing assertion into a pass.
                # Without teardown its broken row poisons unrelated logout tests.
                async with db.begin() as conn:
                    await conn.execute(
                        t.authorization_claims.delete().where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                    await conn.execute(
                        t.execution_jobs.delete().where(t.execution_jobs.c.plan_id == plan)
                    )
                    await conn.execute(t.plans.delete().where(t.plans.c.id == plan))

    asyncio.run(run())


def test_clarification_keep_preserves_parent_while_old_claim_stays_pending(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给赵六900")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "CONFIRMED", "PAUSED")
            parent = (await utter(client, token, sid, "给王吾800", new_id("req"))).json()
            response = await utter(
                client,
                token,
                sid,
                "王五",
                new_id("req"),
                clarification_id=parent["clarification_id"],
            )
            assert response.status_code == 201, response.text
            root = response.json()
            assert root["utterance"]["parent_ids"] == [parent["utterance"]["id"]]
            assert root["utterance"]["scope"]["transfers"][0]["amount_cents"] == 80000
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == parent["utterance"]["id"]
                        )
                    )
                    == "ACTIVE"
                )
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == plan)
                    )
                    == "UNKNOWN"
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                    == "CLAIMED"
                )
                auth = await load_for_plan(
                    conn, sid, [root["utterance"]["id"]], caller_for(root, public)
                )
                assert auth.scope_ids == (root["utterance"]["id"],)
                assert (
                    await conn.scalar(
                        select(t.pending_clarifications.c.status).where(
                            t.pending_clarifications.c.id == parent["clarification_id"]
                        )
                    )
                    == "RESOLVED"
                )

    asyncio.run(run())


def test_hint_replacement_retains_the_newly_adopted_source(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "CONFIRMED", "PAUSED")
            hint_id = new_id("hnt")
            expiry = clock[0] + timedelta(minutes=5)
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=hint_id,
                        sid=sid,
                        scope_json=old["utterance"]["scope"],
                        label="只转王五800",
                        created_at_wall=clock[0],
                        expires_at_wall=expiry,
                    )
                )
            response = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={
                    "request_id": new_id("req"),
                    "kind": "hint",
                    "hint_id": hint_id,
                },
            )
            assert response.status_code == 201, response.text
            async with db.connect() as conn:
                hint = (
                    (await conn.execute(select(t.hints).where(t.hints.c.id == hint_id)))
                    .mappings()
                    .one()
                )
                assert hint["used_by"] == response.json()["utterance"]["id"]
                assert hint["expires_at_wall"] == expiry
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == plan)
                    )
                    == "UNKNOWN"
                )

    asyncio.run(run())


async def wait_for_blocked_customer_lock(db, count=1):
    async with asyncio.timeout(3):
        while True:
            async with db.connect() as conn:
                if (
                    await conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype='advisory' AND NOT granted"
                        )
                    )
                    >= count
                ):
                    return
            await asyncio.sleep(0.01)


def test_cancelled_http_waiter_does_not_mutate_or_leak_real_customer_lock(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "CONFIRMED", "PAUSED")
            async with locks.customer_lock(["cus_zs"]) as owner:
                cancelled = asyncio.create_task(revoke(client, token, sid, "cancel", new_id("req")))
                try:
                    await wait_for_blocked_customer_lock(db)
                    cancelled.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await cancelled
                    await locks.assert_lock_owner(owner)
                    async with db.connect() as conn:
                        assert (
                            await conn.scalar(
                                select(t.utterances.c.status).where(
                                    t.utterances.c.id == old["utterance"]["id"]
                                )
                            )
                            == "ACTIVE"
                        )
                        assert (
                            await conn.scalar(
                                select(t.execution_jobs.c.status).where(
                                    t.execution_jobs.c.plan_id == plan
                                )
                            )
                            == "PAUSED"
                        )
                finally:
                    if not cancelled.done():
                        cancelled.cancel()
                        await asyncio.gather(cancelled, return_exceptions=True)
                pending = asyncio.create_task(revoke(client, token, sid, "cancel", new_id("req")))
                try:
                    await wait_for_blocked_customer_lock(db)
                    assert not pending.done()
                except BaseException:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
            response = await pending
            assert response.status_code == 201, response.text
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == plan)
                    )
                    == "UNKNOWN"
                )
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    )
                    == "CANCELLED"
                )

    asyncio.run(run())


@pytest.mark.parametrize("first", ["register", "logout"])
def test_concurrent_registration_logout_leaves_no_active_old_authorization(
    gateway_db, monkeypatch, first
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "CONFIRMED", "PAUSED")

            async def submit(kind):
                if kind == "logout":
                    return await revoke(client, token, sid, "logout", new_id("req"))
                return await utter(client, token, sid, "给赵六900", new_id("req"))

            tasks = {}
            async with locks.customer_lock(["cus_zs"]):
                try:
                    tasks[first] = asyncio.create_task(submit(first))
                    await wait_for_blocked_customer_lock(db)
                    second = "logout" if first == "register" else "register"
                    tasks[second] = asyncio.create_task(submit(second))
                    await wait_for_blocked_customer_lock(db, count=2)
                except BaseException:
                    for task in tasks.values():
                        task.cancel()
                    await asyncio.gather(*tasks.values(), return_exceptions=True)
                    raise
            responses = {kind: await task for kind, task in tasks.items()}
            assert responses["logout"].status_code == 200, responses["logout"].text
            assert responses["register"].status_code in (201, 401), responses["register"].text
            async with db.connect() as conn:
                assert not (
                    await conn.execute(
                        select(t.utterances.c.id).where(
                            t.utterances.c.sid == sid, t.utterances.c.status == "ACTIVE"
                        )
                    )
                ).all()
                assert (
                    await conn.scalar(
                        select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        select(t.sessions.c.revoked_at_wall).where(t.sessions.c.id == sid)
                    )
                    == clock[0]
                )
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == plan)
                    )
                    == "UNKNOWN"
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                    == "CLAIMED"
                )
            assert (
                await client.get(f"/sessions/{sid}/utterances", headers=headers(token))
            ).status_code == 401

    asyncio.run(run())


def test_legacy_job_without_submission_evidence_still_requires_reconciliation(
    gateway_db, monkeypatch
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "EXECUTING", "EXECUTING")
            async with db.begin() as conn:
                await conn.execute(
                    t.execution_jobs.update()
                    .where(t.execution_jobs.c.plan_id == plan)
                    .values(
                        authorization_evidence=None,
                        deadline_wall=None,
                        deadline_cap_wall=None,
                    )
                )
            response = await revoke(client, token, sid, "cancel", new_id("req"))
            assert response.status_code == 201, response.text
            async with db.connect() as conn:
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
                assert job["authorization_evidence"] is None and job["deadline_cap_wall"] is None
                assert job["deadline_wall"] is None
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.plan_id == plan
                        )
                    )
                    == "CLAIMED"
                )

    asyncio.run(run())


def test_correction_invalidates_each_plan_once_but_replacement_still_does(gateway_db, monkeypatch):
    """Correction must not run a second per-plan invalidation after supersede_session."""
    import services.gateway.app.utterance.lifecycle as lifecycle

    calls = []

    real = lifecycle._invalidate_plan

    async def spy(conn, plan_id):
        calls.append(plan_id)
        return await real(conn, plan_id)

    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            monkeypatch.setattr(lifecycle, "_invalidate_plan", spy)

            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(db, sid, old, clock[0], "CONFIRMED", "PAUSED")
            calls.clear()

            correction = await utter(client, token, sid, "不对；改成给赵六900", new_id("req"))
            assert correction.status_code == 201, correction.text
            assert calls == [plan], "correction must invalidate each plan exactly once"

            calls.clear()
            replacement = await utter(client, token, sid, "给王五800", new_id("req"))
            assert replacement.status_code == 201, replacement.text
            assert calls == [plan], "replacement still invalidates the plan once"

    asyncio.run(run())
