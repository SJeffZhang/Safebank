import os
from contextlib import asynccontextmanager

from app.db import engine
from app.models import metadata
from app.seed import seed_core
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from tests.integration.conftest import test_url


@asynccontextmanager
async def database():
    admin = engine(test_url(True), poolclass=NullPool)
    runtime = engine(test_url(), poolclass=NullPool)
    async with admin.begin() as conn:
        names = ",".join(metadata.tables)
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        assert await seed_core(conn, os.environ["FINGERPRINT_KEY"])
    try:
        yield runtime, admin
    finally:
        await runtime.dispose()
        await admin.dispose()
