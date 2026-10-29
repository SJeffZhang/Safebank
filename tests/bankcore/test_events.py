"""Tests for events.py – pure system-event execution.

Covers ``apply_scheduled_transfer`` (a due standing instruction executes one
period) and ``apply_merchant_charge`` (a merchant billing charges via its
mandate). These are L3 system events, not write tools: inputs are the immutable
event payload (instruction/billing id, due_at) and outputs reuse the same
``(new_state, effects, run)`` style as the other bankcore appliers.
"""

from datetime import datetime

from safebank_bankcore.events import apply_merchant_charge, apply_scheduled_transfer
from safebank_contracts.state import PostTransfer, ScheduleEventJob, SetInstructionStatus

from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_instruction,
    raw_mandate,
    raw_projection_state,
)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# apply_scheduled_transfer
# ---------------------------------------------------------------------------


def test_monthly_transfer_executes_and_clamps_next_run():
    inst = raw_instruction(
        period="MONTHLY",
        anchor_day=31,
        weekday=None,
        amount_cents=1000,
        payee_id=None,
        to_account_id="acc_s",
        next_run_at=at("2026-01-31T06:00:00+08:00"),
    )
    state = raw_projection_state(balance=600000, instructions=[inst])
    due = at("2026-01-31T06:00:00+08:00")

    new_state, effects, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)

    assert run is not None
    assert run.status == "SUCCESS"
    assert run.amount_cents == 1000
    assert run.transfer_id is not None
    # OWN transfer: debit acc_a, credit acc_s.
    assert new_state.accounts["acc_a"].debits_posted_cents == 1000
    assert new_state.accounts["acc_s"].credits_posted_cents == 2_000_000 + 1000
    # Month-end clamp: Jan 31 -> Feb 28 (2026 is not a leap year).
    updated = new_state.obligations.standing_instructions[inst.id]
    assert updated.status == "ACTIVE"
    assert updated.next_run_at == at("2026-02-28T06:00:00+08:00")
    job = next(e for e in effects if isinstance(e, ScheduleEventJob))
    assert job.kind == "SCHEDULED_TRANSFER"
    assert job.due_at == at("2026-02-28T06:00:00+08:00")


def test_insufficient_funds_blocks_and_advances_once():
    inst = raw_instruction(
        period="ONCE",
        amount_cents=700000,
        next_run_at=at("2026-10-18T06:00:00+08:00"),
    )
    state = raw_projection_state(balance=600000, instructions=[inst])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)

    assert run.status == "FAILED_INSUFFICIENT"
    assert run.code == "INSUFFICIENT_FUNDS"
    assert run.transfer_id is None
    # No ledger change.
    assert new_state.accounts == state.accounts
    assert new_state.transfers == state.transfers
    assert new_state.transactions == state.transactions
    # Period advances once: ONCE instructions terminate after their single run.
    updated = new_state.obligations.standing_instructions[inst.id]
    assert updated.status == "DELETED"
    assert updated.next_run_at is None
    assert any(isinstance(e, SetInstructionStatus) for e in effects)


def test_cancelled_instruction_is_blocked():
    inst = raw_instruction(status="DELETED", next_run_at=None)
    state = raw_projection_state(instructions=[inst])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)

    assert run.status == "BLOCKED"
    assert run.code == "SCHEDULED_BYPASS"
    assert run.transfer_id is None
    assert new_state.accounts == state.accounts
    assert effects == ()
    assert new_state.obligations.standing_instructions[inst.id].status == "DELETED"


# ---------------------------------------------------------------------------
# apply_merchant_charge
# ---------------------------------------------------------------------------


def test_merchant_charge_active_mandate_posts():
    billing = raw_billing()
    mandate = raw_mandate()
    state = raw_projection_state(billings=[billing], mandates=[mandate])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_merchant_charge(state, billing_id=billing.id, due_at=due)

    assert run.status == "SUCCESS"
    assert run.instruction_id == mandate.id
    assert run.billing_id == billing.id
    assert run.transfer_id is not None
    assert new_state.accounts["acc_a"].debits_posted_cents == 3000
    assert new_state.accounts["acc_external_clearing"].credits_posted_cents == 3000
    assert new_state.obligations.billings[billing.id].next_due_at == at("2026-11-18T06:00:00+08:00")
    assert any(isinstance(e, PostTransfer) for e in effects)


def test_merchant_charge_cancelled_mandate_blocked():
    billing = raw_billing()
    mandate = raw_mandate(status="DELETED")
    state = raw_projection_state(billings=[billing], mandates=[mandate])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_merchant_charge(state, billing_id=billing.id, due_at=due)

    assert run.status == "BLOCKED"
    assert run.code == "MANDATE_NOT_ACTIVE"
    assert run.transfer_id is None
    assert new_state.accounts == state.accounts
    # Billing advances independently of the mandate.
    assert new_state.obligations.billings[billing.id].next_due_at == at("2026-11-18T06:00:00+08:00")
    assert effects == ()


def test_merchant_charge_exceeds_max_is_blocked():
    billing = raw_billing(amount_cents=5000)
    mandate = raw_mandate()
    state = raw_projection_state(billings=[billing], mandates=[mandate])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_merchant_charge(state, billing_id=billing.id, due_at=due)

    assert run.status == "BLOCKED"
    assert run.code == "MANDATE_AMOUNT_EXCEEDED"
    assert run.transfer_id is None
    assert new_state.accounts == state.accounts
    assert new_state.obligations.billings[billing.id].next_due_at == at("2026-11-18T06:00:00+08:00")
    assert effects == ()


# ---------------------------------------------------------------------------
# shared invariants
# ---------------------------------------------------------------------------


def test_replay_same_period_is_idempotent():
    inst = raw_instruction(
        period="MONTHLY",
        anchor_day=18,
        weekday=None,
        amount_cents=1000,
        next_run_at=at("2026-10-18T06:00:00+08:00"),
    )
    state = raw_projection_state(balance=600000, instructions=[inst])
    due = at("2026-10-18T06:00:00+08:00")

    first, effects_1, run_1 = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)
    assert run_1 is not None
    assert len(first.transfers) == 1

    second, effects_2, run_2 = apply_scheduled_transfer(first, instruction_id=inst.id, due_at=due)
    assert run_2 is None
    assert effects_2 == ()
    assert second.accounts == first.accounts
    assert len(second.transfers) == len(first.transfers)
    assert len(second.transactions) == len(first.transactions)
    assert len(second.obligations.runs) == len(first.obligations.runs)


def test_conservation():
    inst = raw_instruction(amount_cents=2500)
    state = raw_projection_state(balance=600000, instructions=[inst])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, _, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)
    assert run.status == "SUCCESS"

    total_credits = sum(a.credits_posted_cents for a in new_state.accounts.values())
    total_debits = sum(a.debits_posted_cents for a in new_state.accounts.values())
    assert total_credits == total_debits


def test_cents_are_strict_ints():
    inst = raw_instruction(amount_cents=1234)
    state = raw_projection_state(balance=600000, instructions=[inst])
    due = at("2026-10-18T06:00:00+08:00")

    new_state, effects, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=due)

    assert type(run.amount_cents) is int
    trf = next(e.transfer for e in effects if isinstance(e, PostTransfer))
    assert type(trf.amount_cents) is int
    for account in new_state.accounts.values():
        assert type(account.credits_posted_cents) is int
        assert type(account.debits_posted_cents) is int
