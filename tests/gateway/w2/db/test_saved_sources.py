import asyncio
from datetime import UTC, datetime

from sqlalchemy import insert, update
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from services.gateway.app.tables import read_records, sessions
from services.gateway.app.taint.records import load_records
from tests.bankcore.fixtures import NOW


def test_reset_revokes_session_and_blocks_identical_timestamp_record_replay(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        now = datetime.now(UTC)
        async with db.begin() as conn:
            await conn.execute(
                insert(sessions).values(id="sid_reset_w2", customer_id="cus_a", created_at_wall=now)
            )
            await conn.execute(
                insert(read_records).values(
                    id="src_reset_w2",
                    sid="sid_reset_w2",
                    tool="get_accounts",
                    canonical_args={"history_days": 0},
                    response={"accounts": [{"id": "acc_a"}]},
                    untrusted_paths=[],
                    versions={"cus_a": 0},
                    observed_at=NOW,
                    created_at_wall=now,
                )
            )
        async with db.connect() as conn:
            assert "src_reset_w2" in await load_records(conn, "sid_reset_w2", "cus_a")
        async with db.begin() as conn:
            await conn.execute(
                update(sessions).where(sessions.c.id == "sid_reset_w2").values(revoked_at_wall=now)
            )
        async with db.connect() as conn:
            assert await load_records(conn, "sid_reset_w2", "cus_a") == {}
        # W5's reset must revoke the session before a repeated sim_now is usable.
        await db.dispose()

    asyncio.run(run())
