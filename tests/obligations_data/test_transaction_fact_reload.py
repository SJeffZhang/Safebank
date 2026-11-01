"""Coordinator-run PG: actual scheduled/merchant events persist facts used by fresh reads."""

import asyncio
import hashlib
from datetime import timedelta

import pytest
from app import models as m
from app.db import engine
from app.events import EventService
from app.state import load_bank_state
from safebank_bankcore import events as bank_events
from safebank_bankcore.banking import transfer_control_violation
from safebank_contracts.codes import Code
from safebank_contracts.state import StateScope
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import install_core, install_obligations
from tests.obligations_data.test_events_api import clean_database  # noqa: F401


@pytest.mark.parametrize("self_transfer", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_scheduled_actual_commit_fresh_counters_and_limits_remain_identical(
    obligations_db, self_transfer, legacy, monkeypatch
):
    if legacy:
        original_post = bank_events.post_transfer

        def historical_producer(state, cmd, now, **kwargs):
            # The old producer's initial append used one generic code. Keep the
            # real account identities, amount, posting and immutable SQL guards.
            old_command = cmd.model_copy(update={"code": "SCHEDULED_TRANSFER"})
            return original_post(state, old_command, now, **kwargs)

        monkeypatch.setattr(bank_events, "post_transfer", historical_producer)

    async def run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with admin.begin() as conn:
                await install_core(conn)
                await install_obligations(conn)
                row = (
                    (
                        await conn.execute(
                            select(m.standing_instructions).where(
                                m.standing_instructions.c.id == "si_zs_mom"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                due = row["next_run_at"]
                await conn.execute(m.clock.update().values(sim_now=due, version=1))
                if self_transfer:
                    await conn.execute(
                        m.standing_instructions.update()
                        .where(m.standing_instructions.c.id == "si_zs_mom")
                        .values(payee_id=None, to_account_id="acc_zs_sav")
                    )
                # A subsequent 1-cent payment must be blocked by today's usage.
                await conn.execute(
                    m.cards.update()
                    .where(m.cards.c.id == "crd_zs_1")
                    .values(
                        single_limit_cents=row["amount_cents"],
                        daily_limit_cents=row["amount_cents"],
                    )
                )
                raw = f"si_zs_mom|{row['amount_cents']}|acc_zs_chk"
                payload_hash = hashlib.sha256(raw.encode()).hexdigest()
                await conn.execute(
                    m.event_jobs.insert().values(
                        id="evt_fact_schedule",
                        kind="SCHEDULED_TRANSFER",
                        rank=3,
                        due_at=due,
                        immutable_payload={"payload": raw},
                        payload_hash=payload_hash,
                        source_id="si_zs_mom",
                        period_key=due.isoformat(),
                        status="PENDING",
                    )
                )
            async with runtime.begin() as conn:
                result = await EventService().execute_event(
                    conn,
                    "evt_fact_schedule",
                    {"cus_zs": 0},
                    1,
                    payload_hash,
                    {
                        "type": "SCHEDULED_GRANT",
                        "grant_id": "si_zs_mom",
                        "scope_hash": "0" * 64,
                        "source_plan_id": "pln_seed",
                        "approved_risk": "YELLOW",
                    },
                )
                assert result["status"] == "SUCCESS"
            async with runtime.connect() as conn:
                fresh = await load_bank_state(
                    conn,
                    StateScope(customer_ids=("cus_zs",)),
                    due + timedelta(minutes=1),
                    customer_id="cus_zs",
                )
            expected = 0 if self_transfer else row["amount_cents"]
            assert fresh.counters.daily_outflow_cents.get("cus_zs", 0) == expected
            assert fresh.counters.rolling_outflow_cents.get("cus_zs", 0) == expected
            assert fresh.counters.daily_used_cents.get("acc_zs_chk", 0) == expected
            violation = transfer_control_violation(
                fresh, source=fresh.accounts["acc_zs_chk"], amount_cents=1, customer_id="cus_zs"
            )
            assert violation is (None if self_transfer else Code.CARD_DAILY_LIMIT)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())


def test_actual_merchant_event_persists_authoritative_merchant_metadata(obligations_db):
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
                due = bill["next_due_at"]
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
                assert result["status"] == "SUCCESS"
            async with runtime.connect() as conn:
                fresh = await load_bank_state(
                    conn,
                    StateScope(customer_ids=("cus_zs",)),
                    due + timedelta(minutes=1),
                    customer_id="cus_zs",
                )
            txs = [t for t in fresh.transactions if t.kind == "MERCHANT_CHARGE"]
            assert len(txs) == 1 and txs[0].merchant_id == bill["merchant_id"]
            assert txs[0].category == "娱乐"
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())
