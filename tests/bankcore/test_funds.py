"""Wealth-management fund domain (PRD §8.2) — pure bankcore functions."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from safebank_bankcore.banking import Context, apply_plan
from safebank_bankcore.funds import (
    apply_fund_confirm,
    apply_fund_expiry,
    apply_fund_settle,
    holdings_view,
)
from safebank_bankcore.ledger import replace_state
from safebank_contracts.plan import PlanBody
from safebank_contracts.state import Account
from safebank_contracts.views import ProductView

from tests.bankcore.fixtures import NOW, base_state
from tests.contracts.samples import step


def funds_state():
    state = base_state(balance=2_000_000)
    customers = dict(state.customers)
    customers["cus_a"] = customers["cus_a"].model_copy(
        update={"risk_level": 2, "risk_valid_until": "2027-12-31"}
    )
    customers["cus_b"] = customers["cus_b"].model_copy(
        update={"risk_level": 1, "risk_valid_until": "2027-12-31"}
    )
    products = (
        ProductView(
            id="prd_bond",
            name="稳健增利",
            product_type="BOND",
            risk_level=2,
            risk_label="R2",
            nav=Decimal("1.2310"),
            nav_date=date(2026, 10, 15),
            recent_return_bp=0,
            confirm_days=1,
            settle_days=1,
            min_amount_cents=10_000,
            risk_disclosure="合成历史表现，不构成收益承诺",
            disclosure_version="v1",
            nav_history=(),
        ),
        ProductView(
            id="prd_mmf",
            name="货币基金",
            product_type="MMF",
            risk_level=1,
            risk_label="R1",
            nav=Decimal("1.0000"),
            nav_date=date(2026, 10, 15),
            recent_return_bp=0,
            confirm_days=0,
            settle_days=0,
            min_amount_cents=1,
            risk_disclosure="合成历史表现，不构成收益承诺",
            disclosure_version="v1",
            nav_history=(),
        ),
        ProductView(
            id="prd_equity",
            name="进取成长",
            product_type="EQUITY",
            risk_level=4,
            risk_label="R4",
            nav=Decimal("2.5000"),
            nav_date=date(2026, 10, 15),
            recent_return_bp=0,
            confirm_days=1,
            settle_days=2,
            min_amount_cents=10_000,
            risk_disclosure="合成历史表现，不构成收益承诺",
            disclosure_version="v1",
            nav_history=(),
        ),
    )
    accounts = dict(state.accounts)
    for p in products:
        accounts[f"acc_fund_{p.id}"] = Account(
            id=f"acc_fund_{p.id}",
            customer_id=None,
            type="INTERNAL",
            display_name=p.name,
            account_no=f"internal-fund-{p.id}",
            flags=0,
        )
    return replace_state(state, customers=customers, products=products, accounts=accounts)


def simulate(state, steps, plan_id="pln_fund", customer_id="cus_a"):
    return apply_plan(
        state, PlanBody(intents=(), steps=steps, rationale="test").steps, customer_id, plan_id
    )


def total_equity(state):
    return sum(a.balance_cents for a in state.accounts.values())


def test_mmf_subscribe_posts_immediately():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_mmf", amount_cents=10_000)])
    assert result.error is None
    order = result.state.fund_orders[0]
    assert order.status == "SETTLED"
    assert order.units == Decimal("100.0000")
    assert result.state.accounts["acc_a"].debits_posted_cents == 10_000
    assert result.state.accounts["acc_a"].debits_pending_cents == 0
    holding = next(h for h in result.state.holdings if h.product_id == "prd_mmf")
    assert holding.units == Decimal("100.0000")
    assert total_equity(result.state) == 0


def test_non_mmf_subscribe_freezes_then_confirms_demo():
    state = funds_state()
    result = simulate(
        state,
        [step("subscribe_product", product_id="prd_bond", amount_cents=1_195_500)],
    )
    assert result.error is None
    assert result.state.accounts["acc_a"].debits_pending_cents == 1_195_500
    assert result.state.accounts["acc_a"].available_cents == 2_000_000 - 1_195_500
    order = result.state.fund_orders[0]
    assert order.status == "PENDING_CONFIRM"
    assert order.trade_date == NOW.date()
    assert order.confirm_at == datetime.fromisoformat("2026-10-16T09:00:00+08:00")
    conf = apply_fund_confirm(result.state, order.id, order.confirm_at, Context("cus_a", "pln_conf", "s1"))
    assert conf[2] == ()
    settled = conf[0].fund_orders[0]
    assert settled.status == "SETTLED"
    assert settled.units == Decimal("9710.8277")
    assert settled.nav == Decimal("1.2311")
    assert conf[0].accounts["acc_a"].debits_posted_cents == 1_195_500
    assert conf[0].accounts["acc_a"].debits_pending_cents == 0
    holding = next(h for h in conf[0].holdings if h.product_id == "prd_bond")
    assert holding.units == Decimal("9710.8277")
    assert holding.cost_cents == 1_195_500
    assert total_equity(conf[0]) == 0


@pytest.mark.parametrize(
    "now_iso,expected_trade,expected_confirm",
    [
        ("2026-10-16T14:00:00+08:00", date(2026, 10, 16), date(2026, 10, 19)),
        ("2026-10-16T16:00:00+08:00", date(2026, 10, 19), date(2026, 10, 20)),
        ("2026-10-17T10:00:00+08:00", date(2026, 10, 19), date(2026, 10, 20)),
    ],
)
def test_trade_date_cutoff_and_weekend(now_iso, expected_trade, expected_confirm):
    state = replace_state(funds_state(), sim_now=datetime.fromisoformat(now_iso))
    result = simulate(state, [step("subscribe_product", product_id="prd_bond", amount_cents=100_000)])
    assert result.error is None
    order = result.state.fund_orders[0]
    assert order.trade_date == expected_trade
    assert order.confirm_at.date() == expected_confirm


def test_suitability_mismatch_and_missing_assessment():
    state = funds_state()
    assert (
        simulate(state, [step("subscribe_product", product_id="prd_equity", amount_cents=100_000)]).error
        == "SUITABILITY_MISMATCH"
    )
    missing = replace_state(
        base_state(balance=2_000_000), products=state.products, accounts=dict(state.accounts)
    )
    assert (
        simulate(missing, [step("subscribe_product", product_id="prd_bond", amount_cents=100_000)]).error
        == "ASSESSMENT_MISSING"
    )


def test_min_purchase_not_met():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_bond", amount_cents=9_999)])
    assert result.error == "MIN_PURCHASE_NOT_MET"


def test_redeem_reserves_and_settles():
    state = funds_state()
    sub = simulate(state, [step("subscribe_product", product_id="prd_bond", amount_cents=1_195_500)])
    assert sub.error is None
    conf = apply_fund_confirm(
        sub.state, sub.state.fund_orders[0].id, sub.state.fund_orders[0].confirm_at, Context("cus_a", "pln_conf", "s1")
    )
    assert conf[2] == ()
    holding = next(h for h in conf[0].holdings if h.product_id == "prd_bond")
    assert holding.available_units == Decimal("9710.8277")
    before_checking = conf[0].accounts["acc_a"].credits_posted_cents
    red = simulate(
        conf[0],
        [step("redeem_product", account_id="acc_a", product_id="prd_bond", units=str(holding.available_units))],
        plan_id="pln_redeem",
    )
    assert red.error is None
    red_order = red.state.fund_orders[-1]
    assert red_order.type == "RED" and red_order.status == "PENDING_SETTLEMENT"
    h2 = next(h for h in red.state.holdings if h.product_id == "prd_bond")
    assert h2.reserved_units == holding.available_units
    assert h2.available_units == Decimal("0")
    assert red.state.accounts["acc_a"].credits_posted_cents == before_checking
    stl = apply_fund_settle(red.state, red_order.id, red_order.settle_at, Context("cus_a", "pln_settle", "s1"))
    assert stl[2] == ()
    settled_order = next(o for o in stl[0].fund_orders if o.id == red_order.id)
    assert settled_order.status == "SETTLED"
    h3 = next(h for h in stl[0].holdings if h.product_id == "prd_bond")
    assert h3.units == Decimal("0") and h3.reserved_units == Decimal("0")
    assert stl[0].accounts["acc_a"].credits_posted_cents == before_checking + red_order.amount_cents
    assert total_equity(stl[0]) == 0


def test_redeem_cannot_double_sell():
    state = funds_state()
    sub = simulate(state, [step("subscribe_product", product_id="prd_mmf", amount_cents=100_000)])
    assert sub.error is None
    holding = next(h for h in sub.state.holdings if h.product_id == "prd_mmf")
    assert holding.available_units == Decimal("1000.0000")
    red = simulate(sub.state, [step("redeem_product", product_id="prd_mmf", units="1000.0000")], plan_id="pln_redeem")
    assert red.error is None
    again = simulate(red.state, [step("redeem_product", product_id="prd_mmf", units="0.0001")], plan_id="pln_redeem2")
    assert again.error == "HOLDING_INSUFFICIENT"


def test_pending_subscribe_expires_and_releases_freeze():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_bond", amount_cents=1_000_000)])
    assert result.error is None
    order = result.state.fund_orders[0]
    assert order.status == "PENDING_CONFIRM"
    exp = apply_fund_expiry(result.state, order.id, order.expires_at, Context("cus_a", "pln_exp", "s1"))
    assert exp[2] == ()
    expired = exp[0].fund_orders[0]
    assert expired.status == "EXPIRED"
    assert exp[0].accounts["acc_a"].debits_pending_cents == 0
    assert exp[0].accounts["acc_a"].available_cents == 2_000_000
    assert total_equity(exp[0]) == 0


def test_confirm_suitability_failure_releases_and_marks_failed():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_bond", amount_cents=1_000_000)])
    assert result.error is None
    order = result.state.fund_orders[0]
    customers = dict(result.state.customers)
    customers["cus_a"] = customers["cus_a"].model_copy(update={"risk_level": 1})
    downgraded = replace_state(result.state, customers=customers)
    conf = apply_fund_confirm(downgraded, order.id, order.confirm_at, Context("cus_a", "pln_conf", "s1"))
    assert conf[2] == ()
    failed = conf[0].fund_orders[0]
    assert failed.status == "FAILED"
    assert failed.failure_code == "SUITABILITY_MISMATCH"
    assert conf[0].accounts["acc_a"].debits_pending_cents == 0
    assert conf[0].accounts["acc_a"].available_cents == 2_000_000
    assert total_equity(conf[0]) == 0


def test_holdings_view_and_redeem_flow():
    state = funds_state()
    a = simulate(state, [step("subscribe_product", product_id="prd_mmf", amount_cents=50_000)])
    b = simulate(
        state,
        [step("subscribe_product", account_id="acc_b", product_id="prd_mmf", amount_cents=30_000)],
        customer_id="cus_b",
        plan_id="pln_b",
    )
    assert a.error is None and b.error is None
    view = holdings_view(a.state, "cus_a")
    assert [h.product_id for h in view.holdings] == ["prd_mmf"]
    assert [o.product_id for o in view.orders] == ["prd_mmf"]
    view_b = holdings_view(b.state, "cus_b", account_id="acc_b")
    assert [h.product_id for h in view_b.holdings] == ["prd_mmf"]
    assert holdings_view(a.state, "cus_a", product_id="prd_bond").holdings == ()
    assert holdings_view(a.state, "cus_b").holdings == ()


def test_subscribe_via_apply_plan_result_shape():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_mmf", amount_cents=20_000)])
    assert result.error is None
    r = result.results[0].result
    assert r["order"]["status"] == "SETTLED"
    assert r["estimated_units"] == "200.0000"
    assert r["available_after_cents"] == 2_000_000 - 20_000
    assert r["pending_debit_after_cents"] == 0
    assert result.results[0].created_ids["order_id"] == result.state.fund_orders[0].id


def test_no_float_in_fund_arithmetic():
    state = funds_state()
    result = simulate(state, [step("subscribe_product", product_id="prd_mmf", amount_cents=12_345)])
    assert result.error is None
    order = result.state.fund_orders[0]
    assert isinstance(order.units, Decimal) and isinstance(order.nav, Decimal)
    holding = next(h for h in result.state.holdings if h.product_id == "prd_mmf")
    assert isinstance(holding.market_value_cents, int) and isinstance(holding.cost_cents, int)
