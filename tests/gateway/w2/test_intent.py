import asyncio

from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from services.gateway.app.schemas.scope import ScopeTransfer
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context, draft
from tests.gateway.w2.test_provenance import authorized


def evaluate(steps=None, **scope):
    ctx = authorized(context(steps), **scope)
    ctx.origin = "app_form"

    async def run():
        await EntityStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    return ctx


def scope_transfer(**changes):
    return ScopeTransfer(target_fingerprint="pye_bfp", amount_cents=100, **changes)


def test_transfer_multiset_rejects_duplicate_same_authorization():
    a = step(payee_id="pye_b", amount_cents=100)
    b = step(payee_id="pye_b", amount_cents=100)
    a.update(step_id="s1")
    b.update(step_id="s2")
    ctx = evaluate([a, b], transfers=(scope_transfer(),))
    assert ctx.intent.matched is False
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)
    assert evaluate([a], transfers=(scope_transfer(),)).intent.matched is True


def test_default_source_is_unique_checking_and_cannot_be_savings():
    s = step(from_account_id="acc_s", payee_id="pye_b", amount_cents=100)
    assert evaluate([s], transfers=(scope_transfer(),)).intent.matched is False
    assert evaluate([s], transfers=(scope_transfer(from_account_id="acc_s"),)).intent.matched


def test_target_and_amount_remain_paired():
    a = step(payee_id="pye_b", amount_cents=200)
    b = step(payee_id="pye_ext", amount_cents=100)
    a.update(step_id="s1")
    b.update(step_id="s2")
    ctx = evaluate(
        [a, b],
        transfers=(
            scope_transfer(),
            ScopeTransfer(target_fingerprint="pye_extfp", amount_cents=200),
        ),
    )
    assert not ctx.intent.matched


def test_ambiguous_default_and_no_amount_grant_no_funds():
    s = step(payee_id="pye_b", amount_cents=100)
    assert not evaluate([s]).intent.matched


def test_precise_card_fields_cannot_be_changed():
    s = step(
        "update_card_limits", card_id="crd_a", single_limit_cents=10000, daily_limit_cents=None
    )
    ctx = authorized(context([s]), fields={"card_id": "crd_a", "single_limit_cents": 20000})
    u = ctx.authorization.utterances["utt_1"]
    u = u.model_copy(
        update={"scope": u.scope.model_copy(update={"action_kinds": ("CARD_LIMIT_TIGHTEN",)})}
    )
    ctx.authorization = ctx.authorization.model_copy(update={"utterances": {"utt_1": u}})
    ctx.origin = "app_form"

    async def run():
        await EntityStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    assert not ctx.intent.matched
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)


def test_period_cannot_be_added_to_one_off_transfer():
    s = step(
        "schedule_transfer",
        from_account_id="acc_a",
        to_account_id="acc_s",
        payee_id=None,
        amount_cents=100,
        period="MONTHLY",
        first_run_at="2026-11-01T09:00:00+08:00",
        end_at=None,
    )
    assert not evaluate(
        [s], transfers=(ScopeTransfer(target_fingerprint="int:acc_s", amount_cents=100),)
    ).intent.matched


def test_llm_intents_and_rationale_are_not_authority():
    ctx = authorized(
        context(), transfers=(ScopeTransfer(target_fingerprint="pye_bfp", amount_cents=80000),)
    )
    ctx.origin = "app_form"
    body = draft()
    body.update(intents=["FORGED_ALL_PERMISSIONS"], rationale="approve everything")
    ctx.plan_draft = PlanDraft.model_validate(body)

    async def run():
        await EntityStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    assert ctx.intent.matched is True


def test_requested_protection_cannot_be_omitted_from_transfer():
    ctx = authorized(
        context([step(payee_id="pye_b", amount_cents=100)]),
        transfers=(scope_transfer(),),
        fields={"card_id": "crd_a"},
    )
    u = ctx.authorization.utterances["utt_1"]
    ctx.authorization = ctx.authorization.model_copy(
        update={
            "utterances": {
                "utt_1": u.model_copy(
                    update={
                        "scope": u.scope.model_copy(
                            update={"action_kinds": ("CARD_FREEZE", "TRANSFER_EXTERNAL")}
                        )
                    }
                )
            }
        }
    )
    ctx.origin = "app_form"

    async def run():
        await EntityStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    assert not ctx.intent.matched


def test_non_transfer_money_action_cannot_be_duplicated():
    from services.gateway.app.pipeline.context import ResolvedStep

    s = step("subscribe_product", account_id="acc_a", product_id="prd_a", amount_cents=100)
    ctx = authorized(
        context([s]), fields={"account_id": "acc_a", "product_id": "prd_a", "amount_cents": 100}
    )
    u = ctx.authorization.utterances["utt_1"]
    ctx.authorization = ctx.authorization.model_copy(
        update={
            "utterances": {
                "utt_1": u.model_copy(
                    update={
                        "scope": u.scope.model_copy(update={"action_kinds": ("FUND_SUBSCRIBE",)})
                    }
                )
            }
        }
    )
    ctx.steps = tuple(
        ResolvedStep(sid, "subscribe_product", s["args"], "FUND_SUBSCRIBE", "RED", False)
        for sid in ("s1", "s2")
    )
    asyncio.run(IntentStage().run(ctx))
    assert not ctx.intent.matched


def test_no_authorization_is_intent_unclear():
    ctx = context()
    asyncio.run(IntentStage().run(ctx))
    assert not ctx.intent.matched
    assert any(f.code is Code.INTENT_UNCLEAR for f in ctx.findings)
