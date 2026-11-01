"""Pure actual event effects must carry the facts needed by reads and fresh counters."""

from datetime import datetime

import pytest
from safebank_bankcore.analytics import detect_subscriptions, spending_summary
from safebank_bankcore.events import apply_merchant_charge, apply_scheduled_transfer
from safebank_contracts.state import AppendTransaction

from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_instruction,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.bankcore.test_banking import banking_state


@pytest.mark.parametrize(
    "payee,code", [("pye_ext", "P2P_EXTERNAL"), ("pye_b", "P2P_INTERNAL"), (None, "SELF_TRANSFER")]
)
def test_actual_scheduled_transaction_kind_and_metadata_match_its_target(payee, code):
    core = banking_state()
    inst = raw_instruction(
        payee_id=payee, to_account_id="acc_s" if payee is None else None, amount_cents=10000
    )
    state = raw_projection_state(instructions=[inst], payees=list(core.payees.values()))
    state = state.model_copy(update={"accounts": core.accounts, "cards": core.cards})
    after, effects, run = apply_scheduled_transfer(
        state, instruction_id=inst.id, due_at=inst.next_run_at
    )
    assert run.status == "SUCCESS"
    outgoing = [t for t in after.transactions if t.direction == "OUT"]
    assert len(outgoing) == 1 and outgoing[0].kind == code
    assert outgoing[0].payee_id == payee
    assert outgoing[0].card_id == ("crd_a" if payee else None)
    emitted = [e.transaction for e in effects if isinstance(e, AppendTransaction)]
    assert set(t.id for t in emitted) == set(t.id for t in after.transactions)
    assert all(t in after.transactions for t in emitted)


@pytest.mark.parametrize(
    "mcc,category,expected",
    [
        ("7997", "购物", "健身"),
        ("5812", "娱乐", "餐饮"),
        ("0000", "出行", "交通"),
        ("0000", "零售", "购物"),
        ("0000", "视频娱乐", "娱乐"),
        ("0000", "存储", "数码"),
        ("0000", "未分类标签", "其他"),
    ],
)
def test_real_merchant_charge_metadata_uses_mcc_then_authoritative_category(
    mcc, category, expected
):
    bill = raw_billing()
    merchant = raw_merchant(mcc=mcc, category=category, name="自由名称不能决定餐饮购物")
    state = raw_projection_state(billings=[bill], mandates=[raw_mandate()], merchants=[merchant])
    after, effects, run = apply_merchant_charge(state, billing_id=bill.id, due_at=bill.next_due_at)
    assert run.status == "SUCCESS"
    txs = [t for t in after.transactions if t.kind == "MERCHANT_CHARGE"]
    assert len(txs) == 1 and txs[0].merchant_id == merchant.id and txs[0].category == expected
    emitted = [e.transaction for e in effects if isinstance(e, AppendTransaction)]
    assert emitted == txs
    summary = spending_summary(after, customer_id="cus_a", month="2026-10")
    assert summary["categories"][0]["category"] == expected


def test_three_real_charge_periods_are_recognized_without_faking_merchant_metadata():
    due = datetime.fromisoformat("2026-07-18T06:00:00+08:00")
    bill = raw_billing(next_due_at=due, created_at=due)
    mandate = raw_mandate(created_at=due)
    merchant = raw_merchant(usage_expected=True, mcc="7997", category="健身")
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[merchant])
    for _ in range(3):
        current = state.obligations.billings[bill.id]
        state, effects, run = apply_merchant_charge(
            state, billing_id=bill.id, due_at=current.next_due_at
        )
        assert effects and run.status == "SUCCESS"
    result = detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["merchant_id"] == merchant.id and item["occurrences"] == 3
    assert item["usage_evidence"] == {"recent_usage_count": 0, "previous_usage_count": 0}


@pytest.mark.parametrize("self_transfer", [False, True])
def test_legacy_scheduled_serialization_keeps_real_ledger_owner_classification(self_transfer):
    from safebank_bankcore.transaction_facts import is_external_transfer

    core = banking_state()
    inst = raw_instruction(
        payee_id=None if self_transfer else "pye_ext",
        to_account_id="acc_s" if self_transfer else None,
        amount_cents=10000,
    )
    state = raw_projection_state(instructions=[inst], payees=list(core.payees.values()))
    after, _, run = apply_scheduled_transfer(state, instruction_id=inst.id, due_at=inst.next_run_at)
    assert run.status == "SUCCESS"
    owned = {a.id for a in after.accounts.values() if a.customer_id == "cus_a"}
    for tx in after.transactions:
        historical = tx.model_copy(update={"kind": "SCHEDULED_TRANSFER"})
        assert is_external_transfer(historical, after.transfers, owned) == (
            tx.direction == "OUT" and not self_transfer
        )
    outgoing = next(t for t in after.transactions if t.direction == "OUT")
    historical = outgoing.model_copy(update={"kind": "SCHEDULED_TRANSFER"})
    assert is_external_transfer(historical, {}, owned)  # unknown target remains conservative
    old_rows = tuple(
        t.model_copy(update={"kind": "SCHEDULED_TRANSFER"}) for t in after.transactions
    )
    old_state = after.model_copy(update={"transactions": old_rows})
    summary = spending_summary(old_state, customer_id="cus_a", month="2026-10")
    assert summary["transfer_out_cents"] == (0 if self_transfer else 10000)
    assert summary["total_cents"] == 0
