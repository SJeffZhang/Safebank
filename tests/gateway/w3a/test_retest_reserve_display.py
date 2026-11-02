"""Uncomputed reserve must not masquerade as a real zero on approval views."""

import asyncio

import pytest
from safebank_bankcore.banking import apply_plan
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanBody
from safebank_contracts.views import SubscriptionView

from services.agent.app.gateway.wire import GatewayPreflight
from services.gateway.app.pipeline.decision import DecisionStage
from services.gateway.app.pipeline.preflight import PreflightStage
from tests.bankcore.test_funds import funds_state
from tests.contracts.samples import step
from tests.gateway.w3a.conftest import build_ctx, run_entity, transfer, w3_state


def evaluate(steps, state, *, profile="B2", reserve=None):
    ctx = run_entity(build_ctx(steps, state=state))
    ctx.profile = profile
    asyncio.run(PreflightStage(reserve=reserve).run(ctx))
    return ctx, DecisionStage._preflight_view(ctx).model_dump(mode="json")


def test_cancel_displays_known_before_reserve_without_claiming_after_value():
    state = w3_state()
    state = state.model_copy(
        update={
            "subscriptions": (
                SubscriptionView(
                    instruction_id="si_x",
                    billing_id="bill_x",
                    account_id="acc_a",
                    merchant_id="mer_x",
                    merchant_name="会员",
                    period="MONTHLY",
                    amount_cents=4500,
                    max_amount_cents=4600,
                    next_due_at=state.obligations.billings["bill_x"].next_due_at,
                    status="ACTIVE",
                    annual_cost_cents=54000,
                ),
            )
        }
    )
    before = state.model_dump(mode="json")
    ctx, view = evaluate([step("cancel_subscription", instruction_id="si_x")], state)
    assert ctx.preflight.executable
    assert view["scheduled_reserve_cents"] == 4500
    assert view.get("reserve_projection_available") is True
    assert state.model_dump(mode="json") == before
    assert not any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)


def test_mmf_redemption_displays_existing_reserve_with_same_before_semantics():
    state = funds_state().model_copy(update={"obligations": w3_state().obligations})
    buy = apply_plan(
        state,
        PlanBody(
            intents=(),
            rationale="setup",
            steps=[step("subscribe_product", product_id="prd_mmf", amount_cents=1000000)],
        ).steps,
        "cus_a",
        "pln_setup",
    )
    assert buy.error is None
    ctx, view = evaluate(
        [step("redeem_product", product_id="prd_mmf", units="1000.0000")], buy.state
    )
    assert ctx.preflight.executable
    assert view["scheduled_reserve_cents"] == 4500
    assert view.get("reserve_projection_available") is True
    assert not any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)


def test_actual_zero_reserve_is_distinct_from_missing_projection():
    state = w3_state()
    obligations = state.obligations.model_copy(update={"billings": {}, "standing_instructions": {}})
    _, view = evaluate([step("freeze_card")], state.model_copy(update={"obligations": obligations}))
    assert view["scheduled_reserve_cents"] == 0
    assert view.get("reserve_projection_available") is True
    assert (
        GatewayPreflight.model_validate(view).model_dump()["reserve_projection_available"] is True
    )


@pytest.mark.parametrize("profile,missing", [("B2", True), ("B1", False)])
def test_protective_action_unavailable_or_b1_keeps_zero_explicitly_uncomputed(profile, missing):
    state = w3_state().model_copy(update={"obligations": None}) if missing else w3_state()
    ctx, view = evaluate([step("freeze_card")], state, profile=profile)
    assert ctx.preflight.executable
    assert view["scheduled_reserve_cents"] == 0
    assert view.get("reserve_projection_available") is False
    assert (
        GatewayPreflight.model_validate(view).model_dump()["reserve_projection_available"] is False
    )


def test_failed_transfer_keeps_real_failure_when_display_projection_is_unavailable():
    def unavailable(*args, **kwargs):
        raise ValueError("incomplete obligations")

    ctx, view = evaluate([transfer("pye_b", 99999999)], w3_state(), reserve=unavailable)
    assert not ctx.preflight.executable
    assert any(f.code is Code.CARD_SINGLE_LIMIT for f in ctx.findings)
    assert view.get("reserve_projection_available") is False
