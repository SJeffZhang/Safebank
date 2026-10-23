from datetime import timedelta

import pytest
from safebank_bankcore.banking import apply_plan
from safebank_bankcore.ledger import replace_state
from safebank_contracts.plan import PlanBody
from safebank_contracts.state import Card, Counters, DirectoryEntry, Payee
from safebank_contracts.views import ChannelFlags

from tests.bankcore.fixtures import NOW, base_state
from tests.contracts.samples import step


def banking_state():
    state = base_state()
    card = Card(
        id="crd_a",
        account_id="acc_a",
        last4="1234",
        status="ACTIVE",
        single_limit_cents=500000,
        daily_limit_cents=2000000,
        controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=False),
        created_at=NOW,
    )
    payees = {
        p: Payee(
            id=p,
            customer_id="cus_a",
            name=p,
            alias=None,
            phone=None,
            bank_name="bank",
            account_no=number,
            target_account_id=target,
            target_fingerprint=p + "fp",
            created_at=NOW - timedelta(days=10),
        )
        for p, number, target in [
            ("pye_b", "6222020000000003", "acc_b"),
            ("pye_ext", "6222020000000099", None),
        ]
    }
    directory = {
        "number:" + p.account_no: DirectoryEntry(
            target_fingerprint=p.target_fingerprint,
            target_account_id=p.target_account_id,
            customer_id="cus_b" if p.target_account_id else None,
        )
        for p in payees.values()
    }
    directory["number:6222020000000001"] = DirectoryEntry(
        target_fingerprint="new-external", target_account_id=None, customer_id=None
    )
    accounts = dict(state.accounts) | {
        "acc_a": state.accounts["acc_a"].model_copy(update={"current_card_id": "crd_a"})
    }
    return replace_state(
        state, cards={card.id: card}, payees=payees, directory=directory, accounts=accounts
    )


def simulate(state, steps, plan_id="pln_test"):
    return apply_plan(
        state, PlanBody(intents=(), steps=steps, rationale="test").steps, "cus_a", plan_id
    )


@pytest.mark.parametrize("payee,kind", [("pye_b", "P2P_INTERNAL"), ("pye_ext", "P2P_EXTERNAL")])
def test_external_transfer_and_public_privacy(payee, kind):
    state = banking_state()
    result = simulate(state, [step(payee_id=payee, amount_cents=100)])
    assert result.error is None
    assert result.state.accounts["acc_a"].available_cents == 599900
    assert result.state.counters.daily_used_cents["acc_a"] == 100
    assert result.state.counters.daily_outflow_cents["cus_a"] == 100
    assert result.results[0].result["to_available_after_cents"] is None
    assert all(e.entity_id != "acc_b" for e in result.results[0].effects)
    assert any(t.kind == kind for t in result.state.transactions)


@pytest.mark.parametrize("status", ["FROZEN", "LOST"])
def test_frozen_or_lost_cannot_send_but_self_transfer_works(status):
    state = banking_state()
    state = replace_state(
        state,
        cards={
            "crd_a": state.cards["crd_a"].__class__.model_validate(
                state.cards["crd_a"].model_dump() | {"status": status}
            )
        },
    )
    assert simulate(state, [step()]).error == "CARD_STATE_CONFLICT"
    result = simulate(state, [step(payee_id=None, to_account_id="acc_s", amount_cents=600000)])
    assert result.error is None and not result.state.counters.daily_outflow_cents
    assert result.results[0].result["to_available_after_cents"] == 2600000


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"amount_cents": 500001}, "CARD_SINGLE_LIMIT"),
        ({"from_account_id": "acc_b"}, "NOT_FOUND"),
        ({"payee_id": "pye_not_owned"}, "PAYEE_NOT_FOUND"),
        ({"payee_id": None, "to_account_id": "acc_b"}, "NOT_FOUND"),
    ],
)
def test_invalid_transfer_leaves_original_state(change, expected):
    state = banking_state()
    result = simulate(state, [step(**change)])
    assert result.error == expected and result.state is state and result.effects == ()


def test_limits_counters_and_no_card_account():
    state = banking_state()
    state = replace_state(state, counters=Counters(daily_used_cents={"acc_a": 1999999}))
    assert simulate(state, [step(amount_cents=2)]).error == "CARD_DAILY_LIMIT"
    state = replace_state(state, counters=Counters(daily_outflow_cents={"cus_a": 5000000}))
    assert simulate(state, [step(amount_cents=1)]).error == "DAILY_OUTFLOW_LIMIT"
    assert (
        simulate(banking_state(), [step(from_account_id="acc_s", amount_cents=500001)]).error
        == "CARD_SINGLE_LIMIT"
    )


def test_second_step_failure_rolls_back_entire_shadow():
    state = banking_state()
    result = simulate(state, [step(amount_cents=400000), step(step_id="s2", amount_cents=400000)])
    assert result.error == "INSUFFICIENT_FUNDS" and result.state is state and not result.effects
    assert [s.status for s in result.results] == ["ROLLED_BACK", "FAILED"]


def test_new_and_existing_payee_reference_resolves_actual_id():
    state = banking_state()
    for number, expected in [("6222020000000003", "pye_b"), ("6222020000000001", None)]:
        first = step("add_payee", account_no=number)
        second = step(step_id="s2", payee_id={"ref": "s1"}, amount_cents=100) | {
            "depends_on": ["s1"]
        }
        result = simulate(state, [first, second])
        assert result.error is None
        created = result.results[0].created_ids["payee_id"]
        assert result.results[0].result["no_op"] == (expected is not None)
        if expected:
            assert created == expected
        assert (
            result.state.transactions[-1].payee_id == created
            or result.state.transactions[-1].direction == "IN"
        )


def test_cards_tightening_loosening_loss_and_replacement_preserve_counters():
    state = banking_state()
    state = replace_state(state, counters=Counters(daily_used_cents={"acc_a": 10000}))
    freeze = simulate(state, [step("freeze_card")])
    assert freeze.error is None and freeze.state.cards["crd_a"].status == "FROZEN"
    assert (
        simulate(freeze.state, [step("update_card_limits", single_limit_cents=600000)]).error
        == "CARD_STATE_CONFLICT"
    )
    tightened = simulate(freeze.state, [step("update_card_limits", single_limit_cents=1000)])
    assert tightened.error is None and tightened.results[0].result["direction"] == "TIGHTEN"
    assert simulate(freeze.state, [step("unfreeze_card")]).state.cards["crd_a"].status == "ACTIVE"
    opened = simulate(state, [step("update_card_controls", overseas=True)])
    assert opened.results[0].result["direction"] == "LOOSEN"
    lost = simulate(state, [step("report_card_lost")])
    new = lost.results[0].result["new_card_id"]
    assert new != "crd_a" and lost.state.cards["crd_a"].status == "LOST"
    assert lost.state.accounts["acc_a"].current_card_id == new
    assert lost.state.cards[new].controls.atm is False
    assert lost.state.counters.daily_used_cents["acc_a"] == 10000
    assert simulate(lost.state, [step("unfreeze_card")]).error == "CARD_STATE_CONFLICT"
    assert simulate(lost.state, [step("report_card_lost")]).error == "CARD_STATE_CONFLICT"


def test_noop_and_unimplemented_tools_never_fake_business():
    state = banking_state()
    result = simulate(state, [step("unfreeze_card")])
    assert result.results[0].result["no_op"] and not result.effects
    assert simulate(state, [step("schedule_transfer")]).error == "TOOL_NOT_ALLOWED"
