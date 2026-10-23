import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from app import models as m
from app.errors import BankError
from app.execution import ExecutionService, steps_digest
from app.seed import NOW
from app.state import load_bank_state, resolve_scope
from safebank_contracts.plan import Execute, PlanBody, Prepare, Reconcile
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.contracts.samples import step
from tests.integration.helpers import database

pytestmark = pytest.mark.integration


async def commands(db, steps=None, plan_id="pln_test", *, wall=None, customer_id="cus_a"):
    steps = PlanBody(intents=(), steps=steps or [step(amount_cents=100)], rationale="test").steps
    wall = wall or datetime.now(UTC)
    plan_hash = steps_digest(steps)
    evidence = dict(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=["utt_test"],
        utterance_ids=["utt_test"],
        evaluation_id="evl_test",
        approved_evaluation_id="evl_test",
        plan_hash=plan_hash,
        approval_id="apv_test",
        confirmed_view_hash="view_test",
        final_view_hash="view_test",
        risk="YELLOW",
        confirmed_at_wall=wall,
        authorization_epoch=0,
    )
    prepared = Prepare(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=wall + timedelta(minutes=5),
        deadline_cap_wall=wall + timedelta(minutes=10),
        authorization_evidence=evidence,
    )
    async with db.connect() as conn:
        scope = await resolve_scope(conn, customer_id, steps, os.environ["FINGERPRINT_KEY"])
        state = await load_bank_state(conn, scope, NOW, customer_id=customer_id)
    execute = Execute(
        **prepared.model_dump(exclude={"deadline_wall", "deadline_cap_wall"}),
        expected_versions=dict(state.versions),
        clock_version=state.clock_version,
    )
    return prepared, execute


async def rejection(awaitable, code):
    with pytest.raises(BankError) as exc:
        await awaitable
    assert exc.value.code == code


def test_atomic_commit_replay_recovery_and_identity_checks():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db)
            await svc.prepare(p)
            result = await svc.execute(e)
            assert result.status == "EXECUTED" and result.versions == {"cus_a": 1, "cus_b": 1}
            # Lost response: a fresh service reconstructs the committed result.
            restarted = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            assert (await restarted.query("cus_a", p.plan_id))["status"] == "COMMITTED"
            assert (await restarted.execute(e)).replayed
            assert (
                await restarted.reconcile(
                    p.plan_id,
                    Reconcile(
                        customer_id="cus_a", plan_hash=p.plan_hash, cancel_if_uncommitted=True
                    ),
                )
            )["status"] == "COMMITTED"
            await rejection(
                svc.execute(e.model_copy(update={"attempt": 2})), "IDEMPOTENCY_CONFLICT"
            )
            await rejection(
                svc.execute(e.model_copy(update={"plan_hash": "tampered"})), "IDEMPOTENCY_CONFLICT"
            )
            async with db.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 1
                assert await conn.scalar(select(func.count()).select_from(m.operation_events)) == 1
                assert (
                    await conn.scalar(
                        select(m.accounts.c.debits_posted_cents).where(m.accounts.c.id == "acc_a")
                    )
                    == 100
                )

    asyncio.run(scenario())


def test_second_step_failure_and_sql_failure_leave_no_partial_commit():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(
                db, [step(amount_cents=400000), step(step_id="s2", amount_cents=400000)]
            )
            await svc.prepare(p)
            result = await svc.execute(e)
            assert result.status == "FAILED" and result.error_code == "INSUFFICIENT_FUNDS"
            assert [r.status for r in result.steps] == ["ROLLED_BACK", "FAILED"]
            await svc.reconcile(
                p.plan_id,
                Reconcile(customer_id="cus_a", plan_hash=p.plan_hash, cancel_if_uncommitted=True),
            )
            await rejection(svc.execute(e), "EXECUTION_CANCELLED")
            p, e = await commands(db, plan_id="pln_sql_failure")
            await svc.prepare(p)

            async def fail(conn):
                await conn.execute(text("SELECT 1/0"))

            with pytest.raises(DBAPIError):
                await ExecutionService(
                    db, os.environ["FINGERPRINT_KEY"], before_commit=fail
                ).execute(e)
            async with db.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 0
                assert await conn.scalar(select(func.count()).select_from(m.operation_events)) == 0
                assert await conn.scalar(select(func.count()).select_from(m.outbox)) == 0
                assert (
                    await conn.scalar(
                        select(m.accounts.c.debits_posted_cents).where(m.accounts.c.id == "acc_a")
                    )
                    == 0
                )
                assert (await svc.query("cus_a", p.plan_id))["status"] == "PREPARED"

    asyncio.run(scenario())


def test_reconcile_before_prepare_permanently_fences_delayed_requests():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db)
            assert (
                await svc.reconcile(
                    p.plan_id,
                    Reconcile(
                        customer_id="cus_a", plan_hash=p.plan_hash, cancel_if_uncommitted=True
                    ),
                )
            )["status"] == "CANCELLED"
            await rejection(svc.prepare(p), "EXECUTION_CANCELLED")
            await rejection(svc.execute(e), "EXECUTION_CANCELLED")

    asyncio.run(scenario())


def test_attempt_deadline_and_immutable_cap():
    async def scenario():
        async with database() as (db, admin):
            now = datetime.now(UTC)
            clock = [now]
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"], wall_clock=lambda: clock[0])
            p, e = await commands(db, wall=now)
            await svc.prepare(p)
            await svc.prepare(p.model_copy(update={"deadline_wall": now + timedelta(minutes=6)}))
            clock[0] = now + timedelta(minutes=5, seconds=1)
            await rejection(svc.execute(e), "EXECUTION_DEADLINE_EXPIRED")
            evidence = p.authorization_evidence.model_copy(
                update={
                    "evaluation_id": "evl_new",
                    "approved_evaluation_id": "evl_new",
                    "approval_id": "apv_new",
                }
            )
            second = p.model_copy(
                update={
                    "attempt": 2,
                    "deadline_wall": now + timedelta(minutes=9),
                    "authorization_evidence": evidence,
                }
            )
            await svc.prepare(second)
            await rejection(svc.execute(e), "IDEMPOTENCY_CONFLICT")
            await rejection(
                svc.prepare(
                    second.model_copy(
                        update={"attempt": 3, "deadline_cap_wall": now + timedelta(minutes=20)}
                    )
                ),
                "IDEMPOTENCY_CONFLICT",
            )
            result = await svc.execute(
                e.model_copy(update={"attempt": 2, "authorization_evidence": evidence})
            )
            assert result.status == "EXECUTED"
            async with db.connect() as conn:
                history = await conn.scalar(select(m.execution_intents.c.evidence_history))
                assert len(history) == 1 and history[0]["attempt"] == 1

    asyncio.run(scenario())


def test_version_and_clock_changes_are_rejected_without_business_writes():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db)
            await svc.prepare(p)
            async with admin.begin() as conn:
                await conn.execute(update(m.clock).values(version=2))
            await rejection(svc.execute(e), "STATE_CHANGED")
            async with admin.begin() as conn:
                await conn.execute(update(m.clock).values(version=1))
                await conn.execute(
                    update(m.state_versions)
                    .where(m.state_versions.c.customer_id == "cus_b")
                    .values(version=1)
                )
            await rejection(svc.execute(e), "STATE_CHANGED")
            async with db.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 0

    asyncio.run(scenario())


def test_execute_reconcile_race_and_lock_wait_cannot_extend_deadline():
    async def scenario():
        async with database() as (db, admin):
            now = datetime.now(UTC)
            clock = [now]
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"], wall_clock=lambda: clock[0])
            p, e = await commands(db, wall=now)
            await svc.prepare(p)
            async with admin.connect() as blocker:
                tx = await blocker.begin()
                await blocker.execute(select(m.execution_intents).with_for_update())
                task = asyncio.create_task(svc.execute(e))
                await asyncio.sleep(0.05)
                clock[0] = now + timedelta(minutes=6)
                await tx.commit()
                await rejection(task, "EXECUTION_DEADLINE_EXPIRED")
            clock[0] = now
            outcomes = await asyncio.gather(
                svc.execute(e),
                svc.reconcile(
                    p.plan_id,
                    Reconcile(
                        customer_id="cus_a", plan_hash=p.plan_hash, cancel_if_uncommitted=True
                    ),
                ),
                return_exceptions=True,
            )
            status = (await svc.query("cus_a", p.plan_id))["status"]
            assert status in ("COMMITTED", "CANCELLED")
            if status == "COMMITTED":
                assert outcomes[1]["status"] == "COMMITTED"
            else:
                assert (
                    isinstance(outcomes[0], BankError) and outcomes[0].code == "EXECUTION_CANCELLED"
                )

    asyncio.run(scenario())


def test_lock_timeout_is_in_progress_until_durable_reconciliation():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db)
            await svc.prepare(p)
            async with admin.connect() as blocker:
                tx = await blocker.begin()
                await blocker.execute(select(m.execution_intents).with_for_update())
                await rejection(svc.execute(e), "EXECUTION_IN_PROGRESS")
                assert (await svc.query("cus_a", p.plan_id))["status"] == "PREPARED"
                await tx.rollback()
            assert (
                await svc.reconcile(
                    p.plan_id,
                    Reconcile(
                        customer_id="cus_a", plan_hash=p.plan_hash, cancel_if_uncommitted=True
                    ),
                )
            )["status"] == "CANCELLED"
            await rejection(svc.execute(e), "EXECUTION_CANCELLED")

    asyncio.run(scenario())
