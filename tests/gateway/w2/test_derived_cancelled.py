"""cancelled_monthly_fees: exact dedup, account and own-savings target checks."""

import asyncio
from datetime import UTC, datetime

from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.obligations import ObligationSnapshot
from safebank_contracts.views import SubscriptionView

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.scope import ScopeTransfer
from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import raw_billing, raw_merchant, raw_mandate
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_provenance import authorized, source


def subscription(**updates):
    values = dict(
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
    values.update(updates)
    return SubscriptionView(**values)


def obligations_for(sub):
    return ObligationSnapshot(
        covered_customer_ids=("cus_a",),
        merchants={sub.merchant_id: raw_merchant(id=sub.merchant_id, name=sub.merchant_name)},
        billings={
            sub.billing_id: raw_billing(
                id=sub.billing_id,
                customer_id="cus_a",
                account_id=sub.account_id,
                merchant_id=sub.merchant_id,
                amount_cents=sub.amount_cents,
            )
        },
        standing_instructions={
            sub.instruction_id: raw_mandate(
                id=sub.instruction_id,
                customer_id="cus_a",
                status="ACTIVE",
                from_account_id=sub.account_id,
                merchant_id=sub.merchant_id,
                billing_id=sub.billing_id,
                max_amount_cents=sub.max_amount_cents,
            )
        },
        runs=(),
        instruction_targets={},
    )


def cancelled_ctx(cancels, schedule_updates=None, subscription_updates=None):
    schedule_updates = schedule_updates or {}
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
    schedule.update(step_id="save", depends_on=[c["step_id"] for c in cancels])
    schedule["args"].update(schedule_updates)
    schedule["arg_sources"] = [
        source(
            "amount_cents",
            "derived",
            template="cancelled_monthly_fees",
            inputs=[{"kind": "step", "ref": c["step_id"]} for c in cancels]
            + [{"kind": "read", "ref": "src_sub", "pointer": "/items/0/amount_cents"}],
        )
    ]
    steps = [*cancels, schedule]
    ctx = authorized(
        context(steps),
        transfers=(ScopeTransfer(target_fingerprint="int:acc_s", amount_cents=0),),
        period="MONTHLY",
        fields={
            "derived_template": "cancelled_monthly_fees",
            "instruction_ids": sorted({c["args"]["instruction_id"] for c in cancels}),
            "first_run_at": "2026-11-01T09:00:00+08:00",
            "end_at": None,
        },
    )
    u = ctx.authorization.utterances["utt_1"]
    scope = u.scope.model_copy(update={"action_kinds": ("MANDATE_CANCEL", "SCHEDULE_CREATE")})
    ctx.authorization = ctx.authorization.model_copy(
        update={"utterances": {"utt_1": u.model_copy(update={"scope": scope})}}
    )
    sub = subscription(**(subscription_updates or {}))
    ctx._lazy_state = replace_state(
        ctx._lazy_state, subscriptions=(sub,), obligations=obligations_for(sub)
    )
    ctx.origin = "app_form"
    return ctx


def run(ctx, monkeypatch):
    async def records(*_):
        return {
            "src_sub": {
                "tool": "list_subscriptions",
                "response": {"items": [subscription().model_dump(mode="json")]},
                "versions": {"cus_a": 0},
                "observed_at": NOW,
                "untrusted_paths": ["/items/0/merchant_name"],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)

    async def execute():
        await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)

    asyncio.run(execute())
    return ctx.provenance.arg_sources["save"]["amount_cents"]["trust"]


def cancel(step_id, instruction_id="si_a"):
    c = step("cancel_subscription", instruction_id=instruction_id)
    c.update(step_id=step_id)
    return c


def test_cancelled_fees_reject_duplicate_cancel_steps(monkeypatch):
    ctx = cancelled_ctx([cancel("c1"), cancel("c2")])
    assert run(ctx, monkeypatch) == "unknown"
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)


def test_cancelled_fees_require_own_savings_target(monkeypatch):
    from safebank_contracts.state import Account

    ctx = cancelled_ctx([cancel("c1")], schedule_updates={"to_account_id": "acc_a2"})
    # A second owned CHECKING account passes ownership but fails the savings check.
    accounts = dict(ctx._lazy_state.accounts)
    accounts["acc_a2"] = Account(
        id="acc_a2",
        customer_id="cus_a",
        type="CHECKING",
        display_name="活期2",
        account_no="6222020000000009",
    )
    ctx._lazy_state = replace_state(ctx._lazy_state, accounts=accounts)
    assert run(ctx, monkeypatch) == "unknown"
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)


def test_cancelled_fees_reject_weekly_subscription(monkeypatch):
    ctx = cancelled_ctx([cancel("c1")], subscription_updates={"period": "WEEKLY"})
    assert run(ctx, monkeypatch) == "unknown"
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)


def test_cancelled_fees_reject_cross_account_subscription(monkeypatch):
    ctx = cancelled_ctx([cancel("c1")], subscription_updates={"account_id": "acc_s"})
    assert run(ctx, monkeypatch) == "unknown"
    assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)
