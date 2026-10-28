import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.obligations import ObligationSnapshot
from safebank_contracts.views import SubscriptionView

from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.scope import ScopeTransfer
from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import raw_billing, raw_merchant, raw_mandate
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_provenance import authorized, source


def test_cancelled_fees_are_exact_deduplicated_and_bound_to_self_schedule(monkeypatch):
    cancel = step("cancel_subscription", instruction_id="si_a")
    cancel.update(step_id="cancel")
    schedule = step(
        "schedule_transfer",
        from_account_id="acc_a",
        payee_id=None,
        to_account_id="acc_s",
        amount_cents=19900,
        period="MONTHLY",
        first_run_at="2026-11-01T09:00:00+08:00",
        end_at=None,
    )
    schedule.update(step_id="save", depends_on=["cancel"])
    schedule["arg_sources"] = [
        source(
            "amount_cents",
            "derived",
            template="cancelled_monthly_fees",
            inputs=[
                {"kind": "step", "ref": "cancel"},
                {"kind": "read", "ref": "src_sub", "pointer": "/items/0/amount_cents"},
            ],
        )
    ]
    ctx = authorized(
        context([cancel, schedule]),
        transfers=(ScopeTransfer(target_fingerprint="int:acc_s", amount_cents=0),),
        period="MONTHLY",
        fields={
            "derived_template": "cancelled_monthly_fees",
            "instruction_ids": ["si_a"],
            "first_run_at": "2026-11-01T09:00:00+08:00",
            "end_at": None,
        },
    )
    u = ctx.authorization.utterances["utt_1"]
    scope = u.scope.model_copy(update={"action_kinds": ("MANDATE_CANCEL", "SCHEDULE_CREATE")})
    ctx.authorization = ctx.authorization.model_copy(
        update={"utterances": {"utt_1": u.model_copy(update={"scope": scope})}}
    )
    subscription = SubscriptionView(
        instruction_id="si_a",
        billing_id="bil_a",
        account_id="acc_a",
        merchant_id="mer_a",
        merchant_name="合成订阅",
        period="MONTHLY",
        amount_cents=19900,
        max_amount_cents=19900,
        next_due_at="2026-11-01T06:00:00+08:00",
        status="ACTIVE",
        annual_cost_cents=238800,
    )
    ctx._lazy_state = replace_state(
        ctx._lazy_state,
        subscriptions=(subscription,),
        obligations=ObligationSnapshot(
            covered_customer_ids=("cus_a",),
            merchants={"mer_a": raw_merchant(id="mer_a", name="合成订阅")},
            billings={
                "bil_a": raw_billing(
                    id="bil_a",
                    customer_id="cus_a",
                    account_id="acc_a",
                    merchant_id="mer_a",
                    amount_cents=19900,
                )
            },
            standing_instructions={
                "si_a": raw_mandate(
                    id="si_a",
                    customer_id="cus_a",
                    status="ACTIVE",
                    from_account_id="acc_a",
                    merchant_id="mer_a",
                    billing_id="bil_a",
                    max_amount_cents=19900,
                )
            },
            runs=(),
            instruction_targets={},
        ),
    )
    ctx.origin = "app_form"

    async def records(*_):
        return {
            "src_sub": {
                "tool": "list_subscriptions",
                "response": {"items": [subscription.model_dump(mode="json")]},
                "versions": {"cus_a": 0},
                "observed_at": NOW,
                "untrusted_paths": ["/items/0/merchant_name"],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)

    async def run():
        await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    assert ctx.provenance.arg_sources["save"]["amount_cents"]["trust"] == "derived"
    assert ctx.intent.matched


def test_reserve_dependency_is_explicit_and_never_substitutes_balance(monkeypatch):
    s = step("subscribe_product", account_id="acc_a", product_id="prd_a", amount_cents=100)
    s["arg_sources"] = [
        source(
            "amount_cents",
            "derived",
            template="available_less_7d_charges",
            inputs=[{"kind": "read", "ref": "src_acct", "pointer": "/id"}],
        )
    ]
    ctx = authorized(context([s]))

    async def records(*_):
        return {
            "src_acct": {
                "tool": "get_accounts",
                "response": {"id": "acc_a"},
                "versions": {"cus_a": 0},
                "observed_at": NOW,
                "untrusted_paths": [],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)
    with pytest.raises(GatewayError) as exc:
        asyncio.run(ProvenanceStage().run(ctx))
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
    # The explicit test seam proves value checking only, not production reserve behavior.
    asyncio.run(
        ProvenanceStage(
            reserve=lambda state, days: SimpleNamespace(
                accounts={"acc_a": SimpleNamespace(max_outflow_cents=100)}
            )
        ).run(ctx)
    )
    assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "derived"
    asyncio.run(
        ProvenanceStage(
            reserve=lambda state, days: SimpleNamespace(
                accounts={"acc_a": SimpleNamespace(max_outflow_cents=101)}
            )
        ).run(ctx)
    )
    assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "unknown"


def test_existing_hard_deny_is_not_masked_by_unavailable_derived_dependency(monkeypatch):
    from services.gateway.app.schemas.enums import FindingEffect

    s = step(
        "subscribe_product", account_id="acc_missing", product_id="prd_missing", amount_cents=100
    )
    s["arg_sources"] = [
        source(
            "amount_cents",
            "derived",
            template="available_less_7d_charges",
            inputs=[{"kind": "read", "ref": "src_missing", "pointer": "/id"}],
        )
    ]
    ctx = authorized(context([s]))
    ctx.add(Code.NOT_FOUND, FindingEffect.DENY)

    async def records(*_):
        return {
            "src_missing": {
                "tool": "get_accounts",
                "response": {"id": "acc_a"},
                "versions": {"cus_a": 0},
                "observed_at": NOW,
                "untrusted_paths": [],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)

    async def run():
        await ProvenanceStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    assert ctx.provenance is not None
    assert ctx.intent is not None
    assert any(f.code is Code.NOT_FOUND and f.effect is FindingEffect.DENY for f in ctx.findings)
