import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.state import Operation

from services.gateway.app.pipeline.context import ResolvedStep
from services.gateway.app.pipeline.workflow import stage
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step
from tests.gateway.w3.test_preflight import context


def op(kind, target=None, amount=0, *, age=60, seq=1):
    state = banking_state()
    return Operation(
        event_id=f"evt_{kind}_{seq}",
        customer_id="cus_a",
        plan_id="pln_old",
        step_id="s1",
        action_kind=kind,
        target_fingerprint=target,
        amount_cents=amount,
        occurred_at=state.sim_now - timedelta(minutes=age),
        commit_seq=seq,
    )


@pytest.mark.parametrize("reverse,account", [(False, "acc_a"), (True, "acc_a"), (True, "acc_s")])
def test_a3_order_independent_and_cardless(reverse, account):
    async def run():
        events = [op("PAYEE_ADD", "pye_bfp", seq=1), op("CARD_LIMIT_LOOSEN", seq=2)]
        if reverse:
            events = [
                events[1].model_copy(update={"commit_seq": 1}),
                events[0].model_copy(update={"commit_seq": 2}),
            ]
        ctx = context(step(from_account_id=account))
        ctx.snapshot = banking_state().model_copy(update={"ops_7d": tuple(events)})
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
        assert any(f.code == "RISKY_SEQUENCE" and f.effect == "DENY" for f in ctx.findings)
        assert ctx.workflow.cooldowns == (("pye_bfp", ctx.snapshot.sim_now + timedelta(hours=24)),)

    asyncio.run(run())


def test_a1_split_and_customer_isolation():
    async def run():
        events = (
            op("TRANSFER_EXTERNAL", "pye_bfp", 200000, seq=1),
            op("TRANSFER_EXTERNAL", "pye_bfp", 200000, seq=2),
            op("CARD_LIMIT_LOOSEN", seq=3).model_copy(update={"customer_id": "cus_other"}),
        )
        ctx = context(step(amount_cents=200000))
        ctx.snapshot = banking_state().model_copy(update={"ops_7d": events})
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
        assert any(f.code == "SPLIT_TRANSFER" and f.effect == "RISK_RED" for f in ctx.findings)
        assert not any(f.code == "RISKY_SEQUENCE" for f in ctx.findings)

    asyncio.run(run())
