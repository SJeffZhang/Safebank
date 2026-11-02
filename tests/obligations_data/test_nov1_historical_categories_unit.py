"""Historic and new consumption use the same authoritative eight buckets."""

import pytest
from app.reads import read
from safebank_bankcore.analytics import spending_summary
from safebank_contracts.obligations import Merchant
from safebank_contracts.tools import READ_ARGS

from tests.bankcore.obligations_fixtures import at, raw_projection_state
from tests.bankcore.test_analytics import tx


@pytest.mark.parametrize("mcc,saved", [("5812", "数码"), ("9999", "外卖"), ("9999", "")])
def test_summary_category_filter_and_rows_share_one_canonical_projection(mcc, saved):
    state = raw_projection_state(
        merchants=[
            Merchant(
                id="mer_actual",
                name="不可信购物名称",
                mcc=mcc,
                category="茶饮",
                usage_expected=True,
            )
        ]
    )
    original = tx(merchant_id="mer_actual", category=saved, amount_cents=4550)
    state = state.model_copy(update={"transactions": (original,)})
    summary = spending_summary(state, customer_id="cus_a", month="2026-10")
    # Known MCC wins. Unlisted MCC uses authoritative merchant category, then
    # only for a missing merchant does the saved transaction category apply.
    category = "餐饮"
    assert summary["total_cents"] == 4550
    assert summary["categories"] == (
        dict(category=category, amount_cents=4550, share_bp=10000, previous_amount_cents=0),
    )
    args = READ_ARGS["get_transactions"](
        account_id=None,
        from_at=at("2026-10-01T00:00:00+08:00"),
        to_at=at("2026-11-01T00:00:00+08:00"),
        category=category,
        merchant_id=None,
        kind=None,
        cursor=None,
        limit=20,
    )
    result = read(state, "get_transactions", args, "cus_a")
    assert result.expense_cents == 4550 and result.items[0].category == category
    assert state.transactions[0] == original and original.category == saved


@pytest.mark.parametrize("saved,expected", [("外卖", "餐饮"), ("", "其他"), ("任意未知", "其他")])
def test_no_merchant_falls_back_only_to_saved_category_not_free_text(saved, expected):
    state = raw_projection_state().model_copy(
        update={"transactions": (tx(category=saved, counterparty="餐饮", memo="数码"),)}
    )
    summary = spending_summary(state, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == 5000
    assert summary["categories"][0]["category"] == expected
