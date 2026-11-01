"""Real PG: expired merchant authorization is a terminal refusal without a debit."""

import asyncio
import hashlib
from datetime import timedelta

from app import models as m
from app.db import engine
from app.events import EventService
from app.state import load_bank_state
from safebank_contracts.state import StateScope
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import install_core, install_obligations
from tests.obligations_data.test_events_api import clean_database  # noqa: F401


def test_expired_merchant_mandate_cannot_move_money_and_terminal_replay_is_stable(obligations_db):
    async def run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with admin.begin() as conn:
                await install_core(conn)
                await install_obligations(conn)
                bill = (
                    (
                        await conn.execute(
                            select(m.merchant_billing).where(
                                m.merchant_billing.c.id == "bill_zs_video"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                before_transfers = set((await conn.execute(select(m.transfers.c.id))).scalars())
                due = bill["next_due_at"]
                await conn.execute(
                    m.standing_instructions.update()
                    .where(m.standing_instructions.c.id == "si_zs_video")
                    .values(end_at=due - timedelta(seconds=1))
                )
                await conn.execute(m.clock.update().values(sim_now=due, version=1))
                raw = f"{bill['id']}|{bill['amount_cents']}|{bill['account_id']}"
                payload_hash = hashlib.sha256(raw.encode()).hexdigest()
                await conn.execute(
                    m.event_jobs.insert().values(
                        id="evt_fact_merchant",
                        kind="MERCHANT_CHARGE",
                        rank=4,
                        due_at=due,
                        immutable_payload={"payload": raw},
                        payload_hash=payload_hash,
                        source_id=bill["id"],
                        period_key=due.isoformat(),
                        status="PENDING",
                    )
                )
            async with runtime.begin() as conn:
                result = await EventService().execute_event(
                    conn,
                    "evt_fact_merchant",
                    {"cus_zs": 0},
                    1,
                    payload_hash,
                    {
                        "type": "MERCHANT_MANDATE",
                        "instruction_id": "si_zs_video",
                        "billing_id": bill["id"],
                        "grant_id": "si_zs_video",
                    },
                )
                assert result["status"] == "BLOCKED"
                assert result["result"]["code"] == "MANDATE_NOT_ACTIVE"
                replay = await EventService().execute_event(
                    conn, "evt_fact_merchant", {}, 0, "old", {}
                )
                assert replay["replayed"] and replay["status"] == "BLOCKED"
            async with runtime.connect() as conn:
                after_transfers = set((await conn.execute(select(m.transfers.c.id))).scalars())
                fresh = await load_bank_state(
                    conn,
                    StateScope(customer_ids=("cus_zs",)),
                    due + timedelta(minutes=1),
                    customer_id="cus_zs",
                )
            txs = [t for t in fresh.transactions if t.kind == "MERCHANT_CHARGE"]
            assert not txs and after_transfers == before_transfers
            assert fresh.accounts["acc_zs_chk"].available_cents == 600000
            assert fresh.obligations.billings[bill["id"]].next_due_at > due
            assert fresh.versions["cus_zs"] == 1
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())
