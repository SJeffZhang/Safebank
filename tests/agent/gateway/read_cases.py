"""Minimal-but-shape-correct legal args/results for all 18 read tools (C1c1 P2-3).

Each result is the real production field shape (per ``safebank_contracts.views.READ_RESULTS``)
with a **required** field listed first, so the parametrized malformed-result test can drop
the first key and expect ``GatewayProtocolError``.  Args provide every required input
explicitly — the client must never invent a missing required field.
"""

from __future__ import annotations

READ_CASES: dict[str, tuple[dict, dict]] = {
    "get_accounts": (
        {"history_days": 30},
        {
            "accounts": [],
            "daily_outflow_cents": 0,
            "daily_outflow_limit_cents": 100000,
            "remaining_daily_outflow_cents": 100000,
            "balance_history": [],
        },
    ),
    "list_payees": ({"query": None}, {"items": [], "next_cursor": None}),
    "get_transactions": (
        {
            "from_at": "2026-10-01T00:00:00Z",
            "to_at": "2026-10-24T00:00:00Z",
            "account_id": None,
            "category": None,
            "merchant_id": None,
            "kind": None,
        },
        {
            "items": [],
            "next_cursor": None,
            "income_cents": 0,
            "expense_cents": 0,
            "totals_scope": "filtered_all",
        },
    ),
    "get_spending_summary": (
        {"month": "2026-09", "account_id": None},
        {
            "month": "2026-09",
            "total_cents": 0,
            "previous_total_cents": 0,
            "mom_change_cents": 0,
            "mom_change_bp": None,
            "categories": [],
            "daily": [],
            "transfer_out_cents": 0,
        },
    ),
    "detect_anomalies": (
        {"from_at": "2026-10-01T00:00:00Z", "to_at": "2026-10-24T00:00:00Z", "account_id": None},
        {"items": []},
    ),
    "get_cards": ({"account_id": None}, {"items": []}),
    "list_scheduled": ({"status": "ACTIVE"}, {"items": [], "next_cursor": None}),
    "get_split_status": ({"request_id": None}, {"items": [], "next_cursor": None}),
    "detect_subscriptions": ({"lookback_days": 90}, {"items": []}),
    "list_subscriptions": (
        {"status": "ACTIVE"},
        {
            "items": [],
            "next_cursor": None,
            "total_monthly_cents": 0,
            "total_annual_cents": 0,
            "normalization_basis": {
                "weekly_to_annual_factor": 52,
                "monthly_to_annual_factor": 12,
                "annual_to_monthly": "floor_div_12",
            },
        },
    ),
    "upcoming_charges": (
        {"days": 7, "account_id": None},
        {
            "items": [],
            "scheduled_items": [],
            "charges_total_cents": 0,
            "scheduled_transfers_total_cents": 0,
            "reserve_total_cents": 0,
            "by_account": [],
        },
    ),
    "get_risk_questionnaire": (
        {},
        {"questionnaire_id": "rq_v1", "questions": [], "validity_months": 12},
    ),
    "get_risk_profile": (
        {},
        {
            "assessment_id": None,
            "level": None,
            "assessed_at": None,
            "valid_until": None,
            "is_valid": False,
        },
    ),
    "list_products": (
        {"max_risk_level": None, "product_type": None},
        {"items": [], "next_cursor": None},
    ),
    "compare_products": (
        {"product_ids": ["prd_a", "prd_b"]},
        {"items": [], "comparison_fields": []},
    ),
    "get_holdings": ({"account_id": None, "product_id": None}, {"holdings": [], "orders": []}),
    "get_product": (
        {"product_id": "prd_a"},
        {
            "id": "prd_a",
            "name": "稳健货币基金",
            "product_type": "MMF",
            "risk_level": 1,
            "risk_label": "低风险",
            "nav": "1.0000",
            "nav_date": "2026-10-24",
            "recent_return_bp": 0,
            "confirm_days": 1,
            "settle_days": 1,
            "min_amount_cents": 100,
            "risk_disclosure": "",
            "disclosure_version": "v1",
            "nav_history": [],
        },
    ),
    "get_profile": (
        {},
        {
            "customer_id": "cus_a",
            "display_name": "张三",
            "phone_masked": "138****0000",
            "risk_profile": {"level": None, "is_valid": False, "valid_until": None},
            "sim_now": "2026-10-24T00:00:00Z",
        },
    ),
}
