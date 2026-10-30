"""Read-only export of exact synthetic prior consents for local demo reset.

This is a maintenance CLI, not an HTTP route. Only the host reset installer can
write the exported grants into its new gateway database. Existing/changed bank
data cannot acquire consent through this command.
"""

import asyncio
import hashlib
import json
import os

from safebank_bankcore.grants import instruction_scope, scope_hash, seed_consent
from sqlalchemy import select

from . import models as m
from .db import engine
from .seed_demo import SEED_PATH
from .seedlib import prepare_seed, read_seed


async def export(conn, fingerprint_key: str) -> list[dict]:
    prepared = prepare_seed(read_seed(SEED_PATH), fingerprint_key, include_obligations=True)
    clock = (await conn.execute(select(m.clock))).mappings().one()
    if clock["sim_now"] != prepared["sim_now"]:
        raise ValueError("seed consent export requires initial simulated time")
    billings = {b["id"]: b for b in prepared["billings"]}
    grants = []
    for expected in prepared["standing_instructions"]:
        actual = (
            (
                await conn.execute(
                    select(m.standing_instructions).where(
                        m.standing_instructions.c.id == expected["id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        # Compare every persisted field, including original execution cursor and
        # lifecycle status, before exporting the pre-existing synthetic consent.
        if any(actual[key] != value for key, value in expected.items()):
            raise ValueError("seed instruction differs from fixed initial manifest")
        billing = None
        if expected.get("billing_id"):
            billing = (
                (
                    await conn.execute(
                        select(m.merchant_billing).where(
                            m.merchant_billing.c.id == expected["billing_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            if any(billing[key] != value for key, value in billings[billing["id"]].items()):
                raise ValueError("seed billing differs from fixed initial manifest")
        scope = instruction_scope(actual, billing)
        consent = seed_consent(scope, expected["next_run_at"])
        if actual["grant_scope_hash"] != scope_hash(consent):
            raise ValueError("seed consent fingerprint differs")
        grants.append(
            dict(
                instruction_id=actual["id"],
                customer_id=actual["customer_id"],
                source_plan_id=actual["source_plan_id"],
                scope_json={
                    "seed_consent": consent,
                    "dataset_revision": prepared["dataset_revision"],
                    "manifest_sha256": hashlib.sha256(SEED_PATH.read_bytes()).hexdigest(),
                },
                scope_hash=actual["grant_scope_hash"],
                approved_risk="YELLOW",
                status="ACTIVE",
            )
        )
    return grants


async def main():
    db = engine(os.environ["BANK_DATABASE_URL"])
    try:
        async with db.connect() as conn:
            grants = await export(conn, os.environ["FINGERPRINT_KEY"])
        print(json.dumps(grants))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
