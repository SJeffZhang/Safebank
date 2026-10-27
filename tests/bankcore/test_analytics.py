"""Pure-function tests for safebank_bankcore.analytics.spending_summary."""

from datetime import date, datetime

import safebank_bankcore.analytics as subject
from safebank_contracts.enums import Phase
from safebank_contracts.state import Account, BankState, Transaction

from tests.bankcore.fixtures import NOW, base_state


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def tx(**updates) -> Transaction:
    """Minimal posted CARD_SPEND transaction."""
    values = dict(
        id="tx_1",
        transfer_id="xfr_1",
        customer_id="cus_a",
        account_id="acc_a",
        direction="OUT",
        kind="CARD_SPEND",
        phase=Phase.POSTED,
        status="POSTED",
        amount_cents=5000,
        balance_after_cents=595000,
        available_after_cents=595000,
        counterparty="",
        memo=None,
        category="餐饮",
        merchant_id=None,
        payee_id=None,
        card_id=None,
        channel=None,
        occurred_at=at("2026-10-15T12:00:00+08:00"),
        sequence=1,
    )
    values.update(updates)
    return Transaction(**values)


def with_txns(state: BankState, *txns: Transaction) -> BankState:
    return state.model_copy(update={"transactions": txns})


def with_account(state: BankState, account_id: str, **updates) -> BankState:
    accts = dict(state.accounts)
    accts[account_id] = accts[account_id].model_copy(update=updates)
    return state.model_copy(update={"accounts": accts})


def test_no_transactions():
    state = base_state()
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 0
    assert result["previous_total_cents"] == 0
    assert result["mom_change_cents"] == 0
    assert result["mom_change_bp"] is None
    assert result["categories"] == ()
    assert result["daily"] == ()
    assert result["transfer_out_cents"] == 0


def test_previous_month_zero_gives_null_bp():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=1000, occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 1000
    assert result["previous_total_cents"] == 0
    assert result["mom_change_bp"] is None


def test_multi_category_and_share_bp():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=3000, category="餐饮",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=7000, category="购物",
           occurred_at=at("2026-10-10T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 10000
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 3000
    assert cats["购物"]["amount_cents"] == 7000
    # 3000/10000 = 30% = 3000 bp; 7000/10000 = 70% = 7000 bp
    assert cats["餐饮"]["share_bp"] == 3000
    assert cats["购物"]["share_bp"] == 7000
    # Sum of share_bp should be 10000
    assert sum(c["share_bp"] for c in result["categories"]) == 10000


def test_cross_account_aggregation():
    """Transactions across multiple accounts owned by the same customer are aggregated."""
    state = with_account(base_state(), "acc_s", customer_id="cus_a")
    state = with_txns(
        state,
        tx(id="t1", account_id="acc_a", amount_cents=2000, category="餐饮",
           occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
        tx(id="t2", account_id="acc_s", amount_cents=3000, category="餐饮",
           occurred_at=at("2026-10-02T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 5000


def test_account_id_filter():
    state = with_account(base_state(), "acc_s", customer_id="cus_a")
    state = with_txns(
        state,
        tx(id="t1", account_id="acc_a", amount_cents=2000, category="餐饮",
           occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
        tx(id="t2", account_id="acc_s", amount_cents=3000, category="交通",
           occurred_at=at("2026-10-02T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10", account_id="acc_a")
    assert result["total_cents"] == 2000
    cats = {c["category"]: c for c in result["categories"]}
    assert "餐饮" in cats
    assert "交通" not in cats


def test_pending_not_counted():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=5000, status="POSTED", occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=9999, status="PENDING", occurred_at=at("2026-10-01T11:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_released_not_counted():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=5000, status="POSTED", occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=4000, status="RELEASED", occurred_at=at("2026-10-01T11:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_p2p_transfer_out_separate():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=3000, kind="CARD_SPEND", category="购物",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=8000, kind="TRANSFER_EXTERNAL", category="",
           occurred_at=at("2026-10-10T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 8000


def test_transfer_in_not_counted():
    """Transfer in (direction=IN) does not affect consumption or transfer_out."""
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=3000, kind="CARD_SPEND", category="购物",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=5000, kind="TRANSFER_EXTERNAL", direction="IN",
           occurred_at=at("2026-10-10T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 0


def test_self_transfer_excluded():
    """Self-transfers (TRANSFER_SELF) are excluded from both consumption and transfer_out."""
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=3000, kind="CARD_SPEND", category="购物",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=5000, kind="TRANSFER_SELF", direction="OUT",
           occurred_at=at("2026-10-10T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 0


def test_month_boundary():
    """Transactions outside the target month are not counted."""
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=1000, category="餐饮",
           occurred_at=at("2026-09-30T23:59:59+08:00"), sequence=1),
        tx(id="t2", amount_cents=2000, category="餐饮",
           occurred_at=at("2026-10-01T00:00:00+08:00"), sequence=2),
        tx(id="t3", amount_cents=4000, category="交通",
           occurred_at=at("2026-10-31T23:59:59+08:00"), sequence=3),
        tx(id="t4", amount_cents=8000, category="交通",
           occurred_at=at("2026-11-01T00:00:00+08:00"), sequence=4),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 6000  # t2 + t3


def test_previous_month_categories():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=5000, category="娱乐",
           occurred_at=at("2026-09-15T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=10000, category="餐饮",
           occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=2),
        tx(id="t3", amount_cents=3000, category="餐饮",
           occurred_at=at("2026-09-20T10:00:00+08:00"), sequence=3),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 10000
    assert result["previous_total_cents"] == 8000
    # MoM: (10000 - 8000) / 8000 * 10000 = 2500 bp
    assert result["mom_change_bp"] == 2500
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 10000
    assert cats["餐饮"]["previous_amount_cents"] == 3000
    assert cats["娱乐"]["amount_cents"] == 0
    assert cats["娱乐"]["previous_amount_cents"] == 5000


def test_daily_breakdown():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=1000, category="餐饮",
           occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=2000, category="餐饮",
           occurred_at=at("2026-10-01T15:00:00+08:00"), sequence=2),
        tx(id="t3", amount_cents=3000, category="交通",
           occurred_at=at("2026-10-02T10:00:00+08:00"), sequence=3),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert len(result["daily"]) == 2
    days = {d["date"]: d["amount_cents"] for d in result["daily"]}
    assert days[date(2026, 10, 1)] == 3000
    assert days[date(2026, 10, 2)] == 3000


def test_unknown_category_maps_to_other():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=5000, category="some_unknown_cat",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    cats = {c["category"]: c for c in result["categories"]}
    assert "其他" in cats
    assert cats["其他"]["amount_cents"] == 5000


def test_mandate_counts_as_consumption():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=2000, kind="MANDATE", category="生活服务",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 2000


def test_mom_change_negative():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=10000, category="购物",
           occurred_at=at("2026-09-10T10:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=4000, category="购物",
           occurred_at=at("2026-10-10T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 4000
    assert result["previous_total_cents"] == 10000
    assert result["mom_change_cents"] == -6000
    assert result["mom_change_bp"] == -6000  # -60% = -6000 bp


def test_different_customer_transactions_excluded():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=5000, category="餐饮",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=1),
        tx(id="t2", customer_id="cus_b", account_id="acc_b", amount_cents=9999, category="餐饮",
           occurred_at=at("2026-10-05T10:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_year_crossing_boundary():
    """Month 2026-01 should not include 2025-12 transactions, but 2025-12 is prev month."""
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=3000, category="餐饮",
           occurred_at=at("2025-12-31T23:00:00+08:00"), sequence=1),
        tx(id="t2", amount_cents=5000, category="交通",
           occurred_at=at("2026-01-01T01:00:00+08:00"), sequence=2),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-01")
    assert result["total_cents"] == 5000
    assert result["previous_total_cents"] == 3000