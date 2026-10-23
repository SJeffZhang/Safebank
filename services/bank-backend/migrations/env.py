import asyncio
import os

from alembic import context
from app.db import engine
from app.models import metadata


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    db = engine(os.environ["BANK_MIGRATION_DATABASE_URL"])
    async with db.connect() as connection:
        await connection.run_sync(run)
    await db.dispose()


asyncio.run(main())
