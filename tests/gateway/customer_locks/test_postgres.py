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
                locks.configure(second, 2)
                async with locks.customer_lock([cid()]) as owner_second:
                    assert owner_first.sync_connection.engine is first.sync_engine
                    assert owner_second.sync_connection.engine is second.sync_engine
                    await owner_first.close()
                    with pytest.raises(locks.LockOwnershipLost):
                        await locks.assert_lock_owner(owner_first)
                    await locks.assert_lock_owner(owner_second)
            assert len(list(first.sync_engine.pool.dispatch.checkin)) == 1
            assert len(list(second.sync_engine.pool.dispatch.checkin)) == 1
            async with second.begin() as normal:
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
