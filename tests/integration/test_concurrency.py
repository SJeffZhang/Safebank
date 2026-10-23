import asyncio
import os

import pytest
from app import models as m
from app.errors import BankError
from app.execution import ExecutionService
from app.seed import NOW
from app.state import fingerprint, load_bank_state, resolve_scope
from safebank_contracts.state import Payee
from sqlalchemy import func, select

from tests.contracts.samples import step
from tests.integration.helpers import database
from tests.integration.test_execution import commands

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


async def with_current_versions(db, svc, p, e):
    for attempt in range(1, 31):
        try:
            await svc.prepare(p)
            return await svc.execute(e)
        except BankError as exc:
            if exc.code != "STATE_CHANGED":
                raise
            async with db.connect() as conn:
                scope = await resolve_scope(
                    conn, p.customer_id, p.steps, os.environ["FINGERPRINT_KEY"]
                )
                state = await load_bank_state(conn, scope, NOW, customer_id=p.customer_id)
            evidence = p.authorization_evidence.model_copy(
                update={"evaluation_id": f"evl_{p.plan_id}_{attempt}"}
            )
            p = p.model_copy(update={"attempt": p.attempt + 1, "authorization_evidence": evidence})
            e = e.model_copy(
                update={
                    "attempt": p.attempt,
                    "authorization_evidence": evidence,
                    "expected_versions": dict(state.versions),
                }
            )
    raise AssertionError("exhausted test-driver version retries")


def test_twenty_distinct_concurrent_transfers_and_global_invariants():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            requests = [await commands(db, plan_id=f"pln_{i}") for i in range(20)]
            results = await asyncio.gather(
                *(with_current_versions(db, svc, p, e) for p, e in requests)
            )
            assert all(r.status == "EXECUTED" for r in results)
            async with db.connect() as conn:
                a = (
                    (await conn.execute(select(m.accounts).where(m.accounts.c.id == "acc_a")))
                    .mappings()
                    .one()
                )
                b = (
                    (await conn.execute(select(m.accounts).where(m.accounts.c.id == "acc_b")))
                    .mappings()
                    .one()
                )
                assert a["debits_posted_cents"] == 2000 and b["credits_posted_cents"] == 202000
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 20
                assert (
                    await conn.scalar(
                        select(
                            func.sum(m.accounts.c.debits_posted_cents)
                            - func.sum(m.accounts.c.credits_posted_cents)
                        )
                    )
                    == 0
                )
                assert (
                    await conn.scalar(
                        select(
                            func.sum(m.accounts.c.debits_pending_cents)
                            - func.sum(m.accounts.c.credits_pending_cents)
                        )
                    )
                    == 0
                )
                assert (
                    await conn.scalar(
                        select(m.state_versions.c.version).where(
                            m.state_versions.c.customer_id == "cus_a"
                        )
                    )
                    == 20
                )

    asyncio.run(scenario())


def test_twenty_retries_of_same_plan_only_commit_once():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db)
            await svc.prepare(p)
            results = await asyncio.gather(*(svc.execute(e) for _ in range(20)))
            assert sum(not r.replayed for r in results) == 1
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(m.accounts.c.debits_posted_cents).where(m.accounts.c.id == "acc_a")
                    )
                    == 100
                )
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 1

    asyncio.run(scenario())


def test_bidirectional_transfers_and_freeze_competition():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            # Two customers in opposite directions require the same sorted lock order.
            async with admin.begin() as conn:
                payee = Payee(
                    id="pye_a",
                    customer_id="cus_b",
                    name="甲（P1）",
                    alias=None,
                    phone=None,
                    bank_name="本行",
                    account_no="6222020000000001",
                    target_account_id="acc_a",
                    target_fingerprint=fingerprint(
                        "6222020000000001", os.environ["FINGERPRINT_KEY"]
                    ),
                    created_at=NOW,
                )
                await conn.execute(m.payees.insert().values(**payee.model_dump()))
            requests = [
                await commands(db, [step(amount_cents=100)], plan_id="pln_forward"),
                await commands(
                    db,
                    [step(from_account_id="acc_b", payee_id="pye_a", amount_cents=100)],
                    plan_id="pln_reverse",
                    customer_id="cus_b",
                ),
            ]
            results = await asyncio.gather(
                *(with_current_versions(db, svc, p, e) for p, e in requests)
            )
            assert all(r.status == "EXECUTED" for r in results)
            async with db.connect() as conn:
                a = (
                    (await conn.execute(select(m.accounts).where(m.accounts.c.id == "acc_a")))
                    .mappings()
                    .one()
                )
                assert a["credits_posted_cents"] - a["debits_posted_cents"] == 600000
            frozen = await commands(db, [step("freeze_card")], plan_id="pln_freeze")
            transfer = await commands(db, plan_id="pln_competing_transfer")
            results = await asyncio.gather(
                *(with_current_versions(db, svc, p, e) for p, e in (frozen, transfer))
            )
            assert results[0].status == "EXECUTED"
            assert results[1].status == "EXECUTED" or results[1].error_code == "CARD_STATE_CONFLICT"
            async with db.connect() as conn:
                assert (
                    await conn.scalar(select(m.cards.c.status).where(m.cards.c.id == "crd_a"))
                    == "FROZEN"
                )

    asyncio.run(scenario())


def test_concurrent_overspend_never_overdraws():
    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            requests = [
                await commands(db, [step(amount_cents=50000)], plan_id=f"pln_spend_{i}")
                for i in range(20)
            ]
            results = await asyncio.gather(
                *(with_current_versions(db, svc, p, e) for p, e in requests)
            )
            assert sum(r.status == "EXECUTED" for r in results) == 12
            assert sum(r.error_code == "INSUFFICIENT_FUNDS" for r in results) == 8
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(
                            m.accounts.c.credits_posted_cents - m.accounts.c.debits_posted_cents
                        ).where(m.accounts.c.id == "acc_a")
                    )
                    == 0
                )
                assert await conn.scalar(select(func.count()).select_from(m.plan_executions)) == 12

    asyncio.run(scenario())


def test_pending_settle_release_race_has_one_terminal_record():
    from app.state import persist
    from safebank_bankcore.ledger import post_transfer
    from safebank_contracts.state import LedgerTransfer
    from sqlalchemy import update

    async def scenario():
        async with database() as (db, admin):
            p, _ = await commands(db)
            async with db.begin() as conn:
                scope = await resolve_scope(conn, "cus_a", p.steps, os.environ["FINGERPRINT_KEY"])
                state = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                pending = LedgerTransfer(
                    id="trf_race_pending",
                    plan_id="pln_pending_fixture",
                    debit_account_id="acc_a",
                    credit_account_id="acc_b",
                    amount_cents=100,
                    code="P2P_INTERNAL",
                    phase="PENDING",
                    pending_id=None,
                    step_id="s1",
                    occurred_at=NOW,
                    expires_at="2026-10-16T00:00:00+08:00",
                )
                _, effects, errors = post_transfer(state, pending, NOW)
                assert not errors
                await persist(conn, effects)

            async def resolve(phase):
                # Trusted test driver exercises future event accounting, not an exposed endpoint.
                async with db.begin() as conn:
                    await conn.execute(select(m.clock).with_for_update(read=True))
                    await conn.execute(
                        select(m.state_versions)
                        .order_by(m.state_versions.c.customer_id)
                        .with_for_update()
                    )
                    state = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                    command = LedgerTransfer(
                        **(
                            pending.model_dump()
                            | dict(
                                id="trf_race_" + phase,
                                phase=phase,
                                pending_id=pending.id,
                                expires_at=None,
                            )
                        )
                    )
                    _, effects, errors = post_transfer(state, command, NOW)
                    if errors:
                        assert errors[0].code == "PENDING_ALREADY_RESOLVED"
                        return False
                    await persist(conn, effects)
                    await conn.execute(
                        update(m.state_versions).values(version=m.state_versions.c.version + 1)
                    )
                    return True

            assert sum(await asyncio.gather(resolve("POST_PENDING"), resolve("VOID_PENDING"))) == 1
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.transfers)
                        .where(m.transfers.c.pending_id == pending.id)
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        select(m.accounts.c.debits_pending_cents).where(m.accounts.c.id == "acc_a")
                    )
                    == 0
                )

    asyncio.run(scenario())
