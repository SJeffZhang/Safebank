NOW = "2026-10-23T12:00:00+08:00"
WRITE_GOOD = {
    "transfer": dict(
        from_account_id="acc_a", payee_id="pye_b", to_account_id=None, amount_cents=80000, memo=None
    ),
    "add_payee": dict(
        name="王五", account_no="6222020000000001", bank_name="本行", phone=None, alias=None
    ),
    "schedule_transfer": dict(
        from_account_id="acc_a",
        payee_id="pye_b",
        to_account_id=None,
        amount_cents=100,
        period="MONTHLY",
        first_run_at=NOW,
        end_at=None,
    ),
    "cancel_scheduled": dict(instruction_id="si_a"),
    "create_split_request": dict(
        account_id="acc_a",
        total_cents=100,
        participant_payee_ids=["pye_b"],
        include_self=True,
        memo=None,
    ),
    "update_card_limits": dict(card_id="crd_a", single_limit_cents=1000, daily_limit_cents=None),
    "update_card_controls": dict(
        card_id="crd_a", online=False, overseas=None, atm=None, quickpass=None
    ),
    "freeze_card": dict(card_id="crd_a"),
    "unfreeze_card": dict(card_id="crd_a"),
    "report_card_lost": dict(card_id="crd_a", replace=True),
    "cancel_subscription": dict(instruction_id="si_a"),
    "submit_risk_assessment": dict(
        questionnaire_id="rq_v1",
        answers=[dict(question_id=f"q{i}", option_id="a") for i in range(6)],
    ),
    "subscribe_product": dict(account_id="acc_a", product_id="prd_a", amount_cents=100),
    "redeem_product": dict(account_id="acc_a", product_id="prd_a", units="1.0000"),
}
READ_GOOD = {
    "get_accounts": dict(history_days=0),
    "list_payees": dict(query=None),
    "get_transactions": dict(
        account_id=None,
        from_at="2026-10-01T00:00:00+08:00",
        to_at=NOW,
        category=None,
        merchant_id=None,
        kind=None,
    ),
    "get_spending_summary": dict(month="2026-10", account_id=None),
    "detect_anomalies": dict(account_id=None, from_at="2026-10-01T00:00:00+08:00", to_at=NOW),
    "get_cards": dict(account_id=None),
    "list_scheduled": dict(status="ALL"),
    "get_split_status": dict(request_id=None),
    "detect_subscriptions": dict(lookback_days=90),
    "list_subscriptions": dict(status="ACTIVE"),
    "upcoming_charges": dict(days=7, account_id=None),
    "get_risk_questionnaire": {},
    "get_risk_profile": {},
    "list_products": dict(max_risk_level=None, product_type=None),
    "compare_products": dict(product_ids=["prd_a", "prd_b"]),
    "get_holdings": dict(account_id=None, product_id=None),
    "get_product": dict(product_id="prd_a"),
    "get_profile": {},
}


def step(tool="transfer", step_id="s1", **args):
    return dict(
        step_id=step_id, tool=tool, args=WRITE_GOOD[tool] | args, depends_on=[], arg_sources=[]
    )
