import asyncio
import os

import pytest
from app import models as m
from app.seed import NOW
from app.state import load_bank_state, persist, resolve_scope
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.plan import PlanBody
from safebank_contracts.state import BankState, LedgerTransfer
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.contracts.samples import step
from tests.integration.helpers import database

pytestmark = [pytest.mark.integration, pytest.mark.consistency]


def test_snapshot_is_read_only_round_trips_and_redacts_contacts():
    async def scenario():
        async with database() as (db, admin):
            steps = PlanBody(intents=(), steps=[step()], rationale="test").steps
            async with db.connect() as conn:
                await conn.execution_options(isolation_level="REPEATABLE READ")
                async with conn.begin():
                    await conn.execute(text("SET TRANSACTION READ ONLY"))
                    scope = await resolve_scope(conn, "cus_a", steps, os.environ["FINGERPRINT_KEY"])
                    state = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                    assert set(state.versions) == {"cus_a", "cus_b"}
                    assert state.customers["cus_b"].phone == ""
                    assert state.accounts["acc_a"].available_cents == 600_000
                    assert BankState.model_validate_json(state.model_dump_json()) == state
                    with pytest.raises(DBAPIError):
                        async with conn.begin_nested():
                            await conn.execute(text("UPDATE state_versions SET version=9"))
            async with admin.connect() as conn:
                assert await conn.scalar(select(m.state_versions.c.version).limit(1)) == 0

    asyncio.run(scenario())


def test_ledger_persistence_matches_shadow_and_rolls_back():
    async def scenario():
        async with database() as (db, admin):
            steps = PlanBody(intents=(), steps=[step()], rationale="test").steps
            async with db.connect() as conn:
                transaction = await conn.begin()
                scope = await resolve_scope(conn, "cus_a", steps, os.environ["FINGERPRINT_KEY"])
                before = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                cmd = LedgerTransfer(
                    id="trf_consistency",
                    debit_account_id="acc_a",
                    credit_account_id="acc_b",
                    amount_cents=100,
                    code="P2P_INTERNAL",
                    phase="PENDING",
                    pending_id=None,
                    plan_id="pln_test",
                    step_id="s1",
                    occurred_at=NOW,
                    expires_at="2026-10-16T09:00:00+08:00",
                )
                shadow, effects, errors = post_transfer(before, cmd, NOW)
                assert not errors
                await persist(conn, effects)
                actual = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                assert actual.accounts == shadow.accounts
                assert actual.transfers == shadow.transfers
                assert actual.transactions[-1].available_after_cents == 599900
                await transaction.rollback()
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        select(m.transfers.c.id).where(m.transfers.c.id == "trf_consistency")
                    )
                    is None
                )
                assert (
                    await conn.scalar(
                        select(m.accounts.c.debits_pending_cents).where(m.accounts.c.id == "acc_a")
                    )
                    == 0
                )

    asyncio.run(scenario())


def test_card_and_payee_effects_commit_and_reload():
    from safebank_bankcore.banking import apply_plan

    async def scenario():
        async with database() as (db, admin):
            steps = PlanBody(
                intents=(),
                steps=[
                    step("report_card_lost"),
                    step("add_payee", step_id="s2", account_no="6222020000000077"),
                ],
                rationale="test",
            ).steps
            async with db.begin() as conn:
                scope = await resolve_scope(conn, "cus_a", steps, os.environ["FINGERPRINT_KEY"])
                before = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                simulation = apply_plan(before, steps, "cus_a", "pln_card_persist")
                assert simulation.error is None
                await persist(conn, simulation.effects)
            async with db.connect() as conn:
                actual = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                assert actual.cards == simulation.state.cards
                assert actual.payees == simulation.state.payees
                assert (
                    actual.accounts["acc_a"].current_card_id
                    == simulation.state.accounts["acc_a"].current_card_id
                )
                assert len(actual.ops_7d) == 2
                assert len((await conn.execute(select(m.outbox))).all()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "phase,amount", [("POST_PENDING", 100), ("POST_PENDING", 40), ("VOID_PENDING", 100)]
)
def test_pending_resolution_commit_matches_prediction(phase, amount):
    async def scenario():
        async with database() as (db, admin):
            steps = PlanBody(intents=(), steps=[step()], rationale="test").steps
            async with db.begin() as conn:
                scope = await resolve_scope(conn, "cus_a", steps, os.environ["FINGERPRINT_KEY"])
                before = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                pending = LedgerTransfer(
                    id="trf_pending",
                    debit_account_id="acc_a",
                    credit_account_id="acc_b",
                    amount_cents=100,
                    code="P2P_INTERNAL",
                    phase="PENDING",
                    pending_id=None,
                    plan_id="pln_pending",
                    step_id="s1",
                    occurred_at=NOW,
                    expires_at="2026-10-16T09:00:00+08:00",
                )
                shadow, effects, errors = post_transfer(before, pending, NOW)
                assert not errors
                await persist(conn, effects)
            async with db.begin() as conn:
                actual = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                assert actual.accounts == shadow.accounts
                resolution = LedgerTransfer(
                    **(
                        pending.model_dump()
                        | dict(
                            id="trf_resolution",
                            phase=phase,
                            amount_cents=amount,
                            pending_id=pending.id,
                            expires_at=None,
                        )
                    )
                )
                expected, effects, errors = post_transfer(actual, resolution, NOW)
                assert not errors
                await persist(conn, effects)
            async with db.connect() as conn:
                final = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                assert final.accounts == expected.accounts
                assert final.transfers == expected.transfers
                assert final.accounts["acc_a"].debits_pending_cents == 0
                assert final.accounts["acc_b"].credits_pending_cents == 0
                assert final.accounts["acc_a"].available_cents == 600000 - (
                    amount if phase == "POST_PENDING" else 0
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "transfer",
        "external",
        "self",
        "multi",
        "replace",
        "controls",
        "failure",
        "ref_new",
        "ref_existing",
    ],
)
def test_plan_predictions_match_database_for_business_combinations(case):
    from app.execution import ExecutionService
    from safebank_bankcore.banking import apply_plan

    from tests.integration.test_execution import commands

    variants = {
        "transfer": [step(amount_cents=12345)],
        "external": [step(payee_id="pye_ext", amount_cents=99)],
        "self": [step(payee_id=None, to_account_id="acc_s", amount_cents=12345)],
        "multi": [
            step("update_card_limits", single_limit_cents=200000),
            step(step_id="s2", amount_cents=100000),
        ],
        "replace": [step(amount_cents=20000), step("report_card_lost", step_id="s2")],
        "controls": [step("update_card_controls", online=False), step("freeze_card", step_id="s2")],
        "failure": [step(amount_cents=400000), step(step_id="s2", amount_cents=400000)],
    }
    if case.startswith("ref_"):
        number = "6222020000000003" if case == "ref_existing" else "6222020000000077"
        add = step("add_payee", account_no=number)
        transfer = step(step_id="s2", payee_id={"ref": "s1"}, amount_cents=100)
        transfer["depends_on"] = ["s1"]
        variants[case] = [add, transfer]

    async def scenario():
        async with database() as (db, admin):
            svc = ExecutionService(db, os.environ["FINGERPRINT_KEY"])
            p, e = await commands(db, variants[case])
            async with db.connect() as conn:
                scope = await resolve_scope(conn, "cus_a", e.steps, os.environ["FINGERPRINT_KEY"])
                before = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
            predicted = apply_plan(before, e.steps, "cus_a", p.plan_id)
            await svc.prepare(p)
            result = await svc.execute(e)
            assert result.steps == predicted.results
            async with db.connect() as conn:
                after = await load_bank_state(conn, scope, NOW, customer_id="cus_a")
                from safebank_bankcore.ids import digest
                from safebank_bankcore.references import resolved_steps

                stored = await conn.scalar(select(m.execution_intents.c.steps_digest))
                assert stored == digest(resolved_steps(before, e.steps, "cus_a", p.plan_id))
                assert after.accounts == predicted.state.accounts
                assert after.cards == predicted.state.cards
                assert after.payees == predicted.state.payees
                assert after.transfers == predicted.state.transfers
                assert after.counters == predicted.state.counters

                def tx_data(tx):
                    return tx.model_dump(mode="python", exclude={"sequence"})

                expected_txs = [
                    tx_data(tx) for tx in predicted.state.transactions if tx.customer_id == "cus_a"
                ]
                assert [tx_data(tx) for tx in after.transactions] == expected_txs
                from safebank_contracts.state import OperationEvent

                expected_ops = {
                    fx.operation.event_id: fx.operation.model_dump(
                        mode="python", exclude={"commit_seq"}
                    )
                    for fx in predicted.effects
                    if isinstance(fx, OperationEvent)
                }
                assert {
                    op.event_id: op.model_dump(mode="python", exclude={"commit_seq"})
                    for op in after.ops_7d
                } == expected_ops

    asyncio.run(scenario())
