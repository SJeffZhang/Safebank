import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.audit.chain import append, append_tx, verify_chain
from services.gateway.app.db import engine


def _runtime(gateway_db):
    return engine(gateway_db.runtime_url, poolclass=NullPool)


def test_concurrent_append_does_not_fork_chain(gateway_db):
    async def run():
        db = _runtime(gateway_db)

        async def one(i: int):
            async with db.begin() as conn:
                await append(
                    conn,
                    customer_id="cus_1",
                    event_id=f"c_evt_{i}",
                    event_type="test",
                    payload={"i": i},
                )

        await asyncio.gather(*(one(i) for i in range(10)))
        async with db.connect() as conn:
            ok, detail = await verify_chain(conn, "cus_1")
            assert ok, detail
            count = await conn.scalar(
                text("SELECT count(*) FROM audit_events WHERE customer_id='cus_1'")
            )
            assert count == 10
        await db.dispose()

    asyncio.run(run())


def test_append_tx_commits_own_transaction(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        for i in range(3):
            await append_tx(
                db,
                customer_id="cus_3",
                event_id=f"a_evt_{i}",
                event_type="test",
                payload={"i": i},
            )
        async with db.connect() as conn:
            ok, detail = await verify_chain(conn, "cus_3")
            assert ok, detail
        await db.dispose()

    asyncio.run(run())


def test_verify_accepts_non_utc_clock_probe_c(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        plus8 = timezone(timedelta(hours=8))
        async with db.begin() as conn:
            await append(
                conn,
                customer_id="cus_5",
                event_id="z_evt_0",
                event_type="t",
                payload={"i": 0},
                now_wall=datetime(2026, 10, 15, 9, 0, 0, 123456, tzinfo=plus8),
            )
        async with db.connect() as conn:
            ok, detail = await verify_chain(conn, "cus_5")
            assert ok, detail
        await db.dispose()

    asyncio.run(run())


def test_verify_detects_tampered_payload(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        for i in range(3):
            async with db.begin() as conn:
                await append(
                    conn,
                    customer_id="cus_2",
                    event_id=f"t_evt_{i}",
                    event_type="test",
                    payload={"i": i},
                )
        async with db.connect() as conn:
            ok, _ = await verify_chain(conn, "cus_2")
            assert ok
        admin = engine(gateway_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                text("UPDATE audit_events SET payload = :payload WHERE id = 't_evt_1'"),
                {"payload": '{"tampered": true}'},
            )
        await admin.dispose()
        async with db.connect() as conn:
            ok, detail = await verify_chain(conn, "cus_2")
            assert not ok
            assert "tampered" in detail
        await db.dispose()

    asyncio.run(run())


def test_verify_detects_tampered_timestamp(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await append(
                conn, customer_id="cus_4", event_id="w_evt_0", event_type="t", payload={"i": 0}
            )
        async with db.connect() as conn:
            ok, _ = await verify_chain(conn, "cus_4")
            assert ok
        admin = engine(gateway_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                text("UPDATE audit_events SET created_at_wall = now() + interval '1 day'")
            )
        await admin.dispose()
        async with db.connect() as conn:
            ok, detail = await verify_chain(conn, "cus_4")
            assert not ok
            assert "tampered" in detail
        await db.dispose()

    asyncio.run(run())
