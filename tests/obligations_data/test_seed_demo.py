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
    "holdings",
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


def _read(client, tool, args, *, customer_id="cus_zs", auth=SERVICE_TOKEN, timeout=10.0):
    return client.post(
        f"/internal/read/{tool}",
        json={"args": args, "customer_id": customer_id},
        headers={"X-Service-Token": auth},
        timeout=timeout,
    )


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

        # Fund settlement accounts exist for every seeded product.
        async with admin.connect() as conn:
            for product_id in EXPECTED_PRODUCTS:
                settlement_id = f"acc_fund_{product_id}"
                row = (
                    await conn.execute(
                        text(
                            "SELECT id, customer_id, type, single_limit_cents, "
                            "daily_limit_cents FROM accounts WHERE id=:id"
                        ),
                        {"id": settlement_id},
                    )
                ).one_or_none()
                assert row is not None, f"missing settlement account {settlement_id}"
                assert row.customer_id is None
                assert row.type == "INTERNAL"
                assert row.single_limit_cents == 500_000
                assert row.daily_limit_cents == 2_000_000

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


def test_seed_demo_installs_merchant_mandates(obligations_db):
    """张三's four billings get MERCHANT_MANDATE instructions; 李四 gets two billings+mandates."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True
            await install_demo(conn, KEY)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                try:
                    response = await _read(
                        client, "list_subscriptions", {"status": "ACTIVE", "limit": 50}
                    )
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("uvicorn listener never became ready")

            assert response.status_code == 200, response.text
            body = response.json()
            items = body["items"]
            assert len(items) == 4
            assert all(i["status"] == "ACTIVE" for i in items)
            assert body["total_annual_cents"] == 318000
            assert {i["billing_id"] for i in items} == {
                "blg_video",
                "blg_music",
                "blg_cloud",
                "blg_gym",
            }
            assert {i["account_id"] for i in items} == {"acc_zs_chk"}

            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": "acc_zs_chk"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["charges_total_cents"] == 4500
            assert {i["billing_id"] for i in body["items"]} == {"blg_video", "blg_music"}

            r = await _read(
                client,
                "list_subscriptions",
                {"status": "ACTIVE", "limit": 50},
                customer_id="cus_ls",
            )
            assert r.status_code == 200, r.text
            ls = r.json()["items"]
            assert len(ls) == 2
            assert {i["billing_id"] for i in ls} == {"blg_ls_video", "blg_ls_music"}
            assert {i["account_id"] for i in ls} == {"acc_ls_chk"}

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_seed_demo_backfills_mandates_for_existing_customers(obligations_db):
    """Rerunning the seed over customers missing mandates/billings re-adds only those rows."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True
            await install_demo(conn, KEY)

        async with admin.connect() as conn:
            balances_before = (
                await conn.execute(
                    text(
                        "SELECT sum(credits_posted_cents), sum(debits_posted_cents), "
                        "sum(credits_pending_cents), sum(debits_pending_cents) FROM accounts"
                    )
                )
            ).one()
            txns_before = await conn.scalar(text("SELECT count(*) FROM transactions"))
            transfers_before = await conn.scalar(text("SELECT count(*) FROM transfers"))

        # Simulate an older seed: 张三 has billings but no mandates; 李四 has neither.
        async with admin.begin() as conn:
            await conn.execute(
                text("DELETE FROM standing_instructions WHERE kind = 'MERCHANT_MANDATE'")
            )
            await conn.execute(
                text("DELETE FROM merchant_billing WHERE id IN ('blg_ls_video', 'blg_ls_music')")
            )

        async with admin.begin() as conn:
            summary = await install_demo(conn, KEY)
        assert summary["added_customers"] == 0
        assert summary["backfilled_mandates"] == 6
        assert summary["backfilled_billings"] == 2

        async with admin.connect() as conn:
            mandate_ids = set(
                (
                    await conn.execute(
                        text("SELECT id FROM standing_instructions WHERE kind = 'MERCHANT_MANDATE'")
                    )
                ).scalars()
            )
            assert mandate_ids == {
                "si_zs_video",
                "si_zs_music",
                "si_zs_cloud",
                "si_zs_gym",
                "si_ls_video",
                "si_ls_music",
            }
            ls_billings = set(
                (
                    await conn.execute(
                        text("SELECT id FROM merchant_billing WHERE customer_id = 'cus_ls'")
                    )
                ).scalars()
            )
            assert ls_billings == {"blg_ls_video", "blg_ls_music"}

            balances_after = (
                await conn.execute(
                    text(
                        "SELECT sum(credits_posted_cents), sum(debits_posted_cents), "
                        "sum(credits_pending_cents), sum(debits_pending_cents) FROM accounts"
                    )
                )
            ).one()
            assert tuple(balances_after) == tuple(balances_before)
            assert await conn.scalar(text("SELECT count(*) FROM transactions")) == txns_before
            assert await conn.scalar(text("SELECT count(*) FROM transfers")) == transfers_before

        await admin.dispose()

    asyncio.run(check())


def test_seed_demo_installs_lisi_holding_and_risk_dates(obligations_db):
    """李四's 安心货币 holding and PRD risk-assessment dates land on first install."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True
            summary = await install_demo(conn, KEY)
        assert summary["added_holdings"] == 1

        async with admin.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT customer_id, account_id, product_id, units, reserved_units, "
                            "cost_cents, reserved_cost_cents FROM holdings"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert row["customer_id"] == "cus_ls"
            assert row["account_id"] == "acc_ls_chk"
            assert row["product_id"] == "prd_mmf"
            assert row["units"] == Decimal("10000.0000")
            assert row["reserved_units"] == Decimal("0.0000")
            assert row["cost_cents"] == 1000000
            assert row["reserved_cost_cents"] == 0

            ls = (
                (
                    await conn.execute(
                        text(
                            "SELECT risk_level, risk_assessed_at, risk_valid_until "
                            "FROM customers WHERE id='cus_ls'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert ls["risk_level"] == 3
            assert ls["risk_assessed_at"].date() == date(2026, 3, 1)
            assert ls["risk_valid_until"] == "2027-03-01T10:00:00+08:00"
            sb = (
                (
                    await conn.execute(
                        text(
                            "SELECT risk_level, risk_assessed_at, risk_valid_until "
                            "FROM customers WHERE id='cus_sb'"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert sb["risk_level"] == 2
            assert sb["risk_assessed_at"].date() == date(2026, 5, 1)
            assert sb["risk_valid_until"] == "2027-05-01T10:00:00+08:00"

            counts_after_first = await _row_counts(conn)

        async with admin.begin() as conn:
            second = await install_demo(conn, KEY)
        assert second["added_holdings"] == 0
        assert second["backfilled_holdings"] == 0
        assert second["synced_risk_assessments"] == 0
        async with admin.connect() as conn:
            assert await _row_counts(conn) == counts_after_first

        await admin.dispose()

    asyncio.run(check())


def test_seed_demo_backfills_holding_and_syncs_risk_dates_for_existing_customers(obligations_db):
    """Rerunning the seed restores a missing holding and converges risk dates."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)

        async with admin.begin() as conn:
            assert await seed_core(conn, KEY) is True
            await install_demo(conn, KEY)

        async with admin.connect() as conn:
            balances_before = (
                await conn.execute(
                    text(
                        "SELECT sum(credits_posted_cents), sum(debits_posted_cents), "
                        "sum(credits_pending_cents), sum(debits_pending_cents) FROM accounts"
                    )
                )
            ).one()
            txns_before = await conn.scalar(text("SELECT count(*) FROM transactions"))
            transfers_before = await conn.scalar(text("SELECT count(*) FROM transfers"))

        # Simulate an older seed: no 李四 holding, stale risk dates.
        async with admin.begin() as conn:
            await conn.execute(text("DELETE FROM holdings"))
            await conn.execute(
                text(
                    "UPDATE customers SET risk_assessed_at='2026-09-01T10:00:00+08:00', "
                    "risk_valid_until='2027-09-01T10:00:00+08:00' "
                    "WHERE id IN ('cus_ls', 'cus_sb')"
                )
            )

        async with admin.begin() as conn:
            summary = await install_demo(conn, KEY)
        assert summary["added_customers"] == 0
        assert summary["backfilled_holdings"] == 1
        assert summary["synced_risk_assessments"] == 2

        async with admin.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT customer_id, product_id, units, cost_cents FROM holdings")
                    )
                )
                .mappings()
                .one()
            )
            assert (row["customer_id"], row["product_id"]) == ("cus_ls", "prd_mmf")
            assert row["units"] == Decimal("10000.0000")
            assert row["cost_cents"] == 1000000
            assert (
                await conn.scalar(text("SELECT risk_valid_until FROM customers WHERE id='cus_ls'"))
                == "2027-03-01T10:00:00+08:00"
            )
            assert (
                await conn.scalar(text("SELECT risk_valid_until FROM customers WHERE id='cus_sb'"))
                == "2027-05-01T10:00:00+08:00"
            )

            balances_after = (
                await conn.execute(
                    text(
                        "SELECT sum(credits_posted_cents), sum(debits_posted_cents), "
                        "sum(credits_pending_cents), sum(debits_pending_cents) FROM accounts"
                    )
                )
            ).one()
            assert tuple(balances_after) == tuple(balances_before)
            assert await conn.scalar(text("SELECT count(*) FROM transactions")) == txns_before
            assert await conn.scalar(text("SELECT count(*) FROM transfers")) == transfers_before

        async with admin.begin() as conn:
            third = await install_demo(conn, KEY)
        assert third["backfilled_holdings"] == 0
        assert third["synced_risk_assessments"] == 0

        await admin.dispose()

    asyncio.run(check())


def test_seed_consent_export_is_exact_and_rejects_changed_terms(obligations_db):
    from app import models as m
    from app.seed_consents import export
    from safebank_bankcore.grants import scope_hash

    async def run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await seed_core(conn, KEY)
            await install_demo(conn, KEY)
            consents = await export(conn, KEY)
            assert len(consents) == 7
            assert len({g["scope_hash"] for g in consents}) == len(consents)
            for grant in consents:
                assert grant["scope_hash"] == scope_hash(grant["scope_json"]["seed_consent"])
            await conn.execute(
                m.standing_instructions.update()
                .where(m.standing_instructions.c.id == "si_zs_mom")
                .values(amount_cents=100001)
            )
            with pytest.raises(ValueError, match="differs"):
                await export(conn, KEY)
        await admin.dispose()

    asyncio.run(run())
