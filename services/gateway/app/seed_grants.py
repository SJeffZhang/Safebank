"""Host-driven install of fixed synthetic prior consents into an empty demo DB.

No route invokes this CLI. No fake plan execution/OTP is manufactured. Normal
runtime grant recovery still requires a genuinely committed source plan.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from safebank_bankcore.grants import scope_hash
from sqlalchemy import func, select

from . import tables as t
from .audit.chain import append
from .db import engine
from .utterance.service import new_id


async def install(conn, grants: list[dict]):
    if not grants or len({g["instruction_id"] for g in grants}) != len(grants):
        raise ValueError("invalid seed consent manifest")
    # This is a new unpublished database; never overwrite an existing grant or
    # install new consent over user actions/history.
    for table in (t.scheduled_grants, t.sessions, t.plans, t.audit_events):
        if await conn.scalar(select(func.count()).select_from(table)):
            raise ValueError("seed consent install requires an empty gateway database")
    for grant in grants:
        consent = grant["scope_json"]["seed_consent"]
        scope = consent["terms"]
        if (
            grant["source_plan_id"] != "pln_demo_seed"
            or scope["source_plan_id"] != "pln_demo_seed"
            or scope["id"] != grant["instruction_id"]
            or scope["customer_id"] != grant["customer_id"]
            or scope_hash(consent) != grant["scope_hash"]
            or grant["approved_risk"] != "YELLOW"
            or grant["status"] != "ACTIVE"
        ):
            raise ValueError("seed consent binding differs")
        await conn.execute(
            t.scheduled_grants.insert().values(
                **grant, created_at=datetime.now(UTC), revoked_at=None
            )
        )
        await append(
            conn,
            customer_id=grant["customer_id"],
            event_type="SEED_CONSENT_INSTALL",
            event_id=new_id("evt"),
            payload={
                "instruction_id": grant["instruction_id"],
                "scope_hash": grant["scope_hash"],
                "source": "l1_seed_v1",
            },
        )


async def main():
    grants = json.load(sys.stdin)
    db = engine(os.environ["GATEWAY_DATABASE_URL"])
    try:
        async with db.begin() as conn:
            await install(conn, grants)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
