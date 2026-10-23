"""Real gateway PostgreSQL approval/OTP transitions; SMS delivery is a test sink.

These tests use the existing migrated schema, without creating anticipated
view_json/code_ciphertext columns. They prove the approval state machine only.
G9 and crash-durable simulated SMS still require the pending shared migration.
"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.approval import service, sms
from services.gateway.app.approval.planview import build_view
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.idempotency import request_idempotent
from services.gateway.app.middleware import digest_hex
from services.gateway.app.pipeline.context import EvalContext, ResolvedStep
from services.gateway.app.schemas.decision import Decision


async def seed(db, risk="RED", sid=None):
    now = datetime.now(UTC)
    suffix = secrets.token_hex(8)
    customer_id = "cus_" + suffix
    sid = sid or "ses_" + suffix
    plan_id = "pln_" + suffix
    evaluation_id = "evl_" + suffix
    ctx = EvalContext(
        plan_id=plan_id,
        customer_id=customer_id,
        session_id=sid,
        origin="agent",
        wall_now=lambda: now,
        steps=(
            ResolvedStep("s1", "freeze_card", {"card_id": "crd_a"}, "CARD_FREEZE", "GREEN", False),
        ),
        decision=Decision(
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            revision=1,
            decision="REQUIRE_APPROVAL",
            risk=risk,
            policy_version="B2:v1",
        ),
    )
    view, view_hash = build_view(ctx)
    plan = dict(
        id=plan_id,
        customer_id=customer_id,
        sid=sid,
        origin="agent",
        request_id="req_" + suffix,
        request_hash="1" * 64,
        plan_hash=digest_hex({"test": plan_id}),
        steps_hash="2" * 64,
        resolved_steps=[],
        utterance_ids=[],
        scope_ids=[],
        status="PENDING_APPROVAL",
        created_at_wall=now,
    )
    evaluation = dict(
        id=evaluation_id,
        plan_id=plan_id,
        revision=1,
        decision=ctx.decision.model_dump(mode="json"),
        risk=risk,
        findings=[],
        preflight={},
        versions={},
        policy_version="B2:v1",
        view_hash=view_hash,
        clock_version=0,
        created_at_wall=now,
    )
    async with db.begin() as conn:
        await conn.execute(
            t.sessions.insert().values(id=sid, customer_id=customer_id, created_at_wall=now)
        )
        await conn.execute(t.plans.insert().values(**plan))
        await conn.execute(t.evaluations.insert().values(**evaluation))
        approval_id = await service.create_for_evaluation(
            conn, plan=plan, evaluation=evaluation, view=view
        )
    return approval_id, plan, evaluation, view, now


async def loaded(conn, id_, plan):
    return (await service.load(conn, id_, plan["customer_id"], locked=True))[0]


def configured():
    deliveries = []

    async def writer(conn, customer, id_, ciphertext, expiry):
        assert conn.in_transaction()
        deliveries.append((id_, ciphertext, expiry))

    service.configure("a" * 32, "b" * 32, otp_writer=writer, simulated_sms=True)
    return deliveries


def test_otp_resend_expiry_and_confirmation(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url)
        deliveries = configured()
        id_, plan, _, view, now = await seed(db)
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            sent = await service.send_otp(conn, row, view, now)
            assert sent["sent"] and "otp" not in sent and "code" not in sent
            blocked = await service.send_otp(conn, row, view, now + timedelta(seconds=29))
            assert blocked.code == Code.RATE_LIMITED
        first = sms.decrypt(b"b" * 32, id_, view.view_hash, deliveries[0][1])
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            await service.send_otp(conn, row, view, now + timedelta(seconds=30))
            error = await service.confirm(
                conn,
                row,
                plan,
                view,
                now + timedelta(seconds=31),
                otp=first,
                risk_ack=False,
                token=None,
            )
            assert error.code == Code.OTP_INVALID
        code = sms.decrypt(b"b" * 32, id_, view.view_hash, deliveries[-1][1])
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            expired = await service.confirm(
                conn,
                row,
                plan,
                view,
                now + timedelta(seconds=151),
                otp=code,
                risk_ack=False,
                token=None,
            )
            assert expired.code == Code.OTP_EXPIRED
            await service.send_otp(conn, row, view, now + timedelta(seconds=151))
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            assert (
                await service.send_otp(conn, row, view, now + timedelta(seconds=182))
            ).code == Code.RATE_LIMITED
            code = sms.decrypt(b"b" * 32, id_, view.view_hash, deliveries[-1][1])
            result = await service.confirm(
                conn,
                row,
                plan,
                view,
                now + timedelta(seconds=183),
                otp=code,
                risk_ack=False,
                token=None,
            )
            assert result["status"] == "CONFIRMED"
        async with db.connect() as conn:
            assert (
                await conn.scalar(select(t.plans.c.status).where(t.plans.c.id == plan["id"]))
                == "CONFIRMED"
            )
        await db.dispose()

    asyncio.run(run())


def test_five_failed_codes_persist_and_lock_even_after_resend(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url)
        configured()
        id_, plan, _, view, now = await seed(db)
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            await service.send_otp(conn, row, view, now)
        for index in range(5):
            async with db.begin() as conn:
                row = await loaded(conn, id_, plan)
                error = await service.confirm(
                    conn, row, plan, view, now, otp="not-a-code", risk_ack=False, token=None
                )
                assert error.code == (Code.OTP_INVALID if index < 4 else Code.OTP_LOCKED)
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(t.otp_challenges.c.attempt_count).where(
                        t.otp_challenges.c.approval_id == id_
                    )
                )
                == 5
            )
            assert (await service.load(conn, id_, plan["customer_id"]))[0]["status"] == "LOCKED"
        await db.dispose()

    asyncio.run(run())


def test_confirm_idempotency_and_cross_customer_binding(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url)
        configured()
        id_, plan, _, view, now = await seed(db, risk="YELLOW")

        async def request():
            async with db.begin() as conn:

                async def produce():
                    row = await loaded(conn, id_, plan)
                    result = await service.confirm(
                        conn, row, plan, view, now, otp=None, risk_ack=False, token=None
                    )
                    assert not isinstance(result, GatewayError)
                    return 200, result

                return await request_idempotent(
                    conn,
                    customer_id=plan["customer_id"],
                    route="confirm",
                    request_id="req_same",
                    body={"view_hash": view.view_hash},
                    produce=produce,
                )

        first = await request()
        second = await request()
        assert first[:2] == second[:2] and not first[2] and second[2]
        async with db.connect() as conn:
            with pytest.raises(GatewayError) as error:
                await service.load(conn, id_, "cus_other")
            assert error.value.code == Code.NOT_FOUND
        await db.dispose()

    asyncio.run(run())


def test_supersede_and_expiry(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url)
        configured()
        id_, plan, evaluation, view, now = await seed(db, risk="YELLOW")
        async with db.begin() as conn:
            other = await service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
            assert other != id_
        async with db.begin() as conn:
            old = await loaded(conn, id_, plan)
            assert old["status"] == "SUPERSEDED"
            current = await loaded(conn, other, plan)
            error = await service.pending(conn, current, now + timedelta(minutes=5))
            assert error.code == Code.APPROVAL_EXPIRED
        await db.dispose()

    asyncio.run(run())


def test_no_unconfigured_sms_delivery_or_unpersisted_view(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url)
        service.configure("a" * 32, "b" * 32)
        id_, plan, evaluation, view, now = await seed(db)
        async with db.begin() as conn:
            row = await loaded(conn, id_, plan)
            error = await service.send_otp(conn, row, view, now)
            assert error.code == Code.DEPENDENCY_UNAVAILABLE
        with pytest.raises(GatewayError):
            service.stored_view(evaluation)
        await db.dispose()

    asyncio.run(run())
