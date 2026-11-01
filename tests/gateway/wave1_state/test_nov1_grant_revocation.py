"""Committed cancellation archives must revoke persistent grants atomically."""

import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.plan import ExecutionResult, StepResult
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.helpers import seed
from tests.gateway.wave1_state.orphan_helpers import identity, orphan


def committed(plan_id, instruction_id, tool, now):
    result = {"instruction_id": instruction_id, "status": "DELETED", "no_op": False}
    if tool == "cancel_subscription":
        result.update(monthly_saving_cents=3000, annual_saving_cents=36000)
    return ExecutionResult(
        plan_id=plan_id,
        status="EXECUTED",
        versions={},
        committed_at=now,
        error_code=None,
        replayed=False,
        steps=(
            StepResult(
                step_id="cancel",
                tool=tool,
                status="EXECUTED",
                created_ids={},
                result=result,
                effects=(),
            ),
        ),
    )


async def install(conn, expected, instruction_id, tool, now, *, existing=True):
    await conn.execute(
        t.plans.update()
        .where(t.plans.c.id == expected.plan_id)
        .values(
            resolved_steps=[
                {"step_id": "cancel", "tool": tool, "args": {"instruction_id": instruction_id}}
            ]
        )
    )
    if existing:
        await conn.execute(
            t.scheduled_grants.insert().values(
                instruction_id=instruction_id,
                customer_id=expected.customer_id,
                source_plan_id="pln_original",
                scope_json={"original": "preserve"},
                scope_hash="a" * 64,
                approved_risk="YELLOW",
                status="ACTIVE",
                created_at=now,
                revoked_at=None,
            )
        )


async def grant(conn, instruction_id):
    return (
        (
            await conn.execute(
                select(t.scheduled_grants).where(
                    t.scheduled_grants.c.instruction_id == instruction_id
                )
            )
        )
        .mappings()
        .first()
    )


@pytest.mark.parametrize("tool", ["cancel_subscription", "cancel_scheduled"])
@pytest.mark.parametrize("job_status", ["EXECUTING", "UNKNOWN"])
@pytest.mark.parametrize("existing", [True, False])
def test_verified_committed_cancel_revokes_existing_or_missing_grant(
    gateway_db, tool, job_status, existing
):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                expected, _, now, _ = await seed(
                    conn, job_status=job_status, plan_status=job_status
                )
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, tool, now, existing=existing)
            async with db.begin() as conn:
                await state.archive_execution_terminal(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus(job_status),
                    l3_status="COMMITTED",
                    result=committed(expected.plan_id, instruction_id, tool, now),
                )
            async with db.connect() as conn:
                row = await grant(conn, instruction_id)
                assert (
                    row is not None and row["status"] == "REVOKED" and row["revoked_at"] is not None
                )
                assert row["customer_id"] == expected.customer_id
                if existing:
                    assert row["scope_json"] == {"original": "preserve"}
                    assert row["source_plan_id"] == "pln_original" and row["scope_hash"] == "a" * 64
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(
                            t.execution_jobs.c.plan_id == expected.plan_id
                        )
                    )
                    == "EXECUTED"
                )
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("recovery", [False, True])
def test_archive_revocation_rolls_back_and_can_be_recovered(gateway_db, monkeypatch, recovery):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        original = state.revoke_committed_grants
        try:
            async with db.begin() as conn:
                if recovery:
                    ids, now = await orphan(conn, job_status="UNKNOWN", plan_status="UNKNOWN")
                    expected = identity(ids)
                else:
                    expected, _, now, _ = await seed(conn)
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, "cancel_subscription", now)

            async def archive(conn):
                kwargs = dict(
                    expected=expected,
                    l3_status="COMMITTED",
                    result=committed(expected.plan_id, instruction_id, "cancel_subscription", now),
                )
                if recovery:
                    await state.archive_orphan_execution_terminal(conn, **kwargs)
                else:
                    await state.archive_execution_terminal(
                        conn, from_status=state.ExecutionJobStatus.EXECUTING, **kwargs
                    )

            async def fail_after_write(conn, plan, result):
                await original(conn, plan, result)
                assert (await grant(conn, instruction_id))["status"] == "REVOKED"
                raise RuntimeError("injected archive failure")

            monkeypatch.setattr(state, "revoke_committed_grants", fail_after_write)
            with pytest.raises(RuntimeError, match="injected archive failure"):
                async with db.begin() as conn:
                    await archive(conn)
            async with db.connect() as conn:
                assert (await grant(conn, instruction_id))["status"] == "ACTIVE"
                assert await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == expected.plan_id
                    )
                ) == ("UNKNOWN" if recovery else "EXECUTING")
            monkeypatch.setattr(state, "revoke_committed_grants", original)
            async with db.begin() as conn:
                await archive(conn)
            async with db.connect() as conn:
                assert (await grant(conn, instruction_id))["status"] == "REVOKED"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["customer", "target", "tool", "result", "plan"])
def test_mismatched_cancellation_does_not_archive_or_revoke(gateway_db, mismatch):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                expected, _, now, _ = await seed(conn)
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, "cancel_scheduled", now)
                if mismatch == "customer":
                    await conn.execute(
                        t.scheduled_grants.update()
                        .where(t.scheduled_grants.c.instruction_id == instruction_id)
                        .values(customer_id="cus_other")
                    )
            result = committed(expected.plan_id, instruction_id, "cancel_scheduled", now)
            if mismatch == "target":
                result.steps[0].result["instruction_id"] = "si_wrong"
            elif mismatch == "tool":
                result = committed(expected.plan_id, instruction_id, "cancel_subscription", now)
            elif mismatch == "result":
                result.steps[0].result["status"] = "ACTIVE"
            elif mismatch == "plan":
                result = result.model_copy(update={"plan_id": "pln_other"})
            with pytest.raises(GatewayError):
                async with db.begin() as conn:
                    await state.archive_execution_terminal(
                        conn,
                        expected=expected,
                        from_status=state.ExecutionJobStatus.EXECUTING,
                        l3_status="COMMITTED",
                        result=result,
                    )
            async with db.connect() as conn:
                row = await grant(conn, instruction_id)
                assert row["status"] == "ACTIVE" and row["revoked_at"] is None
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(
                            t.execution_jobs.c.plan_id == expected.plan_id
                        )
                    )
                    == "EXECUTING"
                )
        finally:
            await db.dispose()

    asyncio.run(run())


def test_committed_noop_cancel_preserves_first_revocation_and_original_consent(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                expected, _, now, _ = await seed(conn)
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, "cancel_subscription", now)
                first = now - timedelta(days=1)
                await conn.execute(
                    t.scheduled_grants.update()
                    .where(t.scheduled_grants.c.instruction_id == instruction_id)
                    .values(status="REVOKED", revoked_at=first)
                )
            result = committed(expected.plan_id, instruction_id, "cancel_subscription", now)
            result.steps[0].result["no_op"] = True
            async with db.begin() as conn:
                await state.archive_execution_terminal(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    l3_status="COMMITTED",
                    result=result,
                )
            async with db.connect() as conn:
                row = await grant(conn, instruction_id)
                assert row["status"] == "REVOKED" and row["revoked_at"] == first
                assert row["scope_json"] == {"original": "preserve"}
                assert row["source_plan_id"] == "pln_original"
                assert row["approved_risk"] == "YELLOW"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("tool", ["cancel_subscription", "cancel_scheduled"])
@pytest.mark.parametrize("existing", [True, False])
def test_orphan_committed_cancel_uses_same_revocation_rule(gateway_db, tool, existing):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn, job_status="UNKNOWN", plan_status="UNKNOWN")
                expected = identity(ids)
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, tool, now, existing=existing)
            async with db.begin() as conn:
                await state.archive_orphan_execution_terminal(
                    conn,
                    expected=expected,
                    l3_status="COMMITTED",
                    result=committed(expected.plan_id, instruction_id, tool, now),
                )
            async with db.connect() as conn:
                row = await grant(conn, instruction_id)
                assert (
                    row is not None and row["status"] == "REVOKED" and row["revoked_at"] is not None
                )
        finally:
            await db.dispose()

    asyncio.run(run())


def test_l3_cancelled_failed_plan_never_revokes_underlying_grant(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                expected, _, now, _ = await seed(conn)
                instruction_id = "si_" + expected.plan_id
                await install(conn, expected, instruction_id, "cancel_scheduled", now)
            async with db.begin() as conn:
                await state.archive_execution_terminal(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    l3_status="CANCELLED",
                    result=None,
                )
            async with db.connect() as conn:
                row = await grant(conn, instruction_id)
                assert row["status"] == "ACTIVE" and row["revoked_at"] is None
        finally:
            await db.dispose()

    asyncio.run(run())
