import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.pipeline.context import (
    ResolvedStep,
    Resolver,
)
from services.gateway.app.pipeline.decision import stage
from services.gateway.app.schemas.enums import FindingEffect
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step
from tests.gateway.w3.test_preflight import context


@pytest.mark.parametrize(
    "effect,expected,risk",
    [
        (None, "REQUIRE_APPROVAL", "YELLOW"),
        (FindingEffect.RISK_RED, "REQUIRE_APPROVAL", "RED"),
        (FindingEffect.REPLAN, "REPLAN", "YELLOW"),
        (FindingEffect.DENY, "DENY", "YELLOW"),
    ],
)
def test_priority_and_base_risk(effect, expected, risk):
    async def run():
        ctx = context(step())
        ctx.snapshot = banking_state()
        ctx.steps = (
            ResolvedStep(
                "s1",
                "transfer",
                ctx.plan_draft.steps[0].args.model_dump(),
                "TRANSFER_EXTERNAL",
                "YELLOW",
                True,
            ),
        )
        if effect:
            ctx.add(
                Code.INSUFFICIENT_FUNDS,
                effect,
                resolver=Resolver.USER if effect == FindingEffect.REPLAN else None,
            )
        await stage.run(ctx)
        assert ctx.decision.decision == expected
        assert ctx.decision.risk == risk

    asyncio.run(run())


def test_b1_single_threshold_is_independent_of_workflow():
    async def run():
        ctx = context(step(amount_cents=600000), profile="B1")
        ctx.snapshot = banking_state()
        ctx.steps = (
            ResolvedStep(
                "s1",
                "transfer",
                ctx.plan_draft.steps[0].args.model_dump(),
                "TRANSFER_EXTERNAL",
                "YELLOW",
                True,
            ),
        )
        await stage.run(ctx)
        assert ctx.decision.risk == "RED"
        ctx.profile = "B0"
        ctx.findings = []
        await stage.run(ctx)
        assert ctx.decision.risk == "YELLOW"

    asyncio.run(run())
