"""Install the P2 demo-customer seed into a database that already holds P1 data.

Runs ``python -m app.seed_demo`` inside the ``bank-maintenance`` container (see
``make seed-demo``). Unlike ``app.bootstrap`` this is **incremental and
idempotent**:

- P1 customers (``cus_a`` / ``cus_b``) and their accounts, cards, payees and
  ledger history are never touched;
- ``clock.sim_now`` / ``dataset_revision`` are never touched;
- a demo customer that already exists is skipped entirely (nothing of that
  customer is inserted on a re-run), so a second run changes no row counts;
- the internal clearing account (``acc_external_clearing``, created empty by the
  P1 seeder) is *incremented* by the demo seed's synthetic IN/OUT transaction
  totals instead of being re-inserted;
- each new customer account's opening funding is debited against the P1 seed
  equity account (``acc_seed_equity``) through a ``POSTED`` funding transfer, so
  the demo install preserves the global ledger conservation invariant
  (``sum(debits_posted) == sum(credits_posted)``).

Validation and assembly are shared with the E0 loader via ``app.seedlib``
(``prepare_seed(..., include_obligations=True)``); this module only decides
*which* of the prepared rows are new and writes them on a caller-managed
transaction, mirroring ``app.seed.seed_core``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models as m
from .db import engine
from .seedlib import DEMO_PLAN_ID, INTERNAL_CLEARING_ID, prepare_seed, read_seed

SEED_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "l1_seed_v1.json"

# Created by the P1 seeder (``app.seed.seed_core``); the demo seed's opening
# funding reuses it as the debit side so the combined ledger stays balanced.
SEED_EQUITY_ID = "acc_seed_equity"


async def install_demo(conn, fingerprint_key: str) -> dict[str, Any]:
    """Validate the demo seed and insert only its *new* customers' data.

    Returns a summary of what was inserted/skipped; does not commit (the caller
    owns the transaction).
    """
    seed = read_seed(SEED_PATH)
    prepared = prepare_seed(seed, fingerprint_key, include_obligations=True)

    existing = set((await conn.execute(select(m.customers.c.id))).scalars())
    new_customer_ids = {c["id"] for c in prepared["customers"]} - existing
    skipped_customer_ids = {c["id"] for c in prepared["customers"]} & existing

    for customer in prepared["customers"]:
        if customer["id"] not in new_customer_ids:
            continue
        await conn.execute(m.customers.insert().values(**customer))
        await conn.execute(m.state_versions.insert().values(customer_id=customer["id"], version=0))

    new_accounts = [
        a
        for a in prepared["accounts"]
        if a["id"] != INTERNAL_CLEARING_ID and a["customer_id"] in new_customer_ids
    ]
    new_account_ids = {a["id"] for a in new_accounts}
    for account in new_accounts:
        await conn.execute(m.accounts.insert().values(**account))

    # Opening funding: the seed carries final ledger balances but no equity
    # account, so each new account's opening funding (final credits minus money
    # that flowed back *from* the clearing account) is debited against the P1
    # equity account and recorded as a POSTED funding transfer. This is the
    # only write that touches an existing (internal) account.
    in_total_by_account: dict[str, int] = {}
    for txn in prepared["transactions"]:
        if txn["direction"] == "IN":
            in_total_by_account[txn["account_id"]] = (
                in_total_by_account.get(txn["account_id"], 0) + txn["amount_cents"]
            )
    total_funding = 0
    funding_count = 0
    for account in new_accounts:
        funding = account["credits_posted_cents"] - in_total_by_account.get(account["id"], 0)
        if funding <= 0:
            continue
        total_funding += funding
        funding_count += 1
        await conn.execute(
            m.transfers.insert().values(
                id=f"trf_seed_fund_{account['id']}",
                debit_account_id=SEED_EQUITY_ID,
                credit_account_id=account["id"],
                amount_cents=funding,
                code="SEED",
                phase="POSTED",
                pending_id=None,
                reverses_id=None,
                plan_id=DEMO_PLAN_ID,
                event_id=None,
                step_id=f"fund_{account['id']}",
                leg="main",
                occurred_at=prepared["sim_now"],
                expires_at=None,
            )
        )
    if total_funding:
        await conn.execute(
            update(m.accounts)
            .where(m.accounts.c.id == SEED_EQUITY_ID)
            .values(debits_posted_cents=m.accounts.c.debits_posted_cents + total_funding)
        )

    new_cards = [c for c in prepared["cards"] if c["account_id"] in new_account_ids]
    for card in new_cards:
        await conn.execute(m.cards.insert().values(**card))

    new_payees = [p for p in prepared["payees"] if p["customer_id"] in new_customer_ids]
    for payee in new_payees:
        await conn.execute(m.payees.insert().values(**payee))

    # Merchants are global (not customer-scoped); re-runs must not duplicate them.
    added_merchants = 0
    for merchant in prepared["merchants"]:
        result = await conn.execute(
            pg_insert(m.merchants).values(**merchant).on_conflict_do_nothing()
        )
        added_merchants += result.rowcount or 0

    new_transactions = [t for t in prepared["transactions"] if t["customer_id"] in new_customer_ids]
    new_txn_ids = {t["id"] for t in new_transactions}
    new_transfers = [t for t in prepared["transfers"] if t["step_id"] in new_txn_ids]

    for transfer in new_transfers:
        await conn.execute(m.transfers.insert().values(**transfer))
    for txn in new_transactions:
        await conn.execute(m.transactions.insert().values(**txn))

    # The clearing account already exists (empty) from the P1 seeder: fold the
    # demo seed's synthetic flows into it instead of inserting a duplicate.
    in_delta = sum(t["amount_cents"] for t in new_transactions if t["direction"] == "IN")
    out_delta = sum(t["amount_cents"] for t in new_transactions if t["direction"] == "OUT")
    if in_delta or out_delta:
        await conn.execute(
            update(m.accounts)
            .where(m.accounts.c.id == INTERNAL_CLEARING_ID)
            .values(
                debits_posted_cents=m.accounts.c.debits_posted_cents + in_delta,
                credits_posted_cents=m.accounts.c.credits_posted_cents + out_delta,
            )
        )

    new_billings = [b for b in prepared["billings"] if b["customer_id"] in new_customer_ids]
    for billing in new_billings:
        await conn.execute(m.merchant_billing.insert().values(**billing))

    new_instructions = [
        s for s in prepared["standing_instructions"] if s["customer_id"] in new_customer_ids
    ]
    for instruction in new_instructions:
        await conn.execute(m.standing_instructions.insert().values(**instruction))

    return {
        "added_customers": len(new_customer_ids),
        "skipped_customers": sorted(skipped_customer_ids),
        "added_accounts": len(new_accounts),
        "added_cards": len(new_cards),
        "added_payees": len(new_payees),
        "added_merchants": added_merchants,
        "added_transactions": len(new_transactions),
        "added_billings": len(new_billings),
        "added_standing_instructions": len(new_instructions),
        "funding_transfers": funding_count,
        "funding_total_cents": total_funding,
        "clearing_debits_delta": in_delta,
        "clearing_credits_delta": out_delta,
    }


async def main() -> None:
    db = engine(os.environ["BANK_DATABASE_URL"])
    try:
        async with db.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(814001)"))
            summary = await install_demo(conn, os.environ["FINGERPRINT_KEY"])
    finally:
        await db.dispose()

    if summary["added_customers"]:
        print(
            f"Demo seed installed: {summary['added_customers']} customer(s), "
            f"{summary['added_accounts']} account(s), {summary['added_cards']} card(s), "
            f"{summary['added_payees']} payee(s), {summary['added_merchants']} merchant(s), "
            f"{summary['added_transactions']} transaction(s), "
            f"{summary['added_billings']} billing(s), "
            f"{summary['added_standing_instructions']} standing instruction(s); "
            f"funding {summary['funding_transfers']} transfer(s) totalling "
            f"{summary['funding_total_cents']} cents against {SEED_EQUITY_ID}; "
            f"clearing +{summary['clearing_debits_delta']}/"
            f"+{summary['clearing_credits_delta']} (debits/credits)"
        )
    else:
        skipped = ", ".join(summary["skipped_customers"]) or "none"
        print(f"Demo seed already installed (skipped customers: {skipped}); nothing changed")


if __name__ == "__main__":
    asyncio.run(main())
