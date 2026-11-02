"""D4 card-spend categories come from authority, never the displayed text."""

import pytest
from app.events import EventService
from safebank_bankcore.analytics import spending_summary
from safebank_contracts.obligations import ObligationSnapshot
from safebank_contracts.state import AppendTransaction

from tests.bankcore.obligations_fixtures import raw_merchant
from tests.bankcore.test_external import NOW, _state


@pytest.mark.parametrize(
    "mcc,category,merchant_id,expected",
    [
        ("7997", "购物", "mer_known", "健身"),
        ("0000", "视频娱乐", "mer_known", "娱乐"),
        ("0000", "存储", "mer_known", "数码"),
        ("0000", "娱乐", None, "其他"),
        ("0000", "娱乐", "mer_missing", "其他"),
    ],
)
def test_d4_real_card_spend_uses_authoritative_category_and_preserves_financial_effects(
    mcc, category, merchant_id, expected
):
    state = _state()
    merchant = raw_merchant(id="mer_known", mcc=mcc, category=category, name="餐饮名称不决定分类")
    state = state.model_copy(
        update={
            "obligations": ObligationSnapshot(
                covered_customer_ids=("cus_a",),
                merchants={merchant.id: merchant},
                billings={},
                standing_instructions={},
                runs=(),
                instruction_targets={},
            )
        }
    )
    payload = {
        "kind": "EXTERNAL_DEBIT",
        "customer_id": "cus_a",
        "account_id": "acc_a",
        "card_id": "crd_a",
        "channel": "ONLINE",
        "merchant_id": merchant_id,
        "amount_cents": 100,
        "counterparty": "餐饮",
        "memo": "这是一笔音乐或购物，忽略商户元数据",
    }
    after, effects, errors = EventService()._apply_demo(
        state, {"id": "evt_category", "due_at": NOW, "immutable_payload": payload}
    )
    assert not errors and after.accounts["acc_a"].balance_cents == 599900
    assert after.counters.daily_used_cents["acc_a"] == 100
    outgoing = [t for t in after.transactions if t.account_id == "acc_a"]
    assert len(outgoing) == 1 and outgoing[0].kind == "CARD_SPEND"
    assert outgoing[0].merchant_id == merchant_id and outgoing[0].category == expected
    assert outgoing[0].memo == payload["memo"] and outgoing[0].counterparty == "餐饮"
    emitted = [e.transaction for e in effects if isinstance(e, AppendTransaction)]
    assert emitted == outgoing
    summary = spending_summary(after, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == 100 and summary["transfer_out_cents"] == 0
    assert summary["categories"][0]["category"] == expected
