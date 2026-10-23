"""Actual PostgreSQL lock ownership and failure tests; no fake lock/backend."""

import asyncio
import secrets
from contextlib import asynccontextmanager, nullcontext

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from services.gateway.app import locks
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError

LOCK = "SELECT pg_advisory_lock(hashtextextended('cus:' || :cid, 0))"
TRY_LOCK = "SELECT pg_try_advisory_lock(hashtextextended('cus:' || :cid, 0))"
UNLOCK = "SELECT pg_advisory_unlock(hashtextextended('cus:' || :cid, 0))"
HELD = """
SELECT EXISTS (
 SELECT 1 FROM pg_locks
 WHERE locktype='advisory' AND granted AND objsubid=1
 AND classid=((hashtextextended('cus:' || :cid,0) >> 32) & 4294967295)::oid
 AND objid=(hashtextextended('cus:' || :cid,0) & 4294967295)::oid
)
"""


@asynccontextmanager
async def database(gateway_db, *, slots=4, **kwargs):
    db = engine(gateway_db.runtime_url, pool_size=6, max_overflow=0, **kwargs)
    locks.configure(db, slots)
    try:
        yield db
    finally:
        await db.dispose()


def cid():
    return "cus_" + secrets.token_hex(8)


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not await predicate():
            await asyncio.sleep(0.01)


async def acquired(conn, customer):
    value = await conn.scalar(text(HELD), {"cid": customer})
    await conn.commit()
    return value


def test_prd_key_dedup_and_multiple_short_transactions(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            a, b = cid(), cid()
            async with locks.customer_lock([b, a, b]) as owner:
                assert isinstance(owner, AsyncConnection)
                assert not owner.in_transaction()
                for number in range(3):
                    async with owner.begin():
                        assert (
                            await owner.scalar(
                                text("SELECT CAST(:value AS integer)"), {"value": number}
                            )
                            == number
                        )
                        assert await owner.scalar(text("SHOW lock_timeout")) == "5s"
                async with db.begin() as outsider:
                    for customer in (a, b):
                        assert not await outsider.scalar(text(TRY_LOCK), {"cid": customer})
                async with owner.begin():
                    count = await owner.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype='advisory' AND pid=pg_backend_pid()"
                        )
                    )
                    assert count == 2
            async with db.begin() as outsider:
                assert await outsider.scalar(text(TRY_LOCK), {"cid": a})
                await outsider.execute(text(UNLOCK), {"cid": a})

    asyncio.run(run())


def test_sort_order_partial_timeout_and_no_lock_leak(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            low, high = sorted([cid(), cid()])
            async with db.connect() as blocker, db.connect() as observer:
                await blocker.execute(text(LOCK), {"cid": high})
                await blocker.commit()

                async def contender():
                    with pytest.raises(GatewayError) as error:
                        async with locks.customer_lock([high, low]):
                            pytest.fail("contended lock yielded")
                    assert error.value.code == Code.EXECUTION_IN_PROGRESS
                    assert error.value.status == 409

                task = asyncio.create_task(contender())
                try:
                    # Low is acquired even though caller supplied high first.
                    await wait_for(lambda: acquired(observer, low))
                    await task
                    assert await observer.scalar(text(TRY_LOCK), {"cid": low})
                    await observer.execute(text(UNLOCK), {"cid": low})
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await blocker.execute(text(UNLOCK), {"cid": high})

    asyncio.run(run())


def test_opposite_orders_finish_without_deadlock(gateway_db):
    async def run():
        async with database(gateway_db):
            a, b = cid(), cid()
            entered = 0
            active = 0

            async def contender(index):
                nonlocal entered, active
                async with locks.customer_lock([a, b] if index % 2 else [b, a]):
                    active += 1
                    assert active == 1
                    entered += 1
                    await asyncio.sleep(0.01)
                    active -= 1

            async with asyncio.timeout(3):
                await asyncio.gather(*(contender(i) for i in range(8)))
            assert entered == 8

    asyncio.run(run())


def test_different_customers_acquire_in_parallel(gateway_db):
    async def run():
        async with database(gateway_db):
            both = asyncio.Event()
            count = 0

            async def contender():
                nonlocal count
                async with locks.customer_lock([cid()]):
                    count += 1
                    if count == 2:
                        both.set()
                    await both.wait()

            async with asyncio.timeout(3):
                await asyncio.gather(contender(), contender())
            assert count == 2

    asyncio.run(run())


def test_semaphore_bounds_dedicated_connections(gateway_db):
    async def run():
        async with database(gateway_db, slots=2):
            active = 0
            peak = 0

            async def contender():
                nonlocal active, peak
                async with locks.customer_lock([cid()]):
                    active += 1
                    peak = max(peak, active)
                    await asyncio.sleep(0.03)
                    active -= 1

            await asyncio.gather(*(contender() for _ in range(6)))
            assert peak == 2

    asyncio.run(run())


def test_cancel_during_partial_acquire_releases_and_restores_pool(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            low, high = sorted([cid(), cid()])
            async with db.connect() as blocker, db.connect() as observer:
                await blocker.execute(text(LOCK), {"cid": high})
                await blocker.commit()

                async def contender():
                    async with locks.customer_lock([high, low]):
                        pytest.fail("contended lock yielded")

                task = asyncio.create_task(contender())
                try:
                    await wait_for(lambda: acquired(observer, low))
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert await observer.scalar(text(TRY_LOCK), {"cid": low})
                    await observer.execute(text(UNLOCK), {"cid": low})
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await blocker.execute(text(UNLOCK), {"cid": high})
            async with locks.customer_lock([low]):
                pass

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["normal", "error", "cancel"])
def test_exit_restores_settings_and_pool_reuse(gateway_db, outcome):
    async def run():
        # A single physical connection makes cleanup observable on next borrow.
        db = engine(
            gateway_db.runtime_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"lock_timeout": "1234ms"}},
        )
        locks.configure(db, 2)
        ready = asyncio.Event()
        wait = asyncio.Event()
        captured = []
        record_maps = []
        checkin_before = list(db.sync_engine.pool.dispatch.checkin)

        async def holder():
            async with locks.customer_lock([cid()]) as owner:
                captured.append(owner.sync_connection)
                record_maps.append((await owner.get_raw_connection()).record_info)
                ready.set()
                if outcome == "error":
                    raise RuntimeError("body failed")
                if outcome == "cancel":
                    await wait.wait()

        try:
            task = asyncio.create_task(holder())
            await ready.wait()
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "error":
                with pytest.raises(RuntimeError, match="body failed"):
                    await task
            else:
                await task
            assert list(db.sync_engine.pool.dispatch.checkin) == checkin_before
            assert record_maps and not record_maps[0]
            assert captured[0].closed
            # Per-instance listeners must be removed before pooled reuse.
            assert not list(captured[0].dispatch.before_cursor_execute)
            assert not list(captured[0].dispatch.begin)
            assert not list(captured[0].dispatch.commit)
            async with db.begin() as reused:
                assert await reused.scalar(text("SHOW lock_timeout")) == "1234ms"
                assert (
                    await reused.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype='advisory' AND pid=pg_backend_pid()"
                        )
                    )
                    == 0
                )
            async with locks.customer_lock([cid()]) as next_owner:
                async with next_owner.begin():
                    assert await next_owner.scalar(text("SELECT 1")) == 1
        finally:
            await db.dispose()

    asyncio.run(run())


def test_killed_owner_cannot_rollback_reconnect_and_keep_writing(gateway_db):
    async def run():
        async with database(gateway_db):
            admin = engine(gateway_db.admin_url)
            customer = cid()
            try:
                async with locks.customer_lock([customer]) as owner:
                    pid = await owner.scalar(text("SELECT pg_backend_pid()"))
                    await owner.commit()
                    async with admin.begin() as killer:
                        assert await killer.scalar(
                            text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                        )
                    # The first failed command can surface from the driver or
                    # the sticky owner guard; rollback must never revive owner.
                    with pytest.raises((DBAPIError, GatewayError)):
                        await owner.execute(text("SELECT 1"))
                    await owner.rollback()
                    async with locks.customer_lock([customer]) as successor:
                        async with successor.begin():
                            assert await successor.scalar(text("SELECT pg_backend_pid()")) != pid
                        with pytest.raises(GatewayError):
                            await owner.execute(text("SELECT 1"))
                        with pytest.raises(GatewayError):
                            await locks.assert_lock_owner(owner)
            finally:
                await admin.dispose()

    asyncio.run(run())


def test_guard_checks_original_session_without_committing_caller(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            async with locks.customer_lock([cid()]) as owner:
                await locks.assert_lock_owner(owner)
                assert not owner.in_transaction()
                async with owner.begin():
                    transaction = owner.get_transaction()
                    pid = await owner.scalar(text("SELECT pg_backend_pid()"))
                    await locks.assert_lock_owner(owner)
                    assert owner.get_transaction() == transaction
                    assert await owner.scalar(text("SELECT pg_backend_pid()")) == pid
                await locks.assert_lock_owner(owner)
            with pytest.raises(locks.LockOwnershipLost):
                await locks.assert_lock_owner(owner)
            async with db.connect() as unowned:
                with pytest.raises(locks.LockOwnershipLost):
                    await locks.assert_lock_owner(unowned)

    asyncio.run(run())


def test_guard_missing_expected_lock_is_sticky(gateway_db):
    async def run():
        async with database(gateway_db):
            customer = cid()
            async with locks.customer_lock([customer]) as owner:
                async with owner.begin():
                    await owner.execute(text(UNLOCK), {"cid": customer})
                with pytest.raises(locks.LockOwnershipLost):
                    await locks.assert_lock_owner(owner)
                await owner.rollback()
                with pytest.raises(locks.LockOwnershipLost):
                    await owner.execute(text(LOCK), {"cid": customer})
            async with locks.customer_lock([customer]):
                pass

    asyncio.run(run())


def test_early_close_discards_physical_owner_and_leaves_pool_clean(gateway_db):
    async def run():
        db = engine(
            gateway_db.runtime_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"lock_timeout": "1234ms"}},
        )
        locks.configure(db, 2)
        customer = cid()
        try:
            async with locks.customer_lock([customer]) as owner:
                pid = await owner.scalar(text("SELECT pg_backend_pid()"))
                await owner.commit()
                await owner.close()
                with pytest.raises(GatewayError):
                    await locks.assert_lock_owner(owner)
                async with db.begin() as borrowed:
                    # Premature return must discard, rather than lend the still
                    # locked backend to an unrelated caller.
                    assert await borrowed.scalar(text("SELECT pg_backend_pid()")) != pid
                    assert await borrowed.scalar(text("SHOW lock_timeout")) == "1234ms"
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
        finally:
            await db.dispose()

    asyncio.run(run())


def test_terminate_owner_blocks_commit_and_rollback_cannot_revive(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            admin = engine(gateway_db.admin_url)
            customer = cid()
            sid = "ses_" + secrets.token_hex(8)
            try:
                async with locks.customer_lock([customer]) as owner:
                    raw = await owner.get_raw_connection()
                    pid = await owner.scalar(text("SELECT pg_backend_pid()"))
                    await owner.execute(
                        text(
                            "INSERT INTO sessions(id,customer_id,created_at_wall) "
                            "VALUES(:sid,:cid,now())"
                        ),
                        {"sid": sid, "cid": customer},
                    )
                    async with admin.begin() as killer:
                        assert await killer.scalar(
                            text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                        )

                    async def disconnected():
                        return raw.driver_connection.is_closed()

                    await wait_for(disconnected)
                    with pytest.raises(locks.LockOwnershipLost):
                        await owner.commit()
                    await owner.rollback()
                    with pytest.raises(locks.LockOwnershipLost):
                        async with owner.begin():
                            pytest.fail("dead owner began another transaction")
                    with pytest.raises(locks.LockOwnershipLost):
                        await locks.assert_lock_owner(owner)
                async with db.begin() as observer:
                    assert (
                        await observer.scalar(
                            text("SELECT count(*) FROM sessions WHERE id=:sid"), {"sid": sid}
                        )
                        == 0
                    )
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
            finally:
                await admin.dispose()

    asyncio.run(run())


def test_explicit_invalidate_does_not_allow_automatic_reconnect(gateway_db):
    async def run():
        async with database(gateway_db):
            async with locks.customer_lock([cid()]) as owner:
                await owner.invalidate()
                await owner.rollback()
                with pytest.raises(locks.LockOwnershipLost):
                    await owner.execute(text("SELECT 1"))
                with pytest.raises(locks.LockOwnershipLost):
                    await locks.assert_lock_owner(owner)

    asyncio.run(run())


def test_early_close_never_invalidates_a_different_active_owner(gateway_db):
    async def run():
        async with database(gateway_db):
            async with locks.customer_lock([cid()]) as healthy:
                async with healthy.begin():
                    before = await healthy.scalar(text("SELECT pg_backend_pid()"))
                async with locks.customer_lock([cid()]) as early:
                    await early.close()
                    with pytest.raises(locks.LockOwnershipLost):
                        await locks.assert_lock_owner(early)
                    await locks.assert_lock_owner(healthy)
                async with healthy.begin():
                    assert await healthy.scalar(text("SELECT pg_backend_pid()")) == before

    asyncio.run(run())


def test_concurrent_early_close_listener_cleanup_does_not_race(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            checkin_before = list(db.sync_engine.pool.dispatch.checkin)
            gate = asyncio.Event()
            entered = 0

            async def contender(index):
                nonlocal entered
                expected = pytest.raises(locks.LockOwnershipLost) if index % 2 else nullcontext()
                with expected:
                    async with locks.customer_lock([cid()]) as owner:
                        entered += 1
                        if entered == 4:
                            gate.set()
                        await gate.wait()
                        if index % 2:
                            await owner.close()

            async with asyncio.timeout(5):
                await asyncio.gather(*(contender(i) for i in range(4)))
            assert list(db.sync_engine.pool.dispatch.checkin) == checkin_before

    asyncio.run(run())


def test_unobserved_early_close_is_reported_on_context_exit(gateway_db):
    async def run():
        async with database(gateway_db):
            with pytest.raises(locks.LockOwnershipLost):
                async with locks.customer_lock([cid()]) as owner:
                    await owner.close()

    asyncio.run(run())


def test_repeated_configure_installs_one_stable_pool_dispatcher(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            before = list(db.sync_engine.pool.dispatch.checkin)
            assert len(before) == 1
            for _ in range(3):
                locks.configure(db, 2)
                async with locks.customer_lock([cid()]):
                    pass
            assert list(db.sync_engine.pool.dispatch.checkin) == before

    asyncio.run(run())


def test_distinct_engines_keep_independent_pool_owners(gateway_db):
    async def run():
        first = engine(gateway_db.runtime_url, pool_size=1, max_overflow=0)
        second = engine(gateway_db.runtime_url, pool_size=1, max_overflow=0)
        locks.configure(first, 2)
        try:
            async with locks.customer_lock([cid()]) as owner_first:
                with pytest.raises(RuntimeError):
                    locks.configure(second, 2)
                assert not list(second.sync_engine.pool.dispatch.checkin)
                await locks.assert_lock_owner(owner_first)
            # Idle reconfiguration remains supported, with independent hooks.
            locks.configure(second, 2)
            async with locks.customer_lock([cid()]) as owner_second:
                assert owner_second.sync_connection.engine is second.sync_engine
                await owner_second.close()
                with pytest.raises(locks.LockOwnershipLost):
                    await locks.assert_lock_owner(owner_second)
            assert len(list(first.sync_engine.pool.dispatch.checkin)) == 1
            assert len(list(second.sync_engine.pool.dispatch.checkin)) == 1
            async with first.begin() as normal:
                assert await normal.scalar(text("SELECT 1")) == 1
        finally:
            await first.dispose()
            await second.dispose()

    asyncio.run(run())


def test_none_checkin_removes_record_owner_before_reuse(gateway_db):
    async def run():
        async with database(gateway_db) as db:
            observed = []

            def observe(dbapi, _record):
                observed.append(dbapi)

            event.listen(db.sync_engine.pool, "checkin", observe)
            try:
                async with locks.customer_lock([cid()]) as owner:
                    record_info = (await owner.get_raw_connection()).record_info
                    assert record_info
                    await owner.invalidate()
                    assert observed and observed[-1] is None
                    assert not record_info
                    await owner.rollback()
                    with pytest.raises(locks.LockOwnershipLost):
                        await locks.assert_lock_owner(owner)
                async with locks.customer_lock([cid()]) as successor:
                    await locks.assert_lock_owner(successor)
                assert not record_info
            finally:
                event.remove(db.sync_engine.pool, "checkin", observe)

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_step", ["rollback", "unlock", "settings"])
@pytest.mark.parametrize("body_outcome", ["normal", "business", "cancel"])
def test_cleanup_disconnect_classification_preserves_original_error(
    gateway_db, cleanup_step, body_outcome
):
    """Kill the actual backend AFTER the guard but BEFORE cleanup sends SQL."""
    from sqlalchemy.util.concurrency import await_

    async def run():
        async with database(gateway_db):
            admin = engine(gateway_db.admin_url)
            paused, release, body_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
            body_wait = asyncio.Event()
            original = RuntimeError("caller business failure")
            captured = []
            pid = []
            record_maps = []
            customer = cid()

            def pause(_connection, *_args):
                paused.set()
                await_(release.wait())

            def before_cleanup(connection, _cursor, statement, *_args):
                if (cleanup_step == "unlock" and "pg_advisory_unlock_all" in statement) or (
                    cleanup_step == "settings" and "set_config('lock_timeout'" in statement
                ):
                    pause(connection)

            async def holder():
                async with locks.customer_lock([customer]) as owner:
                    captured.append(owner)
                    record_maps.append((await owner.get_raw_connection()).record_info)
                    pid.append(await owner.scalar(text("SELECT pg_backend_pid()")))
                    if cleanup_step != "rollback":
                        await owner.commit()
                    if cleanup_step == "rollback":
                        event.listen(owner.sync_connection, "rollback", pause)
                    else:
                        # Registered after the production statement guard so
                        # the transport loss happens inside the actual send gap.
                        event.listen(owner.sync_connection, "before_cursor_execute", before_cleanup)
                    body_ready.set()
                    if body_outcome == "business":
                        raise original
                    if body_outcome == "cancel":
                        await body_wait.wait()

            task = asyncio.create_task(holder())
            try:
                async with asyncio.timeout(4):
                    await body_ready.wait()
                    if body_outcome == "cancel":
                        task.cancel()
                    await paused.wait()
                    async with admin.begin() as killer:
                        assert await killer.scalar(
                            text("SELECT pg_terminate_backend(:pid)"), {"pid": pid[0]}
                        )
                    release.set()
                    if body_outcome == "normal":
                        with pytest.raises(locks.LockOwnershipLost):
                            await task
                    elif body_outcome == "business":
                        with pytest.raises(RuntimeError) as error:
                            await task
                        assert error.value is original
                    else:
                        with pytest.raises(asyncio.CancelledError):
                            await task
                assert record_maps and not record_maps[0]
                assert captured[0].closed
                with pytest.raises(locks.LockOwnershipLost):
                    await locks.assert_lock_owner(captured[0])
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if captured:
                    sync = captured[0].sync_connection
                    if cleanup_step == "rollback":
                        event.remove(sync, "rollback", pause)
                    else:
                        event.remove(sync, "before_cursor_execute", before_cleanup)
                await admin.dispose()

    asyncio.run(run())


def test_same_configuration_keeps_semaphore_bound_with_active_owner(gateway_db):
    async def run():
        async with database(gateway_db, slots=1) as db:
            entered, release = asyncio.Event(), asyncio.Event()
            task = None

            async def contender():
                async with locks.customer_lock([cid()]):
                    entered.set()
                    await release.wait()

            try:
                async with locks.customer_lock([cid()]):
                    original_slots = locks._slots
                    locks.configure(db, 1)
                    task = asyncio.create_task(contender())
                    with pytest.raises(TimeoutError):
                        async with asyncio.timeout(0.1):
                            await entered.wait()
                    assert locks._slots is original_slots
            finally:
                release.set()
                if task:
                    await task

    asyncio.run(run())


def test_changed_configuration_rejected_atomically_while_owner_active(gateway_db):
    async def run():
        async with database(gateway_db, slots=1) as db:
            other = engine(gateway_db.runtime_url)
            try:
                before = list(db.sync_engine.pool.dispatch.checkin)
                original_slots = locks._slots
                async with locks.customer_lock([cid()]):
                    with pytest.raises(RuntimeError):
                        locks.configure(other, 1)
                    with pytest.raises(RuntimeError):
                        locks.configure(db, 2)
                    assert locks._engine is db and locks._slots is original_slots
                    assert list(db.sync_engine.pool.dispatch.checkin) == before
                    assert not list(other.sync_engine.pool.dispatch.checkin)
            finally:
                await other.dispose()

    asyncio.run(run())


def test_semaphore_waiter_blocks_reconfiguration_even_after_previous_owner_exits(gateway_db):
    async def run():
        async with database(gateway_db, slots=1) as db:
            entered, release = asyncio.Event(), asyncio.Event()
            original_slots = locks._slots
            first = locks.customer_lock([cid()])
            await first.__aenter__()

            async def contender():
                async with locks.customer_lock([cid()]):
                    entered.set()
                    await release.wait()

            task = asyncio.create_task(contender())
            exited = False
            try:

                async def queued():
                    return bool(original_slots._waiters)

                await wait_for(queued)
                locks.configure(db, 1)
                assert locks._slots is original_slots
                await first.__aexit__(None, None, None)
                exited = True
                # No owner has a DB connection now. The resumed semaphore
                # waiter has not run yet, but still captured the old config.
                assert not entered.is_set()
                with pytest.raises(RuntimeError):
                    locks.configure(db, 2)
                assert locks._slots is original_slots
            finally:
                if not exited:
                    await first.__aexit__(None, None, None)
                release.set()
                await task

    asyncio.run(run())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_pool_waiter_blocks_changes_and_cancellation_releases_request(gateway_db, cancel_waiter):
    async def run():
        db = engine(gateway_db.runtime_url, pool_size=1, max_overflow=0)
        other = engine(gateway_db.runtime_url, pool_size=1, max_overflow=0)
        locks.configure(db, 2)
        task = None
        entered = False

        async def contender():
            nonlocal entered
            async with locks.customer_lock([cid()]):
                entered = True

        try:
            original_slots = locks._slots
            async with db.connect():
                # This external non-lock borrower exhausts the actual PG pool.
                task = asyncio.create_task(contender())

                async def queued():
                    return bool(db.sync_engine.pool._pool._queue._getters)

                await wait_for(queued)
                with pytest.raises(RuntimeError):
                    locks.configure(other, 2)
                with pytest.raises(RuntimeError):
                    locks.configure(db, 3)
                assert locks._engine is db and locks._slots is original_slots
                assert not list(other.sync_engine.pool.dispatch.checkin)
                if cancel_waiter:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    # No owner ever existed: cancellation must still drop the
                    # captured-config request count and permit idle changes.
                    locks.configure(other, 2)
            if not cancel_waiter:
                await task
                assert entered
                locks.configure(other, 2)
            else:
                assert not entered
            async with locks.customer_lock([cid()]) as owner:
                await locks.assert_lock_owner(owner)
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await db.dispose()
            await other.dispose()

    asyncio.run(run())


def test_cancelled_semaphore_waiter_does_not_leave_configuration_busy(gateway_db):
    async def run():
        async with database(gateway_db, slots=1) as db:
            original_slots = locks._slots
            async with locks.customer_lock([cid()]):

                async def contender():
                    async with locks.customer_lock([cid()]):
                        pytest.fail("cancelled semaphore waiter entered")

                task = asyncio.create_task(contender())

                async def queued():
                    return bool(original_slots._waiters)

                await wait_for(queued)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                with pytest.raises(RuntimeError):
                    locks.configure(db, 2)
            locks.configure(db, 2)
            async with locks.customer_lock([cid()]) as owner:
                await locks.assert_lock_owner(owner)

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_step", ["unlock", "settings"])
def test_non_disconnect_cleanup_sql_error_retains_type_and_discards(gateway_db, cleanup_step):
    async def run():
        async with database(gateway_db):
            captured = []
            record_maps = []
            customer = cid()

            def fail_sql(_connection, _cursor, statement, parameters, _context, _many):
                if (cleanup_step == "unlock" and "pg_advisory_unlock_all" in statement) or (
                    cleanup_step == "settings" and "set_config('lock_timeout'" in statement
                ):
                    return "SELECT 1/0", ()
                return statement, parameters

            try:
                with pytest.raises(DBAPIError) as error:
                    async with locks.customer_lock([customer]) as owner:
                        captured.append(owner)
                        record_maps.append((await owner.get_raw_connection()).record_info)
                        event.listen(
                            owner.sync_connection, "before_cursor_execute", fail_sql, retval=True
                        )
                assert not error.value.connection_invalidated
                assert error.value.orig.sqlstate == "22012"
                assert captured[0].closed and not record_maps[0]
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
            finally:
                if captured:
                    event.remove(captured[0].sync_connection, "before_cursor_execute", fail_sql)

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_step", ["rollback", "unlock", "settings"])
def test_fresh_cancellation_during_cleanup_is_preserved(gateway_db, cleanup_step):
    from sqlalchemy.util.concurrency import await_

    async def run():
        async with database(gateway_db):
            paused = asyncio.Event()
            release = asyncio.Event()
            captured = []
            record_maps = []
            customer = cid()

            def pause(_connection, *_args):
                paused.set()
                await_(release.wait())

            def before_cleanup(connection, _cursor, statement, *_args):
                if (cleanup_step == "unlock" and "pg_advisory_unlock_all" in statement) or (
                    cleanup_step == "settings" and "set_config('lock_timeout'" in statement
                ):
                    pause(connection)

            async def holder():
                async with locks.customer_lock([customer]) as owner:
                    captured.append(owner)
                    record_maps.append((await owner.get_raw_connection()).record_info)
                    await owner.scalar(text("SELECT 1"))
                    if cleanup_step != "rollback":
                        await owner.commit()
                    if cleanup_step == "rollback":
                        event.listen(owner.sync_connection, "rollback", pause)
                    else:
                        event.listen(owner.sync_connection, "before_cursor_execute", before_cleanup)

            task = asyncio.create_task(holder())
            try:
                async with asyncio.timeout(3):
                    await paused.wait()
                    task.cancel("cleanup cancellation")
                    with pytest.raises(asyncio.CancelledError) as error:
                        await task
                assert error.value.args == ("cleanup cancellation",)
                assert captured[0].closed and not record_maps[0]
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if captured:
                    if cleanup_step == "rollback":
                        event.remove(captured[0].sync_connection, "rollback", pause)
                    else:
                        event.remove(
                            captured[0].sync_connection, "before_cursor_execute", before_cleanup
                        )

    asyncio.run(run())


@pytest.mark.parametrize("body_outcome", ["business", "cancel"])
def test_post_discard_secondary_error_cannot_replace_pending_failure(
    gateway_db, monkeypatch, body_outcome
):
    """Actual invalidate completes before injecting a secondary disposal error."""
    original_discard = locks._discard

    async def discard_then_fail(conn):
        await original_discard(conn)
        raise RuntimeError("secondary disposal failure after physical discard")

    monkeypatch.setattr(locks, "_discard", discard_then_fail)

    async def run():
        async with database(gateway_db):
            captured = []
            record_maps = []
            ready = asyncio.Event()
            body_wait = asyncio.Event()
            original = RuntimeError("original business error")
            customer = cid()

            def cleanup_failure(_connection, _cursor, statement, *_args):
                if "pg_advisory_unlock_all" in statement:
                    raise ValueError("cleanup failure")

            async def holder():
                async with locks.customer_lock([customer]) as owner:
                    captured.append(owner)
                    record_maps.append((await owner.get_raw_connection()).record_info)
                    event.listen(owner.sync_connection, "before_cursor_execute", cleanup_failure)
                    ready.set()
                    if body_outcome == "business":
                        raise original
                    await body_wait.wait()

            task = asyncio.create_task(holder())
            try:
                await ready.wait()
                if body_outcome == "business":
                    with pytest.raises(RuntimeError) as error:
                        await task
                    assert error.value is original
                else:
                    task.cancel("original cancellation")
                    with pytest.raises(asyncio.CancelledError) as error:
                        await task
                    assert error.value.args == ("original cancellation",)
                assert captured[0].closed and not record_maps[0]
                async with locks.customer_lock([customer]) as successor:
                    await locks.assert_lock_owner(successor)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if captured:
                    event.remove(
                        captured[0].sync_connection, "before_cursor_execute", cleanup_failure
                    )

    asyncio.run(run())
