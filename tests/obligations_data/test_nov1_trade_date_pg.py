"""Real PostgreSQL timestamptz round trip through production persist/load/read."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from app import models as m
from app.db import engine
from app.reads import read
from app.state import load_bank_state, persist
from safebank_contracts.state import FUTURE_EFFECTS, FundOrderRecord, StateScope
from safebank_contracts.tools import READ_ARGS
from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import NOW, install_core
from tests.obligations_data.test_funds_exec import _seed_products


def test_business_trade_date_survives_actual_utc_storage_and_read_projection(obligations_db):
    async def run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with admin.begin() as conn:
                await conn.execute(text("TRUNCATE " + ",".join(m.metadata.tables) + " CASCADE"))
                await install_core(conn)
                await _seed_products(conn)
            async with db.begin() as conn:
                order = FundOrderRecord(
                    id="ord_date_projection",
                    customer_id="cus_ls",
                    account_id="acc_ls_chk",
                    product_id="prd_mmf_01",
                    type="SUB",
                    status="SETTLED",
                    amount_cents=10000,
                    units="100.0000",
                    nav="1.0000",
                    reserved_cost_cents=0,
                    pending_transfer_id=None,
                    plan_id="pln_fixture_date",
                    step_id="s_date",
                    trade_date=date(2026, 10, 15),
                    confirm_at=NOW,
                    settle_at=NOW,
                    expires_at=None,
                    failure_code=None,
                )
                await persist(conn, (FUTURE_EFFECTS["InsertFundOrder"](order=order),))
            async with db.connect() as conn:
                await conn.execute(text("SET TIME ZONE 'UTC'"))
                stored = await conn.scalar(select(m.fund_orders.c.trade_date))
                assert stored == datetime(2026, 10, 14, 16, tzinfo=UTC)
                loaded = await load_bank_state(
                    conn, StateScope(customer_ids=("cus_ls",)), NOW, customer_id="cus_ls"
                )
                public = read(
                    loaded,
                    "get_holdings",
                    READ_ARGS["get_holdings"](account_id=None, product_id=None),
                    "cus_ls",
                )
                assert loaded.fund_orders[0].trade_date == date(2026, 10, 15)
                assert public.orders[0].trade_date == date(2026, 10, 15)
                assert public.orders[0].confirm_at == NOW and public.orders[0].settle_at == NOW
        finally:
            await db.dispose()
            await admin.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("hour", [0, 12])
def test_assessment_expiry_and_midnight_nav_anchor_survive_pg_utc(obligations_db, hour):
    async def run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        business = ZoneInfo("Asia/Shanghai")
        assessed = datetime(2026, 10, 15, hour, 30, tzinfo=business)
        expiry = datetime(2027, 10, 15, hour, 30, tzinfo=business)
        anchor = datetime(2026, 10, 15, tzinfo=business)
        try:
            async with admin.begin() as conn:
                await conn.execute(text("TRUNCATE " + ",".join(m.metadata.tables) + " CASCADE"))
                await install_core(conn)
                await _seed_products(conn)
                await conn.execute(
                    m.customers.update()
                    .where(m.customers.c.id == "cus_ls")
                    .values(risk_level=1, risk_valid_until="2027-10-15")
                )
                await conn.execute(
                    m.risk_assessments.insert().values(
                        id="asm_fixture_date",
                        customer_id="cus_ls",
                        questionnaire_id="rq_v1",
                        answers=[[f"q0{i}", "a"] for i in range(1, 7)],
                        score=6,
                        level="R1",
                        assessed_at=assessed,
                        valid_until=expiry,
                        plan_id="pln_fixture_date",
                    )
                )
                await conn.execute(
                    m.products.insert().values(
                        id="prd_midnight",
                        name="合成午夜基准产品",
                        product_type="BOND",
                        risk_level=1,
                        risk_label="R1",
                        base_nav="1.0000",
                        base_date=anchor,
                        confirm_days=1,
                        settle_days=2,
                        min_amount_cents=10000,
                        risk_disclosure="合成风险揭示",
                        disclosure_version="v1",
                    )
                )
            async with db.connect() as conn:
                await conn.execute(text("SET TIME ZONE 'UTC'"))
                assert await conn.scalar(
                    select(m.products.c.base_date).where(m.products.c.id == "prd_midnight")
                ) == datetime(2026, 10, 14, 16, tzinfo=UTC)
                loaded = await load_bank_state(
                    conn, StateScope(customer_ids=("cus_ls",)), assessed, customer_id="cus_ls"
                )
                r13 = read(loaded, "get_risk_profile", READ_ARGS["get_risk_profile"](), "cus_ls")
                r18 = read(loaded, "get_profile", READ_ARGS["get_profile"](), "cus_ls")
                product = read(
                    loaded,
                    "get_product",
                    READ_ARGS["get_product"](product_id="prd_midnight"),
                    "cus_ls",
                )
                assert r13.valid_until == r18.risk_profile.valid_until == date(2027, 10, 15)
                assert r13.is_valid is r18.risk_profile.is_valid is True
                assert product.nav_date == date(2026, 10, 15) and product.nav == Decimal("1.0000")
        finally:
            await db.dispose()
            await admin.dispose()

    asyncio.run(run())
