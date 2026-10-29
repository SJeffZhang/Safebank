"""E0b T1: load an evaluation seed JSON into the real bank backend (tmpfs PG).

Writes customers, state_versions, accounts (plus one internal clearing account
for the synthetic transaction transfers), cards, payees (recomputing
``target_fingerprint`` with the real gateway fingerprint algorithm), merchants
and the seed's transaction history. Every seed transaction gets a synthetic
POSTED ledger transfer so the ``transactions.transfer_id`` NOT NULL foreign key
is satisfied without running the ledger engine.

Validation and assembly live in ``app.seedlib`` (shared with the P2 demo
seeder); this module keeps only the write path and the public ``load_seed`` /
``read_seed`` / ``SeedError`` API used by the evaluation harness.

Fail-closed contract:

- every cross-reference, limit chain and balance is validated before anything
  is written;
- a non-empty database is never overwritten (reload raises ``SeedError``);
- billings / standing_instructions are outside T1 and are rejected loudly
  rather than silently dropped.
"""

from __future__ import annotations

from typing import Any

from app import models as m
from app.seedlib import SeedError, prepare_seed, read_seed
from sqlalchemy import select

# Backwards-compatible alias for anything that imported the private helper.
_prepare = prepare_seed

__all__ = ["SeedError", "read_seed", "load_seed"]


async def load_seed(conn, seed: dict[str, Any], *, fingerprint_key: str) -> None:
    """Validate ``seed`` and write it into ``conn`` in one transaction.

    Raises ``SeedError`` (leaving the database unchanged) when the seed is
    invalid or the database is not empty.
    """
    prepared = prepare_seed(seed, fingerprint_key)
    async with conn.begin():
        existing = await conn.scalar(select(m.customers.c.id).limit(1))
        if existing is not None:
            raise SeedError("database is not empty; refusing to overwrite existing data")
        for customer in prepared["customers"]:
            await conn.execute(m.customers.insert().values(**customer))
            await conn.execute(
                m.state_versions.insert().values(customer_id=customer["id"], version=0)
            )
        for account in prepared["accounts"]:
            await conn.execute(m.accounts.insert().values(**account))
        for card in prepared["cards"]:
            await conn.execute(m.cards.insert().values(**card))
        for payee in prepared["payees"]:
            await conn.execute(m.payees.insert().values(**payee))
        for merchant in prepared["merchants"]:
            await conn.execute(m.merchants.insert().values(**merchant))
        for transfer in prepared["transfers"]:
            await conn.execute(m.transfers.insert().values(**transfer))
        for txn in prepared["transactions"]:
            await conn.execute(m.transactions.insert().values(**txn))
        await conn.execute(
            m.clock.insert().values(
                id=1,
                sim_now=prepared["sim_now"],
                version=1,
                dataset_revision=prepared["dataset_revision"],
            )
        )
