import asyncio
import secrets

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import text

from services.gateway.app import locks
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError


def test_lock_exclusive_sorted_reentrant_pool_and_exception_release(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, pool_size=3, max_overflow=0)
        locks.configure(db, 3)
        cid = "cus_" + secrets.token_hex(6)
        entered = []
        ready = asyncio.Event()
        release = asyncio.Event()

        async def first():
            with pytest.raises(RuntimeError):
                async with locks.customer_lock([cid + "b", cid + "a", cid + "a"]) as conn:
                    assert not conn.in_transaction()
                    entered.append("first")
                    ready.set()
                    await release.wait()
                    raise RuntimeError("caller failed")

        async def second():
            await ready.wait()
            async with locks.customer_lock([cid + "a", cid + "b"]) as conn:
                assert not conn.in_transaction()
                entered.append("second")

        a = asyncio.create_task(first())
        b = asyncio.create_task(second())
        await ready.wait()
        await asyncio.sleep(0.05)
        assert entered == ["first"]
        release.set()
        await asyncio.gather(a, b)
        assert entered == ["first", "second"]
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype='advisory' AND pid=pg_backend_pid()"
                    )
                )
                == 0
            )
        await db.dispose()

    asyncio.run(run())


def test_lock_timeout_is_409_and_does_not_leak_earlier_locks(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, pool_size=3, max_overflow=0)
        locks.configure(db, 3)
        cid = "timeout_" + secrets.token_hex(6)
        async with locks.customer_lock([cid + "z"]):
            with pytest.raises(GatewayError) as caught:
                async with locks.customer_lock([cid + "a", cid + "z"]):
                    pytest.fail("contended lock was acquired")
            assert caught.value.code == Code.EXECUTION_IN_PROGRESS
            # The first lock acquired before timing out was released.
            async with locks.customer_lock([cid + "a"]):
                pass
        await db.dispose()

    asyncio.run(run())


def test_semaphore_bounds_dedicated_connections(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, pool_size=3, max_overflow=0)
        locks.configure(db, 1)
        active = 0
        peak = 0

        async def job(index):
            nonlocal active, peak
            async with locks.customer_lock([f"slot_{index}"]):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*(job(i) for i in range(4)))
        assert peak == 1
        await db.dispose()

    asyncio.run(run())
