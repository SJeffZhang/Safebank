import asyncio

import pytest
from app.db import engine, ready
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.integration.conftest import test_url as database_url

pytestmark = pytest.mark.integration


def test_migration_exists_and_runtime_does_not_own_tables():
    async def check():
        db = engine(database_url(), poolclass=NullPool)
        async with db.connect() as c:
            assert await c.scalar(text("SELECT version_num FROM alembic_version")) == "0005_splits"
            assert (
                await c.scalar(text("SELECT tableowner FROM pg_tables WHERE tablename='accounts'"))
                == "postgres"
            )
            for statement in ("CREATE TABLE illegal(id integer)", "TRUNCATE accounts"):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(statement))
        await db.dispose()

    asyncio.run(check())


def test_readiness_depends_on_database_and_seed():
    async def check():
        db = engine(database_url(), poolclass=NullPool)
        admin = engine(database_url(True), poolclass=NullPool)
        async with admin.begin() as c:
            await c.execute(
                text("INSERT INTO clock VALUES(1,now(),1,'p1_core_v1') ON CONFLICT(id) DO NOTHING")
            )
        assert await ready(db)
        async with db.connect() as c:
            assert await c.scalar(text("SHOW transaction_isolation")) == "read committed"
        await db.dispose()
        await admin.dispose()

    asyncio.run(check())


def test_sql_constraints_and_append_only_triggers():
    async def check():
        db = engine(database_url(), poolclass=NullPool)
        async with db.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(
                    text(
                        "INSERT INTO customers(id,display_name,phone) "
                        "VALUES('cus_constraints','test','000')"
                    )
                )
                for suffix in ("a", "b"):
                    await conn.execute(
                        text("""INSERT INTO accounts(id,customer_id,type,display_name,account_no,
                        single_limit_cents,daily_limit_cents)
                        VALUES(:id,'cus_constraints','CHECKING','test',:id,100,100)"""),
                        {"id": "acc_constraint_" + suffix},
                    )
                with pytest.raises(DBAPIError):
                    async with conn.begin_nested():
                        await conn.execute(
                            text(
                                "UPDATE accounts SET debits_posted_cents=1 "
                                "WHERE id='acc_constraint_a'"
                            )
                        )
                await conn.execute(
                    text("""INSERT INTO transfers(id,debit_account_id,credit_account_id,
                        amount_cents,code,phase,step_id,leg,occurred_at)
                    VALUES('trf_constraint','acc_constraint_a','acc_constraint_b',1,'TEST','POSTED','s1','main',now())""")
                )
                with pytest.raises(DBAPIError):
                    async with conn.begin_nested():
                        await conn.execute(
                            text("UPDATE transfers SET amount_cents=2 WHERE id='trf_constraint'")
                        )
                with pytest.raises(DBAPIError):
                    async with conn.begin_nested():
                        await conn.execute(
                            text("""INSERT INTO transfers(id,debit_account_id,credit_account_id,
                        amount_cents,code,phase,step_id,leg,occurred_at)
                            VALUES('trf_invalid','acc_constraint_a','acc_constraint_a',1,'TEST','POSTED','s1','main',now())""")
                        )
            finally:
                await transaction.rollback()
        await db.dispose()

    asyncio.run(check())


def test_database_failure_cannot_report_ready():
    async def check():
        url = database_url().replace("@postgres-bank/", "@postgres-bank:5433/")
        db = engine(url, poolclass=NullPool, connect_args={"timeout": 1})
        with pytest.raises((OSError, DBAPIError)):
            await ready(db)
        await db.dispose()

    asyncio.run(check())
