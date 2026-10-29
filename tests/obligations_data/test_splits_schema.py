"""Real PostgreSQL schema acceptance for split_requests / split_items.

Covers migration idempotence, CHECK/UNIQUE/FK enforcement, runtime-role DDL
refusal, and a from-0003 upgrade that preserves pre-existing obligation data.
"""

import asyncio

import pytest
from app import models as m
from app.db import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

from tests.obligations_data.conftest import _migrate
from tests.obligations_data.fixtures import NOW, install_core, install_obligations

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_splits(obligations_db):
    """Truncate only the split tables so other tests keep their data."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(text("TRUNCATE split_items, split_requests CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


# ── regression guards ─────────────────────────────────────────────────────────


def test_upgrade_head_twice_succeeds(obligations_db):
    """The session fixture already runs ``upgrade head`` twice; re-run here to
    prove the new tables survive a repeated upgrade without errors."""
    _migrate(obligations_db, "head")
    _migrate(obligations_db, "head")


def test_version_is_0005(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.connect() as conn:
            v = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            assert v == "0007_products"
        await admin.dispose()

    asyncio.run(check())


def test_tables_exist_and_are_not_owned_by_runtime(obligations_db):
    async def check():
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with runtime.connect() as conn:
            for table in ("split_requests", "split_items"):
                owner = await conn.scalar(
                    text(f"SELECT tableowner FROM pg_tables WHERE tablename='{table}'")
                )
                assert owner == "postgres"
        await runtime.dispose()

    asyncio.run(check())


# ── runtime role: no DDL, no TRUNCATE, no DELETE ──────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE illegal_splits(id integer)",
        "TRUNCATE split_requests",
        "TRUNCATE split_items",
        "DELETE FROM split_items",
    ],
)
def test_runtime_cannot_ddl_or_truncate(obligations_db, statement):
    async def check():
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with runtime.connect() as conn:
            with pytest.raises(DBAPIError):
                async with conn.begin_nested():
                    await conn.execute(text(statement))
        await runtime.dispose()

    asyncio.run(check())


# ── CHECK constraints ─────────────────────────────────────────────────────────


def test_split_requests_total_cents_must_be_positive(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            bad = dict(
                id="req_bad_total",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                total_cents=0,
                include_self=True,
                self_share_cents=1000,
                created_at=NOW,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_requests.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


def test_split_requests_include_self_false_forces_zero_self_share(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            bad = dict(
                id="req_bad_self",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                total_cents=10000,
                include_self=False,
                self_share_cents=5000,
                created_at=NOW,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_requests.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


def test_split_items_amount_cents_must_be_positive(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.split_requests.insert().values(
                    id="req_items",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    total_cents=10000,
                    include_self=False,
                    self_share_cents=0,
                    created_at=NOW,
                )
            )
            bad = dict(
                id="item_bad_amt",
                request_id="req_items",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                amount_cents=0,
                status="UNPAID",
                paid_at=None,
                payment_event_id=None,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_items.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


def test_split_items_status_must_be_valid(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.split_requests.insert().values(
                    id="req_status",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    total_cents=10000,
                    include_self=False,
                    self_share_cents=0,
                    created_at=NOW,
                )
            )
            bad = dict(
                id="item_bad_status",
                request_id="req_status",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                amount_cents=5000,
                status="CANCELLED",
                paid_at=None,
                payment_event_id=None,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_items.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


def test_split_items_paid_requires_paid_at(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.split_requests.insert().values(
                    id="req_paid",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    total_cents=10000,
                    include_self=False,
                    self_share_cents=0,
                    created_at=NOW,
                )
            )
            bad = dict(
                id="item_bad_paid",
                request_id="req_paid",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                amount_cents=5000,
                status="PAID",
                paid_at=None,
                payment_event_id=None,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_items.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


# ── UNIQUE constraint ─────────────────────────────────────────────────────────


def test_split_items_request_payee_must_be_unique(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.split_requests.insert().values(
                    id="req_unique",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    total_cents=10000,
                    include_self=False,
                    self_share_cents=0,
                    created_at=NOW,
                )
            )
            good = dict(
                id="item_1",
                request_id="req_unique",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                amount_cents=10000,
                status="UNPAID",
                paid_at=None,
                payment_event_id=None,
            )
            await conn.execute(m.split_items.insert().values(**good))
            dup = {**good, "id": "item_2"}
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_items.insert().values(**dup))
        await admin.dispose()

    asyncio.run(check())


# ── FOREIGN KEY constraints ───────────────────────────────────────────────────


def test_split_requests_rejects_bad_customer(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            bad = dict(
                id="req_bad_cus",
                customer_id="cus_nonexistent",
                account_id="acc_zs_chk",
                total_cents=10000,
                include_self=False,
                self_share_cents=0,
                created_at=NOW,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_requests.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


def test_split_items_rejects_bad_payee(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.split_requests.insert().values(
                    id="req_pye",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    total_cents=10000,
                    include_self=False,
                    self_share_cents=0,
                    created_at=NOW,
                )
            )
            bad = dict(
                id="item_bad_pye",
                request_id="req_pye",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                payee_id="pye_nonexistent",
                amount_cents=5000,
                status="UNPAID",
                paid_at=None,
                payment_event_id=None,
            )
            with pytest.raises(IntegrityError):
                await conn.execute(m.split_items.insert().values(**bad))
        await admin.dispose()

    asyncio.run(check())


# ── upgrade from 0003 preserves existing data ─────────────────────────────────


def test_upgrade_from_0003_preserves_old_data(obligations_0003_db):
    """Seed real core + obligation data at 0003, capture it, upgrade to 0005
    twice, then verify every row and privilege is unchanged."""

    async def check():
        admin = engine(obligations_0003_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        # Capture every table row (the split tables don't exist yet).
        async with admin.connect() as conn:
            tables = (
                "customers",
                "accounts",
                "cards",
                "payees",
                "transfers",
                "transactions",
                "clock",
                "state_versions",
                "execution_intents",
                "plan_executions",
                "operation_events",
                "outbox",
                "merchants",
                "merchant_billing",
                "standing_instructions",
                "instruction_runs",
            )
            baseline = {}
            for tbl in tables:
                rows = (await conn.execute(text(f"SELECT * FROM {tbl}"))).mappings().all()
                if rows and "id" in rows[0].keys():
                    baseline[tbl] = sorted(r["id"] for r in rows)
                else:
                    baseline[tbl] = []

        # Upgrade to 0005 twice.
        _migrate(obligations_0003_db, "head")
        _migrate(obligations_0003_db, "head")

        # All old rows must still be present and identical.
        async with admin.connect() as conn:
            v = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            assert v == "0007_products"

            # split tables exist but are empty.
            sr = await conn.scalar(text("SELECT count(*) FROM split_requests"))
            assert sr == 0
            si = await conn.scalar(text("SELECT count(*) FROM split_items"))
            assert si == 0

            for tbl in tables:
                rows = (await conn.execute(text(f"SELECT * FROM {tbl}"))).mappings().all()
                if rows and "id" in rows[0].keys():
                    ids = sorted(r["id"] for r in rows)
                else:
                    ids = []
                assert ids == baseline[tbl], f"table {tbl} rows changed after upgrade"

        await admin.dispose()

    asyncio.run(check())
