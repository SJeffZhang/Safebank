"""Regression probes for the independent W2 review, round 1."""

import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_predicates import scope


@pytest.mark.parametrize(
    "tool,action,complete,omitted",
    [
        (
            "update_card_controls",
            "CARD_CONTROL_TIGHTEN",
            {
                "card_id": "crd_a",
                "online": False,
                "atm": False,
                "overseas": None,
                "quickpass": None,
            },
            {"atm": None},
        ),
        (
            "update_card_limits",
            "CARD_LIMIT_TIGHTEN",
            {"card_id": "crd_a", "single_limit_cents": 100000, "daily_limit_cents": 1000000},
            {"daily_limit_cents": None},
        ),
    ],
)
def test_explicit_protection_field_cannot_be_deleted(tool, action, complete, omitted):
    for changed, allowed in (({}, True), (omitted, False)):
        ctx = scope(context([step(tool, **(complete | changed))]), action, complete)
        ctx.origin = "app_form"

        async def run(bound=ctx):
            await EntityStage().run(bound)
            await IntentStage().run(bound)

        asyncio.run(run())
        assert ctx.intent.matched is allowed
        assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings) is not allowed


def multi_scope_context(steps, specifications):
    from services.gateway.app.pipeline.context import ResolvedStep
    from services.gateway.app.schemas.enums import UtteranceStatus
    from services.gateway.app.schemas.scope import NormalizedScope
    from tests.gateway.w2.test_provenance import authorized

    ctx = authorized(context(steps))
    prototype = ctx.authorization.utterances["utt_1"]
    refs = {
        f"utt_{index}": prototype.model_copy(
            update={
                "id": f"utt_{index}",
                "scope": NormalizedScope(action_kinds=(action,), fields=fields),
            }
        )
        for index, (action, fields) in enumerate(specifications, start=1)
    }
    ctx.authorization = ctx.authorization.model_copy(
        update={
            "utterance_ids": tuple(refs),
            "scope_ids": tuple(refs),
            "utterances": refs,
            "statuses": {id_: UtteranceStatus.ACTIVE for id_ in refs},
        }
    )
    ctx.plan_draft = ctx.plan_draft.model_copy(update={"utterance_ids": tuple(refs)})
    kinds = {
        "subscribe_product": "FUND_SUBSCRIBE",
        "freeze_card": "CARD_FREEZE",
        "cancel_subscription": "MANDATE_CANCEL",
    }
    ctx.steps = tuple(
        ResolvedStep(
            s.step_id, s.tool, s.args.model_dump(mode="json"), kinds[s.tool], "YELLOW", False
        )
        for s in ctx.plan_draft.steps
    )
    return ctx


@pytest.mark.parametrize(
    "tool,action,first,second",
    [
        (
            "subscribe_product",
            "FUND_SUBSCRIBE",
            {"account_id": "acc_a", "product_id": "prd_a", "amount_cents": 100},
            {"account_id": "acc_a", "product_id": "prd_b", "amount_cents": 100},
        ),
        ("freeze_card", "CARD_FREEZE", {"card_id": "crd_a"}, {"card_id": "crd_b"}),
        (
            "cancel_subscription",
            "MANDATE_CANCEL",
            {"instruction_id": "si_a"},
            {"instruction_id": "si_b"},
        ),
    ],
)
def test_non_transfer_scopes_are_consumed_once(tool, action, first, second):
    for proposal, accepted in (([first, first], False), ([second, first], True)):
        steps = [step(tool, step_id=f"s{i}", **args) for i, args in enumerate(proposal)]
        ctx = multi_scope_context(steps, [(action, first), (action, second)])
        asyncio.run(IntentStage().run(ctx))
        assert ctx.intent.matched is accepted
        assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings) is not accepted


@pytest.mark.parametrize(
    "before,expected_versions,after,accept",
    [
        ({"cus_a": 0}, {"cus_a": 0, "cus_b": 0}, None, True),
        ({"cus_a": 1}, {"cus_a": 0, "cus_b": 0}, None, False),
        ({}, {"cus_a": 0, "cus_b": 0}, None, False),
        ({"cus_a": 0}, {"cus_b": 0}, None, False),
        ({"cus_a": 0, "cus_b": 1}, {"cus_a": 0, "cus_b": 0}, None, False),
        ({"cus_a": 0, "cus_b": 0}, {"cus_a": 0}, None, False),
        ({"cus_a": 0}, {"cus_a": 0, "cus_b": 0}, {"cus_a": 1}, False),
        ({"cus_a": 0}, {"cus_a": 0, "cus_b": 0}, {}, False),
        ({"cus_a": 0, "cus_b": 0}, {"cus_a": 0, "cus_b": 0}, {"cus_a": 0}, False),
        ({"cus_a": 0}, {"cus_a": 0, "cus_b": 0}, {"cus_a": 0, "cus_b": 0}, False),
    ],
)
def test_expected_versions_are_scoped_to_the_read_but_bracketing_is_strict(
    before, expected_versions, after, accept
):
    from safebank_bankcore.ledger import replace_state

    from services.gateway.app.api.tools import fetch_read
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.l3.client import L3Client
    from services.gateway.app.l3.fake import FakeL3
    from tests.bankcore.test_banking import banking_state

    class ScopedReadFake(FakeL3):
        def _minimal_read_result(self, tool):
            if after is not None:
                self.set_snapshot(replace_state(banking_state(), versions=after))
            return super()._minimal_read_result(tool)

    async def run():
        fake = ScopedReadFake()
        fake.set_snapshot(replace_state(banking_state(), versions=before))
        l3 = L3Client("http://l3", fake.token, transport=fake.transport())
        expected = replace_state(banking_state(), versions=expected_versions)
        try:
            if accept:
                envelope = await fetch_read(
                    l3, "cus_a", "get_accounts", {"history_days": 0}, expected=expected
                )
                assert envelope.versions == before
                assert len(fake.calls) == 3
            else:
                with pytest.raises(GatewayError) as exc:
                    await fetch_read(
                        l3, "cus_a", "get_accounts", {"history_days": 0}, expected=expected
                    )
                assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
                assert exc.value.status == 503
        finally:
            await l3.close()

    asyncio.run(run())


def test_overlapping_product_predicate_and_exact_scope_have_a_complete_matching():
    from safebank_bankcore.ledger import replace_state
    from safebank_contracts.views import ProductView

    from services.gateway.app.l3.fake import FakeL3

    common = {"account_id": "acc_a", "amount_cents": 100}
    scopes = [
        ("FUND_SUBSCRIBE", common | {"predicates": {"robust_products": ["obsolete_id"]}}),
        ("FUND_SUBSCRIBE", common | {"product_id": "prd_a"}),
    ]
    steps = [
        step("subscribe_product", step_id=f"s{i}", product_id=p, **common)
        for i, p in enumerate(("prd_a", "prd_b"))
    ]
    ctx = multi_scope_context(steps, scopes)
    state = ctx._lazy_state
    prototype = ProductView.model_validate(FakeL3._minimal_read_result("get_product"))
    products = tuple(
        prototype.model_copy(update={"id": id_, "risk_label": "稳健"}) for id_ in ("prd_a", "prd_b")
    )
    customer = state.customers["cus_a"].model_copy(
        update={"risk_level": 3, "risk_valid_until": "2027-10-01"}
    )
    ctx._lazy_state = replace_state(
        state, products=products, customers=dict(state.customers) | {"cus_a": customer}
    )
    asyncio.run(IntentStage().run(ctx))
    assert ctx.intent.matched


@pytest.mark.parametrize(
    "proposal,accept",
    [
        (["si_b", "si_a", "si_c"], True),
        (["si_a", "si_a", "si_c"], False),
        (["si_a", "si_c"], False),
    ],
)
def test_idle_predicate_expands_once_per_current_target_and_composes_with_explicit_scope(
    proposal, accept
):
    from services.gateway.app.l3.client import L3Client
    from services.gateway.app.l3.fake import FakeL3

    class IdleReadFake(FakeL3):
        def _minimal_read_result(self, tool):
            if tool != "detect_subscriptions":
                return super()._minimal_read_result(tool)
            return {
                "items": [
                    {
                        "merchant_id": "mer_" + id_,
                        "merchant_name": "合成商户",
                        "instruction_id": id_,
                        "billing_id": "bil_" + id_,
                        "period": "MONTHLY",
                        "typical_amount_cents": 100,
                        "occurrences": 3,
                        "last_charged_at": "2026-10-01T06:00:00+08:00",
                        "is_idle": True,
                        "usage_evidence": {"recent_usage_count": 0, "previous_usage_count": 5},
                        "annual_cost_cents": 1200,
                    }
                    for id_ in ("si_a", "si_b")
                ]
            }

    ctx = multi_scope_context(
        [
            step("cancel_subscription", step_id=f"s{i}", instruction_id=id_)
            for i, id_ in enumerate(proposal)
        ],
        [
            ("MANDATE_CANCEL", {"predicates": {"idle_subscriptions": ["untrusted_candidate"]}}),
            ("MANDATE_CANCEL", {"instruction_id": "si_c"}),
        ],
    )

    async def run():
        fake = IdleReadFake()
        fake.set_snapshot(ctx._lazy_state)
        ctx.l3 = L3Client("http://l3", fake.token, transport=fake.transport())
        try:
            await IntentStage().run(ctx)
            assert ctx.intent.matched is accept
        finally:
            await ctx.l3.close()

    asyncio.run(run())


def test_explicit_cancellation_list_is_a_multiset_of_distinct_permissions():
    for ids, accepted in ((["si_a", "si_b"], True), (["si_a", "si_a"], False)):
        ctx = multi_scope_context(
            [
                step("cancel_subscription", step_id=f"s{i}", instruction_id=id_)
                for i, id_ in enumerate(ids)
            ],
            [("MANDATE_CANCEL", {"instruction_ids": ["si_a", "si_b"]})],
        )
        asyncio.run(IntentStage().run(ctx))
        assert ctx.intent.matched is accepted


@pytest.mark.parametrize("initial_clock,expected_clock,after_clock", [(1, 2, 1), (1, 1, 2)])
def test_scoped_expected_versions_do_not_weaken_clock_checks(
    initial_clock, expected_clock, after_clock
):
    from safebank_bankcore.ledger import replace_state

    from services.gateway.app.api.tools import fetch_read
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.l3.client import L3Client
    from services.gateway.app.l3.fake import FakeL3
    from tests.bankcore.test_banking import banking_state

    class ClockReadFake(FakeL3):
        def _minimal_read_result(self, tool):
            self.set_snapshot(
                replace_state(banking_state(), versions={"cus_a": 0}, clock_version=after_clock)
            )
            return super()._minimal_read_result(tool)

    async def run():
        fake = ClockReadFake()
        fake.set_snapshot(
            replace_state(banking_state(), versions={"cus_a": 0}, clock_version=initial_clock)
        )
        l3 = L3Client("http://l3", fake.token, transport=fake.transport())
        expected = replace_state(banking_state(), clock_version=expected_clock)
        try:
            with pytest.raises(GatewayError) as exc:
                await fetch_read(
                    l3, "cus_a", "get_accounts", {"history_days": 0}, expected=expected
                )
            assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
        finally:
            await l3.close()

    asyncio.run(run())


def test_explicit_amount_cannot_be_replaced_by_a_derived_template_marker():
    from services.gateway.app.pipeline.context import ProvenanceResult

    fields = {
        "account_id": "acc_a",
        "product_id": "prd_a",
        "amount_cents": 100,
        "derived_template": "available_less_7d_charges",
    }
    ctx = multi_scope_context(
        [step("subscribe_product", account_id="acc_a", product_id="prd_a", amount_cents=200)],
        [("FUND_SUBSCRIBE", fields)],
    )
    ctx.provenance = ProvenanceResult(
        arg_sources={
            "s1": {
                "amount_cents": {
                    "trust": "derived",
                    "source": {"template": "available_less_7d_charges"},
                }
            }
        }
    )
    asyncio.run(IntentStage().run(ctx))
    assert not ctx.intent.matched
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)
