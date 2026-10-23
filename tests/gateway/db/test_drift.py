import asyncio

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.pool import NullPool

from services.gateway.app import tables
from services.gateway.app.db import engine


def test_migrated_database_matches_metadata(gateway_db):
    async def check():
        db = engine(gateway_db.admin_url, poolclass=NullPool)
        async with db.connect() as conn:

            def run_sync(sync_conn):
                ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
                return compare_metadata(ctx, tables.metadata)

            diffs = await conn.run_sync(run_sync)
        await db.dispose()
        assert diffs == []

    asyncio.run(check())
