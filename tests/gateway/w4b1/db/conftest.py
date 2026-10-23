"""W4b1 DB fixtures: isolated temporary PostgreSQL with per-test cleanup.

The gateway ``gateway_db`` fixture is session-scoped; each test truncates the
gateway schema (as the privileged migration/admin role) so fixed seed IDs never
collide across tests. Only the temporary database is touched.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.db.conftest import gateway_db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_gateway(request):
    db_url = request.getfixturevalue("gateway_db")

    async def truncate():
        db = engine(db_url.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            names = ",".join(t.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await db.dispose()

    asyncio.run(truncate())
