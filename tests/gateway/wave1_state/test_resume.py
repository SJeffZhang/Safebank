import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from safebank_contracts.enums import Risk
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.schemas.enums import PlanStatus
from tests.gateway.wave1_state.helpers import seed


async def paused(conn):
    expected, evidence, now, cap = await seed(conn)
    await state.begin_fencing(conn, expected=expected)
    await state.acknowledge_fence(conn, expected=expected)
    return expected, evidence, now, cap


async def evaluation(conn, expected, decision="ALLOW", risk="GREEN", view="view"):
    await state.guard_paused_execution(conn, expected=expected)
    id_ = "evl_" + uuid4().hex
    await conn.execute(
        t.evaluations.insert().values(
            id=id_,
            plan_id=expected.plan_id,
            revision=expected.revision + 1,
            decision={"decision": decision},
            risk=risk,
            policy_version="v2",
            view_hash=view,
            view_json={},
            created_at_wall=state.FENCE_AT,
        )
    )
    return replace(expected, revision=expected.revision + 1), id_


def test_publish_then_resume_saves_next_attempt_before_http(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await paused(conn)
        async with db.begin() as conn:
            fresh, eid = await evaluation(conn, expected)
            await state.publish_paused_decision(
                conn, expected=fresh, to=PlanStatus.EVALUATED, now=now
            )
        new = old.model_copy(update={"evaluation_id": eid})
        async with db.begin() as conn:
            assert (
                await state.resume_paused_execution(
                    conn,
                    expected=fresh,
                    authorization_evidence=new,
                    deadline_wall=cap - timedelta(seconds=1),
                    deadline_cap_wall=cap,
                    approval_id=None,
                    now=now,
                )
                == 2
            )
        async with db.connect() as conn:
            job = (
                (
                    await conn.execute(
                        select(t.execution_jobs).where(
                            t.execution_jobs.c.plan_id == expected.plan_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert job["status"] == "EXECUTING" and job["l3_attempt"] == 2
            assert job["authorization_evidence"] == new.model_dump(mode="json")
            assert job["deadline_cap_wall"] == cap and job["deadline_wall"] == cap - timedelta(
                seconds=1
            )
        for old_callback in (expected, fresh):
            async with db.begin() as conn:
                with pytest.raises(GatewayError):
                    await state.acknowledge_fence(conn, expected=old_callback)
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("to,decision", [("DENIED", "DENY"), ("REPLAN", "REPLAN")])
def test_paused_deny_replan_do_not_release_claim(gateway_db, to, decision):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, _, now, _ = await paused(conn)
            fresh, _ = await evaluation(conn, expected, decision)
            await state.publish_paused_decision(conn, expected=fresh, to=PlanStatus(to), now=now)
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(t.authorization_claims.c.status).where(
                        t.authorization_claims.c.plan_id == expected.plan_id
                    )
                )
                == "CLAIMED"
            )
            assert (
                await conn.scalar(
                    select(t.execution_jobs.c.deadline_wall).where(
                        t.execution_jobs.c.plan_id == expected.plan_id
                    )
                )
                == state.FENCE_AT
            )
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_cap",
        "expired_deadline",
        "beyond_cap",
        "wrong_evaluation",
        "wrong_hash",
        "wrong_scope",
        "not_paused",
        "old_revision",
        "null_view",
    ],
)
def test_resume_rejects_invalid_persisted_or_new_evidence(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await paused(conn)
            fresh, eid = await evaluation(conn, expected)
            await state.publish_paused_decision(
                conn, expected=fresh, to=PlanStatus.EVALUATED, now=now
            )
            if mutation == "not_paused":
                await conn.execute(
                    t.execution_jobs.update()
                    .where(t.execution_jobs.c.plan_id == expected.plan_id)
                    .values(status="UNKNOWN")
                )
            if mutation == "null_view":
                await conn.execute(
                    t.evaluations.update().where(t.evaluations.c.id == eid).values(view_json=None)
                )
        changes = {"evaluation_id": eid}
        if mutation == "wrong_evaluation":
            changes["evaluation_id"] = "other"
        if mutation == "wrong_hash":
            changes["plan_hash"] = "other"
        if mutation == "wrong_scope":
            changes["scope_ids"] = ("other",)
        new = old.model_copy(update=changes)
        async with db.begin() as conn:
            with pytest.raises(GatewayError):
                await state.resume_paused_execution(
                    conn,
                    expected=expected if mutation == "old_revision" else fresh,
                    authorization_evidence=new,
                    deadline_wall=now
                    if mutation == "expired_deadline"
                    else cap + timedelta(seconds=1)
                    if mutation == "beyond_cap"
                    else cap,
                    deadline_cap_wall=cap + timedelta(seconds=1)
                    if mutation == "wrong_cap"
                    else cap,
                    approval_id=None,
                    now=now,
                )
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "wrong_plan",
        "wrong_customer",
        "wrong_sid",
        "wrong_hash",
        "expired",
        "wrong_status",
        "wrong_view",
        "wrong_evaluation",
    ],
)
def test_approval_ownership_and_validity_are_checked(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await paused(conn)
            fresh, eid = await evaluation(conn, expected, "REQUIRE_APPROVAL", "YELLOW")
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == expected.plan_id)))
                .mappings()
                .one()
            )
            aid = "apv_" + uuid4().hex
            other, _, _, _ = await seed(conn)
            other_plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == other.plan_id)))
                .mappings()
                .one()
            )
            other_eval = await conn.scalar(
                select(t.evaluations.c.id).where(t.evaluations.c.plan_id == other.plan_id)
            )
            fields = dict(
                id=aid,
                plan_id=expected.plan_id,
                evaluation_id=eid,
                customer_id=expected.customer_id,
                sid=plan["sid"],
                plan_hash="hash",
                view_hash="view",
                binding_mac="mac",
                expires_at_wall=cap,
                status="CONFIRMED",
                confirmed_at_wall=now,
            )
            for key, value in {
                "wrong_plan": ("plan_id", other.plan_id),
                "wrong_customer": ("customer_id", "other"),
                "wrong_sid": ("sid", other_plan["sid"]),
                "wrong_hash": ("plan_hash", "other"),
                "expired": ("expires_at_wall", now),
                "wrong_status": ("status", "REJECTED"),
                "wrong_view": ("view_hash", "other"),
                "wrong_evaluation": ("evaluation_id", other_eval),
            }.items():
                if mutation == key:
                    fields[value[0]] = value[1]
            await conn.execute(t.approvals.insert().values(**fields))
            if mutation is None:
                await state.publish_paused_decision(
                    conn, expected=fresh, to=PlanStatus.CONFIRMED, approval_id=aid, now=now
                )
            else:
                with pytest.raises(GatewayError):
                    await state.publish_paused_decision(
                        conn, expected=fresh, to=PlanStatus.CONFIRMED, approval_id=aid, now=now
                    )
        if mutation is None:
            new = old.model_copy(
                update={
                    "evaluation_id": eid,
                    "approved_evaluation_id": eid,
                    "approval_id": aid,
                    "confirmed_view_hash": "view",
                    "confirmed_at_wall": now,
                    "risk": Risk.YELLOW,
                }
            )
            async with db.begin() as conn:
                await state.resume_paused_execution(
                    conn,
                    expected=fresh,
                    authorization_evidence=new,
                    deadline_wall=cap,
                    deadline_cap_wall=cap,
                    approval_id=aid,
                    now=now,
                )
        await db.dispose()

    asyncio.run(run())


def test_expired_cap_prevents_publishing_new_authorization(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, _, now, cap = await paused(conn)
            fresh, _ = await evaluation(conn, expected)
            with pytest.raises(GatewayError):
                await state.publish_paused_decision(
                    conn, expected=fresh, to=PlanStatus.EVALUATED, now=cap
                )
        await db.dispose()

    asyncio.run(run())


def test_concurrent_resume_only_one_new_attempt_is_persisted(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await paused(conn)
            fresh, eid = await evaluation(conn, expected)
            await state.publish_paused_decision(
                conn, expected=fresh, to=PlanStatus.EVALUATED, now=now
            )

        async def attempt():
            try:
                async with db.begin() as conn:
                    return await state.resume_paused_execution(
                        conn,
                        expected=fresh,
                        authorization_evidence=old.model_copy(update={"evaluation_id": eid}),
                        deadline_wall=cap,
                        deadline_cap_wall=cap,
                        approval_id=None,
                        now=now,
                    )
            except GatewayError:
                return "conflict"

        results = await asyncio.gather(attempt(), attempt())
        assert results.count(2) == results.count("conflict") == 1
        await db.dispose()

    asyncio.run(run())


def test_publication_requires_new_evaluation_after_fence(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, _, now, _ = await paused(conn)
            with pytest.raises(GatewayError):
                await state.publish_paused_decision(
                    conn, expected=expected, to=PlanStatus.EVALUATED, now=now
                )
        await db.dispose()

    asyncio.run(run())


def test_equivalent_view_preserves_original_confirmation(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await seed(conn)
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == expected.plan_id)))
                .mappings()
                .one()
            )
            aid = "apv_" + uuid4().hex
            old = old.model_copy(
                update={
                    "risk": Risk.YELLOW,
                    "approval_id": aid,
                    "approved_evaluation_id": old.evaluation_id,
                    "confirmed_view_hash": "view",
                    "confirmed_at_wall": now,
                }
            )
            await conn.execute(
                t.evaluations.update()
                .where(t.evaluations.c.id == old.evaluation_id)
                .values(risk="YELLOW", decision={"decision": "REQUIRE_APPROVAL"})
            )
            await conn.execute(
                t.execution_jobs.update()
                .where(t.execution_jobs.c.plan_id == expected.plan_id)
                .values(authorization_evidence=old.model_dump(mode="json"))
            )
            await conn.execute(
                t.approvals.insert().values(
                    id=aid,
                    plan_id=expected.plan_id,
                    evaluation_id=old.evaluation_id,
                    customer_id=expected.customer_id,
                    sid=plan["sid"],
                    plan_hash="hash",
                    view_hash="view",
                    binding_mac="mac",
                    expires_at_wall=cap,
                    status="CONFIRMED",
                    confirmed_at_wall=now,
                )
            )
            await state.begin_fencing(conn, expected=expected)
            await state.acknowledge_fence(conn, expected=expected)
            fresh, eid = await evaluation(conn, expected, "REQUIRE_APPROVAL", "YELLOW")
            await conn.execute(
                t.evaluations.update()
                .where(t.evaluations.c.id == eid)
                .values(equivalent_to=old.evaluation_id)
            )
            await state.publish_paused_decision(
                conn, expected=fresh, to=PlanStatus.CONFIRMED, approval_id=aid, now=now
            )
            await state.resume_paused_execution(
                conn,
                expected=fresh,
                authorization_evidence=old.model_copy(update={"evaluation_id": eid}),
                deadline_wall=cap,
                deadline_cap_wall=cap,
                approval_id=aid,
                now=now,
            )
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(t.approvals.c.evaluation_id).where(t.approvals.c.id == aid)
                )
                == old.evaluation_id
            )
            assert (
                await conn.scalar(
                    select(t.execution_jobs.c.l3_attempt).where(
                        t.execution_jobs.c.plan_id == expected.plan_id
                    )
                )
                == 2
            )
        await db.dispose()

    asyncio.run(run())


def test_new_pending_approval_cannot_resume_until_confirmation(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await paused(conn)
            fresh, eid = await evaluation(conn, expected, "REQUIRE_APPROVAL", "YELLOW")
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == expected.plan_id)))
                .mappings()
                .one()
            )
            aid = "apv_" + uuid4().hex
            await conn.execute(
                t.approvals.insert().values(
                    id=aid,
                    plan_id=expected.plan_id,
                    evaluation_id=eid,
                    customer_id=expected.customer_id,
                    sid=plan["sid"],
                    plan_hash="hash",
                    view_hash="view",
                    binding_mac="mac",
                    expires_at_wall=cap,
                    status="PENDING",
                )
            )
            await state.publish_paused_decision(
                conn, expected=fresh, to=PlanStatus.PENDING_APPROVAL, approval_id=aid, now=now
            )
        new = old.model_copy(
            update={
                "evaluation_id": eid,
                "approved_evaluation_id": eid,
                "approval_id": aid,
                "confirmed_view_hash": "view",
                "confirmed_at_wall": now,
                "risk": Risk.YELLOW,
            }
        )
        async with db.begin() as conn:
            with pytest.raises(GatewayError):
                await state.resume_paused_execution(
                    conn,
                    expected=fresh,
                    authorization_evidence=new,
                    deadline_wall=cap,
                    deadline_cap_wall=cap,
                    approval_id=aid,
                    now=now,
                )
            assert (
                await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == expected.plan_id
                    )
                )
                == "PAUSED"
            )
        async with db.begin() as conn:
            # Tests only DB conditions; W4 must separately prove authenticated
            # confirmation, view hash/MAC and real customer lock ownership.
            await conn.execute(
                t.approvals.update()
                .where(t.approvals.c.id == aid)
                .values(status="CONFIRMED", confirmed_at_wall=now)
            )
            await state.transition(
                conn,
                t.plans,
                id_=expected.plan_id,
                from_statuses=["PENDING_APPROVAL"],
                to="CONFIRMED",
            )
            await state.resume_paused_execution(
                conn,
                expected=fresh,
                authorization_evidence=new,
                deadline_wall=cap,
                deadline_cap_wall=cap,
                approval_id=aid,
                now=now,
            )
        await db.dispose()

    asyncio.run(run())
