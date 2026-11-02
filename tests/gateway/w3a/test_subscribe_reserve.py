"""Coordinator reproduction: valid actual fund preflight must retain upcoming bill reserve."""

import asyncio

import pytest
from safebank_bankcore.banking import apply_plan
from safebank_bankcore.reserve import projected_shortfall
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft

from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.preflight import PreflightStage
from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.bankcore.test_funds import funds_state
from tests.contracts.samples import step


@pytest.mark.parametrize("product", ["prd_mmf", "prd_bond"])
def test_actual_subscribe_that_grows_legitimate_obligation_shortfall_must_replan(product):
    state = funds_state()
    obligations = raw_projection_state(
        billings=[raw_billing()], mandates=[raw_mandate()], merchants=[raw_merchant()]
    ).obligations
    state = state.model_copy(update={"obligations": obligations})
    plan = PlanDraft(
        request_id="req_reserve_probe",
        session_id="ses_probe",
        utterance_ids=["act_probe"],
        intents=[],
        rationale="",
        steps=[step("subscribe_product", product_id=product, amount_cents=2000000)],
    )
    simulated = apply_plan(state, plan.steps, "cus_a", "pln_reserve_probe")
    assert simulated.error is None
    assert projected_shortfall(state).total_shortfall_cents == 0
    assert projected_shortfall(simulated.state).total_shortfall_cents == 3000
    ctx = EvalContext(
        plan_draft=plan,
        plan_id="pln_reserve_probe",
        origin="app_form",
        customer_id="cus_a",
        session_id="ses_probe",
    )
    ctx._lazy_state = state

    async def run():
        await EntityStage().run(ctx)
        await PreflightStage().run(ctx)

    asyncio.run(run())
    assert ctx.preflight.executable
    assert any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)


@pytest.mark.parametrize("product", ["prd_mmf", "prd_bond"])
def test_safe_subscribe_still_displays_real_reserve_without_changing_amount(product):
    state = funds_state().model_copy(
        update={
            "obligations": raw_projection_state(
                billings=[raw_billing()], mandates=[raw_mandate()], merchants=[raw_merchant()]
            ).obligations
        }
    )
    plan = PlanDraft(
        request_id="req_safe",
        session_id="ses_safe",
        utterance_ids=["act_safe"],
        intents=[],
        rationale="",
        steps=[step("subscribe_product", product_id=product, amount_cents=10000)],
    )
    ctx = EvalContext(
        plan_draft=plan,
        plan_id="pln_safe",
        origin="app_form",
        customer_id="cus_a",
        session_id="ses_safe",
    )
    ctx._lazy_state = state

    async def run():
        await EntityStage().run(ctx)
        await PreflightStage().run(ctx)

    asyncio.run(run())
    assert not any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)
    assert ctx.preflight.scheduled_reserve_cents == 3000
    assert ctx.plan_draft.steps[0].args.amount_cents == 10000


def test_fund_without_authoritative_obligations_does_not_skip_projection_failure():
    from services.gateway.app.errors import GatewayError

    state = funds_state().model_copy(update={"obligations": None})
    plan = PlanDraft(
        request_id="req_missing",
        session_id="ses_missing",
        utterance_ids=["act_missing"],
        intents=[],
        rationale="",
        steps=[step("subscribe_product", product_id="prd_mmf", amount_cents=10000)],
    )
    ctx = EvalContext(
        plan_draft=plan,
        plan_id="pln_missing",
        origin="app_form",
        customer_id="cus_a",
        session_id="ses_missing",
    )
    ctx._lazy_state = state

    async def run():
        await EntityStage().run(ctx)
        with pytest.raises(GatewayError) as caught:
            await PreflightStage().run(ctx)
        assert caught.value.code is Code.DEPENDENCY_UNAVAILABLE

    asyncio.run(run())


def test_new_scheduled_outflow_is_compared_with_existing_obligations():
    from datetime import timedelta

    from tests.gateway.w3a.conftest import build_ctx, run_entity, w3_state

    state = w3_state()
    card = state.cards["crd_a"].model_copy(update={"single_limit_cents": 600000})
    state = state.model_copy(update={"cards": {**state.cards, card.id: card}})
    item = step(
        "schedule_transfer",
        from_account_id="acc_a",
        payee_id="pye_b",
        amount_cents=598000,
        period="ONCE",
        first_run_at=(state.sim_now + timedelta(days=1)).isoformat(),
    )
    ctx = run_entity(build_ctx([item], state=state))
    asyncio.run(PreflightStage().run(ctx))
    assert ctx.preflight.executable
    assert any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)
    assert ctx.preflight.scheduled_reserve_cents == 4500
    assert ctx.plan_draft.steps[0].args.amount_cents == 598000
