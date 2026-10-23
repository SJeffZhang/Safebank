import asyncio

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.audit.chain import append, verify_chain
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


def test_verify_detects_tampered_content(gateway_db):
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
        # A privileged administrator (or anyone with rewrite access) tampering
        # must be detectable by the chain; the runtime role itself cannot UPDATE.
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
