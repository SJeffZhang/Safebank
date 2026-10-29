"""Products schema tests (real tmpfs PG via the obligations_db fixture).

Covers: upgrade head twice, constraint enforcement, DDL/TRUNCATE denial,
and 0003→0007 data preservation.
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


def _base(updates=None):
    vals = dict(
        id="prd_test_1",
        name="测试产品",
        product_type="MMF",
        risk_level=2,
        risk_label="R2",
        base_nav=Decimal("1.0000"),
        base_date=NOW,
        confirm_days=0,
        settle_days=0,
        min_amount_cents=100,
        risk_disclosure="风险提示文本",
        disclosure_version="v1.0",
    )
    if updates:
        vals.update(updates)
    return vals


_INSERT = text(
    "INSERT INTO products (id,name,product_type,risk_level,risk_label,"
    "base_nav,base_date,confirm_days,settle_days,min_amount_cents,"
    "risk_disclosure,disclosure_version) VALUES "
    "(:id,:name,:product_type,:risk_level,:risk_label,:base_nav,"
    ":base_date,:confirm_days,:settle_days,:min_amount_cents,"
    ":risk_disclosure,:disclosure_version)"
)


def test_upgrade_head_twice_succeeds(obligations_db):
    """Empty DB upgrade head twice — conftest does it. Verify version and columns."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0009_funds_units_nonneg"
                columns = {
                    row.column_name
                    for row in (
                        await conn.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name='products'"
                            )
                        )
                    ).mappings()
                }
                assert columns == {
                    "id",
                    "name",
                    "product_type",
                    "risk_level",
                    "risk_label",
                    "base_nav",
                    "base_date",
                    "confirm_days",
                    "settle_days",
                    "min_amount_cents",
                    "risk_disclosure",
                    "disclosure_version",
                }
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_product_type_rejects_invalid(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(_INSERT, _base())
                await conn.commit()

                try:
                    await conn.execute(_INSERT, _base({"id": "prd_bad", "product_type": "STOCK"}))
                    raise AssertionError("Expected CHECK violation for product_type=STOCK")
                except DBAPIError:
                    await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_risk_level_rejects_out_of_range(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(_INSERT, _base({"id": "prd_lvl", "risk_level": 3}))
                await conn.commit()

                for bad in (0, 6):
                    try:
                        await conn.execute(
                            _INSERT,
                            _base({"id": f"prd_lvl_b{abs(bad)}", "risk_level": bad}),
                        )
                        raise AssertionError(f"Expected CHECK violation for risk_level={bad}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_base_nav_rejects_non_positive(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(_INSERT, _base({"id": "prd_nav"}))
                await conn.commit()

                for bad in (Decimal("0.0000"), Decimal("-1.0000")):
                    try:
                        await conn.execute(
                            _INSERT,
                            _base({"id": f"prd_nav_b{str(bad)}", "base_nav": bad}),
                        )
                        raise AssertionError(f"Expected CHECK violation for base_nav={bad}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_min_amount_cents_rejects_non_positive(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(_INSERT, _base({"id": "prd_amt"}))
                await conn.commit()

                for bad in (0, -1):
                    try:
                        await conn.execute(
                            _INSERT,
                            _base({"id": f"prd_amt_b{abs(bad)}", "min_amount_cents": bad}),
                        )
                        raise AssertionError(f"Expected CHECK violation for min_amount_cents={bad}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_confirm_settle_days_reject_negative(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        try:
            async with admin.connect() as conn:
                await conn.execute(_INSERT, _base({"id": "prd_days"}))
                await conn.commit()

                for field in ("confirm_days", "settle_days"):
                    try:
                        await conn.execute(_INSERT, _base({"id": f"prd_{field}_b", field: -1}))
                        raise AssertionError(f"Expected CHECK violation for {field}=-1")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_bank_app_cannot_ddl_or_truncate(obligations_db):
    """Runtime role bank_app cannot CREATE/DROP/TRUNCATE/DELETE the products table."""

    async def check():
        app = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with app.connect() as conn:
                for stmt in (
                    "CREATE TABLE products_copy (LIKE products)",
                    "DROP TABLE products",
                    "TRUNCATE products",
                    "DELETE FROM products",
                ):
                    try:
                        await conn.execute(text(stmt))
                        raise AssertionError(f"Expected permission denied for: {stmt[:40]}")
                    except DBAPIError:
                        await conn.rollback()
        finally:
            await app.dispose()

    asyncio.run(check())


def test_bank_app_cannot_update_or_insert(obligations_db):
    """Runtime role bank_app cannot UPDATE or INSERT products."""

    async def check():
        app = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with app.connect() as conn:
                try:
                    await conn.execute(text("UPDATE products SET name='bad' WHERE id='any'"))
                    raise AssertionError("Expected permission denied for UPDATE")
                except DBAPIError:
                    await conn.rollback()

                try:
                    await conn.execute(
                        text(
                            "INSERT INTO products (id,name,product_type,risk_level,"
                            "risk_label,base_nav,base_date,confirm_days,settle_days,"
                            "min_amount_cents,risk_disclosure,disclosure_version) VALUES "
                            "('x','x','MMF',1,'R1','1.0','2026-01-01T00:00:00+08:00',"
                            "0,0,1,'x','v1')"
                        )
                    )
                    raise AssertionError("Expected permission denied for INSERT")
                except DBAPIError:
                    await conn.rollback()
        finally:
            await app.dispose()

    asyncio.run(check())


def test_0003_to_0007_preserves_existing_data(obligations_0002_db):
    """Upgrade from 0002→0003→0007 with seed data preserves old tables and data."""

    async def check():
        admin = engine(obligations_0002_db.admin_url, poolclass=NullPool)
        try:
            _migrate(obligations_0002_db, "0003_obligations")

            async with admin.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO merchants (id,name,mcc,category,usage_expected) "
                        "VALUES ('mer_seed','种子商户','0000','测试',false)"
                    )
                )

            async with admin.connect() as conn:
                old_count = await conn.scalar(
                    text("SELECT count(*) FROM merchants WHERE id='mer_seed'")
                )
                assert old_count == 1, "seed merchant should exist at 0003"

            _migrate(obligations_0002_db, "head")
            _migrate(obligations_0002_db, "head")

            async with admin.connect() as conn:
                version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert version == "0009_funds_units_nonneg"

                new_count = await conn.scalar(
                    text("SELECT count(*) FROM merchants WHERE id='mer_seed'")
                )
                assert new_count == 1, "merchants seed data must survive upgrade"

                prod_count = await conn.scalar(text("SELECT count(*) FROM products"))
                assert prod_count == 0, "products table should be empty after migration"
        finally:
            await admin.dispose()

    asyncio.run(check())
