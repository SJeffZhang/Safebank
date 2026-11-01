"""Cancellation results and subscription reads use the same frozen period arithmetic."""

from datetime import timedelta

import pytest
from app.reads import read
from safebank_bankcore.banking import apply_plan
from safebank_contracts.plan import PlanBody
from safebank_contracts.tools import READ_ARGS
from safebank_contracts.views import SubscriptionView

from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.contracts.samples import step


@pytest.mark.parametrize(
    "period,amount,monthly,annual",
    [
        ("MONTHLY", 3000, 3000, 36000),
        ("WEEKLY", 3000, 13000, 156000),
        ("WEEKLY", 19900, 86233, 1034800),
    ],
)
def test_actual_cancel_savings_equal_live_subscription_read_monthly_and_annual(
    period, amount, monthly, annual
):
    bill = raw_billing(
        period=period,
        anchor_day=18 if period == "MONTHLY" else None,
        weekday=6 if period == "WEEKLY" else None,
        amount_cents=amount,
    )
    mandate = raw_mandate(max_amount_cents=amount)
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    # The actual L3 read consumes its authoritative SubscriptionView projection.
    sub = SubscriptionView(
        instruction_id=mandate.id,
        billing_id=bill.id,
        account_id=bill.account_id,
        merchant_id=bill.merchant_id,
        merchant_name="合成商户",
        period=period,
        amount_cents=amount,
        max_amount_cents=amount,
        next_due_at=bill.next_due_at,
        status="ACTIVE",
        annual_cost_cents=annual,
    )
    state = state.model_copy(update={"subscriptions": (sub,)})
    totals = read(
        state,
        "list_subscriptions",
        READ_ARGS["list_subscriptions"](status="ACTIVE", cursor=None, limit=50),
        "cus_a",
    )
    assert totals.total_monthly_cents == monthly and totals.total_annual_cents == annual
    plan = PlanBody(
        intents=(), steps=[step("cancel_subscription", instruction_id=mandate.id)], rationale=""
    )
    result = apply_plan(state, plan.steps, "cus_a", "pln_cancel_" + period + str(amount))
    assert result.error is None
    savings = result.results[0].result
    assert savings["monthly_saving_cents"] == totals.total_monthly_cents
    assert savings["annual_saving_cents"] == totals.total_annual_cents


def test_normalize_period_rejects_unfrozen_periods_and_noninteger_amounts():
    from safebank_bankcore.analytics import normalize_period

    assert normalize_period(0, "MONTHLY") == (0, 0)
    assert normalize_period(3000, "WEEKLY") == (13000, 156000)
    for period in ("ONCE", "YEARLY", "DAILY"):
        with pytest.raises(ValueError):
            normalize_period(3000, period)
    for amount in (True, 1.0, "100", -1):
        with pytest.raises(ValueError):
            normalize_period(amount, "MONTHLY")


@pytest.mark.parametrize("inactive", ["deleted", "inactive_billing"])
def test_noop_or_inactive_billing_cancel_reports_zero_savings(inactive):
    bill = raw_billing(
        period="WEEKLY", anchor_day=None, weekday=6, active=inactive != "inactive_billing"
    )
    mandate = raw_mandate(status="DELETED" if inactive == "deleted" else "ACTIVE")
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    plan = PlanBody(
        intents=(), steps=[step("cancel_subscription", instruction_id=mandate.id)], rationale=""
    )
    result = apply_plan(state, plan.steps, "cus_a", "pln_zero_" + inactive)
    assert result.error is None
    assert result.results[0].result["monthly_saving_cents"] == 0
    assert result.results[0].result["annual_saving_cents"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        "cap",
        "end_before_due",
        "expired",
        "future_mandate",
        "future_billing",
        "past_due",
        "coverage",
        "merchant",
        "duplicate_mandate",
    ],
)
def test_cancel_result_uses_original_verified_charge_eligibility(invalid):
    bill = raw_billing(amount_cents=19900)
    mandate = raw_mandate(max_amount_cents=19900)
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    snap = state.obligations
    if invalid == "cap":
        mandate = mandate.model_copy(update={"max_amount_cents": 100})
    elif invalid == "end_before_due":
        mandate = mandate.model_copy(update={"end_at": bill.next_due_at - timedelta(seconds=1)})
    elif invalid == "expired":
        mandate = mandate.model_copy(update={"end_at": state.sim_now - timedelta(seconds=1)})
    elif invalid == "future_mandate":
        mandate = mandate.model_copy(update={"created_at": state.sim_now + timedelta(seconds=1)})
    elif invalid == "future_billing":
        bill = bill.model_copy(update={"created_at": state.sim_now + timedelta(seconds=1)})
    elif invalid == "past_due":
        state = state.model_copy(update={"sim_now": bill.next_due_at + timedelta(seconds=1)})
    elif invalid == "coverage":
        snap = snap.model_copy(update={"covered_customer_ids": ("cus_b",)})
    elif invalid == "merchant":
        snap = snap.model_copy(update={"merchants": {}})
    instructions = {mandate.id: mandate}
    if invalid == "duplicate_mandate":
        other = mandate.model_copy(update={"id": "si_ambiguous"})
        instructions[other.id] = other
    snap = snap.model_copy(
        update={"standing_instructions": instructions, "billings": {bill.id: bill}}
    )
    state = state.model_copy(update={"obligations": snap})
    plan = PlanBody(
        intents=(), steps=[step("cancel_subscription", instruction_id=mandate.id)], rationale=""
    )
    result = apply_plan(state, plan.steps, "cus_a", "pln_ineligible_" + invalid)
    assert result.error is None
    assert result.results[0].result["status"] == "DELETED"
    assert result.results[0].result["no_op"] is False
    assert result.results[0].result["monthly_saving_cents"] == 0
    assert result.results[0].result["annual_saving_cents"] == 0


@pytest.mark.parametrize(
    "invalid",
    ["customer", "account_owner", "merchant_binding", "billing_binding", "calendar", "processed"],
)
def test_unverified_cancellation_rate_cannot_create_positive_savings(invalid):
    from safebank_bankcore.subscription_savings import subscription_savings
    from safebank_contracts.obligations import InstructionRun

    bill, mandate = raw_billing(amount_cents=19900), raw_mandate(max_amount_cents=19900)
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    snap = state.obligations
    customer_id = "cus_a"
    if invalid == "customer":
        customer_id = "cus_b"
    elif invalid == "account_owner":
        account = state.accounts["acc_a"].model_copy(update={"customer_id": "cus_b"})
        state = state.model_copy(update={"accounts": {**state.accounts, account.id: account}})
    elif invalid == "merchant_binding":
        mandate = mandate.model_copy(update={"merchant_id": "mer_other"})
    elif invalid == "billing_binding":
        mandate = mandate.model_copy(update={"billing_id": "bill_other"})
    elif invalid == "calendar":
        bill = bill.model_copy(update={"anchor_day": 19})
    else:
        run = InstructionRun(
            id="run_prior",
            customer_id="cus_a",
            account_id="acc_a",
            kind="MERCHANT_MANDATE",
            instruction_id=mandate.id,
            billing_id=bill.id,
            due_at=bill.next_due_at,
            event_id="evt_prior",
            amount_cents=19900,
            status="BLOCKED",
            code="MANDATE_AMOUNT_EXCEEDED",
            transfer_id=None,
        )
        snap = snap.model_copy(update={"runs": (run,)})
    snap = snap.model_copy(
        update={"standing_instructions": {mandate.id: mandate}, "billings": {bill.id: bill}}
    )
    state = state.model_copy(update={"obligations": snap})
    assert subscription_savings(state, mandate.id, customer_id) in (None, (0, 0))


def test_savings_helper_is_read_only_and_inclusive_at_actual_next_due():
    from safebank_bankcore.subscription_savings import subscription_savings

    bill = raw_billing(amount_cents=19900)
    mandate = raw_mandate(max_amount_cents=19900, end_at=bill.next_due_at)
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    original = state.model_dump()
    assert subscription_savings(state, mandate.id, "cus_a") == (19900, 238800)
    assert state.model_dump() == original
    # Once this due instant is reached, the snapshot cannot establish an unconsumed future charge.
    state = state.model_copy(update={"sim_now": bill.next_due_at})
    assert subscription_savings(state, mandate.id, "cus_a") == (0, 0)
