"""Full-pipeline ``evaluate`` across profiles/ablations and early-DENY paths.

These run the *whole* stage chain (not single stages) so the decision stage is
proven to assemble a stable ``Decision`` even when stage ⑤/⑥ were skipped by a
profile ablation or by an earlier hard DENY — and without re-fetching L3.
"""

import asyncio

from services.gateway.app.pipeline import evaluate
from services.gateway.app.schemas.decision import DecisionValue
from services.gateway.app.schemas.enums import FindingEffect
from tests.gateway.w3a.conftest import build_ctx, transfer


def full_ctx(steps, *, profile="B2", plan_id="pln_1"):
    ctx = build_ctx(steps, plan_id=plan_id)
    ctx.profile = profile
    return ctx


def test_every_profile_and_ablation_produces_a_decision():
    profiles = ["B0", "B1", "B2", "B2_NO_PREFLIGHT", "B2_NO_WORKFLOW", "B2_NO_TAINT"]
    for profile in profiles:
        ctx = full_ctx([transfer("pye_b", 100000)], profile=profile)
        asyncio.run(evaluate(ctx))
        assert ctx.decision is not None, profile
        assert ctx.decision.decision in DecisionValue, profile
        # A profile that skips preflight must not fabricate an executable view.
        if profile in ("B0", "B2_NO_PREFLIGHT"):
            assert ctx.decision.preflight.executable is (not ctx.has_hard_deny())


def test_early_deny_produces_deny_decision_without_crashing():
    # A payee not owned by cus_a makes the entity stage DENY before the snapshot.
    ctx = full_ctx([transfer("pye_not_owned", 100000)])
    asyncio.run(evaluate(ctx))
    assert ctx.decision is not None
    assert ctx.decision.decision is DecisionValue.DENY
    assert any(f.effect is FindingEffect.DENY for f in ctx.findings)
    # The plan was rejected, so the preflight view must not report executable.
    assert ctx.decision.preflight.executable is False


def test_early_deny_needs_no_l3_client():
    # Even when no L3 client is wired at all, a cached snapshot plus an early
    # DENY must still yield a DENY Decision (never a 503/500 from re-fetching).
    ctx = full_ctx([transfer("pye_not_owned", 100000)])
    ctx.l3 = None
    asyncio.run(evaluate(ctx))
    assert ctx.decision is not None
    assert ctx.decision.decision is DecisionValue.DENY


def test_b0_deny_path_does_not_crash():
    # B0 disables provenance/intent/preflight/workflow; an entity DENY still
    # reaches decision with ctx.preflight is None.
    ctx = full_ctx([transfer("pye_not_owned", 100000)], profile="B0")
    asyncio.run(evaluate(ctx))
    assert ctx.decision is not None
    assert ctx.decision.decision is DecisionValue.DENY
    assert ctx.decision.preflight.executable is False
