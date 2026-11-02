"""Real fresh PostgreSQL checks for the additive BankSense demo profile."""

import asyncio

import pytest
from app import models as m
from app.db import engine
from app.reads import read
from app.seed import seed_core
from app.seed_consents import export
from app.seed_demo import install_demo
from app.seed_showcase import install_showcase
from app.state import load_bank_state, resolve_scope
from safebank_contracts.tools import READ_ARGS
from sqlalchemy import func, select, text, update
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, NOW


@pytest.fixture(autouse=True)
def empty_isolated_bank(obligations_db):
    async def clean():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await conn.execute(text(f"TRUNCATE {','.join(m.metadata.tables)} CASCADE"))
        await db.dispose()

    asyncio.run(clean())


async def setup(conn):
    await seed_core(conn, KEY)
    await install_demo(conn, KEY)


def test_showcase_initializes_cards_products_subscriptions_and_preserves_consent(obligations_db):
    async def run():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await setup(conn)
            before = (await conn.execute(select(m.accounts))).mappings().all()
            transfers = await conn.scalar(select(func.count()).select_from(m.transfers))
            first = await install_showcase(conn, KEY)
            assert first["added_rows"] == 3
            assert (await install_showcase(conn, KEY))["added_rows"] == 0
            accounts = {
                r["id"]: dict(r) for r in (await conn.execute(select(m.accounts))).mappings()
            }
            for old in before:
                for key in (
                    "debits_posted_cents",
                    "credits_posted_cents",
                    "debits_pending_cents",
                    "credits_pending_cents",
                ):
                    assert accounts[old["id"]][key] == old[key]
            assert all(
                accounts["acc_zs_demo_reserve"][k] == 0
                for k in (
                    "debits_posted_cents",
                    "credits_posted_cents",
                    "debits_pending_cents",
                    "credits_pending_cents",
                )
            )
            cards = (
                await conn.execute(
                    select(m.cards.c.last4, m.cards.c.status)
                    .join(m.accounts)
                    .where(m.accounts.c.customer_id == "cus_zs")
                    .order_by(m.cards.c.id)
                )
            ).all()
            assert cards == [("1234", "ACTIVE"), ("5678", "ACTIVE"), ("9012", "ACTIVE")]
            assert await conn.scalar(select(func.count()).select_from(m.products)) == 6
            mandates = (
                (
                    await conn.execute(
                        select(m.standing_instructions).where(
                            m.standing_instructions.c.customer_id == "cus_zs",
                            m.standing_instructions.c.kind == "MERCHANT_MANDATE",
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert len(mandates) == 4 and all(row["status"] == "ACTIVE" for row in mandates)
            assert transfers == await conn.scalar(select(func.count()).select_from(m.transfers))
            scope = await resolve_scope(conn, "cus_zs", [], KEY)
            state = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
            subscriptions = read(
                state,
                "list_subscriptions",
                READ_ARGS["list_subscriptions"](status="ALL", cursor=None, limit=100),
                "cus_zs",
            )
            upcoming = read(
                state,
                "upcoming_charges",
                READ_ARGS["upcoming_charges"](days=7, account_id=None),
                "cus_zs",
            )
            assert subscriptions.total_monthly_cents == 26500
            assert subscriptions.total_annual_cents == 318000
            assert upcoming.reserve_total_cents == 4500
            assert len(upcoming.items) == 2
            grants = await export(conn, KEY)
            assert {row["id"] for row in mandates} <= {g["instruction_id"] for g in grants}
            assert all(g["scope_json"]["seed_consent"]["kind"] == "SEED_CONSENT" for g in grants)
        await db.dispose()

    asyncio.run(run())


def test_showcase_refuses_used_balances_and_rolls_back(obligations_db):
    async def run():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await setup(conn)
            # Both postings move together; the global debit/credit invariant stays balanced.
            await conn.execute(
                update(m.accounts)
                .where(m.accounts.c.id == "acc_zs_chk")
                .values(
                    credits_posted_cents=m.accounts.c.credits_posted_cents + 1,
                    debits_posted_cents=m.accounts.c.debits_posted_cents + 1,
                )
            )
        with pytest.raises(ValueError, match="used customer account"):
            async with db.begin() as conn:
                await install_showcase(conn, KEY)
        async with db.connect() as conn:
            assert (
                await conn.scalar(
                    select(m.accounts.c.id).where(m.accounts.c.id == "acc_zs_demo_reserve")
                )
                is None
            )
        await db.dispose()

    asyncio.run(run())


def test_showcase_never_unfreezes_an_existing_card(obligations_db):
    async def run():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await setup(conn)
            await install_showcase(conn, KEY)
            await conn.execute(
                update(m.cards).where(m.cards.c.id == "crd_zs_2").values(status="FROZEN")
            )
        with pytest.raises(ValueError, match="existing or cancelled card"):
            async with db.begin() as conn:
                await install_showcase(conn, KEY)
        async with db.connect() as conn:
            assert (
                await conn.scalar(select(m.cards.c.status).where(m.cards.c.id == "crd_zs_2"))
                == "FROZEN"
            )
        await db.dispose()

    asyncio.run(run())


def test_showcase_pointer_repair_also_invalidates_customer_version(obligations_db):
    async def run():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.begin() as conn:
            await setup(conn)
            await install_showcase(conn, KEY)
            await conn.execute(
                update(m.accounts)
                .where(m.accounts.c.id == "acc_zs_sav")
                .values(current_card_id=None)
            )
            version = await conn.scalar(
                select(m.state_versions.c.version).where(m.state_versions.c.customer_id == "cus_zs")
            )
            result = await install_showcase(conn, KEY)
            assert result["added_rows"] == 0
            assert (
                await conn.scalar(
                    select(m.accounts.c.current_card_id).where(m.accounts.c.id == "acc_zs_sav")
                )
                == "crd_zs_2"
            )
            assert (
                await conn.scalar(
                    select(m.state_versions.c.version).where(
                        m.state_versions.c.customer_id == "cus_zs"
                    )
                )
                == version + 1
            )
        await db.dispose()

    asyncio.run(run())
