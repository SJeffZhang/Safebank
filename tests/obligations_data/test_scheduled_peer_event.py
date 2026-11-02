"""Real-PG scheduled OTHER transfers must fence and version both customers.

The coordinator runs these tests. This worker only collects them: no Docker/DB.
"""

import asyncio
from copy import deepcopy
from hashlib import sha256

import pytest
from app import models as m
from app.errors import BankError
from app.events import EventService
from safebank_contracts.codes import Code
from sqlalchemy import func, select

from tests.obligations_data.test_events_api import (
    SI_DUE,
    _admin_conn,
    _runtime_conn,
    _seed_event,
    _setup,
    clean_database,  # noqa: F401 -- imported autouse PostgreSQL isolation fixture
)

pytestmark = pytest.mark.integration
INSTRUCTION = "si_scheduled_peer_test"
EVENT = "evt_scheduled_peer_test"
AMOUNT = 100000


async def _prepare(db, *, target="OTHER", frozen=False):
    await _setup(db)
    admin = _admin_conn(db)
    async with admin.begin() as conn:
        # Prove these are actual fixture rows, never guessed IDs. A fixture
        # regression fails here before invoking any product implementation.
        payee = (
            (await conn.execute(select(m.payees).where(m.payees.c.id == "pye_zs_ww")))
            .mappings()
            .one()
        )
        peer = (
            (await conn.execute(select(m.accounts).where(m.accounts.c.id == "acc_ww_chk")))
            .mappings()
            .one()
        )
        assert payee["customer_id"] == "cus_zs" and payee["target_account_id"] == peer["id"]
        assert peer["customer_id"] == "cus_ww"
        original = (
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
        instruction = deepcopy(dict(original))
        instruction.update(id=INSTRUCTION, next_run_at=SI_DUE, amount_cents=AMOUNT)
        if target == "OTHER":
            instruction.update(payee_id="pye_zs_ww", to_account_id=None)
        elif target == "OWN":
            instruction.update(payee_id=None, to_account_id="acc_zs_sav")
        else:
            instruction.update(payee_id="pye_zs_mom", to_account_id=None)
        await conn.execute(m.standing_instructions.insert().values(**instruction))
        await conn.execute(m.clock.update().values(sim_now=SI_DUE, version=2))
        if frozen:
            await conn.execute(
                m.cards.update().where(m.cards.c.id == "crd_zs_1").values(status="FROZEN")
            )
        raw = f"{INSTRUCTION}|{AMOUNT}|acc_zs_chk"
        payload_hash = sha256(raw.encode()).hexdigest()
        await _seed_event(
            conn,
            event_id=EVENT,
            kind="SCHEDULED_TRANSFER",
            source_id=INSTRUCTION,
            due_at=SI_DUE,
            payload_hash=payload_hash,
            immutable_payload={"payload": raw},
            period_key=SI_DUE.isoformat(),
        )
    await admin.dispose()
    return payload_hash


async def _versions(conn):
    return dict(
        (
            await conn.execute(
                select(m.state_versions.c.customer_id, m.state_versions.c.version).where(
                    m.state_versions.c.customer_id.in_(("cus_zs", "cus_ww"))
                )
            )
        ).all()
    )


async def _balance(conn, account_id):
    return await conn.scalar(
        select(m.accounts.c.credits_posted_cents - m.accounts.c.debits_posted_cents).where(
            m.accounts.c.id == account_id
        )
    )


async def _execute(conn, payload_hash, expected_versions):
    return await EventService().execute_event(
        conn,
        EVENT,
        expected_versions=expected_versions,
        clock_version=2,
        expected_payload_hash=payload_hash,
        authorization_evidence={
            "type": "SCHEDULED_GRANT",
            "grant_id": INSTRUCTION,
            "scope_hash": "0" * 64,
            "source_plan_id": "pln_seed",
            "approved_risk": "YELLOW",
        },
    )


def test_other_scheduled_transfer_posts_both_customer_views_and_versions_once(obligations_db):
    async def run():
        payload_hash = await _prepare(obligations_db)
        runtime = _runtime_conn(obligations_db)
        try:
            async with runtime.begin() as conn:
                result = await _execute(conn, payload_hash, {"cus_zs": 0, "cus_ww": 0})
            assert result["status"] == "SUCCESS", result
            transfer_id = result["result"]["transfer_id"]
            async with runtime.connect() as conn:
                assert await _balance(conn, "acc_zs_chk") == 500000
                assert await _balance(conn, "acc_ww_chk") == 300000
                assert await _versions(conn) == {"cus_zs": 1, "cus_ww": 1}
                transfer = (
                    (await conn.execute(select(m.transfers).where(m.transfers.c.id == transfer_id)))
                    .mappings()
                    .one()
                )
                assert (
                    transfer["debit_account_id"],
                    transfer["credit_account_id"],
                    transfer["amount_cents"],
                ) == ("acc_zs_chk", "acc_ww_chk", 100000)
                views = (
                    await conn.execute(
                        select(
                            m.transactions.c.customer_id,
                            m.transactions.c.account_id,
                            m.transactions.c.direction,
                            m.transactions.c.amount_cents,
                            m.transactions.c.balance_after_cents,
                        ).where(m.transactions.c.transfer_id == transfer_id)
                    )
                ).all()
                assert set(views) == {
                    ("cus_zs", "acc_zs_chk", "OUT", 100000, 500000),
                    ("cus_ww", "acc_ww_chk", "IN", 100000, 300000),
                }
            async with runtime.begin() as conn:
                replay = await _execute(conn, payload_hash, {"cus_zs": 0, "cus_ww": 0})
            assert replay["replayed"] is True and replay["status"] == "SUCCESS"
            async with runtime.connect() as conn:
                assert await _versions(conn) == {"cus_zs": 1, "cus_ww": 1}
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.transfers)
                        .where(m.transfers.c.id == transfer_id)
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.instruction_runs)
                        .where(m.instruction_runs.c.event_id == EVENT)
                    )
                    == 1
                )
        finally:
            await runtime.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "expected", [{"cus_zs": 0, "cus_ww": 1}, {"cus_zs": 0}], ids=["stale-peer", "missing-peer"]
)
def test_other_scheduled_transfer_stale_or_missing_peer_version_rolls_back_everything(
    obligations_db, expected
):
    async def run():
        payload_hash = await _prepare(obligations_db)
        runtime = _runtime_conn(obligations_db)
        try:
            with pytest.raises(BankError) as caught:
                async with runtime.begin() as conn:
                    await _execute(conn, payload_hash, expected)
            assert caught.value.code is Code.STATE_CHANGED
            async with runtime.connect() as conn:
                assert await _versions(conn) == {"cus_zs": 0, "cus_ww": 0}
                assert (
                    await _balance(conn, "acc_zs_chk") == 600000
                    and await _balance(conn, "acc_ww_chk") == 200000
                )
                assert (
                    await conn.scalar(
                        select(m.standing_instructions.c.next_run_at).where(
                            m.standing_instructions.c.id == INSTRUCTION
                        )
                    )
                    == SI_DUE
                )
                assert (
                    await conn.scalar(
                        select(m.event_jobs.c.status).where(m.event_jobs.c.id == EVENT)
                    )
                    == "PENDING"
                )
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.event_executions)
                        .where(m.event_executions.c.event_id == EVENT)
                    )
                    == 0
                )
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.instruction_runs)
                        .where(m.instruction_runs.c.event_id == EVENT)
                    )
                    == 0
                )
        finally:
            await runtime.dispose()

    asyncio.run(run())


def test_other_blocked_run_only_changes_source_cursor_and_source_version(obligations_db):
    async def run():
        payload_hash = await _prepare(obligations_db, frozen=True)
        runtime = _runtime_conn(obligations_db)
        try:
            async with runtime.begin() as conn:
                result = await _execute(conn, payload_hash, {"cus_zs": 0, "cus_ww": 0})
            assert result["status"] == "BLOCKED"
            async with runtime.connect() as conn:
                assert await _versions(conn) == {"cus_zs": 1, "cus_ww": 0}
                assert (
                    await _balance(conn, "acc_zs_chk") == 600000
                    and await _balance(conn, "acc_ww_chk") == 200000
                )
                assert (
                    await conn.scalar(
                        select(m.standing_instructions.c.next_run_at).where(
                            m.standing_instructions.c.id == INSTRUCTION
                        )
                    )
                    > SI_DUE
                )
        finally:
            await runtime.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "target,account,expected_balance",
    [("OWN", "acc_zs_sav", 2100000), ("EXTERNAL", "acc_external_clearing", 100000)],
)
def test_own_and_external_scheduled_transfer_keep_single_customer_fence(
    obligations_db, target, account, expected_balance
):
    async def run():
        payload_hash = await _prepare(obligations_db, target=target)
        runtime = _runtime_conn(obligations_db)
        try:
            async with runtime.begin() as conn:
                result = await _execute(conn, payload_hash, {"cus_zs": 0})
            assert result["status"] == "SUCCESS", result
            async with runtime.connect() as conn:
                assert await _versions(conn) == {"cus_zs": 1, "cus_ww": 0}
                assert await _balance(conn, "acc_zs_chk") == 500000
                assert await _balance(conn, account) == expected_balance
        finally:
            await runtime.dispose()

    asyncio.run(run())
