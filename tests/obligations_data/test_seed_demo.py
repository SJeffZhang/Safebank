"""P2 demo-customer seed installs incrementally and idempotently alongside P1 data."""

import asyncio
import socket
import threading
from datetime import date
from decimal import Decimal

import httpx
import pytest
import uvicorn
from app.db import engine
from app.main import create_app
from app.seed import seed_core
from app.seed_demo import install_demo
from safebank_bankcore.products import ProductDef, get_product
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY

SERVICE_TOKEN = "obligations-test-token"

COUNTED_TABLES = (
    "customers",
    "accounts",
    "cards",
    "payees",
    "merchants",
    "transfers",
    "transactions",
    "merchant_billing",
    "standing_instructions",
)

# PRD §2.2 demo wealth products; base_nav is the NAV at the seed sim_now date
# (2026-10-15, Asia/Shanghai), so base_date is that same day.
EXPECTED_PRODUCTS = {
    "prd_mmf": dict(
        name="安心货币",
        product_type="MMF",
        risk_level=1,
        risk_label="保守",
        confirm_days=0,
        settle_days=0,
        min_amount_cents=100,
        base_nav=Decimal("1.0000"),
    ),
    "prd_sbond": dict(
        name="稳盈短债",
        product_type="BOND",
        risk_level=2,
        risk_label="稳健",
        confirm_days=1,
        settle_days=1,
        min_amount_cents=100000,
        base_nav=Decimal("1.0520"),
    ),
    "prd_stable": dict(
        name="稳健增利",
        product_type="BOND",
        risk_level=3,
        risk_label="稳健",
        confirm_days=1,
        settle_days=1,
        min_amount_cents=100000,
        base_nav=Decimal("1.2310"),
    ),
    "prd_mixed": dict(
        name="均衡混合",
        product_type="MIXED",
        risk_level=3,
        risk_label="平衡",
        confirm_days=1,
        settle_days=2,
        min_amount_cents=100000,
        base_nav=Decimal("1.4875"),
    ),
    "prd_growth": dict(
        name="成长股票",
        product_type="EQUITY",
        risk_level=4,
        risk_label="进取",
        confirm_days=1,
        settle_days=2,
        min_amount_cents=100000,
        base_nav=Decimal("2.0310"),
    ),
    "prd_tech": dict(
        name="科技先锋",
        product_type="EQUITY",
        risk_level=5,
        risk_label="激进",
        confirm_days=1,
        settle_days=2,
        min_amount_cents=100000,
        base_nav=Decimal("1.8820"),
    ),
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


async def _row_counts(conn) -> dict[str, int]:
    return {
        table: await conn.scalar(text(f"SELECT count(*) FROM {table}")) for table in COUNTED_TABLES
    }


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table so the test starts from an empty bank."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            from app import models as m

            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


def test_seed_demo_installs_alongside_p1(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True

        # Capture P1 state the demo install must leave untouched.
        async with admin.connect() as conn:
            p1_acc_a = (
                await conn.execute(
                    text(
                        "SELECT credits_posted_cents, debits_posted_cents, "
                        "debits_pending_cents, credits_pending_cents FROM accounts "
                        "WHERE id='acc_a'"
                    )
                )
            ).one()
            p1_sim_now = await conn.scalar(text("SELECT sim_now FROM clock WHERE id=1"))
            p1_dataset = await conn.scalar(text("SELECT dataset_revision FROM clock WHERE id=1"))
            p1_acc_a_transfers = await conn.scalar(
                text(
                    "SELECT count(*) FROM transfers "
                    "WHERE debit_account_id='acc_a' OR credit_account_id='acc_a'"
                )
            )

        async with admin.begin() as conn:
            summary = await install_demo(conn, KEY)
        assert summary["added_customers"] == 4
        assert summary["skipped_customers"] == []

        async with admin.connect() as conn:
            customers = set((await conn.execute(text("SELECT id FROM customers"))).scalars())
            assert {"cus_a", "cus_b", "cus_zs", "cus_ww", "cus_ls", "cus_sb"} <= customers

            # cus_a balance and ledger are byte-for-byte unchanged.
            row = (
                await conn.execute(
                    text(
                        "SELECT credits_posted_cents, debits_posted_cents, "
                        "debits_pending_cents, credits_pending_cents FROM accounts "
                        "WHERE id='acc_a'"
                    )
                )
            ).one()
            assert tuple(row) == tuple(p1_acc_a)
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM transfers "
                        "WHERE debit_account_id='acc_a' OR credit_account_id='acc_a'"
                    )
                )
                == p1_acc_a_transfers
            )

            # clock.sim_now / dataset_revision untouched.
            assert (await conn.scalar(text("SELECT sim_now FROM clock WHERE id=1"))) == p1_sim_now
            assert (
                await conn.scalar(text("SELECT dataset_revision FROM clock WHERE id=1"))
                == p1_dataset
            )

            counts_after_first = await _row_counts(conn)

        # Re-run is a no-op.
        async with admin.begin() as conn:
            second = await install_demo(conn, KEY)
        assert second["added_customers"] == 0
        assert set(second["skipped_customers"]) == {"cus_zs", "cus_ww", "cus_ls", "cus_sb"}

        async with admin.connect() as conn:
            assert await _row_counts(conn) == counts_after_first

            # Global ledger conservation.
            debits_posted = await conn.scalar(text("SELECT sum(debits_posted_cents) FROM accounts"))
            credits_posted = await conn.scalar(
                text("SELECT sum(credits_posted_cents) FROM accounts")
            )
            debits_pending = await conn.scalar(
                text("SELECT sum(debits_pending_cents) FROM accounts")
            )
            credits_pending = await conn.scalar(
                text("SELECT sum(credits_pending_cents) FROM accounts")
            )
            assert debits_posted == credits_posted
            assert (debits_pending or 0) == 0
            assert (credits_pending or 0) == 0

        # L3 get_accounts returns the demo seed's balance for cus_zs.
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                try:
                    response = await client.post(
                        "/internal/read/get_accounts",
                        json={"customer_id": "cus_zs", "args": {"history_days": 1}},
                        headers={"X-Service-Token": SERVICE_TOKEN},
                        timeout=2.0,
                    )
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("uvicorn listener never became ready")
        assert response.status_code == 200, response.text
        accounts = {a["id"]: a for a in response.json()["accounts"]}
        assert accounts["acc_zs_chk"]["balance_cents"] == 600000
        assert accounts["acc_zs_sav"]["balance_cents"] == 2000000

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_rerun_converges_payee_creation_times_without_touching_ledger(obligations_db):
    """An install made from the old seed (payees created at sim_now) is corrected on rerun."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await seed_core(conn, KEY)
            await install_demo(conn, KEY)
            # Simulate the earlier seed: payee created exactly at the simulated "now".
            await conn.execute(
                text(
                    "UPDATE payees SET created_at = (SELECT sim_now FROM clock) "
                    "WHERE id = 'pye_zs_ww'"
                )
            )
        async with admin.connect() as conn:
            before = await _row_counts(conn)
            ledger_before = await conn.scalar(
                text("SELECT sum(credits_posted_cents - debits_posted_cents) FROM accounts")
            )
        async with admin.begin() as conn:
            summary = await install_demo(conn, KEY)
        assert summary["added_customers"] == 0
        assert summary["synced_payees"] == 1
        async with admin.connect() as conn:
            created = await conn.scalar(
                text("SELECT created_at FROM payees WHERE id = 'pye_zs_ww'")
            )
            sim_now = await conn.scalar(text("SELECT sim_now FROM clock"))
            assert (sim_now - created).days > 100
            assert await _row_counts(conn) == before
            assert (
                await conn.scalar(
                    text("SELECT sum(credits_posted_cents - debits_posted_cents) FROM accounts")
                )
                == ledger_before
            )
        async with admin.begin() as conn:
            assert (await install_demo(conn, KEY))["synced_payees"] == 0
        await admin.dispose()

    asyncio.run(check())


def test_seed_demo_installs_products_idempotently(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True
            summary = await install_demo(conn, KEY)
        assert summary["added_products"] == 6

        async with admin.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, name, product_type, risk_level, risk_label, base_nav, "
                            "base_date, confirm_days, settle_days, min_amount_cents, "
                            "risk_disclosure, disclosure_version FROM products ORDER BY id"
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert [r["id"] for r in rows] == sorted(EXPECTED_PRODUCTS)
            for r in rows:
                exp = EXPECTED_PRODUCTS[r["id"]]
                assert r["name"] == exp["name"]
                assert r["product_type"] == exp["product_type"]
                assert r["risk_level"] == exp["risk_level"]
                assert r["risk_label"] == exp["risk_label"]
                assert r["confirm_days"] == exp["confirm_days"]
                assert r["settle_days"] == exp["settle_days"]
                assert r["min_amount_cents"] == exp["min_amount_cents"]
                assert r["base_nav"] == exp["base_nav"]
                assert r["base_date"].date() == date(2026, 10, 15)
                assert r["risk_disclosure"].strip()
                assert r["disclosure_version"] == "v1"

        # Second install is a no-op for the global product catalog.
        async with admin.begin() as conn:
            second = await install_demo(conn, KEY)
        assert second["added_products"] == 0

        # NAV projections via bankcore on the loaded product definitions.
        async with admin.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, name, product_type, risk_level, risk_label, base_nav, "
                            "base_date, confirm_days, settle_days, min_amount_cents, "
                            "risk_disclosure, disclosure_version FROM products ORDER BY id"
                        )
                    )
                )
                .mappings()
                .all()
            )
            defs = tuple(
                ProductDef(
                    id=r["id"],
                    name=r["name"],
                    product_type=r["product_type"],
                    risk_level=r["risk_level"],
                    risk_label=r["risk_label"],
                    base_nav=r["base_nav"],
                    base_date=r["base_date"].date(),
                    confirm_days=r["confirm_days"],
                    settle_days=r["settle_days"],
                    min_amount_cents=r["min_amount_cents"],
                    risk_disclosure=r["risk_disclosure"],
                    disclosure_version=r["disclosure_version"],
                )
                for r in rows
            )
            on_seed_day = get_product(defs, "prd_stable", date(2026, 10, 15))
            assert on_seed_day is not None and on_seed_day["nav"] == Decimal("1.2310")
            next_trading_day = get_product(defs, "prd_stable", date(2026, 10, 16))
            assert next_trading_day is not None and next_trading_day["nav"] == Decimal("1.2311")

        await admin.dispose()

    asyncio.run(check())
