"""Stage ⑧ decision: aggregation priority, risk, hints, shadow B1."""

import asyncio
from datetime import UTC, datetime, timedelta

from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk
from safebank_contracts.plan import PlanDraft

from services.gateway.app.pipeline.context import (
    EvalContext,
    EvalMode,
    PreflightResult,
    ResolvedStep,
    Resolver,
)
from services.gateway.app.pipeline.decision import DecisionStage
from services.gateway.app.schemas.decision import DecisionValue
from services.gateway.app.schemas.enums import (
    FindingEffect,
    ParseStatus,
    UtteranceStatus,
)
from services.gateway.app.schemas.scope import (
    AuthorizationInputs,
    NormalizedScope,
    ScopeTransfer,
    UtteranceRef,
)
from tests.gateway.w3a.conftest import draft, transfer, w3_state


def decision_ctx(*, profile="B2", mode=EvalMode.EVALUATE, plan_id="pln_1"):
    ctx = EvalContext(
        plan_draft=PlanDraft.model_validate(draft([transfer("pye_ext", 400000)])),
        customer_id="cus_a",
        session_id="sid_1",
        plan_id=plan_id,
        origin="agent",
        profile=profile,
        mode=mode,
    )
    ctx._lazy_state = w3_state()
    ctx.snapshot = w3_state()
    ctx.steps = (
        ResolvedStep(
            "s1", "transfer", {"amount_cents": 400000}, "TRANSFER_EXTERNAL", "YELLOW", True
        ),
    )
    ctx.preflight = PreflightResult()
    ctx.wall_now = lambda: datetime.now(UTC)
    return ctx


def run(ctx):
    async def execute():
        await DecisionStage().run(ctx)

    asyncio.run(execute())
    return ctx


def test_deny_wins_over_replan():
    ctx = decision_ctx()
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, resolver=Resolver.USER)
    ctx.add(Code.RISKY_SEQUENCE, FindingEffect.DENY)
    run(ctx)
    assert ctx.decision.decision is DecisionValue.DENY


def test_replan_wins_over_approval_and_risk_escalates():
    ctx = decision_ctx()
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, resolver=Resolver.USER)
    ctx.add(Code.AMOUNT_ABOVE_YELLOW_CAP, FindingEffect.RISK_RED)
    run(ctx)
    assert ctx.decision.decision is DecisionValue.REPLAN
    assert ctx.decision.risk is Risk.RED


def test_no_findings_uses_base_risk():
    ctx = decision_ctx()
    run(ctx)
    assert ctx.decision.decision is DecisionValue.REQUIRE_APPROVAL
    assert ctx.decision.risk is Risk.YELLOW


def test_green_base_risk_allows():
    ctx = decision_ctx()
    ctx.steps = (
        ResolvedStep("s1", "freeze_card", {"card_id": "crd_a"}, "CARD_FREEZE", "GREEN", False),
    )
    run(ctx)
    assert ctx.decision.decision is DecisionValue.ALLOW
    assert ctx.decision.risk is Risk.GREEN


def test_replan_hint_shrinks_amount():
    ctx = decision_ctx()
    now = datetime.now(UTC)
    utterance = UtteranceRef(
        id="utt_1",
        kind="typed",
        text="给赵六4000",
        scope=NormalizedScope(
            action_kinds=("TRANSFER_EXTERNAL",),
            transfers=(ScopeTransfer(target_fingerprint="pye_extfp", amount_cents=400000),),
        ),
        parse_status=ParseStatus.SCOPED,
        created_at_wall=now.isoformat(),
        expires_at_wall=(now + timedelta(minutes=10)).isoformat(),
    )
    ctx.authorization = AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_1",),
        utterances={"utt_1": utterance},
        scope_ids=("utt_1",),
        statuses={"utt_1": UtteranceStatus.ACTIVE},
    )
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, step_id="s1", resolver=Resolver.USER)
    ctx.preflight = PreflightResult(hint_max_outflow={"s1": 295500})
    run(ctx)
    assert ctx.decision.decision is DecisionValue.REPLAN
    assert len(ctx.decision.replan_hints) == 1
    hint = ctx.decision.replan_hints[0]
    assert [t.amount_cents for t in hint.scope.transfers] == [295500]


def test_replan_hint_shrinks_only_the_over_limit_step():
    # Two transfers: s1 (300000) must stay, s2 (298000) shrinks to 295500. A
    # hint that enlarged s1 would be rejected (no hint), never emitted.
    from tests.gateway.w3a.conftest import build_ctx

    a = transfer("pye_b", 300000, step_id="s1")
    b = transfer("pye_ext", 298000, step_id="s2")
    b["depends_on"] = ["s1"]
    ctx = build_ctx([a, b], plan_id="pln_two")
    ctx.snapshot = w3_state()
    ctx.steps = (
        ResolvedStep(
            "s1", "transfer", {"amount_cents": 300000}, "TRANSFER_EXTERNAL", "YELLOW", True
        ),
        ResolvedStep(
            "s2", "transfer", {"amount_cents": 298000}, "TRANSFER_EXTERNAL", "YELLOW", True
        ),
    )
    now = datetime.now(UTC)
    utterance = UtteranceRef(
        id="utt_1",
        kind="typed",
        text="转账",
        scope=NormalizedScope(
            action_kinds=("TRANSFER_EXTERNAL", "TRANSFER_EXTERNAL"),
            transfers=(
                ScopeTransfer(target_fingerprint="pye_bfp", amount_cents=300000),
                ScopeTransfer(target_fingerprint="pye_extfp", amount_cents=298000),
            ),
        ),
        parse_status=ParseStatus.SCOPED,
        created_at_wall=now.isoformat(),
        expires_at_wall=(now + timedelta(minutes=10)).isoformat(),
    )
    ctx.authorization = AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_1",),
        utterances={"utt_1": utterance},
        scope_ids=("utt_1",),
        statuses={"utt_1": UtteranceStatus.ACTIVE},
    )
    ctx.add(Code.SCHEDULED_SHORTFALL, FindingEffect.REPLAN, step_id="s2", resolver=Resolver.USER)
    ctx.preflight = PreflightResult(hint_max_outflow={"s2": 295500})
    ctx.wall_now = lambda: datetime.now(UTC)
    run(ctx)
    assert ctx.decision.decision is DecisionValue.REPLAN
    assert len(ctx.decision.replan_hints) == 1
    hint = ctx.decision.replan_hints[0]
    amounts = {t.target_fingerprint: t.amount_cents for t in hint.scope.transfers}
    assert amounts == {"pye_bfp": 300000, "pye_extfp": 295500}


def test_no_hint_when_maximum_would_enlarge():
    ctx = decision_ctx()
    now = datetime.now(UTC)
    utterance = UtteranceRef(
        id="utt_1",
        kind="typed",
        text="转账",
        scope=NormalizedScope(
            action_kinds=("TRANSFER_EXTERNAL",),
            transfers=(ScopeTransfer(target_fingerprint="pye_extfp", amount_cents=100000),),
        ),
        parse_status=ParseStatus.SCOPED,
        created_at_wall=now.isoformat(),
        expires_at_wall=(now + timedelta(minutes=10)).isoformat(),
    )
    ctx.authorization = AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_1",),
        utterances={"utt_1": utterance},
        scope_ids=("utt_1",),
        statuses={"utt_1": UtteranceStatus.ACTIVE},
    )
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, step_id="s1", resolver=Resolver.USER)
    # A "shrink" of 100000 -> 595500 would enlarge: no hint may be produced.
    ctx.preflight = PreflightResult(hint_max_outflow={"s1": 595500})
    run(ctx)
    assert ctx.decision.replan_hints == ()


def test_format_cents_is_integer_arithmetic():
    from services.gateway.app.pipeline.decision import _format_cents

    assert _format_cents(9999999999999999) == "99,999,999,999,999.99 元"
    assert _format_cents(100) == "1.00 元"
    assert _format_cents(295500) == "2,955.00 元"


def test_no_hint_when_taint_present():
    ctx = decision_ctx()
    ctx.add(Code.TAINTED_SENSITIVE_ARG, FindingEffect.DENY)
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, resolver=Resolver.USER)
    ctx.preflight = PreflightResult(hint_max_outflow={"s1": 100})
    run(ctx)
    assert ctx.decision.replan_hints == ()


def test_shadow_b1_computed_only_for_b2_evaluate():
    ctx = decision_ctx(profile="B2", mode=EvalMode.EVALUATE)
    ctx.add(Code.INSUFFICIENT_FUNDS, FindingEffect.REPLAN, resolver=Resolver.USER)
    run(ctx)
    assert ctx.decision.shadow_b1 is not None

    ctx = decision_ctx(profile="B1", mode=EvalMode.EVALUATE)
    run(ctx)
    assert ctx.decision.shadow_b1 is None


def test_policy_version_covers_all_policy_files():
    ctx = decision_ctx()
    run(ctx)
    pv = ctx.decision.policy_version
    assert len(pv) == 64 and all(c in "0123456789abcdef" for c in pv)
