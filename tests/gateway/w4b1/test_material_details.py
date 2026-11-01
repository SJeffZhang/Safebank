"""G9 material disclosures bind fees/calendar facts to the visible view hash."""

from datetime import datetime, timedelta

import pytest
from safebank_bankcore.banking import Context
from safebank_bankcore.funds import apply_fund_confirm
from safebank_bankcore.ledger import replace_state
from safebank_contracts.enums import Risk

from services.gateway.app.approval.planview import build_view
from services.gateway.app.pipeline.context import ResolvedStep
from services.gateway.app.schemas.decision import PreflightView
from tests.bankcore.test_funds import funds_state, simulate
from tests.contracts.samples import step
from tests.gateway.w4b1.conftest import make_ctx
from tests.gateway.w4b1.conftest import state as panel_state


def field(view, key):
    return next(item for item in view.steps[0].fields if item.key == key)


def cancel_ctx(*, amount=19900, period="MONTHLY", status="ACTIVE"):
    state = panel_state()
    subscription = state.subscriptions[0].model_copy(
        update={"amount_cents": amount, "period": period, "status": status}
    )
    from safebank_contracts.obligations import ObligationSnapshot

    from tests.bankcore.obligations_fixtures import raw_billing, raw_mandate, raw_merchant

    billing = raw_billing(
        id="bil_gym",
        merchant_id="mer_gym",
        amount_cents=amount,
        period=period,
        anchor_day=18 if period == "MONTHLY" else None,
        weekday=6 if period == "WEEKLY" else None,
    )
    mandate = raw_mandate(
        id="si_gym",
        billing_id="bil_gym",
        merchant_id="mer_gym",
        max_amount_cents=amount,
        status="ACTIVE" if status == "ACTIVE" else "DELETED",
    )
    obligations = ObligationSnapshot(
        covered_customer_ids=("cus_a",),
        merchants={"mer_gym": raw_merchant(id="mer_gym")},
        billings={billing.id: billing},
        standing_instructions={mandate.id: mandate},
        runs=(),
        instruction_targets={},
    )
    state = state.model_copy(update={"subscriptions": (subscription,), "obligations": obligations})
    resolved = ResolvedStep(
        "cancel",
        "cancel_subscription",
        {"instruction_id": subscription.instruction_id},
        "MANDATE_CANCEL",
        "YELLOW",
        False,
    )
    return make_ctx((resolved,), snapshot=state)


@pytest.mark.parametrize(
    "period,amount,expected", [("MONTHLY", 19900, "199.00"), ("WEEKLY", 3000, "130.00")]
)
def test_cancel_panel_binds_normalized_monthly_saving(period, amount, expected):
    view, _ = build_view(cancel_ctx(period=period, amount=amount))
    saving = field(view, "monthly_saving")
    assert saving.label == "预计每月节省" and saving.value == expected and saving.unit == "元"


@pytest.mark.parametrize("status", ["DELETED", "PAUSED", "EXPIRED"])
def test_inactive_subscription_never_promises_new_savings(status):
    assert field(build_view(cancel_ctx(status=status))[0], "monthly_saving").value == "0.00"


def test_fee_or_subscription_state_change_invalidates_view_hash():
    _, original = build_view(cancel_ctx())
    assert build_view(cancel_ctx(amount=20000))[1] != original
    assert build_view(cancel_ctx(status="DELETED"))[1] != original


def fund_view(state, tool, product_id, *, profile="B2"):
    args = {"account_id": "acc_a", "product_id": product_id}
    args.update({"amount_cents": 10000} if tool == "subscribe_product" else {"units": "1.0000"})
    resolved = ResolvedStep(
        "fund",
        tool,
        args,
        "FUND_SUBSCRIBE" if tool == "subscribe_product" else "FUND_REDEEM",
        "YELLOW",
        False,
    )
    ctx = make_ctx((resolved,), snapshot=state, risk=Risk.YELLOW)
    ctx.profile = profile
    return build_view(ctx)


@pytest.mark.parametrize(
    "now_iso",
    [
        "2026-10-16T14:59:59+08:00",
        "2026-10-16T15:00:00+08:00",
        "2026-10-17T10:00:00+08:00",
        "2026-10-18T23:59:00+08:00",
        "2026-10-16T06:59:59+00:00",
    ],
)
@pytest.mark.parametrize("product_id", ["prd_bond", "prd_mmf"])
@pytest.mark.parametrize(
    "tool,key",
    [("subscribe_product", "expected_confirm_at"), ("redeem_product", "expected_settle_at")],
)
def test_panel_expected_date_equals_actual_apply_plan_order(now_iso, product_id, tool, key):
    state = funds_state()
    if tool == "redeem_product":
        buy = simulate(
            state,
            [step("subscribe_product", product_id=product_id, amount_cents=10000)],
            plan_id="pln_setup",
        )
        assert buy.error is None
        order = buy.state.fund_orders[0]
        state = buy.state
        if order.status != "SETTLED":
            state, _, errors = apply_fund_confirm(
                state, order.id, order.confirm_at, Context("cus_a", "pln_setup_confirm", "confirm")
            )
            assert not errors
    now = datetime.fromisoformat(now_iso)
    state = replace_state(state, sim_now=now)
    args = {
        "product_id": product_id,
        **({"amount_cents": 10000} if tool == "subscribe_product" else {"units": "1.0000"}),
    }
    actual = simulate(state, [step(tool, **args)], plan_id="pln_date_comparison")
    assert actual.error is None
    order = next(
        order for order in actual.state.fund_orders if order.plan_id == "pln_date_comparison"
    )
    expected = order.confirm_at if tool == "subscribe_product" else order.settle_at
    view, _ = fund_view(state, tool, product_id)
    date_field = field(view, key)
    assert date_field.unit == "datetime"
    assert datetime.fromisoformat(date_field.value) == expected
    if product_id == "prd_mmf":
        assert expected == now


@pytest.mark.parametrize("profile", ["B0", "B2", "B2_NO_PREFLIGHT"])
def test_date_fields_use_current_product_and_clock_without_preflight_results(profile):
    state = replace_state(
        funds_state(), sim_now=datetime.fromisoformat("2026-10-16T15:00:00+08:00")
    )
    view, _ = fund_view(state, "subscribe_product", "prd_bond", profile=profile)
    assert field(view, "expected_confirm_at").value == "2026-10-20T09:00:00+08:00"


def test_fund_business_date_or_product_delay_change_invalidates_hash():
    state = replace_state(
        funds_state(), sim_now=datetime.fromisoformat("2026-10-16T14:59:59+08:00")
    )
    _, before = fund_view(state, "subscribe_product", "prd_bond")
    later = replace_state(state, sim_now=datetime.fromisoformat("2026-10-16T15:00:00+08:00"))
    assert fund_view(later, "subscribe_product", "prd_bond")[1] != before
    products = tuple(
        product.model_copy(update={"confirm_days": 2}) if product.id == "prd_bond" else product
        for product in state.products
    )
    assert (
        fund_view(replace_state(state, products=products), "subscribe_product", "prd_bond")[1]
        != before
    )


def test_unknown_fund_product_still_fails_closed():
    with pytest.raises(ValueError):
        fund_view(funds_state(), "subscribe_product", "prd_missing")


def test_daily_used_before_after_are_already_bound_by_semantic_hash():
    def view(before, after):
        preflight = PreflightView(
            executable=True,
            balances=(
                {
                    "account_id": "acc_a",
                    "balance_before_cents": 600000,
                    "balance_after_cents": 600000,
                    "available_before_cents": 600000,
                    "available_after_cents": 600000,
                    "daily_used_before_cents": before,
                    "daily_used_after_cents": after,
                },
            ),
        )
        ctx = cancel_ctx()
        ctx.decision = ctx.decision.model_copy(update={"preflight": preflight})
        return build_view(ctx)

    base = view(10000, 20000)[1]
    assert view(10001, 20000)[1] != base and view(10000, 20001)[1] != base


def test_inactive_authoritative_billing_cannot_promise_savings_from_active_view():
    from safebank_contracts.obligations import ObligationSnapshot

    from tests.bankcore.obligations_fixtures import raw_billing, raw_mandate, raw_merchant

    ctx = cancel_ctx()
    billing = raw_billing(id="bil_gym", merchant_id="mer_gym", amount_cents=19900, active=False)
    mandate = raw_mandate(
        id="si_gym", billing_id="bil_gym", merchant_id="mer_gym", max_amount_cents=19900
    )
    obligations = ObligationSnapshot(
        covered_customer_ids=("cus_a",),
        merchants={"mer_gym": raw_merchant(id="mer_gym")},
        billings={billing.id: billing},
        standing_instructions={mandate.id: mandate},
        runs=(),
        instruction_targets={},
    )
    ctx.snapshot = ctx.snapshot.model_copy(update={"obligations": obligations})
    assert field(build_view(ctx)[0], "monthly_saving").value == "0.00"


def test_missing_raw_billing_never_promises_positive_monthly_savings():
    ctx = cancel_ctx()
    ctx.snapshot = ctx.snapshot.model_copy(update={"obligations": None})
    saving = field(build_view(ctx)[0], "monthly_saving")
    assert saving.value == "暂无法确定" and saving.unit is None


@pytest.mark.parametrize(
    "tool,metadata", [("subscribe_product", "confirm_days"), ("redeem_product", "settle_days")]
)
@pytest.mark.parametrize("invalid", [None, -1, "1", True])
def test_invalid_fund_date_metadata_cannot_create_an_approval_view(tool, metadata, invalid):
    state = funds_state()
    products = tuple(
        product.model_copy(update={metadata: invalid}) if product.id == "prd_bond" else product
        for product in state.products
    )
    with pytest.raises(ValueError):
        fund_view(replace_state(state, products=products), tool, "prd_bond")


def test_mismatched_raw_billing_identity_fails_closed_instead_of_borrowing_fee():
    ctx = cancel_ctx()
    obligations = ctx.snapshot.obligations
    mandates = {
        id_: mandate.model_copy(update={"billing_id": "bil_other"})
        for id_, mandate in obligations.standing_instructions.items()
    }
    ctx.snapshot = ctx.snapshot.model_copy(
        update={"obligations": obligations.model_copy(update={"standing_instructions": mandates})}
    )
    with pytest.raises(ValueError):
        build_view(ctx)


@pytest.mark.parametrize(
    "invalid",
    ["cap", "end_before_due", "expired", "future_mandate", "future_billing", "past_due"],
)
def test_ineligible_next_charge_never_promises_monthly_saving(invalid):
    ctx = cancel_ctx()
    state = ctx.snapshot
    snap = state.obligations
    mandate = snap.standing_instructions["si_gym"]
    billing = snap.billings["bil_gym"]
    if invalid == "cap":
        mandate = mandate.model_copy(update={"max_amount_cents": 100})
    elif invalid == "end_before_due":
        mandate = mandate.model_copy(update={"end_at": billing.next_due_at - timedelta(seconds=1)})
    elif invalid == "expired":
        mandate = mandate.model_copy(update={"end_at": state.sim_now - timedelta(seconds=1)})
    elif invalid == "future_mandate":
        mandate = mandate.model_copy(update={"created_at": state.sim_now + timedelta(seconds=1)})
    elif invalid == "future_billing":
        billing = billing.model_copy(update={"created_at": state.sim_now + timedelta(seconds=1)})
    else:
        state = state.model_copy(update={"sim_now": billing.next_due_at + timedelta(seconds=1)})
    snap = snap.model_copy(
        update={"standing_instructions": {mandate.id: mandate}, "billings": {billing.id: billing}}
    )
    ctx.snapshot = state.model_copy(update={"obligations": snap})
    saving = field(build_view(ctx)[0], "monthly_saving")
    assert saving.value == "0.00" and saving.unit == "元"


@pytest.mark.parametrize("missing", ["coverage", "merchant", "account", "duplicate_mandate"])
def test_unverified_raw_sources_never_promise_monthly_saving(missing):
    ctx = cancel_ctx()
    state, snap = ctx.snapshot, ctx.snapshot.obligations
    if missing == "coverage":
        snap = snap.model_copy(update={"covered_customer_ids": ("cus_other",)})
    elif missing == "merchant":
        snap = snap.model_copy(update={"merchants": {}})
    elif missing == "account":
        state = state.model_copy(update={"accounts": {}})
    else:
        mandate = snap.standing_instructions["si_gym"].model_copy(update={"id": "si_other"})
        snap = snap.model_copy(
            update={"standing_instructions": {**snap.standing_instructions, mandate.id: mandate}}
        )
    ctx.snapshot = state.model_copy(update={"obligations": snap})
    saving = field(build_view(ctx)[0], "monthly_saving")
    assert saving.value == "暂无法确定" and saving.unit is None


def test_next_charge_at_exact_mandate_end_keeps_valid_saving():
    ctx = cancel_ctx()
    snap = ctx.snapshot.obligations
    mandate = snap.standing_instructions["si_gym"].model_copy(
        update={"end_at": snap.billings["bil_gym"].next_due_at}
    )
    ctx.snapshot = ctx.snapshot.model_copy(
        update={
            "obligations": snap.model_copy(update={"standing_instructions": {mandate.id: mandate}})
        }
    )
    assert field(build_view(ctx)[0], "monthly_saving").value == "199.00"


def test_cap_block_is_real_bankcore_outcome_and_changing_eligibility_changes_hash():
    from safebank_bankcore.events import apply_merchant_charge

    ctx = cancel_ctx()
    original_hash = build_view(ctx)[1]
    snap = ctx.snapshot.obligations
    mandate = snap.standing_instructions["si_gym"].model_copy(update={"max_amount_cents": 100})
    ctx.snapshot = ctx.snapshot.model_copy(
        update={
            "obligations": snap.model_copy(update={"standing_instructions": {mandate.id: mandate}})
        }
    )
    _, effects, run = apply_merchant_charge(
        ctx.snapshot,
        billing_id="bil_gym",
        due_at=snap.billings["bil_gym"].next_due_at,
        event_id="evt_cap_negative",
    )
    assert run.status == "BLOCKED" and run.code == "MANDATE_AMOUNT_EXCEEDED" and not effects
    assert field(build_view(ctx)[0], "monthly_saving").value == "0.00"
    assert build_view(ctx)[1] != original_hash
