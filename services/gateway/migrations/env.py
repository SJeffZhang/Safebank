import asyncio
import os

from alembic import context

from services.gateway.app.db import engine
from services.gateway.app.tables import metadata


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    db = engine(os.environ["GATEWAY_MIGRATION_DATABASE_URL"])
    async with db.connect() as connection:
        await connection.run_sync(run)
    await db.dispose()


asyncio.run(main())
