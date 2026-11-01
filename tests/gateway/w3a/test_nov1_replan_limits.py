"""A shrink hint must satisfy all current transfer constraints, not the first one."""

from copy import deepcopy

import pytest
from safebank_bankcore.banking import apply_plan
from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.state import Counters

from tests.gateway.w3a.conftest import build_ctx, run_entity, transfer, w3_state
from tests.gateway.w3a.test_preflight import run


@pytest.mark.parametrize(
    "single,daily,used,outflow,reserve,expected",
    [
        (800000, 2000000, 0, 0, 4500, 595500),
        (800000, 1000000, 900000, 0, 4500, 100000),
        (800000, 2000000, 0, 4999000, 4500, 1000),
        (800000, 2000000, 0, 0, 590000, 10000),
    ],
)
def test_card_single_failure_hint_also_respects_balance_reserve_and_remaining_limits(
    single, daily, used, outflow, reserve, expected
):
    before = w3_state(reserve=reserve)
    card = before.cards["crd_a"].model_copy(
        update={"single_limit_cents": single, "daily_limit_cents": daily}
    )
    state = replace_state(
        before,
        cards=dict(before.cards) | {card.id: card},
        counters=Counters(daily_used_cents={"acc_a": used}, daily_outflow_cents={"cus_a": outflow}),
    )
    snapshot = deepcopy(state.model_dump(mode="json"))
    ctx = run_entity(build_ctx([transfer("pye_ext", 900000)], state=state))
    run(ctx)
    assert any(f.code is Code.CARD_SINGLE_LIMIT for f in ctx.findings)
    assert ctx.preflight.hint_max_outflow == {"s1": expected}
    shrunk = ctx.plan_draft.steps[0].model_copy(
        update={"args": ctx.plan_draft.steps[0].args.model_copy(update={"amount_cents": expected})}
    )
    assert apply_plan(state, [shrunk], "cus_a", ctx.plan_id).error is None
    assert state.model_dump(mode="json") == snapshot


def test_failed_reserve_projection_never_fabricates_a_card_limit_hint():
    ctx = run_entity(build_ctx([transfer("pye_ext", 900000)]))

    def unavailable(*_args, **_kwargs):
        raise ValueError("incomplete obligations")

    run(ctx, reserve=unavailable)
    assert any(f.code is Code.CARD_SINGLE_LIMIT for f in ctx.findings)
    assert not ctx.preflight.hint_max_outflow


def test_hint_is_omitted_if_later_approved_step_would_still_fail():
    before = w3_state()
    card = before.cards["crd_a"].model_copy(update={"single_limit_cents": 800000})
    state = replace_state(before, cards=dict(before.cards) | {card.id: card})
    steps = [transfer("pye_ext", 900000), transfer("pye_b", 10000, step_id="s2")]
    steps[1]["depends_on"] = ["s1"]
    ctx = run_entity(build_ctx(steps, state=state))
    run(ctx)
    assert not ctx.preflight.hint_max_outflow
