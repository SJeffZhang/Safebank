"""Aggregate actual pure transaction producers, not only hand-written legacy kinds."""

import pytest
from safebank_bankcore.analytics import spending_summary
from safebank_bankcore.banking import Context
from safebank_bankcore.events import apply_merchant_charge
from safebank_bankcore.external import ExternalCredit, apply_external_credit

from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import raw_billing, raw_mandate, raw_projection_state
from tests.bankcore.test_banking import banking_state, simulate
from tests.bankcore.test_funds import funds_state
from tests.bankcore.test_funds import simulate as simulate_fund
from tests.contracts.samples import step


@pytest.mark.parametrize(
    "payee,stored_kind", [("pye_b", "P2P_INTERNAL"), ("pye_ext", "P2P_EXTERNAL")]
)
def test_actual_transfer_effects_are_reported_once_as_transfer_out(payee, stored_kind):
    result = simulate(banking_state(), [step(payee_id=payee, amount_cents=10000)])
    assert result.error is None
    assert any(t.kind == stored_kind and t.direction == "OUT" for t in result.state.transactions)
    summary = spending_summary(result.state, customer_id="cus_a", month="2026-10")
    assert summary["transfer_out_cents"] == 10000
    assert summary["total_cents"] == 0
    # The other customer's incoming leg is neither spending nor outgoing.
    other = spending_summary(result.state, customer_id="cus_b", month="2026-10")
    assert other["transfer_out_cents"] == other["total_cents"] == 0


def test_actual_merchant_charge_effects_are_consumption_once():
    bill = raw_billing()
    state = raw_projection_state(billings=[bill], mandates=[raw_mandate()])
    after, effects, run = apply_merchant_charge(state, billing_id=bill.id, due_at=bill.next_due_at)
    assert effects and run.status == "SUCCESS"
    assert any(t.kind == "MERCHANT_CHARGE" for t in after.transactions)
    summary = spending_summary(after, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == bill.amount_cents
    assert summary["transfer_out_cents"] == 0


def test_actual_self_transfer_salary_and_fund_operations_are_not_consumption_or_outflow():
    own = simulate(
        banking_state(), [step(payee_id=None, to_account_id="acc_s", amount_cents=10000)]
    )
    assert own.error is None and {t.kind for t in own.state.transactions} == {"SELF_TRANSFER"}
    credited, effects, errors = apply_external_credit(
        own.state, ExternalCredit("acc_a", 10000), NOW, Context("cus_a", "pln_salary", "s1")
    )
    assert effects and not errors
    summary = spending_summary(credited, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == summary["transfer_out_cents"] == 0
    sub = simulate_fund(
        funds_state(), [step("subscribe_product", product_id="prd_mmf", amount_cents=10000)]
    )
    assert sub.error is None
    red = simulate_fund(
        sub.state,
        [step("redeem_product", product_id="prd_mmf", units="50.0000")],
        plan_id="pln_redeem_review",
    )
    assert red.error is None
    summary = spending_summary(red.state, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == summary["transfer_out_cents"] == 0


def test_canonical_and_legacy_rows_keep_their_own_amounts_without_double_counting():
    from tests.bankcore.fixtures import base_state
    from tests.bankcore.test_analytics import at, tx, with_txns

    kinds = [
        ("P2P_INTERNAL", 100),
        ("P2P_EXTERNAL", 200),
        ("TRANSFER_EXTERNAL", 300),
        ("MERCHANT_CHARGE", 400),
        ("MANDATE", 500),
        ("CARD_SPEND", 600),
    ]
    state = with_txns(
        base_state(),
        *(
            tx(
                id=f"tx_{i}",
                transfer_id=f"trf_{i}",
                kind=kind,
                amount_cents=amount,
                sequence=i,
                occurred_at=at("2026-10-10T10:00:00+08:00"),
            )
            for i, (kind, amount) in enumerate(kinds)
        ),
    )
    summary = spending_summary(state, customer_id="cus_a", month="2026-10")
    assert summary["transfer_out_cents"] == 600
    assert summary["total_cents"] == 1500


def test_canonical_historical_charges_are_recurring_and_never_count_as_usage():
    from safebank_bankcore.analytics import detect_subscriptions

    from tests.bankcore.fixtures import base_state
    from tests.bankcore.test_analytics import _state_with_obligation, _sub_tx, at, with_txns

    state = with_txns(
        _state_with_obligation(base_state()),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 3000, "old", kind="MANDATE"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 3000, "mid", kind="MERCHANT_CHARGE"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 3000, "new", kind="MERCHANT_CHARGE"),
        _sub_tx("mer_gym", at("2026-07-01T06:00:00+08:00"), 1000, "usage", kind="CARD_SPEND"),
    )
    result = detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["occurrences"] == 3 and item["is_idle"]
    assert item["usage_evidence"] == {"recent_usage_count": 0, "previous_usage_count": 1}
