"""Cancellation results and subscription reads use the same frozen period arithmetic."""

import pytest
from app.reads import read
from safebank_bankcore.banking import apply_plan
from safebank_contracts.plan import PlanBody
from safebank_contracts.tools import READ_ARGS
from safebank_contracts.views import SubscriptionView

from tests.bankcore.obligations_fixtures import raw_billing, raw_mandate, raw_projection_state
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
    state = raw_projection_state(billings=[bill], mandates=[mandate])
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
    state = raw_projection_state(billings=[bill], mandates=[mandate])
    plan = PlanBody(
        intents=(), steps=[step("cancel_subscription", instruction_id=mandate.id)], rationale=""
    )
    result = apply_plan(state, plan.steps, "cus_a", "pln_zero_" + inactive)
    assert result.error is None
    assert result.results[0].result["monthly_saving_cents"] == 0
    assert result.results[0].result["annual_saving_cents"] == 0
