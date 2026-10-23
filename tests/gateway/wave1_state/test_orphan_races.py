"""Real PostgreSQL CAS/lock tests; no W1 HTTP or L3 authenticity proof."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.dml import Update

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.orphan_helpers import identity, orphan, snapshot


def new_evaluation(ids, now):
    return t.evaluations.insert().values(
        id="evl_" + ids["pln"],
        plan_id=ids["pln"],
        revision=1,
        decision={},
        risk="GREEN",
        policy_version="v1",
        created_at_wall=now,
    )


@pytest.mark.parametrize("terminal", [False, True])
def test_competing_orphan_cas_only_one_state_change(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(
                    conn,
                    job_status="UNKNOWN" if terminal else "EXECUTING",
                    plan_status="UNKNOWN" if terminal else "EXECUTING",
                )

            async def attempt():
                try:
                    async with db.begin() as conn:
                        if terminal:
                            await state.archive_orphan_execution_terminal(
                                conn, expected=identity(ids), l3_status="CANCELLED", result=None
                            )
                        else:
                            await state.mark_orphan_execution_unknown(
                                conn,
                                expected=identity(ids),
                                from_status=state.ExecutionJobStatus.EXECUTING,
                                reason={},
                            )
                    return "changed"
                except GatewayError:
                    return "conflict"

            assert sorted(await asyncio.gather(attempt(), attempt())) == ["changed", "conflict"]
            async with db.connect() as conn:
                saved = await snapshot(conn, ids)
                assert saved["job"]["status"] == ("FAILED" if terminal else "UNKNOWN")
                assert saved["claims"][0]["status"] == ("CONSUMED" if terminal else "CLAIMED")
        finally:
            await db.dispose()

    asyncio.run(run())


def test_unknown_orphan_mark_is_idempotent_after_full_validation(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn, job_status="UNKNOWN", plan_status="UNKNOWN")
                before = await snapshot(conn, ids)
            for _ in range(2):
                async with db.begin() as conn:
                    await state.mark_orphan_execution_unknown(
                        conn,
                        expected=identity(ids),
                        from_status=state.ExecutionJobStatus.UNKNOWN,
                        reason={"reason": "cancel"},
                    )
            async with db.connect() as conn:
                after = await snapshot(conn, ids)
                assert after["claims"] == before["claims"]
                assert after["job"]["l3_attempt"] == before["job"]["l3_attempt"]
                assert after["job"]["authorization_evidence"] is None
                assert after["job"]["deadline_cap_wall"] is None
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_absence_is_checked_in_actual_cas_not_only_initial_read(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        fired = False
        try:
            async with db.begin() as conn:
                ids, now = await orphan(
                    conn,
                    job_status="UNKNOWN" if terminal else "EXECUTING",
                    plan_status="UNKNOWN" if terminal else "EXECUTING",
                )
                before = await snapshot(conn, ids)

            def insert_between_guard_and_cas(conn, clause, multiparams, params, options):
                nonlocal fired
                if isinstance(clause, Update) and clause.table is t.execution_jobs and not fired:
                    fired = True
                    # Controlled DB fault injection on the same connection. No
                    # mock result: omitting NOT EXISTS would update a real row.
                    conn.execute(new_evaluation(ids, now))

            event.listen(db.sync_engine, "before_execute", insert_between_guard_and_cas)
            async with db.begin() as conn:
                with pytest.raises(GatewayError):
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=identity(ids), l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                assert fired
                assert await snapshot(conn, ids) == before
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(t.evaluations)
                        .where(t.evaluations.c.plan_id == ids["pln"])
                    )
                    == 1
                )
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("eval_first", [True, False])
def test_plan_lock_serializes_eval_writer_and_orphan_mark(gateway_db, eval_first):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        locked = asyncio.Event()
        release = asyncio.Event()
        follower_started = asyncio.Event()
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn)

            async def leader():
                async with db.begin() as conn:
                    if eval_first:
                        await conn.execute(
                            select(t.plans.c.id).where(t.plans.c.id == ids["pln"]).with_for_update()
                        )
                        await conn.execute(new_evaluation(ids, now))
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                    locked.set()
                    await release.wait()

            async def follower():
                await locked.wait()
                async with db.begin() as conn:
                    follower_started.set()
                    if eval_first:
                        with pytest.raises(GatewayError):
                            await state.mark_orphan_execution_unknown(
                                conn,
                                expected=identity(ids),
                                from_status=state.ExecutionJobStatus.EXECUTING,
                                reason={},
                            )
                    else:
                        # Witness the required writer discipline after waiting;
                        # this is not the real W3 append_evaluation integration.
                        status = await conn.scalar(
                            select(t.plans.c.status)
                            .where(t.plans.c.id == ids["pln"])
                            .with_for_update()
                        )
                        assert status == "UNKNOWN"
                        assert (
                            await conn.scalar(
                                select(t.execution_jobs.c.status).where(
                                    t.execution_jobs.c.plan_id == ids["pln"]
                                )
                            )
                            == "UNKNOWN"
                        )

            a = asyncio.create_task(leader())
            b = asyncio.create_task(follower())
            try:
                await asyncio.wait_for(follower_started.wait(), timeout=5)
                release.set()
                await asyncio.wait_for(asyncio.gather(a, b), timeout=5)
            finally:
                release.set()
                for task in (a, b):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(a, b, return_exceptions=True)
            async with db.connect() as conn:
                assert await conn.scalar(
                    select(func.count())
                    .select_from(t.evaluations)
                    .where(t.evaluations.c.plan_id == ids["pln"])
                ) == int(eval_first)
                assert (await snapshot(conn, ids))["claims"][0]["status"] == "CLAIMED"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_later_direct_sql_evaluation_prevents_orphan_archive(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn)
                await state.mark_orphan_execution_unknown(
                    conn,
                    expected=identity(ids),
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    reason={},
                )
            async with db.begin() as conn:
                await conn.execute(new_evaluation(ids, now))
            async with db.begin() as conn:
                before = await snapshot(conn, ids)
                with pytest.raises(GatewayError):
                    await state.archive_orphan_execution_terminal(
                        conn, expected=identity(ids), l3_status="CANCELLED", result=None
                    )
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


def test_savepoint_conflict_keeps_revocation_then_valid_cleanup(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn)
            async with db.begin() as conn:
                await conn.execute(
                    t.sessions.update()
                    .where(t.sessions.c.id == ids["ses"])
                    .values(authorization_epoch=1, revoked_at_wall=now)
                )
                await conn.execute(
                    t.utterances.update()
                    .where(t.utterances.c.id == ids["utt"])
                    .values(status="CANCELLED")
                )
                try:
                    async with conn.begin_nested():
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=replace(identity(ids), attempt=4),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                except GatewayError:
                    pass
                await state.mark_orphan_execution_unknown(
                    conn,
                    expected=identity(ids),
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    reason={},
                )
            async with db.connect() as conn:
                session = (
                    (await conn.execute(select(t.sessions).where(t.sessions.c.id == ids["ses"])))
                    .mappings()
                    .one()
                )
                assert session["authorization_epoch"] == 1 and session["revoked_at_wall"] == now
                saved = await snapshot(conn, ids)
                assert saved["job"]["status"] == "UNKNOWN"
                assert saved["utterance"]["status"] == "CANCELLED"
                assert saved["claims"][0]["status"] == "CLAIMED"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_orphan_cleanup_follows_callers_rollback_and_has_no_internal_commit(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn)
                before = await snapshot(conn, ids)
            with pytest.raises(RuntimeError, match="abort"):
                async with db.begin() as conn:
                    await state.mark_orphan_execution_unknown(
                        conn,
                        expected=identity(ids),
                        from_status=state.ExecutionJobStatus.EXECUTING,
                        reason={},
                    )
                    await state.archive_orphan_execution_terminal(
                        conn, expected=identity(ids), l3_status="CANCELLED", result=None
                    )
                    raise RuntimeError("abort")
            async with db.connect() as conn:
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())
