import asyncio

from safebank_contracts.plan import PlanDraft

from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.preflight import stage as preflight
from services.gateway.app.pipeline.snapshot import stage as snapshot
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step


def context(*steps, profile="B2"):
    return EvalContext(
        customer_id="cus_a",
        session_id="ses_a",
        plan_id="pln_a",
        origin="agent",
        profile=profile,
        plan_draft=PlanDraft(
            request_id="req_a",
            session_id="ses_a",
            utterance_ids=("utt_a",),
            intents=(),
            steps=steps,
            rationale="",
        ),
    )


def test_snapshot_reuses_entity_cache():
    async def run():
        fake = FakeL3()
        fake.set_snapshot(banking_state())
        client = L3Client("http://bank", fake.token, transport=fake.transport())
        ctx = context(step())
        ctx.l3 = client
        first = await ctx.bank_state()
        await snapshot.run(ctx)
        assert ctx.snapshot is first
        assert len([r for r in fake.calls if r["op"] == "snapshot"]) == 1
        await client.close()

    asyncio.run(run())


def test_b2_accumulates_while_b1_checks_independently():
    async def run():
        for profile, executable in (("B1", True), ("B2", False)):
            ctx = context(
                step(step_id="s1", amount_cents=400000),
                step(step_id="s2", amount_cents=400000),
                profile=profile,
            )
            ctx.snapshot = banking_state()
            await preflight.run(ctx)
            assert ctx.preflight.executable is executable
        assert ctx.snapshot.accounts["acc_a"].available_cents == 600000
        assert any(f.code == "INSUFFICIENT_FUNDS" for f in ctx.findings)

    asyncio.run(run())


def test_missing_shared_reserve_cannot_silently_allow_spending():
    import pytest

    from services.gateway.app.errors import GatewayError

    async def run():
        ctx = context(step(amount_cents=100))
        ctx.snapshot = banking_state()
        with pytest.raises(GatewayError) as exc:
            await preflight.run(ctx)
        assert exc.value.code == "DEPENDENCY_UNAVAILABLE"
        assert exc.value.status == 503

    asyncio.run(run())
