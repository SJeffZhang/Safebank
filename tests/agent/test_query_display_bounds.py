"""Independent-review regressions: value meaning and complete-row reply budgets."""

import pytest
from safebank_bankcore.funds import _rebuild_holdings, holdings_view
from safebank_bankcore.ledger import replace_state
from safebank_contracts.views import HoldingView

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import RespondAdapter
from tests.agent.test_bill_accuracy import NoInference
from tests.agent.test_query_gather import state
from tests.bankcore.fixtures import NOW
from tests.bankcore.test_funds import funds_state


def record(tool, data):
    return ReadRecord(tool=tool, source_ref="src_" + tool, data=data)


def test_reserved_holding_is_labelled_total_value_without_changing_the_value():
    holding = HoldingView(
        account_id="acc_a",
        product_id="prd_bond",
        units="1000.0000",
        reserved_units="100.0000",
        available_units="900.0000",
        cost_cents=100000,
        reserved_cost_cents=10000,
        available_cost_cents=90000,
        nav="1.2310",
        market_value_cents=123100,
        unrealized_change_cents=23100,
        reserved_receivable_cents=0,
    )
    bank = _rebuild_holdings(replace_state(funds_state(), holdings=(holding,)), NOW)
    view = holdings_view(bank, "cus_a")
    assert view.holdings[0].market_value_cents == 123100
    current = state("我的持仓和在途订单有哪些")
    current["reads"] = [
        record("get_holdings", view.model_dump(mode="json")),
        record("list_products", {"items": [{"id": "prd_bond", "name": "合成债基"}]}),
    ]
    text = RespondAdapter(NoInference())(current, None)
    assert "持仓总估值1231.00元" in text
    assert "可用份额估值" not in text and "可用900.0000份" in text


def test_large_holdings_reply_discloses_complete_counts_and_keeps_whole_order_rows():
    current = state("我的持仓和在途订单有哪些")
    orders = [
        {
            "product_id": "prd_a",
            "type": "SUB",
            "status": "SETTLED",
            "amount_cents": 12345,
            "trade_date": "2026-10-15",
        }
        for _ in range(200)
    ]
    current["reads"] = [
        record("get_holdings", {"holdings": [], "orders": orders}),
        record("list_products", {"items": [{"id": "prd_a", "name": "合成产品"}]}),
    ]
    text = RespondAdapter(NoInference())(current, None)
    assert len(text) <= 600
    assert "共200笔" in text and "仅列前" in text and "查看全部" in text
    assert "合成产品；申购；已结算；金额123.45元；交易日2026-10-15" in text
    assert len(current["reads"][0].data["orders"]) == 200


@pytest.mark.parametrize("kind", ["scheduled", "upcoming", "compare"])
def test_other_deterministic_collections_are_bounded_without_slicing_financial_rows(kind):
    if kind == "scheduled":
        current = state("我有哪些定时转账")
        current["reads"] = [
            record(
                "list_scheduled",
                {
                    "items": [
                        {
                            "payee_id": "pye_a",
                            "target_display": "合成目标",
                            "amount_cents": 12345,
                            "period": "MONTHLY",
                            "next_run_at": "2026-11-04T22:00:00Z",
                        }
                        for _ in range(200)
                    ],
                    "next_cursor": None,
                },
            )
        ]
    elif kind == "upcoming":
        current = state("未来7天有哪些扣费")
        current["reads"] = [
            record("get_profile", {"sim_now": "2026-10-15T09:00:00+08:00"}),
            record(
                "upcoming_charges",
                {
                    "items": [
                        {
                            "merchant_name": "合成商户",
                            "amount_cents": 12345,
                            "due_at": "2026-10-20T06:00:00+08:00",
                        }
                        for _ in range(200)
                    ],
                    "scheduled_items": [],
                    "charges_total_cents": 2469000,
                    "scheduled_transfers_total_cents": 0,
                    "reserve_total_cents": 2469000,
                },
            ),
        ]
    else:
        current = state("比较一下稳盈短债和稳健增利")
        current["reads"] = [
            record(
                "compare_products",
                {
                    "items": [
                        {
                            "id": "prd_a",
                            "name": "合成产品" * 100,
                            "risk_level": 2,
                            "product_type": "BOND",
                            "nav": "1.2310",
                            "nav_date": "2026-10-15",
                            "recent_return_bp": 10,
                            "min_amount_cents": 12345,
                            "confirm_days": 1,
                            "settle_days": 2,
                        }
                        for _ in range(2)
                    ]
                },
            )
        ]
    text = RespondAdapter(NoInference())(current, None)
    assert len(text) <= 600
    assert "仅列前" in text and "查看全部" in text
    assert not text.endswith("…")
