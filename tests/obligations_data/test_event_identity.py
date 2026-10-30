"""An event hash binds content; a business source and instant identify its run."""

import asyncio
from datetime import timedelta

import pytest
from app import models as m
from app.db import engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import NOW
from tests.obligations_data.test_scheduler import clean_database  # noqa: F401


def row(id, due, period, *, hash="same-content"):
    return dict(
        id=id,
        kind="SCHEDULED_TRANSFER",
        rank=3,
        due_at=due,
        immutable_payload={"payload": "same instruction"},
        payload_hash=hash,
        source_id="si_recurrence",
        period_key=period,
        status="PENDING",
        attempt_count=0,
    )


def test_same_instruction_payload_can_recur_next_month(obligations_db):
    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(m.event_jobs.insert().values(**row("first", NOW, NOW.isoformat())))
            later = NOW + timedelta(days=30)
            await conn.execute(
                m.event_jobs.insert().values(**row("next", later, later.isoformat()))
            )
        await db.dispose()

    asyncio.run(check())


def test_same_source_instant_cannot_reappear_with_other_timezone_text(obligations_db):
    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(m.event_jobs.insert().values(**row("first", NOW, NOW.isoformat())))
        with pytest.raises(DBAPIError):
            async with db.begin() as conn:
                await conn.execute(
                    m.event_jobs.insert().values(
                        **row(
                            "duplicate", NOW, "other-zone-equivalent-instant", hash="different-hash"
                        )
                    )
                )
        await db.dispose()

    asyncio.run(check())


def test_upgrade_keeps_existing_event_and_allows_successor(obligations_0003_db):
    from sqlalchemy import text

    from tests.obligations_data.conftest import _migrate

    db_info = obligations_0003_db
    _migrate(db_info, "0009_funds_units_nonneg")

    async def before():
        db = engine(db_info.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(m.event_jobs.insert().values(**row("legacy", NOW, NOW.isoformat())))
        await db.dispose()

    asyncio.run(before())
    _migrate(db_info, "head")

    async def after():
        db = engine(db_info.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            assert (
                await conn.scalar(text("select payload_hash from event_jobs where id='legacy'"))
                == "same-content"
            )
            later = NOW + timedelta(days=30)
            await conn.execute(
                m.event_jobs.insert().values(**row("successor", later, later.isoformat()))
            )
        await db.dispose()

    asyncio.run(after())


def test_upgrade_refuses_duplicate_history_without_deleting_rows(obligations_0003_db):
    import subprocess

    from sqlalchemy import text

    from tests.obligations_data.conftest import _migrate

    db_info = obligations_0003_db
    _migrate(db_info, "0009_funds_units_nonneg")

    async def before():
        db = engine(db_info.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(
                m.event_jobs.insert().values(**row("legacy_a", NOW, "zone-a", hash="a"))
            )
            await conn.execute(
                m.event_jobs.insert().values(**row("legacy_b", NOW, "zone-b", hash="b"))
            )
        await db.dispose()

    asyncio.run(before())
    with pytest.raises(subprocess.CalledProcessError):
        _migrate(db_info, "head")

    async def after():
        db = engine(db_info.admin_url, poolclass=NullPool)
        async with db.connect() as conn:
            assert await conn.scalar(text("select count(*) from event_jobs")) == 2
            assert (
                await conn.scalar(text("select version_num from alembic_version"))
                == "0009_funds_units_nonneg"
            )
        await db.dispose()

    asyncio.run(after())
