"""BankSense showcase additions, installed only while preparing a new demo DB.

The canonical PRD seed stays unchanged. Existing customer balances, history,
subscription consents and card security rules are not replaced by this profile.
"""

import asyncio
import json
import os

from sqlalchemy import select, update

from . import models as m
from .db import engine
from .seed_demo import SEED_PATH
from .seedlib import prepare_seed, read_seed

PROFILE = "banksense_showcase_v1"
ACCOUNT_ID = "acc_zs_demo_reserve"
CARD_ROWS = (("crd_zs_2", "acc_zs_sav", "5678"), ("crd_zs_3", ACCOUNT_ID, "9012"))
COUNTERS = (
    "debits_posted_cents",
    "credits_posted_cents",
    "debits_pending_cents",
    "credits_pending_cents",
)


async def install_showcase(conn, fingerprint_key: str) -> dict:
    prepared = prepare_seed(read_seed(SEED_PATH), fingerprint_key, include_obligations=True)
    clock = (
        (await conn.execute(select(m.clock).where(m.clock.c.id == 1).with_for_update(read=True)))
        .mappings()
        .one()
    )
    if clock["sim_now"] != prepared["sim_now"]:
        raise ValueError("showcase requires the initial simulated time")
    await conn.execute(
        select(m.state_versions).where(m.state_versions.c.customer_id == "cus_zs").with_for_update()
    )
    accounts = {
        r["id"]: dict(r)
        for r in (
            await conn.execute(select(m.accounts).where(m.accounts.c.customer_id == "cus_zs"))
        ).mappings()
    }
    for expected in prepared["accounts"]:
        if expected["customer_id"] == "cus_zs":
            actual = accounts.get(expected["id"])
            if actual is None or any(actual[k] != expected[k] for k in COUNTERS):
                raise ValueError("showcase will not overwrite a used customer account")
    original = (
        (await conn.execute(select(m.cards).where(m.cards.c.id == "crd_zs_1"))).mappings().one()
    )
    expected_card = next(c for c in prepared["cards"] if c["id"] == "crd_zs_1")
    if any(original[k] != v for k, v in expected_card.items()):
        raise ValueError("showcase will not replace a modified card")
    account = {
        **accounts["acc_zs_chk"],
        "id": ACCOUNT_ID,
        "display_name": "备用活期",
        "account_no": "6222020000009012",
        "current_card_id": "crd_zs_3",
    }
    account.update({key: 0 for key in COUNTERS})
    added = 0
    pointer_changed = False
    current = (
        (await conn.execute(select(m.accounts).where(m.accounts.c.id == ACCOUNT_ID)))
        .mappings()
        .one_or_none()
    )
    if current is None:
        await conn.execute(m.accounts.insert().values(**account))
        added += 1
    elif dict(current) != account:
        raise ValueError("showcase account already exists with different data")
    for card_id, account_id, last4 in CARD_ROWS:
        card = {**dict(original), "id": card_id, "account_id": account_id, "last4": last4}
        actual = (
            (await conn.execute(select(m.cards).where(m.cards.c.id == card_id)))
            .mappings()
            .one_or_none()
        )
        if actual is None:
            await conn.execute(m.cards.insert().values(**card))
            added += 1
        elif dict(actual) != card:
            raise ValueError("showcase will not replace an existing or cancelled card")
        pointer = await conn.scalar(
            select(m.accounts.c.current_card_id).where(m.accounts.c.id == account_id)
        )
        if pointer not in (None, card_id):
            raise ValueError("showcase will not replace an account's current card")
        if pointer is None:
            pointer_changed = True
            await conn.execute(
                update(m.accounts)
                .where(m.accounts.c.id == account_id)
                .values(current_card_id=card_id)
            )
    if added or pointer_changed:
        await conn.execute(
            update(m.state_versions)
            .where(m.state_versions.c.customer_id == "cus_zs")
            .values(version=m.state_versions.c.version + 1)
        )
    return {"profile": PROFILE, "added_rows": added}


async def main():
    db = engine(os.environ["BANK_MIGRATION_DATABASE_URL"])
    try:
        async with db.begin() as conn:
            print(json.dumps(await install_showcase(conn, os.environ["FINGERPRINT_KEY"])))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
