import asyncio
from dataclasses import replace

import pytest
from safebank_contracts.plan import ExecutionResult
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.helpers import seed


@pytest.mark.parametrize("job_status", ["EXECUTING", "FENCING", "PAUSED", "UNKNOWN"])
@pytest.mark.parametrize(
    "plan_status",
    ["EXECUTING", "PENDING_APPROVAL", "DENIED", "REPLAN", "REJECTED", "EXPIRED", "SUPERSEDED"],
)
def test_invalidation_keeps_claim_until_l3_cancelled(gateway_db, job_status, plan_status):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, _, _, _ = await seed(conn, job_status=job_status, plan_status=plan_status)
            await conn.execute(
                t.utterances.update()
                .where(
                    t.utterances.c.id.in_(
                        select(t.authorization_claims.c.scope_id).where(
                            t.authorization_claims.c.plan_id == expected.plan_id
                        )
                    )
                )
                .values(status="CANCELLED")
            )
        async with db.begin() as conn:
            await state.mark_execution_unknown(
                conn,
                expected=expected,
                from_status=state.ExecutionJobStatus(job_status),
                reason={"reason": "cancelled"},
            )
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
                await conn.scalar(select(t.plans.c.status).where(t.plans.c.id == expected.plan_id))
                == "UNKNOWN"
            )
        async with db.begin() as conn:
            await state.archive_execution_terminal(
                conn,
                expected=expected,
                from_status=state.ExecutionJobStatus.UNKNOWN,
                l3_status="CANCELLED",
                result=None,
            )
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(t.authorization_claims.c.status).where(
                        t.authorization_claims.c.plan_id == expected.plan_id
                    )
                )
                == "CONSUMED"
            )
            assert (
                await conn.scalar(select(t.plans.c.status).where(t.plans.c.id == expected.plan_id))
                == "FAILED"
            )
            assert (
                await conn.scalar(
                    select(t.utterances.c.status).where(
                        t.utterances.c.consumed_by == expected.plan_id
                    )
                )
                is None
            )
        async with db.begin() as conn:
            with pytest.raises(GatewayError):
                await state.archive_execution_terminal(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus.UNKNOWN,
                    l3_status="CANCELLED",
                    result=None,
                )
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "committed_without_result",
        "wrong_plan",
        "failed_committed",
        "success_cancelled",
        "nonterminal",
        "stale_attempt",
        "stale_revision",
    ],
)
def test_terminal_result_identity_and_status(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, _, now, _ = await seed(conn, job_status="UNKNOWN", plan_status="UNKNOWN")
        result = ExecutionResult(
            plan_id=expected.plan_id,
            status="EXECUTED",
            steps=(),
            versions={},
            committed_at=now,
            error_code=None,
            replayed=False,
        )
        if mutation == "wrong_plan":
            result = result.model_copy(update={"plan_id": "other"})
        if mutation == "failed_committed":
            result = result.model_copy(update={"status": "FAILED"})
        async with db.begin() as conn:
            args = dict(
                expected=replace(expected, attempt=2)
                if mutation == "stale_attempt"
                else replace(expected, revision=2)
                if mutation == "stale_revision"
                else expected,
                from_status=state.ExecutionJobStatus.UNKNOWN,
                l3_status="CANCELLED"
                if mutation == "success_cancelled"
                else "PREPARED"
                if mutation == "nonterminal"
                else "COMMITTED",
                result=None if mutation == "committed_without_result" else result,
            )
            if mutation is None:
                await state.archive_execution_terminal(conn, **args)
            else:
                with pytest.raises(GatewayError):
                    await state.archive_execution_terminal(conn, **args)
        if mutation is None:
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.plans.c.status).where(t.plans.c.id == expected.plan_id)
                    )
                    == "EXECUTED"
                )
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.consumed_by == expected.plan_id
                        )
                    )
                    == "CONSUMED"
                )
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "table,target",
    [
        ("execution_jobs", "PAUSED"),
        ("execution_jobs", "FENCING"),
        ("execution_jobs", "EXECUTED"),
        ("execution_jobs", "FAILED"),
        ("plans", "EXECUTED"),
        ("plans", "FAILED"),
        ("authorization_claims", "CONSUMED"),
    ],
)
def test_ordinary_transition_cannot_bypass_protocol(gateway_db, table, target):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, _, _ = await seed(conn)
        async with db.begin() as conn:
            with pytest.raises((GatewayError, ValueError)):
                await state.transition(
                    conn,
                    getattr(t, table),
                    id_=old.scope_ids[0] if table == "authorization_claims" else expected.plan_id,
                    from_statuses=["CLAIMED" if table == "authorization_claims" else "EXECUTING"],
                    to=target,
                )
        await db.dispose()

    asyncio.run(run())


def test_paused_changes_rollback_with_callers_transaction(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, *_ = await seed(conn)
        with pytest.raises(RuntimeError):
            async with db.begin() as conn:
                await state.begin_fencing(conn, expected=expected)
                await state.acknowledge_fence(conn, expected=expected)
                raise RuntimeError("caller abort")
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == expected.plan_id
                    )
                )
                == "EXECUTING"
            )
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("source", ["EXECUTING", "UNKNOWN"])
@pytest.mark.parametrize(
    "target", ["EVALUATED", "CONFIRMED", "PENDING_APPROVAL", "DENIED", "REPLAN"]
)
def test_ordinary_execution_and_unknown_never_return_to_decision(source, target):
    assert not state.legal("plans", source, target)
    assert not state.legal("execution_jobs", source, "PAUSED")


def test_helpers_require_callers_active_transaction(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, old, now, cap = await seed(conn)
        async with db.connect() as conn:
            calls = [
                lambda: state.begin_fencing(conn, expected=expected),
                lambda: state.acknowledge_fence(conn, expected=expected),
                lambda: state.guard_paused_execution(conn, expected=expected),
                lambda: state.publish_paused_decision(
                    conn, expected=expected, to=state.PlanStatus.EVALUATED, now=now
                ),
                lambda: state.resume_paused_execution(
                    conn,
                    expected=expected,
                    authorization_evidence=old,
                    deadline_wall=cap,
                    deadline_cap_wall=cap,
                    approval_id=None,
                    now=now,
                ),
                lambda: state.mark_execution_unknown(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    reason={},
                ),
                lambda: state.archive_execution_terminal(
                    conn,
                    expected=expected,
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    l3_status="CANCELLED",
                    result=None,
                ),
            ]
            for call in calls:
                with pytest.raises(ValueError, match="active short transaction"):
                    await call()
                assert not conn.in_transaction()
        await db.dispose()

    asyncio.run(run())
