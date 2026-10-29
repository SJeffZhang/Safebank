import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine


def test_migration_applied_and_runtime_does_not_own_audit(gateway_db):
    async def check():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.connect() as conn:
            assert (
                await conn.scalar(text("SELECT version_num FROM alembic_version"))
                == "0004_in_progress"
            )
            owner = await conn.scalar(
                text("SELECT tableowner FROM pg_tables WHERE tablename='audit_events'")
            )
            assert owner == "postgres"
        await db.dispose()

    asyncio.run(check())


def test_runtime_role_cannot_update_delete_or_drop_audit(gateway_db):
    async def check():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_events (id, customer_id, event_type, payload_digest,"
                    " payload, prev_hash, hash, created_at_wall)"
                    " VALUES ('m_evt_1','cus_1','test','d','{}','p','h',now())"
                )
            )
        async with db.connect() as conn:
            assert (
                await conn.scalar(text("SELECT count(*) FROM audit_events WHERE id = 'm_evt_1'"))
                == 1
            )
            with pytest.raises(DBAPIError):
                await conn.execute(text("UPDATE audit_events SET hash='x' WHERE id='m_evt_1'"))
            with pytest.raises(DBAPIError):
                await conn.execute(text("DELETE FROM audit_events"))
            with pytest.raises(DBAPIError):
                await conn.execute(text("DROP TABLE audit_events"))
        await db.dispose()

    asyncio.run(check())


def test_gateway_admin_role_exists(gateway_db):
    async def check():
        db = engine(gateway_db.admin_url, poolclass=NullPool)
        async with db.connect() as conn:
            present = await conn.scalar(
                text("SELECT count(*) FROM pg_roles WHERE rolname='gateway_admin'")
            )
            assert present == 1
        await db.dispose()

    asyncio.run(check())
