"""Funds schema tests (real tmpfs PG via the obligations_db fixture).

Covers: upgrade head twice, constraint enforcement, event_executions terminal
immutability, DDL denial for the runtime role, and 0002→0008 data preservation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.obligations_data.conftest import _migrate

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 1, 0, 0, 0, tzinfo=TZ)

_CUSTOMER = text(
    "INSERT INTO customers (id,display_name,phone) VALUES (:id,'测试客户','13800000000')"
)
_ACCOUNT = text(
    "INSERT INTO accounts (id,customer_id,type,display_name,account_no,"
    "single_limit_cents,daily_limit_cents) VALUES "
    "(:id,:customer_id,'CHECKING','活期',:account_no,1000000,1000000)"
)
_PRODUCT = text(
    "INSERT INTO products (id,name,product_type,risk_level,risk_label,base_nav,"
    "base_date,confirm_days,settle_days,min_amount_cents,risk_disclosure,"
    "disclosure_version) VALUES "
    "(:id,'测试产品','MMF',2,'R2',:base_nav,:base_date,0,0,100,'风险提示','v1')"
)

_HOLDING = text(
    "INSERT INTO holdings (customer_id,account_id,product_id,units,reserved_units,"
    "cost_cents,reserved_cost_cents) VALUES "
    "(:customer_id,:account_id,:product_id,:units,:reserved_units,:cost_cents,"
    ":reserved_cost_cents)"
)

_ORDER = text(
    "INSERT INTO fund_orders (id,customer_id,account_id,product_id,type,status,"
    "amount_cents,units,reserved_cost_cents,plan_id,step_id,trade_date) VALUES "
    "(:id,:customer_id,:account_id,:product_id,:type,:status,:amount_cents,:units,"
    ":reserved_cost_cents,:plan_id,:step_id,:trade_date)"
)

_EVENT_JOB = text(
    "INSERT INTO event_jobs (id,kind,rank,due_at,immutable_payload,payload_hash,"
    "source_id,period_key,status) VALUES "
    "(:id,'FUND_CONFIRM',0,:due_at,'{}'::jsonb,:payload_hash,:source_id,"
    ":period_key,'PENDING')"
)

_EXECUTION = text(
    "INSERT INTO event_executions (event_id,status,authorization_evidence,"
    "result_json,committed_at) VALUES "
    "(:event_id,:status,CAST(:authorization_evidence AS jsonb),"
    "CAST(:result_json AS jsonb),:committed_at)"
)


async def _seed_env(conn, tag):
    """Seed one customer, two CHECKING accounts and one product; return the ids.

    Two accounts let bad rows target ``acc2`` so the (customer, account, product)
    composite key stays distinct from the good row while the CHECK still fires.
    """
    cus = f"cus_{tag}"
    acc = f"acc_{tag}"
    acc2 = f"acc2_{tag}"
    prd = f"prd_{tag}"
    await conn.execute(_CUSTOMER, {"id": cus})
    for a, no in ((acc, f"no_{tag}"), (acc2, f"no2_{tag}")):
        await conn.execute(_ACCOUNT, {"id": a, "customer_id": cus, "account_no": no})
    await conn.execute(_PRODUCT, {"id": prd, "base_nav": Decimal("1.0000"), "base_date": NOW})
    return {"cus": cus, "acc": acc, "acc2": acc2, "prd": prd}


def _holding(env, **updates):
    vals = dict(
        customer_id=env["cus"],
        account_id=env["acc"],
        product_id=env["prd"],
        units=Decimal("10.0000"),
        reserved_units=Decimal("0.0000"),
        cost_cents=10000,
        reserved_cost_cents=0,
    )
    vals.update(updates)
    return vals


def _order(tag, env, **updates):
    vals = dict(
        id=f"ord_{tag}",
        customer_id=env["cus"],
        account_id=env["acc"],
        product_id=env["prd"],
        type="SUB",
        status="PENDING_CONFIRM",
        amount_cents=10000,
        units=Decimal("10.0000"),
        reserved_cost_cents=10000,
        plan_id=f"plan_{tag}",
        step_id="s1",
        trade_date=NOW,
    )
    vals.update(updates)
    return vals


def test_upgrade_head_twice_succeeds(obligations_db):
    """Empty DB upgrade head twice — conftest does it. Verify version and columns."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0009_funds_units_nonneg"
                for table, expected in (
                    (
                        "holdings",
                        {
                            "customer_id",
                            "account_id",
                            "product_id",
                            "units",
                            "reserved_units",
                            "cost_cents",
                            "reserved_cost_cents",
                        },
                    ),
                    (
                        "fund_orders",
                        {
                            "id",
                            "customer_id",
                            "account_id",
                            "product_id",
                            "type",
                            "status",
                            "amount_cents",
                            "units",
                            "nav",
                            "reserved_cost_cents",
                            "pending_transfer_id",
                            "plan_id",
                            "step_id",
                            "trade_date",
                            "confirm_at",
                            "settle_at",
                            "expires_at",
                            "failure_code",
                        },
                    ),
                    (
                        "event_executions",
                        {
                            "event_id",
                            "status",
                            "authorization_evidence",
                            "result_json",
                            "committed_at",
                        },
                    ),
                ):
                    columns = {
                        row.column_name
                        for row in (
                            await conn.execute(
                                text(
                                    "SELECT column_name FROM information_schema.columns "
                                    f"WHERE table_name='{table}'"
                                )
                            )
                        ).mappings()
                    }
                    assert columns == expected, f"{table}: {columns!r} != {expected!r}"
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_holdings_rejects_reserved_exceeding_units(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                env = await _seed_env(conn, "h_ovr")
                await conn.execute(_HOLDING, _holding(env))
                await conn.commit()

                try:
                    await conn.execute(
                        _HOLDING,
                        _holding(
                            env,
                            account_id=env["acc2"],
                            units=Decimal("5.0000"),
                            reserved_units=Decimal("6.0000"),
                        ),
                    )
                    raise AssertionError("Expected CHECK violation for reserved_units > units")
                except DBAPIError:
                    await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_holdings_rejects_negative_or_cost_inversion(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                env = await _seed_env(conn, "h_neg")
                await conn.execute(_HOLDING, _holding(env))
                await conn.commit()

                bads = (
                    _holding(env, account_id=env["acc2"], reserved_units=Decimal("-1.0000")),
                    _holding(env, account_id=env["acc2"], reserved_cost_cents=-1),
                    _holding(env, account_id=env["acc2"], cost_cents=100, reserved_cost_cents=200),
                )
                for i, bad in enumerate(bads):
                    try:
                        await conn.execute(_HOLDING, bad)
                        raise AssertionError(f"Expected CHECK violation for holding #{i}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_holdings_rejects_duplicate_owner_product(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                env = await _seed_env(conn, "h_dup")
                await conn.execute(_HOLDING, _holding(env))
                await conn.commit()

                try:
                    await conn.execute(_HOLDING, _holding(env))
                    raise AssertionError("Expected unique violation on (customer,account,product)")
                except DBAPIError:
                    await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_fund_orders_rejects_invalid_type_and_status(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                env = await _seed_env(conn, "o_t")
                await conn.execute(_ORDER, _order("o_t", env))
                await conn.commit()

                for bad in ({"type": "BUY"}, {"status": "CANCELLED"}):
                    try:
                        await conn.execute(_ORDER, _order("o_t_bad", env, **bad))
                        raise AssertionError(f"Expected CHECK violation for {bad}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_fund_orders_rejects_non_positive_amount_or_units(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                env = await _seed_env(conn, "o_amt")
                await conn.execute(_ORDER, _order("o_amt", env))
                await conn.commit()

                for bad in (
                    {"amount_cents": 0},
                    {"amount_cents": -1},
                    {"units": Decimal("0.0000")},
                    {"reserved_cost_cents": -1},
                ):
                    try:
                        await conn.execute(_ORDER, _order("o_amt_bad", env, **bad))
                        raise AssertionError(f"Expected CHECK violation for {bad}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_event_executions_terminal_immutable(obligations_db):
    """event_executions rows are terminal: the append-only trigger blocks UPDATE/DELETE."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    _EVENT_JOB,
                    {
                        "id": "evj_imm",
                        "due_at": NOW,
                        "payload_hash": "h_imm",
                        "source_id": "src_imm",
                        "period_key": "p_imm",
                    },
                )
                await conn.execute(
                    _EXECUTION,
                    {
                        "event_id": "evj_imm",
                        "status": "SUCCESS",
                        "authorization_evidence": "{}",
                        "result_json": "{}",
                        "committed_at": NOW,
                    },
                )
                await conn.commit()

                for stmt in (
                    "UPDATE event_executions SET status='FAILED' WHERE event_id='evj_imm'",
                    "DELETE FROM event_executions WHERE event_id='evj_imm'",
                ):
                    try:
                        await conn.execute(text(stmt))
                        raise AssertionError(f"Expected append-only rejection for: {stmt[:40]}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_bank_app_cannot_ddl_funds_tables(obligations_db):
    """Runtime role bank_app cannot CREATE/DROP/TRUNCATE/DELETE the new funds tables."""

    async def check():
        app = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with app.connect() as conn:
                for table in ("holdings", "fund_orders", "event_executions"):
                    for stmt in (
                        f"CREATE TABLE {table}_copy (LIKE {table})",
                        f"DROP TABLE {table}",
                        f"TRUNCATE {table}",
                        f"DELETE FROM {table}",
                    ):
                        try:
                            await conn.execute(text(stmt))
                            raise AssertionError(f"Expected permission denied for: {stmt[:40]}")
                        except DBAPIError:
                            await conn.rollback()
        finally:
            await app.dispose()

    asyncio.run(check())


def test_0002_to_head_preserves_existing_data(obligations_0002_db):
    """Upgrade from 0002→0003→head with seed data preserves old tables and data."""

    async def check():
        admin = engine(obligations_0002_db.admin_url, poolclass=NullPool)
        try:
            _migrate(obligations_0002_db, "0003_obligations")

            async with admin.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO merchants (id,name,mcc,category,usage_expected) "
                        "VALUES ('mer_funds','种子商户','0000','测试',false)"
                    )
                )

            async with admin.connect() as conn:
                old_count = await conn.scalar(
                    text("SELECT count(*) FROM merchants WHERE id='mer_funds'")
                )
                assert old_count == 1, "seed merchant should exist at 0003"

            _migrate(obligations_0002_db, "head")
            _migrate(obligations_0002_db, "head")

            async with admin.connect() as conn:
                version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0009_funds_units_nonneg"

                new_count = await conn.scalar(
                    text("SELECT count(*) FROM merchants WHERE id='mer_funds'")
                )
                assert new_count == 1, "merchants seed data must survive upgrade"

                for table in ("holdings", "fund_orders", "event_executions"):
                    cnt = await conn.scalar(text(f"SELECT count(*) FROM {table}"))
                    assert cnt == 0, f"{table} should be empty after migration"
        finally:
            await admin.dispose()

    asyncio.run(check())
