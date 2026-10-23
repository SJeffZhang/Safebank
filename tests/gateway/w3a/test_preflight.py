"""Stage ⑥ preflight: cumulative / single-step, reserve, thresholds, irreversibility."""

import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline.preflight import PreflightStage
from services.gateway.app.schemas.enums import FindingEffect
from tests.gateway.w3a.conftest import build_ctx, run_entity, transfer, w3_state


def run(ctx, *, profile="B2", reserve=None):
    ctx.profile = profile

    async def execute():
        await PreflightStage(reserve=reserve).run(ctx)

    asyncio.run(execute())
    return ctx


def scene2_steps():
    a = transfer("pye_b", 300000, step_id="s1")
    b = transfer(
        "pye_ext",
        400000,
        step_id="s2",
    )
    b["depends_on"] = ["s1"]
    return [a, b]


def test_cumulative_step2_insufficient_with_hint_295500():
    ctx = run_entity(build_ctx(scene2_steps()))
    run(ctx)
    assert ctx.preflight.executable is False
    insufficient = [f for f in ctx.findings if f.code is Code.INSUFFICIENT_FUNDS]
    assert len(insufficient) == 1
    assert insufficient[0].effect is FindingEffect.REPLAN
    assert insufficient[0].resolver == "user"
    assert ctx.preflight.hint_max_outflow["s2"] == 295500


def test_b1_single_step_checks_each_against_original():
    ctx = run_entity(build_ctx(scene2_steps()))
    run(ctx, profile="B1")
    assert ctx.preflight.executable is True
    assert not any(f.code is Code.INSUFFICIENT_FUNDS for f in ctx.findings)
    assert ctx.preflight.hint_max_outflow == {}


def test_shortfall_hint_attributes_the_over_limit_step_only():
    # available=600000, reserve=4500 (max_outflow=595500). Two transfers total
    # 598000 succeed in bankcore but grow the shortfall; the hint must shrink the
    # *second* step (which crossed the boundary) to 295500, never enlarge s1.
    a = transfer("pye_b", 300000, step_id="s1")
    b = transfer("pye_ext", 298000, step_id="s2")
    b["depends_on"] = ["s1"]
    ctx = run_entity(build_ctx([a, b]))
    run(ctx)
    assert ctx.preflight.executable is True
    shortfall = [f for f in ctx.findings if f.code is Code.SCHEDULED_SHORTFALL]
    assert len(shortfall) == 1
    assert shortfall[0].step_id == "s2"
    assert ctx.preflight.hint_max_outflow == {"s2": 295500}


def test_shrunk_hint_repreflights_successfully():
    # The shrunk plan (s1=300000, s2=295500) must preflight cleanly, proving the
    # hint is a real executable shrink, not just a printed number.
    a = transfer("pye_b", 300000, step_id="s1")
    b = transfer("pye_ext", 295500, step_id="s2")
    b["depends_on"] = ["s1"]
    ctx = run_entity(build_ctx([a, b]))
    run(ctx)
    assert ctx.preflight.executable is True
    assert not any(
        f.code in (Code.INSUFFICIENT_FUNDS, Code.SCHEDULED_SHORTFALL) for f in ctx.findings
    )


def test_amount_above_yellow_cap_escalates_red():
    ctx = run_entity(build_ctx([transfer("pye_b", 500001)]))
    run(ctx)
    assert any(
        f.code is Code.AMOUNT_ABOVE_YELLOW_CAP and f.effect is FindingEffect.RISK_RED
        for f in ctx.findings
    )


def test_scheduled_shortfall_when_balance_drops_below_reserve():
    from safebank_bankcore.ledger import replace_state

    state = w3_state()
    # Raise the card single limit so a 598,000 transfer passes the card check
    # and exercises the L2 reserve policy (600,000 - 4,500 reserve = 595,500 max).
    card = state.cards["crd_a"].model_copy(update={"single_limit_cents": 600_000})
    state = replace_state(state, cards=dict(state.cards) | {card.id: card})
    ctx = run_entity(build_ctx([transfer("pye_b", 598000)], state=state))
    run(ctx)
    assert ctx.preflight.executable is True  # bankcore allows it; L2 policy flags it
    assert any(f.code is Code.SCHEDULED_SHORTFALL and f.resolver == "user" for f in ctx.findings)
    assert ctx.preflight.shortfall_before_cents == 0
    assert ctx.preflight.shortfall_after_cents > 0
    # max_outflow before the plan = 600000 - 4500 = 595500
    assert ctx.preflight.hint_max_outflow["s1"] == 595500


def test_negative_max_outflow_is_signed_and_never_becomes_a_hint():
    ctx = run_entity(build_ctx([transfer("pye_b", 400000)], state=w3_state(reserve=700000)))
    run(ctx)
    # reserve 700000 > available 600000: the projection is still computed (signed),
    # but a negative max_outflow never yields a usable shrink hint.
    assert ctx.preflight.scheduled_reserve_cents == 700000
    assert not ctx.preflight.hint_max_outflow


def test_missing_obligations_fails_closed():
    ctx = run_entity(build_ctx([transfer("pye_b", 100)]))
    ctx._lazy_state = w3_state()
    from safebank_bankcore.ledger import replace_state

    ctx._lazy_state = replace_state(ctx._lazy_state, obligations=None)
    with pytest.raises(GatewayError) as exc:
        run(ctx)
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
    assert exc.value.status == 503


def test_irreversible_step_is_red_not_failure():
    from tests.contracts.samples import step as raw_step

    ctx = run_entity(build_ctx([raw_step("report_card_lost", card_id="crd_a", replace=False)]))
    run(ctx)
    assert ctx.preflight.executable is True
    assert any(
        f.code is Code.IRREVERSIBLE_STEP and f.effect is FindingEffect.RISK_RED
        for f in ctx.findings
    )


def test_unsupported_domain_write_is_tool_not_allowed():
    from tests.contracts.samples import step as raw_step

    sched = raw_step(
        "schedule_transfer",
        from_account_id="acc_a",
        payee_id="pye_ext",
        to_account_id=None,
        amount_cents=100,
        period="MONTHLY",
        first_run_at="2026-11-01T09:00:00+08:00",
        end_at=None,
    )
    ctx = run_entity(build_ctx([sched]))
    run(ctx)
    assert any(f.code is Code.TOOL_NOT_ALLOWED for f in ctx.findings)
