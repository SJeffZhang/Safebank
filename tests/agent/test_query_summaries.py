"""Formal November queries must not lose facts to generic JSON truncation."""

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import _reads_summary


def read(tool, data):
    return ReadRecord(source_ref="src_query", tool=tool, data=data)


def test_catalog_keeps_later_products_without_nav_history_crowding_them_out():
    products = []
    for id_, name in (
        ("growth", "成长股票"),
        ("mixed", "均衡混合"),
        ("mmf", "安心货币"),
        ("sbond", "稳盈短债"),
        ("stable", "稳健增利"),
        ("tech", "科技先锋"),
    ):
        products.append(
            {
                "id": "prd_" + id_,
                "name": name,
                "risk_level": 3,
                "risk_label": "稳健",
                "product_type": "BOND",
                "nav": "1.2310",
                "nav_date": "2026-10-15",
                "recent_return_bp": 120,
                "min_amount_cents": 10000,
                "confirm_days": 1,
                "settle_days": 2,
                "risk_disclosure": "净值可能波动" * 20,
                "nav_history": [{"date": "2026-09-01", "nav": "1.2000"}] * 30,
            }
        )
    text = _reads_summary({"reads": [read("list_products", {"items": products})]})
    assert all(name in text for name in ("稳盈短债", "稳健增利", "科技先锋"))
    assert "1.2310" in text and "100.00元" in text
    assert "nav_history" not in text


def test_transaction_page_survives_after_an_account_block():
    transactions = [
        {
            "id": f"tx_{i}",
            "amount_cents": 100,
            "direction": "OUT",
            "kind": "CARD_PAYMENT",
            "occurred_at": "2026-10-01T00:00:00+08:00",
            "counterparty": "商户" + str(i),
            "category": "餐饮",
            "memo": "备注" * 100,
        }
        for i in range(50)
    ]
    transactions[-1].update(counterparty="兰州拉面", amount_cents=4550)
    text = _reads_summary(
        {
            "reads": [
                read(
                    "get_accounts",
                    {
                        "accounts": [
                            {
                                "display_name": "活期",
                                "balance_cents": 610000,
                                "available_cents": 610000,
                            }
                        ]
                    },
                ),
                read(
                    "get_transactions",
                    {
                        "items": transactions,
                        "next_cursor": None,
                        "income_cents": 0,
                        "expense_cents": 9450,
                    },
                ),
            ]
        }
    )
    assert "兰州拉面" in text and "45.50元" in text


def test_card_and_account_summaries_keep_available_and_daily_remaining():
    text = _reads_summary(
        {
            "reads": [
                read(
                    "get_accounts",
                    {
                        "accounts": [
                            {
                                "display_name": "活期",
                                "balance_cents": 1200000,
                                "available_cents": 1000000,
                                "pending_debit_cents": 200000,
                            }
                        ]
                    },
                ),
                read(
                    "get_cards",
                    {
                        "items": [
                            {
                                "last4": "1234",
                                "status": "ACTIVE",
                                "single_limit_cents": 500000,
                                "daily_limit_cents": 2000000,
                                "daily_used_cents": 40000,
                                "remaining_daily_cents": 1960000,
                            }
                        ]
                    },
                ),
            ]
        }
    )
    assert "10000.00元" in text and "2000.00元" in text
    assert "19600.00元" in text and "400.00元" in text


def test_upcoming_summary_separates_mandates_scheduled_and_total_reserve():
    text = _reads_summary(
        {
            "reads": [
                read(
                    "upcoming_charges",
                    {
                        "items": [
                            {
                                "merchant_name": "视频",
                                "amount_cents": 3000,
                                "due_at": "2026-10-18T22:00:00Z",
                            }
                        ],
                        "scheduled_items": [
                            {
                                "target_display": "赵六",
                                "amount_cents": 20000,
                                "due_at": "2026-10-20T00:00:00Z",
                            }
                        ],
                        "charges_total_cents": 3000,
                        "scheduled_transfers_total_cents": 20000,
                        "reserve_total_cents": 23000,
                    },
                )
            ]
        }
    )
    assert "200.00元" in text and "230.00元" in text
    assert "定时转账" in text and "2026-10-19 06:00" in text


def test_detected_last_charge_is_not_labelled_next_charge():
    text = _reads_summary(
        {
            "reads": [
                read(
                    "detect_subscriptions",
                    {
                        "items": [
                            {
                                "merchant_name": "健身",
                                "typical_amount_cents": 19900,
                                "period": "MONTHLY",
                                "last_charged_at": "2026-09-25T06:00:00+08:00",
                                "is_idle": True,
                            }
                        ]
                    },
                )
            ]
        }
    )
    assert "下次 2026-09-25" not in text
    assert "最近扣费 2026-09-25" in text


def test_subscription_totals_are_present_even_when_items_exist():
    text = _reads_summary(
        {
            "reads": [
                read(
                    "list_subscriptions",
                    {
                        "items": [
                            {"merchant_name": "健身", "amount_cents": 19900, "period": "MONTHLY"}
                        ],
                        "total_monthly_cents": 26500,
                        "total_annual_cents": 318000,
                    },
                )
            ]
        }
    )
    assert "265.00元" in text and "3180.00元" in text


def test_holdings_and_orders_use_read_product_names_and_business_time():
    text = _reads_summary(
        {
            "reads": [
                read(
                    "list_products",
                    {
                        "items": [
                            {"id": "prd_mmf", "name": "安心货币"},
                            {"id": "prd_stable", "name": "稳健增利"},
                        ]
                    },
                ),
                read(
                    "get_holdings",
                    {
                        "holdings": [
                            {
                                "product_id": "prd_mmf",
                                "units": "10000.0000",
                                "available_units": "10000.0000",
                                "reserved_units": "0.0000",
                                "cost_cents": 1000000,
                                "market_value_cents": 1000000,
                            }
                        ],
                        "orders": [
                            {
                                "id": "ord_internal",
                                "product_id": "prd_stable",
                                "type": "SUB",
                                "status": "PENDING_CONFIRM",
                                "amount_cents": 200000,
                                "trade_date": "2026-10-15",
                                "confirm_at": "2026-10-16T01:00:00Z",
                                "settle_at": None,
                            }
                        ],
                    },
                ),
            ]
        }
    )
    assert "安心货币" in text and "稳健增利" in text and "待确认" in text
    assert "2026-10-16 09:00" in text and "ord_internal" not in text
    assert '"holdings"' not in text and '"orders"' not in text


def test_scheduled_summary_resolves_read_payee_and_local_time_without_internal_id():
    text = _reads_summary(
        {
            "reads": [
                read("list_payees", {"items": [{"id": "pye_zl", "name": "赵六"}]}),
                read(
                    "list_scheduled",
                    {
                        "items": [
                            {
                                "id": "si_internal",
                                "payee_id": "pye_zl",
                                "target_display": "他行收款人",
                                "amount_cents": 100000,
                                "period": "MONTHLY",
                                "anchor_day": 5,
                                "time_of_day": "06:00:00",
                                "next_run_at": "2026-11-04T22:00:00Z",
                                "status": "ACTIVE",
                            }
                        ]
                    },
                ),
            ]
        }
    )
    assert "赵六" in text and "2026-11-05 06:00" in text
    scheduled = text.split("list_scheduled:", 1)[1]
    assert "si_internal" not in scheduled and "pye_zl" not in scheduled


def test_analysis_response_does_not_echo_prompt_only_provenance_markers():
    from langchain_core.messages import AIMessage

    from services.agent.app.llm.respond import RespondAdapter

    class Model:
        def invoke(self, _):
            return AIMessage(
                content='<untrusted_data source="src_actual">合成商户</untrusted_data>'
            )

    final = RespondAdapter(Model())({"reads": []}, None)
    assert final == "合成商户"


def test_risk_upgrade_labels_do_not_offer_otp_as_a_hard_limit_override():
    from services.agent.app.llm.respond import _replan_user_items

    labels = _replan_user_items(
        {
            "findings": [
                {"code": "AMOUNT_ABOVE_YELLOW_CAP"},
                {"code": "CARD_SINGLE_LIMIT"},
                {"code": "CUMULATIVE_OUTFLOW"},
            ]
        }
    )
    assert any("短信" in label and "不能" in label for label in labels)
    assert not any("确认风险后重试" in label or "累计转出上限" in label for label in labels)
