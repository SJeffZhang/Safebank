"""X2 derived savings require an actually authorized future monthly charge."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.state import _derive_views
from safebank_bankcore.events import apply_merchant_charge
from safebank_contracts.codes import Code

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from tests.gateway.w2.test_derived_cancelled import cancel, cancelled_ctx


def effective_ctx(*, cap=19900, end_offset=None):
    ctx = cancelled_ctx([cancel("cancel")])
    bank = ctx._lazy_state
    obligations = bank.obligations
    bill = obligations.billings["bil_a"]
    mandate = obligations.standing_instructions["si_a"].model_copy(
        update={
            "max_amount_cents": cap,
            "end_at": None if end_offset is None else bill.next_due_at + end_offset,
        }
    )
    obligations = obligations.model_copy(
        update={
            "standing_instructions": {mandate.id: mandate},
            "covered_customer_ids": tuple(bank.versions),
        }
    )
    _, subscriptions = _derive_views(obligations, "cus_a")
    ctx._lazy_state = bank.model_copy(
        update={"obligations": obligations, "subscriptions": subscriptions}
    )
    return ctx


def run_effective(ctx, monkeypatch, *, resolve_entity=True):
    bank = ctx._lazy_state

    async def records(*_):
        return {
            "src_sub": {
                "tool": "list_subscriptions",
                "response": {"items": [s.model_dump(mode="json") for s in bank.subscriptions]},
                "versions": dict(bank.versions),
                "observed_at": bank.sim_now,
                "untrusted_paths": ["/items/0/merchant_name"],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)

    async def run():
        if resolve_entity:
            await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    return ctx.provenance.arg_sources["save"]["amount_cents"]["trust"]


@pytest.mark.parametrize("invalid", ["cap", "ends_before_due"])
def test_ineffective_monthly_authorization_cannot_create_positive_derived_schedule(
    monkeypatch, invalid
):
    ctx = effective_ctx(
        cap=19899 if invalid == "cap" else 19900,
        end_offset=-timedelta(microseconds=1) if invalid == "ends_before_due" else None,
    )
    if invalid == "cap":
        bank = ctx._lazy_state
        _, effects, actual = apply_merchant_charge(
            bank, billing_id="bil_a", due_at=bank.obligations.billings["bil_a"].next_due_at
        )
        assert actual.status == "BLOCKED" and actual.code == Code.MANDATE_AMOUNT_EXCEEDED.value
        assert actual.transfer_id is None and not effects
    assert run_effective(ctx, monkeypatch) == "unknown"
    assert not ctx.intent.matched
    assert ctx.plan_draft.steps[-1].args.amount_cents == 19900


@pytest.mark.parametrize("end_offset", [None, timedelta(0)])
def test_actual_effective_monthly_charge_retains_exact_derived_amount(monkeypatch, end_offset):
    ctx = effective_ctx(end_offset=end_offset)
    assert run_effective(ctx, monkeypatch) == "derived"
    assert ctx.intent.matched and ctx.plan_draft.steps[-1].args.amount_cents == 19900


@pytest.mark.parametrize(
    "fault",
    [
        "missing_snapshot",
        "missing_bill",
        "missing_coverage",
        "inactive_bill",
        "weekly_raw",
        "different_owned_account",
        "wrong_view_billing",
    ],
)
def test_unknown_or_mismatched_raw_monthly_savings_cannot_authorize_positive_schedule(
    monkeypatch, fault
):
    ctx = effective_ctx()
    bank = ctx._lazy_state
    raw = bank.obligations
    bill = raw.billings["bil_a"]
    mandate = raw.standing_instructions["si_a"]
    if fault == "missing_snapshot":
        # Exercise the provenance boundary with already-resolved entities; a
        # real missing snapshot is also rejected earlier by EntityStage.
        asyncio.run(EntityStage().run(ctx))
        raw = None
    elif fault == "missing_bill":
        raw = raw.model_copy(update={"billings": {}})
    elif fault == "missing_coverage":
        raw = raw.model_copy(update={"covered_customer_ids": ("cus_a",)})
    elif fault == "inactive_bill":
        raw = raw.model_copy(
            update={"billings": {bill.id: bill.model_copy(update={"active": False})}}
        )
    elif fault == "weekly_raw":
        bill = bill.model_copy(
            update={"period": "WEEKLY", "anchor_day": None, "weekday": bill.next_due_at.weekday()}
        )
        raw = raw.model_copy(update={"billings": {bill.id: bill}})
    elif fault == "different_owned_account":
        bill = bill.model_copy(update={"account_id": "acc_s"})
        mandate = mandate.model_copy(update={"from_account_id": "acc_s"})
        raw = raw.model_copy(
            update={"billings": {bill.id: bill}, "standing_instructions": {mandate.id: mandate}}
        )
    else:
        bank = bank.model_copy(
            update={
                "subscriptions": (
                    bank.subscriptions[0].model_copy(update={"billing_id": "bil_other"}),
                )
            }
        )
    ctx._lazy_state = bank.model_copy(update={"obligations": raw})
    assert run_effective(ctx, monkeypatch, resolve_entity=fault != "missing_snapshot") == "unknown"
    assert not ctx.intent.matched
    assert ctx.plan_draft.steps[-1].args.amount_cents == 19900


@pytest.mark.parametrize("second_cap,expected", [(3000, "derived"), (2999, "unknown")])
def test_every_cancelled_monthly_fee_must_be_effective_without_partial_sum(
    monkeypatch, second_cap, expected
):
    ctx = cancelled_ctx(
        [cancel("cancel_a"), cancel("cancel_b", "si_b")],
        schedule_updates={"amount_cents": 22900},
    )
    bank = ctx._lazy_state
    raw = bank.obligations
    second_bill = raw.billings["bil_a"].model_copy(update={"id": "bil_b", "amount_cents": 3000})
    second_mandate = raw.standing_instructions["si_a"].model_copy(
        update={"id": "si_b", "billing_id": "bil_b", "max_amount_cents": second_cap}
    )
    raw = raw.model_copy(
        update={
            "billings": {**raw.billings, second_bill.id: second_bill},
            "standing_instructions": {
                **raw.standing_instructions,
                second_mandate.id: second_mandate,
            },
        }
    )
    _, subscriptions = _derive_views(raw, "cus_a")
    ctx._lazy_state = bank.model_copy(update={"obligations": raw, "subscriptions": subscriptions})
    assert run_effective(ctx, monkeypatch) == expected
    assert ctx.intent.matched is (expected == "derived")
    assert ctx.plan_draft.steps[-1].args.amount_cents == 22900
