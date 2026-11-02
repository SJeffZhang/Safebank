"""Known card merchants without subscriptions still supply authoritative MCC."""

import asyncio

from app.state import load_bank_state
from safebank_bankcore.analytics import spending_summary
from safebank_contracts.obligations import Merchant
from safebank_contracts.state import StateScope

from tests.bankcore.fixtures import NOW
from tests.bankcore.test_analytics import tx
from tests.obligations_data.test_query_read_projection_unit import ReadConnection, Rows


def test_history_merchant_load_is_bounded_to_current_owned_transaction_references():
    class ScopedConnection(ReadConnection):
        merchant_ids = None

        async def execute(self, statement):
            if statement.get_final_froms()[0].name == "merchants":
                ids = set(statement.compile().params["id_1"])
                self.merchant_ids = ids
                return Rows([row for row in self.rows["merchants"] if row["id"] in ids])
            return await super().execute(statement)

    conn = ScopedConnection(NOW)
    conn.rows["fund_orders"] = []
    conn.rows["transactions"] = [
        tx(merchant_id="mer_food", category="数码", amount_cents=4550).model_dump(),
        tx(
            id="tx_foreign",
            merchant_id="mer_foreign",
            customer_id="cus_b",
            account_id="acc_b",
        ).model_dump(),
    ]
    conn.rows["merchants"] = [
        Merchant(
            id=mid, name="购物名称不可决定类别", mcc="5812", category="餐饮", usage_expected=False
        ).model_dump()
        for mid in ["mer_food", "mer_foreign", "mer_unrelated"]
    ]
    loaded = asyncio.run(
        load_bank_state(conn, StateScope(customer_ids=("cus_a",)), NOW, customer_id="cus_a")
    )
    summary = spending_summary(loaded, customer_id="cus_a", month="2026-10")
    assert summary["total_cents"] == 4550 and summary["categories"][0]["category"] == "餐饮"
    assert conn.merchant_ids == {"mer_food"}
    assert set(loaded.obligations.merchants) == {"mer_food"}
    assert loaded.transactions[0].category == "数码"
