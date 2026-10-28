"""Schema acceptance for the event_jobs table (migration 0004_event_jobs).

Requires an isolated temporary PostgreSQL (OBLIGATIONS_DB_TESTS=1).
"""

import asyncio
from datetime import UTC, datetime

import pytest
from app.db import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.obligations_data.conftest import _migrate

TZ = UTC


def _dt(*args: int) -> datetime:
    """Shortcut for timezone-aware datetimes."""
    return datetime(*args, tzinfo=TZ)


def _valid_row(**overrides) -> dict:
    """Produce a valid event_jobs row.  Tests can override any field.

    The default ``id`` and ``payload_hash`` are derived from a counter so that
    parallel *parametrize* cases and consecutive tests never collide on the
    ``UNIQUE(payload_hash)`` constraint.
    """
    _valid_row._counter += 1
    n = _valid_row._counter
    base: dict = dict(
        id=f"evt_test_{n:03d}",
        kind="SCHEDULED_TRANSFER",
        rank=3,
        due_at=_dt(2026, 11, 5, 6, 0, 0),
        immutable_payload=f'{{"si_id":"si_test_{n}","customer_id":"cus_zs"}}',
        payload_hash=f"{n:064x}",
        source_id=f"si_test_{n}",
        period_key="2026-11-05T06:00:00+08:00",
        status="PENDING",
        lease_until_wall=None,
        attempt_count=0,
    )
    base.update(overrides)
    return base


_valid_row._counter = 0  # type: ignore[attr-defined]


# ──  upgrade head twice  ──────────────────────────────────────────────


def test_upgrade_head_twice_succeeds(obligations_db):
    """Already done by the session-scoped fixture which calls head→head.
    This test verifies the alembic_version matches."""

    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.connect() as c:
            version = await c.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0004_event_jobs"
        await db.dispose()

    asyncio.run(check())


def test_event_jobs_table_exists_and_is_empty(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with db.connect() as c:
            count = await c.scalar(text("SELECT count(*) FROM event_jobs"))
            assert count == 0
        await db.dispose()

    asyncio.run(check())


# ──  CHECK constraints: each with one rejection case  ──────────────────


@pytest.mark.parametrize(
    "override,expected_violation",
    [
        ({"kind": "UNKNOWN_KIND"}, "event_jobs_kind_check"),
        ({"status": "COMPLETED"}, "event_jobs_status_check"),
        ({"rank": -1}, "event_jobs_rank_check"),
        ({"attempt_count": -3}, "event_jobs_attempt_count_check"),
    ],
)
def test_check_constraint_rejects_invalid(obligations_db, override, expected_violation):
    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        row = _valid_row(**override)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        async with db.connect() as c:
            with pytest.raises(DBAPIError, match=expected_violation):
                await c.execute(
                    text(f"INSERT INTO event_jobs ({cols}) VALUES ({placeholders})"), row
                )
                await c.commit()
            await c.rollback()
        await db.dispose()

    asyncio.run(check())


# ──  unique constraint rejection  ──────────────────────────────────────


def test_duplicate_payload_hash_rejected(obligations_db):
    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        first = _valid_row(id="evt_dup_1")
        # Same payload_hash as first row, different id.
        second = _valid_row(id="evt_dup_2", payload_hash=first["payload_hash"])
        async with db.connect() as c:
            cols = ", ".join(first.keys())
            placeholders = ", ".join(f":{k}" for k in first)
            await c.execute(text(f"INSERT INTO event_jobs ({cols}) VALUES ({placeholders})"), first)
            await c.commit()
            with pytest.raises(DBAPIError, match="event_jobs_payload_hash_key"):
                await c.execute(
                    text(f"INSERT INTO event_jobs ({cols}) VALUES ({placeholders})"), second
                )
                await c.commit()
            await c.rollback()
        await db.dispose()

    asyncio.run(check())


# ──  runtime role: no DDL / TRUNCATE  ──────────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE illegal_events(id integer)",
        "TRUNCATE event_jobs",
        "DELETE FROM event_jobs",
    ],
)
def test_runtime_cannot_ddl_or_truncate(obligations_db, statement):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with db.connect() as c:
            with pytest.raises(DBAPIError):
                async with c.begin_nested():
                    await c.execute(text(statement))
        await db.dispose()

    asyncio.run(check())


# ──  valid insert through runtime role  ────────────────────────────────


def test_runtime_can_insert_and_update(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        row = _valid_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        async with db.begin() as c:
            await c.execute(text(f"INSERT INTO event_jobs ({cols}) VALUES ({placeholders})"), row)
        # Runtime must also be able to UPDATE (lease, status, attempts).
        async with db.begin() as c:
            await c.execute(
                text(
                    "UPDATE event_jobs SET status='RUNNING', lease_until_wall=:lease, "
                    "attempt_count=attempt_count + 1 WHERE id=:id"
                ),
                {"lease": _dt(2026, 11, 5, 6, 0, 1), "id": row["id"]},
            )
        async with db.connect() as c:
            status = await c.scalar(
                text("SELECT status FROM event_jobs WHERE id=:id"), {"id": row["id"]}
            )
            assert status == "RUNNING"
        await db.dispose()

    asyncio.run(check())


# ──  upgrade from 0003 preserves existing data  ────────────────────────


def test_0003_data_survives_0004_upgrade(obligations_0002_db):
    """Start from 0002, upgrade to 0003 (seed obligation data), then to 0004."""

    async def check():
        admin = engine(obligations_0002_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_0002_db.runtime_url, poolclass=NullPool)

        # Upgrade to 0003 and seed test data.
        _migrate(obligations_0002_db, "0003_obligations")

        async with admin.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO merchants (id,name,mcc,category,usage_expected) "
                    "VALUES ('mer_test','Test','0000','Test',false)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO merchant_billing (id,customer_id,account_id,merchant_id,"
                    "amount_cents,period,anchor_day,weekday,time_of_day,next_due_at,active,"
                    "created_at) VALUES ('bill_test','cus_zs','acc_zs_chk','mer_test',3000,"
                    "'MONTHLY',18,NULL,'06:00:00'::time,"
                    "'2026-11-18T06:00:00+08:00'::timestamptz,true,"
                    "'2026-10-15T09:00:00+08:00'::timestamptz)"
                )
            )

        # Capture the data.
        async with admin.connect() as conn:
            bill_count = await conn.scalar(text("SELECT count(*) FROM merchant_billing"))
            merchant_count = await conn.scalar(text("SELECT count(*) FROM merchants"))

        # Upgrade to 0004.
        _migrate(obligations_0002_db, "0004_event_jobs")
        _migrate(obligations_0002_db, "0004_event_jobs")

        async with admin.connect() as conn:
            version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0004_event_jobs"
            # Old data intact.
            assert await conn.scalar(text("SELECT count(*) FROM merchant_billing")) == bill_count
            assert await conn.scalar(text("SELECT count(*) FROM merchants")) == merchant_count
            # New table exists and is empty.
            assert await conn.scalar(text("SELECT count(*) FROM event_jobs")) == 0

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
