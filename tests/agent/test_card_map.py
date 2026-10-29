"""L1-card-map: real read tool → card type mapping tests.

No real LLM, no PG — card generation from fake state.

The mapping in ``services.agent.app.llm.cards`` must key off real read tool
names (``safebank_contracts.tools.READ_ARGS``); the old map referenced five
bogus tool names, so subscription / product / holding / anomaly cards were
never produced.
"""

from __future__ import annotations

from safebank_contracts.tools import READ_ARGS

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.cards import _READ_TOOL_CARD_TYPE, generate_cards


def _analysis_state(read: ReadRecord) -> dict:
    return {
        "reads": [read],
        "understanding": type("u", (), {"unclear": False, "needs_write": False, "intents": []})(),
    }


class TestMappingGuard:
    def test_every_mapping_key_is_a_real_read_tool(self):
        assert set(_READ_TOOL_CARD_TYPE) <= set(READ_ARGS)

    def test_previously_broken_tools_are_mapped(self):
        for tool in (
            "get_spending_summary",
            "list_subscriptions",
            "upcoming_charges",
            "detect_subscriptions",
            "list_products",
            "compare_products",
            "get_product",
            "get_holdings",
            "detect_anomalies",
        ):
            assert tool in _READ_TOOL_CARD_TYPE, tool


class TestCardTypesFromReads:
    def _cards_for(self, tool: str, data: dict) -> tuple:
        state = _analysis_state(ReadRecord(source_ref="src_a", tool=tool, args={}, data=data))
        return generate_cards(state)

    def test_account(self):
        cards = self._cards_for(
            "get_accounts",
            {
                "accounts": [{"id": "acc_zs_chk", "balance_cents": 600000, "currency": "CNY"}],
                "daily_outflow_cents": 0,
                "daily_outflow_limit_cents": 100000,
                "remaining_daily_outflow_cents": 100000,
                "balance_history": [],
            },
        )
        assert cards and cards[0].type == "ACCOUNT"

    def test_transactions(self):
        cards = self._cards_for(
            "get_transactions",
            {
                "items": [{"id": "txn_1", "amount_cents": 80000, "direction": "OUT"}],
                "next_cursor": None,
                "income_cents": 0,
                "expense_cents": 80000,
                "totals_scope": "filtered_all",
            },
        )
        assert cards and cards[0].type == "TRANSACTIONS"

    def test_spending_summary_is_transactions(self):
        cards = self._cards_for(
            "get_spending_summary",
            {
                "month": "2026-10",
                "total_cents": 80000,
                "previous_total_cents": 50000,
                "mom_change_cents": 30000,
                "mom_change_bp": 6000,
                "categories": [],
                "daily": [],
                "transfer_out_cents": 0,
            },
        )
        assert cards and cards[0].type == "TRANSACTIONS"

    def test_card(self):
        cards = self._cards_for(
            "get_cards",
            {"items": [{"id": "crd_zs_1", "last4": "1234", "status": "ACTIVE"}]},
        )
        assert cards and cards[0].type == "CARD"

    def test_subscriptions_list(self):
        cards = self._cards_for(
            "list_subscriptions",
            {
                "items": [{"instruction_id": "ins_1", "amount_cents": 3000}],
                "next_cursor": None,
                "total_monthly_cents": 3000,
                "total_annual_cents": 36000,
                "normalization_basis": {
                    "weekly_to_annual_factor": 52,
                    "monthly_to_annual_factor": 12,
                    "annual_to_monthly": "floor_div_12",
                },
            },
        )
        assert cards and cards[0].type == "SUBSCRIPTIONS"

    def test_subscriptions_upcoming(self):
        cards = self._cards_for(
            "upcoming_charges",
            {
                "items": [{"instruction_id": "ins_1", "amount_cents": 3000}],
                "scheduled_items": [],
                "charges_total_cents": 3000,
                "scheduled_transfers_total_cents": 0,
                "reserve_total_cents": 0,
                "by_account": [],
            },
        )
        assert cards and cards[0].type == "SUBSCRIPTIONS"

    def test_subscriptions_detect(self):
        cards = self._cards_for(
            "detect_subscriptions",
            {"items": [{"merchant_id": "mch_1", "typical_amount_cents": 3000}]},
        )
        assert cards and cards[0].type == "SUBSCRIPTIONS"

    def test_products_list(self):
        cards = self._cards_for(
            "list_products",
            {"items": [{"id": "prd_1", "risk_level": 2}], "next_cursor": None},
        )
        assert cards and cards[0].type == "PRODUCTS"

    def test_products_compare(self):
        cards = self._cards_for(
            "compare_products",
            {"items": [{"id": "prd_1", "risk_level": 2}], "comparison_fields": ["risk_level"]},
        )
        assert cards and cards[0].type == "PRODUCTS"

    def test_product_detail(self):
        cards = self._cards_for(
            "get_product",
            {"id": "prd_1", "risk_level": 2, "nav": "1.0000"},
        )
        assert cards and cards[0].type == "PRODUCTS"

    def test_holdings(self):
        cards = self._cards_for(
            "get_holdings",
            {"holdings": [{"product_id": "prd_1", "units": "10.0000"}], "orders": []},
        )
        assert cards and cards[0].type == "HOLDINGS"

    def test_anomalies(self):
        cards = self._cards_for(
            "detect_anomalies",
            {
                "items": [
                    {
                        "transaction": {"id": "txn_1"},
                        "codes": ["A1"],
                        "evidence": {"baseline_count": 3},
                    }
                ]
            },
        )
        assert cards and cards[0].type == "ANOMALIES"


class TestCardCapWithRealTools:
    def test_cap_still_5(self):
        tools = [
            "get_accounts",
            "get_transactions",
            "get_cards",
            "list_subscriptions",
            "list_products",
            "get_holdings",
            "detect_anomalies",
            "get_spending_summary",
        ]
        reads = []
        for i, tool in enumerate(tools):
            if tool == "get_accounts":
                data = {"accounts": [{"id": f"acc_{i}"}]}
            else:
                data = {"items": [{"id": f"x_{i}"}]}
            reads.append(ReadRecord(source_ref=f"src_{i}", tool=tool, args={}, data=data))
        state = {
            "reads": reads,
            "understanding": type(
                "u", (), {"unclear": False, "needs_write": False, "intents": []}
            )(),
        }
        cards = generate_cards(state)
        assert len(cards) == 5
