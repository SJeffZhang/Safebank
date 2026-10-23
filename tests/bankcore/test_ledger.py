from datetime import timedelta

import pytest
from safebank_bankcore.ids import derive
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.state import LedgerTransfer

from tests.bankcore.fixtures import NOW, base_state


def command(phase="POSTED", **overrides):
    return LedgerTransfer(
        id="trf_1",
        debit_account_id="acc_a",
        credit_account_id="acc_b",
        amount_cents=100,
        code="P2P_INTERNAL",
        phase=phase,
        pending_id=None,
        plan_id="pln_1",
        step_id="s1",
        occurred_at=NOW,
        expires_at=NOW + timedelta(days=1) if phase == "PENDING" else None,
        **overrides,
    )


def balanced(state):
    for suffix in ("posted_cents", "pending_cents"):
        assert sum(getattr(a, "debits_" + suffix) for a in state.accounts.values()) == sum(
            getattr(a, "credits_" + suffix) for a in state.accounts.values()
        )


def test_tb1_tb2_tb5_tb9_post_and_idempotency():
    state = base_state()
    before = state.model_dump_json()
    after, effects, errors = post_transfer(state, command(), NOW)
    assert not errors and len(effects) == 3
    assert state.model_dump_json() == before
    assert after.accounts["acc_a"].available_cents == 599900
    balanced(after)
    assert post_transfer(after, command(), NOW) == (after, (), ())
    conflict = command().model_copy(update={"amount_cents": 101})
    assert post_transfer(after, conflict, NOW)[2][0].code == "IDEMPOTENCY_CONFLICT"
    assert derive("plan", "step", "trf") == derive("plan", "step", "trf")


def test_tb2_insufficient_and_same_account_leave_state_untouched():
    state = base_state(99)
    assert post_transfer(state, command(), NOW)[0] is state
    assert post_transfer(state, command(), NOW)[2][0].code == "INSUFFICIENT_FUNDS"
    same = command().model_copy(update={"credit_account_id": "acc_a"})
    assert post_transfer(state, same, NOW)[2][0].code == "CONTRACT_INVALID"


@pytest.mark.parametrize("amount", [1, 40, 100])
def test_tb3_pending_partial_and_full_post(amount):
    state = base_state()
    reserved, _, errors = post_transfer(state, command("PENDING"), NOW)
    assert not errors and reserved.accounts["acc_a"].balance_cents == 600000
    assert reserved.accounts["acc_a"].available_cents == 599900
    resolved = command().model_copy(
        update={
            "id": "trf_2",
            "phase": "POST_PENDING",
            "pending_id": "trf_1",
            "amount_cents": amount,
        }
    )
    after, _, errors = post_transfer(reserved, resolved, NOW)
    assert not errors and after.accounts["acc_a"].available_cents == 600000 - amount
    assert after.accounts["acc_a"].debits_pending_cents == 0
    balanced(after)
    again = resolved.model_copy(update={"id": "trf_3"})
    assert post_transfer(after, again, NOW)[2][0].code == "PENDING_ALREADY_RESOLVED"


def test_tb3_void_expiry_and_bounds():
    reserved, _, _ = post_transfer(base_state(), command("PENDING"), NOW)
    expired_at = NOW + timedelta(days=1)
    post = command().model_copy(
        update={
            "id": "trf_2",
            "phase": "POST_PENDING",
            "pending_id": "trf_1",
            "occurred_at": expired_at,
        }
    )
    assert post_transfer(reserved, post, expired_at)[2][0].code == "PENDING_EXPIRED"
    void = post.model_copy(update={"phase": "VOID_PENDING"})
    after, _, errors = post_transfer(reserved, void, expired_at)
    assert not errors and after.accounts["acc_a"].available_cents == 600000
    balanced(after)
    too_much = post.model_copy(update={"amount_cents": 101, "occurred_at": NOW})
    assert post_transfer(reserved, too_much, NOW)[2][0].code == "CONTRACT_INVALID"
