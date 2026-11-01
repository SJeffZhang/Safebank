"""Real PostgreSQL timestamptz round trip through production persist/load/read."""

import asyncio
from datetime import UTC, date, datetime

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
