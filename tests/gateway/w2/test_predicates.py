import asyncio

from safebank_bankcore.ledger import replace_state
from safebank_contracts.views import ProductView

from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.pipeline.context import ResolvedStep
from services.gateway.app.pipeline.intent import IntentStage
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_provenance import authorized


def scope(ctx, action, fields):
    ctx = authorized(ctx, fields=fields)
    u = ctx.authorization.utterances["utt_1"]
    ctx.authorization = ctx.authorization.model_copy(
        update={
            "utterances": {
                "utt_1": u.model_copy(
                    update={"scope": u.scope.model_copy(update={"action_kinds": (action,)})}
                )
            }
        }
    )
    return ctx


def test_robust_product_membership_is_recomputed_not_w1_id_list():
    s = step("subscribe_product", product_id="prd_x", account_id="acc_a", amount_cents=100)
    ctx = scope(
        context([s]),
        "FUND_SUBSCRIBE",
        {
            "account_id": "acc_a",
            "amount_cents": 100,
            "predicates": {"robust_products": ["prd_untrusted_claim"]},
        },
    )
    product = ProductView.model_validate(
        FakeL3._minimal_read_result("get_product") | {"risk_label": "稳健"}
    )
    state = ctx._lazy_state
    customer = state.customers["cus_a"].model_copy(
        update={"risk_level": 3, "risk_valid_until": "2027-10-01"}
    )
    ctx._lazy_state = replace_state(
        state, customers=dict(state.customers) | {"cus_a": customer}, products=(product,)
    )
    ctx.steps = (
        ResolvedStep("s1", "subscribe_product", s["args"], "FUND_SUBSCRIBE", "RED", False),
    )
    asyncio.run(IntentStage().run(ctx))
    assert ctx.intent.matched
    ctx._lazy_state = replace_state(
        ctx._lazy_state, products=(product.model_copy(update={"risk_label": "进取"}),)
    )
    asyncio.run(IntentStage().run(ctx))
    assert not ctx.intent.matched
    assert any(f.code.value == "INTENT_SCOPE_EXCEEDED" for f in ctx.findings)
    ctx._lazy_state = replace_state(ctx._lazy_state, customers=dict(state.customers))
    asyncio.run(IntentStage().run(ctx))
    assert any(f.code.value == "ASSESSMENT_MISSING" for f in ctx.findings)


def test_idle_and_suspicious_predicates_do_not_trust_forged_candidate_ids():
    # Empty actual detector output cannot authorize a cancellation/card selected
    # only by the caller's saved candidate IDs. The fake still checks real schemas.
    async def run():
        for tool, action, args, predicate in (
            (
                "cancel_subscription",
                "MANDATE_CANCEL",
                {"instruction_id": "si_a"},
                "idle_subscriptions",
            ),
            ("freeze_card", "CARD_FREEZE", {"card_id": "crd_a"}, "suspicious_cards"),
        ):
            ctx = scope(
                context([step(tool, **args)]),
                action,
                {"predicates": {predicate: list(args.values())}},
            )
            ctx.steps = (ResolvedStep("s1", tool, args, action, "GREEN", False),)
            fake = FakeL3()
            fake.set_snapshot(ctx._lazy_state)
            ctx.l3 = L3Client("http://l3", fake.token, transport=fake.transport())
            try:
                await IntentStage().run(ctx)
                assert not ctx.intent.matched
                assert any(call["op"] == "read" for call in fake.calls)
            finally:
                await ctx.l3.close()

    asyncio.run(run())
